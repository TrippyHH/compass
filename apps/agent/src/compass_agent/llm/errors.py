"""Execution error vocabulary. No HTTP status or provider text belongs here."""

from typing import Literal

type RuntimeErrorCode = Literal[
    "validation_failed",
    "capability_not_supported",
    "model_authentication_failed",
    "model_rate_limited",
    "model_timeout",
    "model_unavailable_upstream",
    "model_request_rejected",
    "provider_protocol_error",
    "stream_interrupted",
    "output_truncated",
    "model_refused",
    "empty_output",
    "invalid_json",
    "schema_validation_failed",
    "invalid_tool_arguments",
    "continuation_invalid",
    "continuation_too_large",
    "response_too_large",
    "internal_error",
]


class RuntimeErrorInfo:
    """Safe, serialisable execution error information."""

    __slots__ = ("code",)

    def __init__(self, code: RuntimeErrorCode) -> None:
        self.code = code

    def __repr__(self) -> str:
        return f"RuntimeErrorInfo(code={self.code!r})"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, RuntimeErrorInfo) and other.code == self.code

    def __hash__(self) -> int:
        return hash(self.code)


class RuntimeFailure(Exception):
    """Raised by capability checks, adapters and transports with a safe code."""

    def __init__(self, code: RuntimeErrorCode) -> None:
        self.error = RuntimeErrorInfo(code)
        super().__init__(code)
