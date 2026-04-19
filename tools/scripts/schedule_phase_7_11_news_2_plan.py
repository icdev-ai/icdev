# CUI // SP-CTI
"""Idempotent enqueue for FathomDesk Phase 7.11 — News 2.0.

Brief: docs/briefs/phase-7.11-news-2.md
Project: args/projects.yaml → fathomdesk-7-11, prefix=ad711-

Blocked on 7.9 (uses chart layer for news-on-chart overlays). Runs in
PARALLEL with 7.10 — they share 7.9 as predecessor but are independent
of each other.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone

from tools.dashboard.config import DB_PATH
from tools.db.storage import get_connection

PREFIX = "ad711-"
EXTERNAL_PREDECESSOR = "ad79-wrap-04"   # 7.11 starts after 7.9 completes

GATE = (
    "PHASE-EXIT GATE (all 5 must pass before next phase unblocks): "
    "(1) python tools/code_intelligence/codelens.py --all --json; "
    "(2) python tools/workflow/coherence_checker.py --all --fix --gate; "
    "(3) python tools/testing/e2e_full_dashboard.py; "
    "(4) pytest tests/ -x --timeout=120 --ignore=tests/e2e_selenium; "
    "(5) python tools/dx/companion.py --sync --write --json."
)

SUBTASKS: list[tuple[str, str, str, str, str]] = [

    # ═══════════ CAT — categorized view + chart overlay ═══════════
    ("cat-01", "cat-01: /news refactor to category-tab layout",
     "build", "high",
     "Refactor templates/news.html. Replace filter-chip row with a tab row: "
     "All, Macro, Geopolitical, Earnings, Regulatory, Sector, Corporate. Each "
     "tab is a pane containing (a) a category summary card (ad711-cat-02) at "
     "the top, (b) category-filtered news items list below. 'All' tab remains "
     "firehose fallback. Tab switching is client-side (hide/show panes). "
     "Preserve existing auto-refresh + search + export CSV."),

    ("cat-02", "cat-02: per-category summary card",
     "build", "high",
     "Each category tab shows a summary card header: 7d sentiment sparkline "
     "(net bullish − bearish count per day from ad_news_items), total items in "
     "last 24h, active-pattern chip count (pattern count fills after 7.11-pattern-04 "
     "lands; show placeholder '—' until then). Sparkline renders via Chart.js."),

    ("cat-03", "cat-03: Show-on-chart link per news item",
     "build", "medium",
     "Each item row gets a '📈 Show on chart' button. Click → navigate to "
     "/analysis?ticker=<first_mentioned>&highlight=<news_id>. On /analysis "
     "page (from 7.9), when highlight param present: fetch news item via "
     "GET /api/news/<id>, find publish time, draw a vertical annotation line "
     "on the chart at that time with the headline as tooltip. Uses chartjs-"
     "plugin-annotation if bundled; else render via canvas overlay."),

    ("cat-gate", "cat-gate: Epic CAT exit validation",
     "test", "high", GATE),

    # ═══════════ PATTERN — analyzer + append-only table ═══════════
    ("pattern-01", "pattern-01: migration 023_ad_news_patterns",
     "build", "high",
     "Create migration 023_ad_news_patterns. Columns: id TEXT PK, pattern_type "
     "TEXT, category TEXT, severity TEXT CHECK(severity IN ('info','warn',"
     "'critical')), evidence_item_ids TEXT (JSON array of news_id), window_start "
     "TEXT, window_end TEXT, recommendation TEXT, created_at TEXT NOT NULL. "
     "Indexes on (category, created_at DESC) and (severity, created_at DESC). "
     "Register in APPEND_ONLY_TABLES + conftest MINIMAL_ICDEV_SCHEMA. "
     "Derive CHECK severity enum from a Python NEWS_PATTERN_SEVERITIES tuple."),

    ("pattern-02", "pattern-02: tools/trading/news/pattern_db.py CRUD",
     "build", "high",
     "CRUD helpers: insert_pattern(pattern_type, category, severity, "
     "evidence_item_ids, window_start, window_end, recommendation) -> id; "
     "list_patterns(category=None, since=None, limit=50) -> list[dict]; "
     "get_pattern(id) -> dict; exists_recent(pattern_type, category, "
     "window_hours=6) -> bool (dedupe guard). Use get_connection() + "
     "sql_placeholder()."),

    ("pattern-03", "pattern-03: pattern_analyzer.py — per-category detectors",
     "build", "critical",
     "Create tools/trading/news/pattern_analyzer.py. Public: analyze_all() -> "
     "list[dict]. Per-category detectors over last 24h of ad_news_items: "
     "(a) macro: hawkish/dovish skew — when >=70% of items in 24h share a "
     "direction, emit regime_shift pattern with severity derived from count. "
     "(b) earnings: >= 5 items with bullish direction in 48h → broad_tailwind. "
     "(c) geopolitical: item spike > 2× 7d baseline AND bearish skew → risk_off. "
     "(d) regulatory: cluster >= 3 items same sector (derive via mentioned_"
     "tickers → universe sector) + bearish → crackdown (critical severity). "
     "(e) sector: rolling sentiment per sector → rotation flags. Each detector "
     "returns a pattern dict; orchestrator dedupes via pattern_db.exists_recent."),

    ("pattern-04", "pattern-04: /api/news/patterns + UI + pytest",
     "build", "high",
     "(a) Add GET /api/news/patterns?category=X&since=ISO endpoint. (b) Render "
     "active patterns inside each tab's summary card as severity-chip + "
     "recommendation text. (c) pytest in tests/test_news_pattern_analyzer.py "
     "with synthetic corpus: hawkish cluster → regime_shift emitted; dedupe "
     "guard prevents re-emission within cooldown; regulatory 4-item cluster in "
     "Biotech → crackdown(critical). (d) Add pattern_count to cat-02 summary."),

    ("pattern-gate", "pattern-gate: Epic PATTERN exit validation",
     "test", "high", GATE),

    # ═══════════ GENESIS — autonomous actions ═══════════
    ("genesis-01", "genesis-01: tools/genesis/reflexes/fathomdesk_news_patterns.py",
     "build", "high",
     "Create Genesis reflex module. Contract: def run(config, trust) -> "
     "{success, metric_value, details}. Hourly cadence. Calls pattern_analyzer."
     "analyze_all(), persists new patterns via pattern_db, counts new emissions. "
     "Register in tools/genesis/daemon.py reflex list. Fail gracefully — never "
     "raises on malformed upstream data."),

    ("genesis-02", "genesis-02: autonomous action wiring",
     "build", "high",
     "Extend the reflex: on each NEW pattern, (a) write to ad_alerts (existing "
     "alerts engine) with matching severity. (b) Feed into Oracle regime_lens "
     "evidence for macro/geo/regulatory patterns. (c) For severity='critical' "
     "AND env ICDEV_GENESIS_AUTOSPAWN=true: auto-spawn a matching scenario "
     "via scenario_engine.run_scenario (scenario_key mapping table in the "
     "reflex module), auto-publish a Pulse post via existing Pulse engine. "
     "NEVER calls any order placement API. All actions audited in existing "
     "append-only tables."),

    ("genesis-03", "genesis-03: register in Genesis config + env flag",
     "build", "medium",
     "Add the reflex name to Genesis daemon reflex list. Create / update "
     "args/genesis_reflexes.yaml (or equivalent) with schedule: hourly, "
     "severity thresholds, and ICDEV_GENESIS_AUTOSPAWN default=false. "
     "Document in the module docstring that autospawn is DISABLED by default "
     "for safe rollout."),

    ("genesis-gate", "genesis-gate: Epic GENESIS exit validation",
     "test", "high", GATE),

    # ═══════════ WRAP ═══════════
    ("wrap-01", "wrap-01: manifest + coherence + companion",
     "chore", "high",
     "Add news/pattern_analyzer.py + pattern_db.py + genesis/reflexes/"
     "fathomdesk_news_patterns.py to manifest. coherence --all --fix --gate "
     "→ 17/17. companion.py --sync --write."),

    ("wrap-02", "wrap-02: feature doc + Selenium E2E",
     "chore", "high",
     "Write docs/features/phase-7.11-news-2.md. Create tests/e2e_selenium/"
     "test_ad711_news_2.py: load /news → assert tab row with 7 tabs; click "
     "Macro tab → assert summary card + items; click 'Show on chart' on an "
     "item → redirects to /analysis with vertical annotation."),

    ("wrap-03", "wrap-03: backlog + memory",
     "chore", "medium",
     "Append 7.11 entry to docs/fathomdesk-backlog.md. Update memory with "
     "note on Genesis autospawn safety flag + the pattern-analyzer categories."),
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
        "project": "fathomdesk-7-11",
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
