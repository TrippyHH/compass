import { describe, expect, it } from "vitest";
import { parseCallResponse, parseModelEvent, parseProviderView } from "@/lib/model-contracts";
import { providerCreate } from "@/contracts/models";

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
