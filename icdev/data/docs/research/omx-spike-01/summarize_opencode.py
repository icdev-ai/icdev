"""Print a compact view of an opencode --format json stream (omx-spike-01)."""
import json
import sys
for line in open(sys.argv[1], encoding="utf-8"):
    e = json.loads(line); p = e.get("part", {}) or {}
    t = e["type"]
    if t == "tool_use":
        st = p.get("state", {})
        print(t, p.get("tool"), st.get("status"), json.dumps(st.get("input")), "|", str(st.get("error") or st.get("output"))[:240].replace("\n", " "))
    elif t == "text":
        print(t, (p.get("text") or "")[:260].replace("\n", " "))
    elif t == "step_finish":
        print(t, p.get("reason"), json.dumps(p.get("tokens")), "cost=", p.get("cost"))
    else:
        print(t, json.dumps({k: v for k, v in e.items() if k not in ("part",)})[:240])
