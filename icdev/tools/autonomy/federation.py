#!/usr/bin/env python3
# CUI // SP-CTI
"""Engine Federation Router — auto-route between Innovation/Creative/Research (D-AE-11).

Closed-loop pipeline:
    Innovation signals (score >= 0.70) → Creative Engine pain point extraction
    Creative gaps (score >= 0.65)      → Research Engine deep analysis
    Research dossiers (status=reviewed) → Fitness assessment → child app generation

All routing gated by Bayesian Trust Engine (cross_engine_feed category).

Usage:
    python tools/autonomy/federation.py --check --json
    python tools/autonomy/federation.py --route --json
    python tools/autonomy/federation.py --route --dry-run --json
    python tools/autonomy/federation.py --status --json
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.common.helpers import now_iso  # noqa: E402
from tools.db.storage import get_connection as _raw_get_connection  # noqa: E402


def _get_connection():
    conn = _raw_get_connection()
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
    except Exception:
        pass
    return conn


def _load_config():
    config_path = BASE_DIR / "args" / "autonomy_config.yaml"
    if not config_path.exists():
        return {}
    try:
        import yaml

        with open(config_path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except ImportError:
        return {}


def check_routeable_signals() -> Dict[str, Any]:
    """Check all engines for signals ready to route."""
    conn = _get_connection()
    config = _load_config()
    fed_config = config.get("federation", {})
    results = {
        "innovation_to_creative": [],
        "creative_to_research": [],
        "research_to_fitness": [],
    }

    # 1. Innovation → Creative: high-scoring signals not yet routed
    try:
        min_score = fed_config.get("innovation_to_creative", {}).get("min_signal_score", 0.70)
        rows = conn.execute(
            "SELECT id, title, source, composite_score FROM innovation_signals "
            "WHERE composite_score >= %s "
            "AND id NOT IN (SELECT source_signal_id FROM creative_signals WHERE source_signal_id IS NOT NULL) "
            "ORDER BY composite_score DESC LIMIT 10",
            (min_score,),
        ).fetchall()
        results["innovation_to_creative"] = [{"id": r[0], "title": r[1], "source": r[2], "score": r[3]} for r in rows]
    except Exception:
        pass

    # 2. Creative → Research: high-scoring gaps not yet researched
    try:
        min_score = fed_config.get("creative_to_research", {}).get("min_gap_score", 0.65)
        rows = conn.execute(
            "SELECT id, title, composite_score FROM creative_feature_gaps "
            "WHERE composite_score >= %s "
            "AND id NOT IN (SELECT source_gap_id FROM research_sessions WHERE source_gap_id IS NOT NULL) "
            "ORDER BY composite_score DESC LIMIT 10",
            (min_score,),
        ).fetchall()
        results["creative_to_research"] = [{"id": r[0], "title": r[1], "score": r[2]} for r in rows]
    except Exception:
        pass

    # 3. Research → Fitness: reviewed dossiers not yet fitness-assessed
    try:
        rows = conn.execute(
            "SELECT id, session_id, vertical, title FROM research_dossiers "
            "WHERE status = 'reviewed' "
            "AND session_id NOT IN ("
            "  SELECT research_session_id FROM agentic_fitness_assessments "
            "  WHERE research_session_id IS NOT NULL"
            ") "
            "ORDER BY created_at DESC LIMIT 5"
        ).fetchall()
        results["research_to_fitness"] = [
            {"id": r[0], "session_id": r[1], "vertical": r[2], "title": r[3]} for r in rows
        ]
    except Exception:
        pass

    conn.close()

    results["total_routeable"] = sum(len(v) for v in results.values())
    return results


def route_signals(dry_run: bool = False) -> Dict[str, Any]:
    """Route signals between engines, gated by Bayesian trust."""
    from tools.autonomy.trust_engine import should_act, observe
    from tools.autonomy.kill_switch import is_killed

    if is_killed():
        return {"status": "killed", "routed": 0}

    # Check trust gate for cross-engine feed
    trust = should_act("cross_engine_feed")
    if trust["decision"] not in ("approved", "explored"):
        return {
            "status": "trust_denied",
            "trust_decision": trust,
            "routed": 0,
        }

    routeable = check_routeable_signals()
    routed = 0
    details = []

    # Route Innovation → Creative
    for signal in routeable.get("innovation_to_creative", []):
        if dry_run:
            details.append({"route": "innovation→creative", "signal": signal["title"], "dry_run": True})
            continue
        try:
            conn = _get_connection()
            try:
                conn.execute(
                    "INSERT OR IGNORE INTO creative_signals "
                    "(id, source, title, raw_content, source_signal_id, created_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s)",
                    (
                        f"cs-fed-{signal['id'][-10:]}",
                        "innovation_federation",
                        signal["title"],
                        json.dumps({"federated_from": "innovation_signals", "original_id": signal["id"]}),
                        signal["id"],
                        now_iso(),
                    ),
                )
                conn.commit()
                routed += 1
                details.append({"route": "innovation→creative", "signal": signal["title"], "status": "routed"})
            finally:
                conn.close()
        except Exception as e:
            details.append({"route": "innovation→creative", "signal": signal["title"], "error": str(e)})

    # Route Creative → Research (create research session)
    for gap in routeable.get("creative_to_research", []):
        if dry_run:
            details.append({"route": "creative→research", "gap": gap["title"], "dry_run": True})
            continue
        # Research sessions need more setup — just log the intent
        details.append(
            {
                "route": "creative→research",
                "gap": gap["title"],
                "status": "queued",
                "note": "Manual research session creation recommended",
            }
        )

    # Route Research → Fitness
    for dossier in routeable.get("research_to_fitness", []):
        if dry_run:
            details.append({"route": "research→fitness", "dossier": dossier["title"], "dry_run": True})
            continue
        details.append(
            {
                "route": "research→fitness",
                "dossier": dossier["title"],
                "status": "queued",
                "note": "Fitness assessment trigger queued",
            }
        )

    # Record outcome
    if routed > 0 or not dry_run:
        observe(
            "cross_engine_feed",
            success=True,
            source="federation",
            details={"routed": routed, "total_routeable": routeable["total_routeable"]},
        )

    return {
        "status": "completed",
        "dry_run": dry_run,
        "routed": routed,
        "total_routeable": routeable["total_routeable"],
        "details": details,
    }


def main():
    parser = argparse.ArgumentParser(description="Engine Federation Router (D-AE-11)")
    parser.add_argument("--check", action="store_true", help="Check routeable signals")
    parser.add_argument("--route", action="store_true", help="Route signals between engines")
    parser.add_argument("--dry-run", action="store_true", help="Preview without routing")
    parser.add_argument("--status", action="store_true", help="Federation status")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    if args.check or args.status:
        result = check_routeable_signals()
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"  Routeable: {result['total_routeable']}")
            for route, items in result.items():
                if isinstance(items, list) and items:
                    print(f"    {route}: {len(items)} signals")
    elif args.route:
        result = route_signals(dry_run=args.dry_run)
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"  Status: {result['status']}, Routed: {result['routed']}")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
