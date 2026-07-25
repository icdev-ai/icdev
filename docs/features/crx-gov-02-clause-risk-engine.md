# CUI // SP-CTI

# Contract Clause Risk Engine (crx-gov-02)

Deterministic-first clause risk analysis for incoming solicitations / contracts.
Closes gap #3 of the govcon_cpmp review findings.

## What shipped

| Component | Path |
|-----------|------|
| Rulebook (regex clause/indicator catalog + toxic-combination risk rules) | `args/govcon/clause_risk_rules.yaml` |
| Engine (extract → indicators → rules → score → optional gated LLM narrative) | `tools/govcon/clause_risk_engine.py` |
| API surface (capture/opportunity view) | `POST /api/govcon/opportunities/<id>/clause-risk` in `tools/dashboard/api/govcon.py` |
| UI surface | "⚖ Risk" badge + modal on `tools/dashboard/templates/govcon/pipeline.html` |
| Tests | `tests/govcon/test_clause_risk_engine.py` (11 tests) |
| Manifest | `tools/manifest/govcon.md` |

All `tools/` artifacts are mirrored to the `icdev/` package tree.

## Design (deterministic-first)

1. **Clause extraction** reuses `far_dfars_verifier.detect_clauses()` — the existing
   FAR/DFARS clause catalog (no duplication; graceful if unavailable).
2. **Indicator detection** — regex indicators from the YAML rulebook, modeled on the
   DocMod rulebook shape (`args/docmod/rulebook_*.yaml`).
3. **Risk rules** — deterministic combination rules (e.g. `rule-ffp-unbounded-scope`,
   `rule-unlimited-liability`, `rule-ld-ffp-unbounded`) produce severity + rationale +
   mitigation. **These findings alone determine the numeric score.**
4. **LLM narrative** is OPTIONAL and GATED behind the deterministic pass. It is handed
   the already-computed score/findings and asked only to EXPLAIN them. It never
   re-scores, and it degrades silently to `None` when no provider is configured
   (`LLMRouter.has_any_llm()` guard, `invoke(function, LLMRequest)` API).

## TRUST

Every indicator and rule carries its FAR/DFARS `clause_source`; every finding emits an
inline `[source: rule:<id>]` citation. Rules are seeded ONLY from public FAR/DFARS
knowledge (acquisition.gov). Test fixtures use synthetic solicitation text — no customer
or contract data. Assessments persist to `govcon_clause_risk_assessments`
(tenant_id + classification columns) with an append-only `audit_trail` entry.

## Dispositions recorded (rest of govcon_cpmp findings)

- **Subcontract management** — largely COVERED. CPMP already tracks subcontractors and
  the FAR 52.219-9 small-business subcontracting obligation via
  `tools/govcon/subcontractor_tracker.py`. No new build; monitored under CPMP.
- **Orals / oral-presentation support** — should COMPOSE the VIZ slides generator
  (`tools/viz/`, `/presentation`) rather than a bespoke govcon slide path. Recorded as a
  compose-not-build disposition; no duplicate slide engine.

# CUI // SP-CTI
