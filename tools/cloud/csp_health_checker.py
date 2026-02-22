#!/usr/bin/env python3
# CUI // SP-CTI
"""CSP Health Checker — health check all cloud service provider services.

Integrates with heartbeat daemon (D230). Checks all CSP services
(secrets, storage, KMS, monitoring, IAM, registry) and reports health.
Stores status history in cloud_provider_status table.

CLI: --check-all, --check-service <name>, --history [--hours N], --json
"""

import argparse
import json
import logging
import sqlite3
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger("icdev.cloud.health")

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DEFAULT_DB_PATH = BASE_DIR / "data" / "icdev.db"

try:
    import yaml
except ImportError:
    yaml = None


class CSPHealthChecker:
    """Health check all cloud service provider services."""

    # All services that CSPProviderFactory can provide
    SERVICE_NAMES = ["secrets", "storage", "kms", "monitoring", "iam", "registry"]

    def __init__(self, config_path: Optional[str] = None, db_path: Optional[str] = None):
        self._config_path = config_path or str(BASE_DIR / "args" / "cloud_config.yaml")
        self._db_path = str(db_path) if db_path else str(DEFAULT_DB_PATH)
        self._factory = None

    def _get_factory(self):
        """Lazy-load CSPProviderFactory."""
        if self._factory is None:
            from tools.cloud.provider_factory import CSPProviderFactory
            self._factory = CSPProviderFactory(config_path=self._config_path)
        return self._factory

    def _get_provider_for_service(self, service: str):
        """Get provider instance for a given service name."""
        factory = self._get_factory()
        getter_map = {
            "secrets": factory.get_secrets_provider,
            "storage": factory.get_storage_provider,
            "kms": factory.get_kms_provider,
        }
        # Monitoring, IAM, Registry use the new providers via extended factory
        if hasattr(factory, "get_monitoring_provider"):
            getter_map["monitoring"] = factory.get_monitoring_provider
        if hasattr(factory, "get_iam_provider"):
            getter_map["iam"] = factory.get_iam_provider
        if hasattr(factory, "get_registry_provider"):
            getter_map["registry"] = factory.get_registry_provider

        getter = getter_map.get(service)
        if getter:
            return getter()
        return None

    def _record_status(self, provider_name: str, service: str, status: str,
                       latency_ms: float, error_message: str = ""):
        """Record health check status to cloud_provider_status table."""
        try:
            conn = sqlite3.connect(self._db_path)
            # Check if table exists (migration 007 may not have run yet)
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='cloud_provider_status'"
            )
            if not cursor.fetchone():
                logger.debug("cloud_provider_status table not found — skipping history recording")
                conn.close()
                return
            entry_id = f"csp-{uuid.uuid4().hex[:12]}"
            conn.execute(
                "INSERT INTO cloud_provider_status "
                "(id, provider, service, status, latency_ms, error_message, checked_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (entry_id, provider_name, service, status, latency_ms,
                 error_message or None,
                 datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.debug("Failed to record CSP status: %s", e)

    def check_service(self, service: str) -> Dict:
        """Check a specific CSP service and return status."""
        result = {
            "service": service,
            "provider": "unknown",
            "status": "unavailable",
            "latency_ms": 0.0,
            "error": None,
        }

        provider = self._get_provider_for_service(service)
        if provider is None:
            result["error"] = f"No provider configured for service: {service}"
            result["provider"] = "none"
            self._record_status("none", service, "unavailable", 0.0, result["error"])
            return result

        result["provider"] = provider.provider_name
        start = time.monotonic()
        try:
            available = provider.check_availability()
            elapsed_ms = (time.monotonic() - start) * 1000
            result["latency_ms"] = round(elapsed_ms, 2)
            result["status"] = "healthy" if available else "unavailable"
        except Exception as e:
            elapsed_ms = (time.monotonic() - start) * 1000
            result["latency_ms"] = round(elapsed_ms, 2)
            result["status"] = "error"
            result["error"] = str(e)

        self._record_status(
            result["provider"], service, result["status"],
            result["latency_ms"], result.get("error", ""),
        )
        return result

    def check_all(self) -> Dict:
        """Check all CSP services and return aggregate status."""
        factory = self._get_factory()
        results = {}
        healthy_count = 0
        total_count = 0

        for service in self.SERVICE_NAMES:
            total_count += 1
            check = self.check_service(service)
            results[service] = check
            if check["status"] == "healthy":
                healthy_count += 1

        overall = "healthy" if healthy_count == total_count else (
            "degraded" if healthy_count > 0 else "unhealthy"
        )

        return {
            "overall_status": overall,
            "global_provider": factory.global_provider,
            "region": factory.region,
            "impact_level": factory.impact_level,
            "air_gapped": factory.air_gapped,
            "healthy_count": healthy_count,
            "total_count": total_count,
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "services": results,
        }

    def get_status_history(self, hours: int = 24) -> List[Dict]:
        """Get status history from cloud_provider_status table."""
        try:
            conn = sqlite3.connect(self._db_path)
            conn.row_factory = sqlite3.Row
            # Check if table exists
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='cloud_provider_status'"
            )
            if not cursor.fetchone():
                conn.close()
                return []

            cutoff = datetime.now(timezone.utc)
            from datetime import timedelta
            cutoff = (cutoff - timedelta(hours=hours)).isoformat()

            rows = conn.execute(
                "SELECT * FROM cloud_provider_status WHERE checked_at >= ? "
                "ORDER BY checked_at DESC LIMIT 500",
                (cutoff,),
            ).fetchall()
            conn.close()
            return [dict(r) for r in rows]
        except Exception as e:
            logger.error("Failed to get status history: %s", e)
            return []


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="CSP Health Checker — check cloud service provider health"
    )
    parser.add_argument("--check-all", action="store_true",
                        help="Check all CSP services")
    parser.add_argument("--check-service", type=str,
                        help="Check a specific service (secrets, storage, kms, monitoring, iam, registry)")
    parser.add_argument("--history", action="store_true",
                        help="Show status history")
    parser.add_argument("--hours", type=int, default=24,
                        help="History window in hours (default: 24)")
    parser.add_argument("--config", type=str, default=None,
                        help="Path to cloud_config.yaml")
    parser.add_argument("--db", type=str, default=None,
                        help="Path to icdev.db")
    parser.add_argument("--json", action="store_true",
                        help="JSON output")

    args = parser.parse_args()
    checker = CSPHealthChecker(config_path=args.config, db_path=args.db)

    if args.check_service:
        if args.check_service not in CSPHealthChecker.SERVICE_NAMES:
            print(f"Unknown service: {args.check_service}. "
                  f"Available: {', '.join(CSPHealthChecker.SERVICE_NAMES)}",
                  file=sys.stderr)
            sys.exit(1)
        result = checker.check_service(args.check_service)
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            status_icon = "OK" if result["status"] == "healthy" else "FAIL"
            print(f"[{status_icon}] {result['service']}: {result['provider']} "
                  f"({result['status']}, {result['latency_ms']}ms)")
            if result.get("error"):
                print(f"  Error: {result['error']}")

    elif args.history:
        history = checker.get_status_history(hours=args.hours)
        if args.json:
            print(json.dumps({"history": history, "count": len(history)}, indent=2))
        else:
            print(f"Status history (last {args.hours}h): {len(history)} entries")
            for entry in history[:20]:
                status_icon = "OK" if entry["status"] == "healthy" else "FAIL"
                print(f"  [{status_icon}] {entry['checked_at']} "
                      f"{entry['service']}:{entry['provider']} "
                      f"({entry['status']}, {entry.get('latency_ms', 0)}ms)")

    else:
        # Default: check all
        result = checker.check_all()
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"CSP Health: {result['overall_status'].upper()} "
                  f"({result['healthy_count']}/{result['total_count']} healthy)")
            print(f"  Provider: {result['global_provider']} | "
                  f"Region: {result['region']} | "
                  f"IL: {result['impact_level']} | "
                  f"Air-gapped: {result['air_gapped']}")
            for name, svc in result["services"].items():
                status_icon = "OK" if svc["status"] == "healthy" else "FAIL"
                print(f"  [{status_icon}] {name}: {svc['provider']} "
                      f"({svc['status']}, {svc['latency_ms']}ms)")
                if svc.get("error"):
                    print(f"       Error: {svc['error']}")


if __name__ == "__main__":
    main()
