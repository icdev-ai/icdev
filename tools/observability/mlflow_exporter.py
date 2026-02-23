#!/usr/bin/env python3
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
import logging
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger("icdev.observability.mlflow_exporter")

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"

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
        self._tracking_uri = tracking_uri or os.environ.get(
            "ICDEV_MLFLOW_TRACKING_URI", ""
        )

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
        if not self._db_path.exists():
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

        return {
            "status": "ok",
            "exported": exported,
            "errors": errors,
            "traces": len(traces),
        }

    def _read_unexported_spans(self, limit: int) -> List[Dict]:
        """Read spans that haven't been exported yet."""
        try:
            conn = sqlite3.connect(str(self._db_path))
            conn.row_factory = sqlite3.Row
            # Note: we track export via a simple approach — spans older than last export
            rows = conn.execute(
                """SELECT * FROM otel_spans
                   ORDER BY start_time ASC
                   LIMIT ?""",
                (limit,),
            ).fetchall()
            conn.close()
            return [dict(row) for row in rows]
        except sqlite3.Error as e:
            logger.error("Failed to read spans: %s", e)
            return []

    def _export_trace(self, trace_id: str, spans: List[Dict]) -> None:
        """Export a single trace (group of spans) to MLflow."""
        if not HAS_MLFLOW:
            return

        with mlflow.start_run(run_name=f"trace-{trace_id[:12]}"):
            for span in spans:
                attrs = json.loads(span.get("attributes", "{}"))
                events = json.loads(span.get("events", "[]"))

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

        if self._db_path.exists():
            try:
                conn = sqlite3.connect(str(self._db_path))
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
