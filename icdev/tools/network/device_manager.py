# [CUI // SP-CTI]
"""ICDEV™ Network Infrastructure Intelligence — Device Manager.

CRUD operations for the ni_devices table. Supports bulk import of
vendor/model/EOL metadata, criticality scoring via downstream BFS,
and EOL timeline generation.

Usage:
    python tools/network/device_manager.py --topology-id T1 --list --json
    python tools/network/device_manager.py --topology-id T1 --eol-timeline --years 5 --json
    python tools/network/device_manager.py --topology-id T1 --compute-criticality --json
    python tools/network/device_manager.py --topology-id T1 --bulk-import devices.csv --json
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.logging.icdev_logger import get_logger  # noqa: E402

logger = get_logger("icdev.network.device_manager")


def _get_conn():
    from tools.network.db.init_db import get_connection
    return get_connection()


# ── CRUD ─────────────────────────────────────────────────────────────────────

def upsert_device(topology_id: str, node_id: str, conn=None, **kwargs) -> dict:
    """Create or update a device in ni_devices."""
    own_conn = conn is None
    if own_conn:
        conn = _get_conn()

    now = datetime.now(timezone.utc).isoformat()

    existing = conn.execute(
        "SELECT id FROM ni_devices WHERE topology_id = %s AND node_id = %s",
        (topology_id, node_id),
    ).fetchone()

    if existing:
        device_id = existing[0] if isinstance(existing, tuple) else existing["id"]
        updates = []
        values = []
        for key in (
            "label", "device_type", "vendor", "model", "firmware_version",
            "eol_date", "eos_date", "purchase_date", "purchase_cost",
            "annual_maintenance_cost", "replacement_cost", "site",
            "rack_location", "notes", "properties_json",
        ):
            if key in kwargs:
                updates.append(f"{key} = ?")
                values.append(kwargs[key])
        if updates:
            updates.append("updated_at = ?")
            values.append(now)
            values.append(device_id)
            conn.execute(
                f"UPDATE ni_devices SET {', '.join(updates)} WHERE id = %s",  # nosec B608 — column names from hardcoded allowlist
                values,
            )
            conn.commit()
        return {"id": device_id, "action": "updated"}
    else:
        device_id = str(uuid.uuid4())[:12]
        conn.execute(
            "INSERT INTO ni_devices (id, topology_id, node_id, label, device_type, "
            "vendor, model, firmware_version, eol_date, eos_date, purchase_date, "
            "purchase_cost, annual_maintenance_cost, replacement_cost, site, "
            # `rack`, not `rack_location` (swp-scan-01).
            "rack, notes, properties_json, created_at, updated_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                device_id, topology_id, node_id,
                kwargs.get("label", f"device-{node_id}"),
                kwargs.get("device_type", "unknown"),
                kwargs.get("vendor"), kwargs.get("model"),
                kwargs.get("firmware_version"),
                kwargs.get("eol_date"), kwargs.get("eos_date"),
                kwargs.get("purchase_date"),
                kwargs.get("purchase_cost", 0),
                kwargs.get("annual_maintenance_cost", 0),
                kwargs.get("replacement_cost", 0),
                kwargs.get("site"), kwargs.get("rack_location"),
                kwargs.get("notes"),
                kwargs.get("properties_json", "{}"),
                now, now,
            ),
        )
        conn.commit()
        return {"id": device_id, "action": "created"}


def get_devices(topology_id: str, filters: dict | None = None, conn=None) -> list[dict]:
    """List devices for a topology with optional filters."""
    own_conn = conn is None
    if own_conn:
        conn = _get_conn()

    sql = "SELECT * FROM ni_devices WHERE topology_id = ?"
    params: list = [topology_id]

    if filters:
        if filters.get("device_type"):
            sql += " AND device_type = ?"
            params.append(filters["device_type"])
        if filters.get("vendor"):
            sql += " AND vendor = ?"
            params.append(filters["vendor"])
        if filters.get("site"):
            sql += " AND site = ?"
            params.append(filters["site"])
        if filters.get("eol_before"):
            sql += " AND eol_date IS NOT NULL AND eol_date <= ?"
            params.append(filters["eol_before"])

    sql += " ORDER BY criticality_score DESC, label ASC"
    rows = conn.execute(sql, params).fetchall()

    result = []
    for row in rows:
        d = dict(row) if hasattr(row, "keys") else {
            "id": row[0], "topology_id": row[1], "node_id": row[2],
            "label": row[3], "device_type": row[4],
        }
        result.append(d)

    if own_conn:
        conn.close()
    return result


def get_device(device_id: str, conn=None) -> dict | None:
    """Get a single device by ID."""
    own_conn = conn is None
    if own_conn:
        conn = _get_conn()
    row = conn.execute("SELECT * FROM ni_devices WHERE id = %s", (device_id,)).fetchone()
    if own_conn:
        conn.close()
    if row is None:
        return None
    return dict(row) if hasattr(row, "keys") else {"id": row[0]}


# ── Bulk import ──────────────────────────────────────────────────────────────

def bulk_import_devices(topology_id: str, file_path: str, conn=None) -> dict:
    """Import device metadata from CSV or JSON file.

    CSV columns: node_id (or label), vendor, model, firmware_version,
    eol_date, eos_date, purchase_cost, annual_maintenance_cost,
    replacement_cost, site, rack_location, notes.

    JSON: array of objects with same keys.
    """
    own_conn = conn is None
    if own_conn:
        conn = _get_conn()

    p = Path(file_path)
    if not p.exists():
        return {"error": f"File not found: {file_path}"}

    records: list[dict] = []
    if p.suffix.lower() == ".json":
        with open(p, "r", encoding="utf-8") as f:
            records = json.load(f)
    elif p.suffix.lower() == ".csv":
        with open(p, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            records = list(reader)
    else:
        return {"error": f"Unsupported import format: {p.suffix}"}

    # Build label->node_id lookup from existing devices
    existing = conn.execute(
        "SELECT node_id, label FROM ni_devices WHERE topology_id = %s",
        (topology_id,),
    ).fetchall()
    label_to_node_id: dict[str, str] = {}
    for row in existing:
        nid = row[0] if isinstance(row, tuple) else row["node_id"]
        lbl = row[1] if isinstance(row, tuple) else row["label"]
        label_to_node_id[lbl.lower()] = nid
        label_to_node_id[nid] = nid

    created, updated, skipped = 0, 0, 0
    for rec in records:
        node_id = rec.get("node_id", "")
        label = rec.get("label", "")
        # Try to match by node_id or label
        if not node_id and label:
            node_id = label_to_node_id.get(label.lower(), "")
        if not node_id:
            # Auto-generate node_id for new devices not yet in topology
            node_id = "imp-" + str(uuid.uuid4())[:8]
            if label:
                label_to_node_id[label.lower()] = node_id  # prevent dup on re-import

        result = upsert_device(
            topology_id, node_id, conn=conn,
            label=label or node_id,
            device_type=rec.get("device_type", "unknown"),
            vendor=rec.get("vendor"),
            model=rec.get("model"),
            firmware_version=rec.get("firmware_version"),
            eol_date=rec.get("eol_date"),
            eos_date=rec.get("eos_date"),
            purchase_date=rec.get("purchase_date"),
            purchase_cost=float(rec.get("purchase_cost", 0) or 0),
            annual_maintenance_cost=float(rec.get("annual_maintenance_cost", 0) or 0),
            replacement_cost=float(rec.get("replacement_cost", 0) or 0),
            site=rec.get("site"),
            rack_location=rec.get("rack_location"),
            notes=rec.get("notes"),
        )
        if result["action"] == "created":
            created += 1
        else:
            updated += 1

    if own_conn:
        conn.close()
    return {"created": created, "updated": updated, "skipped": skipped, "total": len(records)}


# ── Criticality scoring ─────────────────────────────────────────────────────

def compute_criticality_scores(topology_id: str, conn=None) -> dict:
    """Compute criticality scores for all devices via downstream BFS.

    Criticality = normalized downstream_count (devices that depend on this one).
    """
    own_conn = conn is None
    if own_conn:
        conn = _get_conn()

    # Load topology graph
    row = conn.execute(
        "SELECT graph_json FROM topologies WHERE id = %s", (topology_id,)
    ).fetchone()
    if not row:
        return {"error": "Topology not found"}

    graph = json.loads(row[0] if isinstance(row, tuple) else row["graph_json"])
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])

    # Build undirected adjacency
    adj: dict[str, set[str]] = defaultdict(set)
    for e in edges:
        adj[e["source"]].add(e["target"])
        adj[e["target"]].add(e["source"])

    # BFS from each node to count reachable nodes (downstream = connected component size - 1)
    node_ids = {n["id"] for n in nodes}
    downstream_counts: dict[str, int] = {}

    for nid in node_ids:
        # Count nodes reachable if this node is removed (inverse of blast radius)
        # Simpler: count all neighbors transitively = influence measure
        visited: set[str] = set()
        queue = [nid]
        visited.add(nid)
        while queue:
            current = queue.pop(0)
            for neighbor in adj.get(current, set()):
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append(neighbor)
        downstream_counts[nid] = len(visited) - 1  # exclude self

    # Normalize to 0-1
    max_count = max(downstream_counts.values()) if downstream_counts else 1
    if max_count == 0:
        max_count = 1

    now = datetime.now(timezone.utc).isoformat()
    updated = 0
    for nid, count in downstream_counts.items():
        score = round(count / max_count, 3)
        conn.execute(
            "UPDATE ni_devices SET criticality_score = %s, downstream_count = %s, updated_at = %s "
            "WHERE topology_id = %s AND node_id = %s",
            (score, count, now, topology_id, nid),
        )
        updated += 1
    conn.commit()

    if own_conn:
        conn.close()
    return {"updated": updated, "max_downstream": max_count}


# ── EOL timeline ─────────────────────────────────────────────────────────────

def get_eol_timeline(topology_id: str, years: int = 5, conn=None) -> list[dict]:
    """Get devices grouped by EOL year within the horizon."""
    own_conn = conn is None
    if own_conn:
        conn = _get_conn()

    now_year = datetime.now(timezone.utc).year
    cutoff = f"{now_year + years}-12-31"

    rows = conn.execute(
        "SELECT * FROM ni_devices WHERE topology_id = %s AND eol_date IS NOT NULL "
        "AND eol_date <= %s ORDER BY eol_date ASC",
        (topology_id, cutoff),
    ).fetchall()

    if own_conn:
        conn.close()

    by_year: dict[int, list[dict]] = defaultdict(list)
    for row in rows:
        d = dict(row) if hasattr(row, "keys") else {"id": row[0], "label": row[3], "eol_date": row[8]}
        eol_str = d.get("eol_date", "")
        if eol_str:
            try:
                year = int(eol_str[:4])
                by_year[year].append(d)
            except (ValueError, IndexError):
                pass

    timeline = []
    for year in range(now_year, now_year + years + 1):
        devices = by_year.get(year, [])
        total_replacement = sum(float(d.get("replacement_cost", 0) or 0) for d in devices)
        timeline.append({
            "year": year,
            "device_count": len(devices),
            "total_replacement_cost": round(total_replacement, 2),
            "devices": [{"id": d.get("id"), "label": d.get("label"), "eol_date": d.get("eol_date"),
                         "replacement_cost": d.get("replacement_cost", 0)} for d in devices],
        })

    return timeline


# ── CLI ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="ICDEV™ Network Device Manager")
    parser.add_argument("--topology-id", required=True, help="Topology ID")
    parser.add_argument("--list", action="store_true", help="List devices")
    parser.add_argument("--eol-timeline", action="store_true", help="EOL timeline")
    parser.add_argument("--years", type=int, default=5, help="EOL horizon years")
    parser.add_argument("--compute-criticality", action="store_true", help="Compute criticality scores")
    parser.add_argument("--bulk-import", metavar="FILE", help="Bulk import from CSV/JSON")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    result: Any = None

    if args.list:
        result = get_devices(args.topology_id)
    elif args.eol_timeline:
        result = get_eol_timeline(args.topology_id, args.years)
    elif args.compute_criticality:
        result = compute_criticality_scores(args.topology_id)
    elif args.bulk_import:
        result = bulk_import_devices(args.topology_id, args.bulk_import)
    else:
        parser.print_help()
        return

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
