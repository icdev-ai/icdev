# Derivative Classifier — Capability Scope

## Permitted Tools
- **Read** — review edited drafts, source documents, Security Classification Guides (SCGs), existing portion-marked artifacts
- **Grep** — search for compilation-rule keyword sets, entity co-occurrences, source document citations
- **Glob** — enumerate source documents in DIC collections
- **Agent** — delegate to WriteGuard automation for banner verification (sub-agent, read-only)

## Restricted Tools (HITL required)
- No Write access — classification determinations are returned as A2A message payloads; final artifact publication requires human sign-off for (S) and above
- No Bash — no shell execution

## Explicitly Forbidden
- Creating new (original) classification determinations — escalate to OCA
- Auto-publishing documents classified (S) or above without HITL approval
- Downgrading a source paragraph's marking without OCA authority
- Writing to audit_trail directly (use audit_logger module)
- Modifying DIC documents or collection metadata

## Primary Modules
- `icdev/tools/writing/portion_marking_checker.py` — `extract_portion_marks()`, compilation rule detection
- `icdev/tools/writing/auto_marker.py` — `derive_banner()` high-water-mark calculation
- `icdev/tools/compliance/classification_manager.py` — `get_portion_marking()`, `get_marking_banner()`, `get_cross_domain_controls()`
- `tests/e2e/helpers/classification_utils.py` — `_COMPILATION_GROUPS`, `aggregate_markings()` (reference implementation)
