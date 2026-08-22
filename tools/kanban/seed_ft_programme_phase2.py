# CUI // SP-CTI
"""Seed programme phase 2 onto the kanban board: the FIVE remaining ICDEV[FT] + parent-split initiatives.

The original 13 xcore-/xft- tasks seeded the FT build; most xft- ones are now DONE (built manually in the
private icdev_ft repo, PRs #11-#39). This adds the tasks that remain, under EXISTING epics so no card changes
are needed. They are held MANUAL exactly as the programme demands:

  xft-*  build in the PRIVATE icdev_ft repo. Registered in args/kanban_external_repos.yaml with
         ICDEV_KANBAN_REPO_FT unset, so the dispatcher can never build them inside this PUBLIC checkout.
  xit-*  build here (IT), but every one depends on xit-gate-00 (held in_progress), so
         promote_backlog_to_scheduled never dispatches them -- they are built by hand.

PUBLIC-REPO DISCIPLINE. This is the open ICDEV[IT] repo. Descriptions carry the ENGINEERING; they carry no
broker credentials, no account numbers, no customer data. "The new paper account" is described by its ROLE,
never its keys -- those live only in the private repo's .env and the local board.

    python -m tools.kanban.seed_ft_programme_phase2            # seed the real board
    python -m tools.kanban.seed_ft_programme_phase2 --dry-run  # print, change nothing
"""
from __future__ import annotations

import argparse
import json

from tools.kanban.task_factory import create_tasks

# ── ICDEV[FT] (private repo; parked by the external-repo registry) ────────────
FT: list[dict] = [
    {
        "id": "xft-tmpl-02",
        "title": "Regime family: a dependency-free regime detector + template",
        "priority": "high",
        "description": (
            "The regime strategy family (FT-7). The plan reached for FathomDesk's ml/regime_hmm.py, which was "
            "never migrated -- so build it fresh in the pattern the other five families use, dependency-free "
            "and deterministic: a small Gaussian/vol-state HMM (or a k-means/threshold regime detector) fit on "
            "point-in-time features through the History guard, exposing on_bar(history)->float so it flows "
            "through the identical engine / sizer / cost / order-gate path. Add family=regime to the spec "
            "Family + a Regime block (schema regenerated), families.from_spec wiring, a "
            "templates/trading_systems/regime template, generator bootstrap-fit at generation time, tests that "
            "recover a known regime structure, a generator test for a standalone system, and the Forecast/UI "
            "schema regen. Mirror the RL/supervised bootstrap discipline; keep it parent-token-free in the "
            "vendored runtime."
        ),
    },
    {
        "id": "xft-tmpl-03",
        "title": "Options family: a spread strategy over the options data model",
        "priority": "medium",
        "description": (
            "The options strategy family (FT-7). FathomDesk's options/ was not migrated; build a first options "
            "family fresh: family=options requires asset_class=option (the spec already enforces this). Start "
            "with a defined-risk vertical-spread rule over the fin_option_contracts / chain data model, a "
            "Greeks/IV-rank signal, and the same on_bar contract adapted for an options leg set. Spec Options "
            "block, template, generator wiring, look-ahead-safe tests, standalone generation. Options pricing "
            "stays deterministic (no live vendor); a fixture chain drives CI. Scope the MVP to one spread type; "
            "multi-leg and American-exercise modelling are follow-ups."
        ),
    },
    {
        "id": "xft-ing-02",
        "title": "Run the knowledge pipeline for real: papers -> RAG -> verified claims",
        "priority": "high",
        "description": (
            "The fetchers/downloader/ingest/claims code all EXISTS but has never RUN -- which is why generation "
            "falls back to fixture citations. Run it end to end: fin_arxiv_ingest pulls real q-fin.PM/TR papers, "
            "the downloader fetches PDFs (per-host token bucket, sha256, kb_fetch_log), ingest_file chunks them "
            "into RAG, the finance-vocabulary claim extractor writes kb_claims, and citation_grounding.verify "
            "binds spans so claims reach status='verified'. Then a real strategy spec generates against GENUINE "
            "verified provenance, no override. Bounded (cap PDFs/run), rate-limited, network-aware; the ontology "
            "(finance.ttl -> kg_ontology) is loaded and probed for rows. Report how many claims verified."
        ),
    },
    {
        "id": "xft-data-02",
        "title": "Real market-data provider seam (replace the fixture-only path)",
        "priority": "high",
        "description": (
            "Everything -- backtest, the Forecast page, generation -- is fixture-driven today. Wire a real "
            "MarketDataProvider (keeping the ABC's mandatory source/as_of/is_delayed provenance) behind the "
            "same seam FixtureProvider uses, so the identical code runs on ACTUAL bars. Broker-agnostic: the "
            "paper broker's own data feed is one provider; declared sources in args/market_data_sources.yaml. "
            "FixtureProvider stays the CI default (deterministic); the real provider is opt-in by config. This "
            "unblocks real paper trading and real-data backtests."
        ),
    },
    {
        "id": "xft-safe-02",
        "title": "Real paper trading at scale + the first HITL activation (FT-9)",
        "priority": "high",
        "description": (
            "Run the paper session (paper/stream_cli) against the operator's NEW paper account (a fresh account "
            "with real buying power -- the previous run was blocked by a short book, not by code). A generated "
            "system streams real bars, its strategy proposes targets, and every order passes assert_may_trade + "
            "the kill-switch on the order path. Verify the account first (icdev_fin.brokers.verify -- reports "
            "shape, never values). Then exercise the FT-9 promotion: paper -> live_candidate on the paper "
            "record, and the live_candidate -> live HITL gate (manual kanban gate + step-up-signed approval + a "
            "fin_live_authorizations row) with LIVE STILL DISABLED by config -- prove the gate refuses an order "
            "with no authorization, and that a human approval is required. Real-money live is a separate, "
            "explicitly-human step; this lands the machinery and real paper flow."
        ),
    },
]

# ── ICDEV[IT] parent-split removal (built here, held behind xit-gate-00) ──────
IT: list[dict] = [
    {
        "id": "xit-rm-02",
        "title": "Remove tools/trading + market_intel from IT (P4b)",
        "priority": "high",
        "depends_on_task_id": "xit-gate-00",
        "description": (
            "P4a (dashboard pages + /api/trading/*) already landed (#1900). P4b removes the code: tools/trading, "
            "tools/market_intel, the icdev/tools mirrors, args/trading*.yaml + llm_config_trading.yaml, "
            "seed_fdt_*.py, the fathomdesk-trading-engine manifest shards, and the doc_command_gate / "
            "insert_schema_gate / package_exclusions / PARENT_ONLY_DIRS entries. One PR, every gate green after. "
            "Lower raw_insert_max by exactly the lines removed; never raise a census max. The ad_* migrations "
            "stay as history (never deleted/renumbered). Verify the union merge did not resurrect a manifest row."
        ),
    },
    {
        "id": "xit-rm-03",
        "title": "Remove trading tests + prune the census ceilings (P4c-f)",
        "priority": "high",
        "depends_on_task_id": "xit-gate-00",
        "description": (
            "Delete the 46 test files importing tools.trading/tools.fathomdesk; gated_test_list.py "
            "--prune-backlog and lower backlog_max by the removed count; re-run the 4 gated tests that reference "
            "tools.trading by string. undeclared_import_census.py --prune and lower undeclared_max (drop the "
            "yfinance entries). Retire the fdt project card + its seeders. Trim conftest.py's ad_ schema. "
            "domain_leak_gate flips to path-denylist ENFORCE once tools/trading is gone. Each census max only "
            "goes DOWN."
        ),
    },
    {
        "id": "xit-gen-01",
        "title": "GENESIS_APPS + launcher decoupling for the trading subprocess (P4/GEN)",
        "priority": "medium",
        "depends_on_task_id": "xit-gate-00",
        "description": (
            "GENESIS_APPS -> args/genesis_apps.yaml with root_env; _genesis_run returns root_missing instead of "
            "raising when a sibling root is absent; launcher.py stops starting the trading dashboard subprocess "
            "on 5100. Playwright /genesis stays green. The 8 fathomdesk_* / pmo_* reflex entries leave "
            "REFLEX_NAMES + reflex_registry + args/genesis_config.yaml WITH their modules in one PR "
            "(reflex_registration test)."
        ),
    },
    {
        "id": "xit-rm-04",
        "title": "P5: unhold the FT/core runner once removal is complete",
        "priority": "medium",
        "depends_on_task_id": "xit-gate-00",
        "description": (
            "After the removal PRs land: set ICDEV_KANBAN_REPO_FT / _CORE where the scheduler runs, release the "
            "relevant gate-00 holds only for runner-safe epics, and land one trivial dispatch-proof FT PR to "
            "confirm the external-repo path works. pr_watcher must merge nothing without approval "
            "(auto_merge_require_approval stays true for the window)."
        ),
    },
]

ALL = FT + IT

# xft- tasks are parked by the external-repo registry; the reconciliation of the already-built xft- backlog
# tasks (bt/ing/onto/rl/safe/spec/tmpl/ui/ux/data -> done, PRs #11-#39) is done by the operator via the CLI,
# not here, because a seeder must not fabricate completion.


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "seed programme phase 2").split("\n")[0])
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    if args.dry_run:
        print(json.dumps([{"id": t["id"], "title": t["title"], "priority": t.get("priority"),
                           "depends_on": t.get("depends_on_task_id")} for t in ALL], indent=2))
        return 0
    ids = create_tasks(ALL)
    print(f"seeded {len(ids)} task(s): {', '.join(ids)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
