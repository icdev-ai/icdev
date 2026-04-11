---
name: icdev-market
description: "Manages the ICDEV™ Federated FORGE Asset Marketplace — publishes, installs, searches, reviews, and syncs skills, goals, hardprompts, context, args, and compliance extensions across tenants. Use when publishing a new FORGE asset to the marketplace, installing a skill or extension from another tenant, searching for reusable components, or reviewing a pending marketplace submission."
allowed-tools: ["Bash", "Read", "Write", "Edit", "Glob", "Grep", "Task", "TodoWrite"]
---

# $icdev-market

## Error Handling
- If publish fails on scanning: show which gate failed and specific findings
- If install fails on IL: show consumer IL vs asset IL with allowed levels
- If review is rejected: show rationale and suggest fixes
- If search returns no results: suggest broader query or different filters