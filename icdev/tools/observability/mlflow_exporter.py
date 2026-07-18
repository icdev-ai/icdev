#!/usr/bin/env python3

from tools.logging.icdev_logger import get_logger
# CUI // SP-CTI
"""MLflow Batch Exporter — Export SQLite spans to MLflow (D283).

Supports deferred upload for air-gapped → connected transitions.
Reads from otel_spans table, exports to MLflow tracking server via REST API.

Usage:
    from tools.observability.mlflow_exporter import MLflowExporter
    exporter = MLflowExporter(tracking_uri="http://localhost:5001")
    exporter.export_pending()

CLI:
    python tools/observability/mlflow_exporter.py --export --json
    python tools/observability/mlflow_exporter.py --status --json
"""

import argparse
import json
import sqlite3
from tools.db.storage import get_connection, is_pg
from pathlib import Path
from typing import Dict, List, Optional

logger = get_logger("icdev.observability.mlflow_exporter")

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"

# High-watermark state. otel_spans is append-only (D283) and carries no
# "exported" column, so we track export progress out-of-band in a tiny
# singleton-row table keyed by source. On each pass we only read spans whose
# start_time is strictly greater than the persisted watermark, then advance
# the watermark to the batch max. This makes export_pending idempotent —
# without it, every call re-created MLflow runs for the same spans (unbounded
# duplicates).
_STATE_TABLE = "mlflow_export_state"
_STATE_KEY = "otel_spans"

# Backend-appropriate DB error tuple. Reads route through get_connection,
# which targets PostgreSQL by default; PG raises psycopg2.Error subclasses
# that sqlite3.Error does not cover.
try:  # pragma: no cover - import guard
    import psycopg2

    _DB_ERRORS: tuple = (sqlite3.Error, psycopg2.Error)
except ImportError:  # sqlite-only install
    _DB_ERRORS = (sqlite3.Error,)


def _db_file_missing(db_path: Path) -> bool:
    """Whether the SQLite file gate should short-circuit I/O.

    Only meaningful when the effective backend is SQLite. Under the
    PG-primary runtime, reads go through get_connection (PostgreSQL) and the
    .db path is ignored, so the file-existence gate must NOT apply.
    """
    return not is_pg() and not db_path.exists()

try:
    import mlflow

    HAS_MLFLOW = True
except ImportError:
    HAS_MLFLOW = False
    mlflow = None


class MLflowExporter:
    """Batch export SQLite spans to MLflow (D283).

    Reads unexported spans from otel_spans, creates MLflow traces.
    Marks spans as exported to prevent re-export.
    """

    def __init__(
        self,
        tracking_uri: Optional[str] = None,
        experiment_name: str = "icdev-traces",
        db_path: Optional[Path] = None,
    ):
        self._db_path = db_path or DB_PATH
        self._experiment_name = experiment_name

        import os

        self._tracking_uri = tracking_uri or os.environ.get("ICDEV_MLFLOW_TRACKING_URI", "")

        if HAS_MLFLOW and self._tracking_uri:
            mlflow.set_tracking_uri(self._tracking_uri)
            mlflow.set_experiment(self._experiment_name)

    def export_pending(self, batch_size: int = 100) -> Dict:
        """Export unexported spans to MLflow.

        Returns:
            Dict with export stats.
        """
        if not HAS_MLFLOW:
            return {"status": "skipped", "reason": "mlflow not installed"}
        if not self._tracking_uri:
            return {"status": "skipped", "reason": "no tracking URI configured"}
        if _db_file_missing(self._db_path):
            return {"status": "skipped", "reason": "database not found"}

        spans = self._read_unexported_spans(batch_size)
        if not spans:
            return {"status": "ok", "exported": 0, "message": "no pending spans"}

        exported = 0
        errors = 0

        # Group spans by trace_id
        traces: Dict[str, List[Dict]] = {}
        for span in spans:
            tid = span["trace_id"]
            if tid not in traces:
                traces[tid] = []
            traces[tid].append(span)

        for trace_id, trace_spans in traces.items():
            try:
                self._export_trace(trace_id, trace_spans)
                exported += len(trace_spans)
            except Exception as e:
                logger.error("Failed to export trace %s: %s", trace_id, e)
                errors += len(trace_spans)

        # Advance the watermark past the whole batch we just processed so the
        # next pass never re-reads these spans (prevents duplicate MLflow runs).
        # start_time is a lexicographically-sortable ISO-8601 TEXT column.
        batch_max = max(
            (s.get("start_time") for s in spans if s.get("start_time")),
            default=None,
        )
        if batch_max is not None:
            self._set_watermark(batch_max)

        return {
            "status": "ok",
            "exported": exported,
            "errors": errors,
            "traces": len(traces),
        }

    def _ensure_state_table(self, conn) -> None:
        """Create the singleton watermark table if absent (idempotent DDL)."""
        conn.execute(
            f"""CREATE TABLE IF NOT EXISTS {_STATE_TABLE} (
                    id TEXT PRIMARY KEY,
                    last_start_time TEXT,
                    updated_at TEXT
                )"""
        )
        conn.commit()

    def _get_watermark(self) -> Optional[str]:
        """Return the last exported span start_time, or None if never exported."""
        try:
            conn = get_connection(db_path=str(self._db_path))
            self._ensure_state_table(conn)
            row = conn.execute(
                f"SELECT last_start_time FROM {_STATE_TABLE} WHERE id = %s",  # nosec B608 -- _STATE_TABLE is an internal constant
                (_STATE_KEY,),
            ).fetchone()
            conn.close()
            if not row:
                return None
            try:
                return row["last_start_time"]
            except (TypeError, KeyError, IndexError):
                return row[0]
        except _DB_ERRORS as e:
            logger.error("Failed to read export watermark: %s", e)
            return None

    def _set_watermark(self, watermark: str) -> None:
        """Persist the high-watermark (replace the single state row)."""
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc).isoformat()
        try:
            conn = get_connection(db_path=str(self._db_path))
            self._ensure_state_table(conn)
            conn.execute(
                f"DELETE FROM {_STATE_TABLE} WHERE id = %s",  # nosec B608 -- _STATE_TABLE is an internal constant
                (_STATE_KEY,),
            )
            conn.execute(
                f"""INSERT INTO {_STATE_TABLE} (id, last_start_time, updated_at)
                    VALUES (%s, %s, %s)""",  # nosec B608 -- _STATE_TABLE is an internal constant
                (_STATE_KEY, watermark, now),
            )
            conn.commit()
            conn.close()
        except _DB_ERRORS as e:
            logger.error("Failed to persist export watermark: %s", e)

    def _read_unexported_spans(self, limit: int) -> List[Dict]:
        """Read spans newer than the persisted high-watermark."""
        watermark = self._get_watermark()
        try:
            conn = get_connection(db_path=str(self._db_path))
            if watermark is None:
                rows = conn.execute(
                    """SELECT * FROM otel_spans
                       ORDER BY start_time ASC
                       LIMIT %s""",
                    (limit,),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT * FROM otel_spans
                       WHERE start_time > %s
                       ORDER BY start_time ASC
                       LIMIT %s""",
                    (watermark, limit),
                ).fetchall()
            conn.close()
            return [dict(row) for row in rows]
        except _DB_ERRORS as e:
            logger.error("Failed to read spans: %s", e)
            return []

    def _export_trace(self, trace_id: str, spans: List[Dict]) -> None:
        """Export a single trace (group of spans) to MLflow."""
        if not HAS_MLFLOW:
            return

        with mlflow.start_run(run_name=f"trace-{trace_id[:12]}"):
            for span in spans:
                attrs = json.loads(span.get("attributes", "{}"))
                json.loads(span.get("events", "[]"))

                mlflow.log_param(f"span.{span['id']}.name", span["name"])
                mlflow.log_metric(f"span.{span['id']}.duration_ms", span.get("duration_ms", 0))

                # Log key attributes
                for key, val in attrs.items():
                    safe_key = key.replace(".", "_")[:250]
                    try:
                        mlflow.log_param(f"attr.{safe_key}", str(val)[:500])
                    except Exception:
                        pass

    def get_status(self) -> Dict:
        """Return export status summary."""
        result = {
            "mlflow_available": HAS_MLFLOW,
            "tracking_uri": self._tracking_uri or "(not configured)",
            "experiment": self._experiment_name,
            "db_path": str(self._db_path),
        }

        if not _db_file_missing(self._db_path):
            try:
                conn = get_connection(db_path=str(self._db_path))
                count = conn.execute("SELECT COUNT(*) FROM otel_spans").fetchone()[0]
                conn.close()
                result["total_spans"] = count
            except Exception:
                result["total_spans"] = -1

        return result


def main():
    parser = argparse.ArgumentParser(description="MLflow Span Exporter (D283)")
    parser.add_argument("--export", action="store_true", help="Export pending spans")
    parser.add_argument("--status", action="store_true", help="Show export status")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--batch-size", type=int, default=100, help="Batch size")
    args = parser.parse_args()

    exporter = MLflowExporter()

    if args.status:
        result = exporter.get_status()
    elif args.export:
        result = exporter.export_pending(batch_size=args.batch_size)
    else:
        result = exporter.get_status()

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        for k, v in result.items():
            print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
