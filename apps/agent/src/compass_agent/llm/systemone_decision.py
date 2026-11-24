"""System One decision adapter.

Aliyun decision services and the TypeSafe/JEV official service share one protocol:
state + questions in, typed answers out. The adapter owns the single outbound HTTP
request, bounded response reading and semantic validation of answers.
"""

from __future__ import annotations

import json
import math
from typing import Any

import httpx

from compass_agent.schemas import (
    DecisionAnswer,
    DecisionInput,
    DecisionQuestion,
    ModelUsage,
    OutputValidation,
)

from .contracts import (
    CallSession,
    DecisionOutput,
    ExecutionRequest,
    ModelOutput,
)
from .errors import RuntimeFailure
from .transport import SingleAttemptTransport

_MAX_RESPONSE_BYTES = 1_048_576


def _field(value: object, name: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def systemone_url(base_url: str) -> str:
    if not base_url:
        raise RuntimeFailure("validation_failed")
    try:
        url = httpx.URL(base_url)
    except Exception as exc:
        raise RuntimeFailure("validation_failed") from exc
    local = url.host in {"127.0.0.1", "localhost"}
    if url.scheme != "https" and not (url.scheme == "http" and local):
        raise RuntimeFailure("validation_failed")
    if url.userinfo or url.query or url.fragment:
        raise RuntimeFailure("validation_failed")
    target = base_url.rstrip("/")
    if not target.endswith("/systemone"):
        target += "/systemone"
    return target


def _status_error(status: int) -> None:
    if 200 <= status < 300:
        return
    if status in {401, 403}:
        raise RuntimeFailure("model_authentication_failed")
    if status == 429:
        raise RuntimeFailure("model_rate_limited")
    if status in {408, 504}:
        raise RuntimeFailure("model_timeout")
    if status >= 500:
        raise RuntimeFailure("model_unavailable_upstream")
    raise RuntimeFailure("provider_protocol_error")


async def _read_body(response: httpx.Response) -> bytes:
    body = bytearray()
    async for chunk in response.aiter_bytes():
        if len(body) + len(chunk) > _MAX_RESPONSE_BYTES:
            raise RuntimeFailure("response_too_large")
        body.extend(chunk)
    return bytes(body)


def _usage(value: object) -> ModelUsage:
    if not isinstance(value, dict):
        return ModelUsage()
    return ModelUsage(
        input_tokens=_token_value(value.get("input_tokens") or value.get("prompt_tokens")),
        output_tokens=_token_value(value.get("output_tokens") or value.get("completion_tokens")),
        reasoning_tokens=_token_value(value.get("reasoning_tokens")),
    )


def _token_value(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value >= 0:
        return value
    return None


def _finite(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _probabilities(raw: object, keys: set[str]) -> dict[str, float]:
    if not isinstance(raw, dict) or set(raw) != keys:
        raise RuntimeFailure("provider_protocol_error")
    values: dict[str, float] = {}
    for key, value in raw.items():
        number = _finite(value)
        if number is None or number < 0:
            raise RuntimeFailure("provider_protocol_error")
        values[str(key)] = number
    if abs(sum(values.values()) - 1.0) > 1e-3:
        raise RuntimeFailure("provider_protocol_error")
    return values


def _confidence(raw: object) -> float | None:
    if raw is None:
        return None
    number = _finite(raw)
    if number is None or not 0 <= number <= 1:
        raise RuntimeFailure("provider_protocol_error")
    return number


def _legend(raw: object, keys: set[str]) -> dict[str, Any] | None:
    if raw is None:
        return None
    if not isinstance(raw, dict) or set(raw) != keys:
        raise RuntimeFailure("provider_protocol_error")
    for value in raw.values():
        if not isinstance(value, (str, dict, list)):
            raise RuntimeFailure("provider_protocol_error")
    return {str(key): value for key, value in raw.items()}


def normalize_answers(
    questions: dict[str, DecisionQuestion],
    raw: object,
) -> dict[str, DecisionAnswer]:
    if not isinstance(raw, dict) or set(raw) != set(questions):
        raise RuntimeFailure("provider_protocol_error")
    answers: dict[str, DecisionAnswer] = {}
    for key, question in questions.items():
        value = raw[key]
        if not isinstance(value, dict):
            raise RuntimeFailure("provider_protocol_error")
        answer_type = value.get("type") or question.type
        if answer_type != question.type:
            raise RuntimeFailure("provider_protocol_error")
        confidence = _confidence(value.get("confidence"))
        if question.type == "choice":
            criteria = question.criteria
            if not isinstance(criteria, dict):
                raise RuntimeFailure("provider_protocol_error")
            keys = set(criteria)
            choice = value.get("choice")
            if not isinstance(choice, str) or choice not in criteria:
                raise RuntimeFailure("provider_protocol_error")
            probabilities = _probabilities(value.get("probabilities"), keys)
            answers[key] = DecisionAnswer(
                type="choice",
                choice=choice,
                probabilities=probabilities,
                confidence=confidence,
                legend=_legend(value.get("legend"), keys),
            )
        elif question.type == "noul":
            noul = _finite(value.get("noul", value.get("probability")))
            if noul is None or not 0 <= noul <= 1:
                raise RuntimeFailure("provider_protocol_error")
            probabilities = None
            if value.get("probabilities") is not None:
                probabilities = _probabilities(value.get("probabilities"), {"true", "false"})
            answers[key] = DecisionAnswer(
                type="noul",
                noul=noul,
                probabilities=probabilities,
                confidence=confidence,
                legend=_legend(value.get("legend"), {"true", "false"}),
            )
        else:
            criteria = question.criteria
            if not isinstance(criteria, list) or len(criteria) < 2:
                raise RuntimeFailure("provider_protocol_error")
            score = _finite(value.get("score"))
            if score is None or not 0 <= score <= len(criteria) - 1:
                raise RuntimeFailure("provider_protocol_error")
            keys = {str(index) for index in range(len(criteria))}
            probabilities = _probabilities(value.get("probabilities"), keys)
            answers[key] = DecisionAnswer(
                type="score",
                score=score,
                probabilities=probabilities,
                confidence=confidence,
                legend=_legend(value.get("legend"), keys),
            )
    return answers


class SystemOneDecisionAdapter:
    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._transport = transport

    async def invoke(self, request: ExecutionRequest, session: CallSession) -> ModelOutput:
        input_value = request.input
        if not isinstance(input_value, DecisionInput):
            raise RuntimeFailure("validation_failed")
        spec = request.access.spec
        endpoint = systemone_url(spec.api_base)
        remaining = max(1.0, min(180.0, request.policy.remaining_seconds))
        resources = session.resources
        transport = SingleAttemptTransport(self._transport)
        client = httpx.AsyncClient(
            timeout=httpx.Timeout(remaining, connect=min(10.0, remaining)),
            follow_redirects=False,
            transport=transport,
        )
        resources.register(client)
        payload = {
            "model": spec.api_model,
            "state": input_value.state,
            "questions": {
                key: question.model_dump(mode="json", exclude_none=True)
                for key, question in input_value.questions.items()
            },
        }
        headers = {"Content-Type": "application/json"}
        api_key = request.access.api_key.get_secret_value()
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        resources.mark_sent()
        try:
            async with client.stream("POST", endpoint, headers=headers, json=payload) as response:
                _status_error(response.status_code)
                body = await _read_body(response)
                request_id = response.headers.get("x-request-id")
        except RuntimeFailure:
            raise
        except httpx.TimeoutException as exc:
            raise RuntimeFailure("model_timeout") from exc
        except httpx.HTTPError as exc:
            raise RuntimeFailure("model_unavailable_upstream") from exc

        try:
            decoded = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise RuntimeFailure("provider_protocol_error") from exc
        if not isinstance(decoded, dict):
            raise RuntimeFailure("provider_protocol_error")
        raw_answers = decoded.get("answers")
        answers = normalize_answers(input_value.questions, raw_answers)
        progress = session.progress
        progress.usage = _usage(decoded.get("usage"))
        progress.response_model = decoded.get("model")
        progress.upstream_request_id = decoded.get("id") or request_id
        progress.finish_reason = "stop"
        progress.normal_eof = True
        progress.validation = OutputValidation(status="valid", mode="decision")
        return DecisionOutput(answers=answers)
