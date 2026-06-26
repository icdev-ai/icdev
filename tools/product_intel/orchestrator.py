"""Product Intelligence universal orchestrator — runs all engines and persists results."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from tools.db.storage import get_connection
from tools.product_intel.engine_registry import EngineRegistry

_REQUIRED_RESULT_KEYS = {"run_id", "status", "engines_run", "engines_failed", "total_signals", "results"}


def _ensure_table(conn) -> None:
    """Create the product_intel_runs table idempotently if it is missing."""
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS product_intel_runs (
                id                  TEXT PRIMARY KEY,
                started_at          TEXT,
                completed_at        TEXT,
                engines_run         TEXT,
                engines_failed      TEXT,
                total_signals       INTEGER,
                total_gaps          INTEGER,
                total_dossiers      INTEGER,
                federation_routes   INTEGER,
                result_json         TEXT,
                status              TEXT,
                classification      TEXT DEFAULT 'CUI // SP-CTI'
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_pi_runs_started_at "
            "ON product_intel_runs(started_at)"
        )
    except Exception:
        pass


class ProductIntelOrchestrator:
    def __init__(
        self,
        registry: EngineRegistry | None = None,
        db_path: str | None = None,
    ) -> None:
        self._registry = registry or EngineRegistry()
        self._db_path = db_path

    def run_all(self, dry_run: bool = False) -> dict[str, Any]:
        """Invoke all enabled engines and return a consolidated result dict.

        Inserts one row to product_intel_runs unless dry_run=True.
        Never raises — engine failures are captured in engines_failed.
        """
        run_id = str(uuid.uuid4())
        started_at = datetime.now(timezone.utc).isoformat()

        results: list[dict[str, Any]] = []
        engines_run: list[str] = []
        engines_failed: list[str] = []
        total_signals = 0

        for engine_cfg in self._registry.list_engines():
            engine_result = self._registry.invoke(engine_cfg)
            results.append(
                {
                    "name": engine_result.name,
                    "status": engine_result.status,
                    "duration_ms": engine_result.duration_ms,
                    "signals_count": engine_result.signals_count,
                }
            )
            if engine_result.status == "ok":
                engines_run.append(engine_result.name)
                total_signals += engine_result.signals_count
            elif engine_result.status == "failed":
                engines_run.append(engine_result.name)
                engines_failed.append(engine_result.name)

        completed_at = datetime.now(timezone.utc).isoformat()
        consolidated: dict[str, Any] = {
            "run_id": run_id,
            "status": "completed",
            "started_at": started_at,
            "completed_at": completed_at,
            "engines_run": engines_run,
            "engines_failed": engines_failed,
            "total_signals": total_signals,
            "results": results,
        }

        with get_connection(self._db_path) as conn:
            _ensure_table(conn)
            if not dry_run:
                conn.execute(
                    """INSERT INTO product_intel_runs
                       (id, started_at, completed_at, engines_run, engines_failed,
                        total_signals, total_gaps, total_dossiers, federation_routes,
                        result_json, status)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        run_id,
                        started_at,
                        completed_at,
                        json.dumps(engines_run),
                        json.dumps(engines_failed),
                        total_signals,
                        0,
                        0,
                        0,
                        json.dumps(consolidated),
                        "completed",
                    ),
                )

        return consolidated


if __name__ == "__main__":
    import argparse
    import json as _json

    parser = argparse.ArgumentParser(description="Product Intelligence universal orchestrator")
    parser.add_argument("--dry-run", action="store_true", help="List engines without DB writes")
    parser.add_argument("--run-all", action="store_true", help="Run all engines and persist result")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    args = parser.parse_args()

    orch = ProductIntelOrchestrator()
    result = orch.run_all(dry_run=args.dry_run)
    print(_json.dumps(result, indent=2))
