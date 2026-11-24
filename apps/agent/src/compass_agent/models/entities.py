"""ORM entities for model configuration, change history and call metadata."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from compass_agent.database import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class ModelProvider(Base):
    __tablename__ = "model_provider"
    __table_args__ = (
        CheckConstraint("kind IN ('llm', 'decision')", name="ck_model_provider_kind"),
        CheckConstraint(
            "adapter IN ('litellm_chat', 'systemone_decision')",
            name="ck_model_provider_adapter",
        ),
        CheckConstraint(
            "kind <> 'decision' OR chat_visible = false",
            name="ck_model_provider_decision_hidden",
        ),
        CheckConstraint(
            "version > 0 AND binding_version > 0 AND binding_version <= version",
            name="ck_model_provider_versions",
        ),
        Index("ux_model_provider_alias", text("lower(trim(alias))"), unique=True),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    catalog_id: Mapped[str] = mapped_column(String(200), nullable=False)
    vendor: Mapped[str] = mapped_column(String(200), nullable=False)
    api_model: Mapped[str] = mapped_column(String(200), nullable=False)
    sdk_model: Mapped[str | None] = mapped_column(String(200))
    alias: Mapped[str] = mapped_column(String(80), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    adapter: Mapped[str] = mapped_column(String(30), nullable=False)
    base_url: Mapped[str] = mapped_column(String(2048), nullable=False, default="")
    api_key_ciphertext: Mapped[str] = mapped_column(Text, nullable=False, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    chat_visible: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    binding_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )
    updated_by: Mapped[UUID | None] = mapped_column(Uuid)


class ModelChange(Base):
    __tablename__ = "model_change"
    __table_args__ = (Index("ix_model_change_provider_time", "provider_id", "created_at"),)

    operation_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    provider_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("model_provider.id", ondelete="RESTRICT"), nullable=False
    )
    actor_user_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    request_mac: Mapped[str] = mapped_column(String(64), nullable=False)
    before_version: Mapped[int | None] = mapped_column(Integer)
    after_version: Mapped[int] = mapped_column(Integer, nullable=False)
    changed_fields: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )


class ModelCall(Base):
    __tablename__ = "model_call"
    __table_args__ = (
        CheckConstraint("purpose IN ('test', 'chat')", name="ck_model_call_purpose"),
        CheckConstraint(
            "status IN ('running', 'completed', 'failed', 'canceled', 'incomplete')",
            name="ck_model_call_status",
        ),
        CheckConstraint(
            "(purpose = 'test' AND tenant_id IS NULL AND workspace_id IS NULL)"
            " OR (purpose = 'chat' AND tenant_id IS NOT NULL AND workspace_id IS NOT NULL)",
            name="ck_model_call_scope",
        ),
        Index("ix_model_call_provider_time", "provider_id", "purpose", "started_at"),
        Index("ix_model_call_actor_time", "tenant_id", "actor_user_id", "started_at"),
    )

    call_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    provider_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("model_provider.id", ondelete="RESTRICT"), nullable=False
    )
    config_version: Mapped[int] = mapped_column(Integer, nullable=False)
    binding_version: Mapped[int] = mapped_column(Integer, nullable=False)
    requested_model: Mapped[str] = mapped_column(String(200), nullable=False)
    response_model: Mapped[str | None] = mapped_column(String(200))
    purpose: Mapped[str] = mapped_column(String(20), nullable=False)
    actor_user_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    tenant_id: Mapped[UUID | None] = mapped_column(Uuid)
    workspace_id: Mapped[UUID | None] = mapped_column(Uuid)
    capability_summary: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="running")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    usage: Mapped[dict | None] = mapped_column(JSON)
    error_code: Mapped[str | None] = mapped_column(String(80))
    upstream_request_id: Mapped[str | None] = mapped_column(String(200))
    upstream_outcome_unknown: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
