import asyncio
import os
from typing import Literal
from uuid import uuid4

import pytest
from compass_agent.database import Base
from compass_agent.llm.contracts import GenerationOutput
from compass_agent.llm.runtime import Runtime
from compass_agent.models.service import ModelService
from compass_agent.schemas import (
    ChatCallInput,
    ChatMessage,
    GenerateInput,
    ProviderCreate,
    TextDeltaEvent,
)
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

pytest.importorskip("cryptography")

URL = os.environ.get("COMPASS_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not URL, reason="COMPASS_TEST_DATABASE_URL is required for PostgreSQL integration"
)


class Adapter:
    async def invoke(self, request, session) -> GenerationOutput:
        session.resources.mark_sent()
        session.progress.finish_reason = "stop"
        session.progress.normal_eof = True
        await session.publish(TextDeltaEvent(call_id=request.context.call_id, text="ok"))
        output = GenerationOutput(text="ok")
        session.progress.output = output
        return output


def test_postgres_configuration_constraints_and_call_metadata() -> None:
    engine = create_engine(str(URL))
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    service = ModelService(
        engine, Runtime({"litellm_chat": Adapter(), "systemone_decision": Adapter()})
    )
    platform = _context("platform", "manage")
    tenant = _context("tenant", "chat")

    async def scenario():
        saved = await service.create_provider(
            ProviderCreate(
                operation_id=uuid4(),
                catalog_id="deepseek/deepseek-v4-pro",
                api_model="deepseek-v4-pro",
                alias="PG 模型",
                base_url="https://api.deepseek.com",
                api_key="sk-pg",
                enabled=True,
                chat_visible=True,
            ),
            platform,
        )
        payload = ChatCallInput(
            call_id=uuid4(),
            provider_id=saved.provider_id,
            input=GenerateInput(messages=[ChatMessage(role="user", content="hi")], stream=True),
        )
        return await service.chat_call(payload, tenant), saved

    response, saved = asyncio.run(scenario())
    assert response.status == "completed"
    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT status, config_version, binding_version, requested_model, usage "
                "FROM model_call WHERE call_id = :call_id"
            ),
            {"call_id": response.call_id},
        ).one()
        assert row.status == "completed"
        assert row.requested_model == "deepseek-v4-pro"
        assert "ok" not in str(row.usage)
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO model_provider (id, catalog_id, vendor, api_model, alias, "
                    "kind, adapter, base_url, api_key_ciphertext, enabled, chat_visible, "
                    "version, binding_version) VALUES (:id, 'x', 'aliyun', 'x', 'bad', "
                    "'decision', 'systemone_decision', 'https://x', '', false, true, 1, 1)"
                ),
                {"id": str(uuid4())},
            )
    engine.dispose()


def _context(kind: str, purpose: Literal["manage", "test", "chat", "module_read"]):
    from compass_agent.schemas import (
        PlatformScope,
        TenantScope,
        TrustedContext,
    )

    scope = (
        PlatformScope(kind="platform")
        if kind == "platform"
        else TenantScope(kind="tenant", tenant_id=uuid4(), workspace_id=uuid4())
    )
    return TrustedContext(actor_user_id=uuid4(), scope=scope, purpose=purpose)
