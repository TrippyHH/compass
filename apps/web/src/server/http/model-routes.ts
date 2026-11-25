import type { TransactionMode } from "../platform/db";
import type { WebUseCases } from "../composition";
import type { AgentTrustedContext } from "../../contracts/models";
import { modelAgentClient, relayModelEvents } from "../model-agent-client";
import { assertFixedRoute } from "../../lib/model-contracts";
import { reject } from "../platform/errors";
import { privateHeaders } from "./responses";
import { jsonResponse } from "./responses";
import * as dto from "../../contracts/models";

type Execute = <T>(
  token: string,
  mode: TransactionMode,
  work: (services: WebUseCases) => Promise<T>,
) => Promise<{ value: T; viewRevision: string }>;

export type ModelRouteInput = {
  request: Request;
  token: string;
  method: string;
  path: string[];
  query: Record<string, string>;
  body: unknown;
  execute: Execute;
};

export function isModelRoute(path: string[]): boolean {
  if (path[0] === "models")
    return ["available", "calls"].includes(path[1] ?? "") && path.length <= 3;
  return path[0] === "platform" && path[1] === "models";
}

async function authorize(
  execute: Execute,
  token: string,
  route:
    | { kind: "platform"; purpose: "manage" | "test" }
    | { kind: "tenant"; purpose: "chat" | "module_read" },
  scope: { tenantId?: string; workspaceId?: string } = {},
): Promise<{ context: AgentTrustedContext; revision: string }> {
  const result = await execute(token, "query", ({ models }) =>
    models.authorize(token, route, scope),
  );
  return { context: result.value, revision: result.viewRevision };
}

function tenantScope(query: Record<string, string>) {
  return {
    tenantId: query.tenantId ? dto.uuid.parse(query.tenantId) : undefined,
    workspaceId: query.workspaceId ? dto.uuid.parse(query.workspaceId) : undefined,
  };
}

function streamResponse(stream: ReadableStream<Uint8Array>, revision: string): Response {
  return new Response(stream, {
    status: 200,
    headers: {
      ...privateHeaders,
      "Content-Type": "text/event-stream; charset=utf-8",
      "X-Accel-Buffering": "no",
      "X-Compass-View-Revision": revision,
    },
  });
}

function signalFor(request: Request): AbortSignal {
  return AbortSignal.any([request.signal, AbortSignal.timeout(190_000)]);
}

export async function handleModelRoute(input: ModelRouteInput): Promise<Response | null> {
  const { request, token, method, path, query, body, execute } = input;
  if (!isModelRoute(path)) return null;

  if (path[0] === "models") {
    if (path[1] === "available" && path.length === 2 && method === "GET") {
      const scope = tenantScope(query);
      const authorized = await authorize(
        execute,
        token,
        { kind: "tenant", purpose: "module_read" },
        scope,
      );
      const value = await modelAgentClient.available(authorized.context);
      return jsonResponse(value, authorized.revision);
    }
    if (path[1] === "calls") {
      if (path.length === 2 && method === "POST") {
        const value = dto.normalizeChatCall(dto.chatCallRequest.parse(body));
        const scope = tenantScope(query);
        const authorized = await authorize(
          execute,
          token,
          { kind: "tenant", purpose: "chat" },
          scope,
        );
        if (value.input.stream) {
          const upstream = await modelAgentClient.openChatStream(
            authorized.context,
            value,
            signalFor(request),
          );
          return streamResponse(
            relayModelEvents(upstream, value.call_id, true, value.main_model),
            authorized.revision,
          );
        }
        const response = await modelAgentClient.chatCall(
          authorized.context,
          value,
          signalFor(request),
        );
        try {
          assertFixedRoute(response, value.main_model);
        } catch {
          reject("unavailable", "模型服务响应不符合契约。");
        }
        return jsonResponse(response, authorized.revision);
      }
      if (path.length === 3 && method === "GET") {
        const callId = dto.uuid.parse(path[2]);
        const scope = tenantScope(query);
        const authorized = await authorize(
          execute,
          token,
          { kind: "tenant", purpose: "chat" },
          scope,
        );
        const value = await modelAgentClient.callRecord(authorized.context, callId);
        return jsonResponse(value, authorized.revision);
      }
      return null;
    }
    return null;
  }

  // platform/models
  if (path.length === 2) {
    if (method === "GET") {
      const value = dto.modelQuery.parse(query);
      const authorized = await authorize(execute, token, {
        kind: "platform",
        purpose: "manage",
      });
      const page = await modelAgentClient.providers(authorized.context, {
        q: value.q,
        page: value.page,
        page_size: value.page_size,
      });
      return jsonResponse(page, authorized.revision);
    }
    if (method === "POST") {
      const payload = dto.providerCreate.parse(body);
      const authorized = await authorize(execute, token, {
        kind: "platform",
        purpose: "manage",
      });
      const saved = await modelAgentClient.create(authorized.context, payload);
      await execute(token, "command", ({ models }) =>
        models.audit(token, {
          action: "model.saved",
          targetType: "model",
          targetId: saved.provider_id,
          summary: { version: saved.version, operationId: saved.operation_id },
        }),
      );
      return jsonResponse(saved, authorized.revision, 201);
    }
    return null;
  }

  if (path.length === 3) {
    if (path[2] === "catalog" && method === "GET") {
      const value = dto.catalogQuery.parse(query);
      const authorized = await authorize(execute, token, {
        kind: "platform",
        purpose: "manage",
      });
      const page = await modelAgentClient.catalog(authorized.context, {
        q: value.q,
        vendor: value.vendor,
        page: value.page,
        page_size: value.page_size,
      });
      return jsonResponse(page, authorized.revision);
    }
    const providerId = dto.uuid.parse(path[2]);
    if (method === "GET") {
      const authorized = await authorize(execute, token, {
        kind: "platform",
        purpose: "manage",
      });
      const view = await modelAgentClient.provider(authorized.context, providerId);
      return jsonResponse(view, authorized.revision);
    }
    if (method === "PATCH") {
      const payload = dto.providerPatch.parse(body);
      const authorized = await authorize(execute, token, {
        kind: "platform",
        purpose: "manage",
      });
      const saved = await modelAgentClient.patch(authorized.context, providerId, payload);
      await execute(token, "command", ({ models }) =>
        models.audit(token, {
          action: "model.saved",
          targetType: "model",
          targetId: saved.provider_id,
          summary: { version: saved.version, operationId: saved.operation_id },
        }),
      );
      return jsonResponse(saved, authorized.revision);
    }
    return null;
  }

  if (path.length === 4) {
    if (path[2] === "operations" && method === "GET") {
      const operationId = dto.uuid.parse(path[3]);
      const authorized = await authorize(execute, token, {
        kind: "platform",
        purpose: "manage",
      });
      const saved = await modelAgentClient.operation(authorized.context, operationId);
      return jsonResponse(saved, authorized.revision);
    }
    if (path[2] === "calls" && method === "GET") {
      const callId = dto.uuid.parse(path[3]);
      const authorized = await authorize(execute, token, {
        kind: "platform",
        purpose: "test",
      });
      const record = await modelAgentClient.testRecord(authorized.context, callId);
      return jsonResponse(record, authorized.revision);
    }
    const providerId = dto.uuid.parse(path[2]);
    if (path[3] === "changes" && method === "GET") {
      const value = dto.modelQuery.parse(query);
      const authorized = await authorize(execute, token, {
        kind: "platform",
        purpose: "manage",
      });
      const changes = await modelAgentClient.changes(authorized.context, providerId, {
        page: value.page,
        page_size: value.page_size,
      });
      return jsonResponse(changes, authorized.revision);
    }
    if (path[3] === "tests" && method === "POST") {
      const payload = dto.testCallInput.parse(body);
      const authorized = await authorize(execute, token, {
        kind: "platform",
        purpose: "test",
      });
      if (payload.input.stream) {
        const upstream = await modelAgentClient.openTestStream(
          authorized.context,
          providerId,
          payload,
          signalFor(request),
        );
        return streamResponse(relayModelEvents(upstream, payload.call_id), authorized.revision);
      }
      const response = await modelAgentClient.testCall(
        authorized.context,
        providerId,
        payload,
        signalFor(request),
      );
      await execute(token, "command", ({ models }) =>
        models.audit(token, {
          action: "model.tested",
          targetType: "model",
          targetId: providerId,
          summary: {
            callId: response.call_id,
            status: response.status,
            errorCode: response.error_code ?? null,
          },
        }),
      );
      return jsonResponse(response, authorized.revision);
    }
    return null;
  }

  reject("not_found");
}
