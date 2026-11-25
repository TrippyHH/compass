import base64
import json
from typing import Literal
from uuid import uuid4

import pytest
from compass_agent.llm.contracts import DecisionOutput, GenerationOutput
from compass_agent.llm.runtime import Runtime
from compass_agent.main import app
from compass_agent.models.service import ModelService
from compass_agent.schemas import (
    DecisionAnswer,
    OutputValidation,
    TextDeltaEvent,
)
from compass_agent.settings import get_settings
from fastapi.testclient import TestClient

pytest.importorskip("cryptography")

TOKEN = "agent-test-service-token"


class StreamingAdapter:
    def __init__(self) -> None:
        self.calls = 0

    async def invoke(self, request, session) -> GenerationOutput:
        self.calls += 1
        session.resources.mark_sent()
        if getattr(request.input, "output_format", "text") == "schema":
            text = '{"category_id":"business_channel","scene_id":"SC-02"}'
            session.progress.output = GenerationOutput(text=text)
            session.progress.validation = OutputValidation(status="valid", mode="schema")
            session.progress.finish_reason = "stop"
            session.progress.normal_eof = True
            return GenerationOutput(text=text)
        text = ""
        for chunk in ("你", "好"):
            text += chunk
            session.progress.output = GenerationOutput(text=text)
            await session.publish(TextDeltaEvent(call_id=request.context.call_id, text=chunk))
        session.progress.finish_reason = "stop"
        session.progress.normal_eof = True
        return GenerationOutput(text=text)


class DecisionAdapter:
    def __init__(self) -> None:
        self.calls = 0

    async def invoke(self, request, session) -> DecisionOutput:
        self.calls += 1
        session.resources.mark_sent()
        session.progress.finish_reason = "stop"
        session.progress.normal_eof = True
        from compass_agent.schemas import OutputValidation

        session.progress.validation = OutputValidation(status="valid", mode="decision")
        return DecisionOutput(answers={"pick": DecisionAnswer(type="noul", noul=0.7)})


@pytest.fixture
def adapters() -> tuple[StreamingAdapter, DecisionAdapter]:
    return StreamingAdapter(), DecisionAdapter()


@pytest.fixture
def client(engine, adapters):
    text, decision = adapters
    runtime = Runtime({"litellm_chat": text, "systemone_decision": decision})
    previous = app.state.model_service
    app.state.model_service = ModelService(engine, runtime)
    with TestClient(app) as test_client:
        yield test_client
    app.state.model_service = previous


def context_headers(context) -> dict[str, str]:
    raw = context.model_dump_json().encode("utf-8")
    encoded = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    return {
        "Authorization": f"Bearer {TOKEN}",
        "X-Compass-Context": encoded,
    }


def platform_headers(purpose: Literal["manage", "test", "chat", "module_read"] = "manage"):
    from compass_agent.schemas import PlatformScope, TrustedContext

    return context_headers(
        TrustedContext(
            actor_user_id=uuid4(),
            scope=PlatformScope(kind="platform"),
            purpose=purpose,
        )
    )


def tenant_headers(purpose: Literal["manage", "test", "chat", "module_read"] = "chat"):
    from compass_agent.schemas import TenantScope, TrustedContext

    return context_headers(
        TrustedContext(
            actor_user_id=uuid4(),
            scope=TenantScope(kind="tenant", tenant_id=uuid4(), workspace_id=uuid4()),
            purpose=purpose,
        )
    )


def configure_flash(monkeypatch, provider_id: str) -> None:
    monkeypatch.setenv("COMPASS_ROUTING_MODE", "flash_only")
    monkeypatch.setenv("COMPASS_ROUTING_FLASH_PROVIDER_ID", provider_id)
    get_settings.cache_clear()


def flash_payload(**overrides) -> dict:
    values = {
        "operation_id": str(uuid4()),
        "catalog_id": "deepseek/deepseek-flash",
        "api_model": "deepseek-flash",
        "alias": "Flash",
        "base_url": "https://api.deepseek.com",
        "api_key": "sk-flash",
        "enabled": True,
        "chat_visible": True,
    }
    values.update(overrides)
    return values


def provider_payload(**overrides) -> dict:
    values = {
        "operation_id": str(uuid4()),
        "catalog_id": "deepseek/deepseek-v4-pro",
        "api_model": "deepseek-v4-pro",
        "alias": "API 模型",
        "base_url": "https://api.deepseek.com",
        "api_key": "sk-api-key",
        "enabled": True,
        "chat_visible": True,
    }
    values.update(overrides)
    return values


def test_service_identity_and_context_are_required(client) -> None:
    response = client.get("/v1/models/providers")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthenticated"
    wrong = client.get(
        "/v1/models/providers",
        headers={"Authorization": "Bearer wrong-token", "X-Compass-Context": "e30"},
    )
    assert wrong.status_code == 401
    missing = client.get(
        "/v1/models/providers",
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    assert missing.status_code == 401
    tenant = client.get("/v1/models/providers", headers=tenant_headers())
    assert tenant.status_code == 403
    assert tenant.json()["error"]["code"] == "forbidden"


def test_provider_management_flow(client) -> None:
    headers = platform_headers()
    created = client.post("/v1/models/providers", headers=headers, json=provider_payload())
    assert created.status_code == 201
    saved = created.json()
    provider_id = saved["provider_id"]
    listed = client.get("/v1/models/providers", headers=headers)
    assert listed.status_code == 200
    assert listed.json()["total"] == 1
    view = client.get(f"/v1/models/providers/{provider_id}", headers=headers)
    assert view.status_code == 200
    body = view.json()
    assert body["has_api_key"] is True
    assert "sk-api-key" not in json.dumps(body)
    assert '"api_key"' not in json.dumps(body)
    assert body["chat_supported"] is True
    conflict = client.patch(
        f"/v1/models/providers/{provider_id}",
        headers=headers,
        json={
            "operation_id": str(uuid4()),
            "expected_version": 9,
            "alias": "改名",
        },
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["currentVersion"] == 1
    patched = client.patch(
        f"/v1/models/providers/{provider_id}",
        headers=headers,
        json={
            "operation_id": str(uuid4()),
            "expected_version": 1,
            "alias": "改名",
            "enabled": False,
        },
    )
    assert patched.status_code == 200
    assert patched.json()["version"] == 2
    changes = client.get(f"/v1/models/providers/{provider_id}/changes", headers=headers)
    assert changes.status_code == 200
    assert changes.json()["total"] == 2
    operation_id = saved["operation_id"]
    replay = client.get(f"/v1/models/operations/{operation_id}", headers=headers)
    assert replay.status_code == 200
    assert replay.json()["version"] == 1


def test_catalog_search_is_platform_only(client) -> None:
    response = client.get(
        "/v1/models/catalog", params={"q": "deepseek", "page_size": 10}, headers=platform_headers()
    )
    assert response.status_code == 200
    page = response.json()
    assert page["total"] > 0
    assert page["items"][0]["vendor"] == "deepseek"
    tenant = client.get("/v1/models/catalog", params={"q": "deepseek"}, headers=tenant_headers())
    assert tenant.status_code == 403


def test_available_models_for_tenant(client) -> None:
    headers = platform_headers()
    hidden = client.post(
        "/v1/models/providers",
        headers=headers,
        json=provider_payload(alias="仅管理", enabled=False),
    ).json()
    shown = client.post(
        "/v1/models/providers", headers=headers, json=provider_payload(alias="可用模型")
    ).json()
    available = client.get("/v1/models/available", headers=tenant_headers("module_read"))
    assert available.status_code == 200
    ids = [item["id"] for item in available.json()["items"]]
    assert shown["provider_id"] in ids
    assert hidden["provider_id"] not in ids


def test_chat_json_response_and_record(client, monkeypatch) -> None:
    headers = platform_headers()
    tenant = tenant_headers()
    created = client.post("/v1/models/providers", headers=headers, json=flash_payload()).json()
    configure_flash(monkeypatch, created["provider_id"])
    call_id = str(uuid4())
    response = client.post(
        "/v1/models/calls",
        headers=tenant,
        json={
            "call_id": call_id,
            "provider_id": created["provider_id"],
            "input": {
                "messages": [{"role": "user", "content": "比较一下昨天和前天的渠道汇总"}],
                "stream": False,
            },
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["intent"] == {"category_id": "business_channel", "scene_id": "SC-02"}
    assert body["features"]["difficulty"] == "L1"
    assert body["routing"]["mode"] == "fixed"
    assert body["routing"]["provider_id"] == created["provider_id"]
    assert body["routing"]["reason"] == "user_fixed_main_model"
    assert body["result"]["text"] == "你好"
    assert body["metrics"]["classifier_source"] == "flash"
    assert len(body["metrics"]["classification_calls"]) == 1
    record = client.get(f"/v1/models/calls/{call_id}", headers=tenant)
    assert record.status_code == 200
    assert record.json()["purpose"] == "chat"


def test_chat_stream_emits_start_classification_route_and_single_final(client, monkeypatch) -> None:
    headers = platform_headers()
    created = client.post("/v1/models/providers", headers=headers, json=flash_payload()).json()
    configure_flash(monkeypatch, created["provider_id"])
    call_id = str(uuid4())
    events: list[dict] = []
    with client.stream(
        "POST",
        "/v1/models/calls",
        headers=tenant_headers(),
        json={
            "call_id": call_id,
            "provider_id": created["provider_id"],
            "input": {
                "messages": [{"role": "user", "content": "比较两周渠道汇总"}],
                "stream": True,
            },
        },
    ) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        for line in response.iter_lines():
            if not line:
                continue
            assert line.startswith("data: ")
            events.append(json.loads(line[6:]))
    types = [event["type"] for event in events]
    assert types[0] == "start"
    assert "classification_status" in types
    assert "route_selected" in types
    assert types[-1] == "final"
    assert types.count("final") == 1
    deltas = [event for event in events if event["type"] == "text_delta"]
    assert "".join(event["text"] for event in deltas) == "你好"
    route = next(event for event in events if event["type"] == "route_selected")
    assert route["intent"]["scene_id"] == "SC-02"
    assert route["routing"]["provider_id"] == created["provider_id"]
    final = events[-1]
    assert final["status"] == "completed"
    assert final["call_id"] == call_id
    assert final["result"]["text"] == "你好"
    assert final["metrics"]["classifier_source"] == "flash"


def test_chat_selection_requires_exactly_one_of_provider_and_main_model(client) -> None:
    tenant = tenant_headers()
    body = {
        "call_id": str(uuid4()),
        "input": {"messages": [{"role": "user", "content": "比较两周渠道汇总"}]},
    }
    missing = client.post("/v1/models/calls", headers=tenant, json=body)
    assert missing.status_code == 422
    both = client.post(
        "/v1/models/calls",
        headers=tenant,
        json={
            **body,
            "provider_id": str(uuid4()),
            "main_model": {"mode": "fixed", "provider_id": str(uuid4())},
        },
    )
    assert both.status_code == 422
    mismatch = client.post(
        "/v1/models/calls",
        headers=tenant,
        json={**body, "main_model": {"mode": "auto", "provider_id": str(uuid4())}},
    )
    assert mismatch.status_code == 422


def test_decision_test_call(client, adapters) -> None:
    _, decision_adapter = adapters
    headers = platform_headers()
    tester = platform_headers("test")
    created = client.post(
        "/v1/models/providers",
        headers=headers,
        json=provider_payload(
            catalog_id="typesafe/jev-latest",
            api_model="jev-latest",
            alias="JEV",
            base_url="https://api.typesafe.ai/v1",
            api_key="decision-key",
            chat_visible=False,
        ),
    ).json()
    call_id = str(uuid4())
    response = client.post(
        f"/v1/models/providers/{created['provider_id']}/tests",
        headers=tester,
        json={
            "call_id": call_id,
            "input": {
                "kind": "decision",
                "state": {"sales": 12},
                "questions": {"pick": {"type": "noul", "criteria": {"true": "是", "false": "否"}}},
            },
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["kind"] == "decision"
    assert body["answers"]["pick"]["noul"] == 0.7
    assert decision_adapter.calls == 1
    record = client.get(f"/v1/models/test-calls/{call_id}", headers=tester)
    assert record.status_code == 200


def test_chat_rejects_decision_input(client) -> None:
    headers = platform_headers()
    created = client.post("/v1/models/providers", headers=headers, json=provider_payload()).json()
    response = client.post(
        "/v1/models/calls",
        headers=tenant_headers(),
        json={
            "call_id": str(uuid4()),
            "provider_id": created["provider_id"],
            "input": {
                "kind": "decision",
                "state": "x",
                "questions": {"q": {"type": "noul"}},
            },
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"
