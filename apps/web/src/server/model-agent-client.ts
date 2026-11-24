import "server-only";
import {
  parseAvailableModels,
  parseCallRecord,
  parseCallResponse,
  parseCatalogPage,
  parseModelChangePage,
  parseModelEvent,
  parseProvider,
  parseProviderPage,
  parseProviderView,
} from "../lib/model-contracts";
import { ApplicationError } from "./platform/errors";
import type {
  AgentTrustedContext,
  AvailableModels,
  CallRecordView,
  CallResponse,
  CatalogPage,
  ChatCallInput,
  ModelChangePage,
  ProviderCreateInput,
  ProviderPage,
  ProviderPatchInput,
  ProviderView,
  SavedProvider,
  TestCallInput,
} from "../contracts/models";

export type { AgentTrustedContext } from "../contracts/models";

const TIMEOUT_MS = 190_000;
const MAX_FRAME_BYTES = 2 * 1024 * 1024;

function baseUrl(): string {
  return (process.env.AGENT_API_URL ?? "http://127.0.0.1:8000").replace(/\/$/, "");
}

function serviceToken(): string {
  const token = process.env.COMPASS_AGENT_SERVICE_TOKEN;
  if (!token) throw new ApplicationError("unavailable", "模型服务尚未配置。");
  return token;
}

function headers(context: AgentTrustedContext, body: boolean): HeadersInit {
  return {
    Authorization: `Bearer ${serviceToken()}`,
    "X-Compass-Context": Buffer.from(JSON.stringify(context), "utf8").toString("base64url"),
    ...(body ? { "Content-Type": "application/json" } : {}),
  };
}

async function failure(response: Response): Promise<never> {
  let code = "";
  let message: string | undefined;
  let currentVersion: string | undefined;
  try {
    const payload = (await response.json()) as {
      error?: { code?: string; message?: string; currentVersion?: number };
    };
    code = payload.error?.code ?? "";
    message = payload.error?.message;
    currentVersion =
      payload.error?.currentVersion === undefined
        ? undefined
        : String(payload.error.currentVersion);
  } catch {
    code = "";
  }
  if (response.status === 409) {
    throw new ApplicationError(
      "conflict",
      code === "call_already_exists"
        ? "该调用已存在，请按调用 ID 核对结果。"
        : "配置已被更新，请重新加载并核对修改。",
      currentVersion,
    );
  }
  if (response.status === 403) throw new ApplicationError("forbidden", "当前账号无权访问此操作。");
  if (response.status === 422) {
    throw new ApplicationError("validation_failed", message ?? "请检查输入的内容与范围。");
  }
  if (response.status === 429)
    throw new ApplicationError("rate_limited", "尝试过于频繁，请稍后重试。");
  if (response.status === 404) throw new ApplicationError("not_found", "未找到可访问的记录。");
  throw new ApplicationError("unavailable", "模型服务暂时不可用，请稍后重试。");
}

async function send(
  path: string,
  init: {
    method?: string;
    context: AgentTrustedContext;
    body?: unknown;
    signal?: AbortSignal;
    stream?: boolean;
  },
): Promise<Response> {
  let response: Response;
  try {
    response = await fetch(`${baseUrl()}${path}`, {
      method: init.method ?? (init.body === undefined ? "GET" : "POST"),
      headers: headers(init.context, init.body !== undefined),
      body: init.body === undefined ? undefined : JSON.stringify(init.body),
      cache: "no-store",
      redirect: "error",
      signal: init.signal ?? AbortSignal.timeout(TIMEOUT_MS),
    });
  } catch {
    throw new ApplicationError("unavailable", "模型服务暂时不可用，请稍后重试。");
  }
  if (!response.ok) await failure(response);
  return response;
}

async function json<T>(
  path: string,
  init: {
    method?: string;
    context: AgentTrustedContext;
    body?: unknown;
    signal?: AbortSignal;
  },
  parse: (value: unknown) => T,
): Promise<T> {
  const response = await send(path, init);
  let value: unknown;
  try {
    value = await response.json();
  } catch {
    throw new ApplicationError("unavailable", "模型服务返回了无法解析的响应。");
  }
  try {
    return parse(value);
  } catch {
    throw new ApplicationError("unavailable", "模型服务响应不符合契约。");
  }
}

function query(params: Record<string, string | number | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params))
    if (value !== undefined && value !== "") search.set(key, String(value));
  const text = search.toString();
  return text ? `?${text}` : "";
}

export const modelAgentClient = {
  catalog(
    context: AgentTrustedContext,
    params: { q?: string; vendor?: string; page?: number; page_size?: number },
  ): Promise<CatalogPage> {
    return json(`/v1/models/catalog${query(params)}`, { context }, parseCatalogPage);
  },
  providers(
    context: AgentTrustedContext,
    params: { q?: string; page?: number; page_size?: number },
  ): Promise<ProviderPage> {
    return json(`/v1/models/providers${query(params)}`, { context }, parseProviderPage);
  },
  provider(context: AgentTrustedContext, id: string): Promise<ProviderView> {
    return json(`/v1/models/providers/${id}`, { context }, parseProviderView);
  },
  create(context: AgentTrustedContext, body: ProviderCreateInput): Promise<SavedProvider> {
    return json("/v1/models/providers", { context, body }, parseProvider);
  },
  patch(
    context: AgentTrustedContext,
    id: string,
    body: ProviderPatchInput,
  ): Promise<SavedProvider> {
    return json(`/v1/models/providers/${id}`, { method: "PATCH", context, body }, parseProvider);
  },
  operation(context: AgentTrustedContext, id: string): Promise<SavedProvider> {
    return json(`/v1/models/operations/${id}`, { context }, parseProvider);
  },
  changes(
    context: AgentTrustedContext,
    id: string,
    params: { page?: number; page_size?: number },
  ): Promise<ModelChangePage> {
    return json(
      `/v1/models/providers/${id}/changes${query(params)}`,
      { context },
      parseModelChangePage,
    );
  },
  available(context: AgentTrustedContext): Promise<AvailableModels> {
    return json("/v1/models/available", { context }, parseAvailableModels);
  },
  callRecord(context: AgentTrustedContext, id: string): Promise<CallRecordView> {
    return json(`/v1/models/calls/${id}`, { context }, parseCallRecord);
  },
  testRecord(context: AgentTrustedContext, id: string): Promise<CallRecordView> {
    return json(`/v1/models/test-calls/${id}`, { context }, parseCallRecord);
  },
  testCall(
    context: AgentTrustedContext,
    providerId: string,
    body: TestCallInput,
    signal?: AbortSignal,
  ): Promise<CallResponse> {
    return json(
      `/v1/models/providers/${providerId}/tests`,
      { context, body, signal },
      parseCallResponse,
    );
  },
  chatCall(
    context: AgentTrustedContext,
    body: ChatCallInput,
    signal?: AbortSignal,
  ): Promise<CallResponse> {
    return json("/v1/models/calls", { context, body, signal }, parseCallResponse);
  },
  openTestStream(
    context: AgentTrustedContext,
    providerId: string,
    body: TestCallInput,
    signal?: AbortSignal,
  ): Promise<Response> {
    return send(`/v1/models/providers/${providerId}/tests`, {
      context,
      body,
      signal,
      stream: true,
    });
  },
  openChatStream(
    context: AgentTrustedContext,
    body: ChatCallInput,
    signal?: AbortSignal,
  ): Promise<Response> {
    return send("/v1/models/calls", { context, body, signal, stream: true });
  },
};

export function relayModelEvents(upstream: Response, callId: string): ReadableStream<Uint8Array> {
  const body = upstream.body;
  if (!body) throw new ApplicationError("unavailable", "模型服务未返回流式响应。");
  const reader = body.getReader();
  const decoder = new TextDecoder();
  const encoder = new TextEncoder();
  let buffer = "";
  let finalSeen = false;
  return new ReadableStream<Uint8Array>({
    async start(controller) {
      try {
        for (;;) {
          const { value, done } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true }).replaceAll("\r\n", "\n");
          if (buffer.length > MAX_FRAME_BYTES * 2) throw new Error("Model stream buffer exceeded");
          for (;;) {
            const boundary = buffer.indexOf("\n\n");
            if (boundary < 0) break;
            const frame = buffer.slice(0, boundary);
            buffer = buffer.slice(boundary + 2);
            if (frame.length > MAX_FRAME_BYTES) throw new Error("Model stream frame too large");
            const data = frame
              .split("\n")
              .filter((line) => line.startsWith("data:"))
              .map((line) => line.slice(5).trimStart())
              .join("\n");
            if (!data) continue;
            let event;
            try {
              event = parseModelEvent(JSON.parse(data));
            } catch {
              throw new Error("Invalid model event");
            }
            if (event.call_id !== callId) throw new Error("Mismatched model call id");
            if (finalSeen) throw new Error("Model event after final");
            if (event.type === "final") finalSeen = true;
            controller.enqueue(encoder.encode(`data: ${JSON.stringify(event)}\n\n`));
          }
        }
        if (buffer.trim()) throw new Error("Truncated model stream");
        controller.close();
      } catch (error) {
        controller.error(error);
        await reader.cancel().catch(() => undefined);
      }
    },
    cancel(reason) {
      return reader.cancel(reason);
    },
  });
}
