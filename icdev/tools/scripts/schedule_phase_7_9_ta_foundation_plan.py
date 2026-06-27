# CUI // SP-CTI
"""Idempotent enqueue for FathomDesk Phase 7.9 — TA Foundation.

Brief: docs/briefs/phase-7.9-ta-foundation.md
Project: args/projects.yaml → fathomdesk-7-9, prefix=ad79-

No external predecessor — ships first of the 3-phase chain (7.9 → 7.10
and 7.9 → 7.11 both unblock when this phase's last task completes).
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone

from tools.dashboard.config import DB_PATH
from tools.db.storage import get_connection

PREFIX = "ad79-"
EXTERNAL_PREDECESSOR: str | None = None  # ships first

GATE = (
    "PHASE-EXIT GATE (all 5 must pass before next phase unblocks): "
    "(1) python tools/code_intelligence/codelens.py --all --json; "
    "(2) python tools/workflow/coherence_checker.py --all --fix --gate; "
    "(3) python tools/testing/e2e_full_dashboard.py; "
    "(4) pytest tests/ -x --timeout=120 --ignore=tests/e2e_selenium; "
    "(5) python tools/dx/companion.py --sync --write --json."
)

SUBTASKS: list[tuple[str, str, str, str, str]] = [

    # ═══════════ PRIMITIVES ═══════════
    ("primitives-01", "primitives-01: tools/trading/ta/swings.py",
     "build", "critical",
     "Create tools/trading/ta/swings.py. Public: find_swings(bars, "
     "threshold_pct=1.5) -> [{index, time, price, kind}] with kind in {'high','low'}. "
     "Use percentage-threshold method: a new high is confirmed when price retraces "
     "by >=threshold_pct from the most recent peak. Alternating kinds; never two "
     "consecutive highs or lows. Pure math, no network. Also create args/ta_config.yaml "
     "with defaults: swing_threshold_pct: 1.5, vp_bucket_count: 40, "
     "sr_proximity_pct: 0.5. Hot-reloadable."),

    ("primitives-02", "primitives-02: tools/trading/ta/volume_profile.py",
     "build", "critical",
     "Create volume_profile.py. Public: compute_volume_profile(bars, "
     "bucket_count=40) -> {buckets:[{price_low, price_high, volume}], poc, "
     "value_area:{low, high}, hvns:[{price, volume}], lvns:[{price, volume}]}. "
     "Bucket range spans from min(low) to max(high) across the window. Each bar's "
     "volume is distributed uniformly across the buckets it spans. HVN = bucket in "
     "top-20% by volume; LVN = bottom-20%. POC = single highest-volume bucket. "
     "Value area = the contiguous set of buckets around POC summing to 70% of total."),

    ("primitives-03", "primitives-03: tools/trading/ta/sr.py",
     "build", "critical",
     "Create sr.py. Public: find_support_resistance(bars, swings=None, "
     "volume_profile=None) -> [{price, strength, touch_count, source}]. "
     "If swings/VP not passed, compute them via the primitives modules. "
     "Cluster swing-pivot prices by proximity (ta_config.sr_proximity_pct, default "
     "0.5%); merge clusters whose mean price is within the tolerance of an HVN. "
     "strength = touch_count + (3 if overlaps an HVN else 0). Sort by strength "
     "descending. Cap returned list at top 10 levels."),

    ("primitives-04", "primitives-04: pytest for TA primitives",
     "test", "high",
     "Create tests/test_ta_primitives.py. Tests: (a) swings alternate kinds "
     "(every high follows a low and vice versa); (b) VP volume sum equals input "
     "total within 1e-6; (c) VP value area contains >= 65% but < 75% of volume "
     "(bracketing the 70% target with bucket discretization tolerance); (d) S/R "
     "cluster prices are within sr_proximity_pct of the mean touch price; (e) "
     "handcrafted bar fixture with 3 clear swing highs → S/R finds all 3."),

    ("primitives-gate", "primitives-gate: Epic PRIMITIVES exit validation",
     "test", "high", GATE),

    # ═══════════ PATTERNS ═══════════
    ("patterns-01", "patterns-01: tools/trading/ta/patterns/double.py",
     "build", "critical",
     "Create patterns/double.py. Public: detect_double(bars, swings=None) -> "
     "[{pattern, peaks, neckline, confirmed, breakout_bar, confidence}]. "
     "pattern in {'double_top','double_bottom'}. Rule: find two consecutive same-"
     "kind swings (both highs or both lows) within 2% of each other, separated by "
     "an opposite-kind swing at least 3% away (peak_separation_pct tunable). "
     "neckline = that intermediate swing's price. confirmed=True when any later "
     "bar closes past the neckline. Also create patterns/__init__.py as empty."),

    ("patterns-02", "patterns-02: tools/trading/ta/patterns/triple.py",
     "build", "high",
     "Mirror double.py but requires THREE consecutive same-kind swings within "
     "tolerance. Public: detect_triple(bars, swings=None) -> [...]. Same output "
     "shape. Triple patterns are rarer + higher-conviction."),

    ("patterns-03", "patterns-03: tools/trading/ta/patterns/wedge.py",
     "build", "high",
     "Create wedge.py. Public: detect_wedge(bars, swings=None, min_swings=4) -> "
     "[{pattern, upper_line, lower_line, apex_bar, confidence}]. pattern in "
     "{'rising_wedge','falling_wedge'}. Fit linear regression lines through the "
     "last N swing-highs and swing-lows separately. Rising wedge: both slopes "
     "positive AND upper_slope < lower_slope (converging up). Falling wedge: both "
     "slopes negative AND upper_slope > lower_slope (converging down). Lines "
     "rendered as {slope, intercept, first_bar, last_bar}."),

    ("patterns-04", "patterns-04: patterns orchestrator + pytest",
     "test", "high",
     "In patterns/__init__.py: detect_patterns(bars) -> [...] calls double/triple/"
     "wedge and dedupes overlapping results (same pattern on overlapping bar "
     "ranges). Create tests/test_ta_patterns.py with synthetic fixtures: "
     "(a) V-shape → double bottom detected; (b) W-shape → double bottom with "
     "neckline confirmation; (c) 3-touch resistance → triple top; (d) rising "
     "wedge fixture → detected with correct slope signs. Edge: empty bars returns []."),

    ("patterns-gate", "patterns-gate: Epic PATTERNS exit validation",
     "test", "high", GATE),

    # ═══════════ UI ═══════════
    ("ui-01", "ui-01: GET /api/ta/chart/<ticker>",
     "build", "high",
     "In tools/trading/dashboard/app.py, add /api/ta/chart/<ticker> "
     "?timeframe=1D&limit=120. Returns {bars, volume_profile, sr_levels, patterns, "
     "swings}. Reuses fetch_bars from market_data.py. Auth required. Cache 60s "
     "in-process (cheap mutex dict keyed on ticker+timeframe+limit). Include "
     "`as_of` timestamp in response so UI can display staleness."),

    ("ui-02", "ui-02: candlestick chart on /analysis",
     "build", "high",
     "In templates/analysis.html, add a new section 'Price Chart'. Render bars "
     "using Chart.js. Check if chartjs-chart-financial plugin is bundled in "
     "static/vendor/chartjs; if yes use candlestick type. Else render custom: "
     "bar chart with two datasets — thin 'range' line (high-low) and thick 'body' "
     "bar (open-close colored green/red). 1D timeframe, default 120 bars. Pull "
     "data from /api/ta/chart/<ticker>."),

    ("ui-03", "ui-03: volume profile overlay",
     "build", "high",
     "Overlay VP as a horizontal-histogram layer on the right 15% of the chart "
     "area. Each bucket rendered as a horizontal bar whose width is proportional "
     "to volume. POC bucket highlighted in contrasting color. HVN prices extended "
     "as faint horizontal lines across the whole chart (opacity 0.15). Value-area "
     "band shown as subtle shaded region between VA low and VA high."),

    ("ui-04", "ui-04: S/R lines + pattern markers",
     "build", "high",
     "Overlay S/R levels as horizontal lines; thickness or opacity proportional "
     "to strength. For each pattern: draw a pinned marker at its breakout_bar "
     "with the pattern label (e.g. '🔻 Double Top'). For wedges: draw the two "
     "trend lines. Click on a pattern marker → modal showing pattern details, "
     "confidence, and affected price range."),

    ("ui-gate", "ui-gate: Epic UI exit validation",
     "test", "high", GATE),

    # ═══════════ WRAP ═══════════
    ("wrap-01", "wrap-01: manifest + coherence + companion",
     "chore", "high",
     "Add ta/swings.py, ta/volume_profile.py, ta/sr.py, ta/patterns/{double,triple,"
     "wedge}.py to tools/manifest/fathomdesk-trading-engine.md. coherence --all --fix "
     "--gate → 17/17. companion.py --sync --write."),

    ("wrap-02", "wrap-02: feature doc",
     "chore", "high",
     "Write docs/features/phase-7.9-ta-foundation.md. Cover: (a) swing-pivot "
     "method rationale, (b) VP bucket semantics and POC/VA definitions, (c) S/R "
     "strength scoring, (d) each pattern detector's geometry, (e) chart layout "
     "with screenshots (playwright/screenshots/ta_*.png)."),

    ("wrap-03", "wrap-03: Selenium E2E",
     "test", "high",
     "Create tests/e2e_selenium/test_ad79_ta_foundation.py. Flow: login → "
     "/analysis?ticker=AAPL → assert candle chart renders, volume profile on "
     "right, at least one S/R line visible, swings marked, pattern summary "
     "visible. Screenshots to playwright/screenshots/ta_*.png."),

    ("wrap-04", "wrap-04: backlog + memory",
     "chore", "medium",
     "Append Phase 7.9 entry to docs/fathomdesk-backlog.md. Update "
     "memory/project_fathomdesk_phase7plus.md with 7.9 bullet."),
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
        "project": "fathomdesk-7-9",
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
