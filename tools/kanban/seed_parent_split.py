# CUI // SP-CTI
"""Seed the ICDEV[domain] split programme onto the kanban board.

Three streams, three repos, and the prefixes are what keep them apart:

  xit-*    builds in ICDEV (the ICDEV[IT] side: domain contract, leak gate,
           in-repo extraction, FathomDesk cutover and removal). Default dispatch,
           through every ICDev gate.
  xcore-*  builds in the icdev-core repo. Registered in
           args/kanban_external_repos.yaml so it can never fall through to the
           ICDev default; parked until ICDEV_KANBAN_REPO_CORE is set.
  xft-*    builds in the private ICDEV[FT] repo. Same registration; parked until
           ICDEV_KANBAN_REPO_FT is set. Live-trading code is never built
           unattended, which is what the gate-00 sentinel's RISK line says.

Every stream is held behind its own ``<prefix>gate-00`` (parked ``in_progress``)
and every work task declares ``depends_on_task_id`` on that gate, so releasing a
stream is one deliberate act and not an accident of a census edit.

Descriptions are written for a session arriving cold: token exhaustion
mid-build must not lose the plan. The programme document is
docs/programmes/icdev-domain-split.md; ICDEV[FT] internals live in the FT repo.

NOTE ON CONTENT. ICDEV is a PUBLIC repository. Nothing here names a strategy,
an account, a broker credential or a data vendor key.

    python -m tools.kanban.seed_parent_split --dry-run   # print, change nothing
    python -m tools.kanban.seed_parent_split             # seed
    python -m tools.kanban.seed_parent_split --claim xit-reg-01 --claim xit-decl-01
"""
from __future__ import annotations

import argparse
import json

from tools.kanban.task_factory import create_tasks

PROGRAMME_DOC = "docs/programmes/icdev-domain-split.md"

_GATE_TAIL = (
    "\n\nPipeline-exempt holding gate. While this task is in_progress, "
    "promote_backlog_to_scheduled will not dispatch any {prefix}* task, and every "
    "{prefix}* work task also depends_on this id.\n\n"
    "Do NOT open a kanban/<id> PR for this task."
)


def _gate(prefix: str, risk: str, release: str) -> dict:
    return {
        "id": f"{prefix}gate-00",
        "title": f"MANUAL GATE - hold the {prefix}* stream (do not close)",
        "description": f"RISK: {risk}\n\nRelease only once {release}"
        + _GATE_TAIL.format(prefix=prefix),
        "status": "in_progress",
        "priority": "high",
        "task_type": "chore",
    }


# -- ICDEV[IT] side ------------------------------------------------------------
XIT: list[dict] = [
    _gate(
        "xit-",
        "the cutover and removal PRs must follow the P3 -> P4 ordering in "
        f"{PROGRAMME_DOC} (freeze, export, import, smoke, flip, THEN remove one "
        "gate family per PR). The runner cannot see that ordering; an unattended "
        "removal turns main red for every open PR and strands FathomDesk's data.",
        "the programme doc's P0 tasks (xit-decl-*, xit-leak-01, xit-reg-01) have "
        "merged. Release xit-cut-* and xit-rm-* only by hand, in order.",
    ),
    {
        "id": "xit-reg-01",
        "title": "Register the split: three cards, two external repos, one seeder",
        "description": (
            "args/projects.yaml gains the xit / xcore / xft cards (task ids are "
            "<prefix><epic>-<N>; `gate` is the reserved sentinel epic). "
            "args/kanban_external_repos.yaml gains icdev_core + icdev_ft with "
            "root_env ICDEV_KANBAN_REPO_CORE / _FT (UNSET on purpose - the CONCORD "
            "precedent: an unregistered xft- task would resolve to the ICDev "
            "default and the dispatcher would build a trading-system task inside "
            "this checkout) and the prefixes xcore / xft. xit- is deliberately "
            "absent from the registry because it genuinely builds here.\n\n"
            "tools/kanban/seed_parent_split.py seeds every task through "
            "create_tasks (never a raw INSERT), holds each stream behind its "
            "gate-00 and declares depends_on_task_id on it. "
            "tests/kanban/test_repo_registry.py asserts xft-/xcore- rows resolve "
            "external and park; tests/kanban/test_seed_parent_split.py asserts the "
            "seed's shape (every work task depends on its gate, every id is "
            "claimed by an epic of its card, no gate-shaped work id).\n\n"
            f"Programme document: {PROGRAMME_DOC}."
        ),
        "status": "in_progress",
        "priority": "high",
    },
    {
        "id": "xit-decl-01",
        "title": "icdev/core paths+domain+context: ICDEV[IT] loads through icdev_domain.yaml",
        "description": (
            "Introduce icdev/core/{paths,domain,context}.py and an icdev_domain.yaml "
            "at THIS repo's root declaring ICDEV[IT] (key it, env_prefix ICDEV, db "
            "name_env ICDEV_PG_DATABASE, dashboard.port 5050, components, reflex "
            "packs, mcp servers, trust, ci). ICDEV[IT] becomes the first domain of "
            "the core BEFORE any file moves, so every later move is proved against "
            "a running consumer.\n\n"
            "core.paths merges the three resolvers that exist today - "
            "icdev/_paths.py (get_project_root/get_data_path), "
            "tools/llm/config_path.py and tools/db/storage.py::_resolve_repo_base "
            "(plus component_registry._find_repo_root and core_profile.BASE_DIR) - "
            "and leaves each public name in place as a thin delegate, so the "
            "existing call sites need no edit. Resolution order, documented once: "
            "ICDEV_PROJECT_ROOT -> nearest icdev_domain.yaml walking up from CWD -> "
            "nearest walking up from the calling file (deprecated, logs once) -> "
            "packaged fallback. The domain KEY is derived from the file found, "
            "never from an env var, so two parents on one machine cannot cross-load."
            "\n\ncore.context.assert_identity() is called at dashboard, genesis "
            "daemon, tools/db/migrate.py and tools/kanban/cli.py startup and REFUSES "
            "when the process's <env_prefix>_PG_DATABASE disagrees with the "
            "declaration. Each parent loads .env from its own root, never cwd. "
            "`icdev status` prints the resolved domain key and root.\n\n"
            "Tests are red-first (the declaration file does not exist at the merge "
            "base). Gate the test file via args/ci_test_files/core.d/xit-decl-01.txt. "
            f"Design: {PROGRAMME_DOC} Part A."
        ),
        "priority": "critical",
    },
    {
        "id": "xit-decl-02",
        "title": "Meta-path finder: tools.X and icdev.tools.X are ONE module object",
        "description": (
            "tools/__init__.py is a _ToolsRedirect whose __path__ still points at "
            "tools/, so an `import tools.x.y` STATEMENT loads a second module "
            "object (args/mirror_parity_gate.yaml: 'a is b -> False'). That is the "
            "whole cause of the test-patch trap (311 test files patch 'tools.' "
            "strings, 24 patch 'icdev.tools.') and the reason the generated "
            "icdev/tools mirror has to exist.\n\n"
            "Add icdev/core/shim.py: a meta-path finder that, for any package "
            "listed in the core manifest, imports icdev.tools.<pkg> and registers "
            "the SAME object under both names in sys.modules; non-core names fall "
            "through to the normal finder over the parent's tools/ directory. "
            "Install it from tools/__init__.py in place of the __path__ "
            "fallthrough. Keep the wheel alias in icdev/__init__.py (L27-60) "
            "untouched.\n\n"
            "Guard: tests/test_namespace_identity.py - for every core package, "
            "`import tools.X as a; import icdev.tools.X as b; assert a is b`. This "
            "is the test that would have caught PR #1542. Do NOT rename any "
            "import: 3,060 files import `from tools.`."
        ),
        "priority": "critical",
    },
    {
        "id": "xit-decl-03",
        "title": "Self-root census: ratchet the 2,054 Path(__file__) root sites, never rewrite",
        "description": (
            "2,054 files under tools/ compute a data/args/db path from "
            "Path(__file__).resolve().parent and 1,173 define their own BASE_DIR; "
            "only ~14 honour ICDEV_PROJECT_ROOT. A package that physically moves "
            "to another repo with a parents[n] root lands in the wrong checkout.\n\n"
            "Build tools/ci/self_root_census.py --check (+ --json, --changed, "
            "--staged, --prune) and args/self_root_census.txt enumerating every "
            "site BY NAME (<file>:<line>), with args/self_root_gate.yaml whose "
            "self_root_max may only go DOWN. The sys.path bootstrap idiom "
            "(`_REPO_ROOT = parents[2]; sys.path.insert`) is explicitly ALLOWED - it "
            "resolves the import root and stays correct after a move. Wire "
            "coherence_checker.check_self_rooting (changed-file scope) with --fix "
            "for the simple `BASE_DIR = Path(__file__)...parent` form -> "
            "`BASE_DIR = repo_root()` from xit-decl-01. Core packages must reach "
            "ZERO sites before they move (xit-core-*). Follow the exact shape of "
            "tools/ci/undeclared_import_census.py and "
            "tools/ci/perfect_score_census.py."
        ),
        "priority": "high",
    },
    {
        "id": "xit-decl-04",
        "title": "Schema ownership manifest + sensitivity model (no DDL rewrite)",
        "description": (
            "tools/db/init_icdev_db.py is 11,757 lines and 531 CREATE TABLEs with "
            "no domain partitioning; 253 tables carry `classification`, 119 "
            "`tenant_id`. Generate icdev/core/schema/tables.yaml (core-owned "
            "tables, rls: true|false) and tools/db/schema/tables.yaml (IT-owned) "
            "ONCE by classifying every CREATE TABLE by prefix + referencing "
            "package, then hand-review. Add coherence check schema_ownership: a "
            "migration may only touch tables its repo owns; a table owned by "
            "nobody fails.\n\n"
            "Add icdev/core/sensitivity.py (label_column(), labels(), "
            "is_egress_restricted(), default_label()) reading the declaration's "
            "`sensitivity:` block - for IT that is args/classification_profiles.yaml "
            "(CUI / IL levels), for FT it will be PUBLIC/LICENSED/MNPI/PII/"
            "ACCOUNT_SECRET/TRADE_RECORD. Do NOT rewire consumers in this task; "
            "xit-core-* rewires tools/llm/router.py's egress gate (~L961-1011, "
            "L1279-1336, L2410), tools/security/row_security.py, "
            "tools/quality/citation_grounding.py's classification='CUI' default and "
            "tools/db/storage.py's RLS attach (~L1572) one PR each, IT behaviour "
            "identical. A table not flagged rls:true gets no predicate - "
            "get_canvas_connection generalised."
        ),
        "priority": "high",
    },
    {
        "id": "xit-leak-01",
        "title": "Public-repo leakage gate: no FathomDesk resurrection, no broker keys",
        "description": (
            "This repository is PUBLIC and FathomDesk is leaving it. Build "
            "tools/ci/domain_leak_gate.py --check (+ --staged, --json) with "
            "args/domain_leak_gate.yaml: (a) a PATH denylist - tools/trading/**, "
            "tools/market_intel/**, icdev/tools/trading/**, args/trading*.yaml, "
            "args/llm_config_trading.yaml, and any *.sql containing `COPY ad_` or "
            "`INSERT INTO ad_` - armed via `enforced_after_commit` so it only fires "
            "once the removal (xit-rm-*) has landed; (b) broker-credential "
            "PATTERNS active immediately - Alpaca key id `PK[A-Z0-9]{16,}` and the "
            "`APCA-API-SECRET-KEY` header, Tradier/Tastytrade bearer tokens, Kraken "
            "base64 private keys, IBKR/Schwab tokens - added beside the AWS-only "
            "BUILTIN_PATTERNS in tools/security/secret_detector.py rather than a "
            "second detector. Wire into .githooks/pre-commit through "
            "tools/testing/pre_commit_check.py and into the CI security job.\n\n"
            "Red-first tests with a PLANTED fake key as the negative control (the "
            "test must go red at the merge base). Never a `|| true`; stand it down "
            "only with a named env switch, like CHECK_KILL_SWITCHES."
        ),
        "priority": "critical",
    },
    {
        "id": "xit-gen-01",
        "title": "GENESIS_APPS -> args/genesis_apps.yaml with root_env; root_missing, never raise",
        "description": (
            "tools/dashboard/app.py:8132 GENESIS_APPS hardcodes seven sibling "
            "systems at Path(BASE_DIR).parent / <name> (two of them trading), and "
            "_genesis_run uses that root as cwd with no existence check. Move the "
            "table to args/genesis_apps.yaml in the SAME shape as "
            "args/kanban_external_repos.yaml (name, daemon, promoter, env_var, "
            "root_env with a sibling-path fallback, db). Add an `icdev-ft` entry "
            "(root_env ICDEV_FT_ROOT). _genesis_run returns "
            '{"error": "root_missing"} when Path(root).is_dir() is False. Keep '
            "the GENESIS_APPS name as a loaded dict so nothing else changes. "
            "tools/genesis/launcher.py:46,295-391 starts the trading dashboard on "
            "5100 - make that start conditional on the entry being configured.\n\n"
            "Playwright E2E on /genesis before reporting done (dashboard change)."
        ),
        "priority": "medium",
    },
    {
        "id": "xit-cut-01",
        "title": "FathomDesk export tooling: DDL-enumerated tables, pg_dump, row-count manifest",
        "description": (
            "The live IT PostgreSQL database holds 137 ad_* tables, but only 11 "
            "came from migrations: there are 156 `CREATE TABLE IF NOT EXISTS ad_*` "
            "sites in code (tools/trading/db.py alone has 51). An export keyed on "
            "the migration list, or on an `ad_*` glob, misses tables such as "
            "options_coach_events. Build tools/trading/migrate/export_ft_data.py: "
            "enumerate tables from the DDL sites (AST/regex over tools/trading/** "
            "and tools/fathomdesk/**), pg_dump --schema-only + --data-only for "
            "exactly that list, write a per-table row-count manifest (json), and "
            "`--verify <manifest>` that diffs counts against a target database and "
            "exits non-zero on any difference. --dry-run lists the tables and "
            "writes nothing.\n\n"
            "This is a `run` task: it produces tooling and a rehearsal report "
            "against a scratch database, not the cutover itself. The cutover "
            "(freeze via kill switch, export, import into icdev_ft, smoke with "
            "tools/testing/fathomdesk_smoke.py, flip, >= 7 day dual-run) is done by "
            "hand in the order the programme doc gives. IT migrations are never "
            "renumbered or deleted."
        ),
        "priority": "high",
        "task_type": "run",
    },
]

# -- icdev-core ----------------------------------------------------------------
XCORE: list[dict] = [
    _gate(
        "xcore-",
        "the icdev-core repository does not exist yet (it is carved at programme "
        "phase P3, after the in-repo xit-core-* moves), so every xcore-* task "
        "would be dispatched against a target the runner cannot reach.",
        "the repo exists AND $env:ICDEV_KANBAN_REPO_CORE points at it.",
    ),
    {
        "id": "xcore-boot-01",
        "title": "Carve icdev-core: subtree split, pyproject, CI, per-domain bootstrap",
        "description": (
            "git subtree split of the core portions of icdev/ plus tests/core/ "
            "into the icdev-core repo. pyproject.toml names the distribution "
            "icdev-core with the import root unchanged (icdev.*); the IT parent "
            "keeps the PyPI name `icdev` and depends on icdev-core. CI is copied "
            "from .github/workflows/icdev-ci.yml's required jobs (Lint, Test, "
            "Security Scan). icdev/data/claude_bootstrap/CLAUDE.md becomes a "
            "per-domain artefact generated by prebuild_bootstrap.py from the "
            "domain's declaration, so the bootstrap_parity gate keeps working. "
            "Dev consumption is `pip install -e ../icdev-core` in each parent's "
            "venv; release is a semver tag whose wheel is a GitHub release asset "
            "mirrored to a local wheelhouse for `pip install --no-index "
            "--find-links` (pure Python, offline). Tag the IT repo pre-core-split "
            "before the first removal."
        ),
        "priority": "critical",
    },
    {
        "id": "xcore-api-01",
        "title": "core_api_manifest.json + semver; parents check vendor parity against it",
        "description": (
            "Publish the core's public surface (module, symbol, signature hash) as "
            "core_api_manifest.json on every release and reuse the existing "
            "args/vendor_parity.yaml / coherence check_vendor_parity machinery so "
            "each parent fails its own coherence gate when it calls a symbol the "
            "pinned core no longer exports. No floating `main` dependency anywhere."
        ),
        "priority": "high",
    },
    {
        "id": "xcore-compat-01",
        "title": "core-compat CI matrix {it, ft}: prove a core change against BOTH parents",
        "description": (
            "A workflow in the core repo that checks out each parent (FT via a "
            "deploy-key secret), installs core from the PR SHA, and runs the "
            "parent's declared `ci.compat_suite` (args/ci_test_files/"
            "core_compat.txt - ~50 gated modules that touch storage/kanban/llm/"
            "genesis) plus `coherence_checker --tier core`. Branch protection "
            "requires both legs. Also ship tools/dev/core_compat_local.py reading "
            "ICDEV_CORE_PARENTS (a ; separated list of parent roots) so the same "
            "check runs on the developer's machine before push."
        ),
        "priority": "high",
    },
]

# -- ICDEV[FT] -----------------------------------------------------------------
XFT: list[dict] = [
    _gate(
        "xft-",
        "the ICDEV[FT] repository is PRIVATE and does not exist yet, so every "
        "xft-* task would be dispatched against a target the runner cannot reach "
        "- and live-trading code (order routing, broker credentials, live "
        "authorization) must NEVER be built unattended even once it does.",
        "the repo exists, $env:ICDEV_KANBAN_REPO_FT points at it, and only for "
        "epics a human has marked runner-safe (never xft-safe-*).",
    ),
    {
        "id": "xft-boot-01",
        "title": "Bootstrap ICDEV[FT]: domain declaration, FIN_ env, own PostgreSQL database, CI",
        "description": (
            "`icdev init --domain ft`-style bootstrap of the private repo: "
            "icdev_domain.yaml (key ft, env_prefix FIN, db name_env FIN_PG_DATABASE "
            "= icdev_ft, API port 5200, no IL levels - sensitivity labels "
            "PUBLIC/LICENSED/MNPI/PII/ACCOUNT_SECRET/TRADE_RECORD), a .env.template "
            "whose secrets are `env:`/`vault:` REFERENCES only, a separate "
            "PostgreSQL ROLE so a wrong database is a permission error rather than "
            "a silent write, and CI with the full gate set from day one (red-first, "
            "skip census, no-hardcoded-model-id test, the leak gate, "
            "architecture-agnosticism). No FathomDesk code yet. Package icdev_fin; "
            "FT's own kanban board uses prefix fin-."
        ),
        "priority": "critical",
    },
    {
        "id": "xft-ux-01",
        "title": "Design deliverables BEFORE UI code: IA, tokens, component inventory, 4 mockups",
        "description": (
            "The owner's bar: 'professional UI/UX, not like ICDEV[IT]'. Produce, in "
            "order and reviewed with the owner: docs/ux/ia.md (sitemap of the "
            "eleven screens + five flows: ingest-a-paper, ask->spec->generate, "
            "run-and-read-a-backtest, promote-to-paper, trip/clear kill-switch); "
            "ui/tokens.json (dark-first + light, density variants, semantic tokens "
            "for pnl/risk/provenance/lifecycle/status, tabular numerals) generating "
            "tokens.css + the Tailwind theme; docs/ux/components.md inventory; "
            "hi-fi mockups of Home/Ops, Strategy Builder, Backtest Lab and "
            "Promotion & Approvals, each dark/light x normal/dense. Only after "
            "sign-off is ui/ scaffolded (xft-ui-01)."
        ),
        "priority": "high",
    },
    {
        "id": "xft-ui-01",
        "title": "ft_api (FastAPI, port 5200) + ui/ (Vite, React, TS) shell, Home/Ops, kill-switch",
        "description": (
            "FastAPI app whose pydantic models ARE the OpenAPI and ARE the Strategy "
            "Spec JSON Schema; /api/v1/*, /ws multiplexed channels with seq numbers "
            "and REST snapshots for every topic, /legacy/* mounting the migrated "
            "FathomDesk Flask app during transition, /* serving the built SPA. "
            "ui/: Vite + React + TS strict, TanStack Router/Query, shadcn/ui + "
            "Tailwind on the tokens from xft-ux-01, TradingView Lightweight Charts, "
            "ECharts (tree-shaken), TanStack Table + Virtual, Sigma.js for the KG. "
            "Ship the shell + Home/Ops with the GLOBAL KILL-SWITCH in the top bar "
            "on every route (confirm + step-up passphrase + audit row). dist/ is "
            "NOT committed: CI builds it, records a sha256 and packages it inside "
            "the wheel; an offline-proof CI job installs the wheel with no network "
            "and no Node and runs the Home E2E. OpenAPI/zod drift checks, "
            "size-limit budgets, Lighthouse and axe thresholds, Playwright "
            "screenshots per screen x theme x density."
        ),
        "priority": "high",
    },
    {
        "id": "xft-ing-01",
        "title": "arXiv fetcher + the missing PDF downloader -> ingest_file with persisted provenance",
        "description": (
            "ICDEV[IT]'s tools/research/source_scanner.py::scan_academic_papers "
            "(~L830-945) parses arXiv Atom metadata and captures pdf_url but never "
            "downloads it, and research_signals is not a RAG source. Build "
            "icdev_fin/knowledge/fetchers/arxiv.py (reuse the parser shape, write "
            "kb_papers) and knowledge/downloader.py: per-host token bucket (arXiv 1 "
            "request / 3 s), If-Modified-Since, sha256 of bytes, pathlib paths "
            "data/corpus/<source>/<id>/<version>.pdf, backoff, one kb_fetch_log "
            "row per attempt. Canonical key arxiv:<id> stripped of vN (versions in "
            "kb_paper_versions). Hand the PDF to document_intelligence."
            "ingest_orchestrator.ingest_file(path, collection_id='kb-arxiv-qfin', "
            "classification='PUBLIC', bridge_kg=False) and persist a provenance "
            "row (source_url, sha256, retrieved_at, license). The provenance gate "
            "REFUSES an ingest without it. Tests use a fixture PDF, no network."
        ),
        "priority": "high",
    },
    {
        "id": "xft-onto-01",
        "title": "finance.ttl ontology + loader into kg_ontology (PROBE FIRST: it is empty)",
        "description": (
            "kg_ontology holds ZERO rows on the live IT database (measured "
            "2026-08-21 by capability_consumption --probe-plan). This task is the "
            "WRITER. Author args/ontology/finance.ttl with prefix fin: (IT already "
            "owns strategy.ttl for business strategy): TradingStrategy, Signal, "
            "Factor, Instrument, Contract, Market, Venue, Session, Regime, "
            "RiskModel, CostModel, Metric{Sharpe,IC,MaxDrawdown,Calmar,Turnover}, "
            "Paper, Claim, Evidence, Dataset, Method{RLAlgorithm,HMM,Regression}; "
            "edges claims, supportedBy (Claim->Evidence->Section), usesFactor, "
            "evaluatedOn, reportsMetric, appliesTo, assumesRegime. Register through "
            "tools/ontology/federation.py::build_federation. The KG bridge runs "
            "extract_graph_llm with a typed vocabulary and NEVER the IT regex path "
            "(ingester._ENTITY_KEYWORDS is NIST/DoD-hardwired). Test asserts "
            "kg_ontology rows > 0 after load and re-runs the substrate probe."
        ),
        "priority": "high",
    },
    {
        "id": "xft-spec-01",
        "title": "Strategy Spec: one pydantic model = OpenAPI = form schema, safety fields mandatory",
        "description": (
            "args/strategy_spec.schema.json generated from icdev_fin/strategy/"
            "spec.py (pydantic): family, asset_class, universe (+ roll rule), bars, "
            "session, signals[] each with citations[] in the forms arxiv:ID S / "
            "doi: / edgar:ACC / fred:SERIES / oss:<repo>#path, entry/exit, sizing, "
            "risk (daily_loss, max_drawdown, kill_switch: required - MANDATORY), "
            "costs, evaluation (walk-forward, oos_min_sharpe, seeds), optional rl "
            "block (env, reward, algo, obs, seeds). Served at "
            "/api/v1/schemas/strategy-spec.json for the UI form (react-hook-form + "
            "zod generated from it). Schema tests; a spec without a kill_switch or "
            "without at least one citation per signal is invalid."
        ),
        "priority": "high",
    },
    {
        "id": "xft-tmpl-01",
        "title": "_base + momentum templates -> generator -> standalone system repo with provenance.json",
        "description": (
            "templates/trading_systems/{_base,momentum}/ rendered by the core's "
            "template_engine.render_tree. icdev_fin/strategy/generator.py mirrors "
            "the step structure of tools/builder/child_app_generator.py (no "
            "GENERATION_TOOLS copied): validate -> provenance resolve (every "
            "citation must hit kb_claims.status='verified', else fail or an "
            "audited HITL force-override) -> render -> vendor the runtime allowlist "
            "(data/provider, data/calendars, brokers, execution, risk, "
            "factors/cost_model, backtest) into <system>/runtime/ with imports "
            "rewritten and no parent DB -> write provenance.json via "
            "citation_grounding.build_artifact_provenance -> "
            "fin_system_validator --project-dir X --gate (spec<->code hash, "
            "citations present in code docstrings, tests exist, live flag OFF) -> "
            "git init + commit -> register in fin_systems and "
            "args/kanban_external_repos.yaml. Generated tree: README, runbook, "
            "spec.yaml, provenance.json, config/{system,paper,live}.yaml, "
            "strategy/, runtime/, data/, backtests/, tests/, scripts/."
        ),
        "priority": "high",
    },
    {
        "id": "xft-bt-01",
        "title": "Backtest engine: point-in-time guards, walk-forward, costs, calendars, rolls",
        "description": (
            "Start from tools/fathomdesk/backtester.py::BacktestSession (bar-by-bar, "
            "FIFO lots, Sharpe/Calmar/MaxDD; NO look-ahead guard, NO walk-forward, "
            "NO calendars). Add guards.py (a feature read at t+1 RAISES), next-bar "
            "fills, cost_model, session calendars + contract rolls with "
            "back-adjustment, walkforward.py (train/test windows, OOS metrics, "
            "deflated Sharpe / PBO), multi-seed. Results to fin_backtest_runs. "
            "Tests: a deliberately shifted signal must NOT improve (look-ahead "
            "test); NYSE / CME RTH / crypto 24x7 calendar tests; deterministic "
            "seed. Run the momentum spec from xft-tmpl-01 end to end and persist "
            "the result with provenance."
        ),
        "priority": "high",
    },
    {
        "id": "xft-safe-01",
        "title": "assert_may_trade on EVERY order path + kill-switch consumption test + HITL live gate",
        "description": (
            "Today args/trading_micro_live.yaml declares ICDEV_TRADING_MODE and "
            "require_human_approval_for_sell and ZERO code sites consume either - "
            "the declared-but-unconsumed bug, in the safety path. Build "
            "icdev_fin/governance/live_authorization.py::assert_may_trade(order): "
            "mode='live' requires a non-expired, non-revoked fin_live_authorizations "
            "row whose audit_event_id verifies in the hash chain, checked on EVERY "
            "order - consumed by auto_trader, exit_executor, auto_trade_options and "
            "brokers/router. The only writer of that row is the HITL approve "
            "endpoint (step-up passphrase, Ed25519-signed decision, "
            "record_hitl_decision fail-closed -> audit). Kill switch: global + "
            "per-system, trip -> cancel all / revoke authorizations; clearing a "
            "global trip is HITL. Tests: red-first (a live order without an "
            "authorization row is refused), an AST consumption test that every "
            "module calling a broker submit_* imports and calls is_killed and "
            "assert_may_trade, and capability_consumption class live_gate with "
            "allowance 0."
        ),
        "priority": "critical",
    },
    {
        "id": "xft-rl-01",
        "title": "MarketEnv (gymnasium) + trainer + policy_runner through the SAME risk/execution path",
        "description": (
            "icdev_fin/rl/env.py MarketEnv(gym.Env): observation = window of "
            "FeatureStore features (the same store the rule families read, "
            "point-in-time via the backtest guards) + position/cash/time-to-close; "
            "action Discrete(3) or Box weight; step = next bar with cost_model "
            "fills; episode = one session; rewards.py pluggable (pnl, log_return, "
            "differential_sharpe, dd_penalized). trainer.py is config-driven "
            "(algo ppo|dqn|sac, seeds, time-ordered splits) with an adapter "
            "registry so stable-baselines3 is an optional [rl] extra. "
            "policy_runner.py implements the same Strategy protocol as the rule "
            "families (on_bar(features) -> TargetPosition) so every order flows "
            "through risk_checker -> order_manager -> kill_switch; no policy calls "
            "a broker. Env API-conformance test against a stub env in CI; training "
            "only from an explicit session (this is a `run`-shaped epic)."
        ),
        "priority": "high",
    },
    {
        "id": "xft-data-01",
        "title": "Import the FathomDesk export into icdev_ft; manifest diff == 0; smoke",
        "description": (
            "Consume the export from xit-cut-01: FT migrations (fresh timestamp "
            "ids via migrate.py --create) recreate the DDL-enumerated tables "
            "minus user_id/tenancy columns and the duplicated kg_* tables; import "
            "the data; `export_ft_data.py --verify` must report zero differences; "
            "tools/testing/fathomdesk_smoke.py --url http://localhost:5200 --json "
            "green. Done only during the hand-run cutover window."
        ),
        "priority": "medium",
    },
]


def _with_gate_deps(stream: list[dict]) -> list[dict]:
    gate = next(t for t in stream if t["id"].endswith("gate-00"))
    out = []
    for t in stream:
        spec = dict(t)
        if spec["id"] != gate["id"]:
            spec.setdefault("depends_on_task_id", gate["id"])
        out.append(spec)
    return out


def all_specs() -> list[dict]:
    return _with_gate_deps(XIT) + _with_gate_deps(XCORE) + _with_gate_deps(XFT)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument(
        "--claim", action="append", default=[], metavar="TASK_ID",
        help="seed, then claim these ids for this session (repeatable)",
    )
    args = ap.parse_args()

    specs = all_specs()
    if args.dry_run:
        for t in specs:
            dep = t.get("depends_on_task_id") or "-"
            print(f"{t['id']:<16} [{t.get('status', 'backlog'):<11}] dep={dep:<14} {t['title']}")
        print(f"\n{len(specs)} tasks ({len(XIT)} xit-, {len(XCORE)} xcore-, {len(XFT)} xft-)")
        return 0

    claim_ids = set(args.claim)
    unknown = claim_ids - {t["id"] for t in specs}
    if unknown:
        raise SystemExit(f"--claim names ids not in this seed: {sorted(unknown)}")
    to_claim = [t for t in specs if t["id"] in claim_ids]
    rest = [t for t in specs if t["id"] not in claim_ids]
    created = create_tasks(rest)
    if to_claim:
        created += create_tasks(to_claim, claim=True)
    print(json.dumps({"created": created, "count": len(created),
                      "claimed": sorted(claim_ids)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
