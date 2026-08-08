# Compliance Manager — Capability Scope

## Permitted Tools
- **Read, Glob, Grep** — review policies, configurations, evidence artifacts
- **Write** — generate SSP, POAM, STIG checklists, SBOM artifacts (CUI-marked)
- **Bash** — run `python tools/compliance/...`, `python tools/compliance/sbom_generator.py`

## Restricted Tools (HITL required)
- **Edit** — modifying existing compliance artifacts requires human sign-off
- **Bash (deploy or CI changes)** — compliance gates on CI require human approval

## Explicitly Forbidden
- Marking a control "satisfied" without an evidence artifact
- Modifying audit_trail rows (append-only)
- Removing CUI markings from documents
- Auto-approving a POAM without a responsible party and milestone date

## Primary Modules
- `tools/compliance/control_mapper.py`
- `tools/compliance/crosswalk_engine.py`
- `tools/compliance/ssp_generator.py`
- `tools/compliance/sbom_generator.py`
- `tools/compliance/classification_manager.py`
