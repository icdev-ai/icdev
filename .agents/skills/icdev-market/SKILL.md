---
name: icdev-market
description: "Manage the ICDEV Federated GOTCHA Asset Marketplace — publish, install, search, review, and sync skills, goals, hardprompts, context, args, and compliance extensions across tenant organizations."
allowed-tools: ["Bash", "Read", "Write", "Edit", "Glob", "Grep", "Task", "TodoWrite"]
---

# $icdev-market

## Error Handling
- If publish fails on scanning: show which gate failed and specific findings
- If install fails on IL: show consumer IL vs asset IL with allowed levels
- If review is rejected: show rationale and suggest fixes
- If search returns no results: suggest broader query or different filters