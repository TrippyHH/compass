import { describe, expect, it } from "vitest";
import { relayModelEvents } from "@/server/model-agent-client";

const callId = "6a1f8b1c-7f18-4b7e-9a2f-1f2b3c4d5e6f";
const encoder = new TextEncoder();

function upstream(frames: string[]): Response {
  return new Response(
    new ReadableStream<Uint8Array>({
      start(controller) {
        for (const frame of frames) controller.enqueue(encoder.encode(frame));
        controller.close();
      },
    }),
  );
}

describe("BFF model stream relay", () => {
  it("forwards validated events in order", async () => {
    const response = upstream([
      `data: ${JSON.stringify({ type: "start", call_id: callId, provider_id: callId, config_version: 1 })}\n\n`,
      `data: ${JSON.stringify({ type: "text_delta", call_id: callId, text: "你好" })}\n\n`,
      `data: ${JSON.stringify({
        type: "final",
        call_id: callId,
        provider_id: callId,
        config_version: 1,
        status: "completed",
        duration_ms: 12,
        usage: { input_tokens: 1, output_tokens: 2, reasoning_tokens: null },
        error_code: null,
        upstream_outcome_unknown: false,
        requested_model: "deepseek-v4-pro",
        response_model: "deepseek-v4-pro",
        upstream_request_id: null,
        finish_reason: "stop",
        validation: { status: "not_requested", mode: "text", paths: [] },
        continuation_token: null,
      })}\n\n`,
    ]);
    const text = await new Response(relayModelEvents(response, callId)).text();
    expect(text).toContain('"text_delta"');
    const frames = text
      .trim()
      .split("\n\n")
      .map((frame) => JSON.parse(frame.replace(/^data: /, "")));
    expect(frames.at(-1).type).toBe("final");
  });

  it("forwards routed classification, route selection and final frames", async () => {
    const response = upstream([
      `data: ${JSON.stringify({ type: "start", call_id: callId })}\n\n`,
      `data: ${JSON.stringify({ type: "classification_status", call_id: callId, stage: "flash" })}\n\n`,
      `data: ${JSON.stringify({
        type: "route_selected",
        call_id: callId,
        intent: { category_id: "business_channel", scene_id: "SC-02" },
        features: {
          task_type: "metric_comparison",
          difficulty: "L1",
          response_mode: "answer",
          required_capabilities: [],
        },
        routing: {
          mode: "auto",
          provider_id: callId,
          model: "deepseek-flash",
          reason: "difficulty_L1",
          dictionary_version: "sha256:abc",
          routing_version: "lesson12-flow-1",
        },
      })}\n\n`,
      `data: ${JSON.stringify({ type: "text_delta", call_id: callId, text: "你好" })}\n\n`,
      `data: ${JSON.stringify({
        type: "final",
        call_id: callId,
        status: "failed",
        error_code: "intent_invalid",
      })}\n\n`,
    ]);
    const text = await new Response(
      relayModelEvents(response, callId, true, { mode: "fixed", provider_id: callId }),
    ).text();
    expect(text).toContain('"classification_status"');
    expect(text).toContain('"route_selected"');
    expect(text).toContain('"text_delta"');
    const frames = text
      .trim()
      .split("\n\n")
      .map((frame) => JSON.parse(frame.replace(/^data: /, "")));
    expect(frames.at(-1).type).toBe("final");
    expect(frames.at(-1).status).toBe("failed");
  });

  it("rejects a routed start that still carries a provider", async () => {
    const response = upstream([
      `data: ${JSON.stringify({ type: "start", call_id: callId, provider_id: callId })}\n\n`,
    ]);
    await expect(new Response(relayModelEvents(response, callId, true)).text()).rejects.toThrow();
  });

  it("rejects a fixed route that selected another provider", async () => {
    const response = upstream([
      `data: ${JSON.stringify({ type: "start", call_id: callId })}\n\n`,
      `data: ${JSON.stringify({
        type: "route_selected",
        call_id: callId,
        intent: { category_id: "business_channel", scene_id: "SC-02" },
        features: {
          task_type: "metric_comparison",
          difficulty: "L1",
          response_mode: "answer",
          required_capabilities: [],
        },
        routing: {
          mode: "fixed",
          provider_id: callId,
          model: "deepseek-flash",
          reason: "user_fixed_main_model",
          dictionary_version: "sha256:abc",
          routing_version: "lesson12-flow-1",
        },
      })}\n\n`,
    ]);
    await expect(
      new Response(
        relayModelEvents(response, callId, true, {
          mode: "fixed",
          provider_id: "6a1f8b1c-7f18-4b7e-9a2f-1f2b3c4d5e70",
        }),
      ).text(),
    ).rejects.toThrow();
  });

  it("rejects routed output before route selection", async () => {
    const response = upstream([
      `data: ${JSON.stringify({ type: "start", call_id: callId })}\n\n`,
      `data: ${JSON.stringify({ type: "text_delta", call_id: callId, text: "x" })}\n\n`,
    ]);
    await expect(new Response(relayModelEvents(response, callId, true)).text()).rejects.toThrow();
  });

  it("rejects a stream that does not start with start", async () => {
    const response = upstream([
      `data: ${JSON.stringify({ type: "text_delta", call_id: callId, text: "x" })}\n\n`,
    ]);
    await expect(new Response(relayModelEvents(response, callId, true)).text()).rejects.toThrow();
  });

  it("rejects a mismatched call id", async () => {
    const response = upstream([
      `data: ${JSON.stringify({ type: "text_delta", call_id: "other", text: "x" })}\n\n`,
    ]);
    await expect(new Response(relayModelEvents(response, callId)).text()).rejects.toThrow();
  });
});
