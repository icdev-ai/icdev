# CUI // SP-CTI
"""Idempotent enqueue for FathomDesk Phase 7.7 — Probability & Compare.

Brief: docs/briefs/phase-7.7-probability-compare.md
Project: args/projects.yaml → fathomdesk-7-7, prefix=ad77-
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone

from tools.dashboard.config import DB_PATH
from tools.db.storage import get_connection

PREFIX = "ad77-"

GATE = (
    "PHASE-EXIT GATE (all 5 must pass before next phase unblocks): "
    "(1) python tools/code_intelligence/codelens.py --all --json; "
    "(2) python tools/workflow/coherence_checker.py --all --fix --gate; "
    "(3) python tools/testing/e2e_full_dashboard.py; "
    "(4) regression pytest: pytest tests/ -x --timeout=120 --ignore=tests/e2e_selenium; "
    "(5) python tools/dx/companion.py --sync --write --json."
)

SUBTASKS: list[tuple[str, str, str, str, str]] = [

    # ═══════════════════════════════════════════════════════════
    # Epic PROB — probability of profit + price cone
    # ═══════════════════════════════════════════════════════════
    ("prob-01", "prob-01: args/options_prob_config.yaml",
     "build", "high",
     "Create args/options_prob_config.yaml. Fields: n_samples: 10000, "
     "deterministic_seed: true (so re-renders are stable), "
     "cone_bands: [5, 25, 50, 75, 95] (percentile bands for the shaded cone), "
     "default_iv_fallback_pct: 40 (used when chain doesn't expose IV), "
     "min_dte_for_monte_carlo: 1 (skip intraday, too noisy). "
     "Hot-reloadable via mtime check (same pattern as options_intent_schema.yaml)."),

    ("prob-02", "prob-02: tools/trading/options/probability.py",
     "build", "critical",
     "Create probability.py. Public: compute_pop(legs, spot, iv_annual_pct, "
     "dte_days, qty_multiplier=1) -> dict. Uses IV-implied lognormal: "
     "log-return ~ Normal(0, sigma*sqrt(T)) where sigma=iv/100, T=dte/365. "
     "Sample n_samples terminal prices, compute P&L per path using the same "
     "leg-payoff math as compute_payoff (intrinsic at expiry, 100x multiplier). "
     "Return {pop_pct: float (0-100), expected_pnl: float, "
     "percentile_prices: {p5, p25, p50, p75, p95}, "
     "pnl_distribution: {mean, std, p5, p95}}. Deterministic seed keyed off "
     "hash(underlying + expiry + sorted-strikes) so re-renders are stable. "
     "Skip compute when dte < 1 (return None)."),

    ("prob-03", "prob-03: wire probability into proposal_builder.build_proposal",
     "build", "critical",
     "In tools/trading/options/proposal_builder.py, after compute_payoff, "
     "call probability.compute_pop(legs, spot, iv, dte). Find IV from the "
     "first leg with a non-null iv field; fall back to "
     "options_prob_config.yaml::default_iv_fallback_pct when missing. "
     "Attach result as proposal['probability'] = {...}. Also attach a "
     "short_summary field to rationale_stub: {pop_pct, expected_pnl}."),

    ("prob-04", "prob-04: /api/options/ai-assist/propose includes probability",
     "build", "high",
     "Verify that the existing /api/options/ai-assist/propose endpoint "
     "surfaces the new proposal['probability'] field in its JSON (it will, "
     "since the response already echoes the full proposal). Add a unit-test "
     "assertion in tests/test_options_intent_parser.py OR a dedicated "
     "tests/test_options_probability.py that hits the module directly and "
     "verifies POP is in [0, 100], monotone expectations (e.g. long_call "
     "with higher strike has lower POP than lower strike)."),

    ("prob-05", "prob-05: frontend — POP badge + price-cone overlay",
     "build", "high",
     "Extend tools/trading/dashboard/templates/options.html AI Assist "
     "rendering. In aiRenderProposal: (a) add a badge near the title "
     "'POP: XX% (IV-implied)'; (b) layer the price-cone percentile bands as "
     "shaded background regions on the Chart.js payoff canvas (use "
     "multiple datasets with low opacity); (c) label the percentile dots "
     "on hover. The cone sits BEHIND the payoff line, not on top. Add a "
     "tooltip explaining 'IV-implied POP' so users know this isn't a "
     "guarantee."),

    ("prob-gate", "prob-gate: Epic PROB exit validation",
     "test", "high", GATE),

    # ═══════════════════════════════════════════════════════════
    # Epic COMPARE — side-by-side alternate comparison
    # ═══════════════════════════════════════════════════════════
    ("compare-01", "compare-01: extend build_proposal alternates to full proposals",
     "build", "high",
     "In proposal_builder.py, replace the compact alternates block (just "
     "strategy_id + max_profit + max_loss + reasons) with full proposals "
     "that include legs + payoff + probability + preflight. Keep the "
     "'alternates' key name for backward-compat; add an 'alternates_compact' "
     "fallback key so existing UI code keeps working until compare-03 "
     "lands. Each alternate goes through the same pipeline: pick_expiry + "
     "pick_strikes + compute_payoff + compute_pop."),

    ("compare-02", "compare-02: POST /api/options/ai-assist/compare",
     "build", "high",
     "Add a new Flask route. Body: {intent_text, underlying?, qty?, "
     "strategy_ids?: list}. When strategy_ids given, build a proposal for "
     "EACH (skipping rank_strategies). When omitted, return primary + all "
     "alternates from build_proposal. Response: {intent, proposals: [...]} "
     "where each item matches the shape of /propose's proposal field. "
     "Server-side preflight runs on every proposal. No LLM call — compare "
     "is a pure deterministic diff."),

    ("compare-03", "compare-03: 3-column compare grid in the AI Assist tab",
     "build", "high",
     "In options.html AI Assist rendering, add a 'Compare alternates' button "
     "below the primary proposal card. Click → fetch /compare → render a "
     "3-column responsive grid. Each column: strategy name, compact legs "
     "table, mini payoff chart (Chart.js), POP badge, preflight status "
     "(✓ / ⚠️ / ❌), and a 'Use this one' button. Grid columns collapse to "
     "stack on narrow viewports. Keep the primary proposal visible above "
     "the compare grid."),

    ("compare-04", "compare-04: promote-alternate flow",
     "build", "medium",
     "Clicking 'Use this one' on a compare column copies that alternate "
     "into aiLastProposal, re-renders the main proposal card with its "
     "content, and enables the Execute button (assuming preflight allowed). "
     "The compare grid stays visible so the user can still swap. No new "
     "API call — everything is already on the client from /compare."),

    ("compare-gate", "compare-gate: Epic COMPARE exit validation",
     "test", "high", GATE),

    # ═══════════════════════════════════════════════════════════
    # Epic WRAP
    # ═══════════════════════════════════════════════════════════
    ("wrap-01", "wrap-01: manifest + coherence + companion",
     "chore", "high",
     "Add probability.py entry to tools/manifest/fathomdesk-trading-engine.md. "
     "Run coherence_checker --all --fix --gate (target 17/17). Run "
     "companion.py --sync --write --json (10 platforms). Capture both "
     "outputs in the task completion note."),

    ("wrap-02", "wrap-02: feature doc",
     "chore", "high",
     "Write docs/features/phase-7.7-probability-compare.md. Cover: (a) why "
     "POP is IV-implied (not a guarantee), (b) how the price cone is "
     "computed, (c) when compare is most useful (neutral thesis with 3 "
     "close-scoring strategies), (d) screenshots from playwright/screenshots/. "
     "Reference phase-7.6 feature doc for the full flow context."),

    ("wrap-03", "wrap-03: Selenium E2E — POP badge + compare modal",
     "test", "high",
     "Create tests/e2e_selenium/test_ad77_probability_compare.py. Flow: "
     "log in → /options → AI Assist tab → submit intent → assert POP badge "
     "renders with a numeric value in [0, 100], assert price-cone shading "
     "layer exists on the chart. Click 'Compare alternates' → assert 3 "
     "columns visible. Click 'Use this one' on a non-primary column → "
     "assert primary proposal card updates to match. Screenshots to "
     "playwright/screenshots/options_ai_prob_*.png."),

    ("wrap-04", "wrap-04: backlog + memory update",
     "chore", "medium",
     "Append Phase 7.7 entry to docs/fathomdesk-backlog.md (add under the "
     "7.6 block). Update memory/project_fathomdesk_phase7plus.md with the "
     "7.7 bullet. Update MEMORY.md index line only if new."),
]


def enqueue() -> dict:
    conn = get_connection(db_path=str(DB_PATH))
    now = datetime.now(timezone.utc).isoformat()
    inserted: list[str] = []
    skipped: list[str] = []
    parent: str | None = None
    try:
        cur = conn.cursor()
        for suffix, title, task_type, priority, description in SUBTASKS:
            tid = PREFIX + suffix
            existing = cur.execute(
                "SELECT id, status FROM kanban_tasks WHERE id = %s", (tid,),
            ).fetchone()
            if existing:
                skipped.append(tid)
                parent = tid
                continue
            cur.execute(
                "INSERT INTO kanban_tasks "
                "(id, title, description, task_type, priority, status, "
                "depends_on_task_id, dispatch_source, created_at, updated_at, scheduled_at) "
                "VALUES (%s, %s, %s, %s, %s, 'scheduled', %s, 'manual_plan', %s, %s, %s)",
                (tid, title, description, task_type, priority, parent, now, now, now),
            )
            inserted.append(tid)
            parent = tid
        conn.commit()
    finally:
        conn.close()
    return {
        "project": "fathomdesk-7-7",
        "prefix": PREFIX,
        "inserted": inserted,
        "skipped_already_present": skipped,
        "total_subtasks": len(SUBTASKS),
    }


if __name__ == "__main__":
    result = enqueue()
    print(json.dumps(result, indent=2))
    sys.exit(0 if result["inserted"] or result["skipped_already_present"] else 1)
