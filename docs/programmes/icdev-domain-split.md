# ICDEV[domain] split — a shared core, ICDEV[IT], and a second parent ICDEV[FT]

> Programme document for the ICDEV[domain] split. Approved by the owner 2026-08-21. The
> authoritative, unabridged plan (including ICDEV[FT] internals) lives in the private FT repo.

## Context

ICDEV today is ICDEV[IT]: a FORGE-framework "system that builds systems" whose knowledge, gates and
generated outputs are all IT / DoD-compliance shaped. The owner wants a **second parent**, ICDEV[FT],
in a **separate private repo**, that ingests open research (arXiv q-fin / cs.LG, OpenAlex, SSRN-open,
SEC EDGAR, FRED) into a knowledge base + knowledge graph, and lets a trader ask for a trading system
("momentum day-trading", "RL agent", ...) which ICDEV[FT] then builds, backtests, paper-trades and —
behind a human gate — runs live. ICDEV[FT] is NOT a child app; it is a sibling parent, and the split
must be designed as a reusable `ICDEV[domain]` pattern (ICDEV[Bio], ICDEV[Legal] later).

## Decisions taken with the owner (2026-08-21)

| # | Decision | Answer |
|---|----------|--------|
| 1 | Repo | **Separate private git repo** (this repo is public) |
| 2 | Kernel | **Extract a domain-neutral core** both parents depend on — not a fork |
| 3 | Pattern | **Design `ICDEV[domain]` as a pattern**; FT is the proving ground |
| 4 | v1 scope | **Live trading IS in v1** (research → build → backtest → paper → live) |
| 5 | FathomDesk | **Migrate `tools/trading` (87k lines) OUT of ICDEV[IT] into ICDEV[FT]** as the first domain pack |
| 6 | Vibe-Trading | **Study it, do NOT depend on it** — own the code; ingest it as knowledge |
| 7 | Operator | **Single operator, local-first** — drop Stripe/OAuth/MFA/WebAuthn/tenancy/gamification |
| 8 | Live gate | **HITL approval + kill-switch on EVERY live activation**, audited, never autonomous |
| 9 | RL | **RL is a v1 requirement** (gymnasium env, trainer, policy runner) |
| 10 | Database | **Separate PG database (`icdev_ft`) + own dashboard port** |
| 11 | Provenance | **Mandatory, promotion-gated** paper-level citations (`[source: arxiv:… §…]`) |
| 12 | Autonomy | **Kanban + scheduler + runner + genesis reflexes from day one** |
| 13 | Markets | **Multi-asset data model in v1** (equity, futures/commodity, forex, crypto spot+perp, options); engines phased |
| 14 | Sources | **Open sources only required**; paid vendors are optional adapters |
| 15 | Output form | **Each built trading system is a standalone repo** that registers back to ICDEV[FT] |
| 16 | Language | **Python-only** generated systems in v1 |
| 17 | UI stack | **React + TypeScript SPA** (Vite, design system, TradingView Lightweight Charts); Node at BUILD time only, runtime pure Python serving prebuilt assets |
| 18 | UI rebuild | **Fresh, design-first UI**; FathomDesk's 42 Jinja templates retired, their logic kept behind JSON/OpenAPI; ICDEV[IT]'s dashboard look is explicitly NOT the bar |

## Evidence from the surveys (what exists today)

### Already in ICDEV[IT] that ICDEV[FT] needs
- **FathomDesk** `tools/trading/` — 258 py / ~87.5k lines, 137 live `ad_*` tables in the IT PG DB
  (156 `CREATE TABLE IF NOT EXISTS ad_*` sites, inline in `tools/trading/db.py` + per-subsystem
  `db.py`; only 11 are migrations), 9 broker adapters (`brokers/`), execution +
  reconciliation (`execution/`), risk incl. `risk/kill_switch.py`, options (24 modules), regime HMM
  (`ml/regime_hmm.py`), Fama-French factors (`factors/`), TA, news, multi-agent analyst panel.
  Backtester lives in `tools/fathomdesk/backtester.py` (event-driven, FIFO lots, Sharpe/Calmar).
  NOT in `args/component_registry.yaml`; bolted into `tools/dashboard/app.py` (~L2892, L10239-10321);
  8 `fathomdesk_*` genesis reflexes; 21 `args/*trading*|fathomdesk*|options_*` yamls +
  `args/llm_config_trading.yaml`; 11 `ad_*` migrations in `tools/db/migrations/`; 26 test files;
  project card `fdt` in `args/projects.yaml`. Already excluded from the wheel
  (`tools/installer/sync_package_tree.py:76 PARENT_ONLY_DIRS`, pyproject `exclude`).
  `yfinance` is an UNDECLARED import (census-listed). No RL anywhere.
- **Research verticals** `context/research/verticals/trading.json` + `fintech.json` already declare
  arXiv cats `q-fin.TR/PM/RM/CP, cs.AI, cs.LG`. `tools/research/source_scanner.py::scan_academic_papers`
  fetches arXiv Atom metadata + abstracts into `research_signals` — **captures `pdf_url`, never downloads
  it**, and `research_signals` is NOT a RAG source. No OpenAlex/Semantic Scholar/Crossref/DOI/BibTeX code.
- **Ingestion spine that IS reusable**: `tools/document_intelligence/ingest_orchestrator.py::ingest_file`
  (PDF → RAG chunks → KG bridge → DIC rows, grounded extraction) and
  `tools/rag/rag_to_kg_ingester.py` (anything in `rag_chunks` becomes graph). Both take LOCAL files only.
  KG regex path (`tools/knowledge_graph/ingester.py:137 _ENTITY_KEYWORDS`) is IT/compliance-hardwired;
  the LLM path (`llm_relationship_extractor.py::extract_graph_llm`) is domain-neutral. No finance ontology
  among the 15 TTLs in `args/ontology/`.
- **Sibling-system topology already in production code**: `tools/dashboard/app.py:8130 GENESIS_APPS`
  registers 7 sibling systems (incl. `trading-engine`, `trading-strategy`) each with own root + db + env
  flag; `tools/kanban/repo_registry.py` + `args/kanban_external_repos.yaml` dispatch tasks to external
  repos via `root_env`; `tools/genesis/reflex_registry.py` already tiers reflexes CORE(16) / STRATEGOS /
  DOMAIN(28) / SUPPORT(38).

### Kernel portability findings
- Package: `pyproject.toml` only, `icdev` v1.2.42, pip-installable, `icdev/tools/` is a GENERATED
  mirror of `tools/` (via `sync_package_tree.py`) and has drifted (66 stale files, 19 content diffs).
- Root resolution: `icdev/_paths.py` honours `ICDEV_PROJECT_ROOT` but only 14 call sites use it;
  **1,996 files self-root via `Path(__file__)`** and 1,173 define their own `BASE_DIR`. A copied sibling
  folder therefore works; a pip-install pointed at a different data root does not (yet).
- `ICDEV_` env coupling: 602 distinct names; `ICDEV_DB_PATH` in 227 places.
- CUI banners: 3,800/4,241 files carry `# CUI // SP-CTI` — cosmetic. Real CUI logic in ~4 places
  (`llm/router.py` egress gate, `llm/chain_prompts.py`, `llm/cli_bridge/`, `security/row_security.py`).
- RLS: `get_connection()` injects the classification/tenant predicate ONLY inside a Flask request
  context; `get_canvas_connection()` disables it. Finance tables without `classification` are safe.
- Cleanly liftable: `tools/agents` (0 domain refs), `tools/memory`, `tools/quality` (TRUST),
  `tools/config`, `tools/rag`, `tools/cortex`, `tools/knowledge_graph` (minus `compliance_graph.py`),
  `tools/llm` (retarget egress classification CUI/IL4 → MNPI/PII).
- Liftable with work: `tools/db/storage.py`, `tools/kanban` (89 py), `tools/genesis` (keep 16 CORE),
  `tools/mcp` (`base_server`/`core_server`), `tools/ci` gates.
- The three monoliths: `tools/db/init_icdev_db.py` (11,757 ln, 531 tables, no partitioning),
  `tools/workflow/coherence_checker.py` (10,565 ln, repo-specific invariants),
  `tools/dashboard/app.py` (10,730 ln, 29 inline `register_blueprint`).
- Child-app generator (`tools/builder/child_app_generator.py`) is copy-and-adapt (D21) and strips
  `GENERATION_TOOLS` (D28) — it structurally cannot produce a parent. `icdev init` is the closest
  parent-shaped bootstrap (copies `icdev/data/claude_bootstrap/` — CLAUDE.md 1,674 ln, .claude/, FORGE
  data) and relies on the installed wheel for code. `forge_validator.py --project-dir X --gate` runs
  standalone and is the ready-made acceptance test for a new parent tree.
- FORGE content reuse: ~28% of goals, ~40% of hardprompts, ~15% of context are domain-neutral.
- `ft_*` MCP tools / `tools/finetune/` are FINE-TUNING — naming collision to avoid (use `fin`/`ftr`?).

### Vibe-Trading (HKUDS, MIT, 31k stars) — knowledge source, not dependency
LangChain/LangGraph; 24 data loaders incl. arXiv/OpenAlex "source-anchored claims", SEC EDGAR
PIT-safe; 460 registered factors; 9 market backtest engines with look-ahead guards + OOS gates;
13 broker connectors; AST-sandboxed strategy codegen; hash-chained audit ledger; SDM lifecycle
(active → monitoring → disabled on IC/Sharpe decay). Worth mining: factor taxonomy, backtest
validation gates, SDM lifecycle states, quantlib function inventory (249 fns / 17 modules).

### AlphaDesk (`D:\ICDEV_BACKUP\alphadesk`) — superseded, nothing to lift
Strict EARLIER snapshot of FathomDesk (41 py / 13.4k lines; 39/41 modules are older copies of files now
in `tools/trading`, hardprompts byte-identical to `apps/fathomdesk/hardprompts/trading/`). Not a git
repo; both `.db` files empty; no secrets; no backtester, no RL, no strategy DSL; tests are 2 Selenium
scripts. Two salvage items only: (1) four retired expert personas at
`tools/trading/market_intel/expert_agents.py:36-130` (growth-momentum, monetary-contrarian,
geopolitical, dividend-income) — prompt content that exists nowhere else; (2) the `ad_performance_metrics`
schema sketch (Sharpe / max-DD / win-rate / cumulative PnL per portfolio per day) in `tools/trading/db_init.py`.
The reusable PATTERN is FathomDesk's `args/trading_daemon_config.yaml` (declarative reflexes + trust-kernel
circuit breaker + green/yellow/orange approval tiers).

## Recommended approach

### Part A — the shared core (`icdev-core`) and the `ICDEV[domain]` contract

**Package shape.** Keep `icdev.tools.*` as the core namespace (every `[project.scripts]` entry, the
wheel alias in `icdev/__init__.py:27-60`, and the bootstrap already assume it). Distribution name
`icdev-core`; new contract modules under `icdev/core/` (`paths.py`, `domain.py`, `context.py`,
`shim.py`, `sensitivity.py`, `schema.py`). Do NOT rename imports: 3,060 files and 1,303 tests import
`from tools.`; 311 tests patch `"tools.…"` strings.

**The one change that fixes the module-identity trap.** `tools/__init__.py:33-66` is a `_ToolsRedirect`
whose `__path__` still points at `tools/`, so an `import tools.x.y` STATEMENT loads a second module
object (`args/mirror_parity_gate.yaml`: "a is b -> False"). Replace the fallthrough with a meta-path
finder (`icdev/core/shim.py`) that, for core packages, imports `icdev.tools.<pkg>` and registers the
SAME object under both names. Domain code stays in each parent's own `tools/` (IT keeps
`tools/compliance`, FT gets `tools/trading`) and is served by the same finder — no `icdev_it` rename
needed. Guard: `tests/test_namespace_identity.py` (`import tools.X as a; import icdev.tools.X as b;
assert a is b`), gated from Phase 0.

**Domain declaration — `icdev_domain.yaml` at each parent root** (core refuses to start without one;
IT ships its own so nothing changes for IT). Mostly a pointer file to catalogues that already exist
(`args/core_profiles.yaml`, `args/classification_profiles.yaml`, `args/component_registry.yaml`,
`args/kanban_external_repos.yaml`):
```yaml
domain: { key: it, name: "ICDEV[IT]", env_prefix: ICDEV }          # ft -> env_prefix: FT
paths:  { data: data, forge: [goals, args, context, hardprompts] }
db:     { backend: postgresql, name_env: ICDEV_PG_DATABASE, dsn_env: ICDEV_DATABASE_URL,
          sqlite_path_env: ICDEV_DB_PATH, migrations: [tools/db/migrations], schema: tools/db/schema }
sensitivity: { column: classification, labels_file: args/classification_profiles.yaml,
               default: public, egress_restricted: [cui, cui_sp_cti, secret, itar] }
components: args/component_registry.yaml
reflexes:  { packs: [tools/genesis/reflex_packs/it.yaml] }
dashboard: { port: 5050, blueprints: registry }
kanban:    { board: it, external_repos: args/kanban_external_repos.yaml }
mcp:       { servers: [core, compliance, devsecops, builder, knowledge, maintenance] }
trust:     { citation_required: true, promotion_gates: [coherence, gated_tests] }
ci:        { gated_lists: args/ci_test_files, compat_suite: args/ci_test_files/core_compat.txt }
```
FT differs in `key/env_prefix`, `db.name_env` (own PG DB `icdev_ft`), `sensitivity` (labels
`public, internal, pii, mnpi, account_secret`; `egress_restricted: [pii, mnpi, account_secret]`; no IL
levels), `reflexes.packs`, `mcp.servers` (no compliance/devsecops), `dashboard.port`.

**Sensitivity model replaces CUI/IL hardcoding.** `icdev/core/sensitivity.py` (`label_column()`,
`labels()`, `is_egress_restricted()`, `default_label()`). Rewire, one PR each, IT behaviour identical:
`tools/llm/router.py` egress gate (~L961-1011, L1279-1336, L2410), `tools/security/row_security.py`
(`inject_row_predicate` takes column + labels), `tools/quality/citation_grounding.py`
(`classification="CUI"` default → `default_label()`), `tools/db/storage.py` RLS attach (~L1572).

**One path resolver.** `icdev/core/paths.py` merges `icdev/_paths.py`, `tools/llm/config_path.py`,
`tools/db/storage.py::_resolve_repo_base`, `tools/config/component_registry.py::_find_repo_root`,
`tools/config/core_profile.py::BASE_DIR` — existing names stay as delegates. Order: `ICDEV_PROJECT_ROOT`
→ nearest `icdev_domain.yaml` walking up from CWD → from the calling file (deprecated, logs once) →
packaged fallback. Domain key is derived from the FILE found, never an env var, so two parents on one
machine cannot cross-load.

**Self-rooting debt (2,054 files) migrates by census, never big-bang**: `tools/ci/self_root_census.py
--check` + `args/self_root_census.txt` + `args/self_root_gate.yaml` (ceiling only goes down) + a
`self_rooting` coherence check with `--fix` for the simple `BASE_DIR = Path(__file__)...parent` form.
The `sys.path` bootstrap idiom (`parents[2]; sys.path.insert`) is explicitly allowed. Core packages must
reach ZERO self-root sites BEFORE they physically move.

**Database split — an ownership manifest, not a DDL rewrite.** `icdev/core/schema/tables.yaml` (core
tables, `rls: true|false`) + per-domain `tools/db/schema/tables.yaml`, generated once from the 531
`CREATE TABLE`s by prefix + referencing package, then hand-reviewed. New `schema_ownership` coherence
check: a migration may only touch tables its repo owns. `SCHEMA_SQL` is cut mechanically into
`core_schema.sql` + `domain_schema.sql`; `init_db()` composes them (PG path already delegates to
migrations, `init_icdev_db.py:~11462`). `MigrationRunner(migrations_dirs=[core, *domain])` — union
discovery, existing ordering, one new `schema_migrations.source` column. The 430 IT migrations NEVER
move; the 6 that reference `tools.trading` move to FT keeping their version strings (FT's DB is fresh).
Core ships `00000000000001_core_baseline` (`CREATE TABLE IF NOT EXISTS` for every core table): a no-op
on IT's live DB, builds the kernel on FT's. RLS becomes `core.schema.rls_tables()`-driven — a table not
flagged gets no predicate (generalises `get_canvas_connection`, removes the `UndefinedColumn` class).

**Extraction phases (each leaves ICDEV[IT] green):**

| Phase | What | Proof | Rollback |
|---|---|---|---|
| 0 Contract, no moves | `icdev/core/*`, IT `icdev_domain.yaml`, meta-path finder, `self_root_census`, `schema_ownership` manifest, `test_namespace_identity.py`, `core_compat.txt` gated list | coherence `--all --gate`, gated shards, mirror_parity unchanged | revert one PR |
| 1 End the code mirror in-repo | per package `git mv tools/<pkg> icdev/tools/<pkg>` (overwrites stale mirror); drop from `sync_package_tree.py`. Order: agents, memory, quality, config, rag, cortex, knowledge_graph (−compliance_graph), llm, db, awareness, kanban, genesis CORE tier, mcp base/core, ci, builder, cli | identity test; mirror_parity budget 14→0 per root | `git mv` back |
| 2 Domain fence | sensitivity rewiring; multi-dir MigrationRunner; init composition; reflex packs (`reflex_registry.py` DOMAIN tier → `reflex_packs/<key>.yaml`); dashboard registry-only (retire 29 inline `register_blueprint`; `GENESIS_APPS` → `args/genesis_apps.yaml` with `root_env`); `unified_server.py` composes from `mcp.servers` | `security_context`, `reflex_registry`, PG tier allowlist | per-PR |
| 3 Carve `C:\ai\icdev-core` | `git subtree split` core + `tests/core/`; IT depends on `icdev-core`; `pip install -e ../icdev-core`; bootstrap CLAUDE.md generated per domain by `prebuild_bootstrap.py` | `core-compat` CI matrix `{it, ft}` | tag `pre-core-split`; `ICDEV_CORE_SRC` |
| 4 FT bootstrap | `icdev init --domain ft` from `icdev/data/templates/domain_parent/`; move trading/market_intel/6 migrations/FT reflexes | FT gated list + compat leg | greenfield |
| 5 Optional tidy | `git mv tools/<domain_pkg>` → `icdev_it/` (zero import edits thanks to the finder); retire code sync (keep FORGE-data staging at build only) | identity test | reversible |

**Consumption.** Dev: each parent venv `pip install -e C:\ai\icdev-core`; `tools/dev/core_compat_local.py`
runs every parent's compat suite before push. Release: semver tag, parents pin `icdev-core==X.Y.Z`,
wheel as GitHub release asset mirrored to `C:\ai\wheels\` for `pip install --no-index --find-links`
(pure-Python, offline). Core CI `core-compat` matrix checks out each parent (FT via deploy key), installs
core from the PR SHA, runs `ci.compat_suite` (~50 modules touching storage/kanban/llm/genesis) +
coherence core tier; branch protection requires both legs. PyPI name `icdev` stays for the IT wheel.

**Where the core lives.** Phases 0–2 inside this repo (FT can start immediately against
`pip install -e C:\ai\icdev`); a third repo `icdev-core` is carved at Phase 3 (lowest blast radius and CI
cost; the kanban runner + 166 cards stay in IT; ICDEV[Bio] is then just another `icdev_domain.yaml`).

**`coherence_checker.py`** is split into `--tier core` (reads only the declaration + core manifest) and
domain-registered checks — never moved wholesale.

### Part B — ICDEV[FT]: repo, finance domain pack, pipeline (summary)

ICDEV[FT] lives in a separate PRIVATE repository; its domain-pack design (knowledge pipeline,
finance ontology, strategy spec + generator, backtest engine, promotion ladder, RL, multi-asset data
model, FathomDesk migration map) is maintained in that repo's own plan. What ICDEV[IT] needs to know:
package `icdev_fin`, env prefix `FIN_`, own PostgreSQL database `icdev_ft`, API on port 5200, its own
kanban board with prefix `fin-`; the IT board carries only the PROGRAMME cards (`xit-`, `xcore-`,
`xft-`), and `xft-` tasks are external-repo tasks parked until `ICDEV_KANBAN_REPO_FT` is set.

### Part C — professional UI layer (summary)

A design-first React + TypeScript SPA served by a FastAPI backend in the FT repo; Node is a BUILD-time
dependency only and the built assets ship inside the FT wheel, so air-gap installs need no Node. The
IT dashboard is unaffected. Detail lives in the FT repo.

### Part D — risks, sequencing, project cards, tasks

**Measured before planning (live board/DB, 2026-08-21).** Board 3,331 tasks, 56 `*-gate-00`.
Prefixes `xit-`, `xcore-`, `xft-`, `fin-`, `ft-` are ABSENT from the board and `projects.yaml`
(`fd-` is a live parent prefix — nothing starting `fd`). The `fdt` card owns 0 rows. Live IT PG DB has
**137** `ad_*` tables (DDL scattered over **156** `CREATE TABLE IF NOT EXISTS ad_*` sites, only 11 of
them migrations — export must enumerate by DDL grep, not migration list). `tools/genesis/reflexes/
kanban.py:6388-6403` parks an external-unconfigured task in `validating` (never builds it in IT).
`args/pr_watcher_config.yaml:11-12` = `auto_merge_enabled: true`, `auto_merge_require_approval: false`
— **contradicts the owner's "overnight = push+PR only"**. `args/trading_micro_live.yaml`'s
`ICDEV_TRADING_MODE` and `require_human_approval_for_sell` are consumed by **zero** code sites today
(the declared-but-unconsumed bug, live, in the safety path); `kill_switch.is_killed()` IS consumed (7
modules) but `_FLAG_FILE` is `parents[3]/data/.kill_trading` and breaks on a depth change. No
`tests/trading/*` file is gated, but 4 gated tests reference `tools.trading` by string
(`tests/ci/test_perfect_score_census.py`, `tests/test_documented_clis_have_a_bootstrap.py`,
`tests/test_no_direct_provider_calls.py`, `tests/test_script_bootstrap_import_order.py`); 46 test
files import `tools.trading`/`tools.fathomdesk`. `tools/genesis/launcher.py:46,295-391` starts the
trading dashboard on 5100; `GENESIS_APPS._genesis_run` uses the sibling root as `cwd` with no
existence check. `tools/security/secret_detector.py::BUILTIN_PATTERNS` has AWS patterns only.

**Risk register (top).**

| # | Risk | Mitigation | Early signal |
|---|---|---|---|
| R1 | Two parents on one machine collide on `ICDEV_PG_*`, ports, inherited `.env` | Phase-0 `icdev_domain.yaml` carries `pg_database` + `ports`; `icdev/core/context.py::assert_identity()` at dashboard/daemon/migrate/kanban-CLI startup refuses a mismatched `*_PG_DATABASE`; each parent loads `.env` from its own root never cwd; separate PG ROLES (`icdev` vs `icdev_ft`) so a wrong DB is a permission error, not a silent write | `assert_identity` refusal; `icdev status` prints domain key + root |
| R2 | Kanban runner dispatches FT/core work into the IT checkout | CONCORD precedent: register `icdev_core`/`icdev_ft` in `args/kanban_external_repos.yaml` BEFORE they exist with `root_env` unset → parked; `xit-/xcore-/xft-gate-00` held `in_progress`; every task a session builds is seeded `claim=True`; `tests/kanban/test_repo_registry.py` asserts every `xft-`/`xcore-` row resolves external | task → `validating` by `repo-aware-guard`; a `kanban/xft-*` branch in IT |
| R3 | Overnight runner MERGES what it should only PR | `auto_merge_require_approval: true` for the programme window; merge only via `cli.py --set-status <id> done --merge`; never `--force-done` | `pr_watcher` `merge_requested` with no approving review |
| R4 | FathomDesk data migration (137 live tables, 156 DDL sites, no rollback) | freeze via kill-switch; `pg_dump` of the DDL-enumerated list + per-table row-count manifest; FT migrations fresh (`migrate.py --create`); IT migrations UNTOUCHED; IT tables read-only 30 days then dropped by a NEW migration; rollback = untrip IT | manifest diff ≠ 0; `fathomdesk_smoke.py --json` non-zero |
| R5 | `GENESIS_APPS` sibling-path assumptions | `args/genesis_apps.yaml` with `root_env` (Part A); `_genesis_run` returns `root_missing` instead of raising; Playwright `/genesis` | `/genesis` 500 |
| R6 | Mirror drift (`icdev/tools/trading/` left behind; bootstrap CLAUDE.md stale) | removal PR deletes mirror dirs in the same PR; `mirror_parity --gate`, `bootstrap_parity`; Part A ends the code mirror | `bootstrap_parity` fail |
| R7 | Tests patching `tools.x` strings hit another module object | meta-path finder + `test_namespace_identity.py` (Part A); FT tests patch via `importlib.import_module` + `setattr`; red-first + `isolation_run.py` | `red_first_gate` exit 1 on a moved test |
| R8 | Core version skew | semver + `core_api_manifest.json`; parents pin `icdev-core==X.Y`; `coherence_checker --check vendor_parity`; core-compat CI matrix | `vendor_parity` fail |
| R9 | Public-repo leakage (strategy code, broker creds, `ad_*` dumps into IT) | NEW `tools/ci/domain_leak_gate.py` + `args/domain_leak_gate.yaml`: path denylist (`tools/trading/**`, `tools/market_intel/**`, `args/trading*.yaml`, SQL containing `COPY ad_`/`INSERT INTO ad_`) + broker-key regexes (Alpaca `PK[A-Z0-9]{16,}`/`APCA-API-SECRET-KEY`, Tradier/Tastytrade bearer, Kraken base64, IBKR/Schwab tokens); wired into `.githooks/pre-commit` via `tools/testing/pre_commit_check.py` and the CI `security` job; red-first with a planted fake key as negative control | gate names the path/pattern |
| R10 | HITL live-gate bypass; live mode gate unconsumed TODAY | single `icdev_fin/governance/live_authorization.py::assert_may_trade(order)` consumed by every order path (auto_trader, exit_executor, auto_trade_options, brokers/router); red-first tests prove a SELL/any live order without an authorization row is refused; `check_capability_liveness` class `live_gate` allowance 0 | consumers of `assert_may_trade` == order submitters |
| R11 | Kill-switch liveness after the move | AST consumption test: every module calling a broker `submit_*` imports + calls `is_killed`; flag path from `core.paths.repo_root()` not `parents[n]` | consumption test fail |
| R12 | RL compute blocks the runner | RL training is `task_type: run` behind `xft-gate-00`; CI runs env API-conformance on a stub env only | `max_runtime_seconds` breach |
| R13 | LLM/OS-agnostic violated | `tests/test_no_hardcoded_model_ids.py` + `check_direct_anthropic_import` + `check_architecture_agnosticism` copied into FT CI day 1; `llm_config_trading.yaml` becomes `llm_function` chains only (roles, no model ids, in YAML too) | `model_id_gate` count up |
| R14 | Broker secrets in YAML | `args/fin_brokers.yaml` uses `auth_secret_ref` with the `args/databridge_connections.yaml` grammar (`env:/vault:/aws:/file:`); seeder refuses literals | literal fails the seeder; R9 |
| R15 | `merge=union` not honoured by GitHub → `projects.yaml`/census PRs DIRTY | expected; rebase; never touch `.gitattributes`; `core.d/<task-id>.txt` fragments | PR CONFLICTING while local merge clean |
| R16 | A census max raised "to get through" | every removal PR LOWERS `undeclared_max`/`raw_insert_max`/`backlog_max` by exactly the lines removed; `census_growth.py --check` | any max increasing in a diff |

**Programme phases across three codebases.**

| Phase | Repo(s) | Exit criteria | IT-stays-green proof |
|---|---|---|---|
| P0 Declare | IT | `icdev/core/*` + IT `icdev_domain.yaml` + finder + identity test + `self_root_census` + `domain_leak_gate` + registry entries + cards + gate-00s seeded | additive only; required jobs + `coherence --all --gate` |
| P1 Core extract | IT (Part A phases 1–2), then core repo (phase 3) | per-package moves; IT pins `icdev-core`; `vendor_parity` green | identity test + mirror_parity ratchet per PR |
| P2 FT scaffold + vertical slice | FT | `icdev init --domain ft`; `icdev_ft` DB; arXiv→ingest; `finance.ttl`; spec schema; momentum → backtest → paper; UI S1 | zero IT files touched |
| P3 Cutover | IT DB, FT | FathomDesk data in `icdev_ft`; IT trading frozen; `GENESIS_APPS`/reflexes flipped (reflex entries removed WITH their modules in one PR) | config-only IT PRs; `reflex_registry` check |
| P4 Remove from IT | IT | one PR per gate family, in order: (a) dashboard decoupling `app.py:2892`, `:10262-10321`, `launcher.py`, `/api/ta/patterns` + Playwright; (b) `tools/trading`, `tools/market_intel`, `icdev/tools/trading`, `args/trading*.yaml`, `llm_config_trading.yaml`, `seed_fdt_*.py`, manifest shards (`tools/manifest/fathomdesk-trading-engine.md` 149 rows + stragglers), `doc_command_gate`/`insert_schema_gate`/`package_exclusions` entries, `PARENT_ONLY_DIRS`, `raw_insert_max −2`; (c) tests: delete 46 importing files, `gated_test_list.py --prune-backlog`, `backlog_max 1701→1697`, re-run the 4 string-referencing gated tests; (d) `undeclared_import_census.py --prune`, `undeclared_max 210→187`; (e) retire `fdt` card; (f) trim `conftest.py` `ad_` schema | every gate passing after each family; verify union did not resurrect manifest rows |
| P5 Unhold runner | IT config | set `ICDEV_KANBAN_REPO_FT`/`_CORE` where the scheduler runs; release gate-00 only for runner-safe epics; dispatch-proof task lands one trivial FT PR | `pr_watcher` merges nothing without approval |

**Cutover choreography (P3):** (1) freeze — `kill_switch.py --trip --reason cutover`, stop
`launcher.py`'s trading subprocess; (2) export — DDL-enumerated table list → `pg_dump` schema+data +
row-count manifest; (3) stand up — `FIN_PG_DATABASE=icdev_ft` bootstrap + FT migrations; (4) import,
manifest diff == 0; (5) smoke — `tools/testing/fathomdesk_smoke.py --url http://localhost:5200 --json`
(moved to FT, IT copy kept until P4); (6) flip — `genesis_apps.yaml`, reflex pack; (7) dual-run ≥ 7
days: IT trading stays killed, FT paper-only; rollback = untrip IT.

**Cards in `args/projects.yaml` (the IT board runs the PROGRAMME; FT's own board uses `fin-`).**
- `xit` "Parent Split — ICDEV[IT] side", `task_prefix: xit-`, epics `gate`, `decl` (core contract +
  identity, critical), `leak` (public-repo leakage gate, critical), `reg` (registry + cards), `core`
  (Part A phases 1–2 moves), `cut` (cutover), `rm` (removal per gate family), `gen` (GENESIS_APPS +
  launcher decoupling).
- `xcore` "ICDEV Core — domain-neutral kernel", `task_prefix: xcore-`, epics `gate`, `boot`, `api`
  (core API manifest + vendor parity), `compat` (core-compat CI), `pin`.
- `xft` "ICDEV[FT] — financial trading parent", `task_prefix: xft-`, epics `gate`, `boot`, `ing`,
  `onto`, `spec`, `tmpl`, `bt`, `safe`, `rl`, `data` (migration import), `ui`, `ux` (design
  deliverables).
  No epic key is a prefix of another; `gate` reserved.
- `args/kanban_external_repos.yaml`: `icdev_core: {root_env: ICDEV_KANBAN_REPO_CORE}`,
  `icdev_ft: {root_env: ICDEV_KANBAN_REPO_FT}`; prefixes `xcore`, `xft`. Both env vars UNSET until P5.
- Seeder: new `tools/kanban/seed_parent_split.py` on `create_tasks` (never raw INSERT), `--dry-run`;
  seed `claim=True` in the session that builds P0; `cli.py --release <id>` when landed.

**First ~16 task specs (vertical slice).**

| id | repo | type | pri | essentials |
|---|---|---|---|---|
| `xit-gate-00` / `xcore-gate-00` / `xft-gate-00` | IT/core/FT | chore | high | MANUAL-MODE GATE, held `in_progress`, `RISK:` line (cutover ordering the runner cannot see; private repo unreachable; live-trading code never built unattended) |
| `xit-decl-01` | IT | build | critical | `icdev/core/{paths,domain,context}.py`, IT `icdev_domain.yaml`, `assert_identity()` at dashboard/migrate/kanban-CLI startup; red-first tests; `core.d/xit-decl-01.txt` |
| `xit-decl-02` | IT | build | critical | meta-path finder in `tools/__init__.py` (`icdev/core/shim.py`) + `tests/test_namespace_identity.py` |
| `xit-decl-03` | IT | build | high | `tools/ci/self_root_census.py` + `args/self_root_census.txt` + `self_root_gate.yaml` + `check_self_rooting` (with `--fix`) |
| `xit-decl-04` | IT | build | high | `icdev/core/schema/tables.yaml` ownership manifest (generated, reviewed) + `check_schema_ownership`; `icdev/core/sensitivity.py` |
| `xit-leak-01` | IT | build | critical | `tools/ci/domain_leak_gate.py` + yaml; pre-commit + CI security job; negative-control red-first |
| `xit-reg-01` | IT | chore | high | external-repo entries + prefixes; cards; `test_repo_registry.py` asserts parking; expect DIRTY, rebase |
| `xit-gen-01` | IT | build | medium | `GENESIS_APPS` → `args/genesis_apps.yaml` with `root_env`; `root_missing` instead of raise; Playwright `/genesis` |
| `xit-cut-01` | IT | run | high | `export_ft_data.py` enumerating tables from DDL sites; `pg_dump` + manifest + `--verify` |
| `xcore-boot-01` | core | build | critical | carve core repo (Part A phase 3), CI from `icdev-ci.yml` required jobs, `core_api_manifest.json`, semver, `core-compat` matrix |
| `xft-boot-01` | FT | build | critical | `icdev init --domain ft` bootstrap: `icdev_domain.yaml` (`FIN_`, `icdev_ft`, port 5200), `.env.template` with `env:` refs only, CI with the full gate set (red-first, skip census, model-id, leak gate), PG role + bootstrap; no FathomDesk code yet |
| `xft-ux-01` | FT | build | high | `docs/ux/ia.md` (sitemap + 5 flows), `ui/tokens.json`, `docs/ux/components.md`, hi-fi mockups of the 4 key screens via the `design` skill — reviewed with the owner BEFORE `ui/` is scaffolded |
| `xft-ing-01` | FT | build | high | `knowledge/fetchers/arxiv.py` + `downloader.py` → `ingest_file` with persisted provenance row; provenance gate refuses ingest without it; fixture PDF, no network in tests |
| `xft-onto-01` | FT | build | high | `args/ontology/finance.ttl` + loader into `kg_ontology`; PROBE FIRST (`--probe-substrate kg_ontology`, empty in IT); test asserts rows > 0 after load |
| `xft-spec-01` | FT | build | high | `strategy_spec.schema.json` ↔ pydantic model; mandatory `risk.kill_switch` + `safety.live_gate`; schema tests; `/api/v1/schemas/strategy-spec.json` |
| `xft-tmpl-01` | FT | build | high | `_base` + `momentum` templates → `generator.py` → standalone repo + `provenance.json`; `fin_system_validator --gate` |
| `xft-bt-01` | FT | build | high | `backtest/engine.py` from `fathomdesk/backtester.py` + guards/walk-forward/costs/calendars; look-ahead test (shifted signal must not improve), NYSE/CME/24×7 calendar tests, deterministic seed; momentum spec end to end |
| `xft-safe-01` | FT | build | critical | `assert_may_trade` on every order path; kill-switch AST consumption test; red-first: live order without authorization row refused |

## Verification

**Every ICDEV[IT] PR, all phases:** `pytest $(python tools/ci/gated_test_list.py --print --list core)`;
`python tools/ci/gated_test_list.py --check-coverage`; `python tools/ci/red_first_gate.py --gate`;
`python tools/ci/isolation_run.py --run --root <worktree>`; `python tools/ci/skip_census.py --check`;
`python tools/ci/undeclared_import_census.py --check`; `python tools/ci/perfect_score_census.py --check`;
`python tools/ci/census_growth.py --check`; `python tools/workflow/coherence_checker.py --all --gate`;
`python tools/dx/companion.py --sync --write --json` then `python tools/dx/mirror_parity.py --all --gate`;
`pytest tests/test_migration_version_uniqueness.py tests/test_no_hardcoded_model_ids.py`; sync
`icdev/data/claude_bootstrap/CLAUDE.md` whenever CLAUDE.md changes; `ruff check` on the CHANGED SET.

| Phase | Additional checks |
|---|---|
| Before approval | `python tools/awareness/capability_consumption.py --probe-plan <this plan> --substrate-gate` — RUN 2026-08-21 on PG with operating history: ONE finding, `kg_ontology` = 0 rows (`empty`, writer never ran). This plan only WRITES it (`xft-onto-01` is the writer and asserts rows > 0 after load); nothing here reads it as an input. `rag_chunks` 4,111 · `entity_currency` 230 · `kg_nodes` 8,966 · `audit_trail` 116,539 are populated. |
| P0 | `python -m icdev.core.context --check --json` in both repos' start; `domain_leak_gate.py --check` with a planted fake Alpaca key as negative control; `pytest tests/kanban/test_repo_registry.py` (xft-/xcore- park); `coherence_checker --check project_card_coverage` counts every row of all three cards; `tests/test_namespace_identity.py` |
| P1 | core CI green; IT `coherence_checker --check vendor_parity`; `self_root_census --check`; `schema_ownership`; mirror_parity budget 14 → 0 per moved root; `core-compat` matrix `{it, ft}` |
| P2 | FT: `forge_validator.py --project-dir <generated system> --gate`; provenance gate test; `--probe-substrate kg_ontology` > 0; look-ahead-bias + calendar tests; RL env API-conformance (stub env: `reset/step/observation_space`); `size-limit`, OpenAPI/zod drift, Lighthouse (perf ≥ 90, a11y ≥ 95), Playwright screenshots per screen × theme × density, offline-proof job (wheel install, no network, no Node, Home E2E) |
| P3 | export manifest diff == 0; `kill_switch.py --status --json` shows `killed: true` in IT for the whole window; `assert_may_trade` red-first; kill-switch consumption test; Playwright `/genesis`; `coherence_checker --check reflex_registry`; `fathomdesk_smoke.py --url http://localhost:5200 --json` |
| P4 | per-family PR: standard set + `gated_test_list.py --prune-backlog` + the 4 string-referencing gated tests alone; `git diff tools/manifest/*.md` confirms row deletion stuck post-rebase; `sync_package_tree.py` dry run shows no `trading`; `domain_leak_gate` flips to path-denylist enforcement |
| P5 | dispatch-proof task lands one trivial FT PR with `ICDEV_KANBAN_REPO_FT` set; `pr_watcher` shows no merge without approval |

**FT-only gates built from the first commit** (each gated via `core.d/<task-id>.txt`): provenance gate
test; HITL live-gate red-first; kill-switch consumption AST test; look-ahead-bias test; multi-asset
calendar tests; RL env conformance; `auth_secret_ref` grammar test for `args/fin_brokers.yaml`;
domain-identity startup test; `ConfirmFinancial` E2E asserting the audit row exists after the click.
