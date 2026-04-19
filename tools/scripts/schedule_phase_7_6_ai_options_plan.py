# CUI // SP-CTI
"""Idempotent enqueue for FathomDesk Phase 7.6 — AI-Assisted Options.

Brief: docs/briefs/phase-7.6-ai-options-assist.md
Project registry: args/projects.yaml → key=fathomdesk-7-6, task_prefix=ad76-

Linear-chained subtasks (depends_on_task_id). Every epic ends with an exit
gate running the mandatory 5-step validation (phantom-completion mitigation
per memory feedback_kanban_phantom.md).

Re-runnable: skips rows whose stable id already exists.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone

from tools.dashboard.config import DB_PATH
from tools.db.storage import get_connection

PREFIX = "ad76-"

GATE = (
    "PHASE-EXIT GATE (all 5 must pass before next phase unblocks): "
    "(1) python tools/code_intelligence/codelens.py --all --json; "
    "(2) python tools/workflow/coherence_checker.py --all --fix --gate; "
    "(3) python tools/testing/e2e_full_dashboard.py; "
    "(4) regression pytest: pytest tests/ -x --timeout=120 --ignore=tests/e2e_selenium; "
    "(5) python tools/dx/companion.py --sync --write --json. "
    "If any step fails, stay in_progress and fix within this gate task."
)

# (suffix, title, task_type, priority, description)
SUBTASKS: list[tuple[str, str, str, str, str]] = [

    # ═══════════════════════════════════════════════════════════
    # Epic INTAKE — intent parsing
    # ═══════════════════════════════════════════════════════════
    ("intake-01", "intake-01: args/options_intent_schema.yaml",
     "build", "high",
     "Create args/options_intent_schema.yaml. Top-level 'intent_schema' with enums: "
     "direction: [bullish, bearish, neutral, volatile]; "
     "horizon: [intraday, short, earnings, medium, long]; "
     "iv_view: [high, low, neutral]; "
     "risk_cap: [defined, undefined]. "
     "Add 'horizon_dte_buckets' mapping each horizon to [min_dte, max_dte] windows. "
     "Add 'keyword_hints' dict mapping common phrases to enum values for the rule-fallback path "
     "(e.g. 'bullish' → direction=bullish; 'next earnings' → horizon=earnings; "
     "'limited risk' / 'defined risk' → risk_cap=defined; 'high IV' → iv_view=high). "
     "Hot-reloadable (mtime check pattern from persona_presets.py)."),

    ("intake-02", "intake-02: tools/trading/options/intent_parser.py",
     "build", "critical",
     "Create tools/trading/options/intent_parser.py. Public: parse_intent(text: str, underlying: str) -> dict. "
     "Primary path: call LLMRouter.get_provider_for_function('chat') with a grounded prompt "
     "(request JSON matching args/options_intent_schema.yaml enums ONLY). "
     "Fallback path (air-gap or LLM failure): rule-based keyword matcher using keyword_hints + "
     "simple regex for underlying extraction. Fallback MUST produce a best-effort valid schema dict. "
     "Return shape: {direction, horizon, iv_view, risk_cap, underlying, raw_text, source: 'llm'|'rule'}. "
     "NEVER raise — always return a dict (source='rule' on LLM unavailable per memory no_llm_mode.md). "
     "Log LLM failures at WARNING; fall back silently to rules."),

    ("intake-03", "intake-03: smoke tests — 10 sample intents",
     "test", "high",
     "Create tests/trading/test_options_intent_parser.py. Parametrize 10+ canonical intents: "
     "'Bullish AAPL through earnings, limited risk' → {direction:bullish, horizon:earnings, risk_cap:defined}; "
     "'Sell volatility on SPY next week' → {direction:neutral, horizon:short, iv_view:high}; "
     "'Short AMZN, high conviction, undefined risk ok' → {direction:bearish, risk_cap:undefined}; "
     "etc. Both LLM path (mocked) + rule-only path (ICDEV_NO_LLM=true env) must produce valid "
     "schema. Assert: direction in allowed enum; horizon in allowed enum; underlying correctly extracted."),

    ("intake-gate", "intake-gate: Epic INTAKE exit validation",
     "test", "high", GATE),

    # ═══════════════════════════════════════════════════════════
    # Epic SCORER — deterministic strategy + strike + expiry
    # ═══════════════════════════════════════════════════════════
    ("scorer-01", "scorer-01: tools/trading/options/strategy_selector.py",
     "build", "critical",
     "Deterministic strategy ranker. Public: rank_strategies(intent: dict) -> list[dict]. "
     "Loads args/options_strategies.yaml (via options.strategies.list_strategies). "
     "Scores each against intent via a rule table: "
     "bullish+defined → [bull_call_spread, long_call_butterfly, long_call]; "
     "bullish+undefined → [long_call, synthetic_long]; "
     "bearish+defined → [bear_put_spread, long_put_butterfly]; "
     "neutral+high_iv → [iron_condor, iron_butterfly, short_straddle]; "
     "neutral+low_iv → [calendar_spread, long_straddle]; "
     "volatile → [long_straddle, long_strangle]. "
     "Each returned entry: {strategy_key, score, reasons:[str]}. Top-3 only. Pure rules — NO LLM."),

    ("scorer-02", "scorer-02: tools/trading/options/strike_picker.py",
     "build", "critical",
     "Delta-target strike picker. Public: pick_strikes(strategy_key, underlying, expiry, chain) -> dict. "
     "Loads args/options_strike_targets.yaml (create with defaults: "
     "short_leg_delta: 0.30, long_leg_delta: 0.15, atm_tolerance: 0.05). "
     "For each leg template in the strategy (from options/strategies.yaml), maps the 'leg_role' "
     "(short/long/ATM) to a target delta, then selects the chain contract whose delta is closest. "
     "Falls back to nearest-strike when delta unavailable in chain data. Returns {legs:[{symbol, "
     "strike, delta, bid, ask}], warnings:[]}."),

    ("scorer-03", "scorer-03: expiry bucketizer",
     "build", "high",
     "Add pick_expiry(horizon: str, chain: dict, underlying: str) -> str to strike_picker.py. "
     "Uses horizon_dte_buckets from options_intent_schema.yaml: intraday=[0,3], short=[7,21], "
     "earnings=earnings-aware (pull next earnings date from tools/trading/data/fundamentals.py or "
     "fallback to short bucket), medium=[30,60], long=[60,120]. Returns ISO date of the chain's "
     "nearest available expiry within the bucket. If none match, returns the single nearest "
     "expiry + a 'no_expiry_in_bucket' warning."),

    ("scorer-04", "scorer-04: build_proposal() orchestrator",
     "build", "critical",
     "Create tools/trading/options/proposal_builder.py. Public: "
     "build_proposal(intent: dict, qty: int = 1) -> dict. "
     "Pipeline: rank_strategies(intent) → for top-1, fetch chain, pick_expiry(intent.horizon, chain), "
     "pick_strikes(strategy_key, underlying, expiry, chain), compute_payoff(legs) (from options/strategies.py). "
     "Return {strategy, legs, expiry, payoff:{x,y,breakevens,max_profit,max_loss}, rationale_stub, "
     "warnings, alternates:[top-2 and top-3 compact]}. Rationale_stub is a plain dict of key "
     "facts (no LLM prose yet — confirm-03 does that)."),

    ("scorer-gate", "scorer-gate: Epic SCORER exit validation",
     "test", "high", GATE),

    # ═══════════════════════════════════════════════════════════
    # Epic CONFIRM — pre-flight gates + execute modal
    # ═══════════════════════════════════════════════════════════
    ("confirm-01", "confirm-01: args/options_risk_gates.yaml",
     "build", "high",
     "Create args/options_risk_gates.yaml. Fields: "
     "max_loss_pct_of_equity: 2.0 (hard-block at 2% of account equity); "
     "min_iv_percentile: 20 (warn if selling premium into low IV); "
     "max_iv_percentile: 80 (warn if buying premium into high IV); "
     "undefined_risk_required_tier: 'L3' (block < L3 if strategy has infinite loss); "
     "max_dte_days: 180; min_dte_days: 0. Hot-reloadable."),

    ("confirm-02", "confirm-02: tools/trading/options/preflight.py",
     "build", "critical",
     "Public: run_preflight(proposal: dict, user_id: str) -> dict. "
     "Checks: (1) payoff.max_loss <= max_loss_pct_of_equity * user_equity; "
     "(2) IV percentile in window (via options/chain.iv_percentile if available, else warn 'unknown'); "
     "(3) options approval tier ≥ undefined_risk_required_tier when strategy.max_loss == -Infinity; "
     "(4) graduation gate (inherit from options.broker_gate if live path)). "
     "Returns {allowed: bool, warnings: [{code, message}], blocks: [{code, message}]}. "
     "NEVER executes — inspection only."),

    ("confirm-03", "confirm-03: POST /api/options/ai-assist/propose",
     "build", "critical",
     "In tools/trading/dashboard/app.py, add POST /api/options/ai-assist/propose. Body: "
     "{intent_text: str, underlying: str, qty?: int}. Pipeline: parse_intent → build_proposal → "
     "run_preflight → LLM call to generate a plain-English 'rationale' paragraph grounded strictly "
     "in proposal.payoff + preflight output (prompt must forbid new numbers). Returns full proposal + "
     "rationale + preflight. LLM unavailable → rationale = rule-based template filled from payoff dict."),

    ("confirm-04", "confirm-04: POST /api/options/ai-assist/execute",
     "build", "critical",
     "In app.py, POST /api/options/ai-assist/execute. Body: the proposal + preflight dict returned "
     "from /propose (server re-runs preflight for safety — never trust client). If preflight.allowed "
     "and no blocks, dispatches to sandbox_engine.place_multileg_order() for paper mode, or to the "
     "Phase 7.5 follow-up C live path when graduation_gate + options_tier checks pass. Returns "
     "{fills:[], group_id, preflight:{...}}. Any preflight block returns 403."),

    ("confirm-05", "confirm-05: /options AI Strategy Builder tab",
     "build", "high",
     "Extend tools/trading/dashboard/templates/options.html. Add a new tab 'AI Assist' alongside "
     "Strategy Builder. Content: (1) textarea 'Describe your thesis'; (2) underlying picker "
     "(re-use existing chain underlying selector); (3) Submit button → fetch /propose → render "
     "modal with payoff chart (Chart.js, reuse existing strategy payoff renderer), rationale "
     "paragraph, warnings list, blocks list (in red), and a single 'Execute' button wired to "
     "/execute. Disable Execute if blocks exist."),

    ("confirm-gate", "confirm-gate: Epic CONFIRM exit validation",
     "test", "high", GATE),

    # ═══════════════════════════════════════════════════════════
    # Epic COACH — position monitoring + recommendations
    # ═══════════════════════════════════════════════════════════
    ("coach-01", "coach-01: migration — ad_options_coach_events (append-only)",
     "build", "high",
     "Create tools/db/migrations/017_options_coach_events/up.py + down.py. Table "
     "ad_options_coach_events: id TEXT PK, position_id TEXT, user_id TEXT, tenant_id TEXT, "
     "event_type TEXT CHECK(event_type IN ('profit_target', 'loss_threshold', 'dte_warning', "
     "'dte_roll_window', 'iv_crush', 'greeks_drift')), severity TEXT (info/warn/critical), "
     "summary TEXT, recommendation TEXT, position_snapshot_json TEXT, created_at TEXT. "
     "Index on (user_id, created_at DESC) and (position_id, created_at DESC). "
     "Register in .claude/hooks/pre_tool_use.py APPEND_ONLY_TABLES + tests/conftest.py "
     "MINIMAL_ICDEV_SCHEMA. Use get_connection() — never sqlite3.connect() "
     "(memory feedback_always_use_get_connection.md). Derive CHECK constraint from a Python "
     "COACH_EVENT_TYPES tuple — don't hard-code twice (CLAUDE.md guardrail)."),

    ("coach-02", "coach-02: tools/trading/options/coach_db.py CRUD",
     "build", "high",
     "Create coach_db.py with: insert_event(position_id, user_id, tenant_id, event_type, severity, "
     "summary, recommendation, position_snapshot_json) -> str (event_id); list_events(user_id, "
     "limit=50, since?) -> list[dict]; latest_event_per_position(user_id) -> dict[position_id→event]; "
     "get_event(event_id) -> dict. Per memory feedback_db_layer_in_plans.md: CRUD is a separate "
     "task from migration, never bundled."),

    ("coach-03", "coach-03: tools/trading/options/coach_engine.py (rules only)",
     "build", "critical",
     "Public: scan_user(user_id) -> list[events]. Loads args/options_coach_thresholds.yaml "
     "(create with defaults: profit_target_pct: 50, loss_threshold_mult: 2.0, "
     "dte_warning_days: 7, dte_roll_window_days: 21, iv_crush_pct: 20). "
     "For each open option position: compute P&L vs max_profit/max_loss, DTE, and IV delta "
     "since open (needs a position-state snapshot — add ad_option_positions.opened_iv column if "
     "missing). Emit event when thresholds cross. NO LLM. Writes via coach_db.insert_event. "
     "Deduplicate: don't re-emit the same event_type for the same position within 24h unless "
     "severity escalates."),

    ("coach-04", "coach-04: tools/trading/options/coach_llm.py (explanation only)",
     "build", "high",
     "Public: explain_event(event: dict, position_snapshot: dict) -> str. "
     "Build a grounded prompt containing ONLY: event_type, severity, current P&L, DTE, "
     "Greeks (delta/theta/vega/gamma), underlying price, strategy legs. Prompt MUST forbid "
     "new numbers and require a ≤3-sentence actionable recommendation (close/adjust/hold). "
     "On LLM unavailable, return a rule-generated template string. UPDATE the event row's "
     "recommendation column after call (single UPDATE on a specific row is allowed — the "
     "append-only rule is about not deleting history; in-place enrichment of an existing row "
     "is fine if that column starts NULL). Alternatively, store in a sibling "
     "ad_options_coach_recommendations table; confirm design in PR review."),

    ("coach-05", "coach-05: daemon reflex + /portfolio + /options UI card",
     "build", "high",
     "(a) Register 'options_coach' reflex in tools/trading/market_intel/daemon.py: every 10m, "
     "for every user with ≥ 1 open option position, run coach_engine.scan_user + "
     "coach_llm.explain_event for any new events. Add to args/trading_daemon_config.yaml. "
     "(b) Add a card to /portfolio showing the 5 most-recent coach events (summary + severity "
     "badge). (c) In /options position panel, inline the latest event per position. Click-to-"
     "expand full recommendation text."),

    ("coach-gate", "coach-gate: Epic COACH exit validation",
     "test", "high", GATE),

    # ═══════════════════════════════════════════════════════════
    # Epic WRAP — registration + docs + E2E
    # ═══════════════════════════════════════════════════════════
    ("wrap-01", "wrap-01: manifest + 8-point registration checklist",
     "chore", "high",
     "Update tools/manifest/fathomdesk-trading-engine.md with entries for: intent_parser, "
     "strategy_selector, strike_picker, proposal_builder, preflight, coach_engine, coach_llm, "
     "coach_db. Walk CLAUDE.md 8-point new-tool checklist for every module. Verify OpenAPI "
     "has /api/options/ai-assist/propose + /execute routes."),

    ("wrap-02", "wrap-02: feature doc + screenshots",
     "chore", "high",
     "Write docs/features/phase-7.6-ai-options-assist.md. Cover: (a) the hybrid design "
     "rationale (LLM for interpretation, rules for selection); (b) worked example with "
     "screenshots of the AI Assist tab → proposal modal → executed position → coach event; "
     "(c) explicit safety boundaries (no auto-close, no auto-adjust). Screenshots to "
     "playwright/screenshots/options_ai_*.png per CLAUDE.md."),

    ("wrap-03", "wrap-03: coherence + companion sync",
     "chore", "high",
     "Run: python tools/workflow/coherence_checker.py --all --fix --gate (target 17/17 or "
     "whatever current pass-count is after the phase). Then: python tools/dx/companion.py "
     "--sync --write --json. Capture both JSON outputs in the task completion note."),

    ("wrap-04", "wrap-04: Selenium E2E test",
     "test", "high",
     "Create tests/e2e/fathomdesk/test_options_ai_assist.py (Selenium headless — memory "
     "e2e_selenium.md; no Playwright). Flow: log in → /options → click AI Assist tab → "
     "submit 'Bullish AAPL earnings, limited risk' → assert proposal modal renders with "
     "payoff chart, rationale, warnings list → click Execute → assert /portfolio shows a "
     "new multileg group. Screenshots to playwright/screenshots/options_ai_e2e_*.png."),

    ("wrap-05", "wrap-05: backlog + memory update",
     "chore", "medium",
     "Append a Phase 7.6 entry to docs/fathomdesk-backlog.md marking it DONE with ship date. "
     "Update memory/project_fathomdesk_phase7plus.md to include the 7.6 bullet. Update memory "
     "MEMORY.md index line if the memory file grew new sections."),
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
                "SELECT id, status FROM kanban_tasks WHERE id = ?", (tid,),
            ).fetchone()
            if existing:
                skipped.append(tid)
                parent = tid
                continue
            cur.execute(
                "INSERT INTO kanban_tasks "
                "(id, title, description, task_type, priority, status, "
                "depends_on_task_id, dispatch_source, created_at, updated_at, scheduled_at) "
                "VALUES (?, ?, ?, ?, ?, 'scheduled', ?, 'manual_plan', ?, ?, ?)",
                (tid, title, description, task_type, priority, parent, now, now, now),
            )
            inserted.append(tid)
            parent = tid
        conn.commit()
    finally:
        conn.close()
    return {
        "project": "fathomdesk-7-6",
        "prefix": PREFIX,
        "inserted": inserted,
        "skipped_already_present": skipped,
        "total_subtasks": len(SUBTASKS),
    }


if __name__ == "__main__":
    result = enqueue()
    print(json.dumps(result, indent=2))
    sys.exit(0 if result["inserted"] or result["skipped_already_present"] else 1)
