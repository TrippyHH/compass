import asyncio
from typing import Literal
from uuid import uuid4

import pytest
from compass_agent.llm.contracts import GenerationOutput, RuntimeFailure
from compass_agent.llm.runtime import Runtime
from compass_agent.models.errors import ModelError
from compass_agent.models.service import ModelService
from compass_agent.schemas import (
    ChatCallInput,
    ChatMessage,
    GenerateInput,
    ProviderCreate,
    ProviderPatch,
    TextDeltaEvent,
    ToolDefinition,
    ToolFunction,
)

pytest.importorskip("cryptography")


def platform_context():
    from compass_agent.schemas import PlatformScope, TrustedContext

    return TrustedContext(
        actor_user_id=uuid4(), scope=PlatformScope(kind="platform"), purpose="test"
    )


def tenant_context(purpose: Literal["manage", "test", "chat", "module_read"] = "chat"):
    from compass_agent.schemas import TenantScope, TrustedContext

    return TrustedContext(
        actor_user_id=uuid4(),
        scope=TenantScope(kind="tenant", tenant_id=uuid4(), workspace_id=uuid4()),
        purpose=purpose,
    )


def create_payload(**overrides) -> ProviderCreate:
    values = {
        "operation_id": uuid4(),
        "catalog_id": "deepseek/deepseek-v4-pro",
        "api_model": "deepseek-v4-pro",
        "alias": "边界模型",
        "base_url": "https://api.deepseek.com",
        "api_key": "sk-test-key",
        "enabled": True,
        "chat_visible": True,
    }
    values.update(overrides)
    return ProviderCreate(**values)


class DeltaFailAdapter:
    def __init__(self) -> None:
        self.calls = 0
        self.closed = 0

    async def invoke(self, request, session) -> GenerationOutput:
        self.calls += 1
        session.resources.mark_sent()
        session.progress.usage = _usage()
        session.progress.output = GenerationOutput(text="部分")
        await session.publish(TextDeltaEvent(call_id=request.context.call_id, text="部分"))
        raise RuntimeFailure("stream_interrupted")


def _usage():
    from compass_agent.schemas import ModelUsage

    return ModelUsage(input_tokens=7, output_tokens=2)


def test_single_outbound_and_complete_record(engine) -> None:
    class CountingAdapter:
        def __init__(self) -> None:
            self.calls = 0

        async def invoke(self, request, session) -> GenerationOutput:
            self.calls += 1
            session.resources.mark_sent()
            session.progress.finish_reason = "stop"
            session.progress.normal_eof = True
            output = GenerationOutput(text="ok")
            session.progress.output = output
            return output

    adapter = CountingAdapter()
    runtime = Runtime({"litellm_chat": adapter, "systemone_decision": adapter})
    service = ModelService(engine, runtime)
    platform = platform_context()
    tenant = tenant_context()

    async def scenario():
        saved = await service.create_provider(create_payload(), platform)
        payload = ChatCallInput(
            call_id=uuid4(),
            provider_id=saved.provider_id,
            input=GenerateInput(messages=[ChatMessage(role="user", content="go")]),
        )
        response = await service.chat_call(payload, tenant)
        record = await service.call_record(payload.call_id, tenant)
        return response, record

    response, record = asyncio.run(scenario())
    assert adapter.calls == 1
    assert response.status == "completed"
    assert record.status == "completed"
    assert record.config_version == 1
    assert record.binding_version == 1


def test_partial_stream_failure_keeps_partial_text_and_usage(engine) -> None:
    adapter = DeltaFailAdapter()
    runtime = Runtime({"litellm_chat": adapter, "systemone_decision": adapter})
    service = ModelService(engine, runtime)
    platform = platform_context()
    tenant = tenant_context()

    async def scenario():
        saved = await service.create_provider(create_payload(), platform)
        payload = ChatCallInput(
            call_id=uuid4(),
            provider_id=saved.provider_id,
            input=GenerateInput(messages=[ChatMessage(role="user", content="go")], stream=True),
        )
        response = await service.chat_call(payload, tenant)
        record = await service.call_record(payload.call_id, tenant)
        return response, record

    response, record = asyncio.run(scenario())
    assert response.status == "incomplete"
    assert response.error_code == "stream_interrupted"
    assert response.text == "部分"
    assert response.usage.input_tokens == 7
    assert response.upstream_outcome_unknown is True
    assert record.status == "incomplete"
    assert record.error_code == "stream_interrupted"
    assert adapter.calls == 1


def test_capability_rejection_happens_before_placeholder(engine) -> None:
    class CountingAdapter:
        def __init__(self) -> None:
            self.calls = 0

        async def invoke(self, request, session):
            self.calls += 1
            raise AssertionError("unreachable")

    adapter = CountingAdapter()
    runtime = Runtime({"litellm_chat": adapter, "systemone_decision": adapter})
    service = ModelService(engine, runtime)
    platform = platform_context()
    tenant = tenant_context()

    async def scenario():
        saved = await service.create_provider(
            create_payload(
                catalog_id="acme/mystery-model",
                api_model="mystery-model",
                base_url="",
                alias="未知模型",
            ),
            platform,
        )
        payload = ChatCallInput(
            call_id=uuid4(),
            provider_id=saved.provider_id,
            input=GenerateInput(
                messages=[ChatMessage(role="user", content="go")],
                tools=[
                    ToolDefinition(
                        function=ToolFunction(
                            name="lookup", parameters={"type": "object", "properties": {}}
                        )
                    )
                ],
            ),
        )
        with pytest.raises(ModelError) as captured:
            await service.chat_call(payload, tenant)
        with pytest.raises(ModelError) as missing:
            await service.call_record(payload.call_id, tenant)
        return captured.value, missing.value

    error, missing = asyncio.run(scenario())
    assert error.code == "validation_failed"
    assert missing.code == "not_found"
    assert adapter.calls == 0


def test_endpoint_allowlist_and_connection_parameters(engine) -> None:
    class NoopAdapter:
        async def invoke(self, request, session):
            raise AssertionError("unreachable")

    service = ModelService(
        engine, Runtime({"litellm_chat": NoopAdapter(), "systemone_decision": NoopAdapter()})
    )
    platform = platform_context()

    async def scenario():
        with pytest.raises(ModelError) as blocked:
            await service.create_provider(
                create_payload(base_url="https://evil.example.com"), platform
            )
        with pytest.raises(ModelError) as plain_http:
            await service.create_provider(
                create_payload(base_url="http://api.deepseek.com"), platform
            )
        with pytest.raises(ModelError) as reserved:
            await service.create_provider(
                create_payload(connection_params={"messages": []}), platform
            )
        saved = await service.create_provider(
            create_payload(connection_params={"api_version": "2024-05-01"}), platform
        )
        view = await service.provider(saved.provider_id, platform)
        assert view.has_connection_params is True
        cleared = await service.patch_provider(
            saved.provider_id,
            ProviderPatch(
                operation_id=uuid4(),
                expected_version=1,
                connection_params={},
            ),
            platform,
        )
        assert cleared.version == 2
        view = await service.provider(saved.provider_id, platform)
        assert view.has_connection_params is False
        return blocked.value, plain_http.value, reserved.value

    blocked, plain_http, reserved = asyncio.run(scenario())
    assert blocked.code == "validation_failed"
    assert plain_http.code == "validation_failed"
    assert reserved.code == "validation_failed"


def test_decision_provider_requires_endpoint_and_key(engine) -> None:
    class NoopAdapter:
        async def invoke(self, request, session):
            raise AssertionError("unreachable")

    service = ModelService(
        engine, Runtime({"litellm_chat": NoopAdapter(), "systemone_decision": NoopAdapter()})
    )
    platform = platform_context()

    async def scenario():
        with pytest.raises(ModelError) as no_base:
            await service.create_provider(
                create_payload(
                    catalog_id="aliyun/decision-model-preview",
                    api_model="decision-model-preview",
                    alias="决策",
                    base_url="",
                    chat_visible=False,
                ),
                platform,
            )
        with pytest.raises(ModelError) as no_key:
            await service.create_provider(
                create_payload(
                    catalog_id="aliyun/decision-model-preview",
                    api_model="decision-model-preview",
                    alias="决策",
                    base_url="https://api.typesafe.ai/v1",
                    api_key="",
                    chat_visible=False,
                ),
                platform,
            )
        return no_base.value, no_key.value

    no_base, no_key = asyncio.run(scenario())
    assert no_base.code == "validation_failed"
    assert no_key.code == "validation_failed"


def test_manual_model_identifier_is_accepted(engine) -> None:
    class NoopAdapter:
        async def invoke(self, request, session):
            raise AssertionError("unreachable")

    service = ModelService(
        engine, Runtime({"litellm_chat": NoopAdapter(), "systemone_decision": NoopAdapter()})
    )
    platform = platform_context()

    async def scenario():
        saved = await service.create_provider(
            create_payload(
                catalog_id="openai/gpt-4o",
                api_model="gpt-4o",
                alias="手工模型",
                base_url="",
            ),
            platform,
        )
        return await service.provider(saved.provider_id, platform)

    view = asyncio.run(scenario())
    assert view.vendor == "openai"
    assert view.api_model == "gpt-4o"
    assert view.chat_supported is True
