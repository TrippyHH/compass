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
    expect(text.trim().endsWith('"type":"final"}')).toBe(true);
  });

  it("rejects a mismatched call id", async () => {
    const response = upstream([
      `data: ${JSON.stringify({ type: "text_delta", call_id: "other", text: "x" })}\n\n`,
    ]);
    await expect(new Response(relayModelEvents(response, callId)).text()).rejects.toThrow();
  });
});
