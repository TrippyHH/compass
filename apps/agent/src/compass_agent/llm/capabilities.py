"""Protocol, model and parameter capability rules shared by save, list and call paths."""

from __future__ import annotations

import json
from typing import Literal

from compass_agent.schemas import (
    Capability,
    DecisionInput,
    GenerateInput,
    ModelCapabilities,
)

from .contracts import ModelInput, ModelKind, ModelSpec
from .errors import RuntimeFailure
from .policy import OutputLimits

CapabilityState = Literal["supported", "unsupported", "unknown"]


def capability(state: CapabilityState, reason: str = "") -> Capability:
    return Capability(state=state, reason=reason)


def decision_capabilities() -> ModelCapabilities:
    unsupported = "决策协议不提供该能力"
    return ModelCapabilities(
        sync=capability("supported"),
        stream=capability("unsupported", "System One 决策协议为非流式"),
        tools=capability("unsupported", unsupported),
        thinking=capability("unsupported", unsupported),
        json_output=capability("unsupported", "决策结果使用 answers 结构"),
        schema_output=capability("unsupported", "决策结果使用 answers 结构"),
        reasoning_efforts=[],
    )


def litellm_capabilities(
    *,
    tools: bool | None,
    thinking: bool | None,
    schema_output: bool | None,
    json_output: bool | None,
    reasoning_efforts: list[str] | None = None,
) -> ModelCapabilities:
    def flag(value: bool | None, reason: str) -> Capability:
        if value is True:
            return capability("supported")
        if value is False:
            return capability("unsupported", reason)
        return capability("unknown", "SDK 目录未登记，可在管理端诊断")

    return ModelCapabilities(
        sync=capability("supported"),
        stream=capability("supported"),
        tools=flag(tools, "模型未声明工具调用能力"),
        thinking=flag(thinking, "模型未声明推理能力"),
        json_output=flag(
            bool(json_output or schema_output)
            if json_output is not None or schema_output is not None
            else None,
            "模型未声明 JSON 输出能力",
        ),
        schema_output=flag(schema_output, "模型未声明 JSON Schema 输出能力"),
        reasoning_efforts=reasoning_efforts or [],
    )


def unknown_llm_capabilities() -> ModelCapabilities:
    unknown = "目录未收录该绑定，可在管理端诊断"
    return ModelCapabilities(
        sync=capability("supported"),
        stream=capability("unknown", unknown),
        tools=capability("unknown", unknown),
        thinking=capability("unknown", unknown),
        json_output=capability("unknown", unknown),
        schema_output=capability("unknown", unknown),
        reasoning_efforts=[],
    )


def resolve(spec: ModelSpec) -> ModelCapabilities:
    # Imported lazily so capability rules stay importable without building the catalog.
    from .catalog import find_record

    if spec.kind == "decision":
        return decision_capabilities()
    record = find_record(spec.sdk_model or spec.api_model, spec.api_model, spec.vendor)
    if record is None:
        return unknown_llm_capabilities()
    return record.capabilities


def chat_supported(kind: ModelKind, adapter: str) -> bool:
    return kind == "llm" and adapter == "litellm_chat"


def chat_support_reason(kind: ModelKind, adapter: str) -> str:
    if kind == "decision":
        return "决策模型本期仅支持管理端测试"
    if adapter != "litellm_chat":
        return "当前执行适配器不支持 Chat"
    return ""


def _reject(code: str = "capability_not_supported") -> None:
    raise RuntimeFailure(code)  # type: ignore[arg-type]


def _require(capability_value: Capability, mode: str, label: str) -> None:
    if capability_value.state == "unsupported":
        _reject()
    if capability_value.state == "unknown" and mode == "verified":
        _reject()


def _input_bytes(value: object) -> int:
    return len(json.dumps(value, ensure_ascii=False, default=str).encode("utf-8"))


def validate_input(spec: ModelSpec, request: ModelInput, mode: str) -> None:
    if spec.kind == "llm" and not isinstance(request, GenerateInput):
        _reject("validation_failed")
    if spec.kind == "decision" and not isinstance(request, DecisionInput):
        _reject("validation_failed")

    limits = OutputLimits()
    caps = resolve(spec)
    if isinstance(request, DecisionInput):
        if len(request.questions) > 16:
            _reject("validation_failed")
        _require(caps.sync, mode, "sync")
        return

    if len(request.messages) > limits.messages or len(request.tools) > limits.tools:
        _reject("validation_failed")
    if _input_bytes([message.model_dump(mode="json") for message in request.messages]) > (
        limits.visible_input_bytes
    ):
        _reject("validation_failed")
    if request.output_schema is not None and _input_bytes(request.output_schema) > (
        limits.schema_bytes
    ):
        _reject("validation_failed")

    if request.stream:
        _require(caps.stream, mode, "stream")
    if request.tools:
        _require(caps.tools, mode, "tools")
    if request.thinking:
        _require(caps.thinking, mode, "thinking")
        if request.reasoning_effort and caps.reasoning_efforts:
            if request.reasoning_effort not in caps.reasoning_efforts:
                _reject()
    if request.output_format == "json":
        _require(caps.json_output, mode, "json_output")
    if request.output_format == "schema":
        _require(caps.schema_output, mode, "schema_output")
    if request.continuation_token and spec.kind != "llm":
        _reject("continuation_invalid")
