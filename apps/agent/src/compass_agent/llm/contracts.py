"""In-process execution contracts shared by the Runtime, adapters and ModelService."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Annotated, Literal, Protocol
from uuid import UUID

from pydantic import Field, SecretStr

from compass_agent.schemas import (
    DecisionAnswer,
    DecisionInput,
    GenerateInput,
    ModelUsage,
    OutputValidation,
    PlatformScope,
    ReasoningMetaEvent,
    StrictModel,
    TenantScope,
    TextDeltaEvent,
    ToolCall,
    ToolDeltaEvent,
)

from .errors import RuntimeErrorCode, RuntimeErrorInfo, RuntimeFailure
from .policy import CallPolicy, OutputLimits
from .transport import AsyncClosable, CallResources

type ModelKind = Literal["llm", "decision"]
type AdapterKind = Literal["litellm_chat", "systemone_decision"]
type TerminalStatus = Literal["completed", "failed", "canceled", "incomplete"]
type CapabilityMode = Literal["verified", "diagnostic"]
type ModelInput = Annotated[GenerateInput | DecisionInput, Field(discriminator="kind")]
type RuntimeDelta = TextDeltaEvent | ToolDeltaEvent | ReasoningMetaEvent
type DeltaSink = Callable[[RuntimeDelta], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class ModelSpec:
    provider_id: UUID
    config_version: int
    binding_version: int
    vendor: str
    kind: ModelKind
    adapter: AdapterKind
    api_model: str
    sdk_model: str | None
    api_base: str

    def __post_init__(self) -> None:
        if self.config_version < 1 or self.binding_version < 1:
            raise ValueError("versions must be positive")
        if self.binding_version > self.config_version:
            raise ValueError("binding_version must not exceed config_version")
        expected = "litellm_chat" if self.kind == "llm" else "systemone_decision"
        if self.adapter != expected:
            raise ValueError("model kind and adapter do not match")
        if not self.api_model:
            raise ValueError("api_model is required")


@dataclass(frozen=True, slots=True)
class ProviderAccess:
    spec: ModelSpec
    api_key: SecretStr = field(repr=False)
    connection_params: dict = field(default_factory=dict, repr=False)


class ContinuationBinding(StrictModel):
    actor: UUID
    scope: Annotated[PlatformScope | TenantScope, Field(discriminator="kind")]
    provider: UUID
    binding_version: int = Field(ge=1)


class ContinuationPayload(StrictModel):
    binding: ContinuationBinding
    visible_count: int = Field(ge=1)
    visible_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    private_assistants: dict[str, str | dict] = Field(default_factory=dict, repr=False)
    allowed_tool_call_ids: list[str] = Field(min_length=1, max_length=16)


class ContinuationCodec(Protocol):
    def open(self, token: str) -> ContinuationPayload:
        """Verify purpose and Fernet TTL=900; invalid tokens raise RuntimeFailure."""
        ...

    def seal(self, payload: ContinuationPayload) -> str:
        """Encrypt only a tool_continuation payload and enforce the ciphertext limit."""
        ...


@dataclass(frozen=True, slots=True)
class InvocationContext:
    call_id: UUID
    capability_mode: CapabilityMode
    continuation_binding: ContinuationBinding


@dataclass(frozen=True, slots=True)
class ExecutionRequest:
    context: InvocationContext
    access: ProviderAccess
    input: ModelInput = field(repr=False)
    policy: CallPolicy


@dataclass(frozen=True, slots=True)
class GenerationOutput:
    kind: Literal["generate"] = "generate"
    text: str = field(default="", repr=False)
    tool_calls: tuple[ToolCall, ...] = field(default=(), repr=False)
    continuation_token: str | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class DecisionOutput:
    kind: Literal["decision"] = "decision"
    answers: dict[str, DecisionAnswer] = field(default_factory=dict, repr=False)


type ModelOutput = GenerationOutput | DecisionOutput


def empty_output_for(request_input: ModelInput) -> ModelOutput:
    if request_input.kind == "decision":
        return DecisionOutput()
    return GenerationOutput()


@dataclass(slots=True)
class ProviderProgress:
    output: ModelOutput = field(repr=False)
    usage: ModelUsage = field(default_factory=ModelUsage)
    validation: OutputValidation = field(default_factory=OutputValidation)
    response_model: str | None = None
    upstream_request_id: str | None = None
    finish_reason: str | None = None
    normal_eof: bool = False


@dataclass(slots=True)
class CallSession:
    resources: CallResources
    progress: ProviderProgress
    emit: DeltaSink | None = field(default=None, repr=False)

    async def publish(self, delta: RuntimeDelta) -> None:
        """Publish one already-normalised public delta under back-pressure."""

        if delta.type in {"text_delta", "tool_delta"}:
            self.resources.mark_partial()
        if self.emit is not None:
            await self.emit(delta)


class ProviderAdapter(Protocol):
    async def invoke(self, request: ExecutionRequest, session: CallSession) -> ModelOutput:
        """Normalize the provider protocol, update progress, and return validated output.

        RuntimeFailure carries a safe error code; CancelledError must propagate.
        Register clients/responses in session.resources before waiting on network IO.
        Update normalized partial output before awaiting the public delta sink.
        """
        ...


@dataclass(frozen=True, slots=True)
class RuntimeResult:
    call_id: UUID
    provider_id: UUID
    config_version: int
    binding_version: int
    requested_model: str
    status: TerminalStatus
    duration_ms: int
    output: ModelOutput = field(repr=False)
    usage: ModelUsage = field(default_factory=ModelUsage)
    validation: OutputValidation = field(default_factory=OutputValidation)
    response_model: str | None = None
    upstream_request_id: str | None = None
    finish_reason: str | None = None
    error: RuntimeErrorInfo | None = None
    upstream_outcome_unknown: bool = False
    upstream_sent: bool = False


class RuntimeCancelled(asyncio.CancelledError):
    def __init__(self, result: RuntimeResult) -> None:
        self.result = result
        super().__init__("model call canceled")


__all__ = [
    "AdapterKind",
    "AsyncClosable",
    "CallPolicy",
    "CallResources",
    "CallSession",
    "CapabilityMode",
    "ContinuationBinding",
    "ContinuationCodec",
    "ContinuationPayload",
    "DecisionOutput",
    "DeltaSink",
    "ExecutionRequest",
    "GenerationOutput",
    "InvocationContext",
    "ModelInput",
    "ModelKind",
    "ModelOutput",
    "ModelSpec",
    "OutputLimits",
    "ProviderAccess",
    "ProviderAdapter",
    "ProviderProgress",
    "RuntimeCancelled",
    "RuntimeDelta",
    "RuntimeErrorCode",
    "RuntimeErrorInfo",
    "RuntimeFailure",
    "RuntimeResult",
    "TerminalStatus",
]
