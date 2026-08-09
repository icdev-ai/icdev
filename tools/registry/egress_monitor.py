#!/usr/bin/env python3
# CUI // SP-CTI
"""Network Egress Monitor — NemoClaw-adapted child network tracking (D-NC-6).

Extension to TelemetryCollector that evaluates child app outbound
network connections against egress policies.  Pull-based (D210 air-gap safe).

Child apps expose /health/egress endpoint returning recent outbound
connection log.  Parent polls during telemetry collection and evaluates
against the resolved egress policy for the child's role.

CLI:
    python tools/registry/egress_monitor.py --collect --child-id child-1 --endpoint http://localhost:8445/health/egress --json
    python tools/registry/egress_monitor.py --evaluate --child-id child-1 --json
    python tools/registry/egress_monitor.py --summary --child-id child-1 --json
"""

import argparse
import json
import os
import sqlite3
import sys
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection  # noqa: E402

DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(BASE_DIR / "data" / "icdev.db")))


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class EgressMonitor:
    """Network egress monitoring for child applications (D-NC-6).

    Collects egress data from child /health/egress endpoints and
    evaluates against resolved egress policies.  Results stored
    in child_telemetry with metric_type='egress'.
    """

    def __init__(self, db_path: Optional[Path] = None, timeout: int = 10):
        self._db_path = db_path or DB_PATH
        self._timeout = timeout

    def _get_db(self) -> sqlite3.Connection:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = get_connection(db_path=str(self._db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def collect_egress(self, child_id: str, endpoint_url: str) -> Dict:
        """Poll child /health/egress endpoint for connection data.

        Args:
            child_id: Child application identifier.
            endpoint_url: URL of child's egress health endpoint.

        Returns:
            Dict with connections list or error.
        """
        try:
            req = urllib.request.Request(endpoint_url, method="GET")
            req.add_header("User-Agent", "ICDEV-EgressMonitor/1.0")
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:  # nosec B310 -- URL scheme validated; internal/configured endpoints only
                data = json.loads(resp.read().decode("utf-8"))
                return {
                    "child_id": child_id,
                    "connections": data.get("connections", []),
                    "collected_at": _now(),
                    "source": "health_endpoint",
                }
        except urllib.error.URLError as e:
            return {
                "child_id": child_id,
                "connections": [],
                "collected_at": _now(),
                "error": f"endpoint_unreachable: {e}",
                "source": "health_endpoint",
            }
        except Exception as e:
            return {
                "child_id": child_id,
                "connections": [],
                "collected_at": _now(),
                "error": str(e),
                "source": "health_endpoint",
            }

    def store_egress(self, egress_data: Dict) -> Dict:
        """Store collected egress data in child_telemetry."""
        conn = self._get_db()
        telemetry_id = str(uuid.uuid4())
        try:
            conn.execute(
                """INSERT INTO child_telemetry
                   (id, child_id, metric_type, metric_data, health_status, collected_at)
                   VALUES (%s, %s, 'egress', %s, %s, %s)""",
                (
                    telemetry_id,
                    egress_data["child_id"],
                    json.dumps(egress_data, default=str),
                    "healthy" if not egress_data.get("error") else "degraded",
                    egress_data.get("collected_at", _now()),
                ),
            )
            conn.commit()
        except Exception as e:
            conn.close()
            return {"stored": False, "error": str(e)}
        conn.close()
        return {"stored": True, "telemetry_id": telemetry_id}

    def evaluate_egress(self, child_id: str, role: str = "builder") -> Dict:
        """Evaluate child's egress against resolved policy.

        Args:
            child_id: Child application identifier.
            role: Agent role for policy lookup (default: builder).

        Returns:
            Dict with violations list and evaluation result.
        """
        # Load latest egress data
        conn = self._get_db()
        row = conn.execute(
            """SELECT metric_data FROM child_telemetry
               WHERE child_id = %s AND metric_type = 'egress'
               ORDER BY collected_at DESC LIMIT 1""",
            (child_id,),
        ).fetchone()
        conn.close()

        if not row:
            return {
                "child_id": child_id,
                "violations": [],
                "total_connections": 0,
                "evaluated": False,
                "reason": "no_egress_data",
            }

        try:
            data = json.loads(row["metric_data"])
        except (json.JSONDecodeError, TypeError):
            return {
                "child_id": child_id,
                "violations": [],
                "evaluated": False,
                "reason": "invalid_egress_data",
            }

        connections = data.get("connections", [])

        # Load egress policy for role
        allowed_hosts = self._get_allowed_hosts(role)

        violations = []
        for conn_entry in connections:
            dest = conn_entry.get("dest", conn_entry.get("host", ""))
            port = conn_entry.get("port", 0)
            if not self._is_allowed(dest, port, allowed_hosts):
                violations.append(
                    {
                        "dest": dest,
                        "port": port,
                        "reason": "not_in_egress_policy",
                    }
                )

        return {
            "child_id": child_id,
            "role": role,
            "violations": violations,
            "violation_count": len(violations),
            "total_connections": len(connections),
            "allowed_connections": len(connections) - len(violations),
            "evaluated": True,
            "evaluated_at": _now(),
        }

    def get_summary(self, child_id: str, hours: int = 24) -> Dict:
        """Get egress summary for a child over a time window."""
        conn = self._get_db()
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")

        rows = conn.execute(
            """SELECT metric_data, collected_at FROM child_telemetry
               WHERE child_id = %s AND metric_type = 'egress'
               AND collected_at > %s
               ORDER BY collected_at DESC""",
            (child_id, cutoff),
        ).fetchall()
        conn.close()

        total_connections = 0
        total_collections = len(rows)
        errors = 0

        for row in rows:
            try:
                data = json.loads(row["metric_data"])
                total_connections += len(data.get("connections", []))
                if data.get("error"):
                    errors += 1
            except (json.JSONDecodeError, TypeError):
                errors += 1

        return {
            "child_id": child_id,
            "hours": hours,
            "total_collections": total_collections,
            "total_connections": total_connections,
            "errors": errors,
        }

    def _get_allowed_hosts(self, role: str) -> List[Dict]:
        """Load allowed hosts from egress policy for role."""
        try:
            from tools.security.egress_policy_manager import EgressPolicyManager

            mgr = EgressPolicyManager(db_path=self._db_path)
            policy = mgr.resolve_policy(role)
            if "error" not in policy:
                return policy.get("egress", {}).get("allowed_endpoints", [])
        except Exception:
            pass
        return []

    def _is_allowed(self, dest: str, port: int, allowed_hosts: List[Dict]) -> bool:
        """Check if a destination is allowed by policy."""
        # Internal mesh and DNS always allowed
        if dest in ("localhost", "127.0.0.1") or port == 53:
            return True

        for ep in allowed_hosts:
            ep_host = ep.get("host", "")
            ep_port = ep.get("port", 0)
            if ep_host == "*" and (ep_port == 0 or ep_port == port):
                return True
            if ep_host == dest and (ep_port == 0 or ep_port == port):
                return True
        return False


def main():
    parser = argparse.ArgumentParser(description="Network Egress Monitor — NemoClaw child network tracking (D-NC-6)")
    parser.add_argument("--collect", action="store_true", help="Collect egress from child endpoint")
    parser.add_argument("--evaluate", action="store_true", help="Evaluate egress against policy")
    parser.add_argument("--summary", action="store_true", help="Get egress summary")
    parser.add_argument("--child-id", type=str, default="", help="Child ID")
    parser.add_argument("--endpoint", type=str, default="", help="Child egress endpoint URL")
    parser.add_argument("--role", type=str, default="builder", help="Agent role for policy")
    parser.add_argument("--hours", type=int, default=24, help="Summary window hours")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--db-path", type=Path, help="Database path override")
    args = parser.parse_args()

    monitor = EgressMonitor(db_path=args.db_path)

    if args.collect:
        data = monitor.collect_egress(args.child_id, args.endpoint)
        monitor.store_egress(data)
        result = data
    elif args.evaluate:
        result = monitor.evaluate_egress(args.child_id, role=args.role)
    elif args.summary:
        result = monitor.get_summary(args.child_id, hours=args.hours)
    else:
        parser.print_help()
        return

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        print(result)


if __name__ == "__main__":
    main()
