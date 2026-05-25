# CUI // SP-CTI
"""Scale Engine — horizontal scaling orchestrator for DataBridge (D-SC-1).

Ties together worker pool, connection pool, write batcher, backpressure,
and chunked pipeline. Provides drop-in replacement functions for
sync_engine.sync_read and sync_engine.sync_write.

Usage:
    from tools.databridge.scale.engine import get_scale_engine
    engine = get_scale_engine()
    engine.start()
    result = engine.scaled_sync_read_blocking("conn-123", "users")
    engine.stop()
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import json
import logging
import threading
import time
from concurrent.futures import Future
from pathlib import Path
from typing import Any, Dict, Optional

from tools.databridge.scale.backpressure import BackpressureController
from tools.databridge.scale.connection_pool import ConnectionPool
from tools.databridge.scale.worker_pool import ScaleWorkerPool
from tools.databridge.scale.write_batcher import WriteBatcher

logger = get_logger("databridge.scale.engine")

DB_PATH = Path(__file__).resolve().parents[3] / "data" / "icdev.db"


class ScaleEngine:
    """Horizontal scaling orchestrator for DataBridge (D-SC-1).

    Wraps existing sync_read/sync_write in a thread pool with connection
    pooling and batched writes. All existing sync behavior is preserved.
    """

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        db_path: Optional[str] = None,
    ):
        cfg = config or {}
        self._db_path = db_path or str(DB_PATH)

        wp_cfg = cfg.get("worker_pool", {})
        cp_cfg = cfg.get("connection_pool", {})
        wb_cfg = cfg.get("write_batcher", {})
        bp_cfg = cfg.get("backpressure", {})

        # PG handles concurrent writes natively (MVCC) — increase defaults
        try:
            from tools.db.storage import get_backend

            _is_pg = get_backend() == "postgresql"
        except ImportError:
            _is_pg = False
        _def_workers = 20 if _is_pg else 10
        _def_concurrent = 16 if _is_pg else 5

        self._worker_pool = ScaleWorkerPool(
            max_workers=wp_cfg.get("max_workers", _def_workers),
            max_concurrent_syncs=wp_cfg.get("max_concurrent_syncs", _def_concurrent),
            sync_timeout_seconds=wp_cfg.get("sync_timeout_seconds", 300),
        )
        self._conn_pool = ConnectionPool(
            max_per_type=cp_cfg.get("max_per_type", 5),
            idle_timeout_seconds=cp_cfg.get("idle_timeout_seconds", 300),
            health_check_on_acquire=cp_cfg.get("health_check_on_acquire", False),
        )
        self._write_batcher = WriteBatcher(
            db_path=self._db_path,
            flush_interval_ms=wb_cfg.get("flush_interval_ms", 500),
            max_buffer_size=wb_cfg.get("max_buffer_size", 100),
        )
        self._backpressure = BackpressureController(
            memory_threshold_mb=bp_cfg.get("memory_threshold_mb", 400),
            pause_on_threshold=bp_cfg.get("pause_on_threshold", True),
        )
        self._started = False

    def start(self) -> None:
        """Start write batcher and connection pool reaper."""
        if self._started:
            return
        self._write_batcher.start()
        self._conn_pool.start_reaper()
        self._started = True
        logger.info("Scale engine started")

    def stop(self) -> None:
        """Graceful shutdown: drain worker pool, flush batcher, drain conn pool."""
        if not self._started:
            return
        logger.info("Stopping scale engine...")
        self._worker_pool.shutdown(wait=True, timeout=30.0)
        self._write_batcher.stop()
        self._conn_pool.stop_reaper()
        drained = self._conn_pool.drain()
        self._started = False
        logger.info("Scale engine stopped (drained %d connections)", drained)

    def scaled_sync_read(
        self,
        connection_id: str,
        table_name: str,
        query: str = "",
        limit: Optional[int] = None,
        incremental_key: str = "",
        incremental_value: Any = None,
        db_path: Optional[str] = None,
    ) -> Future:
        """Submit sync_read as a future. Returns Future[Dict[str, Any]]."""
        return self._worker_pool.submit_sync(
            self._do_sync_read,
            connection_id,
            table_name,
            query=query,
            limit=limit,
            incremental_key=incremental_key,
            incremental_value=incremental_value,
            db_path=db_path or self._db_path,
        )

    def scaled_sync_write(
        self,
        connection_id: str,
        table_name: str,
        data: Any,
        db_path: Optional[str] = None,
    ) -> Future:
        """Submit sync_write as a future. Returns Future[Dict[str, Any]]."""
        return self._worker_pool.submit_sync(
            self._do_sync_write,
            connection_id,
            table_name,
            data,
            db_path=db_path or self._db_path,
        )

    def scaled_sync_read_blocking(
        self,
        connection_id: str,
        table_name: str,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Blocking version — same return shape as sync_engine.sync_read."""
        future = self.scaled_sync_read(connection_id, table_name, **kwargs)
        return self._worker_pool.get_result(future)

    def scaled_sync_write_blocking(
        self,
        connection_id: str,
        table_name: str,
        data: Any,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Blocking version — same return shape as sync_engine.sync_write."""
        future = self.scaled_sync_write(connection_id, table_name, data, **kwargs)
        return self._worker_pool.get_result(future)

    def status(self) -> Dict[str, Any]:
        """Aggregate status from all components."""
        return {
            "started": self._started,
            "worker_pool": self._worker_pool.status(),
            "connection_pool": self._conn_pool.stats(),
            "write_batcher": self._write_batcher.stats(),
            "backpressure": self._backpressure.status(),
        }

    @property
    def backpressure(self) -> BackpressureController:
        """Access backpressure controller (for stream integration)."""
        return self._backpressure

    @property
    def connection_pool(self) -> ConnectionPool:
        """Access connection pool directly."""
        return self._conn_pool

    # -- internal sync implementations --

    def _do_sync_read(
        self,
        connection_id: str,
        table_name: str,
        query: str = "",
        limit: Optional[int] = None,
        incremental_key: str = "",
        incremental_value: Any = None,
        db_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Internal: mirrors sync_engine.sync_read but uses conn pool + write batcher."""
        from tools.databridge.connection_manager import (
            get_connection,
            resolve_secret,
            update_connection,
        )
        from tools.databridge.connector import ConnectorRequest
        from tools.databridge.schema_engine import register_schema

        db = db_path or self._db_path
        conn_data = get_connection(connection_id, db_path=db)
        if not conn_data:
            return {"status": "error", "message": "Connection not found"}

        start = time.time()
        connector = None
        try:
            import yaml

            config = yaml.safe_load(conn_data["config_yaml"] or "{}")
            if conn_data.get("auth_secret_ref"):
                secret = resolve_secret(conn_data["auth_secret_ref"])
                if secret:
                    config["_resolved_secret"] = secret

            connector_name = conn_data["connector_name"]

            # Acquire from pool instead of creating new
            connector = self._conn_pool.acquire(connector_name, config, timeout=30.0)

            # Schema inference
            try:
                schema = connector.infer_schema(table_name)
                register_schema(connection_id, table_name, schema, db_path=db)
            except Exception as exc:
                logger.warning("Schema inference failed: %s", exc)

            request = ConnectorRequest(
                connection_id=connection_id,
                table_name=table_name,
                query=query,
                limit=limit,
                incremental_key=incremental_key,
                incremental_value=incremental_value,
                sync_direction="read",
            )

            response = connector.read(request)

            # Release back to pool (not disconnect)
            self._conn_pool.release(connector)
            connector = None

            duration_ms = int((time.time() - start) * 1000)

            update_connection(
                connection_id,
                {"status": "connected", "last_sync": _now_iso()},
                db_path=db,
            )

            # Batched writes instead of direct DB
            self._write_batcher.enqueue_sync_log(
                connection_id=connection_id,
                sync_direction="read",
                table_name=table_name,
                rows_read=response.row_count,
                bytes_transferred=response.bytes_transferred,
                sync_duration_ms=duration_ms,
                incremental_key=incremental_key,
                incremental_value=str(incremental_value or ""),
                error_details="; ".join(response.errors) if response.errors else "",
            )
            self._write_batcher.enqueue_audit(
                action="databridge_sync_completed",
                details=json.dumps(
                    {
                        "connection_id": connection_id,
                        "table": table_name,
                        "direction": "read",
                        "rows": response.row_count,
                        "duration_ms": duration_ms,
                        "scaled": True,
                    }
                ),
            )

            return {
                "status": response.status,
                "data": response.data,
                "row_count": response.row_count,
                "has_more": response.has_more,
                "next_offset": response.next_offset,
                "duration_ms": duration_ms,
                "errors": response.errors,
            }

        except Exception as exc:
            duration_ms = int((time.time() - start) * 1000)
            logger.error("Scaled sync read failed for %s: %s", connection_id, exc)

            # Release connector back to pool if we still hold it
            if connector:
                try:
                    self._conn_pool.release(connector)
                except Exception:
                    pass

            self._write_batcher.enqueue_sync_log(
                connection_id=connection_id,
                sync_direction="read",
                table_name=table_name,
                sync_duration_ms=duration_ms,
                error_details=str(exc),
            )
            update_connection(connection_id, {"status": "error"}, db_path=db)

            return {
                "status": "error",
                "message": str(exc),
                "duration_ms": duration_ms,
            }

    def _do_sync_write(
        self,
        connection_id: str,
        table_name: str,
        data: Any,
        db_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Internal: mirrors sync_engine.sync_write but uses conn pool + write batcher."""
        from tools.databridge.connection_manager import (
            get_connection,
            resolve_secret,
            update_connection,
        )
        from tools.databridge.connector import ConnectorRequest

        db = db_path or self._db_path
        conn_data = get_connection(connection_id, db_path=db)
        if not conn_data:
            return {"status": "error", "message": "Connection not found"}

        start = time.time()
        connector = None
        try:
            import yaml

            config = yaml.safe_load(conn_data["config_yaml"] or "{}")
            if conn_data.get("auth_secret_ref"):
                secret = resolve_secret(conn_data["auth_secret_ref"])
                if secret:
                    config["_resolved_secret"] = secret

            connector_name = conn_data["connector_name"]
            connector = self._conn_pool.acquire(connector_name, config, timeout=30.0)

            if not connector.capabilities.supports_write:
                self._conn_pool.release(connector)
                raise RuntimeError(f"Connector '{connector_name}' does not support write")

            request = ConnectorRequest(
                connection_id=connection_id,
                table_name=table_name,
                sync_direction="write",
            )
            response = connector.write(request, data)

            self._conn_pool.release(connector)
            connector = None

            duration_ms = int((time.time() - start) * 1000)

            update_connection(
                connection_id,
                {"status": "connected", "last_sync": _now_iso()},
                db_path=db,
            )

            self._write_batcher.enqueue_sync_log(
                connection_id=connection_id,
                sync_direction="write",
                table_name=table_name,
                rows_written=response.row_count,
                bytes_transferred=response.bytes_transferred,
                sync_duration_ms=duration_ms,
                error_details="; ".join(response.errors) if response.errors else "",
            )

            return {
                "status": response.status,
                "rows_written": response.row_count,
                "duration_ms": duration_ms,
                "errors": response.errors,
            }

        except Exception as exc:
            duration_ms = int((time.time() - start) * 1000)
            logger.error("Scaled sync write failed for %s: %s", connection_id, exc)

            if connector:
                try:
                    self._conn_pool.release(connector)
                except Exception:
                    pass

            self._write_batcher.enqueue_sync_log(
                connection_id=connection_id,
                sync_direction="write",
                table_name=table_name,
                sync_duration_ms=duration_ms,
                error_details=str(exc),
            )

            return {
                "status": "error",
                "message": str(exc),
                "duration_ms": duration_ms,
            }


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_ENGINE: Optional[ScaleEngine] = None
_ENGINE_LOCK = threading.Lock()


def get_scale_engine(db_path: Optional[str] = None) -> ScaleEngine:
    """Get or create the singleton ScaleEngine from config."""
    global _ENGINE
    with _ENGINE_LOCK:
        if _ENGINE is None:
            config = _load_scale_config()
            _ENGINE = ScaleEngine(config=config, db_path=db_path)
        return _ENGINE


def shutdown_scale_engine() -> None:
    """Shutdown the singleton."""
    global _ENGINE
    with _ENGINE_LOCK:
        if _ENGINE:
            _ENGINE.stop()
            _ENGINE = None


def _load_scale_config() -> Dict[str, Any]:
    """Load scale config from args/databridge_config.yaml."""
    try:
        import yaml

        config_path = Path(__file__).resolve().parents[3] / "args" / "databridge_config.yaml"
        if config_path.exists():
            with open(config_path, "r") as f:
                data = yaml.safe_load(f) or {}
            return data.get("databridge", {}).get("scale", {})
    except Exception as exc:
        logger.warning("Failed to load scale config: %s", exc)
    return {}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="DataBridge Scale Engine")
    parser.add_argument("--status", action="store_true", help="Show engine status")
    parser.add_argument("--shutdown", action="store_true", help="Shutdown engine")
    parser.add_argument("--json", action="store_true", help="JSON output")

    args = parser.parse_args()

    if args.status:
        engine = get_scale_engine()
        engine.start()
        result = engine.status()
        engine.stop()
        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            print(result)
    elif args.shutdown:
        shutdown_scale_engine()
        print("Scale engine shut down")
    else:
        parser.print_help()
