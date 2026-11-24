"""Private tool-continuation payloads.

Only the tool_continuation purpose is allowed here. Credentials never pass through
this module; the adapter receives a narrow ContinuationCodec implementation.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence

from compass_agent.schemas import ChatMessage

from .contracts import (
    ContinuationBinding,
    ContinuationCodec,
    ContinuationPayload,
)
from .errors import RuntimeFailure
from .policy import OutputLimits

PRIVATE_MESSAGE_FIELDS = ("reasoning_content",)


def visible_digest(messages: Sequence[ChatMessage]) -> str:
    canonical = json.dumps(
        [message.model_dump(mode="json", exclude_none=True) for message in messages],
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def seal(
    codec: ContinuationCodec,
    *,
    binding: ContinuationBinding,
    messages: Sequence[ChatMessage],
    private_assistants: dict[str, str | dict],
    allowed_tool_call_ids: Sequence[str],
) -> str:
    payload = ContinuationPayload(
        binding=binding,
        visible_count=max(1, len(messages)),
        visible_digest=visible_digest(messages),
        private_assistants=private_assistants,
        allowed_tool_call_ids=list(allowed_tool_call_ids),
    )
    token = codec.seal(payload)
    if len(token.encode("utf-8")) > OutputLimits().continuation_bytes:
        raise RuntimeFailure("continuation_too_large")
    return token


def restore(
    codec: ContinuationCodec,
    token: str,
    *,
    binding: ContinuationBinding,
    messages: Sequence[ChatMessage],
) -> dict[str, str | dict]:
    payload = codec.open(token)
    if payload.binding != binding:
        raise RuntimeFailure("continuation_invalid")
    if len(messages) <= payload.visible_count:
        raise RuntimeFailure("continuation_invalid")
    prefix = list(messages)[: payload.visible_count]
    if visible_digest(prefix) != payload.visible_digest:
        raise RuntimeFailure("continuation_invalid")
    appended = list(messages)[payload.visible_count :]
    if not appended or any(message.role != "tool" for message in appended):
        raise RuntimeFailure("continuation_invalid")
    ids = [message.tool_call_id for message in appended]
    if None in ids or len(ids) != len(set(ids)):
        raise RuntimeFailure("continuation_invalid")
    if set(ids) != set(payload.allowed_tool_call_ids):
        raise RuntimeFailure("continuation_invalid")
    return payload.private_assistants


def apply_private_assistants(
    messages: Sequence[ChatMessage],
    private_assistants: dict[str, str | dict],
) -> list[dict]:
    """Rebuild wire messages, restoring only whitelisted private assistant fields."""

    wire = [message.model_dump(mode="json", exclude_none=True) for message in messages]
    for key, value in private_assistants.items():
        try:
            index = int(key)
        except ValueError as exc:
            raise RuntimeFailure("continuation_invalid") from exc
        if index < 0 or index >= len(messages) or messages[index].role != "assistant":
            raise RuntimeFailure("continuation_invalid")
        if not messages[index].tool_calls:
            raise RuntimeFailure("continuation_invalid")
        if isinstance(value, str):
            wire[index]["reasoning_content"] = value
            continue
        if not isinstance(value, dict):
            raise RuntimeFailure("continuation_invalid")
        for field in PRIVATE_MESSAGE_FIELDS:
            field_value = value.get(field)
            if isinstance(field_value, str):
                wire[index][field] = field_value
    return wire
