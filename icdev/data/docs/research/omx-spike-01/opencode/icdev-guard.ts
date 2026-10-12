// omx-spike-01 step 3: delegate EVERY tool call to ICDEV's guard
// (tools/airgap/hook_compat.py::run_pre_tool_check via icdev_guard_bridge.py)
// and refuse on deny. Fail closed: a bridge that errors, times out or prints
// garbage is a DENY.
import type { Plugin } from "@opencode-ai/plugin"
import { spawnSync } from "node:child_process"
import { appendFileSync } from "node:fs"

const PY = process.env.OMX_PYTHON ?? (process.platform === "win32" ? "python" : "python3")
const BRIDGE = process.env.OMX_GUARD_BRIDGE ?? ""

function verdict(tool: string, args: unknown): { allowed: boolean; reason: string } {
  const r = spawnSync(PY, [BRIDGE], {
    input: JSON.stringify({ harness: "opencode", tool, args }),
    encoding: "utf-8",
    timeout: 15000,
  })
  if (r.status !== 0) return { allowed: false, reason: `guard bridge failed (exit ${r.status}): ${r.stderr?.slice(-300)}` }
  try {
    return JSON.parse(r.stdout.trim().split("\n").pop() ?? "")
  } catch {
    return { allowed: false, reason: "guard bridge printed no verdict" }
  }
}

export const IcdevGuard: Plugin = async ({ directory }) => ({
  "tool.execute.before": async (input, output) => {
    const v = verdict(input.tool, output.args)
    appendFileSync(`${directory}/hook-input.jsonl`, JSON.stringify({ input, output, verdict: v }) + "\n")
    if (!v.allowed) throw new Error(`ICDEV guard: ${v.reason}`)
  },
})
