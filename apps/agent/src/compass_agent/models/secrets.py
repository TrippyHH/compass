"""Credential envelopes and the narrow tool-continuation codec."""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from compass_agent.llm.contracts import ContinuationPayload
from compass_agent.llm.errors import RuntimeFailure
from compass_agent.llm.policy import OutputLimits
from compass_agent.settings import get_settings

PROVIDER_PURPOSE = "provider_key"
CONTINUATION_PURPOSE = "tool_continuation"
CONTINUATION_TTL_SECONDS = 900


class SecretUnavailable(RuntimeError):
    """The managed encryption key or cryptography dependency is missing."""


class SecretInvalid(RuntimeError):
    """A stored envelope is corrupt or does not match its binding."""


def _fernet_class() -> Any:
    # Lazy so management reads, contract export and health checks work without the
    # optional runtime wheel; saving or calling fails with a safe application error.
    try:
        from cryptography.fernet import Fernet
    except ImportError as exc:  # pragma: no cover - covered by dependency skips
        raise SecretUnavailable("cryptography is not installed") from exc
    return Fernet


@lru_cache(maxsize=1)
def _fernet() -> Any:
    key = get_settings().compass_model_encryption_key
    if not key:
        raise SecretUnavailable("COMPASS_MODEL_ENCRYPTION_KEY is not configured")
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    return _fernet_class()(base64.urlsafe_b64encode(digest))


@dataclass(frozen=True, slots=True)
class ProviderSecret:
    api_key: str = field(repr=False)
    connection_params: dict = field(default_factory=dict, repr=False)

    @property
    def has_key(self) -> bool:
        return bool(self.api_key)


def seal_provider_secret(
    provider_id: str,
    api_key: str,
    connection_params: dict | None,
) -> str:
    payload = {
        "purpose": PROVIDER_PURPOSE,
        "provider_id": str(provider_id),
        "key": api_key,
        "connection_params": connection_params or {},
    }
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return _fernet().encrypt(encoded.encode("utf-8")).decode("ascii")


def open_provider_secret(provider_id: str, ciphertext: str) -> ProviderSecret:
    if not ciphertext:
        return ProviderSecret(api_key="")
    fernet = _fernet()
    try:
        raw = fernet.decrypt(ciphertext.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise SecretInvalid("credential envelope is invalid") from exc
    except Exception as exc:
        raise SecretInvalid("credential envelope is unreadable") from exc
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise SecretInvalid("credential envelope is invalid") from exc
    if (
        not isinstance(payload, dict)
        or payload.get("purpose") != PROVIDER_PURPOSE
        or str(payload.get("provider_id")) != str(provider_id)
    ):
        raise SecretInvalid("credential envelope binding mismatch")
    params = payload.get("connection_params")
    return ProviderSecret(
        api_key=str(payload.get("key") or ""),
        connection_params=params if isinstance(params, dict) else {},
    )


class ModelContinuationCodec:
    """Only opens and seals the tool_continuation purpose."""

    def _cipher(self) -> Any:
        return _fernet()

    def open(self, token: str) -> ContinuationPayload:
        try:
            raw = self._cipher().decrypt(token.encode("ascii"), ttl=CONTINUATION_TTL_SECONDS)
        except SecretUnavailable as exc:
            raise RuntimeFailure("continuation_invalid") from exc
        except Exception as exc:
            raise RuntimeFailure("continuation_invalid") from exc
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise RuntimeFailure("continuation_invalid") from exc
        if not isinstance(payload, dict) or payload.get("purpose") != CONTINUATION_PURPOSE:
            raise RuntimeFailure("continuation_invalid")
        try:
            return ContinuationPayload.model_validate(payload.get("payload"))
        except Exception as exc:
            raise RuntimeFailure("continuation_invalid") from exc

    def seal(self, payload: ContinuationPayload) -> str:
        envelope = {
            "purpose": CONTINUATION_PURPOSE,
            "payload": payload.model_dump(mode="json"),
        }
        token = (
            self._cipher()
            .encrypt(
                json.dumps(envelope, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            )
            .decode("ascii")
        )
        if len(token.encode("utf-8")) > OutputLimits().continuation_bytes:
            raise RuntimeFailure("continuation_too_large")
        return token
