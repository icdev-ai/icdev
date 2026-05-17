# CUI // SP-CTI
"""ETL Pipeline — normalize conflict events into the sg_conflict_events table.

Pulls events from MeshCoordinator, validates required fields, and upserts
into the canonical sg_conflict_events schema.

Usage (library):
    from tools.conflict_mesh.mesh_coordinator import MeshCoordinator
    from tools.conflict_mesh.providers.acled_provider import ACLEDProvider
    from tools.conflict_mesh.etl_pipeline import ETLPipeline

    coord = MeshCoordinator([ACLEDProvider()])
    pipeline = ETLPipeline(coord)
    result = pipeline.run(since_date="2024-01-01", dry_run=False)
    print(result)

Usage (CLI):
    python tools/conflict_mesh/etl_pipeline.py --since 2024-01-01 --dry-run --json
    python tools/conflict_mesh/etl_pipeline.py --since 2024-01-01 --limit 50 --json
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

logger = logging.getLogger("conflict_mesh.etl")

_VALID_EVENT_TYPES = frozenset({
    "frontline_position", "narrative_event", "cyber_op",
    "kinetic", "humanitarian", "diplomatic", "logistics",
})


@dataclass
class ETLResult:
    fetched: int = 0
    inserted: int = 0
    updated: int = 0
    skipped: int = 0
    errors: int = 0
    dry_run: bool = False


def _get_conn(db_path: Optional[str] = None):
    if db_path:
        import sqlite3
        return sqlite3.connect(db_path)
    from tools.db.storage import get_connection
    return get_connection()


class ETLPipeline:
    """Normalize and load conflict events into sg_conflict_events."""

    def __init__(self, coordinator: Any, db_path: Optional[str] = None) -> None:
        self._coordinator = coordinator
        self._db_path = db_path

    def run(
        self,
        since_date: Optional[str] = None,
        limit_per_provider: int = 100,
        dry_run: bool = False,
    ) -> ETLResult:
        result = ETLResult(dry_run=dry_run)
        events = self._coordinator.fetch_all(
            since_date=since_date, limit_per_provider=limit_per_provider
        )
        result.fetched = len(events)

        if dry_run:
            for evt in events:
                if not self._is_valid(evt):
                    result.skipped += 1
                else:
                    result.inserted += 1  # would insert
            result.inserted = 0  # dry_run: no actual writes
            return result

        conn = _get_conn(self._db_path)
        try:
            now = datetime.now(timezone.utc).isoformat()
            for evt in events:
                if not self._is_valid(evt):
                    result.skipped += 1
                    continue
                try:
                    self._upsert(conn, evt, now)
                    result.inserted += 1
                except Exception as exc:
                    logger.warning("Failed to upsert event %s: %s", evt.get("id"), exc)
                    result.errors += 1
            conn.commit()
        finally:
            conn.close()
        return result

    def _is_valid(self, evt: Dict[str, Any]) -> bool:
        """Require at least one of: headline or event_date."""
        return bool(evt.get("headline")) or bool(evt.get("event_date"))

    def _upsert(self, conn: Any, evt: Dict[str, Any], now: str) -> None:
        event_type = evt.get("event_type", "narrative_event")
        if event_type not in _VALID_EVENT_TYPES:
            event_type = "narrative_event"
        metadata = evt.get("metadata") or {}
        metadata_str = json.dumps(metadata) if isinstance(metadata, dict) else str(metadata)

        conn.execute(
            """
            INSERT INTO sg_conflict_events
                (id, event_type, geometry, event_date, source, metadata, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                event_type = excluded.event_type,
                geometry   = excluded.geometry,
                event_date = excluded.event_date,
                metadata   = excluded.metadata,
                updated_at = excluded.updated_at
            """,
            (
                evt.get("id", ""),
                event_type,
                evt.get("geometry"),
                evt.get("event_date"),
                evt.get("source", ""),
                metadata_str,
                now,
                now,
            ),
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Conflict Mesh ETL Pipeline")
    parser.add_argument("--since", help="Fetch events since date (YYYY-MM-DD)")
    parser.add_argument("--limit", type=int, default=100, help="Max events per provider")
    parser.add_argument("--dry-run", action="store_true", help="Do not write to DB")
    parser.add_argument("--providers", nargs="+", default=["acled", "gdelt", "reliefweb"],
                        help="Providers to enable (default: all)")
    parser.add_argument("--json", action="store_true", dest="output_json")
    args = parser.parse_args()

    from tools.conflict_mesh.providers.acled_provider import ACLEDProvider
    from tools.conflict_mesh.providers.gdelt_provider import GDELTProvider
    from tools.conflict_mesh.providers.reliefweb_provider import ReliefWebProvider
    from tools.conflict_mesh.mesh_coordinator import MeshCoordinator

    provider_map = {
        "acled": ACLEDProvider,
        "gdelt": GDELTProvider,
        "reliefweb": ReliefWebProvider,
    }
    active = [cls() for name, cls in provider_map.items() if name in args.providers]
    coord = MeshCoordinator(active)
    pipeline = ETLPipeline(coord)
    result = pipeline.run(
        since_date=args.since,
        limit_per_provider=args.limit,
        dry_run=args.dry_run,
    )

    if args.output_json:
        print(json.dumps(asdict(result), indent=2))
    else:
        print(f"ETL complete: fetched={result.fetched} inserted={result.inserted} "
              f"skipped={result.skipped} errors={result.errors} dry_run={result.dry_run}")


if __name__ == "__main__":
    main()
