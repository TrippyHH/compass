"""Endpoint, alias, connection-parameter and field validation shared by all paths."""

from __future__ import annotations

import json
import re
from urllib.parse import urlsplit

from compass_agent.settings import get_settings

from .errors import ModelError

CONNECTION_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
CONNECTION_BYTES = 16_384
RESERVED_CONNECTION_KEYS = frozenset(
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
_LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def allowed_origins() -> set[str]:
    values = get_settings().compass_model_allowed_origins.split(",")
    return {value.strip().rstrip("/") for value in values if value.strip()}


def _origin(scheme: str, host: str, port: int | None) -> str:
    default = 443 if scheme == "https" else 80
    suffix = "" if port in (None, default) else f":{port}"
    return f"{scheme}://{host}{suffix}"


def validate_base_url(value: str, *, required: bool = False) -> str:
    text = value.strip()
    if not text:
        if required:
            raise ModelError("validation_failed", "该模型必须填写 Base URL。")
        return ""
    if len(text) > 2048:
        raise ModelError("validation_failed", "Base URL 过长。")
    parts = urlsplit(text)
    local = parts.hostname in _LOCAL_HOSTS
    local_http = parts.scheme == "http" and local and get_settings().compass_model_allow_local_http
    if parts.scheme != "https" and not local_http:
        raise ModelError("validation_failed", "Base URL 必须使用 HTTPS。")
    if parts.username or parts.password:
        raise ModelError("validation_failed", "Base URL 不能包含凭据。")
    if parts.query or parts.fragment:
        raise ModelError("validation_failed", "Base URL 不能包含查询串或片段。")
    if not parts.hostname:
        raise ModelError("validation_failed", "Base URL 缺少主机名。")
    origin = _origin(parts.scheme, parts.hostname, parts.port)
    if origin not in allowed_origins() and not local_http:
        raise ModelError("validation_failed", "该端点不在允许的连接范围内。")
    return text.rstrip("/")


def validate_connection_params(value: dict | None) -> dict | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ModelError("validation_failed", "供应商连接参数必须是 JSON 对象。")
    try:
        encoded = json.dumps(value, ensure_ascii=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ModelError("validation_failed", "供应商连接参数不是有效 JSON。") from exc
    if len(encoded) > CONNECTION_BYTES:
        raise ModelError("validation_failed", "供应商连接参数过大。")
    for key in value:
        if not isinstance(key, str) or not CONNECTION_KEY.match(key):
            raise ModelError("validation_failed", "供应商连接参数包含无效键名。")
        if key.casefold() in RESERVED_CONNECTION_KEYS:
            raise ModelError("validation_failed", "供应商连接参数不允许覆盖调用配置。")
    return dict(value)


def validate_alias(value: str) -> str:
    alias = value.strip()
    if not 1 <= len(alias) <= 80:
        raise ModelError("validation_failed", "别称长度必须为 1–80 个字符。")
    return alias


def validate_api_model(value: str) -> str:
    model = value.strip()
    if not 1 <= len(model) <= 200:
        raise ModelError("validation_failed", "模型名称长度必须为 1–200 个字符。")
    return model


def validate_catalog_id(value: str) -> str:
    catalog_id = value.strip()
    if not 1 <= len(catalog_id) <= 200:
        raise ModelError("validation_failed", "模型标识长度必须为 1–200 个字符。")
    return catalog_id
