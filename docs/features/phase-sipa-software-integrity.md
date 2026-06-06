# CUI // SP-CTI

# SIPA — Software Integrity & Provenance Assessor (Implementation Plan)

**Status:** PLANNED — seeded to Kanban as project `sipa` (task_prefix `sipa-`) for autonomous
implementation. Source plan: `C:/Users/schuo/.claude/plans/i-want-to-work-quiet-hejlsberg.md`.

## Problem

A human or an AI agent can embed malicious code or an unauthorized workload that is **not part of
the original requirement/PRD** — in whole or in part. ICDEV™ needs a capability that scans and
assesses software integrity across three populations: (1) ICDEV's own code, (2) contributed code
(PRs from others), and (3) **external software** fetched by GitHub/GitLab URL, UNC path, or URI.

The assessment must work **whether or not we have the requirement/PRD**:

- **Mode A — Provenance-aware (PRD known):** verify every code behavior traces to an *approved*
  requirement. Unauthorized = a capability with no authorizing requirement ("semantic backdoor").
- **Mode B — Provenance-blind (external, no PRD):** no requirement to compare against, so reconcile
  **actual behavior** (a capability manifest extracted from the code) against **claimed behavior**
  (README/docstrings/declared purpose), plus intrinsic-risk scoring, known-bad signatures,
  dependency provenance, secrets, and a tamper digest.

## Decisions (locked)

| Decision | Choice |
|----------|--------|
| Packaging | **New dedicated component** `tools/integrity/` + dashboard canvas `/integrity` + gate |
| Execution model | **Static analysis only** — target code is never executed (dynamic sandbox is a future phase) |
| Enforcement | **Quarantine-first + HITL blocking gate** (mirrors `openclaw_bridge`) |
| Intent engine | **Deterministic core + optional LLM-assist** via the router, with deterministic fallback |

## Architecture

Pipeline — `tools/integrity/engine.py :: assess(source, mode=auto, project_id=None, session_id=None)`:

1. **Ingest → quarantine** (`ingest.py`) — stage local/UNC/URI/git into
   `.tmp/integrity_quarantine/<assessment_id>/`; scheme allowlist; `git clone --depth 1` with a
   fixed arg list, **no `shell=True`**; SHA-256 dir digest via `blueprint_verifier`. Never executes.
2. **Scanner fan-out** (`scanners.py`) — thin adapters over `tools/security/{sast_runner,
   secret_detector,dependency_auditor,container_scanner}.py`, `tools/analysis/formal_verifier.py`,
   and `tools/aiify/pattern_classifier.py` (new malicious-signature Semgrep rules). Normalize all
   results into `integrity_findings`.
3. **Capability manifest (NEW core)** (`capability_extractor.py`) — AST → per file/function
   capability set: `network_egress`, `filesystem`, `process_exec`, `dynamic_code`, `crypto`,
   `env_secret`, `serialization`, `obfuscation` + evidence/lines. Multi-language via Semgrep.
4. **Intent reconciliation (NEW core)** (`intent_reconciler.py`, `claim_parser.py`) —
   Mode A: manifest vs RTM-derived *allowed-capability* set (`traceability_builder.build_rtm`) →
   `unauthorized_capability`. Mode B: manifest vs *claimed* capability → `undisclosed_capability`.
   Optional LLM second opinion (router function `intent_reconciliation`), deterministic fallback.
5. **Scoring + verdict (NEW)** (`scoring.py`) — combine findings + capability weights +
   reconciliation gap + tamper status → 0–100 → **ALLOW / REVIEW / QUARANTINE**.
6. **Persist + gate + HITL** (`engine.py`, `blueprint.py`) — append-only persistence; `--gate`
   non-zero on QUARANTINE; promote/reject HITL mirroring `openclaw_bridge.promote_import`.

### Data model (`tools/integrity/db/init_db.py`, PG default + SQLite fallback, RLS columns)
`integrity_assessments`, `integrity_capabilities`*, `integrity_findings`*, `integrity_verdicts`*,
`integrity_authorizations`* (* = append-only). Every table carries `tenant_id` + `classification`
and uses `get_connection()` (RLS-aware). CHECK constraints derived from `constants.py`.

### Reuse (do not rebuild)
`openclaw_bridge` (quarantine), the `tools/security/*` scanner fleet, `formal_verifier`,
`pattern_classifier` (Semgrep), `blueprint_verifier` (tamper), `traceability_builder` + RTM tables,
`prov_recorder` + `source_citation_registry`, `audit_logger`, `LLMRouter`, `get_connection`.

## Epics & atomic tasks (seeded to Kanban — project `sipa`)

| Epic | Tasks | Theme |
|------|-------|-------|
| `db` | sipa-db-01..04 | constants, dual-schema DB, init_icdev_db + migration + conftest, append-only + toggle + config |
| `ingest` | sipa-ingest-01..03 | quarantine staging, git clone (no shell), tamper digest |
| `scan` | sipa-scan-01..03 | SAST/secrets/deps adapters, formal/container, malicious Semgrep rules |
| `cap` | sipa-cap-01..03 | capability manifest (network/fs/proc), (dynamic/crypto/secret/obfusc), multi-lang |
| `intent` | sipa-intent-01..04 | claim parser, Mode B reconcile, Mode A (RTM), LLM-assist fallback |
| `score` | sipa-score-01 | risk score + verdict |
| `engine` | sipa-engine-01..04 | orchestration, CLI+gate, HITL promote/reject, security-gate + sandbox-coverage |
| `prov` | sipa-prov-01 | PROV linkage + authorizing-requirement edges |
| `dash` | sipa-dash-01..04 | blueprint+routes, templates+mirror, app/nav register, IQE |
| `mcp` | sipa-mcp-01 | MCP tool registration |
| `reflex` | sipa-reflex-01 | Genesis self-assessment drift reflex |
| `doc` | sipa-doc-01 | goal workflow + manifest shard + commands.md |
| `vv` | sipa-vv-01..04 | **CodeLens**, **Coherence**, sync+health, **Playwright E2E** |

Dependencies form a single chain (`depends_on_task_id`, honored by the dispatcher) with secondary
cross-epic prerequisites named in each task description.

## Verification (sipa-vv-*)
- **CodeLens** on every `tools/integrity/` module → all PASS (maintainability ≥ 0.6, no critical smells).
- **Coherence** `--all --fix --gate` green (schema↔code, manifest, append-only, routes, sandbox-coverage,
  security_context, IQE) + `ruff` clean + full `pytest` green.
- Companion sync (**foreground**) + `health_check`.
- **Playwright E2E**: assess a planted-backdoor fixture → verdict `QUARANTINE`; benign → `ALLOW`;
  IQE seed query returns rows; screenshots → `playwright/screenshots/integrity-*.png`.

## PR-diff gate fix — deps scan scoped to changed subset (eqo-sipa-s2)

From `eqo-vv-01` V&V: a benign 1-line staged `*.py` change was gated to
`QUARANTINE` (risk 100) by ~49 repo-wide `vuln_dependency` findings (ambient
aiohttp CVEs) — **zero** of which were introduced by the changed file. The PR gate
therefore failed "passes-on-benign": it blocked *every* change.

**Root cause.** `tools/security/dependency_auditor.py::audit_python` invoked
`pip-audit` with **no `--requirement`** when the project path held no
`requirements.txt` / `pyproject.toml`. Bare `pip-audit` audits the *entire
installed Python environment*, not the target. The SIPA PR-diff gate stages only
the changed `*.py` files (a manifest-less subtree), so every run fell through to
the whole-environment audit and surfaced ambient CVEs unrelated to the diff.

**Fix (two parts, both bounded to the deps path):**
1. `audit_python` now **skips cleanly** (zero findings, `success=True`) when the
   project path carries no Python dependency manifest, instead of auditing the
   whole environment. A "scan THIS project" call can no longer silently pivot to
   "scan the whole machine". Mirrored to `icdev/tools/security/dependency_auditor.py`.
2. `tools/integrity/pr_gates.py` now also stages **changed dependency manifests**
   (`requirements.txt`, `pyproject.toml`, `setup.py`, `package.json`, `go.mod`,
   `Cargo.toml`, `pom.xml`, `*.csproj`, …) alongside the changed `*.py` files. A PR
   that genuinely bumps a dependency has *that manifest* audited — scoped to the
   changed subset — while a code-only change stages no manifest and the deps
   scanner has nothing to audit.

**Net effect:** benign code-only changes → deps scanner finds nothing → no false
`QUARANTINE`; dependency bumps → the changed manifest is still assessed.

**Tests:** `tests/test_dependency_auditor.py` (manifest-less skip never invokes
pip-audit; with `requirements.txt` the audit is pinned to it) and
`tests/test_integrity_pr_gates.py::test_changed_dependency_manifest_is_assessed`
/ `::test_benign_code_change_stages_no_manifest`.
