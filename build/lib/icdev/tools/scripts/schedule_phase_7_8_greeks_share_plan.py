# CUI // SP-CTI
"""Idempotent enqueue for FathomDesk Phase 7.8 — Greeks + Share.

Brief: docs/briefs/phase-7.8-greeks-share.md
Project: args/projects.yaml → fathomdesk-7-8, prefix=ad78-
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone

from tools.dashboard.config import DB_PATH
from tools.db.storage import get_connection

PREFIX = "ad78-"

GATE = (
    "PHASE-EXIT GATE (all 5 must pass before next phase unblocks): "
    "(1) python tools/code_intelligence/codelens.py --all --json; "
    "(2) python tools/workflow/coherence_checker.py --all --fix --gate; "
    "(3) python tools/testing/e2e_full_dashboard.py; "
    "(4) pytest tests/ -x --timeout=120 --ignore=tests/e2e_selenium; "
    "(5) python tools/dx/companion.py --sync --write --json."
)

SUBTASKS: list[tuple[str, str, str, str, str]] = [

    # ═══════════ PRICING ═══════════
    ("pricing-01", "pricing-01: tools/trading/options/pricing.py — Black-Scholes",
     "build", "critical",
     "Create pricing.py. Public: bs_price(option_type, S, K, T, r, sigma, q=0), "
     "bs_greeks(option_type, S, K, T, r, sigma, q=0) returning "
     "{delta, gamma, theta, vega, rho}. Closed-form via math.erf for N(d). "
     "Handle T<=0 edge (returns intrinsic for price, {delta:+-1 or 0, others:0}). "
     "Handle sigma<=0 by returning intrinsic. Theta expressed per-day (annual/365), "
     "vega and rho expressed per-1% (divided by 100). Pure Python, no numpy."),

    ("pricing-02", "pricing-02: tests/test_options_pricing.py",
     "test", "high",
     "Add tests: (a) put-call parity: C - P = S·exp(-qT) - K·exp(-rT) within 1e-4; "
     "(b) ATM price symmetry when r=q=0: call ≈ put within 1e-6; "
     "(c) T->0 edge: price equals intrinsic; "
     "(d) monotone: call price grows with S, put declines with S; "
     "(e) textbook sample: S=100, K=100, T=0.5, r=0.05, sigma=0.25, no-div → C≈7.47 ± 0.02; "
     "(f) greeks sanity: call delta in [0,1], put delta in [-1,0], gamma>=0, vega>=0; "
     "(g) graceful handling of edge cases (T=0, sigma=0, zero spot)."),

    ("pricing-03", "pricing-03: compute_payoff_at_time in probability.py",
     "build", "critical",
     "Add compute_payoff_at_time(legs, spot_range, dte_remaining_days, iv_annual_pct, "
     "r_annual=0.04) -> {x: [...], y: [...]}. For each spot in spot_range, for each "
     "leg: price it with bs_price at the remaining time, sign it by action (buy=+, "
     "sell=-), subtract opening premium, sum across legs, multiply by 100 * qty. "
     "When dte_remaining_days == 0, falls through to compute_payoff intrinsic (sanity: "
     "curves should match at T=0 within rounding). Returns same {x, y} shape as "
     "compute_payoff so the chart swaps frames seamlessly."),

    ("pricing-04", "pricing-04: proposal_builder returns payoff_frames",
     "build", "high",
     "In proposal_builder.build_proposal primary path: compute payoff_at_time at 4 "
     "extra points ([+1d, 25%-of-DTE, 50%-of-DTE, 75%-of-DTE]) plus the existing "
     "expiry curve. Attach proposal['payoff_frames'] = [{days_from_now: int, "
     "dte_remaining: int, payoff: {x, y, max_profit, max_loss}}]. 5 frames total. "
     "Use the same spot_range as the main payoff (pull x from payoff). IMPORTANT: "
     "alternates from build_for_strategy can SKIP frames (keep payload small)."),

    ("pricing-05", "pricing-05: time-T slider on AI Assist payoff chart",
     "build", "high",
     "In options.html aiRenderProposal: add a slider above the chart labeled 'Days "
     "from now: <N>' with ticks at each frame in proposal.payoff_frames. On drag, "
     "replace chart dataset 'P&L at expiry' data array with the selected frame's "
     "payoff.y. Tooltip shows the frame's dte_remaining. When slider is at max "
     "position (expiry), render the original payoff.y. Keep the price-cone bands "
     "static (they represent terminal-price distribution, not time-T)."),

    ("pricing-gate", "pricing-gate: Epic PRICING exit validation",
     "test", "high", GATE),

    # ═══════════ PORTFOLIO ═══════════
    ("portfolio-01", "portfolio-01: tools/trading/options/portfolio_greeks.py",
     "build", "high",
     "Public: compute_portfolio_greeks(user_id) -> dict. Query "
     "ad_sandbox_option_positions WHERE user_id = ? AND qty != 0. For each row, "
     "parse last_greeks_json (may be stale); multiply Δ/Γ/Θ/ν by (100 * qty) "
     "signed by position direction (long=+, short=-, already baked into qty sign). "
     "Sum across positions. Return {net_delta, net_gamma, net_theta, net_vega, "
     "position_count, stale_count (rows with null greeks_json), as_of}. Use "
     "get_connection() + sql_placeholder() — SQLite+PG portable."),

    ("portfolio-02", "portfolio-02: GET /api/options/portfolio/greeks route",
     "build", "high",
     "Add Flask route in app.py. Auth required. Delegates to portfolio_greeks."
     "compute_portfolio_greeks(g.current_user['id']). Returns the dict as JSON. "
     "Add to OpenAPI if that's mechanized."),

    ("portfolio-03", "portfolio-03: /portfolio card — Portfolio Greeks",
     "build", "high",
     "Add a card to templates/portfolio.html below the Options Coach card (or "
     "above Holdings). 4 big numbers: Net Δ (share equivalents), Net Γ, Net Θ "
     "(dollars/day), Net Vega (dollars per 1% IV move). Severity coloring: "
     "|delta| > 500 yellow; theta < -50 yellow, < -200 red. Tooltip explains each "
     "greek in one line. Auto-refresh every 30s. Auto-hides when position_count==0."),

    ("portfolio-gate", "portfolio-gate: Epic PORTFOLIO exit validation",
     "test", "high", GATE),

    # ═══════════ SHARE ═══════════
    ("share-01", "share-01: tools/trading/options/share.py",
     "build", "medium",
     "Public: encode_proposal(proposal, intent) -> str (base64), "
     "decode_proposal(token) -> {proposal, intent} or None. Input payload: "
     "{underlying, strategy_id, legs:[{action, option_type, strike, expiry, qty}], "
     "intent:{direction,horizon,iv_view,risk_cap}}. NO user_id, NO secrets. "
     "Uses base64.urlsafe_b64encode(json.dumps(payload).encode()). Length cap at "
     "~2kB; reject oversize on decode."),

    ("share-02", "share-02: Share button on proposal card",
     "build", "medium",
     "In options.html aiRenderProposal, add a 🔗 Share button next to Execute. "
     "On click: POST /api/options/ai-assist/share with proposal → returns {url}. "
     "(Backend calls share.encode_proposal.) Copy URL to clipboard via "
     "navigator.clipboard.writeText; show toast 'copied'. Fallback: select + "
     "prompt() if clipboard API unavailable."),

    ("share-03", "share-03: auto-load from ?aiproposal=BASE64 URL param",
     "build", "medium",
     "On /options page load in the AI Assist tab's script block: if "
     "URLSearchParams contains 'aiproposal', POST to /api/options/ai-assist/share/decode "
     "(new endpoint — implement in share-02's backend). Fill the intent textarea + "
     "underlying with decoded values, auto-click Propose. The server re-fetches "
     "the chain + reruns preflight so nothing the URL payload claims is trusted."),

    ("share-gate", "share-gate: Epic SHARE exit validation",
     "test", "high", GATE),

    # ═══════════ WRAP ═══════════
    ("wrap-01", "wrap-01: manifest + coherence + companion",
     "chore", "high",
     "Add pricing.py + portfolio_greeks.py + share.py to tools/manifest/"
     "fathomdesk-trading-engine.md. Run coherence --all --fix --gate (target 17/17). "
     "Run companion.py --sync --write --json (10 platforms)."),

    ("wrap-02", "wrap-02: feature doc",
     "chore", "high",
     "Write docs/features/phase-7.8-greeks-share.md. Cover: (a) BS model "
     "assumptions (risk-free 4%, q=0), (b) time-T slider semantics + limits, "
     "(c) portfolio greeks severity thresholds, (d) share URL encoding + why "
     "server re-verifies payload, (e) screenshots."),

    ("wrap-03", "wrap-03: Selenium E2E",
     "test", "high",
     "Create tests/e2e_selenium/test_ad78_greeks_share.py. Flow: (a) /options "
     "AI Assist → submit intent → assert time-slider renders + chart updates on "
     "drag; (b) /portfolio → assert Portfolio Greeks card exists (may auto-hide "
     "if no positions; test passes either way); (c) click Share on proposal → "
     "assert URL written to clipboard; then visit that URL in a new tab → assert "
     "proposal auto-loads."),

    ("wrap-04", "wrap-04: backlog + memory",
     "chore", "medium",
     "Append Phase 7.8 entry to docs/fathomdesk-backlog.md. Update "
     "memory/project_fathomdesk_phase7plus.md with the 7.8 bullet."),
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
        "project": "fathomdesk-7-8",
        "prefix": PREFIX,
        "inserted": inserted,
        "skipped_already_present": skipped,
        "total_subtasks": len(SUBTASKS),
    }


if __name__ == "__main__":
    result = enqueue()
    print(json.dumps(result, indent=2))
    sys.exit(0 if result["inserted"] or result["skipped_already_present"] else 1)
