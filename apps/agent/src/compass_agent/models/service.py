"""Application service: admission, snapshots, placeholders, execution and records."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from pydantic import SecretStr
from sqlalchemy.engine import Engine

from compass_agent.llm import capabilities as capability_rules
from compass_agent.llm import catalog
from compass_agent.llm.contracts import (
    ContinuationBinding,
    ExecutionRequest,
    GenerationOutput,
    InvocationContext,
    ModelSpec,
    ProviderAccess,
    RuntimeCancelled,
    RuntimeFailure,
    RuntimeResult,
)
from compass_agent.llm.runtime import Runtime
from compass_agent.schemas import (
    AvailableModel,
    AvailableModels,
    CallRecordView,
    CallResponse,
    CatalogPage,
    ChatCallInput,
    DecisionInput,
    GenerateInput,
    ModelChangePage,
    ModelChangeView,
    ModelUsage,
    OutputValidation,
    ProviderCreate,
    ProviderPage,
    ProviderPatch,
    ProviderView,
    SavedProvider,
    TestCallInput,
    TestSummary,
    TrustedContext,
)

from . import PreparedCall, RoutingGrant, secrets, validation
from .errors import ModelError
from .repository import (
    BeginCallCommand,
    CallRow,
    ModelRepository,
    ProviderBinding,
    ProviderState,
    SaveProviderCommand,
    as_utc,
    deadline_from,
    request_digest,
)

TERMINAL_STATUSES = {"completed", "failed", "canceled", "incomplete"}


@dataclass(slots=True)
class PreparedExecution:
    prepared: PreparedCall
    request: ExecutionRequest
    value: GenerateInput | DecisionInput
    purpose: str
    started_monotonic: float


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _route(vendor: str, api_model: str) -> str:
    if api_model.startswith(f"{vendor}/"):
        return api_model
    return f"{vendor}/{api_model}"


def _capability_summary(value: Any, mode: str) -> dict:
    summary: dict[str, Any] = {
        "mode": mode,
        "kind": value.kind,
        "stream": bool(getattr(value, "stream", False)),
        "thinking": bool(getattr(value, "thinking", False)),
        "tools": len(getattr(value, "tools", []) or []),
        "output_format": getattr(value, "output_format", "decision"),
    }
    exercised = ["sync"]
    if summary["stream"]:
        exercised.append("stream")
    if summary["tools"]:
        exercised.append("tools")
    if summary["thinking"]:
        exercised.append("thinking")
    if summary["output_format"] == "json":
        exercised.append("json_output")
    if summary["output_format"] == "schema":
        exercised.append("schema_output")
    if summary["kind"] == "decision":
        exercised = ["sync", "decision"]
    summary["capabilities"] = exercised
    return summary


class ModelService:
    def __init__(
        self,
        engine: Engine,
        runtime: Runtime,
        *,
        repository: ModelRepository | None = None,
    ) -> None:
        self._repository = repository or ModelRepository(engine)
        self._runtime = runtime

    # -- catalog and configuration ----------------------------------------

    async def catalog(
        self, *, q: str = "", vendor: str | None = None, page: int = 1, page_size: int = 20
    ) -> CatalogPage:
        return catalog.page(q=q, vendor=vendor, page_number=page, page_size=page_size)

    async def create_provider(
        self, command: ProviderCreate, context: TrustedContext
    ) -> SavedProvider:
        self._require_platform(context, {"manage", "test"})
        binding = self._binding(command.catalog_id, command.api_model)
        base_url = validation.validate_base_url(
            command.base_url, required=binding.kind == "decision"
        )
        params = validation.validate_connection_params(command.connection_params)
        alias = validation.validate_alias(command.alias)
        if command.chat_visible and not capability_rules.chat_supported(
            binding.kind, binding.adapter
        ):
            raise ModelError("chat_model_not_supported", "决策模型仅支持管理端测试。")
        mac = request_digest(
            {
                "op": str(command.operation_id),
                "create": binding.catalog_id,
                "alias": alias,
                "api_model": binding.api_model,
                "base_url": base_url,
                "api_key": _key_digest(command.api_key),
                "connection_params": params if params is not None else None,
                "enabled": command.enabled,
                "chat_visible": command.chat_visible,
            }
        )
        saved = await asyncio.to_thread(
            self._repository.save_provider,
            SaveProviderCommand(
                operation_id=command.operation_id,
                provider_id=None,
                expected_version=None,
                actor_user_id=context.actor_user_id,
                request_mac=mac,
                binding=ProviderBinding(
                    catalog_id=binding.catalog_id,
                    vendor=binding.vendor,
                    api_model=binding.api_model,
                    sdk_model=binding.sdk_model,
                    alias=alias,
                    kind=binding.kind,
                    adapter=binding.adapter,
                ),
                base_url=base_url,
                api_key=command.api_key,
                connection_params=params if params is not None else {},
                enabled=command.enabled,
                chat_visible=command.chat_visible,
            ),
        )
        return SavedProvider(
            provider_id=saved.provider_id,
            version=saved.version,
            operation_id=saved.operation_id,
        )

    async def patch_provider(
        self, provider_id: UUID, command: ProviderPatch, context: TrustedContext
    ) -> SavedProvider:
        self._require_platform(context, {"manage", "test"})
        current = await asyncio.to_thread(self._repository.provider, provider_id)
        catalog_id = (
            validation.validate_catalog_id(command.catalog_id)
            if command.catalog_id is not None
            else current.catalog_id
        )
        api_model = (
            validation.validate_api_model(command.api_model)
            if command.api_model is not None
            else current.api_model
        )
        binding = self._binding(catalog_id, api_model)
        alias = (
            validation.validate_alias(command.alias) if command.alias is not None else current.alias
        )
        base_url = validation.validate_base_url(
            command.base_url if command.base_url is not None else current.base_url,
            required=binding.kind == "decision",
        )
        params = (
            validation.validate_connection_params(command.connection_params)
            if command.connection_params is not None
            else None
        )
        enabled = command.enabled if command.enabled is not None else current.enabled
        chat_visible = (
            command.chat_visible if command.chat_visible is not None else current.chat_visible
        )
        if chat_visible and not capability_rules.chat_supported(binding.kind, binding.adapter):
            raise ModelError("chat_model_not_supported", "决策模型仅支持管理端测试。")
        mac = request_digest(
            {
                "op": str(command.operation_id),
                "patch": str(provider_id),
                "expected_version": command.expected_version,
                "catalog_id": binding.catalog_id,
                "alias": alias,
                "api_model": binding.api_model,
                "base_url": base_url,
                "api_key": _key_digest(command.api_key),
                "connection_params": params if params is not None else "keep",
                "enabled": enabled,
                "chat_visible": chat_visible,
            }
        )
        saved = await asyncio.to_thread(
            self._repository.save_provider,
            SaveProviderCommand(
                operation_id=command.operation_id,
                provider_id=provider_id,
                expected_version=command.expected_version,
                actor_user_id=context.actor_user_id,
                request_mac=mac,
                binding=ProviderBinding(
                    catalog_id=binding.catalog_id,
                    vendor=binding.vendor,
                    api_model=binding.api_model,
                    sdk_model=binding.sdk_model,
                    alias=alias,
                    kind=binding.kind,
                    adapter=binding.adapter,
                ),
                base_url=base_url,
                api_key=command.api_key,
                connection_params=params,
                enabled=enabled,
                chat_visible=chat_visible,
            ),
        )
        return SavedProvider(
            provider_id=saved.provider_id,
            version=saved.version,
            operation_id=saved.operation_id,
        )

    async def operation(self, operation_id: UUID, context: TrustedContext) -> SavedProvider:
        self._require_platform(context, {"manage", "test"})
        change = await asyncio.to_thread(self._repository.get_operation, operation_id)
        if change is None or change.actor_user_id != context.actor_user_id:
            raise ModelError("not_found")
        return SavedProvider(
            provider_id=change.provider_id,
            version=change.after_version,
            operation_id=change.operation_id,
        )

    async def provider(self, provider_id: UUID, context: TrustedContext) -> ProviderView:
        self._require_platform(context, {"manage", "test"})
        state = await asyncio.to_thread(self._repository.provider, provider_id)
        latest = await asyncio.to_thread(self._repository.latest_test, provider_id)
        return self._view(state, latest)

    async def providers(
        self,
        context: TrustedContext,
        *,
        q: str = "",
        page: int = 1,
        page_size: int = 20,
    ) -> ProviderPage:
        self._require_platform(context, {"manage", "test"})
        rows, total = await asyncio.to_thread(
            self._repository.provider_page, q=q, page=page, page_size=page_size
        )
        items: list[ProviderView] = []
        for state in rows:
            latest = await asyncio.to_thread(self._repository.latest_test, state.id)
            items.append(self._view(state, latest))
        return ProviderPage(items=items, total=total, page=page, page_size=page_size)

    async def changes(
        self, provider_id: UUID, context: TrustedContext, *, page: int = 1, page_size: int = 20
    ) -> ModelChangePage:
        self._require_platform(context, {"manage", "test"})
        rows, total = await asyncio.to_thread(
            self._repository.changes, provider_id, page=page, page_size=page_size
        )
        return ModelChangePage(
            items=[
                ModelChangeView(
                    operation_id=row.operation_id,
                    provider_id=row.provider_id,
                    actor_user_id=row.actor_user_id,
                    before_version=row.before_version,
                    after_version=row.after_version,
                    changed_fields=row.changed_fields,
                    created_at=row.created_at,
                )
                for row in rows
            ],
            total=total,
            page=page,
            page_size=page_size,
        )

    async def available(self, context: TrustedContext) -> AvailableModels:
        self._require_tenant(context, {"chat", "module_read"})
        rows = await asyncio.to_thread(self._repository.available)
        items: list[AvailableModel] = []
        for state in rows:
            spec = self._spec(state)
            items.append(
                AvailableModel(
                    id=state.id,
                    alias=state.alias,
                    api_model=state.api_model,
                    kind="llm",
                    capabilities=capability_rules.resolve(spec),
                )
            )
        return AvailableModels(items=items)

    # -- execution ---------------------------------------------------------

    async def test_call(
        self,
        provider_id: UUID,
        command: TestCallInput,
        context: TrustedContext,
        *,
        emit: Callable[[Any], Awaitable[None]] | None = None,
        disconnect: Callable[[], Awaitable[None]] | None = None,
    ) -> CallResponse:
        prepared = await self.prepare_test(provider_id, command.call_id, command.input, context)
        return await self.execute(prepared, emit=emit, disconnect=disconnect)

    async def chat_call(
        self,
        command: ChatCallInput,
        context: TrustedContext,
        *,
        emit: Callable[[Any], Awaitable[None]] | None = None,
        disconnect: Callable[[], Awaitable[None]] | None = None,
    ) -> CallResponse:
        prepared = await self.prepare_chat(command, context)
        return await self.execute(prepared, emit=emit, disconnect=disconnect)

    @property
    def repository(self) -> ModelRepository:
        return self._repository

    async def prepare(
        self,
        call_id: UUID,
        provider_id: UUID,
        value: GenerateInput | DecisionInput,
        context: TrustedContext,
        *,
        deadline_at: datetime | None = None,
        grant: RoutingGrant | None = None,
    ) -> PreparedExecution:
        """Prepare one leaf call for the routing service.

        deadline_at keeps the parent round budget; a classification stage passes
        the smaller of the parent and stage deadlines. The grant authorizes only the
        in-process classification provider and never widens browser input.
        """

        self._require_tenant(context, {"chat"})
        return await self._prepare(
            provider_id,
            call_id,
            value,
            context,
            purpose="chat",
            deadline_at=deadline_at,
            grant=grant,
        )

    async def run(
        self,
        prepared: PreparedExecution,
        *,
        emit: Callable[[Any], Awaitable[None]] | None = None,
        disconnect: Callable[[], Awaitable[None]] | None = None,
    ) -> CallResponse:
        return await self.execute(prepared, emit=emit, disconnect=disconnect)

    async def prepare_test(
        self,
        provider_id: UUID,
        call_id: UUID,
        value: GenerateInput | DecisionInput,
        context: TrustedContext,
    ) -> PreparedExecution:
        self._require_platform(context, {"test"})
        return await self._prepare(provider_id, call_id, value, context, purpose="test")

    async def prepare_chat(
        self, command: ChatCallInput, context: TrustedContext
    ) -> PreparedExecution:
        self._require_tenant(context, {"chat"})
        selection = command.selection()
        if selection.provider_id is None:
            raise ModelError("route_binding_missing", "自动选模需要通过路由服务。")
        return await self._prepare(
            selection.provider_id,
            command.call_id,
            command.input,
            context,
            purpose="chat",
        )

    async def _prepare(
        self,
        provider_id: UUID,
        call_id: UUID,
        value: GenerateInput | DecisionInput,
        context: TrustedContext,
        *,
        purpose: str,
        deadline_at: datetime | None = None,
        grant: RoutingGrant | None = None,
    ) -> PreparedExecution:
        started_at = _utcnow()
        deadline_at = deadline_at or deadline_from(started_at)
        if deadline_at <= started_at:
            raise ModelError("model_timeout", "本次调用已超过父时限。")
        started_monotonic = time.monotonic()
        mode = "diagnostic" if purpose == "test" else "verified"
        holder: dict[str, Any] = {}

        def validate(state: ProviderState) -> None:
            self._validate_call_state(state, value, context, purpose, mode, call_id, grant)
            try:
                validation.validate_base_url(state.base_url, required=state.kind == "decision")
            except ModelError:
                raise
            try:
                opened = secrets.open_provider_secret(str(state.id), state.ciphertext)
            except secrets.SecretUnavailable as exc:
                raise ModelError("unavailable", "模型凭据服务未配置。") from exc
            except secrets.SecretInvalid as exc:
                raise ModelError("unavailable", "模型凭据无法读取。") from exc
            holder["secret"] = opened

        command = BeginCallCommand(
            call_id=call_id,
            provider_id=provider_id,
            purpose=purpose,  # type: ignore[arg-type]
            context=context,
            capability_summary=_capability_summary(value, mode),
            started_monotonic=started_monotonic,
            started_at=started_at,
            deadline_at=deadline_at,
            validate=validate,
        )
        spec, ciphertext, policy = await asyncio.to_thread(self._repository.place_call, command)
        binding = ContinuationBinding(
            actor=context.actor_user_id,
            scope=context.scope,
            provider=spec.provider_id,
            binding_version=spec.binding_version,
        )
        prepared_context = InvocationContext(
            call_id=call_id,
            capability_mode=mode,  # type: ignore[arg-type]
            continuation_binding=binding,
        )
        secret = holder.get("secret")
        access = ProviderAccess(
            spec=spec,
            api_key=SecretStr(secret.api_key if secret else ""),
            connection_params=dict(secret.connection_params) if secret else {},
        )
        request = ExecutionRequest(
            context=prepared_context,
            access=access,
            input=value.model_copy(deep=True),
            policy=policy,
        )
        return PreparedExecution(
            prepared=PreparedCall(
                spec=spec,
                context=prepared_context,
                policy=policy,
                started_at=started_at,
                deadline_at=deadline_at,
                ciphertext=ciphertext,
            ),
            request=request,
            value=value,
            purpose="test" if purpose == "test" else "chat",
            started_monotonic=started_monotonic,
        )

    async def execute(
        self,
        prepared: PreparedExecution,
        *,
        emit: Callable[[Any], Awaitable[None]] | None = None,
        disconnect: Callable[[], Awaitable[None]] | None = None,
    ) -> CallResponse:
        request = prepared.request
        call_id = request.context.call_id
        try:
            result, host_canceled = await self._run_runtime(request, emit, disconnect)
        except asyncio.CancelledError:
            result = _canceled_result(request, prepared.started_monotonic)
            host_canceled = True
        recorded = await self._finish(call_id, result)
        response = self._response(result, prepared.value, recorded)
        if host_canceled:
            raise asyncio.CancelledError()
        return response

    def _validate_call_state(
        self,
        state: ProviderState,
        value: GenerateInput | DecisionInput,
        context: TrustedContext,
        purpose: str,
        mode: str,
        call_id: UUID,
        grant: RoutingGrant | None = None,
    ) -> None:
        spec = self._spec(state)
        if purpose == "chat":
            if context.scope.kind != "tenant":
                raise ModelError("forbidden")
            if grant is not None:
                self._validate_grant(grant, state, context, call_id, value)
                if not state.enabled:
                    raise ModelError("model_unavailable", "当前模型已不可用，请重新选择。")
            else:
                if not capability_rules.chat_supported(spec.kind, spec.adapter):
                    raise ModelError("chat_model_not_supported", "决策模型仅支持管理端测试。")
                if not state.enabled or not state.chat_visible:
                    raise ModelError("model_unavailable", "当前模型已不可用，请重新选择。")
                if not isinstance(value, GenerateInput):
                    raise ModelError("chat_model_not_supported", "Chat 仅接受生成模型输入。")
        else:
            if context.scope.kind != "platform":
                raise ModelError("forbidden")
            if state.kind == "decision" and not isinstance(value, DecisionInput):
                raise ModelError("validation_failed", "决策模型需要决策输入。")
        if state.kind == "decision" and not state.has_api_key:
            raise ModelError("validation_failed", "决策模型必须填写 API Key。")
        try:
            capability_rules.validate_input(spec, value, mode)
        except RuntimeFailure as exc:
            code = (
                "capability_not_supported"
                if exc.error.code == "capability_not_supported"
                else "validation_failed"
            )
            raise ModelError(code, _capability_message(exc.error.code)) from exc

    @staticmethod
    def _validate_grant(
        grant: RoutingGrant,
        state: ProviderState,
        context: TrustedContext,
        call_id: UUID,
        value: GenerateInput | DecisionInput,
    ) -> None:
        if (
            grant.actor_user_id != context.actor_user_id
            or grant.scope != context.scope
            or grant.call_id != call_id
            or grant.provider_id != state.id
        ):
            raise ModelError("forbidden")
        if grant.stage in {"intent_jev", "intent_flash"}:
            if state.kind == "decision":
                if not isinstance(value, DecisionInput):
                    raise ModelError("validation_failed", "JEV 分类需要决策输入。")
                if state.adapter != "systemone_decision":
                    raise ModelError("route_binding_missing", "JEV 分类角色绑定无效。")
            else:
                if not isinstance(value, GenerateInput):
                    raise ModelError("validation_failed", "Flash 分类需要生成输入。")
                if state.adapter != "litellm_chat":
                    raise ModelError("route_binding_missing", "Flash 分类角色绑定无效。")

    async def _run_runtime(
        self,
        request: ExecutionRequest,
        emit: Callable[[Any], Awaitable[None]] | None,
        disconnect: Callable[[], Awaitable[None]] | None,
    ) -> tuple[RuntimeResult, bool]:
        runtime_task = asyncio.create_task(self._runtime.invoke(request, emit))

        async def _disconnect() -> None:
            assert disconnect is not None
            await disconnect()

        disconnect_task = asyncio.create_task(_disconnect()) if disconnect is not None else None
        pending: set[asyncio.Task[Any]] = {runtime_task}
        if disconnect_task is not None:
            pending.add(disconnect_task)
        canceled = False
        try:
            try:
                done, _ = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            except asyncio.CancelledError:
                canceled = True
                runtime_task.cancel()
                done = set()
            if disconnect_task is not None and disconnect_task in done and not runtime_task.done():
                runtime_task.cancel()
            if canceled or (disconnect_task is not None and disconnect_task in done):
                result = await _collect_canceled(runtime_task)
                return result, canceled
            try:
                return runtime_task.result(), False
            except RuntimeCancelled as exc:
                return exc.result, False
        finally:
            if disconnect_task is not None:
                disconnect_task.cancel()
                await asyncio.gather(disconnect_task, return_exceptions=True)

    async def _finish(self, call_id: UUID, result: RuntimeResult) -> bool:
        task = asyncio.create_task(
            asyncio.to_thread(
                self._repository.finish_call,
                call_id,
                status=result.status,
                duration_ms=result.duration_ms,
                usage=result.usage,
                error_code=result.error.code if result.error else None,
                response_model=result.response_model,
                upstream_request_id=result.upstream_request_id,
                upstream_outcome_unknown=result.upstream_outcome_unknown,
            )
        )
        try:
            async with asyncio.timeout(2.0):
                return await asyncio.shield(task)
        except TimeoutError:
            task.cancel()
            return False
        except asyncio.CancelledError:
            try:
                async with asyncio.timeout(2.0):
                    return await asyncio.shield(task)
            except Exception:
                return False

    # -- reads -------------------------------------------------------------

    async def call_record(self, call_id: UUID, context: TrustedContext) -> CallRecordView:
        row = await asyncio.to_thread(self._repository.call, call_id)
        if row is None:
            raise ModelError("not_found")
        self._authorize_record(row, context)
        return self._record(row)

    async def test_record(self, call_id: UUID, context: TrustedContext) -> CallRecordView:
        self._require_platform(context, {"test"})
        row = await asyncio.to_thread(self._repository.call, call_id)
        if row is None or row.purpose != "test" or row.actor_user_id != context.actor_user_id:
            raise ModelError("not_found")
        return self._record(row)

    def _authorize_record(self, row: CallRow, context: TrustedContext) -> None:
        if row.purpose == "test":
            if context.scope.kind != "platform" or row.actor_user_id != context.actor_user_id:
                raise ModelError("not_found")
            return
        if context.scope.kind != "tenant":
            raise ModelError("not_found")
        if row.tenant_id != context.scope.tenant_id:
            raise ModelError("not_found")
        if row.workspace_id != context.scope.workspace_id:
            raise ModelError("not_found")

    def _record(self, row: CallRow) -> CallRecordView:
        status = row.status
        error_code = row.error_code
        unknown = row.upstream_outcome_unknown
        if status == "running" and as_utc(row.deadline_at) < _utcnow():
            status = "incomplete"
            error_code = "model_timeout"
            unknown = True
        return CallRecordView(
            call_id=row.call_id,
            provider_id=row.provider_id,
            config_version=row.config_version,
            binding_version=row.binding_version,
            purpose=row.purpose,
            status=status,  # type: ignore[arg-type]
            requested_model=row.requested_model,
            response_model=row.response_model,
            started_at=row.started_at,
            finished_at=row.finished_at,
            duration_ms=row.duration_ms,
            usage=row.usage,
            error_code=error_code,
            upstream_outcome_unknown=unknown,
        )

    # -- mapping -----------------------------------------------------------

    def _binding(self, catalog_id: str, api_model: str) -> ProviderBinding:
        model = validation.validate_api_model(api_model)
        record = catalog.find_record(catalog_id)
        if record is not None:
            kind = record.kind
            adapter = record.adapter
            vendor = record.vendor
            sdk_model = _route(vendor, model) if kind == "llm" else None
            return ProviderBinding(
                catalog_id=catalog_id,
                vendor=vendor,
                api_model=model,
                sdk_model=sdk_model,
                alias="",
                kind=kind,
                adapter=adapter,
            )
        try:
            kind, adapter, vendor, _ = catalog.classify_manual(catalog_id)
        except ValueError as exc:
            raise ModelError("validation_failed", "请填写 provider/model 格式的模型标识。") from exc
        sdk_model = _route(vendor, model) if kind == "llm" else None
        return ProviderBinding(
            catalog_id=catalog_id,
            vendor=vendor,
            api_model=model,
            sdk_model=sdk_model,
            alias="",
            kind=kind,
            adapter=adapter,
        )

    def spec_for(self, state: ProviderState) -> ModelSpec:
        return self._spec(state)

    @staticmethod
    def _spec(state: ProviderState) -> ModelSpec:
        return ModelSpec(
            provider_id=state.id,
            config_version=state.version,
            binding_version=state.binding_version,
            vendor=state.vendor,
            kind=state.kind,
            adapter=state.adapter,  # type: ignore[arg-type]
            api_model=state.api_model,
            sdk_model=state.sdk_model,
            api_base=state.base_url,
        )

    def _view(self, state: ProviderState, latest: CallRow | None) -> ProviderView:
        capabilities = capability_rules.resolve(self._spec(state))
        summary = None
        if latest is not None:
            capabilities_list = latest.capability_summary.get("capabilities")
            summary = TestSummary(
                call_id=latest.call_id,
                status=latest.status,
                binding_version=latest.binding_version,
                started_at=latest.started_at,
                capabilities=[str(item) for item in capabilities_list]
                if isinstance(capabilities_list, list)
                else [],
                stale=latest.binding_version != state.binding_version,
            )
        return ProviderView(
            id=state.id,
            catalog_id=state.catalog_id,
            vendor=state.vendor,
            api_model=state.api_model,
            alias=state.alias,
            kind=state.kind,
            base_url=state.base_url,
            has_api_key=state.has_api_key,
            has_connection_params=state.has_connection_params,
            enabled=state.enabled,
            chat_visible=state.chat_visible,
            chat_supported=capability_rules.chat_supported(state.kind, state.adapter),
            version=state.version,
            binding_version=state.binding_version,
            capabilities=capabilities,
            latest_test=summary,
            created_at=state.created_at,
            updated_at=state.updated_at,
        )

    @staticmethod
    def _response(
        result: RuntimeResult, value: GenerateInput | DecisionInput, recorded: bool
    ) -> CallResponse:
        status = result.status
        error_code = result.error.code if result.error else None
        upstream_unknown = result.upstream_outcome_unknown
        if not recorded:
            status = "failed"
            error_code = "record_write_failed"
            upstream_unknown = result.upstream_sent
        output = result.output
        text = ""
        tool_calls = []
        answers = None
        token = None
        if isinstance(output, GenerationOutput):
            text = output.text
            tool_calls = list(output.tool_calls)
            token = output.continuation_token
        else:
            answers = dict(output.answers)
        return CallResponse(
            call_id=result.call_id,
            provider_id=result.provider_id,
            config_version=result.config_version,
            status=status,  # type: ignore[arg-type]
            duration_ms=result.duration_ms,
            usage=result.usage if isinstance(result.usage, ModelUsage) else ModelUsage(),
            error_code=error_code,
            upstream_outcome_unknown=upstream_unknown,
            requested_model=result.requested_model,
            response_model=result.response_model,
            upstream_request_id=result.upstream_request_id,
            finish_reason=result.finish_reason,
            validation=result.validation
            if isinstance(result.validation, OutputValidation)
            else OutputValidation(),
            kind=value.kind,
            text=text,
            tool_calls=tool_calls,
            answers=answers,
            continuation_token=token,
        )

    @staticmethod
    def _require_platform(context: TrustedContext, purposes: set[str]) -> None:
        if context.scope.kind != "platform" or context.purpose not in purposes:
            raise ModelError("forbidden")

    @staticmethod
    def _require_tenant(context: TrustedContext, purposes: set[str]) -> None:
        if context.scope.kind != "tenant" or context.purpose not in purposes:
            raise ModelError("forbidden")


async def _collect_canceled(task: asyncio.Task[Any]) -> RuntimeResult:
    try:
        result = await asyncio.wait_for(asyncio.shield(task), 2.0)
        return result
    except RuntimeCancelled as exc:
        return exc.result
    except (asyncio.CancelledError, TimeoutError, Exception):
        pass
    raise asyncio.CancelledError()


def _key_digest(value: str | None) -> str | None:
    if value is None:
        return None
    import hashlib

    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canceled_result(request: ExecutionRequest, started: float) -> RuntimeResult:
    spec = request.access.spec
    return RuntimeResult(
        call_id=request.context.call_id,
        provider_id=spec.provider_id,
        config_version=spec.config_version,
        binding_version=spec.binding_version,
        requested_model=spec.api_model,
        status="canceled",
        duration_ms=max(0, int((time.monotonic() - started) * 1000)),
        output=GenerationOutput() if spec.kind == "llm" else _empty_decision(),
        usage=ModelUsage(),
        validation=OutputValidation(),
        upstream_outcome_unknown=True,
        upstream_sent=True,
    )


def _empty_decision():
    from compass_agent.llm.contracts import DecisionOutput

    return DecisionOutput()


def _capability_message(code: str) -> str:
    if code == "capability_not_supported":
        return "当前模型不支持所选能力或参数组合。"
    if code == "validation_failed":
        return "请检查调用输入与参数组合。"
    return "当前模型无法接受该输入。"
