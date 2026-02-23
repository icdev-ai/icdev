---
mode: agent
description: "Query, search, and update the ICDEV learning knowledge base for patterns, solutions, and recommendations"
tools:
  - terminal
  - file_search
---

# icdev-knowledge

Manages the self-learning knowledge base that powers self-healing:
1. Search for known patterns, solutions, and best practices
2. Add new patterns discovered during development/operations
3. Get project-specific improvement recommendations
4. Analyze failures to determine root cause
5. View knowledge base statistics and health

## Example
```
#prompt:icdev-knowledge search "database connection timeout"
#prompt:icdev-knowledge add "OOM Kill Pattern"
#prompt:icdev-knowledge recommend abc123-uuid
#prompt:icdev-knowledge analyze "ConnectionRefusedError: [Errno 111] Connection refused"
#prompt:icdev-knowledge stats
```