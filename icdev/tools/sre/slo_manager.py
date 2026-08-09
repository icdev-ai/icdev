
# [TEMPLATE: CUI // SP-CTI]
"""SLO (Service Level Objective) management for ICDEV™ SRE module.

Tracks service-level objectives, records measurements, calculates error budget
burn rates, and provides dashboard/gate outputs.
"""

import argparse
import json
import logging
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from tools.logging.icdev_logger import get_logger  # noqa: E402

import yaml  # noqa: E402

from tools.db.storage import get_connection  # noqa: E402

logger = get_logger(__name__)

CONFIG_PATH = BASE_DIR / "args" / "sre_config.yaml"

SLO_TYPES = ("availability", "latency_p95", "latency_p99", "error_rate", "throughput")
SLO_STATUSES = ("healthy", "warning", "critical", "exhausted")
MEASUREMENT_SOURCES = ("prometheus", "manual", "synthetic")


def _load_config() -> dict:
    """Load SRE configuration from args/sre_config.yaml."""
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}


def _get_slo_config() -> dict:
    """Get SLO-specific config with defaults."""
    cfg = _load_config().get("slo", {})
    return {
        "default_window_days": cfg.get("default_window_days", 30),
        "alert_threshold_warning": cfg.get("alert_threshold_warning", 0.3),
        "alert_threshold_critical": cfg.get("alert_threshold_critical", 0.1),
        "burn_rate_alert_multiplier": cfg.get("burn_rate_alert_multiplier", 2.0),
    }


def init_tables(conn: sqlite3.Connection) -> None:
    """Create SRE SLO tables if they don't exist."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS sre_slos (
            id TEXT PRIMARY KEY,
            service_name TEXT NOT NULL,
            slo_name TEXT NOT NULL,
            slo_type TEXT NOT NULL CHECK(slo_type IN ('availability', 'latency_p95', 'latency_p99', 'error_rate', 'throughput')),
            target_value REAL NOT NULL,
            window_days INTEGER NOT NULL DEFAULT 30,
            current_value REAL,
            budget_remaining REAL,
            burn_rate REAL,
            status TEXT NOT NULL DEFAULT 'healthy' CHECK(status IN ('healthy', 'warning', 'critical', 'exhausted')),
            alert_threshold_warning REAL DEFAULT 0.3,
            alert_threshold_critical REAL DEFAULT 0.1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS sre_slo_measurements (
            id TEXT PRIMARY KEY,
            slo_id TEXT NOT NULL,
            measured_value REAL NOT NULL,
            timestamp TEXT NOT NULL,
            good_events INTEGER,
            total_events INTEGER,
            source TEXT NOT NULL DEFAULT 'manual' CHECK(source IN ('prometheus', 'manual', 'synthetic')),
            FOREIGN KEY (slo_id) REFERENCES sre_slos(id)
        );
    """)
    conn.commit()


def create_slo(service: str, name: str, slo_type: str, target: float, window_days: int = 30) -> dict:
    """Create a new SLO for a service.

    Args:
        service: Service name (e.g., 'dashboard', 'api-gateway').
        name: SLO name (e.g., 'availability', 'p99-latency').
        slo_type: One of availability, latency_p95, latency_p99, error_rate, throughput.
        target: Target value (e.g., 0.999 for 99.9% availability).
        window_days: Measurement window in days (default 30).

    Returns:
        dict with created SLO details.
    """
    if slo_type not in SLO_TYPES:
        return {"status": "error", "message": f"Invalid slo_type: {slo_type}. Must be one of {SLO_TYPES}"}

    cfg = _get_slo_config()
    slo_id = f"slo-{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc).isoformat()

    conn = get_connection()
    init_tables(conn)

    conn.execute(
        """INSERT INTO sre_slos
           (id, service_name, slo_name, slo_type, target_value, window_days,
            current_value, budget_remaining, burn_rate, status,
            alert_threshold_warning, alert_threshold_critical, created_at, updated_at)
           VALUES (%s, %s, %s, %s, %s, %s, NULL, 1.0, 0.0, 'healthy', %s, %s, %s, %s)""",
        (
            slo_id,
            service,
            name,
            slo_type,
            target,
            window_days,
            cfg["alert_threshold_warning"],
            cfg["alert_threshold_critical"],
            now,
            now,
        ),
    )
    conn.commit()

    logger.info("Created SLO %s for service %s", slo_id, service)
    return {
        "status": "ok",
        "slo_id": slo_id,
        "service_name": service,
        "slo_name": name,
        "slo_type": slo_type,
        "target_value": target,
        "window_days": window_days,
        "budget_remaining": 1.0,
        "burn_rate": 0.0,
        "slo_status": "healthy",
    }


def record_measurement(
    slo_id: str, value: float, good_events: int = None, total_events: int = None, source: str = "manual"
) -> dict:
    """Record a measurement against an SLO.

    Args:
        slo_id: SLO identifier.
        value: Measured value.
        good_events: Number of good events (optional).
        total_events: Total events (optional).
        source: Measurement source (prometheus, manual, synthetic).

    Returns:
        dict with measurement details and updated SLO status.
    """
    if source not in MEASUREMENT_SOURCES:
        return {"status": "error", "message": f"Invalid source: {source}. Must be one of {MEASUREMENT_SOURCES}"}

    conn = get_connection()
    init_tables(conn)

    row = conn.execute("SELECT * FROM sre_slos WHERE id = %s", (slo_id,)).fetchone()
    if not row:
        return {"status": "error", "message": f"SLO not found: {slo_id}"}

    measurement_id = f"slom-{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc).isoformat()

    conn.execute(
        """INSERT INTO sre_slo_measurements
           (id, slo_id, measured_value, timestamp, good_events, total_events, source)
           VALUES (%s, %s, %s, %s, %s, %s, %s)""",
        (measurement_id, slo_id, value, now, good_events, total_events, source),
    )

    # Update current_value on the SLO
    conn.execute(
        "UPDATE sre_slos SET current_value = %s, updated_at = %s WHERE id = %s",
        (value, now, slo_id),
    )
    conn.commit()

    # Recalculate burn rate after recording
    burn_result = calculate_burn_rate(slo_id)

    logger.info("Recorded measurement %s for SLO %s: %s", measurement_id, slo_id, value)
    return {
        "status": "ok",
        "measurement_id": measurement_id,
        "slo_id": slo_id,
        "measured_value": value,
        "source": source,
        "burn_rate": burn_result.get("burn_rate"),
        "budget_remaining": burn_result.get("budget_remaining"),
        "slo_status": burn_result.get("slo_status"),
    }


def calculate_burn_rate(slo_id: str) -> dict:
    """Calculate error budget burn rate for an SLO.

    The burn rate measures how quickly the error budget is being consumed.
    A burn_rate of 1.0 means on pace to exactly exhaust the budget by window end.
    Values > 1.0 mean burning faster than sustainable.

    Args:
        slo_id: SLO identifier.

    Returns:
        dict with burn_rate, budget_remaining, and status.
    """
    conn = get_connection()
    init_tables(conn)

    slo = conn.execute("SELECT * FROM sre_slos WHERE id = %s", (slo_id,)).fetchone()
    if not slo:
        return {"status": "error", "message": f"SLO not found: {slo_id}"}

    # Get column names
    cols = [desc[0] for desc in conn.execute("SELECT * FROM sre_slos LIMIT 0").description]
    slo_dict = dict(zip(cols, slo))

    target = slo_dict["target_value"]
    window_days = slo_dict["window_days"]
    warn_threshold = slo_dict["alert_threshold_warning"]
    crit_threshold = slo_dict["alert_threshold_critical"]
    _get_slo_config()

    # Get measurements within the window
    datetime.now(timezone.utc).isoformat()
    measurements = conn.execute(
        """SELECT measured_value, good_events, total_events, timestamp
           FROM sre_slo_measurements
           WHERE slo_id = %s
           ORDER BY timestamp DESC""",
        (slo_id,),
    ).fetchall()

    if not measurements:
        return {
            "status": "ok",
            "slo_id": slo_id,
            "burn_rate": 0.0,
            "budget_remaining": 1.0,
            "slo_status": "healthy",
        }

    # Calculate error budget based on SLO type
    slo_type = slo_dict["slo_type"]

    if slo_type in ("availability", "error_rate"):
        # For event-based SLOs, use good/total events if available
        total_good = 0
        total_all = 0
        for m in measurements:
            if m[1] is not None and m[2] is not None:
                total_good += m[1]
                total_all += m[2]

        if total_all > 0:
            actual = total_good / total_all
        else:
            # Fallback to average of measured values
            actual = sum(m[0] for m in measurements) / len(measurements)

        # Error budget: allowed failure fraction
        error_budget_total = 1.0 - target
        if error_budget_total <= 0:
            error_budget_total = 0.001  # Prevent division by zero

        errors_consumed = max(0.0, 1.0 - actual)
        budget_remaining = max(0.0, 1.0 - (errors_consumed / error_budget_total))

        # Burn rate: how fast we're consuming budget relative to window
        num_measurements = len(measurements)
        expected_fraction = min(1.0, num_measurements / max(1, window_days))
        if expected_fraction > 0:
            burn_rate = round((1.0 - budget_remaining) / expected_fraction, 4)
        else:
            burn_rate = 0.0

    else:
        # For latency/throughput, compare latest value to target
        latest_value = measurements[0][0]
        if target > 0:
            ratio = latest_value / target
            # For latency: over target is bad. For throughput: under target is bad.
            if slo_type in ("latency_p95", "latency_p99"):
                budget_remaining = max(0.0, 1.0 - max(0.0, ratio - 1.0))
            else:  # throughput
                budget_remaining = min(1.0, ratio)
            burn_rate = round(1.0 - budget_remaining, 4)
        else:
            budget_remaining = 1.0
            burn_rate = 0.0

    budget_remaining = round(budget_remaining, 6)
    burn_rate = round(burn_rate, 4)

    # Determine status
    if budget_remaining <= 0:
        slo_status = "exhausted"
    elif budget_remaining <= crit_threshold:
        slo_status = "critical"
    elif budget_remaining <= warn_threshold:
        slo_status = "warning"
    else:
        slo_status = "healthy"

    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """UPDATE sre_slos
           SET budget_remaining = %s, burn_rate = %s, status = %s, updated_at = %s
           WHERE id = %s""",
        (budget_remaining, burn_rate, slo_status, now, slo_id),
    )
    conn.commit()

    return {
        "status": "ok",
        "slo_id": slo_id,
        "burn_rate": burn_rate,
        "budget_remaining": budget_remaining,
        "slo_status": slo_status,
    }


def get_slo_dashboard() -> list:
    """Get all SLOs with current status for dashboard display.

    Returns:
        list of dicts, one per SLO with full status info.
    """
    conn = get_connection()
    init_tables(conn)

    rows = conn.execute(
        """SELECT id, service_name, slo_name, slo_type, target_value,
                  window_days, current_value, budget_remaining, burn_rate,
                  status, created_at, updated_at
           FROM sre_slos
           ORDER BY
               CASE status
                   WHEN 'exhausted' THEN 0
                   WHEN 'critical' THEN 1
                   WHEN 'warning' THEN 2
                   WHEN 'healthy' THEN 3
               END,
               service_name""",
    ).fetchall()

    cols = [
        "id",
        "service_name",
        "slo_name",
        "slo_type",
        "target_value",
        "window_days",
        "current_value",
        "budget_remaining",
        "burn_rate",
        "status",
        "created_at",
        "updated_at",
    ]

    dashboard = []
    for row in rows:
        entry = dict(zip(cols, row))

        # Count measurements
        count = conn.execute(
            "SELECT COUNT(*) FROM sre_slo_measurements WHERE slo_id = %s",
            (entry["id"],),
        ).fetchone()[0]
        entry["measurement_count"] = count

        dashboard.append(entry)

    return dashboard


def check_slo_health() -> dict:
    """Check health of all SLOs for gate validation.

    Returns:
        dict with passed (bool), summary counts, and list of failing SLOs.
    """
    conn = get_connection()
    init_tables(conn)

    rows = conn.execute(
        "SELECT id, service_name, slo_name, status, budget_remaining FROM sre_slos",
    ).fetchall()

    failing = []
    counts = {"healthy": 0, "warning": 0, "critical": 0, "exhausted": 0}

    for row in rows:
        slo_id, service, name, status, budget = row
        counts[status] = counts.get(status, 0) + 1
        if status in ("critical", "exhausted"):
            failing.append(
                {
                    "slo_id": slo_id,
                    "service": service,
                    "slo_name": name,
                    "status": status,
                    "budget_remaining": budget,
                }
            )

    passed = len(failing) == 0
    return {
        "gate": "slo_health",
        "passed": passed,
        "total_slos": len(rows),
        "counts": counts,
        "failing": failing,
    }


def main():
    """CLI entry point for SLO manager."""
    parser = argparse.ArgumentParser(description="ICDEV™ SRE SLO Manager")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--gate", action="store_true", help="Run gate check (exit 1 if critical/exhausted)")

    # Actions
    parser.add_argument("--create", action="store_true", help="Create a new SLO")
    parser.add_argument("--record", action="store_true", help="Record a measurement")
    parser.add_argument("--burn-rate", action="store_true", help="Calculate burn rate for an SLO")
    parser.add_argument("--dashboard", action="store_true", help="Show SLO dashboard")

    # Create args
    parser.add_argument("--service", type=str, help="Service name")
    parser.add_argument("--name", type=str, help="SLO name")
    parser.add_argument("--type", type=str, dest="slo_type", help="SLO type")
    parser.add_argument("--target", type=float, help="Target value")
    parser.add_argument("--window", type=int, default=30, help="Window days (default 30)")

    # Record args
    parser.add_argument("--slo-id", type=str, help="SLO ID")
    parser.add_argument("--value", type=float, help="Measured value")
    parser.add_argument("--good", type=int, default=None, help="Good events count")
    parser.add_argument("--total", type=int, default=None, help="Total events count")
    parser.add_argument("--source", type=str, default="manual", help="Measurement source")

    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    result = None

    if args.gate:
        result = check_slo_health()
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            status_str = "PASSED" if result["passed"] else "FAILED"
            print(f"SLO Health Gate: {status_str}")
            print(f"  Total SLOs: {result['total_slos']}")
            print(f"  Counts: {result['counts']}")
            if result["failing"]:
                print("  Failing:")
                for f in result["failing"]:
                    print(f"    - {f['service']}/{f['slo_name']}: {f['status']} (budget: {f['budget_remaining']})")
        if not result["passed"]:
            sys.exit(1)

    elif args.create:
        if not all([args.service, args.name, args.slo_type, args.target]):
            parser.error("--create requires --service, --name, --type, and --target")
        result = create_slo(args.service, args.name, args.slo_type, args.target, args.window)
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"Created SLO: {result.get('slo_id')} ({result.get('status')})")

    elif args.record:
        if not all([args.slo_id, args.value is not None]):
            parser.error("--record requires --slo-id and --value")
        result = record_measurement(args.slo_id, args.value, args.good, args.total, args.source)
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"Recorded: {result.get('measurement_id')} -> status: {result.get('slo_status')}")

    elif args.burn_rate:
        if not args.slo_id:
            parser.error("--burn-rate requires --slo-id")
        result = calculate_burn_rate(args.slo_id)
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(
                f"Burn rate: {result.get('burn_rate')}, budget: {result.get('budget_remaining')}, status: {result.get('slo_status')}"
            )

    elif args.dashboard:
        dashboard = get_slo_dashboard()
        if args.json:
            print(json.dumps(dashboard, indent=2))
        else:
            print(f"SLO Dashboard ({len(dashboard)} SLOs):")
            for slo in dashboard:
                print(
                    f"  [{slo['status']:>9}] {slo['service_name']}/{slo['slo_name']} "
                    f"target={slo['target_value']} current={slo['current_value']} "
                    f"budget={slo['budget_remaining']} burn={slo['burn_rate']}"
                )

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
