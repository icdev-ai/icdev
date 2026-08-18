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
# FathomDesk authenticated smoke test (MANDATORY after any FathomDesk change)
python tools/testing/fathomdesk_smoke.py                            # reads .env for credentials
python tools/testing/fathomdesk_smoke.py --fast                     # skip DB schema checks
python tools/testing/fathomdesk_smoke.py --json                     # machine-readable

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

# Capability consumption — is a DECLARED capability actually being used? (#exa-live-01)
python tools/awareness/capability_consumption.py --json                  # all classes, 30d window
python tools/awareness/capability_consumption.py --window-days 7         # configurable window
python tools/awareness/capability_consumption.py --class reflex --json   # one class
python tools/awareness/capability_consumption.py --known-inert --json    # the 5 known-inert cases
python tools/awareness/capability_consumption.py --gate                  # exit 1 if a class is UNMEASURABLE
# Reuses existing telemetry only (genesis_reflex_state, studio_mcp_dispatch_audit,
# agent_approval_log, audit_platform, prompt_versions, audit_trail, agent_improvement_artifacts).
# A missing table reports telemetry_available:false — never a misleading zero.
# Config: args/capability_consumption.yaml

# Substrate probe — does the thing you are about to design against HAVE ROWS? (#trust-disc-04)
python tools/awareness/capability_consumption.py --probe-plan <plan.md> --substrate-gate  # BEFORE writing code
python tools/awareness/capability_consumption.py --probe-substrate kg_ontology            # one table
python tools/awareness/capability_consumption.py --probe-substrate kg_nodes.ontology_id   # one column
python tools/awareness/capability_consumption.py --probe-diff origin/main --json          # what the branch reads
python tools/awareness/capability_consumption.py --substrates                             # the curated declared list
# empty (writer never ran) / absent (migration never ran) / column_unpopulated (rows exist,
# column 100% NULL) are never merged — they send you to different fixes. On a database with
# no operating history everything reports UNMEASURABLE and the gate exits 0.
# Gate consumer: coherence_checker.py --check substrate_liveness (warn).

# Audit hash-chain integrity — is the audit_trail chain actually intact? (#exa-audit-04)
python tools/audit/chain_sweep.py --json           # whole-table sweep, four buckets
python tools/audit/chain_sweep.py --gate           # exit 1 if any chained link is broken
python tools/audit/chain_sweep.py --verify-signatures
python tools/audit/chain_sweep.py --db-path <db>   # sweep an evidence copy / tenant db
# verified | pre_cutover (unverifiable, NOT tampered) | unchained (writer bypassed) | BROKEN
# BROKEN is the only tamper signal. Signatures are reported separately and never counted
# broken — an unsigned deployment must not read as 100% tampered.
# UI: /provenance -> "Audit Chain Integrity"   API: /api/govchain-provenance/chain-health
# Cadence: the genesis `audit` reflex (args/genesis_config.yaml -> reflexes.audit.checks)

# Per-provider prompt-cache effectiveness — not one aggregate number (#cch-obs-01)
python tools/cache_savings/by_provider.py --json
python tools/cache_savings/by_provider.py --window-days 30
python tools/cache_savings/by_provider.py --provider anthropic --json
# Reads ai_telemetry (cch-tel-01's per-call ledger), NOT llm_response_cache — that table
# answers "was a call avoided outright" and only holds response-cached results, so it can
# never describe cached INPUT tokens on a call that still happened.
# FOUR zeroes the old single hit rate merged into one, of which only the third is a defect:
# no_data (nobody called it) | unreported (transport reports no counters — claude-cli
# carried 626 such calls) | no_cache_hits (a real measured 0%) | caching. cached_share_pct
# is None, never 0.0, for the first two. usd_basis: local (Ollama) has no bill, so
# usd_saved is None and latency is reported — $0.00 there reads as "caching failed" for a
# cache that works and simply is not billed.
# Token accounting is per provider and NEVER summed across shapes: Anthropic/Bedrock report
# input_tokens DISJOINT from cache tokens, OpenAI/Azure report cached tokens as a SUBSET.
# The same raw numbers give 28.57% vs 40.00%; averaging them double-counts every OpenAI
# cached token — which is exactly what the one aggregate did. No blended rate is emitted.
# A database with no operating history reports UNMEASURABLE, never a wall of no_data.
# Claims are provider-keyed, NEVER model-keyed: args/cache_effectiveness.yaml
# UI: /cache-savings -> "Prefix Cache by Provider"   API: /api/cache-savings/by-provider

# DocMod asks ONE governed seam instead of hand-querying tables (#cef-di-01)
# A library, no CLI. Import it:
#   from tools.doc_modernization.evidence import (
#       resolve_evidence, currency_assertion, graph_citations, run_stats)
#   bundle = resolve_evidence("Catalyst 6500", entity_type="hardware_model")
#   hit = currency_assertion(bundle)   # entity_currency.resolve()'s exact dict shape
# Toggle: `cortex.enabled` in args/docmod/docmod_config.yaml — DEFAULT OFF, and
# off means the seam is NEVER consulted, so the rollback is a flag flip and not
# a merge revert. TRUST rule 1 is unchanged: resolve() supplies the EVIDENCE and
# DomainPack.evaluate() still derives the verdict, from TYPED fields, with no
# model call anywhere (resolve passes corrective=False, so even the CRAG rewrite
# does not run). Only `extraction: structured` claims are handed to a pack — a
# claim read off a retrieved document's PROSE, or a pack's own verdict returning
# through the fan-out, can never become a verdict.
# cortex.resolve() RUNS the packs (resolver.assess), so a pack calling it inside
# evaluate() recurses without bound: a thread-local guard returns None for a
# re-entrant ask and the pack takes its legacy read. Same for a spent
# `max_resolves_per_run` budget, an absent Cortex, or a governance refusal —
# every one of them degrades to the legacy path and none can fail a sweep.
# Bounds are REPORTED (`run_stats()` -> resolutions / capped), never silent.
# Migrated: packs/network_hardware.py::_currency_hit (the entity-currency
# lookup), packs/policy_refs.py::_kg_corroboration (the kg_nodes SELECT),
# scanner.py::_enrich_evidence (governed citations on every finding written).
# NOT migrated, on purpose: docmod_eol_products, docmod_nist_pubs (revision_num
# compared numerically), dic_chunk_links/rag_chunks (a hash equality),
# dic_documents (a timestamp comparison) — those are EXACT values a ranked
# retrieval seam cannot return, and swapping them would turn a proven verdict
# into an approximation.

# Is this entity still current? ONE store, any source, any domain (#cef-fnd-04)
python -m tools.currency.entity_currency --backfill --json
python -m tools.currency.entity_currency --stats --json
python -m tools.currency.entity_currency --resolve "<entity>" --entity-type hardware_model
# One row per (source, entity, version) ASSERTION in `entity_currency`. Sources
# are declared in args/entity_currency.yaml — tools/currency/ names no table,
# column, vendor, product or domain, so a fourth provider is a config entry.
# Disagreement is PRESERVED: two sources that disagree keep two rows, resolve()
# picks a winner at READ time and returns the losers under `others` with
# conflict:true. Curated sources are `authoritative` and win ahead of confidence
# AND recency — a tie-break that a bumped prior can overturn is not authority.
# `confidence` is a DECLARED PRIOR, not a measurement. `as_of` (the source's
# clock) is kept apart from `observed_at` (ours) so stale evidence stays
# distinguishable from fresh. Refreshed by the doc_modernization_sweep reflex.
# The de-facto learner's input is now a DECLARATION too
# (args/docmod/inventory_feeds.yaml): docmod_defacto_standards held 0 rows for
# months not because the writer never ran but because its only input, ni_devices,
# held 0 rows. Each learned row records source_feed + evidence_kind and share_pct
# is a share WITHIN one feed — an observed estate beats a drawing of one, and no
# quantity of drawings adds up to an observation.

# Agent adapter capability matrix — DECLARED vs ACTUAL per adapter (#exa-bench-03)
python tools/agents/capability_matrix.py --json          # 5 adapters x 7 capabilities
python tools/agents/capability_matrix.py --adapter claude_cli
python tools/agents/capability_matrix.py --capability sandbox_passthrough
python tools/agents/capability_matrix.py --gate          # exit 1 on a declared-but-absent capability
# actual is present | absent | unconfirmed — and unconfirmed is NEVER folded into
# either. Only behavioral (adapter code run) and interface (live object inspected)
# probes may assert present/absent; a source_evidence probe may only say unconfirmed.
# Routing consults it: pick_default("build", require=["sandbox_passthrough"]) skips
# any adapter not MEASURED present. Claims: args/agent_capabilities.yaml
# NOT a replacement for tools/workflow/executor_parity.py — that one replays a task
# corpus in worktrees to measure OUTCOME parity. This measures CAPABILITY parity.

# PreToolUse hook enforcement — the hook's exit 2 now reaches the caller (#exa-bench-05)
python tools/hooks/fire_rate_survey.py --json               # per-check fire rate over recent sessions
python tools/hooks/fire_rate_survey.py --check dangerous_rm --samples 25
python tools/hooks/fire_rate_survey.py --gate --max-fire-rate 0.01
# `.claude/settings.json` no longer wraps the hook in `|| true`, so a BLOCKED
# refusal actually blocks. Stand it down with ICDEV_PRETOOLUSE_ENFORCE=0 (every
# check still runs and prints, prefixed ADVISORY:) or per check with
# ICDEV_<CHECK>_GUARD=0 — see CHECK_KILL_SWITCHES in .claude/hooks/pre_tool_use.py.
# NEVER re-add a shell neutraliser: run the survey, then narrow the check.
# Corpus is the Claude Code transcripts; hook_events stores tool-input KEY NAMES
# only, so it cannot drive a replay and reports itself unusable.

# Is this task id ALREADY on main? task -> main, not task -> PR (#trust-disc-05)
python -m tools.kanban.landed_check --task <task-id> --json
python -m tools.kanban.landed_check --all --json            # every non-terminal task
python -m tools.kanban.landed_check --task <task-id> --gate  # exit 1 if already on main
# The board tracks task -> PR; nothing checked task -> main, so a task whose work had
# already merged under a different PR number got dispatched again and opened a PR that
# could only land as a REVERT (#1651: -38/+26 on rest_v1.py).
# Evidence is tiered: merge_ref | subject BLOCK, body NEVER does (a body mention is a
# citation as often as a landing). Boundary-matched, so ctx-perf-02 != ctx-perf-021 and a
# parent id != its children. FAIL-OPEN — `checked: false` is never a clean answer.
# Advisory by default; KANBAN_LANDED_CHECK=enforce refuses, =off disables. Survey first.

# Does an epic CLAIM this task id? Surveyed, then armed to `report` (#rem-hyg-03/04)
python -m tools.kanban.identity_survey --json            # every id, machine-readable
python -m tools.kanban.identity_survey                   # per-card table + headline rates
python -m tools.kanban.identity_survey --env-file /path/to/.env   # running from a worktree
# KANBAN_IDENTITY_CHECK=enforce|report|off, default `report` — same shape as
# KANBAN_LANDED_CHECK, read by tools/kanban/task_identity.py::mode and consulted by
# create_tasks BEFORE any insert, so a refusal cannot half-land a batch. The default is
# what the survey SUPPORTS, not arming left half-done. Measured 2026-08-16 on the live
# board (3,244 rows): refusing every unclaimed id = 35.17%. The NARROWING is the finding:
# 789 of the 1,119 no_card rows are opaque machine ids — `task-<hex>` is what the
# dashboard's own create-task API and `awareness/suggested_card_writer` generate — so
# refusing them refuses routine work, the exact defect the PreToolUse survey found.
# Exempting them (`is_enforceable`, the ONE predicate the survey's NARROWED column and
# the seeder's refusal both call) gives 10.85% lifetime and 15.81% over 30d — ten times
# the rate this file already calls refusing routine work, so `enforce` is offered and
# documented, not defaulted. Re-survey before changing that; do NOT instead widen an
# exemption list, and do NOT drop `no_card`, which is the case the card exists for.
# Scope: 95 modules INSERT INTO kanban_tasks directly and never reach `create_tasks`.
# UNMEASURABLE is never 0%: an unreadable projects.yaml, and an empty board — the worktree
# trap, where a missing .env silently reads a throwaway SQLite DB. The survey itself stays
# REPORT ONLY with no --gate; `enforcement.mode` in its output says which posture is live.

# An undeclared third-party import that fails SILENTLY (#tsg-iso-03)
python tools/ci/undeclared_import_census.py --check          # the gate; exit 1 on a NEW site
python tools/ci/undeclared_import_census.py --json
python tools/ci/undeclared_import_census.py --changed tools/foo.py --check
python tools/ci/undeclared_import_census.py --staged
python tools/ci/undeclared_import_census.py --prune          # drop entries whose site is gone
# The finding is a CONJUNCTION, never the undeclared import alone: an
# UNDECLARED third-party package imported inside a handler that SWALLOWS --
# returns/passes/continues without logging, raising, or otherwise recording
# that it fired. A genuinely optional dependency behind a handler that NAMES
# the missing package is correct and passes (tools/blockchain/transports does
# it properly), so a site leaves by fixing EITHER half.
# `python-dateutil` had the bad shape at two sites: the stale reaper skipped
# EVERY task and had never once run on CI, and every notification duration
# rendered "unknown". It passed on Windows, where dateutil arrives
# transitively as somebody else's dependency, and failed on the CI runner and
# on any air-gapped install -- the deployment this project targets. That
# asymmetry is what kept it alive. Both sites are now stdlib
# (`tools.common.helpers.parse_utc_timestamp`); dateutil was DELETED, not
# declared, and `tests/test_no_undeclared_dateutil.py` bans it outright.
# Import name is mapped to DISTRIBUTION name (`yaml` -> `pyyaml`) from a
# curated table -- `packages_distributions()` only knows what is INSTALLED, so
# on the very runner where a package is missing it reports nothing.
# 210 sites grandfathered BY NAME in args/undeclared_import_census.txt;
# `undeclared_max` in args/undeclared_import_gate.yaml may only go DOWN.

# Red-first proof — did the changed test actually go RED? (trust-disc-01)
python tools/ci/red_first_gate.py --gate                 # the merge gate (0 clean / 1 finding / 2 could-not-run)
python tools/ci/red_first_gate.py                        # report only, always exit 0
python tools/ci/red_first_gate.py --files tests/test_x.py --gate
python tools/ci/red_first_gate.py --json --out red-first-proof.json
# Checks out the merge base, applies ONLY the changed test file on top, and
# asserts it does NOT pass there. The captured merge-base pytest output IS the
# recorded RED. Exempt a file with a WRITTEN REASON in args/red_first_gate.yaml;
# never `mode: advisory` and never a shell neutraliser.

# Which open PRs are awaiting merge, and WHY is each one not merging? (kpr-watch-01)
python -m tools.ci.merge_readiness --json          # every open PR, task-linked or not
python -m tools.ci.merge_readiness                 # human table
python -m tools.ci.merge_readiness --state awaiting_ci --state conflicting
python -m tools.ci.merge_readiness --from-json prs.json --default-branch main
python -m tools.ci.merge_readiness --state behind_main       # the stale ones
python -m tools.ci.merge_readiness --no-measure-behind       # skip the /compare calls
# READ-ONLY: it never merges, pushes, un-drafts or closes. `pr_watcher`'s unlinked
# sweep and this report read the SAME pure function,
# classify_merge_readiness(pr, *, default_branch, linked_urls, behind_by) ->
# (state, reason), so the report can never describe a merge policy the merger
# does not have. Do NOT write a second copy of the ladder. States: merged |
# linked | draft | held_label | wrong_base | conflicting | no_checks |
# ci_failed | awaiting_ci | changes_requested | behind_main | ready.
# `no_checks` (empty rollup) is never merged into `awaiting_ci`, and
# mergeable=UNKNOWN reports a different REASON from CONFLICTING. Exit 2 = the
# report could not be produced, never an empty table.
#
# `behind_main` (kpr-stale-02) is THE SAFETY HOLE this ladder was missing.
# `mergeable` answers only "does this collide TEXTUALLY", and GitHub reports
# MERGEABLE for a branch arbitrarily far behind main — so the CONFLICTING
# interlock caught only the colliding subset and the rest merged CLEANLY,
# re-applying their diff over a tree that had moved on (#1651: -38/+26 on
# rest_v1.py, 36 commits behind). Do NOT key this on `mergeStateStatus` alone:
# it is BEHIND only where the base branch has `required_status_checks.strict`,
# which this repo does NOT — measured 2026-08-18, it reads CLEAN for a branch
# 217 commits behind. The forge verdict is the belt; the measured count from
# `measure_behind_by` (the /compare endpoint, NOT a local git rev-list, which
# understates staleness whenever origin/main is itself stale) is the check.
# Threshold `max_behind_commits: 10` in args/pr_watcher_config.yaml, surveyed:
# over 120 merged PRs the routine population tops out at 8 behind at merge
# (p50 1, p90 5, p95 6), so it fires on 0 of them and on #1651.
# Measured LAST, and only for a PR every cheaper rung already passed — it is
# the one rung that costs a forge round-trip. UNMEASURED is `None` and prints
# "?", NEVER 0, and is FAIL-OPEN: a forge that cannot answer must not freeze
# the pipeline. The repair differs by door — a task-linked kanban/<id> branch
# goes to the existing `_maybe_rebase` path (same ownership refusal, same
# per-base-era budget) and raises a HITL alert when that declines; an UNLINKED
# PR is reported and LEFT ALONE, because the sweep never pushes.
# Raw board writers — does this INSERT bypass the canonical seeder? (rem-hyg-05)
python tools/kanban/raw_insert_census.py --check          # the gate; exit 1 on a NEW raw INSERT
python tools/kanban/raw_insert_census.py --json           # full report
python tools/kanban/raw_insert_census.py --changed tools/foo.py --check
python tools/kanban/raw_insert_census.py --staged         # only what this commit touches
python tools/kanban/raw_insert_census.py --prune          # drop entries whose site is gone
python tools/workflow/coherence_checker.py --check board_writer_census --gate
# 219 raw `INSERT INTO kanban_tasks` sites bypass task_factory today (42 of them
# in tools/genesis/reflexes/*, the autonomous path). They are grandfathered BY
# NAME in args/kanban_raw_insert_census.txt; a NEW one fails. The fix is
# `from tools.kanban.task_factory import create_tasks`. Converting the 219 is
# rem-hyg-06. `raw_insert_max` in args/board_writer_gate.yaml may only go DOWN.

# DataBridge external rung — 33 connectors, now ONE authorized (#cef-fnd-03)
python -m tools.databridge.seed_connections --seed --json     # db_connections <- args/databridge_connections.yaml
python -m tools.databridge.seed_connections --dry-run --json  # validate, write nothing
python -m tools.databridge.seed_connections --verify --json   # row present? credential resolves?
python -c "from icdev.tools.databridge import broker; print(broker.list_available('doc_reviewer'))"
# TWO files, on purpose: args/databridge_agent_access.yaml is the AUTHORIZATION
# (connector+table allowlist, per-agent grants, classification ceiling) and
# args/databridge_connections.yaml is the ENDPOINT (url, egress allowlist,
# auth_secret_ref). Different reviewers, different cadence; merging them would
# reopen the security review on every endpoint edit.
# NO SECRET VALUE IN EITHER. auth_secret_ref must be an env:/vault:/aws:/file:
# REFERENCE and the seeder REFUSES a literal — refused, not warned, because a
# warning still lands the secret in git. classification is the LABEL
# (UNCLASSIFIED/CUI/...), never the banner 'CUI // SP-CTI': that column feeds the
# RLS predicate, and a banner matches no label at any clearance, so the row is
# written, retained and invisible.
# Three things had to be fixed before ANY grant could work, each silent:
#  * connectors register on IMPORT and nothing imported them, so every fetch died
#    at "connector 'rss' is not registered" — and `tools.databridge.registry` vs
#    `icdev.tools.databridge.registry` are two module objects with two _REGISTRY
#    dicts, so the connectors registered into the copy the broker does not read;
#  * `connection_id` was decorative — the broker never read the row, so a
#    per-connection egress_allowlist was declared and never enforced;
#  * rss_connector does not extend saas_base and so fetched with NO egress guard,
#    while being the one connector an agent can reach through the broker.
# Every call, allowed or denied, writes one databridge_agent_access_log row.

# GEPA Optimizer — Genome Evolution Pressure Analyzer (MCP tool: gepa_optimizer)
python tools/skills/gepa_optimizer.py --json           # Run optimization pass (prune low-fitness genome entries)
python tools/skills/gepa_optimizer.py --dry-run --json # Scan without writing changes
# MCP tool: gepa_optimizer  params: dry_run (bool, default false)
#   Returns: {applied: [...], declined: [...], skipped: [...], errors: [...]}
# rem-cap-01: GEPA records a DECISION against every artifact it evaluates, not
# only the ones it applies. `declined_no_delta` / `declined_low_score` /
# `declined_unmappable_skill` are TERMINAL (nothing rescores an artifact after
# insert, and a blank skill_used can never resolve) so the artifact leaves the
# queue; `declined_skill_file_missing` / `declined_rubric` /
# `declined_empty_patch` are retried next cycle. Consumption for the
# `skill_optimizer` capability class is a recorded decision, applied OR
# declined — counting applies alone made "GEPA ran and correctly declined
# everything" read identically to "GEPA never ran", which is the exact defect
# capability_consumption exists to catch. `status` is deliberately untouched:
# skills_lifecycle.py and ace/blueprint.py read status='pending' as NOVA's
# proposal queue.
```

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

ICDEV™ is fully operable without the Claude Code CLI. Use `tools/airgap/` as the runtime shim — it replicates hooks, session management, and safety gates as plain Python.

### Quick Start

```bash
# Detect environment (cloud vs air-gap)
python -m tools.airgap --detect --json

# Activate local-only LLM routing (air-gap mode)
python -m tools.airgap --activate

# Health check before any risky operation
python tools/testing/health_check.py --json
```

### Cron Job Setup

```bash
# /etc/cron.d/icdev-audit — nightly compliance scan
0 2 * * * icdev-user cd /opt/icdev && \
  python -c "
from tools.airgap.hook_compat import get_session_id, run_auto_commit
get_session_id()   # sets CLAUDE_SESSION_ID + ICDEV_SESSION_ID for audit trail
# ... invoke tools here (health_check, bandit, etc.) ...
run_auto_commit('chore: nightly audit auto-commit')
" >> /var/log/icdev/cron.log 2>&1
```

### CI/CD Pipeline (GitLab Stage End)

```yaml
# .gitlab-ci.yml — security gate + auto-commit at stage end
security-scan:
  stage: validate
  script:
    - export ICDEV_AUTO_COMMIT=true
    - python tools/testing/health_check.py --json
    - python -m bandit -r tools/ --severity-level medium
    - python -c "from tools.airgap.hook_compat import run_pre_tool_check; \
        r = run_pre_tool_check('Bash', {'command': 'git push'}); \
        exit(0 if r['allowed'] else 1)"
    - python -c "from tools.airgap.hook_compat import run_auto_commit; \
        run_auto_commit('ci: post-scan auto-commit')"
```

### Headless ANVIL Workflow

All 10 core ANVIL commands are runnable headlessly via `tools/anvil/<name>.py`.
Each wrapper parses its source (`.claude/commands/*.md` or an `icdev-*` skill),
extracts documented `python tools/…` steps, substitutes `$ARGUMENTS`, and runs
them with an allowlisted prefix.

```bash
python tools/anvil/status.py --json                       # skill-backed
python tools/anvil/feature.py --dry-run -- "add foo bar"  # md-backed preview
python tools/anvil/feature.py --json -- "add foo bar"     # full execution
```

Available wrappers (11 total): `feature`, `bug`, `chore`, `test`, `review`,
`commit`, `status`, `monitor`, `maintain`, `secure`, `deploy`.

### Skill Invocation (Headless)

Every `.agents/skills/icdev-*` skill is invokable from a non-Claude shell.

```bash
python tools/skills/invoke.py --list --json              # list all skills
python tools/skills/invoke.py --show icdev-status        # print skill card
python tools/skills/invoke.py --dry-run icdev-secure     # preview commands
python tools/skills/invoke.py --exec icdev-status --json # execute + capture output
python tools/skills/invoke.py --exec icdev-secure -- --scan tools/ --json  # with args
```

Allowlisted command prefixes: `python tools/`, `python -m tools`, `python -c`. Shell builtins, curl, etc. are refused.

### Air-Gap LLM Routing (Ollama-only)

```bash
# .env — forces all routing through local Ollama, no cloud fallback
OLLAMA_BASE_URL=http://localhost:11434
ICDEV_LLM_PROVIDER=ollama
# Also set in args/llm_config.yaml: two_tier.enabled: false
```

```python
# Programmatic activation
from tools.airgap import is_airgap, activate_airgap
if is_airgap():
    activate_airgap()   # patches llm_config.yaml routing to local-only
```

### Validation in Air-Gap Mode

```bash
python tools/testing/health_check.py --json                            # env + DB + deps
python tools/testing/e2e_runner.py --run-all --mode native --json      # UI lifecycle tests
python -m bandit -r tools/ --severity-level medium                     # security scan
python tools/workflow/coherence_checker.py --all --gate                # coherence gate
```

> **Long-form reference:** [docs/ops/airgap-runbook.md](docs/ops/airgap-runbook.md)

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
- When adding a new dashboard page route, ALWAYS add it to the `Pages:` line in `.claude/commands/start.md`
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
- **Project cards are MANDATORY for every multi-task build — regardless of origin (CLI session, Kanban, chat request).** Before implementation starts: (1) register the project in `args/projects.yaml` (`key`, `name`, `task_prefix`, `briefs[]`, `epics[]`); (2) seed one kanban task per shippable unit via `tools.kanban.task_factory.create_tasks` (never raw INSERT), with descriptions rich enough for a fresh session to implement from cold. Rationale: token exhaustion mid-build must never lose state — the card + tasks are the handoff. For MANUAL-only work (e.g. private external repos the runner cannot build in), gate all tasks behind a `<prefix>-gate-00` task held `in_progress` so `promote_backlog_to_scheduled` never dispatches them; sessions mark tasks done via `python tools/kanban/cli.py --set-status <id> done` with `.env` loaded. **`done` is merge-verified**: the CLI refuses when a branch carrying that task id still has commits not on `origin/<default>` — open a PR and get it merged first. **Satisfy the gate instead of bypassing it:** `--set-status <id> done --merge` lands the task's PR (requires an OPEN PR based on the default branch, green CI, no requested changes, the enforced done-gate, and the sibling-conflict guard) and marks done only once GitHub reports it `MERGED`; it is strictly stricter than the refusal, fail-closed on every unknown, and it ignores `KANBAN_REQUIRE_MERGE_FOR_DONE`. Add `--dry-run` to preflight without merging. Override with `--force-done --reason '<why>'` (audit-logged) only when there is genuinely nothing to land, and set `KANBAN_REQUIRE_MERGE_FOR_DONE=0` only for repos where git verification cannot apply. This closes the recurring "board says done but it is not on main" bug: the runner and the dashboard move API were already gated, the CLI was not — and the CLI is what worker sessions use to report their own completion. Card mechanics: register it in `args/projects.yaml`: define `key`, `name`, `task_prefix`, `briefs[]`, and `epics[]`. Its progress card appears on Home (`/`) below the Task Board automatically via the reusable partial `tools/dashboard/templates/_projects_in_flight.html`. Task IDs MUST use the form `<task_prefix><epic_key>-<N>` (e.g. `dt-iqe-01`). Rules enforced at render time: two projects may NOT share an IDENTICAL `task_prefix` (the later entry is skipped with a warning, because no predicate can assign a row to one of them); a NESTED prefix is fine and supported — `aadc-` alongside `aadc-enh-` and `aadc-sp-` is a legitimate parent/child namespace, and `tools/project/prefix_scope.py::child_prefixes` subtracts each child's rows from the parent's queries rather than letting the parent absorb them (the older handling DROPPED the colliding entry, which silently hid whichever card came later in the YAML — in practice the 38-epic `aadc` card). Within a project, no epic `key` may be a prefix of another under the `-` separator. Cards auto-hide at 100% done or 0 tasks.
- **`<task_prefix><epic_key>-<N>` is the whole contract — a task no epic key matches is counted by nothing, and the card silently vanishes.** Every number on a card comes from EPIC patterns (`<task_prefix><epic_key>-%`), never from `task_prefix` alone, so seeding `<prefix>foo-01` for an unregistered `foo` — or adding tasks for an epic that never made it into `args/projects.yaml` — drops those rows out of `total`, `done` and the percentage. When ALL of a card's rows are dropped the card disappears entirely, indistinguishable from a project with no work; when only some are, the percentage silently describes a subset, which is the recurring "the card's own progress claim is wrong" complaint. Measured on the live board 2026-08-14: **8 registered cards owned tasks no epic claimed, and only 1 card was rendering at all** — `aiify_2` (150 tasks) and `dic` (27) showed nothing, while `ndc` (17 uncounted), `gdx` (1) and `tsg` (3) had auto-hidden as "100% done" with that work still open. Nothing detected any of it except a human noticing a card was missing. `coherence_checker.py:check_project_card_coverage` now reports it (`warn`, because the finding is board DATA — failing a per-task code gate on it would block unrelated commits), and a card with unclaimed rows stays VISIBLE and says so instead of hiding. **`gate` is a RESERVED epic key**: 30 cards use it, and its only job is to make the `<prefix>gate-00` hold sentinel appear on the card. Never key a work epic `gate` — `tools/kanban/gates.py::is_manual_gate` returns True for any `<card>-gate-<n>` id, so `promote_backlog_to_scheduled` filters every one of that epic's tasks out forever, silently; `task_factory.create_tasks` refuses to seed them, which is where you want to find out. Enforced by `tests/test_project_card_coverage.py`.
- Screenshots: ALWAYS use `playwright/screenshots/<name>.png` as the filename
- In Jinja2 templates, NEVER use `'%%.0f'|format(value)` — use `value|round(0)|int`
- In Behave step definitions, match step text to tool return signatures
- SQL CHECK constraints: derive from Python constants, never hardcode
- Entity types: add to BOTH the Python constant AND the SQL CHECK constraint
- Child apps: ALWAYS use `child_app_generator.py` + `forge_validator.py --gate`
- Before writing tests: ALWAYS run `api_surface_extractor.py --file <module> --json`
- **Cross-platform:** pathlib.Path, `encoding='utf-8'`, `tempfile.gettempdir()`, `datetime.now(timezone.utc)`, `hashlib.sha256` not md5
- **NEVER hand a `/tmp/...` path between Bash and Python on Windows.** In Git Bash, `> /tmp/report.json` writes to the MSYS temp dir (`C:\Users\<user>\AppData\Local\Temp\`). Python's `open('/tmp/report.json')` resolves the path literally, i.e. `C:\tmp\report.json`. **They are different files, and neither call errors.** A redirect that succeeds followed by a read that succeeds can silently serve you a stale file written weeks ago by another session — indistinguishable from fresh output. Write scratch files to the session scratchpad using an absolute Windows path, and pass that path explicitly to both sides. Corollary: when a generated report names a file, `ls` the file before acting on the report — if it does not exist in the checkout, the report is stale, not the tree wrong.
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
