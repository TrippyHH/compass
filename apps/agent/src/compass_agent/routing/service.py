"""Thin model-routing flow; no Harness, task dispatch, retries or recovery engine."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid5

from starlette.concurrency import run_in_threadpool

from compass_agent.llm import capabilities as capability_rules
from compass_agent.llm.errors import RuntimeFailure
from compass_agent.models import RoutingGrant
from compass_agent.models.errors import ModelError
from compass_agent.models.repository import ProviderState
from compass_agent.routing.config import POLICY
from compass_agent.routing.dictionary import catalog, features_for
from compass_agent.routing.intent import decision_input, flash_input, from_flash, from_jev
from compass_agent.routing.prompts import MODE_PROMPTS, OFF_TOPIC_PROMPT
from compass_agent.schemas import (
    CallOutcome,
    ChatMessage,
    ClassificationEvent,
    Intent,
    ModelRoute,
    RoutedCallResponse,
    RouteSelectedEvent,
    RoutingFeatures,
)
from compass_agent.settings import get_settings

FALLBACK_CODES = {
    "route_binding_missing",
    "capability_not_supported",
    "response_too_large",
    "model_unavailable",
    "model_timeout",
    "validation_failed",
}

CAPABILITY_LABELS = {
    "sync": "同步调用",
    "stream": "流式输出",
    "tools": "工具调用",
    "thinking": "Thinking",
    "json_output": "JSON 输出",
    "schema_output": "结构化输出",
}


class RoutingService:
    def __init__(self, models, settings=None) -> None:
        self.models = models
        self.settings = settings or get_settings()

    async def provider(self, role: str) -> ProviderState:
        configured = getattr(self.settings, f"compass_routing_{role}_provider_id")
        return await run_in_threadpool(self.models.repository.routing_provider, role, configured)

    async def run(
        self,
        command,
        context,
        emit: Callable[[Any], Awaitable[None]] | None = None,
    ) -> RoutedCallResponse:
        loop = asyncio.get_running_loop()
        started = loop.time()
        parent_deadline_at = datetime.now(UTC) + timedelta(seconds=POLICY.parent_seconds)
        response = RoutedCallResponse(call_id=command.call_id, status="failed")
        metrics = response.metrics

        async def stage(
            role: str,
            state: ProviderState,
            value: Any,
            stage_deadline_at: datetime,
            kind: str,
        ):
            physical_id = command.call_id if role == "inference" else uuid5(command.call_id, role)
            grant = (
                RoutingGrant(
                    actor_user_id=context.actor_user_id,
                    scope=context.scope,
                    call_id=physical_id,
                    provider_id=state.id,
                    stage=role,
                )
                if kind == "classifier"
                else None
            )
            prepared = await self.models.prepare(
                physical_id,
                state.id,
                value,
                context,
                deadline_at=min(parent_deadline_at, stage_deadline_at),
                grant=grant,
            )
            result = await self.models.run(prepared, emit=emit if role == "inference" else None)
            if kind == "classifier":
                metrics.classification_calls.append(
                    CallOutcome.model_validate(
                        result.model_dump(include=set(CallOutcome.model_fields))
                    )
                )
            if result.error_code == "record_write_failed":
                raise ModelError("record_write_failed", "调用结果可能已产生，请按调用 ID 核对。")
            return result

        classification_cutoff = min(
            parent_deadline_at - timedelta(seconds=1),
            datetime.now(UTC) + timedelta(seconds=POLICY.classification_seconds),
        )

        def stage_deadline(seconds: float) -> datetime:
            return min(classification_cutoff, datetime.now(UTC) + timedelta(seconds=seconds))

        try:
            async with asyncio.timeout_at(started + POLICY.parent_seconds):
                intent: Intent | None = None
                if self.settings.compass_routing_mode == "jev_with_flash":
                    metrics.fallback_reason = "jev_unavailable"
                    try:
                        jev_state = await self.provider("jev")
                        if emit:
                            await emit(ClassificationEvent(call_id=command.call_id, stage="jev"))
                        decision = await stage(
                            "intent_jev",
                            jev_state,
                            decision_input(command.input),
                            stage_deadline(POLICY.jev_seconds),
                            "classifier",
                        )
                        intent = from_jev(decision)
                        if intent is None:
                            metrics.fallback_reason = "jev_unavailable_or_uncertain"
                    except ModelError as exc:
                        if exc.code not in FALLBACK_CODES:
                            raise
                elif self.settings.compass_routing_mode != "flash_only":
                    raise ModelError("route_binding_missing", "路由分类模式不可用。")

                if intent is None:
                    flash_state = await self.provider("flash")
                    if emit:
                        await emit(ClassificationEvent(call_id=command.call_id, stage="flash"))
                    prediction = await stage(
                        "intent_flash",
                        flash_state,
                        flash_input(command.input),
                        stage_deadline(POLICY.flash_seconds),
                        "classifier",
                    )
                    intent = from_flash(prediction)
                    metrics.classifier_source = "flash"
                else:
                    metrics.classifier_source = "jev"
                    metrics.fallback_reason = None

                response.intent = intent
                features = features_for(intent)
                response.features = features
                selection = command.selection()
                off_topic = intent.scene_id == "SC-39"
                state: ProviderState
                if off_topic:
                    state = await self.provider("flash")
                    reason = "off_topic_flash"
                    if not state.chat_visible:
                        raise ModelError("route_binding_missing", "范围引导模型未启用 Chat。")
                elif selection.mode == "fixed":
                    if selection.provider_id is None:
                        raise ModelError("model_unavailable", "当前没有满足任务要求的可用模型。")
                    try:
                        state = await run_in_threadpool(
                            self.models.repository.provider, selection.provider_id
                        )
                    except ModelError as exc:
                        if exc.code != "not_found":
                            raise
                        raise ModelError(
                            "model_unavailable", "当前没有满足任务要求的可用模型。"
                        ) from exc
                    reason = "user_fixed_main_model"
                    if not state.enabled or not state.chat_visible:
                        raise ModelError("model_unavailable", "当前没有满足任务要求的可用模型。")
                else:
                    role = "pro" if features.difficulty in {"L2", "L3"} else "flash"
                    state = await self.provider(role)
                    reason = "difficulty_" + features.difficulty
                    if not state.chat_visible:
                        raise ModelError("route_binding_missing", "自动选模模型未启用 Chat。")

                self._require_capabilities(state, features)
                prompt = OFF_TOPIC_PROMPT if off_topic else MODE_PROMPTS[features.response_mode]
                updates: dict = {
                    "messages": [
                        ChatMessage(role="system", content=prompt),
                        *[
                            message
                            for message in command.input.messages
                            if message.role != "system"
                        ],
                    ]
                }
                if off_topic:
                    updates.update(
                        thinking=False,
                        reasoning_effort=None,
                        tools=[],
                        output_format="text",
                        output_schema=None,
                        max_tokens=512,
                        continuation_token=None,
                    )
                generated = command.input.model_copy(update=updates)
                self._preflight(state, generated)
                response.routing = ModelRoute(
                    mode=selection.mode,
                    provider_id=state.id,
                    model=state.api_model,
                    reason=reason,
                    dictionary_version=catalog()[0],
                    routing_version=POLICY.version,
                )
                if emit:
                    await emit(
                        RouteSelectedEvent(
                            call_id=command.call_id,
                            intent=intent,
                            features=features,
                            routing=response.routing,
                        )
                    )
                result = await stage("inference", state, generated, parent_deadline_at, "inference")
                response.result = result
                response.status = result.status
                response.error_code = result.error_code
        except ModelError as exc:
            if exc.code == "call_already_exists":
                raise
            response.error_code = exc.code
        except TimeoutError:
            response.error_code = "model_timeout"
        finally:
            metrics.duration_ms = max(0, int((loop.time() - started) * 1000))
            usages = [item.usage for item in metrics.classification_calls]
            if response.result is not None:
                usages.append(response.result.usage)
            metrics.usage_complete = bool(usages) and all(
                usage.input_tokens is not None and usage.output_tokens is not None
                for usage in usages
            )
        return response

    def _require_capabilities(self, state: ProviderState, features: RoutingFeatures) -> None:
        capabilities = capability_rules.resolve(self.models.spec_for(state))
        for name in features.required_capabilities:
            capability = getattr(capabilities, name, None)
            if capability is None or capability.state != "supported":
                label = CAPABILITY_LABELS.get(name, name)
                raise ModelError(
                    "capability_not_supported",
                    f"当前模型不支持本任务要求的“{label}”，请选择其他模型。",
                )

    def _preflight(self, state: ProviderState, value) -> None:
        try:
            capability_rules.validate_input(self.models.spec_for(state), value, "verified")
        except RuntimeFailure as exc:
            code = (
                "capability_not_supported"
                if exc.error.code == "capability_not_supported"
                else "validation_failed"
            )
            raise ModelError(code, "当前模型无法接受该输入或能力组合。") from exc
