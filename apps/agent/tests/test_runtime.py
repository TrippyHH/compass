import asyncio
import time
from dataclasses import replace

import pytest
from compass_agent.llm.contracts import (
    DecisionOutput,
    ExecutionRequest,
    GenerationOutput,
    RuntimeCancelled,
    RuntimeFailure,
)
from compass_agent.llm.runtime import Runtime
from compass_agent.schemas import (
    ChatMessage,
    DecisionAnswer,
    DecisionInput,
    DecisionQuestion,
    TextDeltaEvent,
)


class Closable:
    def __init__(self) -> None:
        self.closed = 0

    async def aclose(self) -> None:
        self.closed += 1


class OkAdapter:
    def __init__(self, text: str = "ok") -> None:
        self.calls = 0
        self.text = text
        self.closable = Closable()

    async def invoke(self, request: ExecutionRequest, session) -> GenerationOutput:
        self.calls += 1
        session.resources.register(self.closable)
        session.resources.mark_sent()
        session.progress.finish_reason = "stop"
        session.progress.normal_eof = True
        session.progress.output = GenerationOutput(text=self.text)
        return GenerationOutput(text=self.text)


class PartialFailureAdapter:
    def __init__(self, code: str = "stream_interrupted") -> None:
        self.calls = 0
        self.code = code
        self.closable = Closable()

    async def invoke(self, request: ExecutionRequest, session) -> GenerationOutput:
        self.calls += 1
        session.resources.register(self.closable)
        session.resources.mark_sent()
        await session.publish(TextDeltaEvent(call_id=request.context.call_id, text="part"))
        session.progress.output = GenerationOutput(text="part")
        raise RuntimeFailure(self.code)  # type: ignore[arg-type]


class SleepingAdapter:
    def __init__(self, seconds: float = 5.0) -> None:
        self.calls = 0
        self.seconds = seconds
        self.closable = Closable()

    async def invoke(self, request: ExecutionRequest, session) -> GenerationOutput:
        self.calls += 1
        session.resources.register(self.closable)
        session.resources.mark_sent()
        await asyncio.sleep(self.seconds)
        raise AssertionError("unreachable")


class DecisionAdapter:
    def __init__(self, answers: dict | None = None) -> None:
        self.calls = 0
        self.answers = answers or {}

    async def invoke(self, request: ExecutionRequest, session) -> DecisionOutput:
        self.calls += 1
        session.resources.mark_sent()
        session.progress.finish_reason = "stop"
        session.progress.normal_eof = True
        session.progress.validation = session.progress.validation.model_copy(
            update={"status": "valid", "mode": "decision"}
        )
        return DecisionOutput(answers=self.answers)


def run(runtime: Runtime, request: ExecutionRequest):
    return asyncio.run(runtime.invoke(request))


def test_completed_call_returns_typed_output_and_closes_resources(request_factory) -> None:
    adapter = OkAdapter()
    runtime = Runtime({"litellm_chat": adapter, "systemone_decision": OkAdapter()})
    request = request_factory()
    result = run(runtime, request)
    assert result.status == "completed"
    assert result.error is None
    assert isinstance(result.output, GenerationOutput)
    assert result.output.text == "ok"
    assert result.usage.input_tokens is None
    assert result.upstream_sent is True
    assert adapter.calls == 1
    assert adapter.closable.closed == 1


def test_unknown_adapter_mapping_is_rejected(request_factory) -> None:
    with pytest.raises(ValueError):
        Runtime({"other": OkAdapter()})  # type: ignore[arg-type]


def test_expired_deadline_never_reaches_adapter(request_factory) -> None:
    adapter = OkAdapter()
    runtime = Runtime({"litellm_chat": adapter, "systemone_decision": adapter})
    request = request_factory()
    started = time.monotonic()
    expired = replace(
        request,
        policy=replace(
            request.policy,
            started_monotonic=started - 10,
            deadline_monotonic=started - 1,
        ),
    )
    result = run(runtime, expired)
    assert result.status == "failed"
    assert result.error is not None and result.error.code == "model_timeout"
    assert result.upstream_sent is False
    assert adapter.calls == 0


def test_timeout_without_partial_output_is_failed_and_cleaned(request_factory) -> None:
    adapter = SleepingAdapter(seconds=2)
    runtime = Runtime({"litellm_chat": adapter, "systemone_decision": adapter})
    request = request_factory(budget=0.2)
    result = run(runtime, request)
    assert result.status == "failed"
    assert result.error is not None and result.error.code == "model_timeout"
    assert result.upstream_outcome_unknown is True
    assert adapter.calls == 1
    assert adapter.closable.closed == 1


def test_failure_after_visible_delta_is_incomplete(request_factory) -> None:
    adapter = PartialFailureAdapter()
    runtime = Runtime({"litellm_chat": adapter, "systemone_decision": adapter})
    result = run(runtime, request_factory())
    assert result.status == "incomplete"
    assert result.error is not None and result.error.code == "stream_interrupted"
    assert isinstance(result.output, GenerationOutput)
    assert result.output.text == "part"
    assert result.upstream_outcome_unknown is True


def test_cancellation_propagates_result_and_unknown_outcome(request_factory) -> None:
    adapter = SleepingAdapter(seconds=5)
    runtime = Runtime({"litellm_chat": adapter, "systemone_decision": adapter})
    request = request_factory()

    async def scenario():
        task = asyncio.create_task(runtime.invoke(request))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(RuntimeCancelled) as captured:
            await task
        return captured.value.result

    result = asyncio.run(scenario())
    assert result.status == "canceled"
    assert result.error is None
    assert result.upstream_outcome_unknown is True
    assert adapter.calls == 1
    assert adapter.closable.closed == 1


def test_verified_mode_rejects_unknown_tool_capability(request_factory) -> None:
    adapter = OkAdapter()
    runtime = Runtime({"litellm_chat": adapter, "systemone_decision": adapter})
    request = request_factory(
        sdk_model="acme/mystery-model",
        api_model="mystery-model",
    )
    from compass_agent.schemas import GenerateInput, ToolDefinition, ToolFunction

    with_tools = replace(
        request,
        input=GenerateInput(
            messages=[ChatMessage(role="user", content="hi")],
            tools=[
                ToolDefinition(
                    function=ToolFunction(
                        name="lookup", parameters={"type": "object", "properties": {}}
                    )
                )
            ],
        ),
        context=replace(request.context, capability_mode="verified"),
    )
    result = run(runtime, with_tools)
    assert result.status == "failed"
    assert result.error is not None and result.error.code == "capability_not_supported"
    assert adapter.calls == 0


def test_binding_mismatch_is_rejected_before_outbound(request_factory) -> None:
    adapter = OkAdapter()
    runtime = Runtime({"litellm_chat": adapter, "systemone_decision": adapter})
    request = request_factory()
    mismatched = replace(
        request,
        context=replace(
            request.context,
            continuation_binding=request.context.continuation_binding.model_copy(
                update={"binding_version": 2}
            ),
        ),
    )
    result = run(runtime, mismatched)
    assert result.status == "failed"
    assert result.error is not None and result.error.code == "continuation_invalid"
    assert adapter.calls == 0


def test_decision_output_must_contain_answers(request_factory) -> None:
    adapter = DecisionAdapter()
    runtime = Runtime({"litellm_chat": adapter, "systemone_decision": adapter})
    request = request_factory(
        kind="decision",
        sdk_model=None,
        api_model="decision-model-preview",
        input_value=DecisionInput(
            state={"sales": 1},
            questions={"pick": DecisionQuestion(type="choice", criteria={"a": "A", "b": "B"})},
        ),
    )
    result = run(runtime, request)
    assert result.status == "failed"
    assert result.error is not None and result.error.code == "empty_output"
    assert adapter.calls == 1


def test_decision_completed_output_keeps_typed_answers(request_factory) -> None:
    adapter = DecisionAdapter(
        answers={"pick": DecisionAnswer(type="choice", choice="a", probabilities={"a": 1.0})}
    )
    runtime = Runtime({"litellm_chat": adapter, "systemone_decision": adapter})
    request = request_factory(
        kind="decision",
        sdk_model=None,
        api_model="decision-model-preview",
        input_value=DecisionInput(
            state={"sales": 1},
            questions={"pick": DecisionQuestion(type="choice", criteria={"a": "A", "b": "B"})},
        ),
    )
    result = run(runtime, request)
    assert result.status == "completed"
    assert isinstance(result.output, DecisionOutput)
    assert result.output.answers["pick"].choice == "a"
