---
name: icdev-innovate
description: "Runs the ICDEV™ Innovation Engine to scan the web, analyze the codebase introspectively, monitor competitors, and propose improvement signals for the next program increment. Use when triggering an autonomous self-improvement cycle, discovering new patterns from external sources, or generating innovation backlog items from introspective codebase analysis."
allowed-tools: ["Bash", "Read", "Write", "Edit", "Glob", "Grep", "Task", "TodoWrite"]
---

# $icdev-innovate

## Error Handling
- If web scan fails for a source → continues with other sources, logs error
- If database tables missing → returns error with migration instructions
- If air-gapped → skips web sources, runs introspective analysis only
- If rate limited → backs off, retries on next cycle
- If budget exceeded → logs signal for next PI, skips generation