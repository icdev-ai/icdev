#!/usr/bin/env python3
# CUI // SP-CTI
"""Temporal correlator: match CyberOperation events to ConflictEvents within a time window.

Reads sg_conflict_events for both ``event_type='cyber_op'`` (STIX-imported) and
``event_type='narrative_event'`` (GDELT-imported) rows, then writes a ``CORRELATES``
edge to canvas_kg_edges for every pair whose timestamps fall within the configured
window (default: 72 hours).

Deduplication uses deterministic edge IDs of the form
``corr_<cyber_op_id>_<conflict_event_id>``, so re-running is safe.

Nodes for each event type are upserted into canvas_kg_nodes so the kill-chain
graph can render all correlated pairs.

Usage
-----
  python tools/strategos/temporal_correlator.py --run
  python tools/strategos/temporal_correlator.py --run --window-hours 48 --json
  python tools/strategos/temporal_correlator.py --dry-run --json
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.logging.icdev_logger import get_logger  # noqa: E402

from tools.db.storage import get_connection  # noqa: E402

logger = get_logger(__name__)

_DEFAULT_WINDOW_HOURS = 72
_EDGE_TYPE = "CORRELATES"
_KG_CANVAS = "sg"
_DESIGN_ID = "temporal-correlator-v1"

# ---------------------------------------------------------------------------
# KG table DDL (mirrors stix_importer / canvas/kg_builder schemas)
# ---------------------------------------------------------------------------

_CREATE_KG_NODES = """\
CREATE TABLE IF NOT EXISTS canvas_kg_nodes (
    id            TEXT PRIMARY KEY,
    canvas        TEXT NOT NULL,
    design_id     TEXT NOT NULL,
    node_id       TEXT NOT NULL,
    node_type     TEXT,
    label         TEXT,
    metadata_json TEXT,
    updated_at    TEXT
)"""

_CREATE_KG_EDGES = """\
CREATE TABLE IF NOT EXISTS canvas_kg_edges (
    id            TEXT PRIMARY KEY,
    canvas        TEXT NOT NULL,
    design_id     TEXT NOT NULL,
    source_id     TEXT NOT NULL,
    target_id     TEXT NOT NULL,
    edge_type     TEXT,
    metadata_json TEXT,
    updated_at    TEXT
)"""


def _ensure_tables(conn) -> None:
    conn.execute(_CREATE_KG_NODES)
    conn.execute(_CREATE_KG_EDGES)


# ---------------------------------------------------------------------------
# Timestamp parsing
# ---------------------------------------------------------------------------


def _ts_str(val) -> str | None:
    """Coerce a datetime or string to an ISO-8601 string, or None."""
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.isoformat()
    return str(val)


def _parse_ts(ts) -> datetime | None:
    """Parse a timestamp value (str, datetime, or None) → timezone-aware UTC datetime."""
    if ts is None:
        return None
    if isinstance(ts, datetime):
        return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    if not isinstance(ts, str):
        return None
    # fromisoformat handles the common cases; normalize trailing Z first
    cleaned = ts.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(cleaned)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        pass
    # Fallback: date-only
    try:
        dt = datetime.strptime(ts.strip()[:10], "%Y-%m-%d")
        return dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def correlate(
    window_hours: int = _DEFAULT_WINDOW_HOURS,
    dry_run: bool = False,
    as_json: bool = False,
) -> dict[str, Any]:
    """Find CyberOperation↔ConflictEvent pairs within *window_hours* and write CORRELATES edges.

    Steps
    -----
    1. Load all ``cyber_op`` rows (CyberOperations) from sg_conflict_events.
    2. Load all ``narrative_event`` rows (ConflictEvents) from sg_conflict_events.
    3. Upsert KG nodes for every cyber_op and conflict_event (idempotent).
    4. For each pair within the time window write one ``CORRELATES`` edge
       using a deterministic ID — safe to re-run.

    Returns
    -------
    dict with keys: cyber_ops, conflict_events, pairs_found, edges_written,
                    edges_skipped, dry_run, window_hours
    """
    window = timedelta(hours=window_hours)
    now_iso = datetime.now(timezone.utc).isoformat()

    conn = get_connection()
    try:
        _ensure_tables(conn)

        # -- Load cyber_op rows -------------------------------------------
        cyber_ops: list[dict] = [
            dict(r)
            for r in conn.execute(
                "SELECT id, external_id, event_ts, description, actor1, "
                "       technique_ids, threat_actor, confidence, metadata_json "
                "FROM sg_conflict_events WHERE event_type = 'cyber_op'"
            ).fetchall()
        ]

        # -- Load narrative_event (ConflictEvent) rows ---------------------
        conflict_events: list[dict] = []
        try:
            conflict_events = [
                dict(r)
                for r in conn.execute(
                    "SELECT id, external_id, event_ts, description, actor1, "
                    "       event_code, goldstein_scale, lat, lon, aoi "
                    "FROM sg_conflict_events WHERE event_type = 'narrative_event'"
                ).fetchall()
            ]
        except Exception as exc:
            # Columns like goldstein_scale may not exist in older schemas
            logger.debug("narrative_event query fell back: %s", exc)
            conflict_events = [
                dict(r)
                for r in conn.execute(
                    "SELECT id, external_id, event_ts, description, actor1 "
                    "FROM sg_conflict_events WHERE event_type = 'narrative_event'"
                ).fetchall()
            ]

        if dry_run:
            pairs = sum(
                1
                for co in cyber_ops
                for ce in conflict_events
                if (co_ts := _parse_ts(co["event_ts"])) is not None
                and (ce_ts := _parse_ts(ce["event_ts"])) is not None
                and abs(co_ts - ce_ts) <= window
            )
            summary: dict[str, Any] = {
                "dry_run":         True,
                "window_hours":    window_hours,
                "cyber_ops":       len(cyber_ops),
                "conflict_events": len(conflict_events),
                "pairs_found":     pairs,
            }
            if as_json:
                print(json.dumps(summary, indent=2))
            else:
                print(f"[DRY RUN] {pairs} correlatable pairs "
                      f"({len(cyber_ops)} cyber ops × {len(conflict_events)} conflict events, "
                      f"{window_hours}h window)")
            return summary

        # -- Upsert KG nodes for cyber_ops ---------------------------------
        for co in cyber_ops:
            meta: dict = {}
            try:
                meta = json.loads(co.get("metadata_json") or "{}")
            except (json.JSONDecodeError, TypeError):
                pass
            label = meta.get("name") or co.get("threat_actor") or co.get("actor1") or co["id"]
            conn.execute(
                "INSERT OR IGNORE INTO canvas_kg_nodes "
                "(id, canvas, design_id, node_id, node_type, label, metadata_json, updated_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    f"corr_co_{co['id']}",
                    _KG_CANVAS,
                    _DESIGN_ID,
                    co["id"],
                    "CyberOperation",
                    label,
                    json.dumps({
                        "event_ts":      _ts_str(co.get("event_ts")),
                        "technique_ids": co.get("technique_ids"),
                        "actor1":        co.get("actor1"),
                        "confidence":    co.get("confidence"),
                    }),
                    now_iso,
                ),
            )

        # -- Upsert KG nodes for conflict_events ---------------------------
        for ce in conflict_events:
            label = (ce.get("description") or "")[:80] or ce["id"]
            conn.execute(
                "INSERT OR IGNORE INTO canvas_kg_nodes "
                "(id, canvas, design_id, node_id, node_type, label, metadata_json, updated_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    f"corr_ce_{ce['id']}",
                    _KG_CANVAS,
                    _DESIGN_ID,
                    ce["id"],
                    "ConflictEvent",
                    label,
                    json.dumps({
                        "event_ts":        _ts_str(ce.get("event_ts")),
                        "event_code":      ce.get("event_code"),
                        "goldstein_scale": ce.get("goldstein_scale"),
                        "aoi":             ce.get("aoi"),
                        "actor1":          ce.get("actor1"),
                    }),
                    now_iso,
                ),
            )

        # -- Match pairs and write CORRELATES edges ------------------------
        edges_written = edges_skipped = 0
        for co in cyber_ops:
            co_ts = _parse_ts(co["event_ts"])
            if co_ts is None:
                continue
            for ce in conflict_events:
                ce_ts = _parse_ts(ce["event_ts"])
                if ce_ts is None:
                    continue
                delta = abs(co_ts - ce_ts)
                if delta > window:
                    continue

                edge_id = f"corr_{co['id']}_{ce['id']}"
                cur = conn.execute(
                    "INSERT OR IGNORE INTO canvas_kg_edges "
                    "(id, canvas, design_id, source_id, target_id, "
                    " edge_type, metadata_json, updated_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                    (
                        edge_id,
                        _KG_CANVAS,
                        _DESIGN_ID,
                        co["id"],
                        ce["id"],
                        _EDGE_TYPE,
                        json.dumps({
                            "delta_hours":   round(delta.total_seconds() / 3600, 2),
                            "window_hours":  window_hours,
                            "co_actor":      co.get("actor1"),
                            "co_techniques": co.get("technique_ids"),
                            "ce_event_code": ce.get("event_code"),
                            "ce_aoi":        ce.get("aoi"),
                        }),
                        now_iso,
                    ),
                )
                rc = getattr(cur, "rowcount", None)
                if rc == 0:
                    edges_skipped += 1
                else:
                    edges_written += 1

        conn.commit()
    finally:
        conn.close()

    summary = {
        "dry_run":         False,
        "window_hours":    window_hours,
        "cyber_ops":       len(cyber_ops),
        "conflict_events": len(conflict_events),
        "pairs_found":     edges_written + edges_skipped,
        "edges_written":   edges_written,
        "edges_skipped":   edges_skipped,
    }
    if as_json:
        print(json.dumps(summary, indent=2))
    else:
        print(
            f"Temporal correlator ({window_hours}h window): "
            f"{edges_written} CORRELATES edges written, "
            f"{edges_skipped} duplicate(s) skipped"
        )
        print(
            f"  Matched {edges_written + edges_skipped} pairs from "
            f"{len(cyber_ops)} cyber ops × {len(conflict_events)} conflict events"
        )
    return summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Match CyberOperation events to ConflictEvents within a time window "
            "and write CORRELATES edges to canvas_kg_edges."
        )
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--run", action="store_true", help="Execute correlation (writes to DB)")
    group.add_argument("--dry-run", action="store_true", help="Count pairs without writing")
    parser.add_argument(
        "--window-hours",
        type=int,
        default=_DEFAULT_WINDOW_HOURS,
        help=f"Correlation window in hours (default: {_DEFAULT_WINDOW_HOURS})",
    )
    parser.add_argument("--json", action="store_true", dest="as_json", help="JSON output")
    parser.add_argument("-v", "--verbose", action="store_true", help="Debug logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(message)s",
    )

    correlate(
        window_hours=args.window_hours,
        dry_run=args.dry_run,
        as_json=args.as_json,
    )


if __name__ == "__main__":
    main()
