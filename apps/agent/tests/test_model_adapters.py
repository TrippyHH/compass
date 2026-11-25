import asyncio
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from compass_agent.llm.contracts import (
    CallSession,
    ContinuationBinding,
    DecisionOutput,
    ExecutionRequest,
    GenerationOutput,
    InvocationContext,
    ModelSpec,
    ProviderAccess,
    ProviderProgress,
    RuntimeFailure,
    empty_output_for,
)
from compass_agent.llm.litellm_chat import LiteLLMChatAdapter
from compass_agent.llm.systemone_decision import SystemOneDecisionAdapter, systemone_url
from compass_agent.llm.transport import CallResources
from compass_agent.schemas import (
    ChatMessage,
    DecisionInput,
    DecisionQuestion,
    GenerateInput,
    PlatformScope,
    ToolDefinition,
    ToolFunction,
)
from pydantic import SecretStr

pytest.importorskip("cryptography")
pytest.importorskip("jsonschema")


def spec(**overrides) -> ModelSpec:
    values = {
        "provider_id": uuid4(),
        "config_version": 1,
        "binding_version": 1,
        "vendor": "deepseek",
        "kind": "llm",
        "adapter": "litellm_chat",
        "api_model": "deepseek-v4-pro",
        "sdk_model": "deepseek/deepseek-v4-pro",
        "api_base": "https://api.deepseek.com",
    }
    values.update(overrides)
    return ModelSpec(**values)


def request(model, value: GenerateInput, connection_params=None) -> ExecutionRequest:
    scope = PlatformScope(kind="platform")
    binding = ContinuationBinding(
        actor=uuid4(),
        scope=scope,
        provider=model.provider_id,
        binding_version=model.binding_version,
    )
    import time

    from compass_agent.llm.contracts import CallPolicy

    now = time.monotonic()
    return ExecutionRequest(
        context=InvocationContext(
            call_id=uuid4(), capability_mode="diagnostic", continuation_binding=binding
        ),
        access=ProviderAccess(
            spec=model,
            api_key=SecretStr("sk-test"),
            connection_params=connection_params or {},
        ),
        input=value,
        policy=CallPolicy(started_monotonic=now, deadline_monotonic=now + 30),
    )


def session(execution: ExecutionRequest, collected: list) -> CallSession:
    async def emit(event) -> None:
        collected.append(event)

    return CallSession(
        resources=CallResources(),
        progress=ProviderProgress(output=empty_output_for(execution.input)),
        emit=emit,
    )


def generate(**overrides: object) -> GenerateInput:
    values: dict[str, object] = {"messages": [ChatMessage(role="user", content="hi")]}
    values.update(overrides)
    return GenerateInput.model_validate(values)


def completion_response(text="你好", finish="stop", tool_calls=None):
    message = SimpleNamespace(content=text, tool_calls=tool_calls)
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason=finish)],
        usage=SimpleNamespace(prompt_tokens=3, completion_tokens=5),
        model="deepseek-v4-pro",
        id="req_1",
    )


def test_non_stream_params_and_output() -> None:
    captured = {}

    async def completion(**params):
        captured.update(params)
        return completion_response()

    adapter = LiteLLMChatAdapter(completion=completion)
    execution = request(spec(), generate(thinking=True, reasoning_effort="high"))
    collected: list = []
    output = asyncio.run(adapter.invoke(execution, session(execution, collected)))
    assert isinstance(output, GenerationOutput)
    assert output.text == "你好"
    assert captured["stream"] is False
    assert captured["num_retries"] == 0
    assert captured["max_retries"] == 0
    assert captured["drop_params"] is False
    assert captured["api_key"] == "sk-test"
    assert captured["extra_body"] == {"thinking": {"type": "enabled"}}
    assert captured["reasoning_effort"] == "high"
    assert execution.policy.max_attempts == 1


def test_thinking_field_is_not_sent_to_other_vendors() -> None:
    captured = {}

    async def completion(**params):
        captured.update(params)
        return completion_response()

    adapter = LiteLLMChatAdapter(completion=completion)
    model = spec(
        vendor="anthropic",
        sdk_model="anthropic/claude-sonnet-4-5",
        api_model="claude-sonnet-4-5",
        api_base="https://api.anthropic.com",
    )
    execution = request(model, generate(thinking=True, reasoning_effort="high"))
    asyncio.run(adapter.invoke(execution, session(execution, [])))
    assert "extra_body" not in captured


def test_stream_aggregates_text_tools_and_usage() -> None:
    class FakeStream:
        def __init__(self) -> None:
            self.closed = False

        async def aclose(self) -> None:
            self.closed = True

        def __aiter__(self):
            async def iterator():
                yield SimpleNamespace(
                    choices=[
                        SimpleNamespace(delta=SimpleNamespace(content="你"), finish_reason=None)
                    ],
                    usage=None,
                    model="deepseek-v4-pro",
                )
                yield SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            delta=SimpleNamespace(
                                content="好",
                                tool_calls=[
                                    SimpleNamespace(
                                        index=0,
                                        id="call_1",
                                        function=SimpleNamespace(
                                            name="lookup", arguments='{"metric":'
                                        ),
                                    )
                                ],
                            ),
                            finish_reason=None,
                        )
                    ],
                    usage=None,
                    model="deepseek-v4-pro",
                )
                yield SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            delta=SimpleNamespace(
                                content=None,
                                tool_calls=[
                                    SimpleNamespace(
                                        index=0,
                                        id=None,
                                        function=SimpleNamespace(name=None, arguments='"sales"}'),
                                    )
                                ],
                            ),
                            finish_reason="tool_calls",
                        )
                    ],
                    usage=SimpleNamespace(
                        prompt_tokens=7,
                        completion_tokens=9,
                        completion_tokens_details=SimpleNamespace(reasoning_tokens=2),
                    ),
                    model=None,
                )

            return iterator()

    stream = FakeStream()

    async def completion(**params):
        assert params["stream"] is True
        assert params["stream_options"] == {"include_usage": True}
        return stream

    adapter = LiteLLMChatAdapter(completion=completion)
    tool = ToolDefinition(
        function=ToolFunction(
            name="lookup",
            parameters={
                "type": "object",
                "properties": {"metric": {"type": "string"}},
                "required": ["metric"],
            },
        )
    )
    execution = request(spec(), generate(stream=True, tools=[tool]))
    collected: list = []
    call_session = session(execution, collected)
    output = asyncio.run(adapter.invoke(execution, call_session))
    assert isinstance(output, GenerationOutput)
    assert output.text == "你好"
    assert len(output.tool_calls) == 1
    assert output.tool_calls[0].id == "call_1"
    assert output.tool_calls[0].function.arguments == '{"metric":"sales"}'
    assert execution.access.spec.kind == "llm"
    assert collected[-1].type == "tool_delta"
    assert call_session.resources.sent is True
    asyncio.run(call_session.resources.close())
    assert stream.closed is True


def test_schema_validation_failure_is_explicit() -> None:
    async def completion(**params):
        return completion_response(text='{"sales": "not-a-number"}')

    adapter = LiteLLMChatAdapter(completion=completion)
    execution = request(
        spec(),
        generate(
            output_format="schema",
            output_schema={
                "type": "object",
                "properties": {"sales": {"type": "number"}},
                "required": ["sales"],
            },
        ),
    )
    with pytest.raises(RuntimeFailure) as captured:
        asyncio.run(adapter.invoke(execution, session(execution, [])))
    assert captured.value.error.code == "schema_validation_failed"


def test_upstream_rejection_is_not_reported_as_protocol_error() -> None:
    class Rejected(Exception):
        status_code = 400

    async def completion(**params):
        raise Rejected("unknown model")

    adapter = LiteLLMChatAdapter(completion=completion)
    execution = request(spec(), generate())
    with pytest.raises(RuntimeFailure) as captured:
        asyncio.run(adapter.invoke(execution, session(execution, [])))
    assert captured.value.error.code == "model_request_rejected"


def test_connection_params_cannot_override_call_fields() -> None:
    async def completion(**params):
        raise AssertionError("unreachable")

    adapter = LiteLLMChatAdapter(completion=completion)
    execution = request(spec(), generate(), connection_params={"messages": []})
    with pytest.raises(RuntimeFailure) as captured:
        asyncio.run(adapter.invoke(execution, session(execution, [])))
    assert captured.value.error.code == "validation_failed"


def decision_request(model: ModelSpec, value: DecisionInput) -> ExecutionRequest:
    scope = PlatformScope(kind="platform")
    binding = ContinuationBinding(
        actor=uuid4(), scope=scope, provider=model.provider_id, binding_version=1
    )
    import time

    from compass_agent.llm.contracts import CallPolicy

    now = time.monotonic()
    return ExecutionRequest(
        context=InvocationContext(
            call_id=uuid4(), capability_mode="diagnostic", continuation_binding=binding
        ),
        access=ProviderAccess(spec=model, api_key=SecretStr("decision-key")),
        input=value,
        policy=CallPolicy(started_monotonic=now, deadline_monotonic=now + 30),
    )


def decision_model() -> ModelSpec:
    return spec(
        kind="decision",
        adapter="systemone_decision",
        vendor="typesafe",
        api_model="jev-latest",
        sdk_model=None,
        api_base="https://api.typesafe.ai/v1",
    )


def decision_input() -> DecisionInput:
    return DecisionInput(
        state={"sales": 12},
        questions={"pick": DecisionQuestion(type="choice", criteria={"a": "增长", "b": "下降"})},
    )


def test_decision_adapter_posts_systemone_and_normalizes_answers() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        import json

        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "model": "jev-latest-2026",
                "request_id": "req_body",
                "answers": {
                    "pick": {
                        "type": "choice",
                        "choice": "a",
                        "probabilities": {"a": 0.8, "b": 0.2},
                        "confidence": 0.9,
                    }
                },
                "usage": {"input_tokens": 31, "output_tokens": 4},
            },
            headers={"x-request-id": "req_2"},
        )

    adapter = SystemOneDecisionAdapter(transport=httpx.MockTransport(handler))
    execution = decision_request(decision_model(), decision_input())
    call_session = session(execution, [])
    output = asyncio.run(adapter.invoke(execution, call_session))
    assert isinstance(output, DecisionOutput)
    assert str(seen["url"]).endswith("/v1/systemone")
    assert seen["auth"] == "Bearer decision-key"
    assert seen["body"]["model"] == "jev-latest"
    assert output.answers["pick"].choice == "a"
    assert output.answers["pick"].probabilities == {"a": 0.8, "b": 0.2}
    assert call_session.progress.normal_eof is True
    assert call_session.progress.usage.input_tokens == 31
    assert call_session.progress.usage.output_tokens == 4
    assert call_session.progress.upstream_request_id == "req_body"


def test_decision_adapter_normalizes_rounded_probabilities() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "answers": {
                    "pick": {
                        "type": "choice",
                        "choice": "a",
                        "probabilities": {"a": 0.55, "b": 0.44},
                        "confidence": 0.5,
                    }
                }
            },
        )

    adapter = SystemOneDecisionAdapter(transport=httpx.MockTransport(handler))
    execution = decision_request(decision_model(), decision_input())
    output = asyncio.run(adapter.invoke(execution, session(execution, [])))
    assert isinstance(output, DecisionOutput)
    probabilities = output.answers["pick"].probabilities or {}
    assert abs(sum(probabilities.values()) - 1.0) < 1e-9
    assert probabilities["a"] == pytest.approx(0.55 / 0.99)


def test_decision_adapter_rejects_incomplete_probabilities() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "answers": {
                    "pick": {
                        "type": "choice",
                        "choice": "a",
                        "probabilities": {"a": 1.0},
                    }
                }
            },
        )

    adapter = SystemOneDecisionAdapter(transport=httpx.MockTransport(handler))
    execution = decision_request(decision_model(), decision_input())
    with pytest.raises(RuntimeFailure) as captured:
        asyncio.run(adapter.invoke(execution, session(execution, [])))
    assert captured.value.error.code == "provider_protocol_error"


def test_decision_adapter_maps_authentication_failure() -> None:
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return httpx.Response(401, json={"error": "denied"})

    adapter = SystemOneDecisionAdapter(transport=httpx.MockTransport(handler))
    execution = decision_request(decision_model(), decision_input())
    with pytest.raises(RuntimeFailure) as captured:
        asyncio.run(adapter.invoke(execution, session(execution, [])))
    assert captured.value.error.code == "model_authentication_failed"
    assert calls["count"] == 1


def test_systemone_url_rules() -> None:
    assert systemone_url("https://api.typesafe.ai/v1") == "https://api.typesafe.ai/v1/systemone"
    assert systemone_url("https://api.typesafe.ai/v1/systemone") == (
        "https://api.typesafe.ai/v1/systemone"
    )
    for value in (
        "",
        "http://example.com/v1",
        "https://user:pass@example.com",
        "https://x.test/?q=1",
    ):
        with pytest.raises(RuntimeFailure):
            systemone_url(value)
