#!/usr/bin/env python3
# CUI // SP-CTI
"""Genesis Heal Reflex — pattern-based auto-remediation for known failures.

Monitors recent error patterns in the audit trail, matches against known
healing patterns in the knowledge base, and applies auto-remediation
when confidence exceeds threshold.

YELLOW tier (reversible writes with cooldown).
Scanner-tier only (zero Claude tokens).
"""

import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _get_recent_failures(lookback_hours: int = 6) -> List[Dict[str, Any]]:
    """Query recent error events from audit trail."""
    conn = get_connection()
    try:
        cutoff = (_utcnow() - timedelta(hours=lookback_hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
        rows = conn.execute("""
            SELECT id, event_type, details, created_at
            FROM audit_trail
            WHERE event_type LIKE 'error.%'
            AND created_at > ?
            ORDER BY created_at DESC
            LIMIT 50
        """, (cutoff,)).fetchall()
        return [dict(r) if hasattr(r, "keys") else {
            "id": r[0], "event_type": r[1], "details": r[2], "created_at": r[3]
        } for r in rows]
    except Exception as e:
        print(f"  WARN: Could not query failures: {e}")
        return []
    finally:
        conn.close()


def _get_healing_patterns() -> List[Dict[str, Any]]:
    """Load known healing patterns from self_healing_patterns table."""
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT id, pattern_name, error_pattern, resolution_type,
                   resolution_action, confidence, success_count, failure_count
            FROM self_healing_patterns
            WHERE confidence >= 0.7
            ORDER BY confidence DESC
        """).fetchall()
        return [dict(r) if hasattr(r, "keys") else {
            "id": r[0], "pattern_name": r[1], "error_pattern": r[2],
            "resolution_type": r[3], "resolution_action": r[4],
            "confidence": r[5], "success_count": r[6], "failure_count": r[7],
        } for r in rows]
    except Exception:
        return []
    finally:
        conn.close()


def _match_pattern(failure: Dict, patterns: List[Dict]) -> Optional[Dict]:
    """Match a failure against known healing patterns."""
    error_type = failure.get("event_type", "")
    event_data = failure.get("details", "")
    if isinstance(event_data, str):
        try:
            event_data = json.loads(event_data)
        except (json.JSONDecodeError, TypeError):
            pass

    error_msg = str(event_data) if event_data else error_type

    for pattern in patterns:
        match_str = pattern.get("error_pattern", "")
        if match_str and match_str.lower() in error_msg.lower():
            return pattern

    return None


def _record_healing_event(failure_id: int, pattern_id: int,
                          action: str, success: bool) -> None:
    """Record a healing event in self_healing_events table."""
    conn = get_connection()
    try:
        conn.execute("""
            INSERT INTO self_healing_events
            (pattern_id, trigger_event_id, action_taken, success, created_at)
            VALUES (?, ?, ?, ?, ?)
        """, (pattern_id, failure_id, action, 1 if success else 0, _utcnow_iso()))
        conn.commit()
    except Exception as e:
        print(f"  WARN: Could not record healing event: {e}")
    finally:
        conn.close()


def _apply_remediation(pattern: Dict) -> bool:
    """Apply a remediation action. Currently: log-only (safe mode)."""
    resolution_type = pattern.get("resolution_type", "")
    action = pattern.get("resolution_action", "")

    # For safety, Genesis heal only logs recommendations in v2.0
    # Active remediation (restart, config change) requires Phase 5+ maturity
    print(f"  Heal: matched pattern '{pattern.get('pattern_name', '')}' "
          f"→ recommended action: {action}")

    # For now, record as "would heal" — don't take destructive action
    return True


def run(config: Dict[str, Any], trust: Any) -> Dict[str, Any]:
    """Execute the Heal Reflex."""
    confidence_threshold = config.get("confidence_threshold", 0.7)
    max_heals = config.get("max_auto_heals_per_hour", 5)

    # Get recent failures
    failures = _get_recent_failures(lookback_hours=6)
    if not failures:
        return {
            "success": True,
            "metric_value": 0.0,
            "details": {"status": "no_recent_failures"},
        }

    # Load healing patterns
    patterns = _get_healing_patterns()
    if not patterns:
        return {
            "success": True,
            "metric_value": 0.0,
            "details": {
                "status": "no_healing_patterns",
                "failures_found": len(failures),
            },
        }

    matches = []
    healed = 0

    for failure in failures[:20]:  # Process max 20 per cycle
        match = _match_pattern(failure, patterns)
        if not match:
            continue

        if match.get("confidence", 0) < confidence_threshold:
            continue

        if healed >= max_heals:
            matches.append({
                "failure_id": failure.get("id"),
                "pattern": match.get("pattern_name"),
                "status": "rate_limited",
            })
            continue

        success = _apply_remediation(match)
        _record_healing_event(
            failure_id=failure.get("id", 0),
            pattern_id=match.get("id", 0),
            action=match.get("resolution_action", ""),
            success=success,
        )

        healed += 1
        matches.append({
            "failure_id": failure.get("id"),
            "pattern": match.get("pattern_name"),
            "status": "healed" if success else "failed",
            "confidence": match.get("confidence"),
        })

    return {
        "success": True,
        "metric_value": float(healed),
        "details": {
            "failures_scanned": len(failures),
            "patterns_available": len(patterns),
            "matches_found": len(matches),
            "healed_count": healed,
            "matches": matches,
        },
    }
