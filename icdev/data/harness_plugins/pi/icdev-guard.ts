// CUI // SP-CTI
// ICDEV guard for Pi (omx-guard-03).
//
// Every tool call is handed to ICDEV's PreToolUse guard -- the SAME checks the
// Claude Code hook runs (tools/hooks/shared_checks.py), reached through
// `python -m tools.hooks.harness_guard --harness pi`, which maps Pi's spelling
// (lowercase tool names, `path`, `edits[]`) onto ICDEV's and calls
// tools/airgap/hook_compat.run_pre_tool_check. This file holds no check.
//
// A deny is `{ block: true, reason }`: Pi records the tool call as an error
// (`tool_execution_end.isError`) carrying the check's reason and the run
// continues. Nested calls a tool issues through ctx.executeTool also pass
// through `tool_call`, so codemode cannot step around it (omx-spike-01).
//
// A bridge that cannot be run, times out or prints no verdict FAILS OPEN (as
// .claude/hooks/pre_tool_use.py main() does) and is logged to stderr and to
// .pi/icdev-guard.log. Pi treats a THROWING handler as a block, so every
// failure is caught here and turned into an allow: failing open is a choice
// this file makes, not something Pi does for it.
//
// Kill switches are the hook's own environment variables
// (ICDEV_PRETOOLUSE_ENFORCE, ICDEV_<CHECK>_GUARD); the bridge reads them.
// `--no-extensions` does NOT drop an explicit `-e` (measured, omx-spike-01),
// which is how the pi_cli adapter passes this file.
//
// Installed by `icdev harness install-guard pi`, which fills in the two
// defaults below; ICDEV_PYTHON / ICDEV_ROOT override them at run time.
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent"
import { spawnSync } from "node:child_process"
import { appendFileSync, mkdirSync } from "node:fs"
import { join } from "node:path"

const PYTHON = process.env.ICDEV_PYTHON || "__ICDEV_PYTHON__"
const ROOT = process.env.ICDEV_ROOT || "__ICDEV_ROOT__"
const TIMEOUT_MS = Number(process.env.ICDEV_GUARD_TIMEOUT_MS || 30000)

type Verdict = { allowed: boolean; reason: string; advisory?: boolean }

function failOpen(directory: string, why: string): Verdict {
  const line = `${new Date().toISOString()} ICDEV guard bridge FAILED OPEN: ${why}`
  console.error(line)
  try {
    mkdirSync(join(directory, ".pi"), { recursive: true })
    appendFileSync(join(directory, ".pi", "icdev-guard.log"), line + "\n")
  } catch {
    // the stderr line above is the record
  }
  return { allowed: true, reason: why }
}

function verdict(directory: string, tool: string, args: unknown): Verdict {
  const r = spawnSync(PYTHON, ["-m", "tools.hooks.harness_guard", "--harness", "pi"], {
    cwd: ROOT,
    input: JSON.stringify({ tool, args }),
    encoding: "utf-8",
    timeout: TIMEOUT_MS,
    env: { ...process.env, PYTHONPATH: [ROOT, process.env.PYTHONPATH].filter(Boolean).join(process.platform === "win32" ? ";" : ":") },
  })
  if (r.error) return failOpen(directory, `could not run ${PYTHON}: ${r.error.message}`)
  if (r.status !== 0) return failOpen(directory, `bridge exited ${r.status}: ${(r.stderr || "").slice(-300)}`)
  try {
    const v = JSON.parse((r.stdout || "").trim().split("\n").pop() || "")
    if (typeof v?.allowed !== "boolean") throw new Error("no 'allowed' field")
    return v
  } catch (e) {
    return failOpen(directory, `bridge printed no verdict: ${(e as Error).message}`)
  }
}

export default function (pi: ExtensionAPI) {
  pi.on("tool_call", async (event, ctx) => {
    let v: Verdict
    const directory = (ctx && ctx.cwd) || process.cwd()
    try {
      v = verdict(directory, event.toolName, event.input)
    } catch (e) {
      v = failOpen(directory, `guard extension error: ${(e as Error).message}`)
    }
    if (v.advisory) console.error(`ICDEV guard: ${v.reason}`)
    if (!v.allowed) return { block: true, reason: `ICDEV guard: ${v.reason}` }
    return undefined
  })
}
