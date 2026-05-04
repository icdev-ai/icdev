#!/usr/bin/env python3
# CUI // SP-CTI
"""Behavior Learner — calibrate Bayesian priors from user responses (D-AE-12).

Tracks user behavior signals:
    - Finding dismissed    → β += 0.5 (partial penalty for that category)
    - Finding acted on     → α += 1.0 (full reward)
    - PR merged quickly    → α += 1.5 (strong reward)
    - PR rejected          → β += 2.0 (strong penalty)
    - Finding ignored >7d  → neutral (no update)

Scans audit_trail and review_board tables for these signals periodically.

Usage:
    python tools/autonomy/behavior_learner.py --scan --json
    python tools/autonomy/behavior_learner.py --history --json
    python tools/autonomy/behavior_learner.py --stats --json
"""

import argparse
import json
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, List

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


def scan_user_behavior() -> Dict[str, Any]:
    """Scan for user behavior signals and apply Bayesian updates."""
    from tools.autonomy.trust_engine import ensure_tables

    ensure_tables()
    config = _load_config()
    bl_config = config.get("behavior_learner", {})
    if not bl_config.get("enabled", True):
        return {"status": "disabled"}

    signals_config = bl_config.get("signals", {})
    window_days = bl_config.get("observation_window_days", 30)
    conn = _get_connection()
    signals_found = []

    try:
        # 1. Findings that were fixed (acted on) — maps to auto_fix category
        try:
            acted_on = conn.execute(
                "SELECT id, reflex_name, category FROM review_board_findings "
                "WHERE fix_applied = 1 "
                "AND created_at > datetime('now', ? || ' days') "
                "AND id NOT IN (SELECT finding_id FROM autonomy_behavior_log WHERE finding_id IS NOT NULL)",
                (f"-{window_days}",),
            ).fetchall()
            for row in acted_on:
                signal = {
                    "signal_type": "finding_acted_on",
                    "finding_id": row[0],
                    "category": "auto_fix",
                    "alpha_delta": signals_config.get("finding_acted_on", {}).get("alpha_increment", 1.0),
                    "beta_delta": 0.0,
                }
                signals_found.append(signal)
        except Exception:
            pass

        # 2. Remediation successes — strengthen auto_fix trust
        try:
            successful_fixes = conn.execute(
                "SELECT id, category FROM review_board_remediation_log "
                "WHERE status IN ('fixed', 'verified') AND tier = 'auto_fix' "
                "AND created_at > datetime('now', ? || ' days') "
                "AND id NOT IN (SELECT COALESCE(finding_id, '') FROM autonomy_behavior_log)",
                (f"-{window_days}",),
            ).fetchall()
            for row in successful_fixes:
                signal = {
                    "signal_type": "pr_merged",  # Treat verified fix like PR merge
                    "finding_id": row[0],
                    "category": "auto_fix",
                    "alpha_delta": signals_config.get("pr_merged", {}).get("alpha_increment", 1.5),
                    "beta_delta": 0.0,
                }
                signals_found.append(signal)
        except Exception:
            pass

        # 3. Failed remediations — penalize auto_fix trust
        try:
            failed_fixes = conn.execute(
                "SELECT id, category FROM review_board_remediation_log "
                "WHERE status = 'failed' "
                "AND created_at > datetime('now', ? || ' days') "
                "AND id NOT IN (SELECT COALESCE(finding_id, '') FROM autonomy_behavior_log)",
                (f"-{window_days}",),
            ).fetchall()
            for row in failed_fixes:
                signal = {
                    "signal_type": "pr_rejected",
                    "finding_id": row[0],
                    "category": "auto_fix",
                    "alpha_delta": 0.0,
                    "beta_delta": signals_config.get("pr_rejected", {}).get("beta_increment", 2.0),
                }
                signals_found.append(signal)
        except Exception:
            pass

        # Apply signals to trust engine
        applied = 0
        for signal in signals_found:
            try:
                _apply_signal(conn, signal)
                applied += 1
            except Exception:
                pass

        conn.commit()

    finally:
        conn.close()

    return {
        "status": "scanned",
        "signals_found": len(signals_found),
        "signals_applied": applied,
        "signals": signals_found[:20],
    }


def _apply_signal(conn, signal: Dict) -> None:
    """Apply a behavior signal — update trust and log."""
    category = signal.get("category", "auto_fix")
    alpha_delta = signal.get("alpha_delta", 0.0)
    beta_delta = signal.get("beta_delta", 0.0)

    if alpha_delta == 0 and beta_delta == 0:
        return  # Neutral signal

    # Update trust state
    if alpha_delta > 0:
        conn.execute(
            "UPDATE autonomy_trust_state SET alpha = alpha + ?, last_updated = ? WHERE category = ?",
            (alpha_delta, now_iso(), category),
        )
    if beta_delta > 0:
        conn.execute(
            "UPDATE autonomy_trust_state SET beta = beta + ?, last_updated = ? WHERE category = ?",
            (beta_delta, now_iso(), category),
        )

    # Log to behavior log (append-only)
    conn.execute(
        "INSERT INTO autonomy_behavior_log "
        "(id, signal_type, finding_id, alpha_delta, beta_delta, category, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            f"abl-{uuid.uuid4().hex[:10]}",
            signal.get("signal_type", "unknown"),
            signal.get("finding_id"),
            alpha_delta,
            beta_delta,
            category,
            now_iso(),
        ),
    )


def get_history(limit: int = 50) -> List[Dict]:
    """Get behavior signal history."""
    conn = _get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM autonomy_behavior_log ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
    finally:
        conn.close()


def get_stats() -> Dict[str, Any]:
    """Get behavior learning statistics."""
    conn = _get_connection()
    try:
        by_type = conn.execute(
            "SELECT signal_type, COUNT(*), SUM(alpha_delta), SUM(beta_delta) "
            "FROM autonomy_behavior_log GROUP BY signal_type"
        ).fetchall()
        return {
            "by_signal_type": {
                r[0]: {"count": r[1], "total_alpha": r[2] or 0, "total_beta": r[3] or 0} for r in by_type
            },
            "total_signals": sum(r[1] for r in by_type) if by_type else 0,
        }
    except Exception as e:
        return {"error": str(e)}
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="Behavior Learner (D-AE-12)")
    parser.add_argument("--scan", action="store_true", help="Scan for behavior signals")
    parser.add_argument("--history", action="store_true", help="Show signal history")
    parser.add_argument("--stats", action="store_true", help="Show learning stats")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--limit", type=int, default=50, help="History limit")
    args = parser.parse_args()

    if args.scan:
        result = scan_user_behavior()
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"  Signals found: {result.get('signals_found', 0)}, Applied: {result.get('signals_applied', 0)}")
    elif args.history:
        history = get_history(limit=args.limit)
        if args.json:
            print(json.dumps({"history": history}, indent=2))
        else:
            for h in history:
                print(
                    f"  [{h.get('signal_type')}] α+{h.get('alpha_delta', 0)} "
                    f"β+{h.get('beta_delta', 0)} ({h.get('created_at')})"
                )
    elif args.stats:
        stats = get_stats()
        print(json.dumps(stats, indent=2) if args.json else str(stats))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
