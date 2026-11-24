import { setTimeout as delay } from "node:timers/promises";

const DISABLED_VALUES = new Set(["0", "false", "no", "off", "none"]);

export function resolveDevLoginUrl(port) {
  return `http://127.0.0.1:${port}/login`;
}

/**
 * @param {Record<string, string | undefined>} [env]
 */
export function shouldOpenBrowser(env = process.env) {
  const ci = env.CI?.trim().toLowerCase();
  if (ci && ci !== "0" && ci !== "false") return false;
  const value = env.WEB_DEV_OPEN_BROWSER?.trim().toLowerCase();
  return !value || !DISABLED_VALUES.has(value);
}

/**
 * @param {NodeJS.Platform} platform
 * @param {string} url
 * @param {Record<string, string | undefined>} [env]
 */
export function browserCommand(platform, url, env = process.env) {
  if (platform === "win32") return { command: "cmd", args: ["/c", "start", "", url] };
  if (platform === "darwin") return { command: "open", args: [url] };
  if (env.WSL_DISTRO_NAME) return { command: "cmd.exe", args: ["/c", "start", "", url] };
  return { command: "xdg-open", args: [url] };
}

export async function waitForServer(url, options = {}) {
  const {
    signal,
    timeoutMs = 120_000,
    intervalMs = 300,
    attemptTimeoutMs = 10_000,
    fetchImpl = fetch,
  } = options;
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (signal?.aborted) return false;
    try {
      const signals = [AbortSignal.timeout(attemptTimeoutMs)];
      if (signal) signals.push(signal);
      const response = await fetchImpl(url, {
        redirect: "manual",
        signal: AbortSignal.any(signals),
      });
      await response.body?.cancel();
      return true;
    } catch {
      if (signal?.aborted || Date.now() >= deadline) return false;
      try {
        await delay(intervalMs, undefined, { signal });
      } catch {
        return false;
      }
    }
  }
  return false;
}
