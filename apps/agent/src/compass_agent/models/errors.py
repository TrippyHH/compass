"""Application errors for model configuration and call admission."""

from typing import Literal

ModelErrorCode = Literal[
    "validation_failed",
    "forbidden",
    "not_found",
    "conflict",
    "call_already_exists",
    "chat_model_not_supported",
    "model_unavailable",
    "unavailable",
    "model_timeout",
    "capability_not_supported",
    "intent_unavailable",
    "intent_invalid",
    "route_binding_missing",
    "record_write_failed",
]


class ModelError(Exception):
    def __init__(self, code: ModelErrorCode, message: str = "", current_version: int | None = None):
        self.code = code
        self.message = message or code
        self.current_version = current_version
        super().__init__(self.message)
