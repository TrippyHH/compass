import { createServer, type Server } from "node:http";
import { describe, expect, it } from "vitest";
import {
  browserCommand,
  resolveDevLoginUrl,
  shouldOpenBrowser,
  waitForServer,
} from "../scripts/dev-browser.mjs";

function listen(server: Server) {
  return new Promise<string>((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      if (!address || typeof address === "string") {
        reject(new Error("Expected a TCP address"));
        return;
      }
      resolve(`http://127.0.0.1:${address.port}`);
    });
  });
}

async function close(server: Server) {
  server.closeAllConnections();
  await new Promise<void>((resolve) => server.close(() => resolve()));
}

describe("web dev browser", () => {
  it("targets the local login page", () => {
    expect(resolveDevLoginUrl("3000")).toBe("http://127.0.0.1:3000/login");
  });

  it("opens by default and respects explicit opt-outs", () => {
    expect(shouldOpenBrowser({})).toBe(true);
    expect(shouldOpenBrowser({ WEB_DEV_OPEN_BROWSER: "1" })).toBe(true);
    for (const value of ["0", "false", "NO", " off ", "none"])
      expect(shouldOpenBrowser({ WEB_DEV_OPEN_BROWSER: value })).toBe(false);
    expect(shouldOpenBrowser({ CI: "true" })).toBe(false);
    expect(shouldOpenBrowser({ CI: "false" })).toBe(true);
  });

  it("uses the platform default browser command", () => {
    const url = "http://127.0.0.1:3000/login";
    expect(browserCommand("win32", url)).toEqual({
      command: "cmd",
      args: ["/c", "start", "", url],
    });
    expect(browserCommand("darwin", url)).toEqual({ command: "open", args: [url] });
    expect(browserCommand("linux", url, {})).toEqual({ command: "xdg-open", args: [url] });
    expect(browserCommand("linux", url, { WSL_DISTRO_NAME: "Ubuntu" })).toEqual({
      command: "cmd.exe",
      args: ["/c", "start", "", url],
    });
  });

  it("waits until the dev server answers", async () => {
    const server = createServer((_request, response) => {
      response.statusCode = 200;
      response.end("ok");
    });
    const base = await listen(server);
    try {
      await expect(
        waitForServer(`${base}/login`, { timeoutMs: 3000, intervalMs: 20 }),
      ).resolves.toBe(true);
    } finally {
      await close(server);
    }
  });

  it("gives up when nothing listens", async () => {
    const server = createServer();
    const base = await listen(server);
    await close(server);
    await expect(waitForServer(`${base}/login`, { timeoutMs: 300, intervalMs: 50 })).resolves.toBe(
      false,
    );
  });
});
