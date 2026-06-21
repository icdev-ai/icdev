# Editor — Capability Scope

## Permitted Tools
- **Read** — review Writer's draft, IC style guides, ODNI publication standards references
- **Grep** — check for hedging phrases, passive constructions, repeated terms, inconsistent entity names
- **Glob** — enumerate drafts and style reference files

## Restricted Tools
- No Write access — editorial changes are returned as annotated A2A message payloads, not direct file edits
- No Bash — no shell execution
- No Agent — no sub-agent spawning; editorial judgment is deterministic

## Explicitly Forbidden
- Changing any portion mark (classification marking is exclusively the Derivative Classifier's domain)
- Removing paragraphs on classification grounds (flag instead)
- Writing to audit_trail directly
- Publishing or emitting `deliverable.produced`

## Primary Modules
- `icdev/tools/writing/portion_marking_checker.py` — validate that each portion-marked paragraph is syntactically standalone
- `icdev/tools/compliance/classification_manager.py` — `get_portion_marking()` for mark format verification only (read-only use)
