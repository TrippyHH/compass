import base64
import json
from typing import Literal
from uuid import uuid4, uuid5

import pytest
from compass_agent.llm.contracts import DecisionOutput, GenerationOutput, RuntimeFailure
from compass_agent.llm.errors import RuntimeErrorCode
from compass_agent.llm.runtime import Runtime
from compass_agent.main import app
from compass_agent.models.errors import ModelError
from compass_agent.models.service import ModelService
from compass_agent.routing.dictionary import (
    catalog,
    categories,
    features_for,
    scenes,
    validate_intent,
)
from compass_agent.schemas import (
    DecisionAnswer,
    OutputValidation,
    PlatformScope,
    TenantScope,
    TextDeltaEvent,
    TrustedContext,
)
from compass_agent.settings import get_settings
from fastapi.testclient import TestClient

TOKEN = "agent-test-service-token"


def headers(context: TrustedContext) -> dict[str, str]:
    raw = context.model_dump_json().encode("utf-8")
    encoded = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    return {"Authorization": f"Bearer {TOKEN}", "X-Compass-Context": encoded}


def platform_headers(purpose: Literal["manage", "test"] = "manage") -> dict[str, str]:
    return headers(
        TrustedContext(actor_user_id=uuid4(), scope=PlatformScope(kind="platform"), purpose=purpose)
    )


def tenant_headers() -> dict[str, str]:
    return headers(
        TrustedContext(
            actor_user_id=uuid4(),
            scope=TenantScope(kind="tenant", tenant_id=uuid4(), workspace_id=uuid4()),
            purpose="chat",
        )
    )


def provider_payload(role: str, alias: str, **overrides) -> dict:
    values = {
        "operation_id": str(uuid4()),
        "alias": alias,
        "enabled": True,
        "chat_visible": True,
    }
    if role == "jev":
        values.update(
            catalog_id="typesafe/jev-latest",
            api_model="jev-latest",
            base_url="https://api.typesafe.ai/v1",
            api_key="jev-key",
            chat_visible=False,
        )
    elif role == "flash":
        values.update(
            catalog_id="deepseek/deepseek-flash",
            api_model="deepseek-flash",
            base_url="https://api.deepseek.com",
            api_key="flash-key",
        )
    else:
        values.update(
            catalog_id="deepseek/deepseek-v4-pro",
            api_model="deepseek-v4-pro",
            base_url="https://api.deepseek.com",
            api_key="pro-key",
        )
    values.update(overrides)
    return values


def create_provider(client: TestClient, role: str, alias: str, **overrides) -> str:
    response = client.post(
        "/v1/models/providers",
        headers=platform_headers(),
        json=provider_payload(role, alias, **overrides),
    )
    assert response.status_code == 201, response.text
    return response.json()["provider_id"]


def configure_routing(
    monkeypatch, *, mode: str, jev: str = "", flash: str = "", pro: str = ""
) -> None:
    monkeypatch.setenv("COMPASS_ROUTING_MODE", mode)
    monkeypatch.setenv("COMPASS_ROUTING_JEV_PROVIDER_ID", jev)
    monkeypatch.setenv("COMPASS_ROUTING_FLASH_PROVIDER_ID", flash)
    monkeypatch.setenv("COMPASS_ROUTING_PRO_PROVIDER_ID", pro)
    get_settings.cache_clear()


class FakeLLM:
    def __init__(self) -> None:
        self.flash_payload = '{"category_id":"business_channel","scene_id":"SC-02"}'
        self.flash_error: RuntimeErrorCode | None = None
        self.inference_error: RuntimeErrorCode | None = None
        self.answer = "这是一次受控回答。"
        self.flash_calls = 0
        self.inference_calls = 0
        self.requests = []

    async def invoke(self, request, session) -> GenerationOutput:
        session.resources.mark_sent()
        self.requests.append(request)
        if getattr(request.input, "output_format", "text") == "schema":
            self.flash_calls += 1
            if self.flash_error:
                raise RuntimeFailure(self.flash_error)
            session.progress.output = GenerationOutput(text=self.flash_payload)
            session.progress.validation = OutputValidation(status="valid", mode="schema")
            session.progress.finish_reason = "stop"
            session.progress.normal_eof = True
            return GenerationOutput(text=self.flash_payload)
        self.inference_calls += 1
        if self.inference_error:
            raise RuntimeFailure(self.inference_error)
        if getattr(request.input, "stream", False):
            await session.publish(TextDeltaEvent(call_id=request.context.call_id, text=self.answer))
        session.progress.output = GenerationOutput(text=self.answer)
        session.progress.finish_reason = "stop"
        session.progress.normal_eof = True
        return GenerationOutput(text=self.answer)


class FakeJev:
    def __init__(self) -> None:
        self.answers: dict[str, DecisionAnswer] = {}
        self.error: RuntimeErrorCode | None = None
        self.calls = 0

    async def invoke(self, request, session) -> DecisionOutput:
        session.resources.mark_sent()
        self.calls += 1
        if self.error:
            raise RuntimeFailure(self.error)
        session.progress.finish_reason = "stop"
        session.progress.normal_eof = True
        session.progress.validation = OutputValidation(status="valid", mode="decision")
        return DecisionOutput(answers=self.answers)


def jev_answers(scene_id: str, probability: float = 0.95, other: float = 0.05) -> dict:
    row = scenes()[scene_id]
    category_id = row["category_id"]
    other_category = next(key for key in categories() if key != category_id)
    other_scene = next(key for key in scenes() if key != scene_id)
    return {
        "category": DecisionAnswer(
            type="choice",
            choice=category_id,
            probabilities={category_id: probability, other_category: other},
        ),
        "scene": DecisionAnswer(
            type="choice",
            choice=scene_id,
            probabilities={scene_id: probability, other_scene: other},
        ),
    }


@pytest.fixture
def routed(engine):
    llm = FakeLLM()
    jev = FakeJev()
    runtime = Runtime({"litellm_chat": llm, "systemone_decision": jev})
    service = ModelService(engine, runtime)
    previous = app.state.model_service
    app.state.model_service = service
    with TestClient(app) as client:
        yield client, llm, jev, service
    app.state.model_service = previous


def roles(client: TestClient) -> dict[str, str]:
    return {
        "jev": create_provider(client, "jev", "JEV"),
        "flash": create_provider(client, "flash", "Flash"),
        "pro": create_provider(client, "pro", "Pro"),
    }


def call_body(call_id: str, selection: dict, content: str = "比较这两天的渠道汇总") -> dict:
    return {
        "call_id": call_id,
        "main_model": selection,
        "input": {"messages": [{"role": "user", "content": content}], "stream": False},
    }


def post_call(client: TestClient, body: dict):
    return client.post("/v1/models/calls", headers=tenant_headers(), json=body)


def test_dictionary_is_deterministic_and_enforces_parent() -> None:
    version, rows = catalog()
    assert len(rows) == 40
    assert len(categories()) == 10
    assert version.startswith("sha256:")
    intent = validate_intent({"category_id": "business_channel", "scene_id": "SC-01"})
    first = features_for(intent)
    second = features_for(intent)
    assert first == second
    assert first.task_type == "metric_lookup"
    assert first.difficulty == "L1"
    assert first.response_mode == "answer"
    assert first.required_capabilities == []
    assert rows["SC-39"]["response_mode"] == "refuse"
    assert rows["SC-03"]["required_capabilities"] == ["thinking"]
    with pytest.raises(ModelError) as mismatch:
        validate_intent({"category_id": "marketing_member", "scene_id": "SC-01"})
    assert mismatch.value.code == "intent_invalid"
    with pytest.raises(ModelError):
        validate_intent({"category_id": "business_channel", "scene_id": "SC-99"})


@pytest.mark.parametrize("scene_id", sorted(scenes()))
def test_every_registered_scene_has_valid_features(scene_id: str) -> None:
    row = scenes()[scene_id]
    intent = validate_intent({"category_id": row["category_id"], "scene_id": scene_id})
    features = features_for(intent)
    assert features.task_type
    assert features.difficulty in {"L1", "L2", "L3", "undetermined"}
    assert features.response_mode in {"answer", "clarify", "refuse", "handoff"}
    assert all(
        capability in {"thinking", "schema_output", "tools", "stream"}
        for capability in features.required_capabilities
    )


def test_jev_l1_auto_selects_flash(routed, monkeypatch) -> None:
    client, llm, jev, _ = routed
    ids = roles(client)
    configure_routing(
        monkeypatch, mode="jev_with_flash", jev=ids["jev"], flash=ids["flash"], pro=ids["pro"]
    )
    jev.answers = jev_answers("SC-02")
    call_id = str(uuid4())
    response = post_call(client, call_body(call_id, {"mode": "auto"}))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "completed"
    assert body["intent"] == {"category_id": "business_channel", "scene_id": "SC-02"}
    assert body["features"]["difficulty"] == "L1"
    assert body["routing"]["mode"] == "auto"
    assert body["routing"]["provider_id"] == ids["flash"]
    assert body["routing"]["reason"] == "difficulty_L1"
    assert body["routing"]["dictionary_version"].startswith("sha256:")
    assert body["routing"]["routing_version"] == "lesson12-flow-1"
    assert body["result"]["text"] == "这是一次受控回答。"
    assert body["metrics"]["classifier_source"] == "jev"
    assert body["metrics"]["fallback_reason"] is None
    assert len(body["metrics"]["classification_calls"]) == 1
    assert body["metrics"]["classification_calls"][0]["status"] == "completed"
    assert body["metrics"]["classification_calls"][0]["usage"]["input_tokens"] is None
    assert body["metrics"]["usage_complete"] is False
    assert jev.calls == 1
    assert llm.flash_calls == 0
    assert llm.inference_calls == 1


def test_jev_l3_auto_selects_pro(routed, monkeypatch) -> None:
    client, llm, jev, _ = routed
    ids = roles(client)
    configure_routing(
        monkeypatch, mode="jev_with_flash", jev=ids["jev"], flash=ids["flash"], pro=ids["pro"]
    )
    jev.answers = jev_answers("SC-03")
    body = post_call(client, call_body(str(uuid4()), {"mode": "auto"}, "四周销售和流量下降")).json()
    assert body["status"] == "completed"
    assert body["features"]["difficulty"] == "L3"
    assert body["features"]["required_capabilities"] == ["thinking"]
    assert body["routing"]["provider_id"] == ids["pro"]
    assert body["routing"]["reason"] == "difficulty_L3"
    assert llm.flash_calls == 0
    assert llm.inference_calls == 1


def test_fixed_pro_keeps_l1_features(routed, monkeypatch) -> None:
    client, llm, jev, _ = routed
    ids = roles(client)
    configure_routing(
        monkeypatch, mode="jev_with_flash", jev=ids["jev"], flash=ids["flash"], pro=ids["pro"]
    )
    jev.answers = jev_answers("SC-02")
    body = post_call(
        client,
        call_body(str(uuid4()), {"mode": "fixed", "provider_id": ids["pro"]}),
    ).json()
    assert body["status"] == "completed"
    assert body["features"]["difficulty"] == "L1"
    assert body["routing"]["mode"] == "fixed"
    assert body["routing"]["provider_id"] == ids["pro"]
    assert body["routing"]["reason"] == "user_fixed_main_model"
    assert body["metrics"]["classifier_source"] == "jev"
    assert llm.inference_calls == 1


def test_low_confidence_jev_falls_back_to_flash_once(routed, monkeypatch) -> None:
    client, llm, jev, _ = routed
    ids = roles(client)
    configure_routing(
        monkeypatch, mode="jev_with_flash", jev=ids["jev"], flash=ids["flash"], pro=ids["pro"]
    )
    jev.answers = jev_answers("SC-02", probability=0.4, other=0.35)
    llm.flash_payload = '{"category_id":"business_channel","scene_id":"SC-03"}'
    body = post_call(client, call_body(str(uuid4()), {"mode": "auto"}, "销售下降了")).json()
    assert body["status"] == "completed"
    assert body["intent"]["scene_id"] == "SC-03"
    assert body["metrics"]["classifier_source"] == "flash"
    assert body["metrics"]["fallback_reason"] == "jev_unavailable_or_uncertain"
    assert len(body["metrics"]["classification_calls"]) == 2
    assert [item["status"] for item in body["metrics"]["classification_calls"]] == [
        "completed",
        "completed",
    ]
    assert jev.calls == 1
    assert llm.flash_calls == 1
    assert llm.inference_calls == 1
    assert body["routing"]["provider_id"] == ids["pro"]


def test_flash_extra_field_is_rejected_before_inference(routed, monkeypatch) -> None:
    client, llm, jev, _ = routed
    ids = roles(client)
    configure_routing(
        monkeypatch, mode="jev_with_flash", jev=ids["jev"], flash=ids["flash"], pro=ids["pro"]
    )
    jev.error = "model_unavailable_upstream"
    llm.flash_payload = '{"category_id":"business_channel","scene_id":"SC-02","difficulty":"L1"}'
    body = post_call(client, call_body(str(uuid4()), {"mode": "auto"})).json()
    assert body["status"] == "failed"
    assert body["error_code"] == "intent_invalid"
    assert body["intent"] is None
    assert body["routing"] is None
    assert body["result"] is None
    assert len(body["metrics"]["classification_calls"]) == 2
    assert llm.inference_calls == 0


def test_both_classifiers_fail_stops_before_inference(routed, monkeypatch) -> None:
    client, llm, jev, _ = routed
    ids = roles(client)
    configure_routing(
        monkeypatch, mode="jev_with_flash", jev=ids["jev"], flash=ids["flash"], pro=ids["pro"]
    )
    jev.error = "model_unavailable_upstream"
    llm.flash_error = "provider_protocol_error"
    body = post_call(client, call_body(str(uuid4()), {"mode": "auto"})).json()
    assert body["status"] == "failed"
    assert body["error_code"] == "intent_unavailable"
    assert body["result"] is None
    assert len(body["metrics"]["classification_calls"]) == 2
    assert jev.calls == 1
    assert llm.flash_calls == 1
    assert llm.inference_calls == 0


def test_sc_39_uses_flash_prompt_even_when_pro_is_fixed(routed, monkeypatch) -> None:
    client, llm, jev, _ = routed
    ids = roles(client)
    configure_routing(
        monkeypatch, mode="jev_with_flash", jev=ids["jev"], flash=ids["flash"], pro=ids["pro"]
    )
    jev.answers = jev_answers("SC-39")
    body = post_call(
        client,
        call_body(str(uuid4()), {"mode": "fixed", "provider_id": ids["pro"]}, "讲个笑话"),
    ).json()
    assert body["status"] == "completed"
    assert body["intent"] == {"category_id": "input_boundary", "scene_id": "SC-39"}
    assert body["features"]["response_mode"] == "refuse"
    assert body["routing"]["mode"] == "fixed"
    assert body["routing"]["provider_id"] == ids["flash"]
    assert body["routing"]["reason"] == "off_topic_flash"
    sent = llm.requests[-1].input
    assert sent.max_tokens == 512
    assert sent.output_format == "text"
    assert "婉拒" in (sent.messages[0].content or "")
    assert "电商经营数据分析助手" in (sent.messages[0].content or "")
    assert llm.inference_calls == 1


def test_fixed_model_missing_required_capability_is_rejected(routed, monkeypatch) -> None:
    client, llm, jev, _ = routed
    ids = roles(client)
    basic = create_provider(
        client,
        "pro",
        "基础模型",
        catalog_id="acme/basic-model",
        api_model="basic-model",
        base_url="",
        api_key="basic-key",
    )
    configure_routing(
        monkeypatch, mode="jev_with_flash", jev=ids["jev"], flash=ids["flash"], pro=ids["pro"]
    )
    jev.answers = jev_answers("SC-03")
    body = post_call(
        client,
        call_body(str(uuid4()), {"mode": "fixed", "provider_id": basic}, "多维诊断"),
    ).json()
    assert body["status"] == "failed"
    assert body["error_code"] == "capability_not_supported"
    assert body["routing"] is None
    assert body["result"] is None
    assert llm.inference_calls == 0


def test_missing_flash_binding_returns_route_error(routed, monkeypatch) -> None:
    client, llm, _, _ = routed
    configure_routing(monkeypatch, mode="flash_only")
    body = post_call(client, call_body(str(uuid4()), {"mode": "auto"})).json()
    assert body["status"] == "failed"
    assert body["error_code"] == "route_binding_missing"
    assert body["result"] is None
    assert llm.inference_calls == 0


def test_classification_and_inference_use_derived_call_ids(routed, monkeypatch) -> None:
    client, llm, jev, service = routed
    ids = roles(client)
    configure_routing(
        monkeypatch, mode="jev_with_flash", jev=ids["jev"], flash=ids["flash"], pro=ids["pro"]
    )
    jev.error = "model_unavailable_upstream"
    llm.flash_payload = '{"category_id":"business_channel","scene_id":"SC-02"}'
    call_id = uuid4()
    body = post_call(client, call_body(str(call_id), {"mode": "auto"})).json()
    assert body["status"] == "completed"
    flash_call = service.repository.call(uuid5(call_id, "intent_flash"))
    assert flash_call is not None
    assert flash_call.purpose == "chat"
    assert flash_call.status == "completed"
    main_call = service.repository.call(call_id)
    assert main_call is not None
    assert main_call.purpose == "chat"
    assert main_call.status == "completed"


def test_inference_failure_is_not_retried_or_replaced(routed, monkeypatch) -> None:
    client, llm, jev, _ = routed
    ids = roles(client)
    configure_routing(
        monkeypatch, mode="jev_with_flash", jev=ids["jev"], flash=ids["flash"], pro=ids["pro"]
    )
    jev.answers = jev_answers("SC-02")
    llm.inference_error = "model_timeout"
    body = post_call(client, call_body(str(uuid4()), {"mode": "auto"})).json()
    assert body["status"] == "failed"
    assert body["error_code"] == "model_timeout"
    assert body["routing"]["provider_id"] == ids["flash"]
    assert body["result"]["status"] == "failed"
    assert body["result"]["upstream_outcome_unknown"] is True
    assert len(body["metrics"]["classification_calls"]) == 1
    assert llm.inference_calls == 1
    assert llm.flash_calls == 0


def test_stream_reports_classification_then_route_then_final(routed, monkeypatch) -> None:
    client, llm, jev, _ = routed
    ids = roles(client)
    configure_routing(
        monkeypatch, mode="jev_with_flash", jev=ids["jev"], flash=ids["flash"], pro=ids["pro"]
    )
    jev.answers = jev_answers("SC-03")
    call_id = str(uuid4())
    events: list[dict] = []
    with client.stream(
        "POST",
        "/v1/models/calls",
        headers=tenant_headers(),
        json={
            "call_id": call_id,
            "main_model": {"mode": "auto"},
            "input": {"messages": [{"role": "user", "content": "多维诊断"}], "stream": True},
        },
    ) as response:
        assert response.status_code == 200
        for line in response.iter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    types = [event["type"] for event in events]
    assert types[0] == "start"
    assert types[1] == "classification_status"
    assert types[1:3] == ["classification_status", "route_selected"]
    assert "text_delta" in types
    assert types.count("final") == 1
    assert types[-1] == "final"
    assert "provider_id" not in events[0]
    route = events[2]
    assert route["routing"]["provider_id"] == ids["pro"]
    final = events[-1]
    assert final["status"] == "completed"
    assert final["intent"]["scene_id"] == "SC-03"
    assert final["result"]["text"] == "这是一次受控回答。"
    assert final["metrics"]["classifier_source"] == "jev"


def test_undetermined_scene_auto_uses_flash_clarify_response(routed, monkeypatch) -> None:
    client, llm, jev, _ = routed
    ids = roles(client)
    configure_routing(
        monkeypatch, mode="jev_with_flash", jev=ids["jev"], flash=ids["flash"], pro=ids["pro"]
    )
    jev.answers = jev_answers("SC-33")
    body = post_call(client, call_body(str(uuid4()), {"mode": "auto"}, "帮我看看")).json()
    assert body["status"] == "completed"
    assert body["features"]["difficulty"] == "undetermined"
    assert body["features"]["response_mode"] == "clarify"
    assert body["routing"]["provider_id"] == ids["flash"]
    assert body["routing"]["reason"] == "difficulty_undetermined"
    assert llm.inference_calls == 1


def test_flash_parent_mismatch_is_rejected(routed, monkeypatch) -> None:
    client, llm, jev, _ = routed
    ids = roles(client)
    configure_routing(
        monkeypatch, mode="jev_with_flash", jev=ids["jev"], flash=ids["flash"], pro=ids["pro"]
    )
    jev.error = "model_unavailable_upstream"
    llm.flash_payload = '{"category_id":"marketing_member","scene_id":"SC-01"}'
    body = post_call(client, call_body(str(uuid4()), {"mode": "auto"})).json()
    assert body["status"] == "failed"
    assert body["error_code"] == "intent_invalid"
    assert body["intent"] is None
    assert llm.inference_calls == 0


def test_unknown_fixed_provider_is_rejected_without_outbound(routed, monkeypatch) -> None:
    client, llm, jev, _ = routed
    ids = roles(client)
    configure_routing(
        monkeypatch, mode="jev_with_flash", jev=ids["jev"], flash=ids["flash"], pro=ids["pro"]
    )
    jev.answers = jev_answers("SC-02")
    body = post_call(
        client,
        call_body(str(uuid4()), {"mode": "fixed", "provider_id": str(uuid4())}),
    ).json()
    assert body["status"] == "failed"
    assert body["error_code"] == "model_unavailable"
    assert body["routing"] is None
    assert llm.inference_calls == 0


def test_same_scene_features_stay_deterministic_across_rounds(routed, monkeypatch) -> None:
    client, llm, jev, _ = routed
    ids = roles(client)
    configure_routing(
        monkeypatch, mode="jev_with_flash", jev=ids["jev"], flash=ids["flash"], pro=ids["pro"]
    )
    jev.answers = jev_answers("SC-01")
    first = post_call(client, call_body(str(uuid4()), {"mode": "auto"}, "本月销售额是多少")).json()
    second = post_call(client, call_body(str(uuid4()), {"mode": "auto"}, "本月销售额是多少")).json()
    assert first["intent"] == second["intent"]
    assert first["features"] == second["features"]
    assert first["routing"]["dictionary_version"] == second["routing"]["dictionary_version"]
    assert first["routing"]["provider_id"] == second["routing"]["provider_id"] == ids["flash"]
    assert llm.inference_calls == 2
