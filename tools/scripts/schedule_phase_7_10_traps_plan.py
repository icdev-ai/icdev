# CUI // SP-CTI
"""Idempotent enqueue for FathomDesk Phase 7.10 — Trap Detection.

Brief: docs/briefs/phase-7.10-trap-detection.md
Project: args/projects.yaml → fathomdesk-7-10, prefix=ad710-

Blocked on 7.9 — first task depends on the last task of 7.9 (ad79-wrap-04).
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone

from tools.dashboard.config import DB_PATH
from tools.db.storage import get_connection

PREFIX = "ad710-"
EXTERNAL_PREDECESSOR = "ad79-wrap-04"   # 7.10 starts after 7.9's final task

GATE = (
    "PHASE-EXIT GATE (all 5 must pass before next phase unblocks): "
    "(1) python tools/code_intelligence/codelens.py --all --json; "
    "(2) python tools/workflow/coherence_checker.py --all --fix --gate; "
    "(3) python tools/testing/e2e_full_dashboard.py; "
    "(4) pytest tests/ -x --timeout=120 --ignore=tests/e2e_selenium; "
    "(5) python tools/dx/companion.py --sync --write --json."
)

SUBTASKS: list[tuple[str, str, str, str, str]] = [

    # ═══════════ MACRO ═══════════
    ("macro-01", "macro-01: macro data ingestor (Fed funds, M2, CPI)",
     "build", "high",
     "Extend tools/trading/data/macro_data.py (or create if missing). Ingest "
     "Fed funds rate, M2 money supply, headline CPI YoY. Use yfinance FRED symbols "
     "if available (FEDFUNDS, M2SL, CPIAUCSL). Fallback: stubbed constants with "
     "loud WARNING log. Cache fetched values in-process 6h. Public: "
     "fetch_macro_indicators() -> {fed_funds, m2_velocity, cpi_yoy, as_of}."),

    ("macro-02", "macro-02: liquidity_trap_detector + migration 021",
     "build", "high",
     "Create migration 021_ad_macro_regimes (append-only). Columns: id, regime_type, "
     "active (bool), evidence_json, confidence, created_at. Register in "
     "APPEND_ONLY_TABLES + conftest schema. Then create tools/trading/ta/"
     "macro_liquidity.py with detect_liquidity_trap(data) -> {active, evidence, "
     "confidence}. Active when: fed_funds < 1.0 AND m2_velocity change < 2% YoY "
     "AND cpi_yoy < target (default 2.5%). Writes one row per day (idempotent)."),

    ("macro-03", "macro-03: Oracle regime_lens + preflight integration",
     "build", "high",
     "(a) In Oracle regime_lens: pull latest ad_macro_regimes row; if active "
     "liquidity_trap, attach as evidence with weight 0.3. (b) In options/"
     "preflight.py: when liquidity_trap active, add block (code='liquidity_trap_"
     "premium_sell') for strategies where payoff indicates short-premium posture "
     "(max_profit < abs(max_loss) and max_profit > 0). Bypassable via env "
     "ICDEV_PREFLIGHT_LIQTRAP_OVERRIDE=true or per-call kwarg."),

    ("macro-gate", "macro-gate: Epic MACRO exit validation",
     "test", "high", GATE),

    # ═══════════ TECHNICAL ═══════════
    ("technical-01", "technical-01: tools/trading/ta/traps.py",
     "build", "critical",
     "Create traps.py. Uses 7.9 primitives: find_swings, find_support_resistance. "
     "Public: detect_traps(bars) -> [{pattern:'bull_trap'|'bear_trap', broken_level, "
     "breakout_bar, reentry_bar, confidence, volume_ratio, lower_high_bar?}]. "
     "Bull trap: price closes ABOVE an S/R level with volume < breakout_volume_ratio "
     "(default 0.7 of 20-bar avg), then closes BACK below within max_reentry_bars "
     "(default 3), then forms a lower high within confirmation_lookback_bars "
     "(default 5). Bear trap: mirror. Thresholds in args/ta_config.yaml::traps."),

    ("technical-02", "technical-02: trap_scanner daemon reflex",
     "build", "high",
     "(a) Migration 022_ad_trap_events (append-only): id, ticker, pattern, "
     "broken_level, confidence, evidence_json, created_at. Register append-only. "
     "(b) New reflex in market_intel/daemon.py: 'trap_scanner' on 15m schedule. "
     "For each user, union of (watchlist tickers, open-position tickers); fetch "
     "bars, run detect_traps, insert events. Dedupe per (ticker, pattern) within "
     "24h. Add to args/trading_daemon_config.yaml."),

    ("technical-03", "technical-03: signals + virtual alert subjects",
     "build", "high",
     "(a) Extend /signals page: new 'Traps' column showing most-recent trap event "
     "per ticker (with pattern icon + bar-age). (b) Register virtual subjects "
     "TRAP_BULL + TRAP_BEAR in tools/trading/alerts/virtual_subjects.py evaluator. "
     "Resolve as: 'did ticker X have a matching trap event in the last N bars?' "
     "(c) Add to args/alerts_virtual_subjects.yaml."),

    ("technical-04", "technical-04: coach integration — trap_against_position",
     "build", "high",
     "Extend tools/trading/options/coach_engine.py. New event_type "
     "'trap_against_position': when a position is LONG an underlying and "
     "detect_traps just emitted a bull_trap on that underlying (or SHORT + "
     "bear_trap). Add to COACH_EVENT_TYPES tuple + migration 020 constraint. "
     "Dedupe per (position, trap_event_id). coach_llm explanation cites broken "
     "S/R level, volume confirmation, and close/hedge/adjust recommendation."),

    ("technical-gate", "technical-gate: Epic TECHNICAL exit validation",
     "test", "high", GATE),

    # ═══════════ INTEGRATION ═══════════
    ("integration-01", "integration-01: Genesis trap-scenarios reflex",
     "build", "medium",
     "Create tools/genesis/reflexes/fathomdesk_trap_scenarios.py conforming to "
     "Genesis contract (run(config, trust) -> {success, metric_value, details}). "
     "Hourly cadence. For high-severity trap events (confidence >= 0.8) since "
     "last run: auto-spawn a matching scenario via scenario_engine.run_scenario "
     "('potential_reversal_cascade'). Write daily trap digest to Pulse. Register "
     "in Genesis reflex list."),

    ("integration-02", "integration-02: pytest for trap detection",
     "test", "high",
     "Create tests/test_ta_traps.py. Handcrafted fixtures: (a) breakout above "
     "$100 with declining volume then close back at $98 + lower high at $99 → "
     "bull_trap detected; (b) mirror bear_trap fixture; (c) clean breakout with "
     "rising volume and continuation → NO trap detected; (d) liquidity_trap "
     "active scenario → preflight blocks short-condor proposal; (e) coach event "
     "emitted when trap fires on a user's position."),

    ("integration-gate", "integration-gate: Epic INTEGRATION exit validation",
     "test", "high", GATE),

    # ═══════════ WRAP ═══════════
    ("wrap-01", "wrap-01: manifest + coherence + companion",
     "chore", "high",
     "Add ta/macro_liquidity.py + ta/traps.py to manifest. coherence --all --fix "
     "--gate → 17/17. companion.py --sync --write."),

    ("wrap-02", "wrap-02: feature doc + Selenium E2E",
     "chore", "high",
     "Write docs/features/phase-7.10-trap-detection.md + create "
     "tests/e2e_selenium/test_ad710_traps.py: /analysis shows trap markers; "
     "/signals shows trap column; coach card shows trap event."),

    ("wrap-03", "wrap-03: backlog + memory",
     "chore", "medium",
     "Append 7.10 to docs/fathomdesk-backlog.md. Update memory with trap-regime "
     "notes + the 'liquidity trap blocks short premium' safety rule."),
]


def enqueue() -> dict:
    conn = get_connection(db_path=str(DB_PATH))
    now = datetime.now(timezone.utc).isoformat()
    inserted: list[str] = []
    skipped: list[str] = []
    parent: str | None = EXTERNAL_PREDECESSOR
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
        "project": "fathomdesk-7-10",
        "prefix": PREFIX,
        "predecessor": EXTERNAL_PREDECESSOR,
        "inserted": inserted,
        "skipped_already_present": skipped,
        "total_subtasks": len(SUBTASKS),
    }


if __name__ == "__main__":
    result = enqueue()
    print(json.dumps(result, indent=2))
    sys.exit(0 if result["inserted"] or result["skipped_already_present"] else 1)
