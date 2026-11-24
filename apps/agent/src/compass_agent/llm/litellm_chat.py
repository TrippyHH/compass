"""LiteLLM generation adapter.

The adapter performs exactly one SDK invocation, normalises stream fragments into
public deltas, validates structured/tool output locally and seals private provider
state into a short-lived continuation token.
"""

from __future__ import annotations

import inspect
import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from compass_agent.schemas import (
    ChatMessage,
    GenerateInput,
    ModelUsage,
    OutputValidation,
    ReasoningMetaEvent,
    TextDeltaEvent,
    ToolCall,
    ToolCallFunction,
    ToolDeltaEvent,
)

from . import continuation
from .contracts import (
    CallSession,
    ContinuationCodec,
    ExecutionRequest,
    GenerationOutput,
    ModelOutput,
)
from .errors import RuntimeErrorCode, RuntimeFailure

Completion = Callable[..., Awaitable[Any]]

_RESERVED_CONNECTION_KEYS = frozenset(
    {
        "api_key",
        "api_base",
        "base_url",
        "model",
        "messages",
        "stream",
        "stream_options",
        "timeout",
        "num_retries",
        "max_retries",
        "drop_params",
        "fallbacks",
        "fallback_models",
        "client",
        "clients",
        "callbacks",
        "logger",
        "retry_policy",
        "mock_response",
        "response_format",
        "tools",
        "tool_choice",
        "thinking",
        "reasoning_effort",
        "max_tokens",
        "n",
        "extra_body",
        "strict",
    }
)
_CONNECTION_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
_CONNECTION_BYTES = 16_384


@dataclass(slots=True)
class _Draft:
    text: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    private_reasoning: str = field(default="", repr=False)


def _field(value: object, name: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _text_of(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict) and isinstance(part.get("text"), str):
                parts.append(part["text"])
            else:
                text_value = getattr(part, "text", None)
                if isinstance(text_value, str):
                    parts.append(text_value)
        return "".join(parts)
    return ""


def _non_negative(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value >= 0:
        return value
    return None


def _usage(value: object) -> ModelUsage:
    if value is None:
        return ModelUsage()
    details = _field(value, "completion_tokens_details")
    return ModelUsage(
        input_tokens=_non_negative(_field(value, "prompt_tokens"))
        or _non_negative(_field(value, "input_tokens")),
        output_tokens=_non_negative(_field(value, "completion_tokens"))
        or _non_negative(_field(value, "output_tokens")),
        reasoning_tokens=_non_negative(_field(details, "reasoning_tokens")),
    )


def _map_sdk_error(error: BaseException) -> RuntimeErrorCode:
    status = getattr(error, "status_code", None)
    if isinstance(status, int):
        if status in {401, 403}:
            return "model_authentication_failed"
        if status == 429:
            return "model_rate_limited"
        if status in {408, 504}:
            return "model_timeout"
        if status >= 500:
            return "model_unavailable_upstream"
        if status in {400, 404, 409, 422}:
            # The endpoint answered and refused the request itself (unknown model, bad参数,
            # missing permission on that route); the response was recognised, so it is not a
            # protocol error.
            return "model_request_rejected"
        if status >= 400:
            return "provider_protocol_error"
    name = type(error).__name__.casefold()
    if "timeout" in name:
        return "model_timeout"
    if "authentication" in name or "permission" in name:
        return "model_authentication_failed"
    if "ratelimit" in name or "rate_limit" in name:
        return "model_rate_limited"
    if "connection" in name:
        return "model_unavailable_upstream"
    return "internal_error"


class _StreamCloser:
    def __init__(self, response: object) -> None:
        self._response = response

    async def aclose(self) -> None:
        closer = getattr(self._response, "aclose", None) or getattr(self._response, "close", None)
        if closer is None:
            return
        result = closer()
        if inspect.isawaitable(result):
            await result


class _ToolAccumulator:
    def __init__(self) -> None:
        self.items: dict[int, dict[str, Any]] = {}

    def feed(
        self,
        index: int,
        call_id: str | None,
        name: str | None,
        arguments: str | None,
    ) -> None:
        item = self.items.setdefault(index, {"id": "", "name": "", "arguments": ""})
        if call_id:
            item["id"] = call_id
        if name:
            item["name"] = name
        if arguments:
            item["arguments"] += arguments

    def finalize(self, declared: list) -> tuple[ToolCall, ...]:
        schema_module = _jsonschema()
        by_name = {tool.function.name: tool for tool in declared}
        calls: list[ToolCall] = []
        for index in sorted(self.items):
            item = self.items[index]
            name = str(item["name"])
            identifier = str(item["id"])
            arguments = str(item["arguments"])
            tool = by_name.get(name)
            if tool is None or not identifier:
                raise RuntimeFailure("invalid_tool_arguments")
            try:
                parsed = json.loads(arguments or "{}")
            except ValueError as exc:
                raise RuntimeFailure("invalid_tool_arguments") from exc
            if not isinstance(parsed, dict):
                raise RuntimeFailure("invalid_tool_arguments")
            try:
                schema_module.Draft202012Validator(tool.function.parameters).validate(parsed)
            except (schema_module.SchemaError, schema_module.ValidationError) as exc:
                raise RuntimeFailure("invalid_tool_arguments") from exc
            calls.append(
                ToolCall(id=identifier, function=ToolCallFunction(name=name, arguments=arguments))
            )
        if not calls:
            raise RuntimeFailure("invalid_tool_arguments")
        return tuple(calls)


def _merge_connection_params(params: dict | None) -> dict:
    if not params:
        return {}
    try:
        encoded = json.dumps(params, ensure_ascii=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise RuntimeFailure("validation_failed") from exc
    if len(encoded) > _CONNECTION_BYTES:
        raise RuntimeFailure("validation_failed")
    merged: dict = {}
    for key, value in params.items():
        if not isinstance(key, str) or not _CONNECTION_KEY.match(key):
            raise RuntimeFailure("validation_failed")
        if key.casefold() in _RESERVED_CONNECTION_KEYS:
            raise RuntimeFailure("validation_failed")
        merged[key] = value
    return merged


def _with_json_instruction(messages: list[dict], schema: dict | None) -> list[dict]:
    if len(messages) >= 64:
        raise RuntimeFailure("validation_failed")
    instruction = "只输出一个 JSON 对象，不要包含 Markdown 代码块或额外说明。"
    if schema is not None:
        instruction += "结果必须符合以下 JSON Schema：\n" + json.dumps(
            schema, ensure_ascii=False, sort_keys=True
        )
    return [*messages, {"role": "system", "content": instruction}]


def _jsonschema():
    try:
        import jsonschema
    except ImportError as exc:  # pragma: no cover - dependency is part of the locked runtime
        raise RuntimeFailure("internal_error") from exc
    return jsonschema


def _schema_ref_paths(schema: object, path: str = "$") -> list[str]:
    paths: list[str] = []
    if isinstance(schema, dict):
        for key, value in schema.items():
            if key == "$ref" and (not isinstance(value, str) or not value.startswith("#")):
                paths.append(f"{path}.$ref")
            else:
                paths.extend(_schema_ref_paths(value, f"{path}.{key}"))
    elif isinstance(schema, list):
        for index, value in enumerate(schema):
            paths.extend(_schema_ref_paths(value, f"{path}[{index}]"))
    return paths


class LiteLLMChatAdapter:
    def __init__(
        self,
        continuation_codec: ContinuationCodec | None = None,
        completion: Completion | None = None,
    ) -> None:
        self._codec = continuation_codec
        self._completion_fn = completion

    async def invoke(self, request: ExecutionRequest, session: CallSession) -> ModelOutput:
        input_value = request.input
        if not isinstance(input_value, GenerateInput):
            raise RuntimeFailure("validation_failed")
        progress = session.progress
        limits = request.policy.limits
        messages = self._prepare_messages(request, input_value)
        params = self._params(request, input_value, messages)
        if input_value.thinking:
            await session.publish(
                ReasoningMetaEvent(
                    call_id=request.context.call_id,
                    thinking=True,
                    reasoning_effort=input_value.reasoning_effort or "high",
                )
            )
        draft = (
            await self._stream(request, session, params)
            if input_value.stream
            else await self._complete(request, session, params)
        )
        if len(draft.text.encode("utf-8")) > limits.visible_output_bytes:
            raise RuntimeFailure("response_too_large")
        self._validate_structured(input_value, progress, draft)
        output = GenerationOutput(text=draft.text, tool_calls=draft.tool_calls)
        if output.tool_calls and self._codec is not None:
            output = self._seal_continuation(request, input_value, draft, output)
        progress.output = output
        return output

    def _prepare_messages(
        self, request: ExecutionRequest, input_value: GenerateInput
    ) -> list[dict]:
        if not input_value.continuation_token:
            return [
                message.model_dump(mode="json", exclude_none=True)
                for message in input_value.messages
            ]
        if self._codec is None:
            raise RuntimeFailure("continuation_invalid")
        private = continuation.restore(
            self._codec,
            input_value.continuation_token,
            binding=request.context.continuation_binding,
            messages=input_value.messages,
        )
        return continuation.apply_private_assistants(input_value.messages, private)

    def _params(
        self,
        request: ExecutionRequest,
        input_value: GenerateInput,
        messages: list[dict],
    ) -> dict:
        spec = request.access.spec
        remaining = request.policy.remaining_seconds
        params: dict[str, Any] = {
            "model": spec.sdk_model or spec.api_model,
            "messages": messages,
            "stream": input_value.stream,
            "max_tokens": input_value.max_tokens,
            "num_retries": 0,
            "max_retries": 0,
            "drop_params": False,
            "timeout": max(1.0, min(180.0, remaining)),
        }
        if spec.api_base:
            params["api_base"] = spec.api_base
        api_key = request.access.api_key.get_secret_value()
        if api_key:
            params["api_key"] = api_key
        params.update(_merge_connection_params(request.access.connection_params))
        if input_value.stream:
            params["stream_options"] = {"include_usage": True}
        if input_value.tools:
            params["tools"] = [tool.model_dump(mode="json") for tool in input_value.tools]
        if input_value.output_format != "text":
            params["response_format"] = {"type": "json_object"}
            params["messages"] = _with_json_instruction(messages, input_value.output_schema)
        if input_value.thinking:
            if spec.vendor == "deepseek":
                params["extra_body"] = {"thinking": {"type": "enabled"}}
            params["reasoning_effort"] = input_value.reasoning_effort or "high"
        elif spec.vendor == "deepseek":
            params["extra_body"] = {"thinking": {"type": "disabled"}}
        return params

    async def _call_sdk(self, **params: Any) -> Any:
        if self._completion_fn is not None:
            return await self._completion_fn(**params)
        import litellm

        return await litellm.acompletion(**params)

    async def _complete(
        self,
        request: ExecutionRequest,
        session: CallSession,
        params: dict,
    ) -> _Draft:
        progress = session.progress
        session.resources.mark_sent()
        try:
            response = await self._call_sdk(**params)
        except RuntimeFailure:
            raise
        except Exception as exc:
            raise RuntimeFailure(_map_sdk_error(exc)) from exc
        progress.usage = _usage(_field(response, "usage"))
        progress.response_model = _field(response, "model")
        choices = _field(response, "choices") or []
        if not choices:
            raise RuntimeFailure("provider_protocol_error")
        choice = choices[0]
        message = _field(choice, "message")
        progress.finish_reason = _field(choice, "finish_reason")
        progress.normal_eof = progress.finish_reason is not None
        raw_tools = _field(message, "tool_calls") or []
        input_value = request.input
        assert isinstance(input_value, GenerateInput)
        tool_calls = (
            ()
            if progress.finish_reason in {"length", "content_filter"}
            else self._complete_tools(input_value, raw_tools)
        )
        return _Draft(
            text=_text_of(_field(message, "content")),
            tool_calls=tool_calls,
            private_reasoning=str(_field(message, "reasoning_content") or ""),
        )

    @staticmethod
    def _complete_tools(input_value: GenerateInput, raw_tools: list) -> tuple[ToolCall, ...]:
        if not raw_tools:
            return ()
        accumulator = _ToolAccumulator()
        for index, raw in enumerate(raw_tools):
            function = _field(raw, "function")
            accumulator.feed(
                index,
                _field(raw, "id"),
                _field(function, "name"),
                _field(function, "arguments"),
            )
        return accumulator.finalize(input_value.tools)

    async def _stream(
        self,
        request: ExecutionRequest,
        session: CallSession,
        params: dict,
    ) -> _Draft:
        input_value = request.input
        assert isinstance(input_value, GenerateInput)
        progress = session.progress
        call_id = request.context.call_id
        text_parts: list[str] = []
        private_parts: list[str] = []
        private_bytes = 0
        tools = _ToolAccumulator()
        finish_seen = False
        session.resources.mark_sent()
        try:
            stream = await self._call_sdk(**params)
        except RuntimeFailure:
            raise
        except Exception as exc:
            raise RuntimeFailure(_map_sdk_error(exc)) from exc
        session.resources.register(_StreamCloser(stream))
        try:
            async for chunk in stream:
                usage_value = _field(chunk, "usage")
                if usage_value is not None:
                    progress.usage = _usage(usage_value)
                model_value = _field(chunk, "model")
                if model_value:
                    progress.response_model = str(model_value)
                choices = _field(chunk, "choices") or []
                if not choices:
                    continue
                choice = choices[0]
                delta = _field(choice, "delta") or {}
                reasoning = _field(delta, "reasoning_content") or _field(delta, "reasoning")
                if isinstance(reasoning, str) and reasoning:
                    private_parts.append(reasoning)
                    private_bytes += len(reasoning.encode("utf-8"))
                    if private_bytes > request.policy.limits.private_reasoning_bytes:
                        raise RuntimeFailure("response_too_large")
                text = _text_of(_field(delta, "content"))
                if text:
                    text_parts.append(text)
                    progress.output = GenerationOutput(text="".join(text_parts))
                    await session.publish(TextDeltaEvent(call_id=call_id, text=text))
                for tool_delta in _field(delta, "tool_calls") or []:
                    index = _field(tool_delta, "index")
                    if not isinstance(index, int):
                        index = len(tools.items)
                    function = _field(tool_delta, "function")
                    name = _field(function, "name")
                    arguments = _field(function, "arguments")
                    tools.feed(
                        index,
                        _field(tool_delta, "id"),
                        name if isinstance(name, str) else None,
                        arguments if isinstance(arguments, str) else None,
                    )
                    await session.publish(
                        ToolDeltaEvent(
                            call_id=call_id,
                            index=index,
                            id=_field(tool_delta, "id"),
                            name=name if isinstance(name, str) else None,
                            arguments=arguments if isinstance(arguments, str) else "",
                        )
                    )
                finish = _field(choice, "finish_reason")
                if finish:
                    finish_seen = True
                    progress.finish_reason = str(finish)
        except RuntimeFailure:
            raise
        except Exception as exc:
            raise RuntimeFailure(_map_sdk_error(exc)) from exc
        progress.normal_eof = finish_seen
        if not finish_seen:
            raise RuntimeFailure("stream_interrupted")
        tool_calls = (
            tools.finalize(input_value.tools)
            if tools.items and progress.finish_reason not in {"length", "content_filter"}
            else ()
        )
        return _Draft(
            text="".join(text_parts),
            tool_calls=tool_calls,
            private_reasoning="".join(private_parts),
        )

    @staticmethod
    def _validate_structured(
        input_value: GenerateInput,
        progress: object,
        draft: _Draft,
    ) -> None:
        from .contracts import ProviderProgress

        assert isinstance(progress, ProviderProgress)
        if input_value.output_format == "text":
            return
        if not draft.text.strip():
            return
        try:
            parsed = json.loads(draft.text)
        except ValueError as exc:
            progress.validation = OutputValidation(status="invalid", mode=input_value.output_format)
            raise RuntimeFailure("invalid_json") from exc
        if input_value.output_format == "json":
            progress.validation = OutputValidation(status="valid", mode="json")
            return
        schema_module = _jsonschema()
        schema = input_value.output_schema or {}
        bad_refs = _schema_ref_paths(schema)
        if bad_refs:
            progress.validation = OutputValidation(
                status="invalid", mode="schema", paths=sorted(set(bad_refs))[:20]
            )
            raise RuntimeFailure("validation_failed")
        try:
            validator = schema_module.Draft202012Validator(schema)
        except schema_module.SchemaError as exc:
            progress.validation = OutputValidation(status="invalid", mode="schema")
            raise RuntimeFailure("validation_failed") from exc
        errors = sorted(validator.iter_errors(parsed), key=lambda error: list(error.absolute_path))
        if errors:
            paths = ["/".join(str(part) for part in error.absolute_path) for error in errors]
            progress.validation = OutputValidation(
                status="invalid", mode="schema", paths=[path for path in paths if path][:20]
            )
            raise RuntimeFailure("schema_validation_failed")
        progress.validation = OutputValidation(status="valid", mode="schema")

    def _seal_continuation(
        self,
        request: ExecutionRequest,
        input_value: GenerateInput,
        draft: _Draft,
        output: GenerationOutput,
    ) -> GenerationOutput:
        assert self._codec is not None
        assistant = {
            "role": "assistant",
            "content": output.text or None,
            "tool_calls": [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {"name": call.function.name, "arguments": call.function.arguments},
                }
                for call in output.tool_calls
            ],
        }
        assistant_index = len(input_value.messages)
        history = [message.model_copy(deep=True) for message in input_value.messages]
        history.append(ChatMessage.model_validate(assistant))
        private_assistants: dict[str, str | dict] = {}
        if draft.private_reasoning:
            private_assistants[str(assistant_index)] = {
                "reasoning_content": draft.private_reasoning
            }
        token = continuation.seal(
            self._codec,
            binding=request.context.continuation_binding,
            messages=history,
            private_assistants=private_assistants,
            allowed_tool_call_ids=[call.id for call in output.tool_calls],
        )
        return GenerationOutput(
            text=output.text,
            tool_calls=output.tool_calls,
            continuation_token=token,
        )
