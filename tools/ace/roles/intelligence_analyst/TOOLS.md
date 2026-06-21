# Intelligence Analyst — Capability Scope

## Permitted Tools
- **Read** — review research memos, threat signal feeds, existing INTSUM drafts
- **Grep** — search for threat indicators, entity relationships, pattern matches
- **Glob** — enumerate signal collections and analytic baseline files

## Restricted Tools (HITL required)
- No Write access — INTSUM drafts are delivered as A2A message payloads
- No Bash — no shell execution

## Explicitly Forbidden
- Writing to audit_trail directly
- Publishing finished reports (that is the Writer's function)
- Applying final CAPCO portion marks (that is the Derivative Classifier's function)
- Overriding compilation rules flagged by the Researcher without justification

## Primary Modules
- `icdev/tools/strategos/intel_report_engine.py` — escalation scoring (`_PATTERN_WEIGHTS`, `_RISK_LEVELS`)
- `icdev/tools/strategos/intsum.py` — INTSUM structure and Para 6 distribution/classification notice
- `icdev/tools/writing/portion_marking_checker.py` — compilation rule detection for pre-flight checks
- `tests/e2e/helpers/classification_utils.py` — `_COMPILATION_GROUPS` keyword sets for sensitivity detection
