# CUI // SP-CTI
"""Scale Connection Pool — per-connector-type reusable connection pools (D-SC-2).

Maintains pools of connected connector instances keyed by connector_name.
Prevents one noisy connector type from starving others.
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import hashlib
import json
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from tools.databridge.connector import DataConnector

logger = get_logger("databridge.scale.connection_pool")


@dataclass
class PooledConnector:
    """Wrapper tracking connector state within the pool."""

    connector: DataConnector
    connector_name: str
    config_hash: str
    last_used: float = field(default_factory=time.monotonic)
    in_use: bool = False


class ConnectionPool:
    """Per-connector-type connection pool (D-SC-2).

    Pool key = connector_name. Each type has independent max_per_type limit.
    """

    def __init__(
        self,
        max_per_type: int = 5,
        idle_timeout_seconds: int = 300,
        health_check_on_acquire: bool = False,
    ):
        self._max_per_type = max_per_type
        self._idle_timeout = idle_timeout_seconds
        self._health_check = health_check_on_acquire

        self._pools: Dict[str, List[PooledConnector]] = {}
        self._lock = threading.Lock()
        self._condition = threading.Condition(self._lock)
        self._reaper_thread: Optional[threading.Thread] = None
        self._reaper_stop = threading.Event()
        self._stats = {
            "acquires": 0,
            "releases": 0,
            "creates": 0,
            "evictions": 0,
            "health_check_failures": 0,
        }

    def acquire(
        self,
        connector_name: str,
        config: Dict[str, Any],
        connector_factory: Any = None,
        timeout: float = 30.0,
    ) -> DataConnector:
        """Get a connected connector from the pool, or create one.

        Args:
            connector_name: Type key (e.g. 'postgresql', 'salesforce')
            config: Connection config dict (used for hash matching)
            connector_factory: Callable that returns a new DataConnector instance
            timeout: Max wait if pool is full

        Returns:
            Connected DataConnector instance

        Raises:
            TimeoutError: If pool is full and no connector available in time
            RuntimeError: If connection or health check fails
        """
        cfg_hash = _config_hash(config)
        deadline = time.monotonic() + timeout

        with self._condition:
            while True:
                pool = self._pools.setdefault(connector_name, [])

                idle = self._find_idle(pool, cfg_hash)
                if idle is not None:
                    idle.in_use = True
                    idle.last_used = time.monotonic()
                    self._stats["acquires"] += 1
                    return idle.connector

                # Create new if under limit
                if len(pool) < self._max_per_type:
                    return self._create_and_add(
                        pool,
                        connector_name,
                        config,
                        cfg_hash,
                        connector_factory,
                    )

                # Pool full — wait for release
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(
                        f"Connection pool for '{connector_name}' full "
                        f"({self._max_per_type}/{self._max_per_type}), "
                        f"timed out after {timeout}s"
                    )
                self._condition.wait(timeout=remaining)

    def _find_idle(self, pool: List[PooledConnector], cfg_hash: str) -> Optional[PooledConnector]:
        """Find an idle connector matching cfg_hash (caller holds lock).

        Runs optional health check; removes unhealthy entries.
        """
        for entry in pool:
            if entry.in_use or entry.config_hash != cfg_hash:
                continue
            if self._health_check and not self._passes_health_check(pool, entry):
                continue
            return entry
        return None

    def _passes_health_check(self, pool: List[PooledConnector], entry: PooledConnector) -> bool:
        """Return True if entry passes health check; remove it otherwise."""
        try:
            health = entry.connector.health_check()
            if health.get("status") == "healthy":
                return True
        except Exception:
            pass
        self._stats["health_check_failures"] += 1
        self._remove_entry(pool, entry)
        return False

    def _create_and_add(
        self,
        pool: List[PooledConnector],
        connector_name: str,
        config: Dict[str, Any],
        cfg_hash: str,
        connector_factory: Any,
    ) -> DataConnector:
        """Create a new connector outside lock, add to pool, return it."""
        self._condition.release()
        try:
            connector = self._create_connector(connector_name, config, connector_factory)
        finally:
            self._condition.acquire()

        entry = PooledConnector(
            connector=connector,
            connector_name=connector_name,
            config_hash=cfg_hash,
            in_use=True,
        )
        pool.append(entry)
        self._stats["acquires"] += 1
        self._stats["creates"] += 1
        return connector

    def release(self, connector: DataConnector) -> None:
        """Return connector to pool for reuse."""
        with self._condition:
            for pool in self._pools.values():
                for entry in pool:
                    if entry.connector is connector:
                        entry.in_use = False
                        entry.last_used = time.monotonic()
                        self._stats["releases"] += 1
                        self._condition.notify_all()
                        return

            # Not found in pool — just disconnect
            logger.warning("Released connector not found in pool, disconnecting")
            try:
                connector.disconnect()
            except Exception:
                pass

    def start_reaper(self) -> None:
        """Start background reaper thread for idle eviction."""
        if self._reaper_thread and self._reaper_thread.is_alive():
            return
        self._reaper_stop.clear()
        self._reaper_thread = threading.Thread(
            target=self._reaper_loop,
            name="scale-pool-reaper",
            daemon=True,
        )
        self._reaper_thread.start()

    def stop_reaper(self) -> None:
        """Stop the reaper thread."""
        self._reaper_stop.set()
        if self._reaper_thread:
            self._reaper_thread.join(timeout=5.0)

    def drain(self) -> int:
        """Disconnect all pooled connectors. Returns count drained."""
        count = 0
        with self._lock:
            for pool in self._pools.values():
                for entry in pool:
                    try:
                        entry.connector.disconnect()
                    except Exception:
                        pass
                    count += 1
                pool.clear()
            self._pools.clear()
        return count

    def stats(self) -> Dict[str, Any]:
        """Per-type stats: total, in_use, idle."""
        with self._lock:
            per_type = {}
            for name, pool in self._pools.items():
                in_use = sum(1 for e in pool if e.in_use)
                per_type[name] = {
                    "total": len(pool),
                    "in_use": in_use,
                    "idle": len(pool) - in_use,
                }
            return {
                "max_per_type": self._max_per_type,
                "idle_timeout_seconds": self._idle_timeout,
                "health_check_on_acquire": self._health_check,
                "pools": per_type,
                **self._stats,
            }

    # -- internal --

    def _create_connector(
        self,
        connector_name: str,
        config: Dict[str, Any],
        connector_factory: Any,
    ) -> DataConnector:
        """Create and connect a new connector instance."""
        if connector_factory:
            connector = connector_factory()
        else:
            from tools.databridge.registry import get_connector_instance

            connector = get_connector_instance(connector_name)
            if not connector:
                raise RuntimeError(f"Connector '{connector_name}' not found in registry")

        connected = connector.connect(config)
        if not connected:
            raise RuntimeError(f"Failed to connect '{connector_name}'")
        return connector

    def _remove_entry(self, pool: List[PooledConnector], entry: PooledConnector) -> None:
        """Disconnect and remove a pooled entry (caller holds lock)."""
        try:
            entry.connector.disconnect()
        except Exception:
            pass
        pool.remove(entry)

    def _reaper_loop(self) -> None:
        """Periodically evict idle connectors."""
        interval = max(self._idle_timeout / 2, 10.0)
        while not self._reaper_stop.wait(timeout=interval):
            self._evict_idle()

    def _evict_idle(self) -> int:
        """Disconnect connectors idle beyond timeout. Returns count evicted."""
        evicted = 0
        now = time.monotonic()
        with self._lock:
            for pool in self._pools.values():
                to_remove = []
                for entry in pool:
                    if not entry.in_use and (now - entry.last_used) > self._idle_timeout:
                        to_remove.append(entry)
                for entry in to_remove:
                    try:
                        entry.connector.disconnect()
                    except Exception:
                        pass
                    pool.remove(entry)
                    evicted += 1
        if evicted:
            self._stats["evictions"] += evicted
            logger.info("Evicted %d idle connectors", evicted)
        return evicted


def _config_hash(config: Dict[str, Any]) -> str:
    """SHA-256 hash of config dict for cache key matching."""
    raw = json.dumps(config, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
