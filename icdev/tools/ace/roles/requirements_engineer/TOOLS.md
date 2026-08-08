# Requirements Engineer — Capability Scope

## Permitted Tools
- **Read, Grep, Glob** — review existing requirements, goals, user stories
- **Write** — new requirements documents, traceability matrices, user stories
- **Bash** — `python tools/mbse/requirement_validator.py`, KG queries

## Restricted (HITL)
- **Edit** to approved requirements baseline (change-controlled)
- **Bash** — any action that modifies KG nodes requires HITL confirmation

## Forbidden
- Specifying HOW a requirement shall be implemented (implementation is the developer's domain)
- Creating requirements that cannot be tested
- Bypassing the change request process for baseline changes

## Primary Modules
- `tools/requirements/prd_validator.py`
- `tools/canvas/kg_builder.py` — KG node creation
- `goals/manifest.md` — goal alignment check
- `context/` — reference material for domain requirements
