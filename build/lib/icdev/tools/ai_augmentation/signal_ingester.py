# CUI // SP-CTI
"""AAC Signal Ingester (Option A — Innovation → AAC Bridge).

Queries innovation_signals for entries relevant to AI augmentation patterns
and cross-references them against aac_opportunities to create bridge records
in aac_innovation_bridge.

Run nightly (or on-demand):
    python tools/ai_augmentation/signal_ingester.py --ingest --json
    python tools/ai_augmentation/signal_ingester.py --report --json
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone

from tools.ai_augmentation.db.init_db import get_connection as _aac_conn

# Innovation signal categories that map to AAC patterns
_CATEGORY_TO_PATTERNS: dict[str, list[str]] = {
    "ai_tooling":                    ["hardcoded_threshold", "nested_conditionals"],
    "agentic":                       ["scheduled_cron", "string_template_rendering"],
    "external_framework_analysis":   ["keyword_list_search", "regex_user_input"],
    "ai_augmentation_opportunity":   ["large_rule_table", "db_render_notify_chain"],
}

_BRIDGE_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS aac_innovation_bridge (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id       TEXT NOT NULL,
    opportunity_id  INTEGER NOT NULL,
    pattern_type    TEXT NOT NULL,
    match_reason    TEXT,
    innovation_score REAL,
    bridged_at      TEXT DEFAULT CURRENT_TIMESTAMP
)"""

_BRIDGE_TABLE_DDL_PG = """
CREATE TABLE IF NOT EXISTS aac_innovation_bridge (
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


def _ensure_bridge_table(aac: object) -> None:
    """Create aac_innovation_bridge if it doesn't exist."""
    import os
    backend = os.environ.get(
        "AAC_STORAGE_BACKEND",
        os.environ.get("ICDEV_CANVAS_STORAGE_BACKEND", "postgresql"),
    ).lower()
    ddl = _BRIDGE_TABLE_DDL_PG if backend == "postgresql" else _BRIDGE_TABLE_DDL
    try:
        aac.execute(ddl)
        aac.commit()
    except Exception:
        pass


def ingest(min_innovation_score: float = 0.60) -> dict:
    """Pull innovation_signals and bridge to matching aac_opportunities.

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
                "WHERE category IN ('ai_tooling','agentic','external_framework_analysis','ai_augmentation_opportunity') "
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

    # Load AAC opportunities
    aac = _aac_conn()
    _ensure_bridge_table(aac)
    try:
        rows = aac.execute(
            "SELECT opportunity_id, pattern_type, module_path FROM aac_opportunities"
        ).fetchall()
        opps = [dict(r) for r in rows]
    finally:
        aac.close()

    bridged = skipped = 0
    errors: list[str] = []

    aac = _aac_conn()
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
                existing = aac.execute(
                    "SELECT id FROM aac_innovation_bridge WHERE signal_id = ? AND opportunity_id = ?",
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
                    aac.execute(
                        "INSERT INTO aac_innovation_bridge "
                        "(signal_id, opportunity_id, pattern_type, match_reason, innovation_score, bridged_at) "
                        "VALUES (?,?,?,?,?,?)",
                        (sig_id, opp_id, opp["pattern_type"], match_reason,
                         sig.get("innovation_score"), _now()),
                    )
                    aac.commit()
                    bridged += 1
                except Exception as exc:
                    errors.append(str(exc))
    finally:
        aac.close()

    return {"bridged": bridged, "skipped": skipped, "errors": errors}


def report() -> list[dict]:
    """Return all bridge records with signal title and opportunity path."""
    aac = _aac_conn()
    try:
        rows = aac.execute(
            "SELECT b.id, b.signal_id, b.opportunity_id, b.pattern_type, "
            "b.match_reason, b.innovation_score, b.bridged_at, "
            "o.module_path, o.ai_paradigm "
            "FROM aac_innovation_bridge b "
            "LEFT JOIN aac_opportunities o ON o.opportunity_id = b.opportunity_id "
            "ORDER BY b.innovation_score DESC, b.bridged_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
    finally:
        aac.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AAC Innovation Signal Ingester")
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
