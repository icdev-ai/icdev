// omx-spike-01 step 2: refuse any bash call containing the recursive-force rm
// literal. Pure opencode -- no ICDEV involved. Throwing from
// tool.execute.before is opencode's documented way to refuse a call.
import type { Plugin } from "@opencode-ai/plugin"
import { appendFileSync } from "node:fs"

const NEEDLE = ["rm", "-rf"].join(" ")

export const LiteralGuard: Plugin = async ({ directory }) => ({
  "tool.execute.before": async (input, output) => {
    appendFileSync(`${directory}/hook-input.jsonl`, JSON.stringify({ input, output }) + "\n")
    if (input.tool === "bash" && String(output.args?.command ?? "").includes(NEEDLE)) {
      throw new Error(`omx-spike literal guard: refused bash call containing '${NEEDLE}'`)
    }
  },
})
