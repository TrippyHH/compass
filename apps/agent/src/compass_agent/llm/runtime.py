"""The single lifecycle owner for one model call."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping
from uuid import UUID

from .capabilities import validate_input
from .contracts import (
    AdapterKind,
    CallSession,
    DeltaSink,
    ExecutionRequest,
    GenerationOutput,
    ModelOutput,
    ProviderAdapter,
    ProviderProgress,
    RuntimeCancelled,
    RuntimeResult,
    empty_output_for,
)
from .errors import RuntimeErrorInfo, RuntimeFailure
from .transport import CallResources

_KNOWN_ADAPTERS: frozenset[str] = frozenset({"litellm_chat", "systemone_decision"})
_UNKNOWN_OUTCOME_CODES: frozenset[str] = frozenset(
    {
        "model_timeout",
        "model_unavailable_upstream",
        "stream_interrupted",
        "internal_error",
    }
)


class Runtime:
    """Coordinates capability checks, the deadline, cancellation and cleanup."""

    def __init__(self, adapters: Mapping[AdapterKind, ProviderAdapter]) -> None:
        self._adapters = dict(adapters)
        unknown = set(self._adapters) - _KNOWN_ADAPTERS
        if unknown:
            raise ValueError("unknown adapter keys")

    async def invoke(
        self,
        request: ExecutionRequest,
        emit: DeltaSink | None = None,
    ) -> RuntimeResult:
        started = time.monotonic()
        resources = CallResources()
        progress = ProviderProgress(output=empty_output_for(request.input))
        session = CallSession(resources=resources, progress=progress, emit=emit)
        error: RuntimeErrorInfo | None = None
        canceled = False
        result_status = "completed"

        try:
            self._check_binding(request)
            validate_input(
                request.access.spec,
                request.input,
                request.context.capability_mode,
            )
            remaining = request.policy.deadline_monotonic - time.monotonic()
            if remaining <= 0:
                raise RuntimeFailure("model_timeout")
            adapter = self._adapters.get(request.access.spec.adapter)
            if adapter is None:
                raise RuntimeFailure("internal_error")
            async with asyncio.timeout(remaining):
                output = await adapter.invoke(request, session)
            self._finalize(request, session, output)
            progress.output = output
            progress.normal_eof = True
        except asyncio.CancelledError:
            canceled = True
        except TimeoutError:
            error = RuntimeErrorInfo("model_timeout")
        except RuntimeFailure as failure:
            error = failure.error
        except Exception:
            error = RuntimeErrorInfo("internal_error")
        finally:
            await self._cleanup(resources, request.policy.cleanup_timeout_s)

        if canceled:
            result_status = "canceled"
        elif error is not None:
            interrupted = error.code in {"stream_interrupted", "output_truncated"}
            result_status = "incomplete" if (resources.has_partial or interrupted) else "failed"

        usage = progress.usage.model_copy(deep=True)
        validation = progress.validation.model_copy(deep=True)
        output_copy = self._copy_output(progress.output)
        result = RuntimeResult(
            call_id=request.context.call_id,
            provider_id=request.access.spec.provider_id,
            config_version=request.access.spec.config_version,
            binding_version=request.access.spec.binding_version,
            requested_model=request.access.spec.api_model,
            status=result_status,  # type: ignore[arg-type]
            duration_ms=max(0, int((time.monotonic() - started) * 1000)),
            output=output_copy,
            usage=usage,
            validation=validation,
            response_model=progress.response_model or resources.wire_response_model,
            upstream_request_id=progress.upstream_request_id or resources.wire_request_id,
            finish_reason=progress.finish_reason or resources.wire_finish_reason,
            error=error,
            upstream_outcome_unknown=bool(
                resources.sent
                and (canceled or (error is not None and error.code in _UNKNOWN_OUTCOME_CODES))
            ),
            upstream_sent=resources.sent,
        )
        if canceled:
            raise RuntimeCancelled(result)
        return result

    @staticmethod
    def _check_binding(request: ExecutionRequest) -> None:
        spec = request.access.spec
        binding = request.context.continuation_binding
        if not isinstance(request.context.call_id, UUID):
            raise RuntimeFailure("validation_failed")
        if binding.provider != spec.provider_id:
            raise RuntimeFailure("continuation_invalid")
        if binding.binding_version != spec.binding_version:
            raise RuntimeFailure("continuation_invalid")

    @staticmethod
    def _finalize(
        request: ExecutionRequest,
        session: CallSession,
        output: ModelOutput,
    ) -> None:
        progress = session.progress
        if output.kind != request.input.kind:
            raise RuntimeFailure("provider_protocol_error")
        if output.kind == "generate":
            if not progress.normal_eof or progress.finish_reason is None:
                raise RuntimeFailure("stream_interrupted")
            finish = progress.finish_reason
            if finish == "length":
                raise RuntimeFailure("output_truncated")
            if finish == "content_filter":
                raise RuntimeFailure("model_refused")
            if finish not in {"stop", "tool_calls"}:
                raise RuntimeFailure("provider_protocol_error")
            generation = output if isinstance(output, GenerationOutput) else GenerationOutput()
            if not generation.text.strip() and not generation.tool_calls:
                raise RuntimeFailure("empty_output")
            if request.input.kind == "generate" and request.input.output_format != "text":
                if progress.validation.status != "valid":
                    raise RuntimeFailure("schema_validation_failed")
            return
        decision = output
        answers = getattr(decision, "answers", {})
        if not answers:
            raise RuntimeFailure("empty_output")

    @staticmethod
    def _copy_output(output: ModelOutput) -> ModelOutput:
        if isinstance(output, GenerationOutput):
            return GenerationOutput(
                text=output.text,
                tool_calls=tuple(call.model_copy(deep=True) for call in output.tool_calls),
                continuation_token=output.continuation_token,
            )
        return type(output)(
            answers={key: answer.model_copy(deep=True) for key, answer in output.answers.items()}
        )

    @staticmethod
    async def _cleanup(resources: CallResources, timeout_s: float) -> None:
        cleanup = asyncio.create_task(resources.close())
        deadline = time.monotonic() + timeout_s
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                cleanup.cancel()
                return
            try:
                await asyncio.wait_for(asyncio.shield(cleanup), remaining)
                return
            except TimeoutError:
                cleanup.cancel()
                return
            except asyncio.CancelledError:
                # Keep the parent cancellation pending, but finish cleanup within budget.
                continue
