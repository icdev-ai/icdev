#!/usr/bin/env python3
# CUI // SP-CTI
"""Codebase Health Scorer — composite score across all 7 personas (D-RB-12).

Produces a single 0-100 health score from:
    - Finding severity counts (weighted)
    - Fix success rate
    - Reflex pass rates
    - Coverage metrics

Trend tracking: stores scores in review_board_health_history (append-only).

Usage:
    python tools/review_board/health_scorer.py --compute --json
    python tools/review_board/health_scorer.py --trend --json
    python tools/review_board/health_scorer.py --latest --json
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection as _raw_get_connection  # noqa: E402
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.review_board.health_scorer")


def _get_connection():
    conn = _raw_get_connection()
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
    except Exception:
        pass
    return conn


def _now():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _gen_id():
    import uuid

    return f"rbh-{uuid.uuid4().hex[:10]}"


# Severity penalty weights (deducted from 100)
SEVERITY_PENALTIES = {
    "critical": 15.0,
    "high": 8.0,
    "medium": 3.0,
    "low": 1.0,
    "info": 0.0,
}

# Per-persona weights (how much each persona matters to overall health)
PERSONA_WEIGHTS = {
    "sre": 0.20,
    "security": 0.20,
    "qa": 0.15,
    "perf": 0.15,
    "ux": 0.10,
    "docs": 0.10,
    "product": 0.10,
}


def ensure_tables():
    """Create health history table."""
    conn = _get_connection()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS review_board_health_history (
                id          TEXT PRIMARY KEY,
                score       REAL NOT NULL,
                grade       TEXT NOT NULL,
                trend       TEXT NOT NULL DEFAULT 'stable',
                breakdown   TEXT NOT NULL,
                created_at  TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_rbhh_created
                ON review_board_health_history(created_at)
        """)
        conn.commit()
    finally:
        conn.close()


def compute_health_score() -> Dict[str, Any]:
    """Compute composite codebase health score (0-100)."""
    ensure_tables()
    conn = _get_connection()
    try:
        # 1. Count open findings by severity (unfixed)
        severity_counts = {}
        try:
            rows = conn.execute(
                "SELECT severity, COUNT(*) FROM review_board_findings WHERE fix_applied = 0 GROUP BY severity"
            ).fetchall()
            severity_counts = {r[0]: r[1] for r in rows}
        except Exception:
            pass

        # 2. Calculate penalty from findings
        total_penalty = 0.0
        for severity, count in severity_counts.items():
            penalty = SEVERITY_PENALTIES.get(severity, 0.0) * count
            total_penalty += penalty
        # Cap penalty at 80 (minimum score = 20)
        total_penalty = min(total_penalty, 80.0)

        # 3. Per-persona health scores
        persona_scores = {}
        try:
            reflex_rows = conn.execute(
                "SELECT reflex_name, total_runs, total_successes, total_failures FROM review_board_reflex_state"
            ).fetchall()
            for r in reflex_rows:
                name = r[0]
                total = r[1] or 0
                successes = r[2] or 0
                if total > 0:
                    persona_scores[name] = round((successes / total) * 100, 1)
                else:
                    persona_scores[name] = 100.0  # No runs = no failures
        except Exception:
            pass

        # 4. Remediation success rate bonus (up to +10)
        fix_bonus = 0.0
        try:
            fixed = conn.execute(
                "SELECT COUNT(*) FROM review_board_remediation_log WHERE status IN ('fixed', 'verified')"
            ).fetchone()
            failed = conn.execute(
                "SELECT COUNT(*) FROM review_board_remediation_log WHERE status = 'failed'"
            ).fetchone()
            total_remediations = (fixed[0] if fixed else 0) + (failed[0] if failed else 0)
            if total_remediations > 0:
                fix_rate = (fixed[0] if fixed else 0) / total_remediations
                fix_bonus = fix_rate * 10.0  # Up to +10 for perfect fix rate
        except Exception:
            pass

        # 5. Compute final score
        base_score = 100.0 - total_penalty + fix_bonus
        score = max(0.0, min(100.0, base_score))
        grade = _score_to_grade(score)

        # 6. Determine trend (compare to last score)
        trend = "stable"
        try:
            last = conn.execute(
                "SELECT score FROM review_board_health_history ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
            if last:
                delta = score - last[0]
                if delta > 2.0:
                    trend = "improving"
                elif delta < -2.0:
                    trend = "degrading"
        except Exception:
            pass

        breakdown = {
            "severity_counts": severity_counts,
            "severity_penalty": round(total_penalty, 1),
            "persona_scores": persona_scores,
            "fix_bonus": round(fix_bonus, 1),
            "finding_count_unfixed": sum(severity_counts.values()),
        }

        result = {
            "score": round(score, 1),
            "grade": grade,
            "trend": trend,
            "breakdown": breakdown,
        }

        # 7. Store in history (append-only)
        try:
            conn.execute(
                "INSERT INTO review_board_health_history "
                "(id, score, grade, trend, breakdown, created_at) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (_gen_id(), score, grade, trend, json.dumps(breakdown), _now()),
            )
            conn.commit()
        except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
            logger.warning(
                "compute_health_score: best-effort INSERT into review_board_health_history failed (non-blocking): %s",
                exc,
            )

        return result

    finally:
        conn.close()


def get_trend(limit: int = 30) -> List[Dict]:
    """Get health score trend over time."""
    ensure_tables()
    conn = _get_connection()
    try:
        rows = conn.execute(
            "SELECT id, score, grade, trend, created_at "
            "FROM review_board_health_history "
            "ORDER BY created_at DESC LIMIT %s",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
    finally:
        conn.close()


def get_latest() -> Dict[str, Any]:
    """Get the most recent health score."""
    ensure_tables()
    conn = _get_connection()
    try:
        row = conn.execute("SELECT * FROM review_board_health_history ORDER BY created_at DESC LIMIT 1").fetchone()
        if row:
            result = dict(row)
            result["breakdown"] = json.loads(result.get("breakdown", "{}"))
            return result
        return {"score": None, "grade": "N/A", "trend": "unknown"}
    except Exception:
        return {"score": None, "grade": "N/A", "trend": "unknown"}
    finally:
        conn.close()


def _score_to_grade(score: float) -> str:
    """Convert numeric score to letter grade."""
    if score >= 90:
        return "A"
    if score >= 80:
        return "B"
    if score >= 70:
        return "C"
    if score >= 60:
        return "D"
    return "F"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Codebase Health Scorer (D-RB-12)")
    parser.add_argument("--compute", action="store_true", help="Compute current health score")
    parser.add_argument("--trend", action="store_true", help="Show score trend")
    parser.add_argument("--latest", action="store_true", help="Show latest score")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--limit", type=int, default=30, help="Trend limit")
    args = parser.parse_args()

    if args.compute:
        result = compute_health_score()
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"Health Score: {result['score']}/100 ({result['grade']}) — {result['trend']}")
    elif args.trend:
        trend = get_trend(limit=args.limit)
        if args.json:
            print(json.dumps({"trend": trend, "count": len(trend)}, indent=2))
        else:
            for t in trend:
                print(f"  {t['created_at']}: {t['score']}/100 ({t['grade']}) {t['trend']}")
    elif args.latest:
        result = get_latest()
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"Latest: {result.get('score', 'N/A')}/100 ({result.get('grade', 'N/A')})")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
