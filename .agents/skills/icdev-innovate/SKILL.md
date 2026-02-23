---
name: icdev-innovate
description: "Run the ICDEV Innovation Engine — autonomous self-improvement through web intelligence, introspective analysis, competitive monitoring, and standards tracking."
allowed-tools: ["Bash", "Read", "Write", "Edit", "Glob", "Grep", "Task", "TodoWrite"]
---

# $icdev-innovate

## Error Handling
- If web scan fails for a source → continues with other sources, logs error
- If database tables missing → returns error with migration instructions
- If air-gapped → skips web sources, runs introspective analysis only
- If rate limited → backs off, retries on next cycle
- If budget exceeded → logs signal for next PI, skips generation