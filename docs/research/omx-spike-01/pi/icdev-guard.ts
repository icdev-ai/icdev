// omx-spike-01 step 3 (Pi): delegate EVERY tool call to ICDEV's guard
// (tools/airgap/hook_compat.py::run_pre_tool_check via icdev_guard_bridge.py).
// Fail closed: bridge error / timeout / garbage = block.
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { spawnSync } from "node:child_process";
import { appendFileSync } from "node:fs";

const PY = process.env.OMX_PYTHON ?? (process.platform === "win32" ? "python" : "python3");
const BRIDGE = process.env.OMX_GUARD_BRIDGE ?? "";

function verdict(tool: string, args: unknown): { allowed: boolean; reason: string } {
  const r = spawnSync(PY, [BRIDGE], {
    input: JSON.stringify({ harness: "pi", tool, args }),
    encoding: "utf-8",
    timeout: 15000,
  });
  if (r.status !== 0) return { allowed: false, reason: `guard bridge failed (exit ${r.status}): ${r.stderr?.slice(-300)}` };
  try {
    return JSON.parse(r.stdout.trim().split("\n").pop() ?? "");
  } catch {
    return { allowed: false, reason: "guard bridge printed no verdict" };
  }
}

export default function (pi: ExtensionAPI) {
  pi.on("tool_call", async (event, ctx) => {
    const v = verdict(event.toolName, (event as any).input);
    appendFileSync(`${ctx.cwd}/hook-input.jsonl`, JSON.stringify({ event, verdict: v }) + "\n");
    if (!v.allowed) return { block: true, reason: `ICDEV guard: ${v.reason}` };
  });
}
