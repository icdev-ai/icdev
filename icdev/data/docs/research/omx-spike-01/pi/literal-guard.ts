// omx-spike-01 step 2 (Pi): refuse any bash call containing the
// recursive-force rm literal. Pure Pi -- no ICDEV. A tool_call handler that
// returns {block:true} refuses the call; a handler that THROWS also blocks
// ("fail-safe", docs/extensions.md).
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { appendFileSync } from "node:fs";

const NEEDLE = ["rm", "-rf"].join(" ");

export default function (pi: ExtensionAPI) {
  pi.on("tool_call", async (event, ctx) => {
    appendFileSync(`${ctx.cwd}/hook-input.jsonl`, JSON.stringify(event) + "\n");
    const cmd = String((event as any).input?.command ?? "");
    if (event.toolName === "bash" && cmd.includes(NEEDLE)) {
      return { block: true, reason: `omx-spike literal guard: refused bash call containing '${NEEDLE}'` };
    }
  });
}
