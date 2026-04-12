---
name: icdev-market
description: "Manage the ICDEV™ Federated FORGE Asset Marketplace — publish, install, search, and sync skills and compliance extensions. Use when publishing or installing marketplace assets."
allowed-tools: ["Bash", "Read", "Write", "Edit", "Glob", "Grep", "Task", "TodoWrite"]
---

# $icdev-market

## Error Handling
- If publish fails on scanning: show which gate failed and specific findings
- If install fails on IL: show consumer IL vs asset IL with allowed levels
- If review is rejected: show rationale and suggest fixes
- If search returns no results: suggest broader query or different filters