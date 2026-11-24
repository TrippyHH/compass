"""Model providers, configuration changes and call metadata."""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "model_provider",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("catalog_id", sa.String(200), nullable=False),
        sa.Column("vendor", sa.String(200), nullable=False),
        sa.Column("api_model", sa.String(200), nullable=False),
        sa.Column("sdk_model", sa.String(200), nullable=True),
        sa.Column("alias", sa.String(80), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("adapter", sa.String(30), nullable=False),
        sa.Column("base_url", sa.String(2048), nullable=False, server_default=""),
        sa.Column("api_key_ciphertext", sa.Text(), nullable=False, server_default=""),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("chat_visible", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("binding_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.CheckConstraint("kind IN ('llm', 'decision')", name="ck_model_provider_kind"),
        sa.CheckConstraint(
            "adapter IN ('litellm_chat', 'systemone_decision')",
            name="ck_model_provider_adapter",
        ),
        sa.CheckConstraint(
            "kind <> 'decision' OR chat_visible = false",
            name="ck_model_provider_decision_hidden",
        ),
        sa.CheckConstraint(
            "version > 0 AND binding_version > 0 AND binding_version <= version",
            name="ck_model_provider_versions",
        ),
    )
    op.create_index(
        "ux_model_provider_alias",
        "model_provider",
        [sa.text("lower(trim(alias))")],
        unique=True,
    )
    op.create_table(
        "model_change",
        sa.Column("operation_id", sa.Uuid(), primary_key=True),
        sa.Column(
            "provider_id",
            sa.Uuid(),
            sa.ForeignKey("model_provider.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("actor_user_id", sa.Uuid(), nullable=False),
        sa.Column("request_mac", sa.String(64), nullable=False),
        sa.Column("before_version", sa.Integer(), nullable=True),
        sa.Column("after_version", sa.Integer(), nullable=False),
        sa.Column("changed_fields", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_model_change_provider_time", "model_change", ["provider_id", "created_at"])
    op.create_table(
        "model_call",
        sa.Column("call_id", sa.Uuid(), primary_key=True),
        sa.Column(
            "provider_id",
            sa.Uuid(),
            sa.ForeignKey("model_provider.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("config_version", sa.Integer(), nullable=False),
        sa.Column("binding_version", sa.Integer(), nullable=False),
        sa.Column("requested_model", sa.String(200), nullable=False),
        sa.Column("response_model", sa.String(200), nullable=True),
        sa.Column("purpose", sa.String(20), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=True),
        sa.Column("workspace_id", sa.Uuid(), nullable=True),
        sa.Column("capability_summary", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="running"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("usage", sa.JSON(), nullable=True),
        sa.Column("error_code", sa.String(80), nullable=True),
        sa.Column("upstream_request_id", sa.String(200), nullable=True),
        sa.Column(
            "upstream_outcome_unknown",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.CheckConstraint("purpose IN ('test', 'chat')", name="ck_model_call_purpose"),
        sa.CheckConstraint(
            "status IN ('running', 'completed', 'failed', 'canceled', 'incomplete')",
            name="ck_model_call_status",
        ),
        sa.CheckConstraint(
            "(purpose = 'test' AND tenant_id IS NULL AND workspace_id IS NULL)"
            " OR (purpose = 'chat' AND tenant_id IS NOT NULL AND workspace_id IS NOT NULL)",
            name="ck_model_call_scope",
        ),
    )
    op.create_index(
        "ix_model_call_provider_time",
        "model_call",
        ["provider_id", "purpose", "started_at"],
    )
    op.create_index(
        "ix_model_call_actor_time", "model_call", ["tenant_id", "actor_user_id", "started_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_model_call_actor_time", table_name="model_call")
    op.drop_index("ix_model_call_provider_time", table_name="model_call")
    op.drop_table("model_call")
    op.drop_index("ix_model_change_provider_time", table_name="model_change")
    op.drop_table("model_change")
    op.drop_index("ux_model_provider_alias", table_name="model_provider")
    op.drop_table("model_provider")
