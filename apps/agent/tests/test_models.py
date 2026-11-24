from uuid import uuid4

import pytest
from compass_agent.llm import catalog
from compass_agent.llm.capabilities import resolve, validate_input
from compass_agent.llm.contracts import (
    ContinuationBinding,
    ModelSpec,
    RuntimeFailure,
)
from compass_agent.models import secrets, validation
from compass_agent.models.errors import ModelError
from compass_agent.schemas import (
    ChatMessage,
    DecisionInput,
    DecisionQuestion,
    GenerateInput,
    PlatformScope,
    ToolCall,
    ToolCallFunction,
    ToolDefinition,
    ToolFunction,
)

pytest.importorskip("cryptography")


def spec(**overrides) -> ModelSpec:
    values = {
        "provider_id": uuid4(),
        "config_version": 2,
        "binding_version": 1,
        "vendor": "deepseek",
        "kind": "llm",
        "adapter": "litellm_chat",
        "api_model": "deepseek-v4-pro",
        "sdk_model": "deepseek/deepseek-v4-pro",
        "api_base": "https://api.deepseek.com",
    }
    values.update(overrides)
    return ModelSpec(**values)


def test_catalog_covers_sdk_models_and_decision_presets() -> None:
    page = catalog.page(q="deepseek", page_size=100)
    ids = {item.id for item in page.items}
    assert "deepseek/deepseek-v4-pro" in ids
    assert catalog.find_record("deepseek/deepseek-v4-pro") is not None
    assert page.sdk_version == "1.106.6"
    assert all(item.adapter_supported for item in page.items)
    assert page.items[0].vendor == "deepseek"
    decision = catalog.page(vendor="aliyun", page_size=100)
    assert any(item.id == "aliyun/decision-model-preview" for item in decision.items)
    jev = catalog.page(q="jev", page_size=100)
    assert any(item.id == "typesafe/jev-latest" for item in jev.items)
    assert all(item.kind == "decision" for item in jev.items if item.source == "systemone")


def test_manual_identifier_classification() -> None:
    kind, adapter, vendor, api_model = catalog.classify_manual("systemone/custom-v2")
    assert (kind, adapter, vendor, api_model) == (
        "decision",
        "systemone_decision",
        "systemone",
        "custom-v2",
    )
    kind, adapter, vendor, api_model = catalog.classify_manual("openai/gpt-4o")
    assert (kind, adapter, vendor, api_model) == ("llm", "litellm_chat", "openai", "gpt-4o")
    with pytest.raises(ValueError):
        catalog.classify_manual("no-prefix")


def test_capabilities_resolve_and_reject_invalid_effort() -> None:
    capabilities = resolve(spec())
    assert capabilities.tools.state == "supported"
    assert capabilities.thinking.state == "supported"
    assert capabilities.reasoning_efforts == ["low", "medium", "high", "max"]

    with pytest.raises(RuntimeFailure) as unsupported:
        validate_input(
            spec(
                vendor="anthropic",
                sdk_model="anthropic/claude-sonnet-4-5",
                api_model="claude-sonnet-4-5",
                api_base="https://api.anthropic.com",
            ),
            GenerateInput(
                messages=[ChatMessage(role="user", content="hi")],
                thinking=True,
                reasoning_effort="max",
            ),
            "diagnostic",
        )
    assert unsupported.value.error.code == "capability_not_supported"


def test_decision_input_is_rejected_for_generation_model() -> None:
    with pytest.raises(RuntimeFailure) as captured:
        validate_input(
            spec(),
            DecisionInput(
                state="x",
                questions={"q": DecisionQuestion(type="noul")},
            ),
            "diagnostic",
        )
    assert captured.value.error.code == "validation_failed"


def test_tool_and_schema_bytes_are_bounded() -> None:
    huge_schema = {"type": "object", "properties": {"x": {"enum": ["a" * 20_000]}}}
    with pytest.raises(RuntimeFailure) as captured:
        validate_input(
            spec(),
            GenerateInput(
                messages=[ChatMessage(role="user", content="hi")],
                output_format="schema",
                output_schema=huge_schema,
            ),
            "diagnostic",
        )
    assert captured.value.error.code == "validation_failed"


def test_verified_mode_rejects_unknown_capability() -> None:
    unknown = spec(sdk_model="acme/unknown-model", vendor="acme", api_model="unknown-model")
    with pytest.raises(RuntimeFailure):
        validate_input(
            unknown,
            GenerateInput(
                messages=[ChatMessage(role="user", content="hi")],
                tools=[
                    ToolDefinition(
                        function=ToolFunction(
                            name="lookup", parameters={"type": "object", "properties": {}}
                        )
                    )
                ],
            ),
            "verified",
        )


def test_provider_secret_roundtrip_and_binding() -> None:
    provider_id = str(uuid4())
    ciphertext = secrets.seal_provider_secret(
        provider_id, "sk-secret", {"api_version": "2024-05-01"}
    )
    opened = secrets.open_provider_secret(provider_id, ciphertext)
    assert opened.api_key == "sk-secret"
    assert opened.connection_params == {"api_version": "2024-05-01"}
    with pytest.raises(secrets.SecretInvalid):
        secrets.open_provider_secret(str(uuid4()), ciphertext)
    with pytest.raises(secrets.SecretInvalid):
        secrets.open_provider_secret(provider_id, ciphertext[:-4] + "AAAA")
    empty = secrets.open_provider_secret(provider_id, "")
    assert empty.api_key == ""


def test_continuation_codec_roundtrip_and_binding() -> None:
    codec = secrets.ModelContinuationCodec()
    binding = ContinuationBinding(
        actor=uuid4(),
        scope=PlatformScope(kind="platform"),
        provider=uuid4(),
        binding_version=2,
    )
    from compass_agent.llm.continuation import seal
    from compass_agent.schemas import ChatMessage

    token = seal(
        codec,
        binding=binding,
        messages=[ChatMessage(role="user", content="hi")],
        private_assistants={"1": {"reasoning_content": "private"}},
        allowed_tool_call_ids=["call_1"],
    )
    payload = codec.open(token)
    assert payload.binding == binding
    assert payload.private_assistants == {"1": {"reasoning_content": "private"}}
    with pytest.raises(RuntimeFailure) as captured:
        codec.open(token + "x")
    assert captured.value.error.code == "continuation_invalid"


def test_base_url_rules() -> None:
    assert validation.validate_base_url("https://api.deepseek.com/") == ("https://api.deepseek.com")
    assert validation.validate_base_url("http://localhost:8080/v1") == ("http://localhost:8080/v1")
    for value in (
        "http://api.deepseek.com",
        "https://user:pass@api.deepseek.com",
        "https://api.deepseek.com/?x=1",
        "https://evil.example.com",
    ):
        with pytest.raises(ModelError):
            validation.validate_base_url(value)
    with pytest.raises(ModelError):
        validation.validate_base_url("", required=True)


def test_connection_parameter_validation() -> None:
    assert validation.validate_connection_params(None) is None
    assert validation.validate_connection_params({}) == {}
    with pytest.raises(ModelError):
        validation.validate_connection_params({"api-key": "x"})
    with pytest.raises(ModelError):
        validation.validate_connection_params({"messages": []})
    with pytest.raises(ModelError):
        validation.validate_connection_params({"payload": "x" * 20_000})


def test_alias_and_model_name_are_trimmed_and_bounded() -> None:
    assert validation.validate_alias("  经营分析  ") == "经营分析"
    with pytest.raises(ModelError):
        validation.validate_alias("   ")
    with pytest.raises(ModelError):
        validation.validate_api_model("x" * 201)


def test_continuation_supports_multiple_tool_rounds() -> None:
    from compass_agent.llm.continuation import restore, seal

    codec = secrets.ModelContinuationCodec()
    binding = ContinuationBinding(
        actor=uuid4(),
        scope=PlatformScope(kind="platform"),
        provider=uuid4(),
        binding_version=1,
    )
    assistant_one = ChatMessage(
        role="assistant",
        content=None,
        tool_calls=[
            ToolCall(id="call_1", function=ToolCallFunction(name="lookup", arguments="{}"))
        ],
    )
    tool_one = ChatMessage(role="tool", tool_call_id="call_1", content="ok")
    history_one = [ChatMessage(role="user", content="hi"), assistant_one]
    first_token = seal(
        codec,
        binding=binding,
        messages=history_one,
        private_assistants={"1": {"reasoning_content": "private-one"}},
        allowed_tool_call_ids=["call_1"],
    )
    assert restore(codec, first_token, binding=binding, messages=[*history_one, tool_one]) == {
        "1": {"reasoning_content": "private-one"}
    }

    assistant_two = ChatMessage(
        role="assistant",
        content=None,
        tool_calls=[
            ToolCall(id="call_2", function=ToolCallFunction(name="lookup", arguments="{}"))
        ],
    )
    tool_two = ChatMessage(role="tool", tool_call_id="call_2", content="ok")
    history_two = [*history_one, tool_one, assistant_two]
    second_token = seal(
        codec,
        binding=binding,
        messages=history_two,
        private_assistants={"3": {"reasoning_content": "private-two"}},
        allowed_tool_call_ids=["call_2"],
    )
    assert restore(codec, second_token, binding=binding, messages=[*history_two, tool_two]) == {
        "3": {"reasoning_content": "private-two"}
    }

    tampered = [
        ChatMessage(role="user", content="changed"),
        *history_two[1:],
        tool_two,
    ]
    with pytest.raises(RuntimeFailure):
        restore(codec, second_token, binding=binding, messages=tampered)
