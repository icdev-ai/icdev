# CUI // SP-CTI
"""AI-ify Signal Ingester (Option A — Innovation → AI-ify Bridge).

Queries innovation_signals for entries relevant to AI augmentation patterns
and cross-references them against aiify_opportunities to create bridge records
in aiify_innovation_bridge.

Run nightly (or on-demand):
    python tools/aiify/signal_ingester.py --ingest --json
    python tools/aiify/signal_ingester.py --report --json
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone

from tools.aiify.db.init_db import get_connection as _aiify_conn

# Innovation signal categories that map to AI-ify patterns
_CATEGORY_TO_PATTERNS: dict[str, list[str]] = {
    "ai_tooling":                    ["hardcoded_threshold", "nested_conditionals"],
    "agentic":                       ["scheduled_cron", "string_template_rendering"],
    "external_framework_analysis":   ["keyword_list_search", "regex_user_input"],
    "aiify_opportunity":   ["large_rule_table", "db_render_notify_chain"],
}

_BRIDGE_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS aiify_innovation_bridge (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id       TEXT NOT NULL,
    opportunity_id  INTEGER NOT NULL,
    pattern_type    TEXT NOT NULL,
    match_reason    TEXT,
    innovation_score REAL,
    bridged_at      TEXT DEFAULT CURRENT_TIMESTAMP
)"""

_BRIDGE_TABLE_DDL_PG = """
CREATE TABLE IF NOT EXISTS aiify_innovation_bridge (
    id              SERIAL PRIMARY KEY,
    signal_id       TEXT NOT NULL,
    opportunity_id  INTEGER NOT NULL,
    pattern_type    TEXT NOT NULL,
    match_reason    TEXT,
    innovation_score REAL,
    bridged_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)"""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ensure_bridge_table(aiify: object) -> None:
    """Create aiify_innovation_bridge if it doesn't exist."""
    import os
    backend = os.environ.get(
        "AIIFY_STORAGE_BACKEND",
        os.environ.get("ICDEV_CANVAS_STORAGE_BACKEND", "postgresql"),
    ).lower()
    ddl = _BRIDGE_TABLE_DDL_PG if backend == "postgresql" else _BRIDGE_TABLE_DDL
    try:
        aiify.execute(ddl)
        aiify.commit()
    except Exception:
        pass


def ingest(min_innovation_score: float = 0.60) -> dict:
    """Pull innovation_signals and bridge to matching aiify_opportunities.

    Returns {"bridged": N, "skipped": M, "errors": [...]}
    """
    # Load innovation signals from main ICDEV DB
    signals: list[dict] = []
    try:
        from tools.db.storage import get_connection as _icdev_conn
        icdev = _icdev_conn()
        try:
            rows = icdev.execute(
                "SELECT id, category, title, description, innovation_score "
                "FROM innovation_signals "
                "WHERE category IN ('ai_tooling','agentic','external_framework_analysis','aiify_opportunity') "
                "AND (innovation_score IS NULL OR innovation_score >= ?) "
                "ORDER BY innovation_score DESC LIMIT 200",
                (min_innovation_score,),
            ).fetchall()
            signals = [dict(r) for r in rows]
        finally:
            icdev.close()
    except Exception as exc:
        return {"bridged": 0, "skipped": 0, "errors": [str(exc)]}

    if not signals:
        return {"bridged": 0, "skipped": 0, "errors": []}

    # Load AI-ify opportunities
    aiify = _aiify_conn()
    _ensure_bridge_table(aiify)
    try:
        rows = aiify.execute(
            "SELECT opportunity_id, pattern_type, module_path FROM aiify_opportunities"
        ).fetchall()
        opps = [dict(r) for r in rows]
    finally:
        aiify.close()

    bridged = skipped = 0
    errors: list[str] = []

    aiify = _aiify_conn()
    try:
        for sig in signals:
            sig_id = sig["id"]
            category = sig.get("category", "")
            matched_patterns = _CATEGORY_TO_PATTERNS.get(category, [])
            if not matched_patterns:
                continue

            for opp in opps:
                if opp["pattern_type"] not in matched_patterns:
                    continue
                opp_id = opp["opportunity_id"]

                # Skip if bridge record already exists
                existing = aiify.execute(
                    "SELECT id FROM aiify_innovation_bridge WHERE signal_id = ? AND opportunity_id = ?",
                    (sig_id, opp_id),
                ).fetchone()
                if existing:
                    skipped += 1
                    continue

                match_reason = (
                    f"Innovation signal category '{category}' maps to pattern "
                    f"'{opp['pattern_type']}' in {opp.get('module_path','?')}"
                )
                try:
                    aiify.execute(
                        "INSERT INTO aiify_innovation_bridge "
                        "(signal_id, opportunity_id, pattern_type, match_reason, innovation_score, bridged_at) "
                        "VALUES (?,?,?,?,?,?)",
                        (sig_id, opp_id, opp["pattern_type"], match_reason,
                         sig.get("innovation_score"), _now()),
                    )
                    aiify.commit()
                    bridged += 1
                except Exception as exc:
                    errors.append(str(exc))
    finally:
        aiify.close()

    return {"bridged": bridged, "skipped": skipped, "errors": errors}


def report() -> list[dict]:
    """Return all bridge records with signal title and opportunity path."""
    aiify = _aiify_conn()
    try:
        rows = aiify.execute(
            "SELECT b.id, b.signal_id, b.opportunity_id, b.pattern_type, "
            "b.match_reason, b.innovation_score, b.bridged_at, "
            "o.module_path, o.ai_paradigm "
            "FROM aiify_innovation_bridge b "
            "LEFT JOIN aiify_opportunities o ON o.opportunity_id = b.opportunity_id "
            "ORDER BY b.innovation_score DESC, b.bridged_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
    finally:
        aiify.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AI-ify Innovation Signal Ingester")
    parser.add_argument("--ingest", action="store_true", help="Run signal ingestion")
    parser.add_argument("--report", action="store_true", help="Show bridge report")
    parser.add_argument("--min-score", type=float, default=0.60, help="Min innovation_score threshold")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    if args.ingest:
        result = ingest(min_innovation_score=args.min_score)
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"Bridged: {result['bridged']}  Skipped: {result['skipped']}  Errors: {len(result['errors'])}")
            for e in result["errors"]:
                print(f"  ERROR: {e}")
    elif args.report:
        rows = report()
        if args.json:
            print(json.dumps(rows, indent=2))
        else:
            for r in rows:
                print(f"[{r.get('innovation_score',0):.2f}] {r.get('pattern_type')} | {r.get('module_path')} ← signal {r.get('signal_id')}")
    else:
        parser.print_help()
