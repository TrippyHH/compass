"""Agent model HTTP API: service identity, trusted context, DTOs and SSE."""

from __future__ import annotations

import asyncio
import base64
import hmac
import json
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse

from compass_agent.models.errors import ModelError
from compass_agent.models.service import ModelService, PreparedExecution
from compass_agent.schemas import (
    AvailableModels,
    CallRecordView,
    CallResponse,
    CatalogPage,
    ChatCallInput,
    FinalEvent,
    GenerateInput,
    ModelChangePage,
    ProviderCreate,
    ProviderPage,
    ProviderPatch,
    ProviderView,
    SavedProvider,
    StartEvent,
    TestCallInput,
    TrustedContext,
)
from compass_agent.settings import get_settings

MAX_REQUEST_BYTES = 2 * 1024 * 1024

router = APIRouter(prefix="/v1/models", tags=["models"])

_STATUS = {
    "validation_failed": 422,
    "chat_model_not_supported": 422,
    "forbidden": 403,
    "not_found": 404,
    "conflict": 409,
    "call_already_exists": 409,
    "model_unavailable": 503,
    "unavailable": 503,
    "unauthenticated": 401,
}

_SAFE_MESSAGES = {
    "forbidden": "仅平台管理员可管理模型接入。",
    "not_found": "未找到可访问的模型配置。",
    "conflict": "配置已被更新，请重新加载并核对修改。",
    "call_already_exists": "该调用已存在，请按调用 ID 核对结果。",
    "chat_model_not_supported": "决策模型本期仅支持管理端测试。",
    "model_unavailable": "当前模型已不可用，请重新选择。",
    "unavailable": "模型服务暂时不可用。",
    "unauthenticated": "服务身份校验失败。",
    "validation_failed": "请检查模型配置或调用参数。",
}


class ModelAPIError(Exception):
    def __init__(
        self,
        code: str,
        *,
        message: str | None = None,
        current_version: int | None = None,
    ) -> None:
        self.code = code
        self.status_code = _STATUS.get(code, 503)
        self.message = message or _SAFE_MESSAGES.get(code, "请求未能完成。")
        self.current_version = current_version
        super().__init__(self.message)


def error_response(error: ModelError) -> JSONResponse:
    api = ModelAPIError(
        error.code,
        message=error.message if error.code in {"validation_failed"} else None,
        current_version=error.current_version,
    )
    return api_error_response(api)


def api_error_response(error: ModelAPIError) -> JSONResponse:
    payload: dict[str, Any] = {
        "error": {
            "code": error.code,
            "message": error.message,
            "requestId": str(uuid4()),
        }
    }
    if error.current_version is not None:
        payload["error"]["currentVersion"] = error.current_version
    return JSONResponse(payload, status_code=error.status_code, headers=_headers())


def _headers() -> dict[str, str]:
    return {"Cache-Control": "private, no-store", "Vary": "Cookie"}


def _require_service(request: Request) -> None:
    expected = get_settings().compass_agent_service_token
    if not expected:
        raise ModelAPIError("unavailable", message="模型服务未配置服务身份。")
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if not scheme or scheme.lower() != "bearer" or not token:
        raise ModelAPIError("unauthenticated")
    if not hmac.compare_digest(token.encode("utf-8"), expected.encode("utf-8")):
        raise ModelAPIError("unauthenticated")


def _decode_context(raw: str) -> TrustedContext:
    try:
        padded = raw + "=" * (-len(raw) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
        return TrustedContext.model_validate(payload)
    except Exception as exc:
        raise ModelAPIError("unauthenticated") from exc


def _context(request: Request) -> TrustedContext:
    _require_service(request)
    raw = request.headers.get("x-compass-context")
    if not raw:
        raise ModelAPIError("unauthenticated")
    return _decode_context(raw)


def _platform(request: Request, purposes: set[str]) -> TrustedContext:
    context = _context(request)
    if context.scope.kind != "platform" or context.purpose not in purposes:
        raise ModelAPIError("forbidden")
    return context


def platform_manage(request: Request) -> TrustedContext:
    return _platform(request, {"manage", "test"})


def platform_test(request: Request) -> TrustedContext:
    return _platform(request, {"test"})


def tenant_read(request: Request) -> TrustedContext:
    context = _context(request)
    if context.scope.kind != "tenant" or context.purpose not in {"module_read", "chat"}:
        raise ModelAPIError("forbidden")
    return context


def tenant_chat(request: Request) -> TrustedContext:
    context = _context(request)
    if context.scope.kind != "tenant" or context.purpose != "chat":
        raise ModelAPIError("forbidden")
    return context


def _check_body(request: Request) -> None:
    length = request.headers.get("content-length")
    if length and length.isdigit() and int(length) > MAX_REQUEST_BYTES:
        raise ModelAPIError("validation_failed", message="请求内容过大。")


def _service(request: Request) -> ModelService:
    service = getattr(request.app.state, "model_service", None)
    if service is None:
        raise ModelAPIError("unavailable")
    return service


def _sse(event: Any) -> str:
    payload = event.model_dump(mode="json", exclude_none=True)
    return "data: " + json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n\n"


async def _wait_disconnect(request: Request) -> None:
    while not await request.is_disconnected():
        await asyncio.sleep(0.2)


def _final(response: CallResponse) -> FinalEvent:
    return FinalEvent(
        call_id=response.call_id,
        provider_id=response.provider_id,
        config_version=response.config_version,
        status=response.status,
        duration_ms=response.duration_ms,
        usage=response.usage,
        error_code=response.error_code,
        upstream_outcome_unknown=response.upstream_outcome_unknown,
        requested_model=response.requested_model,
        response_model=response.response_model,
        upstream_request_id=response.upstream_request_id,
        finish_reason=response.finish_reason,
        validation=response.validation,
        continuation_token=response.continuation_token,
    )


def _failed_final(prepared: PreparedExecution, code: str) -> FinalEvent:
    spec = prepared.prepared.spec
    return FinalEvent(
        call_id=prepared.request.context.call_id,
        provider_id=spec.provider_id,
        config_version=spec.config_version,
        status="failed",
        duration_ms=0,
        error_code=code,
        upstream_outcome_unknown=True,
        requested_model=spec.api_model,
    )


async def _stream(
    prepared: PreparedExecution, service: ModelService, request: Request
) -> AsyncIterator[str]:
    queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=16)
    spec = prepared.prepared.spec

    async def emit(delta: Any) -> None:
        await queue.put(delta)

    async def produce() -> None:
        try:
            response = await service.execute(
                prepared, emit=emit, disconnect=lambda: _wait_disconnect(request)
            )
            await queue.put(_final(response))
        except asyncio.CancelledError:
            raise
        except Exception:
            await queue.put(_failed_final(prepared, "internal_error"))
        finally:
            await queue.put(None)

    task = asyncio.create_task(produce())
    try:
        yield _sse(
            StartEvent(
                call_id=prepared.request.context.call_id,
                provider_id=spec.provider_id,
                config_version=spec.config_version,
            )
        )
        while True:
            item = await queue.get()
            if item is None:
                break
            yield _sse(item)
            if getattr(item, "type", "") == "final":
                break
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def _streaming_response(
    prepared: PreparedExecution, service: ModelService, request: Request
) -> StreamingResponse:
    return StreamingResponse(
        _stream(prepared, service, request),
        media_type="text/event-stream",
        headers={
            **_headers(),
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.get("/catalog", response_model=CatalogPage)
async def catalog(
    request: Request,
    context: TrustedContext = Depends(platform_manage),
    q: str = Query(default="", max_length=200),
    vendor: str | None = Query(default=None, max_length=100),
    page: int = Query(default=1, ge=1, le=10_000),
    page_size: int = Query(default=20, ge=1, le=100),
) -> CatalogPage:
    del context
    return await _service(request).catalog(q=q, vendor=vendor, page=page, page_size=page_size)


@router.get("/providers", response_model=ProviderPage)
async def providers(
    request: Request,
    context: TrustedContext = Depends(platform_manage),
    q: str = Query(default="", max_length=200),
    page: int = Query(default=1, ge=1, le=10_000),
    page_size: int = Query(default=20, ge=1, le=100),
) -> ProviderPage:
    return await _service(request).providers(context, q=q, page=page, page_size=page_size)


@router.post("/providers", response_model=SavedProvider, status_code=201)
async def create_provider(
    request: Request,
    payload: ProviderCreate,
    context: TrustedContext = Depends(platform_manage),
) -> SavedProvider:
    _check_body(request)
    return await _service(request).create_provider(payload, context)


@router.get("/providers/{provider_id}", response_model=ProviderView)
async def provider(
    request: Request,
    provider_id: UUID,
    context: TrustedContext = Depends(platform_manage),
) -> ProviderView:
    return await _service(request).provider(provider_id, context)


@router.patch("/providers/{provider_id}", response_model=SavedProvider)
async def patch_provider(
    request: Request,
    provider_id: UUID,
    payload: ProviderPatch,
    context: TrustedContext = Depends(platform_manage),
) -> SavedProvider:
    _check_body(request)
    return await _service(request).patch_provider(provider_id, payload, context)


@router.get("/operations/{operation_id}", response_model=SavedProvider)
async def operation(
    request: Request,
    operation_id: UUID,
    context: TrustedContext = Depends(platform_manage),
) -> SavedProvider:
    return await _service(request).operation(operation_id, context)


@router.get("/providers/{provider_id}/changes", response_model=ModelChangePage)
async def changes(
    request: Request,
    provider_id: UUID,
    context: TrustedContext = Depends(platform_manage),
    page: int = Query(default=1, ge=1, le=10_000),
    page_size: int = Query(default=20, ge=1, le=100),
) -> ModelChangePage:
    return await _service(request).changes(provider_id, context, page=page, page_size=page_size)


@router.get("/available", response_model=AvailableModels)
async def available(
    request: Request,
    context: TrustedContext = Depends(tenant_read),
) -> AvailableModels:
    return await _service(request).available(context)


@router.post("/providers/{provider_id}/tests", response_model=CallResponse)
async def test_provider(
    request: Request,
    provider_id: UUID,
    payload: TestCallInput,
    context: TrustedContext = Depends(platform_test),
) -> CallResponse | StreamingResponse:
    _check_body(request)
    service = _service(request)
    prepared = await service.prepare_test(provider_id, payload.call_id, payload.input, context)
    if isinstance(payload.input, GenerateInput) and payload.input.stream:
        return _streaming_response(prepared, service, request)
    return await service.execute(prepared, disconnect=lambda: _wait_disconnect(request))


@router.post("/calls", response_model=CallResponse)
async def chat_call(
    request: Request,
    payload: ChatCallInput,
    context: TrustedContext = Depends(tenant_chat),
) -> CallResponse | StreamingResponse:
    _check_body(request)
    service = _service(request)
    prepared = await service.prepare_chat(payload, context)
    if payload.input.stream:
        return _streaming_response(prepared, service, request)
    return await service.execute(prepared, disconnect=lambda: _wait_disconnect(request))


@router.get("/calls/{call_id}", response_model=CallRecordView)
async def call_record(
    request: Request,
    call_id: UUID,
    context: TrustedContext = Depends(tenant_chat),
) -> CallRecordView:
    return await _service(request).call_record(call_id, context)


@router.get("/test-calls/{call_id}", response_model=CallRecordView)
async def test_record(
    request: Request,
    call_id: UUID,
    context: TrustedContext = Depends(platform_test),
) -> CallRecordView:
    return await _service(request).test_record(call_id, context)
