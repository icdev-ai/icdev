---
name: icdev-knowledge
description: Query, search, and update the ICDEV learning knowledge base for patterns, solutions, and recommendations
allowed-tools: Bash, Read, Write, Edit, Glob, Grep
---

# $icdev-knowledge

## What This Does
Manages the self-learning knowledge base that powers self-healing:
1. Search for known patterns, solutions, and best practices
2. Add new patterns discovered during development/operations
3. Get project-specific improvement recommendations
4. Analyze failures to determine root cause
5. View knowledge base statistics and health

## Example
```
$icdev-knowledge search "database connection timeout"
$icdev-knowledge add "OOM Kill Pattern"
$icdev-knowledge recommend abc123-uuid
$icdev-knowledge analyze "ConnectionRefusedError: [Errno 111] Connection refused"
$icdev-knowledge stats
```

## Error Handling
- If knowledge DB empty: suggest running /icdev-secure and /icdev-test to generate initial patterns
- If search returns no results: suggest broader query terms
- If pattern already exists (by name): update existing instead of duplicate