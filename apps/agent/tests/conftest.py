import time
from collections.abc import Iterator
from typing import Literal
from uuid import UUID, uuid4

import pytest
from compass_agent.database import Base
from compass_agent.llm.contracts import (
    CallPolicy,
    ContinuationBinding,
    ExecutionRequest,
    InvocationContext,
    ModelSpec,
    ProviderAccess,
)
from compass_agent.models import secrets
from compass_agent.schemas import (
    ChatMessage,
    GenerateInput,
    PlatformScope,
    TenantScope,
    TrustedContext,
)
from compass_agent.settings import get_settings
from pydantic import SecretStr
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool


@pytest.fixture(scope="session", autouse=True)
def warm_catalog() -> None:
    from compass_agent.llm import catalog

    catalog.records()


@pytest.fixture(autouse=True)
def model_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("COMPASS_MODEL_ENCRYPTION_KEY", "agent-test-model-key")
    monkeypatch.setenv("COMPASS_AGENT_SERVICE_TOKEN", "agent-test-service-token")
    monkeypatch.setenv(
        "COMPASS_MODEL_ALLOWED_ORIGINS",
        "https://api.deepseek.com,https://api.typesafe.ai",
    )
    get_settings.cache_clear()
    secrets._fernet.cache_clear()
    yield
    get_settings.cache_clear()
    secrets._fernet.cache_clear()


@pytest.fixture
def engine() -> Iterator[Engine]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )

    # SQLite ignores foreign keys unless asked; enforce them so tests match PostgreSQL.
    @event.listens_for(engine, "connect")
    def _enforce_foreign_keys(dbapi_connection, *_args) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


def platform_context(
    purpose: Literal["manage", "test", "chat", "module_read"] = "test",
) -> TrustedContext:
    return TrustedContext(
        actor_user_id=uuid4(), scope=PlatformScope(kind="platform"), purpose=purpose
    )


def tenant_context(
    purpose: Literal["manage", "test", "chat", "module_read"] = "chat",
) -> TrustedContext:
    return TrustedContext(
        actor_user_id=uuid4(),
        scope=TenantScope(kind="tenant", tenant_id=uuid4(), workspace_id=uuid4()),
        purpose=purpose,
    )


@pytest.fixture
def request_factory():
    return execution_request


def execution_request(
    *,
    kind: str = "llm",
    provider_id: UUID | None = None,
    input_value: GenerateInput | None = None,
    budget: float = 5.0,
    api_base: str = "https://api.deepseek.com",
    sdk_model: str | None = "deepseek/deepseek-v4-pro",
    api_model: str = "deepseek-v4-pro",
    binding_version: int = 1,
) -> ExecutionRequest:
    provider = provider_id or uuid4()
    spec = ModelSpec(
        provider_id=provider,
        config_version=2,
        binding_version=binding_version,
        vendor="deepseek" if kind == "llm" else "aliyun",
        kind=kind,  # type: ignore[arg-type]
        adapter="litellm_chat" if kind == "llm" else "systemone_decision",
        api_model=api_model,
        sdk_model=sdk_model,
        api_base=api_base,
    )
    context = TrustedContext(
        actor_user_id=uuid4(), scope=PlatformScope(kind="platform"), purpose="test"
    )
    binding = ContinuationBinding(
        actor=context.actor_user_id,
        scope=context.scope,
        provider=provider,
        binding_version=binding_version,
    )
    invocation = InvocationContext(
        call_id=uuid4(), capability_mode="diagnostic", continuation_binding=binding
    )
    started = time.monotonic()
    return ExecutionRequest(
        context=invocation,
        access=ProviderAccess(spec=spec, api_key=SecretStr("sk-test")),
        input=input_value or GenerateInput(messages=[ChatMessage(role="user", content="hi")]),
        policy=CallPolicy(started_monotonic=started, deadline_monotonic=started + budget),
    )
