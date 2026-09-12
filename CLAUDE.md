# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

---

## Quick Reference

### Essential Commands
```bash
# Initialize framework (first run)
/initialize

# Session start
python tools/memory/memory_read.py --format markdown
python tools/project/session_context_builder.py --format markdown

# ICDEV™ CLI entry points
icdev init [target]               # Scaffold new project (CLAUDE.md + FORGE data + .claude/ + .env)
icdev enable <name> [...]         # Turn on canvas / subsystem toggles in .env
icdev disable <name> [...]        # Turn off toggles
icdev status [--json]              # Show active toggles
icdev list [--json]               # List supported toggles
icdev scaffold canvas <key> --display-name "Name" [--flavor <flavor>]   # Generate a new canvas from a Jinja2 template
icdev scaffold child-app <key> --display-name "Name" --flavor <flavor> [--canvases k1,k2]  # Generate a new child app from a Jinja2 template
icdev profile list                 # List enterprise core profiles
icdev profile show [<name>]        # Show active/core profile details
icdev profile apply <name>         # Apply a profile's env overrides to .env

# Testing
pytest tests/ -v --tb=short      # Run all platform tests (~330+ tests; SQLite forced by conftest)
pytest tests/test_<name>.py -v   # Run a single test file
behave features/                  # BDD / Gherkin scenario tests
python tools/testing/health_check.py --json
python tools/testing/test_orchestrator.py --project-dir /path/to/project
python tools/testing/e2e_runner.py --run-all

# Lint / quality
ruff check .                      # Ultra-fast Python linter (replaces flake8+isort+black)
ruff check <file>
python -m bandit -r tools/ --severity-level medium

# Frontend (OpenAPI codegen)
cd frontend && npm run codegen    # Regenerate lib/api-types.ts from localhost:5050

# Memory
python tools/memory/memory_write.py --content "text" --type event
python tools/memory/hybrid_search.py --query "query"

# LLM Provider
python -c "from tools.llm.router import LLMRouter; r = LLMRouter(); print(r.get_provider_for_function('code_generation'))"
# Config: args/llm_config.yaml — providers, models, routing, embeddings

# Database
python tools/db/init_icdev_db.py
python tools/db/storage.py --health --json

# Companion sync (ALWAYS after code changes)
python tools/dx/companion.py --sync --write --json

# Coherence check — tiered (fast = per-task gate, full = nightly sweep)
python tools/workflow/coherence_checker.py --all --fix --gate                       # full tier + autofix
python tools/workflow/coherence_checker.py --tier fast --gate --changed-files "tools/foo.py"
python tools/workflow/coherence_checker.py --tier fast --list-tier                  # which checks the tier runs
python tools/genesis/reflexes/coherence_sweep.py                                    # full-tier sweep + baseline refresh
python tools/workflow/coherence_checker.py --check capability_liveness --gate       # declared-but-never-consumed capabilities

# Showcase / Demo Runner
python tools/showcase/ai_canvas_demo_runner.py --scenario 1 --audience exec --json
# synthetic_data_engine.py is a library (SyntheticDataEngine, DOMAINS) — import it, no CLI

# Internal Awareness Engine (Phase 1-6, D-AWARE)
python tools/awareness/component_indexer.py --scan --json        # Refresh kg-icdev-self-awareness nodes + edges
python tools/awareness/edge_deriver.py --derive --json           # Derive dependency edges (imports, DDL, routes, registry)
python tools/awareness/edge_deriver.py --dependents tools/db/storage.py --json   # Blast radius: what breaks if this changes
python tools/awareness/edge_deriver.py --dependencies tools/awareness/health_prober.py --depth 2
python tools/awareness/edge_deriver.py --stats --json            # Edge counts by derivation method
python tools/awareness/health_prober.py --run-all --json         # Probe routes, imports, coherence
python tools/awareness/drift_detector.py --detect --json         # Detect regressions vs baseline
python tools/awareness/gap_detector.py --detect --json           # Surface structural gaps
python tools/awareness/suggested_card_writer.py --write --json   # Promote predictions to kanban
python -c "from tools.genesis.reflexes.awareness import run; run({}, None)"  # Full 5-phase cycle
# UI: http://localhost:5050/components-map (visual map) + /ask-icdev (Q&A chat)
# Config: args/awareness_config.yaml — 3h cadence, 7 gap rules, 0.7 threshold


```

#### Card records — one line each; the essay is `docs/reference/cards/<id>.md`

Each card below is a hard-won incident record: what was MEASURED, what changed, and
what deliberately did not. The essays used to sit inline in this block — 297,635 of
this file's 367,462 bytes — and every session paid for all 82 whether or not it
touched one (xrv-docs-02). They moved VERBATIM to `docs/reference/cards/`, one file
per card, named for the id below. Read the one your task names; the command here is
the single entry point, and the essay carries the rest.

Budget: `args/claude_md_budget.yaml`, warned by `coherence_checker.py --check
claude_md_budget`. It may only go DOWN.

Carried up from `flx-twin-01` because it is a RULE and not a record: NEVER source a
performance, cost or capacity claim from the floci twin — an emulator reproduces the
AWS API contract, not its performance characteristics.

- `xit-decl-01` — Which parent IS this checkout, and may it touch THIS database? — `python -m icdev.core.context --check`
- `rmf-inert-03` — A reflex that is GREEN while it can reach 3 of 11 subjects — `python -m tools.genesis.reflexes.canvas_reassess --coverage`
- `exa-live-01` — Capability consumption — is a DECLARED capability actually being used? — `python tools/awareness/capability_consumption.py --json`
- `cef-ci-01` — The Cortex federation layer is UNDER that gate — `python tools/awareness/capability_consumption.py --class cortex_backend --json`
- `rem-hyg-17` — Does the surface's CLAIM survive an INDEPENDENT re-derivation? — `python tools/awareness/claim_verifier.py --json`
- `claim-verif-33c9f4cd11` — A service's session id is INHERITED by everything it spawns — `python tools/awareness/claim_verifier.py --claim scheduler_heartbeat_is_fresh`
- `autonomy-id-06` — A daemon's reload watch set is what it EXECUTES, not what it had imported at start — `python -m pytest tests/genesis/test_code_reload.py -q`
- `autonomy-lrn-02` — Is intervention actually FALLING? The AUTONOMY card held to its own standard — `python -m tools.awareness.autonomy_loop`
- `autonomy-act-03, autonomy-dep-04` — The restore tier, ENUMERATED — four mechanical acts, and no fifth — `python tools/awareness/restore_acts.py --list`
- `autonomy-lrn-01` — An INCIDENT becomes a STANDING CLAIM, and the claim cites it — `python tools/awareness/claim_verifier.py --incidents`
- `trust-disc-04` — Substrate probe — does the thing you are about to design against HAVE ROWS? — `python tools/awareness/capability_consumption.py --probe-plan <plan.md> --substrate-gate`
- `exa-audit-04` — Audit hash-chain integrity — is the audit_trail chain actually intact? — `python tools/audit/chain_sweep.py --json`
- `cch-obs-01` — Per-provider prompt-cache effectiveness — not one aggregate number — `python tools/cache_savings/by_provider.py --json`
- `dwr-fid-01` — The UPLOADED original is KEPT, content-addressed, before its temp file goes — `python -m tools.document_intelligence.originals --survey [--json] [--verify]`
- `dwr-anchor-06` — A suggestion drafted against a TOKEN is retired, never back-filled — `python -m tools.document_intelligence.suggestion_redraft --census`
- `dwr-fid-02` — Where on the page did each word SIT? The layer a left pane renders from — `python -m tools.document_intelligence.page_geometry --survey [--json]`
- `dwr-fid-03` — A DEGRADED render SAYS it is degraded, and the ingest posture is REAL — `python -m tools.document_intelligence.reading_pane --survey [--json]`
- `cef-di-01` — DocMod asks ONE governed seam instead of hand-querying tables — `from tools.doc_modernization.evidence import (`
- `cef-di-03` — DocDrift's SSP evidence comes from ONE governed seam — `from icdev.tools.document_intelligence.ssp_evidence import resolve_evidence`
- `cef-di-05` — DIC document generation asks ONE governed seam, and screens what it wrote — `from icdev.tools.document_intelligence.docgen_evidence import (`
- `cef-di-04` — DIC grounded search asks ONE governed seam for its candidates — `from icdev.tools.document_intelligence.search_evidence import resolve_evidence`
- `cef-ui-01` — DocDrift SHOWS the verdict — and shows an unknown as a finding — `from icdev.tools.document_intelligence.docdrift_evidence import (`
- `cef-ui-03` — HITL approve/reject for a resolve-produced proposal — EXISTING routes — `POST /document-intelligence/api/modernization/findings/<id>/resolve`
- `cef-ui-02` — A conflict/gap the request DIDN'T take with it, browsable on Explorer — `from tools.cortex.finding_store import list_findings, finding_stats`
- `cef-fnd-04` — Is this entity still current? ONE store, any source, any domain — `python -m tools.currency.entity_currency --backfill --json`
- `dwr-ev-01` — An AUTHOR's upload is a declared source, ranked top, and the catalog it contradicts survives — `python -m tools.currency.entity_currency --resolve "catalyst 6500" --entity-type hardware_model`
- `dwr-ev-02` — A COMMENT is an instruction until a human promotes it; then it is CITED, and marked — `python -m tools.currency.entity_currency --resolve "tls 1.1" --entity-type crypto_protocol`
- `dwr-ev-03` — Redraft with my comments — a button a human presses — `from tools.document_intelligence.redraft import redraft_change, run_stats`
- `dwr-word-02` — A reviewer's Word revisions, read back in and RECONCILED — `python -m tools.document_intelligence.docx_review_import --file review.docx --version <version_id>`
- `exa-bench-03` — Agent adapter capability matrix — DECLARED vs ACTUAL per adapter — `python tools/agents/capability_matrix.py --json`
- `exa-bench-05` — PreToolUse hook enforcement — the hook's exit 2 now reaches the caller — `python tools/hooks/fire_rate_survey.py --json`
- `kpr-rvfy-05` — A raw `gh pr merge` on a KANBAN-LINKED PR is refused — `python tools/hooks/fire_rate_survey.py --check gh_pr_merge_bypass --samples 10`
- `mfx-mrg-04` — A protected-path PR lands through the DOOR, with an audited reason — `python tools/kanban/cli.py --set-status <id> done --merge --protected-ok --reason '<why>'`
- `mfx-mrg-07` — The Actions auto-merge workflow is a FOURTH door, and it now honours protected_paths — `python tools/ci/protected_paths.py --config-file args/pr_watcher_config.yaml --files tools/ci/pr_watcher.py docs/x.md`
- `mfx-mrg-08` — The union resolver DISCARDED a deletion: an empty side is not "nothing to say" — `python -m tools.kanban.union_deletion_survey`
- `mfx-own-02` — A claim from a PLAIN SHELL now HOLDS -- `--claim` hands its lease to a keeper — `python tools/kanban/cli.py --claim <task-id> --intent "repairing its PR by hand" [--ttl 7200]`
- `mfx-own-05` — A REPARK id extends a task id at the FRONT -- the matcher no longer binds it — `python -m tools.kanban.branch_match_survey --env-file C:/AI/ICDev/.env`
- `kph-repark-kph-repark-mfx-ci-04` — The worktree-add budget is REAL, and the checkout is parallel — `python -m pytest tests/kanban/test_worktree_add_budget_is_real.py -q`
- `mfx-own-04` — A worktree HUSK with no .git marker is provably dead -- swept on a clock of HOURS — `python -m tools.kanban.worktree_husks --survey [--json]`
- `kpr-watch-15` — A `merge -s ours` supersede made the branch UNREBASABLE — `python -m tools.ci.rebase_merge_survey --classify`
- `kpr-watch-13` — Did that resume REACH anything, or was a line just written? — `python -m tools.ci.resume_delivery --survey`
- `kpr-watch-14` — CLAUDE.md is DECLARED for the union rung, and its generated copy is DERIVED — `python -m tools.kanban.claude_md_union_survey`
- `kpr-watch-11` — Is a task's status OSCILLATING — two writers taking turns? — `python -m tools.kanban.status_churn --json`
- `autonomy-act-05` — ONE statement of which pr_watcher actions are recovery evidence — `python -m tools.kanban.recovery_action_survey`
- `autonomy-act-02` — Consume the detectors nobody runs — and file each finding ONCE, with its evidence — `python -m tools.kanban.detector_findings --json`
- `kpr-fix-03` — Would that check have been RIGHT to refuse? Surveyed; answer is NO — `python -m tools.kanban.landed_dispatch_survey --json`
- `kpr-rvfy-04` — A `done` task with NO artifact, and a comment mention read as a landing — `python -m tools.kanban.artifact_evidence --survey`
- `trust-disc-05` — Is this task id ALREADY on main? task -> main, not task -> PR — `python -m tools.kanban.landed_check --task <task-id> --json`
- `rem-hyg-03/04` — Does an epic CLAIM this task id? Surveyed, then armed to `report` — `python -m tools.kanban.identity_survey --json`
- `tsg-iso-03` — An undeclared third-party import that fails SILENTLY — `python tools/ci/undeclared_import_census.py --check`
- `xrv-route-03` — A CI reference that does NOT NAME THE BYTES it resolves to — `python tools/ci/pin_census.py --check`
- `xit-leak-01` — This repo is PUBLIC: nothing from the trading domain comes back — `python tools/ci/domain_leak_gate.py --check`
- `xit-decl-04` — Every table has ONE owner: core | it | ft — `python tools/db/schema_ownership.py --check`
- `xit-decl-03` — A module that computes the REPO ROOT from its own location — `python tools/ci/self_root_census.py --check`
- `rem-hyg-13` — A PERFECT SCORE returned when the denominator is empty — `python tools/ci/perfect_score_census.py --check`
- `rem-tst-06` — Promote an ungated test module — but only if it is green BOTH WAYS — `python -m tools.ci.gate_promoter --plan --limit 10`
- `rem-hyg-14` — An ungated test that is RED FROM BIRTH, not only one that regressed — `python tools/ci/born_red_survey.py`
- `crx-test-05` — The gated pytest run is SHARDED across runners — `python tools/ci/gated_test_list.py --print --list core --shard 2/4`
- `mfx-ci-02` — ONE ICDEV CI run per ref -- a newer push CANCELS the superseded run — `python -m pytest tests/ci/test_ci_concurrency.py -q`
- `crx-test-07` — The shards are BIN-PACKED by measured duration, not file count — `python tools/ci/shard_timings.py --show`
- `trust-disc-01` — Red-first proof — did the changed test actually go RED? — `python tools/ci/red_first_gate.py --gate`
- `cef-ci-02` — A closed census may LOSE names and must never GAIN one — `python tools/ci/census_growth.py --check`
- `mfx-ci-01` — A cheap static check that runs only on CI is a MANUAL FIX 20 minutes later — `python tools/dx/mirror_parity.py --files tools/db/storage.py --json`
- `mfx-ci-04` — Editing CLAUDE.md without regenerating the packaged bootstrap is refused at COMMIT — `python tools/installer/prebuild_bootstrap.py`
- `qa-fail-6a87916931be3793` — The E2E suite writes fixtures — point it at a THROWAWAY database — `python tools/db/bootstrap_pg.py`
- `qa-fail-5cacee65f1d03c8c` — A sweep whose timeouts fell inside a HOST STALL can now say so — `python tools/testing/qa_agent_runner.py --run --json`
- `kpr-watch-01` — Which open PRs are awaiting merge, and WHY is each one not merging? — `python -m tools.ci.merge_readiness --json`
- `kpr-watch-02` — A PR that IS eligible and STILL open — the merger has stalled — `python -m tools.ci.merge_stall`
- `rem-hyg-05` — Raw board writers — does this INSERT bypass the canonical seeder? — `python tools/kanban/raw_insert_census.py --check`
- `cef-fnd-03` — DataBridge external rung — 33 connectors, now ONE authorized — `python -m tools.databridge.seed_connections --seed --json`
- `crx-test-06` — Is `E2E (Playwright)` reliable enough to be REQUIRED? Surveyed; answer is NOT YET — `python tools/ci/e2e_flake_survey.py --json`
- `rmf-disc-02` — The page was live, the five endpoints it called were DEFINED NOWHERE — `python -m tools.network.discovery_store`
- `rmf-zt-01` — A ZT check with NO PROBE behind it says `unknown`, never `passed` — `SC_STORAGE_BACKEND=sqlite python -m tools.security_canvas.zt_verdict_survey`
- `rmf-rail-01` — No rate in the RMF surfaces returns 0.0 or 100.0 over an empty denominator — `python -m pytest tests/test_rmf_honesty_rails.py -q`
- `rmf-rail-02` — The Compliance Posture widget's TWO remaining perfect scores, refused — `python -m pytest tests/test_compliance_posture_rail_02.py -q`
- `rmf-wp-02` — A DIC version leaves the canvas through ONE gated door, and CoT/CoD prose is redacted — `from tools.document_intelligence.exporter import export_version, export_gate, EXPORT_FORMATS`
- `mfx-boot-02` — A crash-looping self-hosted CI runner is re-registered with a fresh token — `python tools/genesis/daemon.py --reflex ci_runner_health --json`
- `gepa-optimizer` — GEPA Optimizer — Genome Evolution Pressure Analyzer — `python tools/skills/gepa_optimizer.py --json`
- `rmf-cyc-01` — RMF cycle time: TWO clocks that are never merged — `python -m tools.compliance.rmf_cycle_time`
- `rmf-wp-01` — WHITEPAPER document type, and template_id made LOAD-BEARING — `python -m tools.quality.outline_contract --artifact-type WHITEPAPER`
- `rmf-rfp-01` — The RFP shredder is WIRED, and there is ONE compliance matrix — `python tools/govcon/compliance_matrix_builder.py --opportunity-id "opp-xxx" --ingest solicitation.pdf --json`
- `flx-twin-01` — A twin over the EMULATOR, read through the broker, marked `emulated` — `from tools.twin_core.registry import TwinRegistry`
- `xrv` — Nine external repos reviewed; eight gaps were OUR OWN unconsumed capabilities — `python -m tools.cost.session_cost --survey --by-verdict --json`


- `mfx-own-09` — `git worktree add` is OFF the dispatch critical path -- a warm pool — `python -m tools.kanban.worktree_pool --status [--json]      # depth, each entry's health, refill verdict` — `docs/reference/cards/mfx-own-09.md`

- `kpr-watch-19` — The resume queue now has a consumer on the executor that RUNS, and is ONE directory from every checkout — `python -m tools.ci.resume_delivery --survey` — `docs/reference/cards/kpr-watch-19.md`

- `xrv-cost-05` — Every `tools/call` Claude Code makes leaves ONE audit row — `python -m tools.awareness.capability_consumption --class mcp_dispatch_tool --json  # extra.by_caller_source` — `docs/reference/cards/xrv-cost-05.md`

- `kpr-watch-21` — A refusal that names no remedy

### Python Dependencies
See `requirements.txt`. Key: sqlite3, pathlib, json (stdlib); openai, anthropic, python-dotenv (optional); pyyaml, jinja2, flask, pytest (ICDEV™).

> **Full command reference:** See [docs/reference/commands.md](docs/reference/commands.md) for all CLI commands across every ICDEV™ module.

---

## Architecture

### FORGE Framework (6 layers)

AI orchestrates; deterministic tools execute.

| Layer | Directory | Role |
|-------|-----------|------|
| **Goals** | `goals/` | Process definitions — what to achieve, which tools, expected outputs |
| **Orchestration** | *(Claude Code / multi-agent)* | Read goal, decide tool order, apply args, handle errors |
| **Tools** | `icdev/tools/` (canonical) | Python scripts, one job each. Deterministic execution. |
| **Args** | `args/` | YAML/JSON behavior settings. Change behavior without editing goals/tools |
| **Context** | `context/` | Static reference material (tone rules, writing samples, case studies) |
| **Hard Prompts** | `hardprompts/` | Reusable LLM instruction templates |

**Why the separation:** LLMs are probabilistic. At 90% accuracy per step, a 5-step workflow degrades to ~59% end-to-end (0.9⁵). FORGE confines LLM reasoning to orchestration; all execution is delegated to deterministic Python tools.

**Key indexes:**
- `goals/manifest.md` — master index of goal workflows. Check before starting any task.
- `tools/manifest.md` — thin index; per-domain detail lives in `tools/manifest/<topic>.md`. Grep the shards when searching for an existing tool.
- `memory/MEMORY.md` — curated long-term facts/preferences.
- `.env` — API keys, LLM model names. **Admins configure LLM here, not in code.**
- `.tmp/` — disposable scratch work. Never store important data here.

### ANVIL Build Workflow
5-phase TDD cycle invoked automatically on build requests:
1. **Architect** — design and acceptance criteria
2. **Navigate** — map to existing tools and patterns
3. **Verify** — write failing tests (RED)
4. **Integrate** — generate implementation (GREEN)
5. **Launch** — refactor, security scan, compliance map, merge

### Multi-Agent System
16 agents across 4 tiers communicate via A2A protocol (JSON-RPC 2.0 over mutual TLS).

| Tier | Agents | Port Range |
|------|--------|------------|
| Core | Orchestrator, Architect | 8443–8444 |
| Domain | Builder, Compliance, Security, Infrastructure, MBSE, Modernization, Requirements, Supply Chain, Simulation, DevSecOps & ZTA, Gateway | 8445–8458 |
| Support | Knowledge, Monitor | 8449–8450 |
| Application | ACE Co-Worker Engine | 8460 |

Claude Code interacts with agents through MCP servers using stdio transport.

### Import Conventions
**Canonical namespace:** `icdev.tools.*`

```python
# CORRECT for all new code
from icdev.tools.llm.router import LLMRouter
from icdev.tools.db.storage import get_connection

# WRONG for new code (backward-compat shim only)
from tools.llm.router import LLMRouter
```

The root `tools/` package is a backward-compatibility shim (`tools/__init__.py`) that redirects `tools.xxx` to `icdev.tools.xxx`. Existing scripts continue to work, but all new code must use `icdev.tools.*`.

**In a source checkout the two spellings are ONE module object (xit-decl-02).** `tools/__init__.py` installs `icdev/_shim.py`, a meta-path finder that answers `icdev.tools.X` with the object already bound to `tools.X` — so a monkeypatch on either spelling lands on the code under test, and a module-level singleton exists once. The physical file is the one under `tools/` (aliasing the other way would re-root 2,054 `Path(__file__)` sites onto the packaged copies under `icdev/`). Five `tools/` files are tiny shims over a real implementation that lives only in `icdev/tools/` (`llm/agent_loop`, `showcase/synthetic_data_engine`, `testing/qa_agent_runner`, `testing/selector_healer`, `billing/tier`) and resolve to that file under both names. The finder never installs in the wheel or beside a project's own `tools/` package. Pinned by `tests/test_namespace_identity.py`.

**`icdev.core` comes from the `icdev-core` DISTRIBUTION, not from this repo (xcore-cut-02).** This parent stopped shipping `icdev/core/*.py`; `icdev` is a shared namespace, with `icdev-core` contributing `icdev/core/` and this parent contributing `icdev/__init__.py` (which calls `pkgutil.extend_path`), `icdev/tools/` and `icdev/data/`. `icdev-core` deliberately ships NO `icdev/__init__.py`: with one in both, only ONE executes and the loser's work is silently skipped -- and if core's won, `_alias_tools_namespace()` would never run and the wheel's ~1,900 `tools.X` imports would break. `icdev/core/schema/tables.yaml` STAYS here: it is a DATA file that `schema_ownership.py --regenerate` writes and `sensitivity._manifest_exempt` reads from the repo root by path, and a leftover directory holding only data does not shadow the installed package (PEP 420 uses a namespace portion only when no regular package is found anywhere on the path). The pinned version lives in `args/core_api.yaml` and is enforced by `coherence_checker --check core_api`; install it with `pip install -r requirements.txt`.

**Test environments:** `tests/conftest.py` automatically injects the repo root into `sys.path` and forces `ICDEV_STORAGE_BACKEND=sqlite`. If you see `ModuleNotFoundError: No module named 'icdev'`, the repo root is not on `PYTHONPATH` and the package is not installed in editable mode.

### Databases & Storage
- Default backend: **SQLite** (`data/icdev.db`, 391+ tables).
- Override via `.env`: `ICDEV_STORAGE_BACKEND=postgresql` (requires `psycopg2-binary`).
- SaaS platform DB: `data/platform.db`
- Per-tenant: `data/tenants/{slug}.db`
- Audit trail is **append-only/immutable** (NIST AU). Never UPDATE or DELETE audit rows.

### Unified Component Registry
Canvases, child apps, features, and core extensions are declared in `args/component_registry.yaml` and loaded by `tools/config/component_registry.py`. Runtime registration (blueprints, routes, CLI toggles, nav links, IQE dispatch, RBAC metadata) is derived from the registry.

- Add a canvas by editing `args/component_registry.yaml` and providing the module/templates; no changes to `tools/dashboard/app.py`, `tools/cli/enable.py`, or `tools/dashboard/templates/base.html` are required.
- Core profiles live in `args/core_profiles.yaml`; activate with `icdev profile apply <name>`.
- Tenant-level enablement overrides live in `tenant_component_overrides` (migration 207); fallback is the env/default setting.
- Component configuration changes are logged to the append-only `component_audit_log` (migration 208).
- Full feature doc: [docs/features/enterprise-configurable-platform.md](docs/features/enterprise-configurable-platform.md)

---

## How to Operate

1. **Check goals first** — Read `goals/manifest.md` before starting a task. If a goal exists, follow it.
2. **Check tools first** — Before writing new code, grep `tools/manifest/` shards for existing tools. If you create a new tool, add its entry to the appropriate topic shard (e.g. `tools/manifest/memory-system.md`), not the thin index.
3. **When tools fail** — Read the error, fix the tool, update the goal with what you learned.
4. **Goals are living docs** — Update when better approaches emerge. Never modify/create without permission.
5. **When stuck** — Explain what's missing. Don't guess or invent capabilities.

### Session Start Protocol
1. Read `memory/MEMORY.md` for long-term context
2. Read today's daily log (`memory/logs/YYYY-MM-DD.md`)
3. Read yesterday's log for continuity
4. Or run: `python tools/memory/memory_read.py --format markdown`
5. Load project context: `python tools/project/session_context_builder.py --format markdown`

### First Run
If `memory/MEMORY.md` doesn't exist, this is a fresh environment. Run `/initialize`.

---

## Running ICDEV Outside Claude Code

ICDEV™ is fully operable without the Claude Code CLI. `tools/airgap/` is the runtime
shim — it replicates hooks, session management and safety gates as plain Python; the
headless ANVIL wrappers (`python tools/anvil/<name>.py`), headless skill invocation
(`python tools/skills/invoke.py --list --json`), cron/CI recipes, Ollama-only LLM
routing and air-gap validation all live in one place:

> **[docs/ops/airgap-runbook.md](docs/ops/airgap-runbook.md)** — §14 is the headless
> operation section this heading used to hold inline (moved verbatim, xrv-docs-02).

---

## Guardrails

### Development Rules
- **Runtime SQL is authored for PostgreSQL; `translate_sql` is a thin SQLite init-fallback ONLY, never load-bearing.** PostgreSQL is the primary backend. Do NOT write SQLite-dialect JSON SQL (`json_extract`, `json_array_length`, `json_each`) in runtime call sites and rely on `tools/db/storage.py::translate_sql` to rewrite it for PG. Instead either (a) **compute in Python** — read the raw JSON column and parse with `json.loads()` (preferred for filters, grouping, existence checks, NOT-IN subqueries; see `tools/cloud/csp_monitor.py::get_status`, `tools/creative/creative_engine.py`, `tools/research/trend_detector.py`, `tools/dashboard/app.py::api_chat_sources`), or (b) **author PG-native `jsonb`** behind an explicit `is_pg` branch with a SQLite fallback alongside (see `tools/network/network_ingester.py` node-id lookup and `tools/dashboard/app.py::components_map_page`). `translate_sql` JSON rules exist solely so init/seed/migrate paths still work when PG is unreachable at startup. The `pg_portability_linter` (pgp-tx-03) gates this — runtime modules (excluding init/seed/migrate/tests) must report zero high-severity JSON findings.
- **Canvas DB connections MUST use `get_canvas_connection()`** — Canvas-specific tables (e.g. `aac_*`, `dsoc_*`, `ccc_*`) have no `classification`/`tenant_id` columns. Using `get_connection()` directly in a canvas `db/init_db.py` attaches the global RLS predicate and raises `UndefinedColumn` on every query. Always use `from tools.db.storage import get_canvas_connection` in canvas init files. See `tools/ai_augmentation/db/init_db.py` for the canonical pattern.
- **Every column in an INSERT must exist in the LIVE schema, not just in the source DDL.** `CREATE TABLE IF NOT EXISTS` never alters an existing table, so a table created by an older migration keeps its old columns while the DDL in `init_icdev_db.py` moves on. The INSERT then raises at runtime, is swallowed by the surrounding `except Exception: pass`, and the feature reports success while persisting nothing — that is how `module_budget_usage` held 0 rows and `tools/govcon` never wrote an audit row. Adding a column means writing a migration, not editing the `CREATE TABLE`. Enforced by `coherence_checker.py:check_insert_schema_parity`, which reads `information_schema.columns` (PG) / `PRAGMA table_info` (SQLite); the pre-existing backlog is grandfathered in `args/insert_schema_gate.yaml` — do not add new entries to get a commit through.
- **Never hand-number a migration. Scaffold it: `python tools/db/migrate.py --create "<name>"`.** Migration ids are 14-digit UTC timestamps (`YYYYMMDDHHMMSS_slug`); the legacy `001`–`341` sequence is CLOSED. Picking "highest on main + 1" is a read-modify-write across every concurrent session with no lock between them, so two branches routinely choose the same number — one branch collided three times in a single session on 2026-08-02 (329, 330, 333) and one of those collisions broke `main` for every other PR. A collision is expensive rather than cosmetic because `MigrationRunner` keeps only the FIRST entry per version and the listing is alphabetical: the loser never runs and its tables simply never exist (60 migrations on main are shadowed this way). Also give every migration an `up.sql` or `up.py` — a directory with neither, or a bare `NNN_name.py` file, is skipped silently by `discover_migrations` and never runs at all (17 exist). Enforced by `tests/test_migration_version_uniqueness.py`.
- **Never wrap a security hook in a shell neutraliser, and never enable one without a fire-rate survey first.** `.claude/settings.json` wired the PreToolUse hook as `python … pre_tool_use.py || true` from the beginning. A PreToolUse hook signals "block" with **exit 2**; `|| true` makes the shell return 0 whatever the hook decided, so all eleven checks printed `BLOCKED: …` and blocked nothing — and because `tools/agents/adapters/claude_cli.py` launches Claude Code with `--dangerously-skip-permissions` (D394), that hook is the ONLY ICDEV control that sees a tool call inside a spawned session. The wrapper was redundant for its apparent purpose too: `main()` already exits 0 on `JSONDecodeError` and on any unexpected exception, so a broken hook fails open without shell help — the wrapper suppressed only the WORKING case. To stand a check down use `ICDEV_PRETOOLUSE_ENFORCE=0` or a per-check `ICDEV_<CHECK>_GUARD=0` (`CHECK_KILL_SWITCHES`), which are auditable in a way a shell operator inside a JSON string is not. Going the other way — arming a check — requires measuring it first with `python tools/hooks/fire_rate_survey.py --json`: over 96,818 real tool calls, eight of the twelve were refusing routine work (4.86% of all calls, narrowed to 1.63%), and the worst refused 2,526 — one call in forty — because it read a heredoc BODY as commands, kept the `)` of `$( … 2>/dev/null)` on the path, and could not spell this worktree the way Git Bash spells it (`/c/AI/ICDev`). Note WHICH check that was: `check_write_outside_worktree` had shipped as a hard block a task earlier, and its rate had still never been observed, because `|| true` was discarding everything it returned. **A check that is nominally enforcing behind a neutraliser is unmeasured, not proven** — survey it when you remove the neutraliser, not when you write it. The survey MUST be driven from the Claude Code transcripts — `hook_events` persists tool-input KEY NAMES and never the operand, so a replay sourced from it reports 0 fires for every check and reads as "safe to enable". Enforced by `tests/test_skip_permissions_compensating_controls.py`.
- **A declared capability that is never consumed is a defect, and it is the one this platform ships most.** Registered, importable, catalogued, `enabled: true` — and nothing ever calls it, while nothing goes red. The reflex flavour shipped three times (xbm-wake-01/02, hgx-obs-02); the audit hash chain, `MCPToolAuthorizer`, the prompt registry and GEPA are the same defect in different clothes. `coherence_checker.py:check_capability_liveness` fails when a capability class declares more units with ZERO lifetime consumption than `args/liveness_gate.yaml` allows, measuring through `tools/awareness/capability_consumption.py` (existing telemetry only — no new tables). Two things it deliberately does NOT fail on: a unit consumed at some point but idle inside the recent window (a quarterly reflex is not a dead one — it is reported as `idle_this_window`), and a database with no operating history (a fresh worktree or ephemeral CI database makes everything look inert, so the check warns instead of fabricating findings). Wire the capability to a consumer or stop declaring it; **never raise a count** to get a commit through.
- **Probe a substrate for ROWS before you design against it — one `SELECT COUNT(*)`, before the plan is approved.** The same defect one layer down: not a declared capability nobody calls, but a declared SUBSTRATE — a table, a column, a config block — that code is designed against and that holds nothing. An approved implementation plan described `kg_ontology` as a working SHACL-lite supplying declared `(subject_type, predicate, object_type)` legality; measured the same afternoon, `kg_nodes` held 8,869 rows and `kg_edges` 16,493 while `kg_ontology`, `ontology_subclass_closure` and `kg_nodes.ontology_id` held nothing at all. The whole declared-ontology chain was inert end to end beneath a rich graph, so the validator built on the empty half would have answered "unknown" forever while looking like it worked. Run `python tools/awareness/capability_consumption.py --probe-plan <plan.md> --substrate-gate` (or `--probe-substrate <table>[.<column>]`, `--probe-diff origin/main`) at DESIGN time; `coherence_checker.py:check_substrate_liveness` is the backstop and warns when a changed module READS a declared substrate holding zero rows. Three distinctions the tool refuses to blur, because each sends you to a different fix: `empty` (the table exists, a writer never ran), `absent` (no such table, a migration never ran) and `column_unpopulated` (rows exist, the column is 100% NULL). And two zeroes that are NOT findings: a database with no operating history — 1,320 of the 1,775 tables on the live board are empty, so a fresh worktree or ephemeral CI database reports UNMEASURABLE rather than 1,320 fabrications — and a reference that only WRITES the substrate, since `INSERT INTO x` is the fix for an empty `x`, not the defect. Add a load-bearing substrate to `substrates:` in `args/capability_consumption.yaml`; that list is curated claims, never a schema dump.
- Always grep `tools/manifest/` shards before writing a new script
- **Register a new tool by APPENDING a row to its topic shard — never by rewriting neighbouring rows.** `tools/manifest.md` and `tools/manifest/*.md` are declared `merge=union` in `.gitattributes` (kax-conflict-03), so two branches appending under the same topic both land and neither needs a human: git takes the superset, which is the resolution these files always got by hand anyway. Measured 2026-08-08: 219 commits touched `tools/manifest/` in 14 days, 95 added rows against 22 removed, and the shards conflicted in three of six blocked PRs. Union is clean for 2/3/5 concurrent branches in BOTH the local `git merge` path and the bare `git merge-tree --write-tree` plumbing a forge runs server-side — re-measure any time with `python tools/git/manifest_merge_rehearsal.py`, which also shows that a heading-delimited block is NOT a fix (it collides at the same end-of-file offset a table row does). Two consequences: (1) a row DELETED on one branch is resurrected if that hunk conflicts with another branch's append, so verify a removal actually stuck; (2) if both sides add the same row you get an exact duplicate — `coherence_checker.py:check_manifest` reports those, and it still flags tools that were never registered at all. Union is safe here only because these are flat, line-oriented tables with independent rows; never extend it to YAML/JSON/Python.
- **`merge=union` does not apply on GitHub, so a union collision still turns the PR DIRTY — and a rebase, not a resume, is what clears it.** `.gitattributes` merge drivers are honoured by `git merge`, `git rebase` and `git merge-tree`, and NOT by the merge GitHub computes for a pull request. The rehearsal notes above say union was verified in "the plumbing a forge runs server-side"; that plumbing was git's, never GitHub's. So a branch that collides on a union path merges clean locally and is reported CONFLICTING/DIRTY on the PR, and the two disagree while each is right about its own merge. It is the COMMON case, not an edge: the test-gating rule below requires every PR that adds a test file to append to `args/ci_test_files/core.txt`, so nearly every kanban PR collides there against a main that also appended. Measured 2026-08-17 — nine of ten open PRs were DIRTY, and re-running each merge with the union rules stripped reproduced the forge's verdict on ten of ten including the negative control (#1730 also appended to `core.txt`, did not collide there, and was the one PR reported MERGEABLE). The cost was not cosmetic: `pr_watcher._conflict_is_real` used `git merge-tree`, saw a clean merge, and classified the whole class a stale forge cache — a one-shot budget sized for a verdict that goes stale once, spent against a collision that returns on every push to main. Both budgets emptied, one `escalate` row was written, and the watcher then went permanently quiet (hcx-evt-03: 499 escalates, 10 resumes; kpr-dup-03: 380 and 15) while AWAITING MERGE never drained. `classify_conflict` now separates **real** (git conflicts too — escalate), **union_only** (clean here, conflicting there, and only because of union) and **phantom** (clean both ways — the cached verdict really is stale), and counts rebase attempts per BASE ERA rather than per PR lifetime, so a recurring cause cannot exhaust a one-off budget. **Do NOT respond to a DIRTY union PR by deleting a rule from `.gitattributes`** — that puts the conflict back into every local merge as well. Rebase the branch: it applies the union rule and writes the resolution into the branch, after which the forge has nothing left to object to. An LLM resume can never fix either kind — the branch it is asked to repair has no conflict in it.
- **A new test file is gated in the PR that adds it, or CI fails.** CI runs 192 of the 2,150 test modules pytest collects; the other 1,826 have never gated a merge, which is why a suite can be wrong from its FIRST commit and nobody finds out for six weeks (`remediation_simulator._run_nqe_layer` was dead since June, and one isolated sweep on 2026-08-11 found 531 failure lines across 87 files). Add each file **in the same PR that makes it pass** — that is the only sanctioned way to widen the allowlist. Write ONE fragment named for your task, `args/ci_test_files/core.d/<task-id>.txt`, rather than appending to `core.txt`: that shared file was the largest merge-collision surface in the repository — **82.8% of merged kanban PRs touched it**, and because GitHub does not apply `merge=union` (see the rule above) every one of them went CONFLICTING as soon as a sibling merged, costing a rebase on 30.9% of PRs and a human on 27.4%. Two PRs writing two differently-named files cannot collide at all. `core.txt` is unchanged and still authoritative for everything already in it — the two are read as one list, so the duplicate check, the truncation floor and the census all see the combined set (tsg-policy-03). Do NOT bulk-widen: those files are ungated AND an unknown number are red, so adding them wholesale turns `main` red and the gate gets disabled, which is strictly worse than the debt. The 1,826 are grandfathered BY NAME in `args/ci_test_backlog.txt` (enumerated, not counted — a count can be held constant while the set churns, and that is exactly how the gap regrows behind a green gate); that census only ever SHRINKS, and `backlog_max` in `args/test_gating_gate.yaml` may only go down — never append to the census or raise the ceiling to get a commit through. An exclusion needs a written reason next to the pattern. Enforced by `python tools/ci/gated_test_list.py --check-coverage` in the required `test` job **and, since tsg-policy-02, at `git commit` time** — `.githooks/pre-commit` runs the same census whenever the commit ADDS or RENAMES a file in scope, so you learn it in one second instead of after `main` goes red and blocks every open PR (measured: 0 cost for a commit touching no test file, 155ms before / 154ms after). The hook is the fast path, CI is the backstop — it is bypassable with `--no-verify`, and it deliberately does NOT append the file for you: a hook that widened the allowlist itself would gate a test nobody has run. Policy: [docs/ci/test-gating-policy.md](docs/ci/test-gating-policy.md).
- **A changed test file is run BOTH alone and in-suite, or its order-dependence is invisible.** Nothing in this pipeline randomises or isolates test order — not `.github/workflows/icdev-ci.yml`, not `pytest.ini` (absent), not `pyproject.toml`. Every gated module runs exactly once, in one process, in the order of `args/ci_test_files/core.txt` followed by the `core.d/` fragments in filename order, so a file can be green **because of what ran before it** and stay green until an unrelated allowlist edit reshuffles the run — at which point it surfaces as a failure in whatever PR happened to move the list. Four files had that shape in a single session: `tests/cortex/test_chat_routing.py` passed in-directory and failed ALONE, while `tests/cortex/test_chat_turn_connections.py`, `tests/cortex/test_blueprint_routes.py` and `tests/test_cnr_mission_canvas.py` passed alone and failed IN-SUITE — all four registering a blueprint onto the shared `tools.dashboard.app` singleton behind an `if "x" not in app.blueprints` guard that skips only when the blueprint is ALREADY there, i.e. never in the case that fails. **The two directions are different defects and need both runs.** In-suite is the existing `Run core unit tests` step; alone is `python tools/ci/isolation_run.py --run`, which re-runs only the test files the PR touched, one process each. Only an ALLOWLISTED changed file can fail it — that file is already green in-suite, so a standalone failure is real news; a changed file in no allowlist has never gated a merge and may be red for reasons predating the PR, so it is run and warned about rather than enforced (enforcing there would earn the step a `|| true` inside a week). It needs `fetch-depth: 0` on the checkout: a shallow clone has no merge base, and the tool exits **2** rather than resolving to "no files changed" — a step that ran nothing must not read as green. Fix an isolation failure by making the test self-sufficient; never by re-ordering `core.txt` or renaming a fragment to move it up the run.
- **A changed test that still passes against the pre-change tree is not a test, and CI now proves it.** ANVIL mandates RED -> GREEN and NOTHING ANYWHERE RECORDED THE RED: a process instruction whose evidence is never captured is the `|| true` failure of D394 in a second form — the rule is stated, the artifact proving it fired is absent, and no reader can distinguish a check that ran from one that did not. The `test` job now re-derives it. For every test file the branch ADDS or MODIFIES, `python tools/ci/red_first_gate.py --gate` checks out the merge base, applies **only that test file** on top, and asserts it does NOT pass there while it does pass here; the merge-base pytest output is uploaded as the `red-first-proof` artifact, which is the record. The case that motivates it: a test asserting `check_project_card_coverage` "degrades honestly when the board is unreachable" passed locally because the UNPATCHED call raised — the monkeypatch had landed on `tools.db.storage` while the checker resolved `icdev.tools.db.storage`, two distinct module objects. It was correct-looking, reviewed, and worthless. The decision table is SHARED with `tools/security/reproduction_validator.py` (`decide_discrimination`), which already stated the rule for HTTP replay — "fires" means "the predicate evaluated true" there and "the test FAILED" here; do not write a second copy. Three states, three exit codes: 0 clean, 1 a non-discriminating test, **2 the gate could not run** — a gate that cannot run is not a gate that found nothing, so 2 stays red (it needs `fetch-depth: 0`). The escape hatch is an exemption **with a written reason** in `args/red_first_gate.yaml`; never `mode: advisory` and never `|| true` to get a commit through. A docstring-only edit is reported `not_applicable`, because it adds no assertion and so has no new RED to record.
- **A gated test that SKIPS is an UNMEASURED test, not a passing one.** The rule above answers "does CI run this file?" and nothing else; `pytest.skip` makes that a different question from "did it assert anything?". `tests/test_app.py`'s overview test is on `core.txt`, ran on every PR, reported green, and skipped itself — `SQLite test DB lacks platform schema for overview: ...`, reproduced 2026-08-15. Its `except OperationalError` catches whatever the route raises FIRST, so the message moves as `MINIMAL_ICDEV_SCHEMA` gains pieces: `no such column: classification` when the missing piece was the column the RLS predicate in `get_connection()` filters on (which turned EVERY read of `kanban_tasks` into a raise), `no such table: agents` today. The skip presented that as coverage for an unknown length of time and nothing went red. `python tools/ci/skip_census.py --check` fails when a skip site in a gated file is not enumerated BY NAME in `args/ci_skip_census.txt` with a **written reason** (`flaky`, `TBD` and friends are refused), and `skip_census.skip_max` may only go DOWN — registering a new skip breaches the ceiling, not registering it breaches the by-name check, and there is no third door. Adoption count: 81 sites across 31 of the 260 gated files, enumerated not counted, because a count can be held constant while the set churns. The static AST half runs before pytest and at `git commit` (`--staged`); the `--from-report <junit.xml>` half reads what the gated run ACTUALLY skipped, per file, and fails on a gated file that skipped while declaring no site of its own — a conftest fixture, a plugin or a rebound alias, none of which the source scan can see. Surveyed before arming, as the PreToolUse rule above demands: a full gated run (2026-08-15, 240 targets, 6,864 collected) skipped 45 (0.66%) with ZERO unaccounted, and 37 of those 45 came from ONE parametrized site — site count and skip count are different quantities, which is why both halves report their own. **Delete the skip and make the test run; registering one is a debt you have written down.** Enforced by `tests/test_skip_census.py`.
- **A raw `INSERT INTO kanban_tasks` bypasses every guarantee the canonical seeder provides, and a gate inside `create_tasks` can never see it.** `tools/kanban/task_factory.py` has opened with "Canonical task seeder — never use raw INSERT directly" from the beginning, and nothing ever checked it. Measured 2026-08-16 across `tools/` and the `icdev/tools/` mirror: **231 raw board INSERT sites in 209 files**, 219 of them debt once the seeder itself and `db/migrations/**` are excluded — roughly **seven board writers in ten bypass the seeder**, and 42 of those sites are the AUTONOMOUS path (`tools/genesis/reflexes/*`, plus `ace/controller.py`, `ace/coworker_thread.py`, `awareness/suggested_card_writer.py`, `chat/kanban_bridge.py`, `chat/requirement_intake_hook.py`). What the bypass costs: the `VALID_TASK_TYPES` check that PostgreSQL enforces and SQLite silently does not, the `_assert_real_board` refusal that stops a seed run landing in a throwaway worktree database (the "36/36 created" against a database deleted with the worktree), the gate-id and risk-marker validation in `tools/kanban/gates.py`, and the dedupe that makes a re-run idempotent — none of them run, and the write reports success anyway. **A gate placed inside `create_tasks` is therefore only ever half the answer: it sees the 30% that already call it.** The other half is `coherence_checker.py:check_board_writer_census`, which fails on a raw-INSERT site not enumerated BY NAME in `args/kanban_raw_insert_census.txt`. Enumerated rather than counted, for the same reason as `args/ci_test_backlog.txt`: a count can be held constant while the set churns. Per SITE (`<file>::<qualname>[<n>]`), not per file, so a grandfathered module cannot grow a second and third writer unobserved. `raw_insert_max` in `args/board_writer_gate.yaml` may only go DOWN — lower it when a writer is converted, **never raise it to get a commit through**. Two exclusions exist and each states why a raw INSERT is CORRECT there (the seeder itself; the migrations tree, which runs against the table mid-shape). Converting the 219 is rem-hyg-06; this gate only stops the set growing. One thing it deliberately does NOT catch: a table name built at runtime (`f"INSERT INTO {table}"`), which is documented as a non-goal and was MEASURED before being accepted — of the 53 modules that interpolate an INSERT target, the only four that also mention `kanban_tasks` are `storage.py::translate_sql` and the checker itself. Enforced by `tests/test_raw_insert_census.py`.
- **Never document a command whose file does not exist.** A documented command that does not exist is worse than an undocumented one: an agent reading CLAUDE.md will confidently run it and burn a cycle deciding whether the tree is broken or the doc is. Before adding a `python tools/...` line to CLAUDE.md or `docs/reference/commands.md`, verify the file is committed. If a tool is a library with no `argparse`/`__main__`, document the import, not a CLI. Enforced by `coherence_checker.py:check_doc_command_paths` (grandfather list: `args/doc_command_gate.yaml`).
- Verify tool output format before chaining into another tool
- Don't assume APIs support batch operations — check first
- When a workflow fails mid-execution, preserve intermediate outputs before retrying
- Read the full goal before starting a task — don't skim
- **NEVER DELETE YOUTUBE VIDEOS** — Irreversible.
- **Worktree-first, branch-first workflow — NEVER commit directly to `main`, and NEVER work on the shared checkout.** Multiple AI sessions run concurrently in the SAME repository working directory, so branch changes there collide (one session's `git checkout` moves `HEAD` under another's feet — commits land on the wrong branch and pushes get clobbered). Therefore every code change — whether from a Claude Code session, Cursor, the kanban runner, or any AI assistant — MUST be done in a dedicated git worktree and follow: (1) create an isolated worktree on a new branch off `origin/main` — `git worktree add -b feat/<slug> <path> origin/main`, where `<path>` MUST come from `python -m tools.git.worktree_paths --path cli <slug>` (or your session scratchpad) and is OUTSIDE the repo; NEVER `git checkout -b` in the shared working directory, and NEVER invent a base directory. **"outside the repo" alone is not a convention** — asking for it in prose produced 150 worktrees across 22 different parent directories (33 flat in `%TEMP%\claude`, 28 in `C:\AI\.worktrees`, 27 nested *inside* another worktree), with five basenames colliding across parents; two simultaneous `wt-wake2` checkouts on different branches is how one session's edits appeared in another session's working tree. Roots are disjoint per actor (`kanban`/`cli`/`verify`/`autofix`) and `cli` paths are namespaced by session id, so two sessions choosing the same slug cannot collide. `.claude/hooks/pre_tool_use.py::check_worktree_path` blocks a `git worktree add` outside those roots (`ICDEV_WORKTREE_GUARD=0` to disable, `ICDEV_WORKTREE_ROOT` to relocate); audit with `python -m tools.git.worktree_paths --audit`. The **contents** of the worktree are bounded too: `check_write_outside_worktree` refuses a write whose resolved target (`..` and symlinks followed) lands outside the session worktree, the main checkout, and the sanctioned scratch roots — the boundary `args/file_access_tiers.yaml` cannot express, because a glob list enumerates paths and `/etc/cron.d/pwn` is the one nobody enumerated (`ICDEV_WRITE_BOUNDARY_GUARD=0` to disable, `=monitor` to record without refusing, `ICDEV_WRITE_BOUNDARY_EXTRA_ROOTS` to sanction more roots), (2) make changes inside that worktree (the Bash tool's cwd resets to the repo root after each call, so `cd <path>` at the start of each command), (3) commit, (4) `git push -u origin feat/<slug>`, (5) open a PR with `gh pr create`, (6) wait for CI to pass (required checks: Lint, Test, Security Scan, Helm Lint), (7) merge with `gh pr merge --merge`, (8) `git worktree remove <path>`. If commits accidentally land on the wrong branch, they are safe as git objects — create a fresh worktree off `origin/main` and `git cherry-pick` them into it. The only exception is a trivial one-liner hotfix explicitly approved by the user. This rule is LLM-agnostic and applies regardless of which model or tool produced the change.
- When adding an append-only/immutable DB table, ALWAYS add it to `APPEND_ONLY_TABLES` in `.claude/hooks/pre_tool_use.py`
- **The `Pages:` line and the Compliance dropdown's active-path list are DERIVED — never hand-append to either (mfx-sib-02).** Both were lists nothing generated: the `- Pages:` line in `.claude/commands/start.md` and the `request.path in ['/compliance', ...]` list on the Compliance trigger in `tools/dashboard/templates/base.html` **and its `icdev/` mirror**. Every route-migration card appended one token to each — the new canvas URL and the legacy URL its 301 preserves — so N cards of one epic collided N-1 times on two lines none of them was really editing. Now: add your route, add your menu link, add your 301, and run **`python tools/dashboard/nav_paths.py --write`**. Both blocks sit between markers; a hand edit is drift that `--check` sees, and there is no third door.
  - **The nav list is derived from the MENU, not from a config list.** `request.path in [...]` answers one question — am I on a page THIS dropdown links to — so the enumeration is the dropdown's own `<a href>` values plus every literal `redirect("<target>", code=301)` in `tools/dashboard/app.py` whose target is one of them. That redirect IS the statement "these two URLs are the same page"; a card that writes it does not also have to say so in a template. An alias whose target is NOT in this menu is never pulled in. The `startswith(...)` clauses stay HAND-WRITTEN outside the markers: a prefix is a policy ("everything under the Security canvas counts as Compliance"), an enumeration is a fact about the menu, and only the fact is generated.
  - **The Pages line comes from the real `url_map`, read in a SCRUBBED SUBPROCESS.** Blueprint registration depends on `.env` toggles — measured 2026-09-04, the SAME checkout yields 4568 rules under one operator's ambient environment and 2370 under a bare one, so a `--check` reading the inherited environment passes or fails according to whose shell ran it. Only platform-location variables pass through (`APPDATA`/`USERPROFILE` are load-bearing on Windows: `icdev-core` installs into the per-user site-packages directory and the probe cannot import `icdev.core.paths` without them), and every component in `args/component_registry.yaml` is forced ON so the documented list is the SUPERSET of pages the platform can serve. The old hand-maintained line was ~400 blueprint-RELATIVE rule fragments (`/tasks`, `/status`, `/<context_id>/close`) that were never reachable URLs, and was missing 413 real pages including the whole Academy canvas.
  - Legacy tokens the derivation provably cannot see — `/iac`, `/migration`, `/migration-cost`, `/analytics`, which the Compliance menu has never linked to and nothing redirects to — are DECLARED in `args/nav_paths.yaml` **with a written reason**, so this change alters no highlight behaviour and the residue is visible to a reviewer rather than lost inside a template attribute. Deleting one is a product decision, not a merge accident. Do NOT add an entry to quieten a `--check`: if the path belongs in the menu, put it in the menu.
  - Cost is split by what it needs. `--nav-only` is a template parse plus an AST walk (milliseconds, no Flask, no database) and runs in `.githooks/pre-commit`, firing only when the commit stages one of the four files that can change the answer; `--pages-only` needs the app (~16s). **BOTH run in `test-gates`, not in `lint`** — `lint` installs only `ruff`, and both halves import `icdev.core.paths` through `tools/__init__.py`, so the nav check there died on `ModuleNotFoundError: No module named 'icdev.core.paths'` (measured, run 33946644203) — failing for the wrong reason, which is the same argument the swallowed-INSERT gate already records in that file. `icdev-core` is a GIT dependency, not a PyPI one, so installing it into a 30-second linting job buys fast feedback with a network clone and another failure surface; the hook is where fast feedback belongs. Exit `2` (the derivation could not be produced) stays RED and must never be wrapped in `|| true` — a generator reporting "no drift" after its config or markers vanished is the exact failure the gate exists to close.
  - NOT converted here, and named as the next shared surface: `tests/e2e/nav_intelligence_compliance.spec.ts` and `tests/e2e_ui_full_coverage.py` still carry hand-maintained route tables, and the other five dropdowns still carry hand-written active-path lists. Adding one is a `dropdowns:` entry in `args/nav_paths.yaml` plus its own marker pair — the generator is already per-dropdown.
- **TRUST invariants (anti-hallucination / provenance / masking) — enforced by `coherence_checker.py:check_trust_coverage`:**
  - Every LLM-generated artifact (proposals, RFI, DIC, Tech Writer, and any new drafting surface) MUST carry inline `[source: …]` citations validated against its evidence and a persisted provenance/source record. Gate promote/export on citation defects (`citation_guard`, mirroring `placeholder_guard`) with a HITL `force_*` override + audit. Build on the shared `tools/quality/citation_grounding.py` — do not re-implement citation parsing/validation.
  - Both grounding modules (`content_grounding.py`, `citation_grounding.py`) MUST exist in **both** `tools/quality/` and `icdev/tools/quality/`, and `tools/quality` MUST stay in `child_app_generator.py` `DIRECTORY_TREE` so generated child apps inherit them.
  - Redaction is **fail-closed-capable** (`redaction.fail_closed`) at LLM egress and **mask-at-ingestion-capable** (`redaction.mask_at_ingestion`) — both default off; never remove the toggles. Run the `redaction_scan_reflex` sweep for at-rest PII.
- **New dashboard page completeness gate (8 required components — ALL must ship together):**
  1. `tools/dashboard/templates/<canvas>/page.html` — template exists
  2. `icdev/tools/dashboard/templates/<canvas>/page.html` — mirrored to icdev/ package (copy or companion sync)
  3. `@bp.route(...)` in `tools/<canvas>/blueprint.py` — route renders the template
  4. `tools/<canvas>/<module>.py` — backing module with functions the route imports; no ImportError at startup
  5. `tools/<canvas>/constants.py` — any new constants (INTENT_RULES, OBJECT_TYPES, etc.) added
  6. DB migration — created if new tables needed; table existence handled gracefully if migration hasn't run yet
  7. Nav/parent link — page reachable from navigation or a parent page link
  8. **IQE integration** — `tools/iqe/adapters/<canvas>.py` (registers collections), `POST /api/iqe-query` route in blueprint, `{% include "includes/iqe_query_widget.html" %}` in template, canvas entry in `iqe_dispatch()` `_CANVAS_MAP` in `app.py`, path entry in mini-bar `PATH_CANVAS` in `base.html`, ≥3 seed queries in `context/iqe/queries/<canvas>/`
  **Never ship a template without all 7 other components. This has caused repeated failures.**
- **Project cards are MANDATORY for every multi-task build — regardless of origin (CLI session, Kanban, chat request).** Before implementation starts: (1) register the project in `args/projects.yaml` (`key`, `name`, `task_prefix`, `briefs[]`, `epics[]`); (2) seed one kanban task per shippable unit via `tools.kanban.task_factory.create_tasks` (never raw INSERT), with descriptions rich enough for a fresh session to implement from cold. **If you are going to build a task yourself, seed it with `create_tasks(specs, claim=True)`** — or claim it separately with `python tools/kanban/cli.py --claim <id>` — and release it with `--release <id>` when the work lands. The autonomous runner acquires the same `kanban:task:<id>` coordination lease before spending a token and skips any task another live session holds, so a claim is what stops the runner building the task you are already building. WITHOUT IT the two race: a session seeds a task, starts implementing, and the runner picks the same row up — four duplicate PRs in two days (#1784, #1792, #1806, #1807), each needing a human to close the loser. It is not only wasted work: #1807 sat open on `kanban/kpr-fix-02`, so the respawn guard withheld that task from dispatch and the board reported `review_bound` with capacity free — a duplicate blocks the queue behind it. **Do NOT reach for `--pause-runner` instead.** It halts every task on the board to protect one, and it is a 4-hour lease with no renewal that lapses silently — which is exactly how #1806 and #1807 got built hours after a pause was taken. A claim is scoped to the one task and expires safely, so a session that dies delays that task rather than stranding the board (kpr-dup-07). Rationale: token exhaustion mid-build must never lose state — the card + tasks are the handoff. For MANUAL-only work (e.g. private external repos the runner cannot build in), gate all tasks behind a `<prefix>-gate-00` task held `in_progress` so `promote_backlog_to_scheduled` never dispatches them; sessions mark tasks done via `python tools/kanban/cli.py --set-status <id> done` with `.env` loaded. **`done` is merge-verified**: the CLI refuses when a branch carrying that task id still has commits not on `origin/<default>` — open a PR and get it merged first. **Satisfy the gate instead of bypassing it:** `--set-status <id> done --merge` lands the task's PR (requires an OPEN PR based on the default branch, green CI, no requested changes, the enforced done-gate, and the sibling-conflict guard) and marks done only once GitHub reports it `MERGED`; it is strictly stricter than the refusal, fail-closed on every unknown, and it ignores `KANBAN_REQUIRE_MERGE_FOR_DONE`. Add `--dry-run` to preflight without merging. Override with `--force-done --reason '<why>'` (audit-logged) only when there is genuinely nothing to land, and set `KANBAN_REQUIRE_MERGE_FOR_DONE=0` only for repos where git verification cannot apply. This closes the recurring "board says done but it is not on main" bug: the runner and the dashboard move API were already gated, the CLI was not — and the CLI is what worker sessions use to report their own completion. Card mechanics: register it in `args/projects.yaml`: define `key`, `name`, `task_prefix`, `briefs[]`, and `epics[]`. Its progress card appears on Home (`/`) below the Task Board automatically via the reusable partial `tools/dashboard/templates/_projects_in_flight.html`. Task IDs MUST use the form `<task_prefix><epic_key>-<N>` (e.g. `dt-iqe-01`). Rules enforced at render time: two projects may NOT share an IDENTICAL `task_prefix` (the later entry is skipped with a warning, because no predicate can assign a row to one of them); a NESTED prefix is fine and supported — `aadc-` alongside `aadc-enh-` and `aadc-sp-` is a legitimate parent/child namespace, and `tools/project/prefix_scope.py::child_prefixes` subtracts each child's rows from the parent's queries rather than letting the parent absorb them (the older handling DROPPED the colliding entry, which silently hid whichever card came later in the YAML — in practice the 38-epic `aadc` card). Within a project, no epic `key` may be a prefix of another under the `-` separator. Cards auto-hide at 100% done or 0 tasks.
- **`<task_prefix><epic_key>-<N>` is the whole contract — a task no epic key matches is counted by nothing, and the card silently vanishes.** Every number on a card comes from EPIC patterns (`<task_prefix><epic_key>-%`), never from `task_prefix` alone, so seeding `<prefix>foo-01` for an unregistered `foo` — or adding tasks for an epic that never made it into `args/projects.yaml` — drops those rows out of `total`, `done` and the percentage. When ALL of a card's rows are dropped the card disappears entirely, indistinguishable from a project with no work; when only some are, the percentage silently describes a subset, which is the recurring "the card's own progress claim is wrong" complaint. Measured on the live board 2026-08-14: **8 registered cards owned tasks no epic claimed, and only 1 card was rendering at all** — `aiify_2` (150 tasks) and `dic` (27) showed nothing, while `ndc` (17 uncounted), `gdx` (1) and `tsg` (3) had auto-hidden as "100% done" with that work still open. Nothing detected any of it except a human noticing a card was missing. `coherence_checker.py:check_project_card_coverage` now reports it (`warn`, because the finding is board DATA — failing a per-task code gate on it would block unrelated commits), and a card with unclaimed rows stays VISIBLE and says so instead of hiding. **`gate` is a RESERVED epic key**: 30 cards use it, and its only job is to make the `<prefix>gate-00` hold sentinel appear on the card. Never key a work epic `gate` — `tools/kanban/gates.py::is_manual_gate` returns True for any `<card>-gate-<n>` id, so `promote_backlog_to_scheduled` filters every one of that epic's tasks out forever, silently; `task_factory.create_tasks` refuses to seed them, which is where you want to find out. Enforced by `tests/test_project_card_coverage.py`.
- Screenshots: ALWAYS use `playwright/screenshots/<name>.png` as the filename
- In Jinja2 templates, NEVER use `'%%.0f'|format(value)` — use `value|round(0)|int`
- In Behave step definitions, match step text to tool return signatures
- SQL CHECK constraints: derive from Python constants, never hardcode
- Entity types: add to BOTH the Python constant AND the SQL CHECK constraint
- Child apps: ALWAYS use `child_app_generator.py` + `forge_validator.py --gate`
- Before writing tests: ALWAYS run `api_surface_extractor.py --file <module> --json`
- **Cross-platform:** pathlib.Path, `encoding='utf-8'`, `tempfile.gettempdir()`, `datetime.now(timezone.utc)`, `hashlib.sha256` not md5
- **NEVER hand a `/tmp/...` path between Bash and Python on Windows.** In Git Bash, `> /tmp/report.json` writes to the MSYS temp dir (`C:\Users\<you>\AppData\Local\Temp\`). Python's `open('/tmp/report.json')` resolves the path literally, i.e. `C:\tmp\report.json`. **They are different files, and neither call errors.** A redirect that succeeds followed by a read that succeeds can silently serve you a stale file written weeks ago by another session — indistinguishable from fresh output. Write scratch files to the session scratchpad using an absolute Windows path, and pass that path explicitly to both sides. Corollary: when a generated report names a file, `ls` the file before acting on the report — if it does not exist in the checkout, the report is stale, not the tree wrong.
- **LLM config via `.env`**, never hardcode model IDs in Python. A literal like `model="claude-haiku-4-5-20251001"` on an `LLMRequest` pins one vendor into code, and because these call sites are wrapped in `except Exception: pass` an air-gapped or non-Anthropic deployment degrades **silently** rather than erroring. Route by `llm_function` through `LLMRouter` and declare that function's chain in `args/llm_config.yaml` — an undeclared function silently falls back to `routing.default`, so declaring it is part of the fix, not optional. Enforced by `tests/test_no_hardcoded_model_ids.py`, which parses the AST (prose mentioning a model is fine; a value bound to `model=`/`model_id=` is not) across `tools/` **and** the `icdev/tools/` mirror. Pre-existing pins are grandfathered per-file with a count in `args/model_id_gate.yaml` — lower a count when you fix one; do not raise one to get a commit through.
- **New tool/module registration checklist (8 points):**
  1. `tools/manifest/<topic>.md` — add tool entry to the appropriate shard (index at `tools/manifest.md`)
  2. `CLAUDE.md` — add CLI commands to [docs/reference/commands.md](docs/reference/commands.md)
  3. `args/security_gates.yaml` — add gate if blocking/warning conditions
  4. `tools/mcp/tool_registry.py` + `gap_handlers.py` — register in MCP gateway
  5. `.claude/hooks/pre_tool_use.py` — add append-only tables
  6. `tests/conftest.py` — add new table schemas to MINIMAL_ICDEV_SCHEMA
  7. `python tools/dx/companion.py --sync --write --json` — sync to all AI platforms
  8. `python tools/workflow/coherence_checker.py --all --fix --gate` — coherence validation
- **Adding or changing a canvas / child app / feature:** update `args/component_registry.yaml` first. Do not add new Python lists in `tools/dashboard/app.py`, `tools/cli/enable.py`, or `tools/dashboard/templates/base.html`. Mirror any registry file changes to the root `tools/` copy until the canonical/legacy split is removed.
- **Component configuration audit:** when adding an append-only component-audit event, use `log_component_audit()` from `tools.config.component_registry`. New component-audit tables must be added to `APPEND_ONLY_TABLES` in `.claude/hooks/pre_tool_use.py`.

### RLS Bypass Annotations for cwd-Sensitive Hook Logic

`.claude/hooks/pre_tool_use.py` runs before every tool call and contains path-sensitive checks (e.g. resolving `args/file_access_tiers.yaml` and repo-root-relative exemption lists). When Claude Code is invoked from a git worktree or from a directory other than the canonical repo root, `os.getcwd()` can point at the worktree root, so path-sensitive logic may fail or apply the wrong rules. The hook already resolves the repo root via `__file__` (`_get_repo_root()`), but any future line that intentionally bypasses a path/cwd-dependent safety check must be annotated:

```python
# rls-bypass: <reason> — required for task-3bc9eb0918 because cwd changes in worktrees
```

This annotation documents why the bypass is safe and lets `tools/workflow/coherence_checker.py::check_security_context_wiring` distinguish intentional exceptions from accidental wiring gaps. The file involved is `.claude/hooks/pre_tool_use.py`.

#### Notes for agents working from worktrees or non-root directories

1. **Do not rely on `os.getcwd()` for repo-root-relative paths.** In git worktrees the current working directory is the worktree root, while CI runners and test harnesses may change directory into a subdirectory. Always resolve the repository root from a known file location (`__file__`, `pathlib.Path(__file__).resolve()`, or a `REPO_ROOT` constant) rather than from `os.getcwd()`.

2. **Tests started from the wrong directory break RLS/coherence validation.** Running `pytest` from inside `tests/`, `tools/`, or a worktree subdirectory can cause coherence checks to compute relative paths against a non-canonical root, which in turn makes RLS predicates, changed-file scans, and exemption lists fail. Always run the test command from the canonical repo root with an absolute `PYTHONPATH`:

   ```bash
   # Correct (absolute PYTHONPATH, root working directory)
   $env:PYTHONPATH="C:\AI\ICDev"  # Windows
   export PYTHONPATH=/opt/icdev    # Unix
   pytest tests/ -v --tb=short
   ```

3. **GitHub Actions must set the working directory and PYTHONPATH explicitly.** The CI workflow `.github/workflows/icdev-ci.yml` uses a workflow-level `defaults.run.working-directory` and absolute `PYTHONPATH: ${{ github.workspace }}` so that all steps, including coherence/RLS checks, operate against the canonical checkout root rather than whatever directory the runner happens to start in. If you add a new job or reusable workflow, preserve this path isolation.

4. **When a path/cwd check is intentionally bypassed, annotate it.** If you write a line in `pre_tool_use.py` (or any other security hook) that deliberately ignores `os.getcwd()` or otherwise bypasses path-sensitive logic, add the `# rls-bypass:` comment on the same line or immediately above it. Include:
   - The concrete reason the bypass is safe (e.g. repo root resolved via `__file__`, not cwd).
   - The task ID that introduced the bypass, so future `check_security_context_wiring` runs can correlate it with a known change.

5. **If coherence checker reports a security-context wiring gap, check cwd first.** Many "unexpected bypass" findings are actually false positives caused by running the checker from a non-root directory. Re-run from the repo root with `PYTHONPATH` set before concluding that the hook logic is wrong.

6. **Keep the canonical/legacy import namespace distinction in mind.** Tests that patch `tools.xxx` via string form may hit a different object than imports of `icdev.tools.xxx`. This interacts with cwd because the `tools/` shim resolution also depends on `sys.path` order, which is sensitive to how the process was launched. Patch via `importlib.import_module("tools.x")` and `setattr`, and launch tests from a single canonical root.

### Compliance & Security Rules
- All artifacts MUST include classification markings (CUI for IL4/IL5, SECRET for IL6)
- Use `classification_manager.py` for markings — don't hard-code CUI banners
- Audit trail is append-only — NEVER UPDATE/DELETE audit tables
- Security gates block on: CAT1 STIG, critical/high vulns, failed tests, missing markings
- When implementing NIST 800-53 control, call crosswalk engine for FedRAMP/CMMC auto-populate
- Self-healing limited to confidence ≥ 0.7, max 3/hour **per pattern** and 5/hour **across all patterns**. All four thresholds live in `args/heal_constitution.yaml` under `rate_limits` — never hardcode them in a module
- All A2A uses mutual TLS; never store secrets in code
- **SBOM: one per build, and a correction is a new SBOM — never an edit.** The SBOM 2026 Minimum Elements Frequency element requires an SBOM for every software version or update, including a build that only integrates updated dependencies. Pass `--build-id` (or set `$ICDEV_BUILD_ID`; git HEAD of the project directory is the fallback) so `sbom_records.source_revision` records which build the SBOM describes — that column is what makes per-build conformance checkable, and `sbom_revision.evaluate_frequency` reports `sbom_not_regenerated_for_current_build` when it does not match. `sbom_max_age_days: 30` in `args/security_gates.yaml` is the **stale-evidence backstop**, not the rule; do not cite it as the per-build gate. Accommodation of Updates: a corrected SBOM is inserted as a **successor** row via `sbom_revision.apply_correction`, pointing at its predecessor through `supersedes_sbom_id`. Never UPDATE the row being corrected — not even to flag it superseded; that flag is derived at read time by `sbom_revision.revision_chain`, because a recipient may already hold the document the old row describes. Containers non-root, read-only rootfs.
- IL6/SECRET: SIPR-only, NSA Type 1 encryption, air-gapped CI/CD
- **V&V before handoff** — if change affects UI, verify with Playwright MCP before reporting
- **Playwright E2E after dashboard changes** — mandatory post-implementation verification
- **Feature docs** — create `docs/features/phase-{N}-{slug}.md` after each phase
- **Sandbox coverage (OPT-58)** — any new `tools/` module that ingests user-provided content MUST land a decision in [docs/security/sandbox-coverage.md](docs/security/sandbox-coverage.md) (sandboxed / trusted-first-party / sandboxed-on-demand / bypass-documented). Canvas templates are first-party; canvas design JSON is data only. `.tmp/*.py` scripts are dev-scratch only — productize under `tools/` before merge. Enforced by `coherence_checker.py:check_sandbox_coverage`.

---

## Security Gates (Summary)

Gates block on critical conditions. Full definitions: [docs/reference/compliance-security.md](docs/reference/compliance-security.md).

Key gates: Code Review, Merge, Deploy, FedRAMP, CMMC, cATO, DES, Migration, RICOAS, Supply Chain, FIPS 199/200, Marketplace, Multi-Regime, DevSecOps, ZTA, MOSA, AI Security, RAG, Fine-Tuning, Coherence, Acceptance Validation.

---

## Karpathy Principles — Pre-Design Engineering Gate

Before writing code, apply these 5 heuristics from `hardprompts/karpathy_principles.md`:

1. **State assumptions** — Name the constraints, inputs, invariants you're relying on. Unstated assumptions are where bugs hide.
2. **Enumerate interpretations** — For any ambiguous requirement, list the 2–4 ways it could be read before picking one. Surface them to the user if the choice is load-bearing.
3. **Prefer simpler** — Three similar lines beats one clever abstraction. Don't design for hypothetical future requirements. YAGNI.
4. **Bound your edit scope** — Only touch what the task requires. No drive-by refactors, no surrounding cleanup, no speculative error handling.
5. **Success criteria** — State how you'll know the change is done before writing it. If you can't write the test / acceptance check, the spec is incomplete.

Applies to: build, bug fix, refactor, TDD, and code review workflows. Enforced across all 10 AI platform configs by `tools/workflow/coherence_checker.py::check_karpathy_sync`.

---

## Reference Documentation

Detailed reference material (read on-demand, not loaded automatically):

| File | Contents |
|------|----------|
| [commands.md](docs/reference/commands.md) | All CLI commands for every ICDEV™ module |
| [architecture.md](docs/reference/architecture.md) | Agents, MCP servers, languages, skills, deployment, scaling, installation |
| [adrs.md](docs/reference/adrs.md) | All architecture decision records (D1–D360+) |
| [compliance-security.md](docs/reference/compliance-security.md) | Compliance frameworks, crosswalk, security gates, args config |
| [subsystems.md](docs/reference/subsystems.md) | Innovation, Creative, Research engines; RICOAS; SaaS; Marketplace; CI/CD |
| [goals.md](docs/reference/goals.md) | All existing goal workflows with descriptions |
| [testing.md](docs/reference/testing.md) | Testing framework, test commands, E2E specs |
| [databases.md](docs/reference/databases.md) | Database tables, schemas, migration commands |
| [ops/airgap-runbook.md](docs/ops/airgap-runbook.md) | Running ICDEV™ outside Claude Code — cron, CI/CD, air-gap LLM, headless ANVIL |

---

## Continuous Improvement

Every failure strengthens the system: identify what broke → fix the tool → test it → update the goal → next run succeeds automatically.

Be direct. Be reliable. Get shit done.
