// CUI // SP-CTI
// ICDEV guard for opencode (omx-guard-01).
//
// Every tool call is handed to ICDEV's PreToolUse guard -- the SAME checks the
// Claude Code hook runs (tools/hooks/shared_checks.py), reached through
// `python -m tools.hooks.harness_guard --harness opencode`, which maps
// opencode's spelling onto ICDEV's and calls
// tools/airgap/hook_compat.run_pre_tool_check. This file holds no check.
//
// A deny is a throw: opencode records the tool call as an error carrying the
// check's reason and the run continues. A bridge that cannot be run, times out
// or prints no verdict FAILS OPEN (as .claude/hooks/pre_tool_use.py main()
// does) and is logged to stderr and to .opencode/icdev-guard.log.
//
// Kill switches are the hook's own environment variables
// (ICDEV_PRETOOLUSE_ENFORCE, ICDEV_<CHECK>_GUARD); the bridge reads them.
// Never run opencode with --pure: it skips this plugin (measured, omx-spike-01).
//
// Installed by `icdev harness install-guard opencode`, which fills in the two
// defaults below; ICDEV_PYTHON / ICDEV_ROOT override them at run time.
import type { Plugin } from "@opencode-ai/plugin"
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
    mkdirSync(join(directory, ".opencode"), { recursive: true })
    appendFileSync(join(directory, ".opencode", "icdev-guard.log"), line + "\n")
  } catch {
    // the stderr line above is the record
  }
  return { allowed: true, reason: why }
}

function verdict(directory: string, tool: string, args: unknown): Verdict {
  const r = spawnSync(PYTHON, ["-m", "tools.hooks.harness_guard", "--harness", "opencode"], {
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

export const IcdevGuard: Plugin = async ({ directory }) => ({
  "tool.execute.before": async (input, output) => {
    const v = verdict(directory, input.tool, output.args)
    if (v.advisory) console.error(`ICDEV guard: ${v.reason}`)
    if (!v.allowed) throw new Error(`ICDEV guard: ${v.reason}`)
  },
})
