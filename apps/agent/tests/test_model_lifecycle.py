import asyncio
from typing import Literal
from uuid import uuid4

import pytest
from compass_agent.llm.contracts import DecisionOutput, GenerationOutput, RuntimeFailure
from compass_agent.llm.runtime import Runtime
from compass_agent.models.errors import ModelError
from compass_agent.models.service import ModelService
from compass_agent.schemas import (
    ChatCallInput,
    ChatMessage,
    DecisionInput,
    DecisionQuestion,
    GenerateInput,
    ModelUsage,
    ProviderCreate,
    ProviderPatch,
)
from compass_agent.schemas import (
    TestCallInput as CallInput,
)

pytest.importorskip("cryptography")


def platform_context(purpose: Literal["manage", "test", "chat", "module_read"] = "test"):
    from compass_agent.schemas import PlatformScope, TrustedContext

    return TrustedContext(
        actor_user_id=uuid4(), scope=PlatformScope(kind="platform"), purpose=purpose
    )


def tenant_context(purpose: Literal["manage", "test", "chat", "module_read"] = "chat"):
    from compass_agent.schemas import TenantScope, TrustedContext

    return TrustedContext(
        actor_user_id=uuid4(),
        scope=TenantScope(kind="tenant", tenant_id=uuid4(), workspace_id=uuid4()),
        purpose=purpose,
    )


class TextAdapter:
    def __init__(self, text: str = "answer") -> None:
        self.calls = 0
        self.text = text

    async def invoke(self, request, session) -> GenerationOutput:
        self.calls += 1
        session.resources.mark_sent()
        session.progress.finish_reason = "stop"
        session.progress.normal_eof = True
        session.progress.usage = ModelUsage(input_tokens=2, output_tokens=3)
        session.progress.output = GenerationOutput(text=self.text)
        return GenerationOutput(text=self.text)


class DecisionAdapter:
    def __init__(self) -> None:
        self.calls = 0

    async def invoke(self, request, session) -> DecisionOutput:
        self.calls += 1
        session.resources.mark_sent()
        session.progress.finish_reason = "stop"
        session.progress.normal_eof = True
        from compass_agent.schemas import DecisionAnswer, OutputValidation

        session.progress.validation = OutputValidation(status="valid", mode="decision")
        return DecisionOutput(answers={"pick": DecisionAnswer(type="noul", noul=0.4)})


@pytest.fixture
def adapters():
    return TextAdapter(), DecisionAdapter()


@pytest.fixture
def service(engine, adapters):
    text, decision = adapters
    runtime = Runtime({"litellm_chat": text, "systemone_decision": decision})
    return ModelService(engine, runtime)


def create_payload(**overrides) -> ProviderCreate:
    values = {
        "operation_id": uuid4(),
        "catalog_id": "deepseek/deepseek-v4-pro",
        "api_model": "deepseek-v4-pro",
        "alias": "经营分析",
        "base_url": "https://api.deepseek.com",
        "api_key": "sk-test-key",
        "enabled": True,
        "chat_visible": True,
    }
    values.update(overrides)
    return ProviderCreate(**values)


def chat_payload(provider_id, text: str = "本月表现如何") -> ChatCallInput:
    return ChatCallInput(
        call_id=uuid4(),
        provider_id=provider_id,
        input=GenerateInput(messages=[ChatMessage(role="user", content=text)]),
    )


def test_create_is_idempotent_and_rejects_reused_operation(service) -> None:
    platform = platform_context()
    payload = create_payload()

    async def scenario():
        first = await service.create_provider(payload, platform)
        again = await service.create_provider(payload, platform)
        assert first == again
        with pytest.raises(ModelError) as captured:
            await service.create_provider(
                create_payload(operation_id=payload.operation_id, alias="另一个别称"),
                platform,
            )
        return captured.value

    error = asyncio.run(scenario())
    assert error.code == "conflict"
    assert error.current_version is None


def test_version_conflict_and_binding_version(service) -> None:
    platform = platform_context()

    async def scenario():
        saved = await service.create_provider(create_payload(), platform)
        assert saved.version == 1
        with pytest.raises(ModelError) as conflict:
            await service.patch_provider(
                saved.provider_id,
                ProviderPatch(
                    operation_id=uuid4(),
                    expected_version=9,
                    alias="改名",
                ),
                platform,
            )
        assert conflict.value.code == "conflict"
        renamed = await service.patch_provider(
            saved.provider_id,
            ProviderPatch(operation_id=uuid4(), expected_version=1, alias="改名"),
            platform,
        )
        assert renamed.version == 2
        view = await service.provider(saved.provider_id, platform)
        assert view.alias == "改名"
        assert view.binding_version == 1
        rebound = await service.patch_provider(
            saved.provider_id,
            ProviderPatch(
                operation_id=uuid4(),
                expected_version=2,
                api_model="deepseek-v4-flash",
            ),
            platform,
        )
        assert rebound.version == 3
        view = await service.provider(saved.provider_id, platform)
        assert view.binding_version == 3
        assert view.api_model == "deepseek-v4-flash"

    asyncio.run(scenario())


def test_duplicate_alias_is_a_conflict(service) -> None:
    platform = platform_context()

    async def scenario():
        await service.create_provider(create_payload(alias="Alpha"), platform)
        with pytest.raises(ModelError) as captured:
            await service.create_provider(create_payload(alias="alpha"), platform)
        return captured.value

    assert asyncio.run(scenario()).code == "conflict"


def test_chat_call_records_outcome_and_duplicate_call_is_rejected(service, adapters) -> None:
    platform = platform_context()
    tenant = tenant_context()
    text_adapter, _ = adapters

    async def scenario():
        saved = await service.create_provider(create_payload(), platform)
        payload = chat_payload(saved.provider_id)
        response = await service.chat_call(payload, tenant)
        assert response.status == "completed"
        assert response.text == "answer"
        assert response.usage.input_tokens == 2
        assert response.requested_model == "deepseek-v4-pro"
        record = await service.call_record(payload.call_id, tenant)
        assert record.status == "completed"
        assert record.purpose == "chat"
        with pytest.raises(ModelError) as duplicate:
            await service.chat_call(payload, tenant)
        return duplicate.value

    error = asyncio.run(scenario())
    assert error.code == "call_already_exists"
    assert text_adapter.calls == 1


def test_chat_rejects_disabled_and_decision_providers(service) -> None:
    platform = platform_context()
    tenant = tenant_context()

    async def scenario():
        disabled = await service.create_provider(
            create_payload(alias="停用模型", enabled=False), platform
        )
        with pytest.raises(ModelError) as unavailable:
            await service.chat_call(chat_payload(disabled.provider_id), tenant)
        assert unavailable.value.code == "model_unavailable"
        decision = await service.create_provider(
            create_payload(
                catalog_id="aliyun/decision-model-preview",
                api_model="decision-model-preview",
                alias="决策服务",
                base_url="https://api.typesafe.ai/v1",
                api_key="decision-key",
                chat_visible=False,
            ),
            platform,
        )
        available = await service.available(tenant_context(purpose="module_read"))
        assert all(item.id != decision.provider_id for item in available.items)
        with pytest.raises(ModelError) as unsupported:
            await service.chat_call(chat_payload(decision.provider_id), tenant)
        assert unsupported.value.code == "chat_model_not_supported"
        with pytest.raises(ModelError) as hidden:
            await service.patch_provider(
                decision.provider_id,
                ProviderPatch(
                    operation_id=uuid4(),
                    expected_version=1,
                    chat_visible=True,
                ),
                platform,
            )
        assert hidden.value.code == "chat_model_not_supported"

    asyncio.run(scenario())


def test_decision_test_call_returns_answers(service, adapters) -> None:
    platform = platform_context()
    _, decision_adapter = adapters

    async def scenario():
        saved = await service.create_provider(
            create_payload(
                catalog_id="typesafe/jev-latest",
                api_model="jev-latest",
                alias="JEV 决策",
                base_url="https://api.typesafe.ai/v1",
                api_key="decision-key",
                chat_visible=False,
            ),
            platform,
        )
        payload = CallInput(
            call_id=uuid4(),
            input=DecisionInput(
                state={"sales": 12},
                questions={
                    "pick": DecisionQuestion(type="noul", criteria={"true": "是", "false": "否"})
                },
            ),
        )
        response = await service.test_call(saved.provider_id, payload, platform)
        assert response.status == "completed"
        assert response.answers is not None
        assert response.answers["pick"].noul == 0.4
        record = await service.test_record(payload.call_id, platform)
        assert record.purpose == "test"
        return saved

    saved = asyncio.run(scenario())
    assert decision_adapter.calls == 1
    assert saved.provider_id


def test_test_call_is_allowed_for_disabled_provider(service, adapters) -> None:
    platform = platform_context()
    text_adapter, _ = adapters

    async def scenario():
        saved = await service.create_provider(
            create_payload(alias="停用诊断", enabled=False), platform
        )
        payload = CallInput(
            call_id=uuid4(),
            input=GenerateInput(messages=[ChatMessage(role="user", content="ping")]),
        )
        response = await service.test_call(saved.provider_id, payload, platform)
        assert response.status == "completed"
        view = await service.provider(saved.provider_id, platform)
        assert view.latest_test is not None
        assert view.latest_test.stale is False
        return saved

    asyncio.run(scenario())
    assert text_adapter.calls == 1


def test_latest_test_becomes_stale_after_rebinding(service) -> None:
    platform = platform_context()

    async def scenario():
        saved = await service.create_provider(create_payload(), platform)
        payload = CallInput(
            call_id=uuid4(),
            input=GenerateInput(messages=[ChatMessage(role="user", content="ping")]),
        )
        await service.test_call(saved.provider_id, payload, platform)
        view = await service.provider(saved.provider_id, platform)
        assert view.latest_test is not None and view.latest_test.stale is False
        await service.patch_provider(
            saved.provider_id,
            ProviderPatch(
                operation_id=uuid4(),
                expected_version=1,
                api_key="sk-replaced",
            ),
            platform,
        )
        view = await service.provider(saved.provider_id, platform)
        assert view.latest_test is not None and view.latest_test.stale is True

    asyncio.run(scenario())


def test_record_write_failure_is_reported_without_retry(engine, adapters) -> None:
    text_adapter, _ = adapters
    runtime = Runtime({"litellm_chat": text_adapter, "systemone_decision": text_adapter})
    service = ModelService(engine, runtime)
    service._repository.finish_call = lambda *args, **kwargs: False  # type: ignore[assignment]
    platform = platform_context()
    tenant = tenant_context()

    async def scenario():
        saved = await service.create_provider(create_payload(), platform)
        response = await service.chat_call(chat_payload(saved.provider_id), tenant)
        return response

    response = asyncio.run(scenario())
    assert response.status == "failed"
    assert response.error_code == "record_write_failed"
    assert response.upstream_outcome_unknown is True
    assert text_adapter.calls == 1


def test_failure_before_outbound_is_failed_without_unknown(engine) -> None:
    class FailingAdapter:
        def __init__(self) -> None:
            self.calls = 0

        async def invoke(self, request, session):
            self.calls += 1
            raise RuntimeFailure("model_authentication_failed")

    adapter = FailingAdapter()
    runtime = Runtime({"litellm_chat": adapter, "systemone_decision": adapter})
    service = ModelService(engine, runtime)
    platform = platform_context()
    tenant = tenant_context()

    async def scenario():
        saved = await service.create_provider(create_payload(), platform)
        return await service.chat_call(chat_payload(saved.provider_id), tenant)

    response = asyncio.run(scenario())
    assert response.status == "failed"
    assert response.error_code == "model_authentication_failed"
    assert response.upstream_outcome_unknown is False
    assert adapter.calls == 1


def test_cancellation_records_canceled_and_unknown(engine) -> None:
    class SlowAdapter:
        def __init__(self) -> None:
            self.calls = 0
            self.started = asyncio.Event()

        async def invoke(self, request, session):
            self.calls += 1
            session.resources.mark_sent()
            self.started.set()
            await asyncio.sleep(5)
            raise AssertionError("unreachable")

    adapter = SlowAdapter()
    runtime = Runtime({"litellm_chat": adapter, "systemone_decision": adapter})
    service = ModelService(engine, runtime)
    platform = platform_context()
    tenant = tenant_context()

    async def scenario():
        saved = await service.create_provider(create_payload(), platform)
        payload = chat_payload(saved.provider_id)
        task = asyncio.create_task(service.chat_call(payload, tenant))
        await adapter.started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        record = await service.call_record(payload.call_id, tenant)
        return record

    record = asyncio.run(scenario())
    assert record.status == "canceled"
    assert record.upstream_outcome_unknown is True
    assert adapter.calls == 1
