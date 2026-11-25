import { describe, expect, it } from "vitest";
import {
  assertFixedRoute,
  parseCallResponse,
  parseModelEvent,
  parseProviderView,
  parseRoutedCallResponse,
  parseRoutedModelEvent,
} from "@/lib/model-contracts";
import { chatCallRequest, normalizeChatCall, providerCreate } from "@/contracts/models";

const capabilities = {
  sync: { state: "supported", reason: "" },
  stream: { state: "supported", reason: "" },
  tools: { state: "supported", reason: "" },
  thinking: { state: "supported", reason: "" },
  json_output: { state: "supported", reason: "" },
  schema_output: { state: "supported", reason: "" },
  reasoning_efforts: ["low", "medium", "high", "max"],
};

const provider = {
  id: "6a1f8b1c-7f18-4b7e-9a2f-1f2b3c4d5e6f",
  catalog_id: "deepseek/deepseek-v4-pro",
  vendor: "deepseek",
  api_model: "deepseek-v4-pro",
  alias: "经营分析",
  kind: "llm",
  base_url: "https://api.deepseek.com",
  has_api_key: true,
  has_connection_params: false,
  enabled: true,
  chat_visible: true,
  chat_supported: true,
  version: 1,
  binding_version: 1,
  capabilities,
  latest_test: null,
  created_at: "2026-11-22T10:00:00Z",
  updated_at: "2026-11-22T10:00:00Z",
};

describe("model response contracts", () => {
  it("accepts a provider view with capabilities but no secret material", () => {
    const parsed = parseProviderView(provider);
    expect(parsed.alias).toBe("经营分析");
    expect(parsed.has_api_key).toBe(true);
    expect(parsed).not.toHaveProperty("api_key");
  });

  it("rejects a provider view with an unknown capability state", () => {
    expect(() =>
      parseProviderView({
        ...provider,
        capabilities: { ...capabilities, tools: { state: "maybe", reason: "" } },
      }),
    ).toThrow();
  });

  it("accepts a call response and rejects a missing status", () => {
    const response = {
      call_id: provider.id,
      provider_id: provider.id,
      config_version: 1,
      status: "completed",
      duration_ms: 12,
      usage: { input_tokens: 3, output_tokens: 4, reasoning_tokens: null },
      error_code: null,
      upstream_outcome_unknown: false,
      requested_model: "deepseek-v4-pro",
      response_model: "deepseek-v4-pro",
      upstream_request_id: null,
      finish_reason: "stop",
      validation: { status: "not_requested", mode: "text", paths: [] },
      kind: "generate",
      text: "你好",
      tool_calls: [],
      answers: null,
      continuation_token: null,
    };
    expect(parseCallResponse(response).text).toBe("你好");
    expect(() => parseCallResponse({ ...response, status: "unknown" })).toThrow();
  });

  it("validates stream events and their call id", () => {
    const event = parseModelEvent({
      type: "text_delta",
      call_id: provider.id,
      text: "你好",
    });
    expect(event.type).toBe("text_delta");
    expect(() =>
      parseModelEvent({ type: "text_delta", call_id: "not-a-uuid", text: "x" }),
    ).toThrow();
  });

  it("accepts a routed response with separated intent, features, routing and metrics", () => {
    const routed = {
      call_id: provider.id,
      status: "completed",
      error_code: null,
      intent: { category_id: "business_channel", scene_id: "SC-02" },
      features: {
        task_type: "metric_comparison",
        difficulty: "L1",
        response_mode: "answer",
        required_capabilities: [],
      },
      routing: {
        mode: "auto",
        provider_id: provider.id,
        model: "deepseek-flash",
        reason: "difficulty_L1",
        dictionary_version: "sha256:abc",
        routing_version: "lesson12-flow-1",
      },
      result: {
        call_id: provider.id,
        provider_id: provider.id,
        config_version: 1,
        status: "completed",
        duration_ms: 12,
        usage: { input_tokens: 3, output_tokens: 4, reasoning_tokens: null },
        error_code: null,
        upstream_outcome_unknown: false,
        requested_model: "deepseek-flash",
        response_model: "deepseek-flash",
        upstream_request_id: null,
        finish_reason: "stop",
        validation: { status: "not_requested", mode: "text", paths: [] },
        kind: "generate",
        text: "你好",
        tool_calls: [],
        answers: null,
        continuation_token: null,
      },
      metrics: {
        classifier_source: "jev",
        fallback_reason: null,
        classification_calls: [],
        duration_ms: 40,
        usage_complete: true,
      },
    };
    const parsed = parseRoutedCallResponse(routed);
    expect(parsed.intent?.scene_id).toBe("SC-02");
    expect(parsed.features?.difficulty).toBe("L1");
    expect(parsed.routing?.reason).toBe("difficulty_L1");
    expect(parsed.result?.text).toBe("你好");
    expect(() =>
      parseRoutedCallResponse({
        ...routed,
        intent: { ...routed.intent, difficulty: "L1" },
      }),
    ).toThrow();
  });

  it("validates routed stream events including classification and route selection", () => {
    const route = parseRoutedModelEvent({
      type: "route_selected",
      call_id: provider.id,
      intent: { category_id: "business_channel", scene_id: "SC-02" },
      features: {
        task_type: "metric_comparison",
        difficulty: "L1",
        response_mode: "answer",
        required_capabilities: [],
      },
      routing: {
        mode: "auto",
        provider_id: provider.id,
        model: "deepseek-flash",
        reason: "difficulty_L1",
        dictionary_version: "sha256:abc",
        routing_version: "lesson12-flow-1",
      },
    });
    expect(route.type).toBe("route_selected");
    expect(
      parseRoutedModelEvent({ type: "classification_status", call_id: provider.id, stage: "flash" })
        .type,
    ).toBe("classification_status");
    expect(() =>
      parseRoutedModelEvent({
        type: "start",
        call_id: provider.id,
        provider_id: provider.id,
      }),
    ).toThrow();
  });

  it("normalises a legacy provider_id into fixed and keeps auto provider-free", () => {
    const base = {
      call_id: provider.id,
      input: { messages: [{ role: "user", content: "比较渠道汇总" }] },
    };
    const legacy = normalizeChatCall(chatCallRequest.parse({ ...base, provider_id: provider.id }));
    expect(legacy.main_model).toEqual({ mode: "fixed", provider_id: provider.id });
    const auto = normalizeChatCall(
      chatCallRequest.parse({ ...base, main_model: { mode: "auto" } }),
    );
    expect(auto.main_model.mode).toBe("auto");
    expect(auto.main_model.provider_id).toBeUndefined();
    expect(() => chatCallRequest.parse(base)).toThrow();
    expect(() =>
      chatCallRequest.parse({
        ...base,
        provider_id: provider.id,
        main_model: { mode: "fixed", provider_id: provider.id },
      }),
    ).toThrow();
  });

  it("rejects a fixed response that selected a different model unless SC-39 applies", () => {
    const routed = {
      intent: { scene_id: "SC-02" },
      routing: { provider_id: provider.id, reason: "user_fixed_main_model" },
    };
    expect(() =>
      assertFixedRoute(routed, { mode: "fixed", provider_id: provider.id }),
    ).not.toThrow();
    expect(() =>
      assertFixedRoute(routed, {
        mode: "fixed",
        provider_id: "6a1f8b1c-7f18-4b7e-9a2f-1f2b3c4d5e70",
      }),
    ).toThrow();
    expect(() => assertFixedRoute(routed, { mode: "auto" })).not.toThrow();
    expect(() =>
      assertFixedRoute({ status: "failed" }, { mode: "fixed", provider_id: provider.id }),
    ).not.toThrow();
    expect(() =>
      assertFixedRoute({ status: "completed" }, { mode: "fixed", provider_id: provider.id }),
    ).toThrow();
    expect(() =>
      assertFixedRoute(
        {
          ...routed,
          intent: { scene_id: "SC-39" },
          routing: { ...routed.routing, provider_id: "x", reason: "off_topic_flash" },
        },
        { mode: "fixed", provider_id: provider.id },
      ),
    ).not.toThrow();
    expect(() =>
      assertFixedRoute(
        { ...routed, intent: { scene_id: "SC-39" } },
        { mode: "fixed", provider_id: provider.id },
      ),
    ).toThrow();
  });

  it("rejects unknown fields in model write input", () => {
    expect(() =>
      providerCreate.parse({
        operation_id: provider.id,
        catalog_id: "deepseek/deepseek-v4-pro",
        api_model: "deepseek-v4-pro",
        alias: "经营分析",
        unexpected: true,
      }),
    ).toThrow();
  });
});
