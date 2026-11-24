import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { PagePayload } from "@/contracts/page";
import { DocumentSession } from "@/client/request-scope";

const VIEW_KEY = "/admin/capabilities/models";
const SESSION_ID = "11111111-1111-4111-8111-111111111111";
const OTHER_SESSION_ID = "22222222-2222-4222-8222-222222222222";
const REVISION = "a".repeat(64);
const OTHER_REVISION = "b".repeat(64);

type Payload = Pick<PagePayload, "page" | "session" | "viewKey">;

function payload(): Payload {
  return {
    page: { id: "capabilities-models", scopeKind: "platform" } as Payload["page"],
    session: { sessionId: SESSION_ID, viewRevision: REVISION, scope: {} } as Payload["session"],
    viewKey: VIEW_KEY,
  };
}

function sessionBody(viewRevision = REVISION, sessionId = SESSION_ID) {
  return {
    csrfToken: "c".repeat(43),
    user: {
      id: SESSION_ID,
      loginName: "local.admin",
      displayName: "本地管理员",
      preferences: {},
      version: "1",
    },
    sessionId,
    isPlatformAdmin: true,
    expiresAt: new Date(Date.now() + 3_600_000).toISOString(),
    viewRevision,
    authzVersion: "1",
    capabilities: [],
    tenantCapabilities: [],
    navigation: [],
    scope: {},
    tenant: null,
    workspace: null,
  };
}

function respond(body: Record<string, unknown>) {
  return {
    ok: true,
    status: 200,
    headers: new Headers({ "X-Compass-View-Revision": String(body.viewRevision) }),
    json: async () => body,
  };
}

let root: HTMLElement;
let navigation: { push: (path: string) => void; refresh: () => void };

beforeEach(() => {
  root = { hidden: false, inert: false } as unknown as HTMLElement;
  navigation = { push: vi.fn(), refresh: vi.fn() };
  vi.stubGlobal("document", {
    visibilityState: "visible",
    querySelectorAll: () => [],
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  });
  vi.stubGlobal("window", {
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    confirm: () => true,
  });
  vi.stubGlobal("location", {
    pathname: VIEW_KEY,
    search: "",
    href: `http://127.0.0.1:3000${VIEW_KEY}`,
    replace: vi.fn(),
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function activeSession() {
  const session = new DocumentSession(payload(), navigation);
  session.phase = "active";
  session.attach(root);
  return session;
}

describe("background suspension", () => {
  it("covers the document without retiring it", () => {
    const session = activeSession();
    session.suspend();
    expect(session.suspended).toBe(true);
    expect(session.phase).toBe("active");
    expect(root.hidden).toBe(true);
    expect(root.inert).toBe(true);
  });

  it("re-verifies and restores the same document instead of reloading", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      void input;
      return respond(sessionBody());
    });
    vi.stubGlobal("fetch", fetchMock);
    const session = activeSession();
    session.suspend();
    await session.resume();
    expect(session.suspended).toBe(false);
    expect(session.phase).toBe("active");
    expect(root.hidden).toBe(false);
    expect(session.message).toBe("");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(String(fetchMock.mock.calls[0]?.[0])).toContain("/api/web/v1/session");
    expect(navigation.refresh).not.toHaveBeenCalled();
    expect((location.replace as unknown as ReturnType<typeof vi.fn>)).not.toHaveBeenCalled();
  });

  it("retires and reloads when the probe cannot confirm the session", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => {
      throw new Error("offline");
    }));
    const session = activeSession();
    session.suspend();
    await session.resume();
    expect(session.phase).toBe("retired");
    expect(root.hidden).toBe(true);
    expect(location.replace).toHaveBeenCalledWith(VIEW_KEY);
  });

  it("retires when the authorization revision changed while backgrounded", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => respond(sessionBody(OTHER_REVISION))));
    const session = activeSession();
    session.suspend();
    await session.resume();
    expect(session.phase).toBe("retired");
    expect(location.replace).toHaveBeenCalledWith(VIEW_KEY);
  });

  it("retires when another session took over", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => respond(sessionBody(REVISION, OTHER_SESSION_ID))));
    const session = activeSession();
    session.suspend();
    await session.resume();
    expect(session.phase).toBe("retired");
  });

  it("ignores suspension outside the active phase", () => {
    const session = new DocumentSession(payload(), navigation);
    session.suspend();
    expect(session.suspended).toBe(false);
  });
});
