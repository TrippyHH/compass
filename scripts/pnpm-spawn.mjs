import { spawn, spawnSync } from "node:child_process";

// Windows ships pnpm and the workspace CLI shims as .cmd files: Node's spawn
// performs no PATHEXT lookup and refuses to launch a .cmd without a shell, so a
// bare "pnpm" or "node_modules/.bin/turbo" fails with ENOENT/EINVAL. Build one
// quoted command line and let a shell run it; macOS and Linux keep the plain
// argument vector, so their behaviour is unchanged.
const isWindows = process.platform === "win32";

function quote(argument) {
  if (argument === "") return '""';
  if (!/[\s"&|<>^()%]/.test(argument)) return argument;
  return `"${argument.replaceAll('"', '""')}"`;
}

function commandLine(executable, args) {
  return [executable, ...args].map(quote).join(" ");
}

function needsShell(command) {
  return command === "pnpm" || /\.(?:cmd|bat)$/i.test(command);
}

function shellOptions(command, args, options) {
  return needsShell(command)
    ? [commandLine(command, args), { ...options, shell: true }]
    : [command, args, options];
}

export function toolSpawnSync(command, args, options) {
  if (!isWindows) return spawnSync(command, args, options);
  return spawnSync(...shellOptions(command, args, options));
}

export function toolSpawn(command, args, options) {
  if (!isWindows) return spawn(command, args, options);
  return spawn(...shellOptions(command, args, options));
}

export function pnpmSpawnSync(args, options) {
  return toolSpawnSync("pnpm", args, options);
}
