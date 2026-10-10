"""Print a compact view of a `pi --mode json` stream (omx-spike-01)."""
import json
import sys
for line in open(sys.argv[1], encoding="utf-8"):
    e = json.loads(line); t = e["type"]
    if t in ("message_update", "tool_execution_update", "message_start", "turn_start"):
        continue
    if t == "tool_execution_start":
        print(t, e["toolName"], json.dumps(e["args"]))
    elif t == "tool_execution_end":
        txt = " ".join(c.get("text", "") for c in e["result"].get("content", []))
        print(t, e["toolName"], "isError=", e.get("isError"), "|", txt[:220].replace("\n", " "))
    elif t == "message_end":
        m = e["message"]
        if m["role"] == "assistant":
            txt = " ".join(c.get("text", "") for c in m["content"] if c.get("type") == "text")
            print(t, "assistant stopReason=", m.get("stopReason"), "usage=", json.dumps(m.get("usage"))[:260], "|", txt[:200].replace("\n", " "), "| error=", m.get("errorMessage"))
    elif t in ("agent_end",):
        print(t, "willRetry=", e.get("willRetry"), "n_messages=", len(e.get("messages", [])))
    else:
        print(t, json.dumps(e)[:200])
