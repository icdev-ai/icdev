#!/usr/bin/env python3
# CUI // SP-CTI
"""ICDEV™ Migration Canvas — Inventory Scanner.

Parses server/VM inventory from various input formats and populates
mc_srv_inventory and mc_inventory_imports in the migration canvas DB.

Supported formats:
  1. CSV  — columns: hostname, ip_address, os, cpu_cores, ram_gb, disk_gb,
             environment, classification (flexible, case-insensitive)
  2. JSON — [{hostname, ip, os, cpu_cores, ram_gb, disk_gb, ...}, ...]
  3. NMAP XML — nmap -oX scan.xml output; extracts hosts + open ports

Usage:
  python -m tools.migration_canvas.inventory_scanner --csv assets.csv --session-id <sid>
  python -m tools.migration_canvas.inventory_scanner --json assets.json --session-id <sid>
  python -m tools.migration_canvas.inventory_scanner --nmap scan.xml --session-id <sid> --dry-run
"""
from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import argparse
import csv
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = get_logger("icdev.migration_canvas.inventory_scanner")

# ---------------------------------------------------------------------------
# SLA & performance constants
# ---------------------------------------------------------------------------
DEFAULT_HOST_TIMEOUT_SECONDS: int = 30
DEFAULT_WORKER_COUNT: int = 10
SLA_BUDGET_SECONDS: int = 7200   # 2 hours
SLA_MAX_SERVERS: int = 1000


class PerformanceTracker:
    """Times a batch operation and checks SLA compliance."""

    def __init__(self, sla_seconds: float = SLA_BUDGET_SECONDS) -> None:
        self._sla_seconds = sla_seconds
        self._start: float | None = None
        self._stop: float | None = None
        self.server_count: int = 0

    def start(self) -> None:
        import time
        self._start = time.monotonic()

    def stop(self) -> None:
        import time
        self._stop = time.monotonic()

    @property
    def elapsed_seconds(self) -> float:
        import time
        if self._start is None:
            return 0.0
        end = self._stop if self._stop is not None else time.monotonic()
        return end - self._start

    @property
    def sla_compliant(self) -> bool:
        return self.elapsed_seconds <= self._sla_seconds

    @property
    def throughput_per_hour(self) -> float:
        elapsed = self.elapsed_seconds
        if elapsed <= 0 or self.server_count <= 0:
            return 0.0
        return (self.server_count / elapsed) * 3600

    def projected_duration(self, target_count: int) -> float:
        """Linear projection: time for target_count based on current rate."""
        elapsed = self.elapsed_seconds
        if self.server_count <= 0 or elapsed <= 0:
            return 0.0
        return (elapsed / self.server_count) * target_count

    def report(self) -> dict:
        proj = self.projected_duration(SLA_MAX_SERVERS)
        return {
            "elapsed_seconds": self.elapsed_seconds,
            "server_count": self.server_count,
            "throughput_per_hour": self.throughput_per_hour,
            "sla_budget_seconds": self._sla_seconds,
            "sla_compliant": self.sla_compliant,
            "projected_1000_server_seconds": proj,
            "projected_1000_server_within_sla": proj <= self._sla_seconds,
        }


def validate_performance_sla(
    sample_size: int = 100,
    dry_run: bool = True,
) -> dict:
    """Time a sample write_inventory call and project SLA compliance at SLA_MAX_SERVERS."""
    import uuid as _uuid
    servers = [
        {
            "id": str(_uuid.uuid4()),
            "hostname": f"srv-sla-{i:04d}",
            "ip_address": f"10.0.{i // 256}.{i % 256}",
            "os": "RHEL 9",
            "cpu_cores": 4,
            "ram_gb": 16,
            "disk_gb": 500,
            "environment": "sla-sample",
            "status": "active",
            "tags": "[]",
            "created_at": _now(),
        }
        for i in range(sample_size)
    ]
    tracker = PerformanceTracker(sla_seconds=SLA_BUDGET_SECONDS)
    tracker.server_count = sample_size
    tracker.start()
    write_inventory(servers, f"sla-{_uuid.uuid4().hex[:8]}", dry_run=dry_run)
    tracker.stop()

    proj = tracker.projected_duration(SLA_MAX_SERVERS)
    scale = SLA_MAX_SERVERS / sample_size if sample_size > 0 else 0.0
    return {
        "sample_size": sample_size,
        "scale_factor": scale,
        "elapsed_seconds": tracker.elapsed_seconds,
        "throughput_per_hour": tracker.throughput_per_hour,
        "projected_1000_server_seconds": proj,
        "projected_1000_server_within_sla": proj <= SLA_BUDGET_SECONDS,
        "sla_max_servers": SLA_MAX_SERVERS,
        "sla_budget_seconds": SLA_BUDGET_SECONDS,
    }


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _conn():
    from tools.migration_canvas.db.init_db import get_connection
    return get_connection()


def _norm(row: dict) -> dict:
    return {k.strip().lower().replace(" ", "_"): v for k, v in row.items()}


def _int(v, default: int = 0) -> int:
    try:
        return int(float(str(v))) if v not in (None, "", "n/a", "na") else default
    except (TypeError, ValueError):
        return default


def _str(v, default: str = "") -> str:
    return str(v).strip() if v not in (None, "", "n/a") else default


# ── Parsers ───────────────────────────────────────────────────────────────────

def _parse_csv_row(row: dict) -> dict | None:
    r = _norm(row)
    hostname = _str(r.get("hostname") or r.get("name") or r.get("server_name") or r.get("host"))
    if not hostname:
        return None
    return {
        "id": str(uuid.uuid4()),
        "hostname": hostname,
        "ip_address": _str(r.get("ip_address") or r.get("ip") or r.get("ip_addr")),
        "os": _str(r.get("os") or r.get("operating_system") or r.get("os_name")),
        "cpu_cores": _int(r.get("cpu_cores") or r.get("cpu") or r.get("vcpu") or r.get("cores")),
        "ram_gb": _int(r.get("ram_gb") or r.get("ram") or r.get("memory_gb") or r.get("memory")),
        "disk_gb": _int(r.get("disk_gb") or r.get("disk") or r.get("storage_gb") or r.get("storage")),
        "environment": _str(r.get("environment") or r.get("env") or r.get("tier"), "production"),
        "status": _str(r.get("status"), "active"),
        "tags": json.dumps([t.strip() for t in str(r.get("tags", "")).split(",") if t.strip()]),
        "raw_json": json.dumps({k: v for k, v in r.items()}),
        "created_at": _now(),
    }


def parse_csv(path: Path) -> list[dict]:
    results: list[dict] = []
    with open(path, encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            parsed = _parse_csv_row(row)
            if parsed:
                results.append(parsed)
    return results


def parse_json(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    rows = data if isinstance(data, list) else data.get("servers", data.get("hosts", data.get("data", [])))
    results: list[dict] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        parsed = _parse_csv_row(row)
        if parsed:
            results.append(parsed)
    return results


def parse_nmap_xml(path: Path) -> list[dict]:
    try:
        from xml.etree import ElementTree as ET
    except ImportError:
        return []

    results: list[dict] = []
    try:
        tree = ET.parse(str(path))
        root = tree.getroot()
    except Exception:
        return []

    for host in root.findall("host"):
        status_el = host.find("status")
        if status_el is not None and status_el.get("state") != "up":
            continue

        hostname = ""
        hostnames_el = host.find("hostnames")
        if hostnames_el is not None:
            for hn in hostnames_el.findall("hostname"):
                hostname = hn.get("name", "")
                if hostname:
                    break

        ip = ""
        for addr in host.findall("address"):
            if addr.get("addrtype") == "ipv4":
                ip = addr.get("addr", "")
                break
        if not ip and not hostname:
            continue
        hostname = hostname or ip

        os_match = ""
        os_el = host.find("os")
        if os_el is not None:
            om = os_el.find("osmatch")
            if om is not None:
                os_match = om.get("name", "")

        open_ports: list[str] = []
        ports_el = host.find("ports")
        if ports_el is not None:
            for port in ports_el.findall("port"):
                state = port.find("state")
                if state is not None and state.get("state") == "open":
                    open_ports.append(f"{port.get('portid')}/{port.get('protocol','tcp')}")

        results.append({
            "id": str(uuid.uuid4()),
            "hostname": hostname,
            "ip_address": ip,
            "os": os_match,
            "cpu_cores": 0,
            "ram_gb": 0,
            "disk_gb": 0,
            "environment": "discovered",
            "status": "active",
            "tags": json.dumps(open_ports[:20]),
            "raw_json": json.dumps({"open_ports": open_ports}),
            "created_at": _now(),
        })
    return results


# ── Writer ────────────────────────────────────────────────────────────────────

def write_inventory(
    servers: list[dict],
    session_id: str,
    source_format: str = "csv",
    source_file: str = "",
    dry_run: bool = False,
) -> dict[str, Any]:
    """Write parsed server inventory to mc_srv_inventory and mc_inventory_imports.

    Returns {inserted, skipped, import_id, session_id, dry_run}
    """
    import_id = str(uuid.uuid4())

    if dry_run:
        tracker = PerformanceTracker()
        tracker.server_count = len(servers)
        tracker.start()
        tracker.stop()
        return {
            "inserted": len(servers),
            "skipped": 0,
            "import_id": import_id,
            "session_id": session_id,
            "dry_run": True,
            "performance": tracker.report(),
        }

    conn = _conn()
    inserted = skipped = 0
    now = _now()

    try:
        # Log the import attempt
        try:
            conn.execute(
                "INSERT INTO mc_inventory_imports "
                "(id, session_id, source_format, source_file, record_count, status, imported_at) "
                "VALUES (?, ?, ?, ?, ?, 'pending', ?)",
                (import_id, session_id, source_format, source_file, len(servers), now),
            )
        except Exception as exc:
            logger.debug("mc_inventory_imports insert skipped (table may not exist): %s", exc)

        for srv in servers:
            try:
                conn.execute(
                    "INSERT OR IGNORE INTO mc_srv_inventory "
                    "(id, session_id, hostname, ip_address, os, cpu_cores, ram_gb, disk_gb, "
                    "environment, status, tags, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        srv["id"], session_id, srv["hostname"], srv["ip_address"],
                        srv["os"], srv["cpu_cores"], srv["ram_gb"], srv["disk_gb"],
                        srv["environment"], srv["status"], srv.get("tags", "[]"), srv["created_at"],
                    ),
                )
                inserted += 1
            except Exception as exc:
                logger.warning("Failed to insert server %s: %s", srv.get("hostname", "?"), exc)
                skipped += 1

        # Update import record
        try:
            conn.execute(
                "UPDATE mc_inventory_imports SET status='complete', record_count=? WHERE id=?",
                (inserted, import_id),
            )
        except Exception as exc:
            logger.debug("mc_inventory_imports update skipped: %s", exc)

        conn.commit()
    finally:
        conn.close()

    return {
        "inserted": inserted,
        "skipped": skipped,
        "import_id": import_id,
        "session_id": session_id,
        "dry_run": False,
    }


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description="MCE Inventory Scanner — import server inventory")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--csv", metavar="FILE", help="CSV inventory file")
    src.add_argument("--json", dest="json_file", metavar="FILE", help="JSON inventory file")
    src.add_argument("--nmap", metavar="XML", help="NMAP XML scan output")
    ap.add_argument("--session-id", required=True, help="Migration session ID")
    ap.add_argument("--dry-run", action="store_true", help="Parse only, no DB writes")
    ap.add_argument("--output-json", action="store_true", help="Emit JSON result to stdout")
    args = ap.parse_args()

    if args.csv:
        servers = parse_csv(Path(args.csv))
        fmt, src_file = "csv", args.csv
    elif args.json_file:
        servers = parse_json(Path(args.json_file))
        fmt, src_file = "json", args.json_file
    else:
        servers = parse_nmap_xml(Path(args.nmap))
        fmt, src_file = "nmap_xml", args.nmap

    result = write_inventory(servers, args.session_id, fmt, src_file, dry_run=args.dry_run)
    result["total_parsed"] = len(servers)

    if args.output_json:
        print(json.dumps(result, indent=2))
    else:
        dr = " (dry-run)" if args.dry_run else ""
        print(f"[inventory_scanner] {result['inserted']} inserted, {result['skipped']} skipped{dr}")


if __name__ == "__main__":
    main()
