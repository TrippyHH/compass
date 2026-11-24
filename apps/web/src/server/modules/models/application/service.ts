import { reject } from "../../../platform/errors";
import type { AuditEntry } from "../../../platform/application-types";
import type { AgentTrustedContext } from "../../../../contracts/models";
import { allowed, requirePermission, type AccessContext } from "../../access/public";

export type ModelRoute =
  | { kind: "platform"; purpose: "manage" | "test" }
  | { kind: "tenant"; purpose: "chat" | "module_read" };

export type ModelScope = { tenantId?: string; workspaceId?: string };

export type ModelAuditEvent = {
  action: string;
  targetType: string;
  targetId?: string | null;
  tenantId?: string | null;
  workspaceId?: string | null;
  summary?: AuditEntry["safeSummary"];
};

export function createModelService(deps: { context: AccessContext; newId(): string }) {
  const { context } = deps;
  return {
    async authorize(
      token: string,
      route: ModelRoute,
      scope: ModelScope = {},
    ): Promise<AgentTrustedContext> {
      const actor = await context.identity.requireActor(token);
      if (route.kind === "platform") {
        if (!actor.isPlatformAdmin) reject("forbidden", "仅平台管理员可管理模型接入。");
        return {
          actor_user_id: actor.userId,
          purpose: route.purpose,
          scope: { kind: "platform" },
        };
      }
      if (!scope.tenantId || !scope.workspaceId) reject("validation_failed");
      const access = await context.access.current(actor, scope.tenantId);
      const workspace = await context.access.workspace(scope.tenantId, scope.workspaceId);
      if (workspace.status !== "active") reject("forbidden");
      const target = { tenantId: scope.tenantId, workspaceId: scope.workspaceId };
      if (route.purpose === "chat") {
        requirePermission(access, "module.analysis", target);
        requirePermission(access, "models.invoke", target);
      } else if (
        !allowed(access, "module.analysis", target) &&
        !allowed(access, "module.admin.capabilities", target)
      ) {
        reject("forbidden");
      }
      return {
        actor_user_id: actor.userId,
        purpose: route.purpose,
        scope: {
          kind: "tenant",
          tenant_id: scope.tenantId,
          workspace_id: scope.workspaceId,
        },
      };
    },

    async audit(token: string, event: ModelAuditEvent): Promise<void> {
      const actor = await context.identity.requireActor(token);
      const id = deps.newId();
      await context.audit.append({
        id,
        eventKey: `${event.action}:${event.targetId ?? id}:${id}`,
        actorUserId: actor.userId,
        tenantId: event.tenantId ?? null,
        workspaceId: event.workspaceId ?? null,
        action: event.action,
        targetType: event.targetType,
        targetId: event.targetId ?? null,
        outcome: "success",
        safeSummary: event.summary ?? {},
      });
    },

    async canInvoke(token: string, scope: ModelScope): Promise<boolean> {
      if (!scope.tenantId || !scope.workspaceId) return false;
      const actor = await context.identity.requireActor(token);
      const access = await context.access.current(actor, scope.tenantId);
      return allowed(access, "models.invoke", {
        tenantId: scope.tenantId,
        workspaceId: scope.workspaceId,
      });
    },
  };
}
