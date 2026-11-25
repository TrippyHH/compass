"""Short transactions around model configuration and call metadata.

No network call happens inside a transaction here. The service passes a validation
callback so capability and endpoint rules run while the provider row is locked.
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, cast
from uuid import UUID, uuid4

from sqlalchemy import func, select, text, update
from sqlalchemy.engine import CursorResult, Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from compass_agent.llm.contracts import CallPolicy, ModelSpec
from compass_agent.schemas import ModelUsage, TrustedContext
from compass_agent.settings import get_settings

from . import secrets
from .entities import ModelCall, ModelChange, ModelProvider, utcnow
from .errors import ModelError

CALL_BUDGET_SECONDS = 180


def as_utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class ProviderState:
    id: UUID
    catalog_id: str
    vendor: str
    api_model: str
    sdk_model: str | None
    alias: str
    kind: Literal["llm", "decision"]
    adapter: str
    base_url: str
    ciphertext: str
    has_api_key: bool
    has_connection_params: bool
    enabled: bool
    chat_visible: bool
    version: int
    binding_version: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class ProviderBinding:
    catalog_id: str
    vendor: str
    api_model: str
    sdk_model: str | None
    alias: str
    kind: Literal["llm", "decision"]
    adapter: str


@dataclass(frozen=True, slots=True)
class SaveProviderCommand:
    operation_id: UUID
    provider_id: UUID | None
    expected_version: int | None
    actor_user_id: UUID
    request_mac: str
    binding: ProviderBinding
    base_url: str
    api_key: str | None
    connection_params: dict | None
    enabled: bool
    chat_visible: bool


@dataclass(frozen=True, slots=True)
class SavedProviderResult:
    provider_id: UUID
    version: int
    operation_id: UUID


@dataclass(frozen=True, slots=True)
class BeginCallCommand:
    call_id: UUID
    provider_id: UUID
    purpose: Literal["test", "chat"]
    context: TrustedContext
    capability_summary: dict
    started_monotonic: float
    started_at: datetime
    deadline_at: datetime
    validate: Callable[[ProviderState], None]


@dataclass(frozen=True, slots=True)
class CallRow:
    call_id: UUID
    provider_id: UUID
    config_version: int
    binding_version: int
    requested_model: str
    response_model: str | None
    purpose: Literal["test", "chat"]
    actor_user_id: UUID
    tenant_id: UUID | None
    workspace_id: UUID | None
    capability_summary: dict
    status: str
    started_at: datetime
    deadline_at: datetime
    finished_at: datetime | None
    duration_ms: int | None
    usage: ModelUsage
    error_code: str | None
    upstream_request_id: str | None
    upstream_outcome_unknown: bool


@dataclass(frozen=True, slots=True)
class ChangeRow:
    operation_id: UUID
    provider_id: UUID
    actor_user_id: UUID
    request_mac: str
    before_version: int | None
    after_version: int
    changed_fields: list[str]
    created_at: datetime


def request_digest(payload: dict[str, Any]) -> str:
    canonical = repr(sorted((key, value) for key, value in payload.items())).encode("utf-8")
    key = get_settings().compass_model_encryption_key.encode("utf-8")
    if key:
        return hmac.new(key, canonical, hashlib.sha256).hexdigest()
    return hashlib.sha256(canonical).hexdigest()


def _state(row: ModelProvider) -> ProviderState:
    has_key = False
    has_params = False
    if row.api_key_ciphertext:
        try:
            opened = secrets.open_provider_secret(str(row.id), row.api_key_ciphertext)
            has_key = bool(opened.api_key)
            has_params = bool(opened.connection_params)
        except (secrets.SecretInvalid, secrets.SecretUnavailable):
            has_key = bool(row.api_key_ciphertext)
    return ProviderState(
        id=row.id,
        catalog_id=row.catalog_id,
        vendor=row.vendor,
        api_model=row.api_model,
        sdk_model=row.sdk_model,
        alias=row.alias,
        kind=row.kind,  # type: ignore[arg-type]
        adapter=row.adapter,
        base_url=row.base_url,
        ciphertext=row.api_key_ciphertext,
        has_api_key=has_key,
        has_connection_params=has_params,
        enabled=row.enabled,
        chat_visible=row.chat_visible,
        version=row.version,
        binding_version=row.binding_version,
        created_at=as_utc(row.created_at),
        updated_at=as_utc(row.updated_at),
    )


def _call_row(row: ModelCall) -> CallRow:
    usage = row.usage if isinstance(row.usage, dict) else {}
    return CallRow(
        call_id=row.call_id,
        provider_id=row.provider_id,
        config_version=row.config_version,
        binding_version=row.binding_version,
        requested_model=row.requested_model,
        response_model=row.response_model,
        purpose=row.purpose,  # type: ignore[arg-type]
        actor_user_id=row.actor_user_id,
        tenant_id=row.tenant_id,
        workspace_id=row.workspace_id,
        capability_summary=row.capability_summary or {},
        status=row.status,
        started_at=as_utc(row.started_at),
        deadline_at=as_utc(row.deadline_at),
        finished_at=as_utc(row.finished_at) if row.finished_at else None,
        duration_ms=row.duration_ms,
        usage=ModelUsage.model_validate(usage),
        error_code=row.error_code,
        upstream_request_id=row.upstream_request_id,
        upstream_outcome_unknown=row.upstream_outcome_unknown,
    )


def _change_row(row: ModelChange) -> ChangeRow:
    return ChangeRow(
        operation_id=row.operation_id,
        provider_id=row.provider_id,
        actor_user_id=row.actor_user_id,
        request_mac=row.request_mac,
        before_version=row.before_version,
        after_version=row.after_version,
        changed_fields=list(row.changed_fields or []),
        created_at=as_utc(row.created_at),
    )


class ModelRepository:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    @contextmanager
    def _write(self, *, statement_timeout: str | None = None) -> Iterator[Session]:
        with Session(self._engine) as session:
            with session.begin():
                if statement_timeout and session.bind is not None:
                    if session.bind.dialect.name == "postgresql":
                        session.execute(
                            text(f"SET LOCAL statement_timeout = '{statement_timeout}'")
                        )
                yield session

    @contextmanager
    def _read(self) -> Iterator[Session]:
        with Session(self._engine) as session:
            yield session

    # -- configuration -----------------------------------------------------

    def save_provider(self, command: SaveProviderCommand) -> SavedProviderResult:
        try:
            return self._save_once(command)
        except IntegrityError as exc:
            detail = str(getattr(exc, "orig", exc))
            if "model_change_pkey" in detail:
                existing = self.get_operation(command.operation_id)
                if existing is None:
                    raise ModelError("conflict", "保存操作发生并发冲突，请重试。") from exc
                self._require_same_operation(existing, command)
                return SavedProviderResult(
                    existing.provider_id, existing.after_version, command.operation_id
                )
            if "ux_model_provider_alias" in detail:
                raise ModelError("conflict", "别称已被使用，请更换后重试。") from exc
            raise

    def _save_once(self, command: SaveProviderCommand) -> SavedProviderResult:
        binding = command.binding
        with self._write() as session:
            existing = session.get(ModelChange, command.operation_id)
            if existing is not None:
                self._require_same_operation(_change_row(existing), command)
                return SavedProviderResult(
                    existing.provider_id, existing.after_version, command.operation_id
                )
            now = utcnow()
            if command.provider_id is None:
                row = ModelProvider(
                    id=uuid4(),
                    catalog_id=binding.catalog_id,
                    vendor=binding.vendor,
                    api_model=binding.api_model,
                    sdk_model=binding.sdk_model,
                    alias=binding.alias,
                    kind=binding.kind,
                    adapter=binding.adapter,
                    base_url=command.base_url,
                    api_key_ciphertext="",
                    enabled=command.enabled,
                    chat_visible=command.chat_visible,
                    version=1,
                    binding_version=1,
                    created_at=now,
                    updated_at=now,
                    updated_by=command.actor_user_id,
                )
                session.add(row)
                before_version: int | None = None
                previous_key = ""
                previous_params: dict = {}
            else:
                row = session.execute(
                    select(ModelProvider)
                    .where(ModelProvider.id == command.provider_id)
                    .with_for_update()
                ).scalar_one_or_none()
                if row is None:
                    raise ModelError("not_found")
                if row.version != command.expected_version:
                    raise ModelError(
                        "conflict",
                        "配置已被更新，请重新加载并核对修改。",
                        current_version=row.version,
                    )
                before_version = row.version
                try:
                    previous_secret = secrets.open_provider_secret(
                        str(row.id), row.api_key_ciphertext
                    )
                except (secrets.SecretInvalid, secrets.SecretUnavailable) as exc:
                    raise ModelError("unavailable", "模型凭据无法读取。") from exc
                previous_key = previous_secret.api_key
                previous_params = previous_secret.connection_params

            merged_key = command.api_key if command.api_key not in (None, "") else previous_key
            merged_params = (
                previous_params
                if command.connection_params is None
                else dict(command.connection_params)
            )
            if binding.kind == "decision":
                if not command.base_url:
                    raise ModelError("validation_failed", "决策模型必须填写 Base URL。")
                if not merged_key:
                    raise ModelError("validation_failed", "决策模型必须填写 API Key。")
                if command.chat_visible:
                    raise ModelError("chat_model_not_supported", "决策模型仅支持管理端测试。")
            if command.chat_visible and not (
                binding.kind == "llm" and binding.adapter == "litellm_chat"
            ):
                raise ModelError("chat_model_not_supported", "当前模型不支持 Chat。")

            key_changed = merged_key != previous_key
            params_changed = merged_params != previous_params
            binding_changed = command.provider_id is None or any(
                (
                    row.catalog_id != binding.catalog_id,
                    row.api_model != binding.api_model,
                    row.sdk_model != binding.sdk_model,
                    row.base_url != command.base_url,
                    row.kind != binding.kind,
                    row.adapter != binding.adapter,
                    key_changed,
                    params_changed,
                )
            )
            changed_fields = self._changed_fields(
                row,
                binding,
                command,
                key_changed,
                params_changed,
                is_create=command.provider_id is None,
            )
            row.catalog_id = binding.catalog_id
            row.vendor = binding.vendor
            row.api_model = binding.api_model
            row.sdk_model = binding.sdk_model
            row.alias = binding.alias
            row.kind = binding.kind
            row.adapter = binding.adapter
            row.base_url = command.base_url
            row.enabled = command.enabled
            row.chat_visible = command.chat_visible
            row.version = (before_version or 0) + 1
            row.updated_at = now
            row.updated_by = command.actor_user_id
            if command.provider_id is None or key_changed or params_changed:
                try:
                    row.api_key_ciphertext = secrets.seal_provider_secret(
                        str(row.id), merged_key, merged_params
                    )
                except secrets.SecretUnavailable as exc:
                    raise ModelError("unavailable", "模型凭据服务未配置。") from exc
            if binding_changed:
                row.binding_version = row.version
            # 变更记录引用 provider 主键：先落 provider，再写变更行，避免工作单元按实体名
            # 排序时先插入 model_change 触发外键错误。
            session.flush()
            session.add(
                ModelChange(
                    operation_id=command.operation_id,
                    provider_id=row.id,
                    actor_user_id=command.actor_user_id,
                    request_mac=command.request_mac,
                    before_version=before_version,
                    after_version=row.version,
                    changed_fields=changed_fields,
                    created_at=now,
                )
            )
            session.flush()
            return SavedProviderResult(row.id, row.version, command.operation_id)

    @staticmethod
    def _require_same_operation(existing: ChangeRow, command: SaveProviderCommand) -> None:
        if (
            existing.actor_user_id != command.actor_user_id
            or existing.request_mac != command.request_mac
        ):
            raise ModelError("conflict", "操作标识已用于其他保存请求。")

    @staticmethod
    def _changed_fields(
        row: ModelProvider,
        binding: ProviderBinding,
        command: SaveProviderCommand,
        key_changed: bool,
        params_changed: bool,
        *,
        is_create: bool,
    ) -> list[str]:
        if is_create:
            return sorted(
                {
                    "alias",
                    "catalog_id",
                    "api_model",
                    "sdk_model",
                    "kind",
                    "adapter",
                    "base_url",
                    "enabled",
                    "chat_visible",
                    "api_key",
                    "connection_params",
                }
            )
        fields: list[str] = []
        candidates = {
            "alias": (row.alias, binding.alias),
            "catalog_id": (row.catalog_id, binding.catalog_id),
            "api_model": (row.api_model, binding.api_model),
            "sdk_model": (row.sdk_model, binding.sdk_model),
            "kind": (row.kind, binding.kind),
            "adapter": (row.adapter, binding.adapter),
            "base_url": (row.base_url, command.base_url),
            "enabled": (row.enabled, command.enabled),
            "chat_visible": (row.chat_visible, command.chat_visible),
        }
        for name, (old, new) in candidates.items():
            if old != new:
                fields.append(name)
        if key_changed:
            fields.append("api_key")
        if params_changed:
            fields.append("connection_params")
        return sorted(fields)

    def get_operation(self, operation_id: UUID) -> ChangeRow | None:
        with self._read() as session:
            row = session.get(ModelChange, operation_id)
            return _change_row(row) if row else None

    def provider(self, provider_id: UUID) -> ProviderState:
        with self._read() as session:
            row = session.get(ModelProvider, provider_id)
            if row is None:
                raise ModelError("not_found")
            return _state(row)

    def provider_page(
        self, *, q: str = "", page: int = 1, page_size: int = 20
    ) -> tuple[list[ProviderState], int]:
        with self._read() as session:
            statement = select(ModelProvider)
            if q:
                pattern = f"%{q.casefold()}%"
                statement = statement.where(
                    func.lower(ModelProvider.alias).like(pattern)
                    | func.lower(ModelProvider.api_model).like(pattern)
                )
            total = session.scalar(select(func.count()).select_from(statement.subquery())) or 0
            rows = session.scalars(
                statement.order_by(func.lower(ModelProvider.alias))
                .offset((page - 1) * page_size)
                .limit(page_size)
            ).all()
            return [_state(row) for row in rows], int(total)

    def changes(
        self, provider_id: UUID, *, page: int = 1, page_size: int = 20
    ) -> tuple[list[ChangeRow], int]:
        with self._read() as session:
            total = (
                session.scalar(
                    select(func.count())
                    .select_from(ModelChange)
                    .where(ModelChange.provider_id == provider_id)
                )
                or 0
            )
            rows = session.scalars(
                select(ModelChange)
                .where(ModelChange.provider_id == provider_id)
                .order_by(ModelChange.created_at.desc(), ModelChange.operation_id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            ).all()
            return [_change_row(row) for row in rows], int(total)

    def available(self) -> list[ProviderState]:
        with self._read() as session:
            rows = session.scalars(
                select(ModelProvider)
                .where(
                    ModelProvider.enabled.is_(True),
                    ModelProvider.chat_visible.is_(True),
                    ModelProvider.kind == "llm",
                    ModelProvider.adapter == "litellm_chat",
                )
                .order_by(func.lower(ModelProvider.alias))
            ).all()
            return [_state(row) for row in rows]

    def routing_provider(self, role: str, configured: str) -> ProviderState:
        """Resolve one code-configured routing role to an enabled Provider.

        The binding is deliberately code/environment-owned: no browser input and no
        database policy row can add a role or widen the candidate set.
        """

        if not configured:
            raise ModelError("route_binding_missing", f"路由角色 {role} 未配置 Provider。")
        try:
            provider_id = UUID(configured)
        except ValueError as exc:
            raise ModelError(
                "route_binding_missing", f"路由角色 {role} 的 Provider 标识无效。"
            ) from exc
        try:
            state = self.provider(provider_id)
        except ModelError as exc:
            if exc.code == "not_found":
                raise ModelError(
                    "route_binding_missing", f"路由角色 {role} 的 Provider 不存在。"
                ) from exc
            raise
        if not state.enabled:
            raise ModelError("route_binding_missing", f"路由角色 {role} 的 Provider 已停用。")
        expected = ("decision", "systemone_decision") if role == "jev" else ("llm", "litellm_chat")
        if (state.kind, state.adapter) != expected:
            raise ModelError("route_binding_missing", f"路由角色 {role} 的 Provider 类型不匹配。")
        return state

    # -- calls -------------------------------------------------------------

    def place_call(self, command: BeginCallCommand) -> tuple[ModelSpec, str, CallPolicy]:
        with self._write() as session:
            row = session.execute(
                select(ModelProvider)
                .where(ModelProvider.id == command.provider_id)
                .with_for_update(read=True)
            ).scalar_one_or_none()
            if row is None:
                raise ModelError("not_found")
            state = _state(row)
            command.validate(state)
            if session.get(ModelCall, command.call_id) is not None:
                raise ModelError("call_already_exists", "调用标识已存在，请核对原调用。")
            session.add(
                ModelCall(
                    call_id=command.call_id,
                    provider_id=row.id,
                    config_version=row.version,
                    binding_version=row.binding_version,
                    requested_model=row.api_model,
                    response_model=None,
                    purpose=command.purpose,
                    actor_user_id=command.context.actor_user_id,
                    tenant_id=getattr(command.context.scope, "tenant_id", None),
                    workspace_id=getattr(command.context.scope, "workspace_id", None),
                    capability_summary=command.capability_summary,
                    status="running",
                    started_at=command.started_at,
                    deadline_at=command.deadline_at,
                    upstream_outcome_unknown=False,
                )
            )
            try:
                session.flush()
            except IntegrityError as exc:
                raise ModelError("call_already_exists", "调用标识已存在，请核对原调用。") from exc
            spec = ModelSpec(
                provider_id=row.id,
                config_version=row.version,
                binding_version=row.binding_version,
                vendor=row.vendor,
                kind=row.kind,  # type: ignore[arg-type]
                adapter=row.adapter,  # type: ignore[arg-type]
                api_model=row.api_model,
                sdk_model=row.sdk_model,
                api_base=row.base_url,
            )
            policy = CallPolicy(
                started_monotonic=command.started_monotonic,
                deadline_monotonic=command.started_monotonic
                + (command.deadline_at - command.started_at).total_seconds(),
            )
            return spec, row.api_key_ciphertext, policy

    def finish_call(
        self,
        call_id: UUID,
        *,
        status: str,
        duration_ms: int,
        usage: ModelUsage,
        error_code: str | None,
        response_model: str | None,
        upstream_request_id: str | None,
        upstream_outcome_unknown: bool,
    ) -> bool:
        values = {
            "status": status,
            "duration_ms": duration_ms,
            "usage": usage.model_dump(mode="json", exclude_none=True),
            "error_code": error_code,
            "response_model": response_model,
            "upstream_request_id": upstream_request_id,
            "upstream_outcome_unknown": upstream_outcome_unknown,
            "finished_at": utcnow(),
        }
        with self._write(statement_timeout="1800ms") as session:
            result = cast(
                "CursorResult[Any]",
                session.execute(
                    update(ModelCall)
                    .where(ModelCall.call_id == call_id, ModelCall.status == "running")
                    .values(**values)
                ),
            )
            if result.rowcount == 1:
                return True
            row = session.get(ModelCall, call_id)
            return row is not None and row.status == status

    def call(self, call_id: UUID) -> CallRow | None:
        with self._read() as session:
            row = session.get(ModelCall, call_id)
            return _call_row(row) if row else None

    def latest_test(self, provider_id: UUID) -> CallRow | None:
        with self._read() as session:
            row = session.scalars(
                select(ModelCall)
                .where(ModelCall.provider_id == provider_id, ModelCall.purpose == "test")
                .order_by(ModelCall.started_at.desc(), ModelCall.call_id.desc())
                .limit(1)
            ).first()
            return _call_row(row) if row else None


def deadline_from(started_at: datetime) -> datetime:
    return started_at + timedelta(seconds=CALL_BUDGET_SECONDS)


__all__ = [
    "BeginCallCommand",
    "CallRow",
    "ChangeRow",
    "ModelRepository",
    "ProviderBinding",
    "ProviderState",
    "SaveProviderCommand",
    "SavedProviderResult",
    "as_utc",
    "deadline_from",
    "request_digest",
]
