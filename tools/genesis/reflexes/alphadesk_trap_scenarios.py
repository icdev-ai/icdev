#!/usr/bin/env python3
# CUI // SP-CTI
"""Genesis Reflex — AlphaDesk Trap Scenarios.

Runs on an hourly cadence.  For every high-confidence trap event
(confidence >= 0.8) detected since the last run it spawns a matching
scenario simulation via scenario_engine.run_scenario(). At the end of
each calendar day it writes a digest draft to Pulse.

Data sources (in priority order):
  1. ad_trap_events        — populated by technical-02 (trap_scanner daemon)
  2. ad_options_coach_events — event_type='trap_against_position', severity='critical'
     used as fallback when ad_trap_events is not yet migrated.

Scenario:
  scenario_engine.run_scenario('potential_reversal_cascade') is called for
  each qualifying event.  Import is soft — reflex degrades gracefully when
  the scenario engine is not yet available.

GREEN tier (read + draft writes, no LLM in hot path).  Air-gap safe.
"""

from __future__ import annotations

import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

try:
    from tools.db.storage import get_connection
except ImportError:
    get_connection = None  # type: ignore[assignment]

try:
    from tools.trading.market_intel.scenario_engine import run_scenario as _run_scenario
except ImportError:
    _run_scenario = None  # type: ignore[assignment]

# ── Constants ─────────────────────────────────────────────────────────────────

CONFIDENCE_THRESHOLD = 0.8
SCENARIO_KEY = "potential_reversal_cascade"
_STATE_KEY = "_trap_last_check_at"
_DIGEST_SLUG_PREFIX = "trap-digest-"


# ── Internal helpers ──────────────────────────────────────────────────────────


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _get_last_check(config: Dict[str, Any]) -> str:
    """Return ISO timestamp of previous check, defaulting to 1 hour ago."""
    stored = config.get(_STATE_KEY)
    if stored:
        return stored
    return (_utcnow() - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _set_last_check(config: Dict[str, Any], ts: str) -> None:
    config[_STATE_KEY] = ts


def _fetch_trap_events(conn: Any, since: str) -> List[Dict[str, Any]]:
    """Fetch high-confidence trap events since *since* (ISO timestamp).

    Tries ad_trap_events first; falls back to ad_options_coach_events so the
    reflex works before migration 022_ad_trap_events is applied.
    """
    # Primary: ad_trap_events (from technical-02)
    try:
        rows = conn.execute(
            "SELECT id, ticker, pattern, broken_level, confidence, created_at "
            "FROM ad_trap_events "
            "WHERE confidence >= ? AND created_at > ? "
            "ORDER BY created_at ASC",
            (CONFIDENCE_THRESHOLD, since),
        ).fetchall()
        if rows is not None:
            result = []
            for r in rows:
                row = dict(r) if hasattr(r, "keys") else {
                    "id": r[0], "ticker": r[1], "pattern": r[2],
                    "broken_level": r[3], "confidence": r[4], "created_at": r[5],
                }
                result.append(row)
            return result
    except Exception:
        pass  # Table not yet migrated — fall through to coach events

    # Fallback: critical-severity coach events for trap_against_position
    try:
        rows = conn.execute(
            "SELECT id, summary, created_at "
            "FROM ad_options_coach_events "
            "WHERE event_type = 'trap_against_position' "
            "AND severity = 'critical' "
            "AND created_at > ? "
            "ORDER BY created_at ASC",
            (since,),
        ).fetchall()
        result = []
        for r in rows:
            row = dict(r) if hasattr(r, "keys") else {
                "id": r[0], "summary": r[1], "created_at": r[2],
            }
            row.setdefault("ticker", "unknown")
            row.setdefault("pattern", "trap_against_position")
            row.setdefault("confidence", CONFIDENCE_THRESHOLD)  # threshold met by severity
            row.setdefault("broken_level", None)
            result.append(row)
        return result
    except Exception:
        return []


def _spawn_scenario(trap: Dict[str, Any]) -> Optional[str]:
    """Call run_scenario and return run_id, or None if engine unavailable."""
    if _run_scenario is None:
        return None
    try:
        result = _run_scenario(SCENARIO_KEY)
        if isinstance(result, dict):
            return result.get("run_id") or result.get("id") or str(result)
        return str(result)
    except Exception as exc:
        print(f"  [trap_scenarios] scenario spawn failed: {exc}")
        return None


def _today_date_str() -> str:
    return _utcnow().strftime("%Y-%m-%d")


def _digest_already_written(conn: Any, date_str: str) -> bool:
    """Return True if a digest draft already exists for today."""
    slug = f"{_DIGEST_SLUG_PREFIX}{date_str}"
    try:
        row = conn.execute(
            "SELECT id FROM pulse_posts WHERE slug = ?", (slug,)
        ).fetchone()
        return row is not None
    except Exception:
        return False


def _write_digest(
    conn: Any,
    date_str: str,
    events: List[Dict[str, Any]],
    scenarios_spawned: int,
) -> Optional[str]:
    """Write a daily trap digest to pulse_posts as a draft."""
    slug = f"{_DIGEST_SLUG_PREFIX}{date_str}"
    title = f"FathomDesk Daily Trap Digest — {date_str}"

    # Build markdown body
    lines = [
        f"# {title}",
        "",
        "## Summary",
        "",
        f"- **{len(events)}** high-confidence trap event(s) detected "
        f"(confidence ≥ {CONFIDENCE_THRESHOLD:.0%})",
        f"- **{scenarios_spawned}** scenario simulation(s) spawned "
        f"(`{SCENARIO_KEY}`)",
        "",
        "## Trap Events",
        "",
        "| Ticker | Pattern | Confidence | Detected At |",
        "|--------|---------|-----------|------------|",
    ]
    for ev in events:
        ticker = ev.get("ticker", "—")
        pattern = ev.get("pattern", "—")
        conf = ev.get("confidence", 0.0)
        ts = ev.get("created_at", "—")
        lines.append(f"| {ticker} | {pattern} | {conf:.2f} | {ts} |")

    if not events:
        lines.append("| — | No high-confidence traps detected this run | — | — |")

    lines += [
        "",
        "---",
        f"*Generated by Genesis trap_scenarios reflex — {_utcnow_iso()}*",
    ]
    body = "\n".join(lines)

    now = _utcnow_iso()
    post_id = f"trap-digest-{uuid.uuid4().hex[:8]}"
    try:
        conn.execute(
            "INSERT OR IGNORE INTO pulse_posts "
            "(id, title, slug, status, topic, body_markdown, author_id, created_at, updated_at) "
            "VALUES (?, ?, ?, 'draft', ?, ?, 'genesis_trap_scenarios', ?, ?)",
            (post_id, title, slug, title, body, now, now),
        )
        conn.commit()
        return post_id
    except Exception as exc:
        print(f"  [trap_scenarios] digest write failed: {exc}")
        return None


# ── Genesis contract ──────────────────────────────────────────────────────────


def run(config: Dict[str, Any], trust: Any) -> Dict[str, Any]:
    """Execute one trap-scenarios reflex cycle.

    Called by the Genesis daemon every interval_seconds (3 600 s = 1 hour).
    Returns a summary dict consumed by the daemon's success_metric gate.
    """
    run_id = f"trap-scenarios-{uuid.uuid4().hex[:10]}"
    print(f"[trap_scenarios] run start  run_id={run_id}")

    if get_connection is None:
        return {
            "success": False,
            "metric_value": 0.0,
            "details": {"error": "get_connection not available", "run_id": run_id},
        }

    since = _get_last_check(config)
    check_started_at = _utcnow_iso()
    print(f"  [trap_scenarios] querying events since={since}")

    conn = None
    try:
        conn = get_connection()
    except Exception as exc:
        return {
            "success": False,
            "metric_value": 0.0,
            "details": {"error": f"DB connection failed: {exc}", "run_id": run_id},
        }

    # ── Fetch high-confidence trap events ────────────────────────────────────
    events: List[Dict[str, Any]] = []
    try:
        events = _fetch_trap_events(conn, since)
    except Exception as exc:
        print(f"  [trap_scenarios] WARNING: fetch failed: {exc}")

    print(f"  [trap_scenarios] found {len(events)} qualifying event(s)")

    # ── Spawn scenario for each qualifying event ──────────────────────────────
    scenarios_spawned = 0
    scenario_run_ids: List[str] = []
    for ev in events:
        ticker = ev.get("ticker", "?")
        pattern = ev.get("pattern", "?")
        print(
            f"  [trap_scenarios] spawning scenario for {ticker}/{pattern} "
            f"conf={ev.get('confidence', '?')}"
        )
        run_result = _spawn_scenario(ev)
        if run_result is not None:
            scenarios_spawned += 1
            scenario_run_ids.append(run_result)
        elif _run_scenario is None:
            print("  [trap_scenarios] scenario_engine unavailable — skipping spawn")

    # ── Daily digest (at most once per calendar day) ──────────────────────────
    digest_post_id: Optional[str] = None
    date_str = _today_date_str()
    digest_written = False
    try:
        if not _digest_already_written(conn, date_str):
            digest_post_id = _write_digest(conn, date_str, events, scenarios_spawned)
            digest_written = digest_post_id is not None
            if digest_written:
                print(f"  [trap_scenarios] digest staged post_id={digest_post_id}")
            else:
                print("  [trap_scenarios] digest write skipped or failed")
        else:
            print(f"  [trap_scenarios] digest for {date_str} already exists — skipping")
    except Exception as exc:
        print(f"  [trap_scenarios] WARNING: digest phase failed: {exc}")

    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass

    # Update last-check state so next run is incremental
    _set_last_check(config, check_started_at)

    metric_value = float(len(events))
    return {
        "success": True,
        "metric_value": metric_value,
        "details": {
            "run_id": run_id,
            "since": since,
            "events_found": len(events),
            "scenarios_spawned": scenarios_spawned,
            "scenario_run_ids": scenario_run_ids,
            "scenario_engine_available": _run_scenario is not None,
            "digest_written": digest_written,
            "digest_post_id": digest_post_id,
            "run_at": check_started_at,
        },
    }
