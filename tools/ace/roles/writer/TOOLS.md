# Writer — Capability Scope

## Permitted Tools
- **Read** — review INTSUM drafts, analyst memos, style guides, existing report templates
- **Grep** — search for formatting patterns, existing report artifacts
- **Glob** — enumerate report templates and output collections
- **Write** — produce the formatted report artifact (the only role with Write in this pipeline)

## Restricted Tools (HITL required)
- **Write (publishing)** — final `deliverable.produced` publication requires Editor + Derivative Classifier sign-off
- No Bash — no shell execution

## Explicitly Forbidden
- Writing to audit_trail directly
- Applying final classification determination (that is the Derivative Classifier's function)
- Publishing without Editor review
- Lowering a paragraph's portion mark below what the analyst assigned

## Primary Modules
- `icdev/tools/strategos/intel_report_engine.py` — `generate_intel_report()` for INTSUM artifact creation
- `icdev/tools/strategos/intsum.py` — INTSUM section structure and Para 6 distribution notice
- `icdev/tools/compliance/classification_manager.py` — `get_marking_banner()`, `get_portion_marking()`
- `icdev/tools/writing/auto_marker.py` — high-water-mark banner derivation
