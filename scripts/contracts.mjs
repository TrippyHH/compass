import { spawnSync } from "node:child_process";
import { copyFileSync, existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { pnpmSpawnSync } from "./pnpm-spawn.mjs";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const temp = mkdtempSync(path.join(tmpdir(), "compass-contracts-"));
const schemaFiles = [
  "demo-overview.schema.json",
  "model-available.schema.json",
  "model-call-record.schema.json",
  "model-call-response.schema.json",
  "model-catalog-page.schema.json",
  "model-change-page.schema.json",
  "model-event.schema.json",
  "model-provider-page.schema.json",
  "model-provider-view.schema.json",
  "model-saved-provider.schema.json",
];
function run(cmd, args) {
  const options = { cwd: root, stdio: "inherit" };
  const result = cmd === "pnpm" ? pnpmSpawnSync(args, options) : spawnSync(cmd, args, options);
  if (result.status !== 0) throw new Error(`Contract command failed: ${cmd}`);
}
try {
  run("uv", [
    "run",
    "--locked",
    "--package",
    "compass-agent",
    "python",
    "scripts/export-contracts.py",
    temp,
  ]);
  run("pnpm", [
    "exec",
    "openapi-typescript",
    path.join(temp, "openapi.json"),
    "-o",
    path.join(temp, "api.ts"),
  ]);
  const outputs = [
    ["openapi.json", "contracts/openapi.json"],
    ["openapi.json", "apps/web/src/generated/openapi.json"],
    ["api.ts", "apps/web/src/generated/api.ts"],
    ...schemaFiles.flatMap((file) => [
      [file, `contracts/${file}`],
      [file, `apps/web/src/generated/${file}`],
    ]),
  ];
  for (const [source, dest] of outputs) {
    const target = path.join(root, dest);
    if (process.argv.includes("--check")) {
      if (
        !existsSync(target) ||
        !readFileSync(target).equals(readFileSync(path.join(temp, source)))
      )
        throw new Error(`Contract drift: ${dest}; run pnpm contracts`);
    } else {
      mkdirSync(path.dirname(target), { recursive: true });
      copyFileSync(path.join(temp, source), target);
    }
  }
  console.log(
    process.argv.includes("--check")
      ? "Contracts match their Python source."
      : "Contracts generated.",
  );
} finally {
  rmSync(temp, { recursive: true, force: true });
}
