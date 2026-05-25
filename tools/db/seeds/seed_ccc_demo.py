#!/usr/bin/env python3
# CUI // SP-CTI
"""CCC Demo Seed -- populates ccc_canvas.db with realistic circuit & capacity demo data.

Tables seeded:
  ccc_circuits (15), ccc_cross_connects (10), ccc_loa_requests (5),
  ccc_capacity_plans (8), ccc_dwdm_spans (6), ccc_xc_order_events (15)

Usage:
    python tools/db/seeds/seed_ccc_demo.py --all [--reset] [--json]
    python tools/db/seeds/seed_ccc_demo.py --verify --json
"""
from __future__ import annotations

import argparse
import json
import random
import sqlite3
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_ROOT))

random.seed(42)

_NOW = datetime.now(timezone.utc)
_T0 = _NOW - timedelta(hours=72)


def _ts(offset_hours: float = 0.0) -> str:
    return (_T0 + timedelta(hours=offset_hours)).isoformat()


def _uid() -> str:
    return str(uuid.uuid4())


def _get_conn():
    try:
        from tools.ccc_canvas.db.init_db import get_connection, init_db
        conn = get_connection()
        init_db()
        return conn
    except Exception:
        db = _ROOT / "data" / "ccc_canvas.db"
        db.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        return conn


def _reset_demo_data(conn) -> None:
    for tbl in ("ccc_xc_order_events", "ccc_capacity_plans", "ccc_loa_requests", "ccc_cross_connects", "ccc_circuits", "ccc_dwdm_spans"):
        try:
            conn.execute(f"DELETE FROM {tbl}")
        except Exception:
            pass
    conn.commit()


_CIRCUITS = [
    ("CIR-1001", "100g", "Lumen", 100.0, "POP-Ashburn", "POP-Dallas", "active", "LUM-12345-A", 45000.0, "2023-01-15", "2027-01-14", 72.5),
    ("CIR-1002", "100g", "ATT", 100.0, "POP-Ashburn", "POP-Seattle", "active", "ATT-56789-B", 52000.0, "2023-03-01", "2027-02-28", 58.2),
    ("CIR-1003", "wavelength", "Zayo", 100.0, "POP-Dallas", "DC-Dallas", "active", "ZAY-98765-C", 38000.0, "2022-11-01", "2026-10-31", 45.0),
    ("CIR-1004", "ethernet", "Cogent", 10.0, "DC-Chicago", "POP-Chicago", "active", "COG-11111-D", 8500.0, "2024-01-01", "2026-12-31", 33.1),
    ("CIR-1005", "100g", "Verizon", 100.0, "POP-London", "POP-Amsterdam", "active", "VZ-22222-E", 48000.0, "2023-06-01", "2027-05-31", 81.0),
    ("CIR-1006", "mpls", "Lumen", 10.0, "DC-Dallas", "DC-Chicago", "active", "LUM-33333-F", 12000.0, "2024-02-01", "2026-01-31", 28.4),
    ("CIR-1007", "dark_fiber", "Zayo", 400.0, "POP-Seattle", "POP-Portland", "active", "ZAY-44444-G", 95000.0, "2021-08-01", "2026-07-31", 12.0),
    ("CIR-1008", "oc192", "ATT", 10.0, "POP-Miami", "POP-SaoPaulo", "maintenance", "ATT-55555-H", 28000.0, "2022-05-01", "2026-04-30", 0.0),
    ("CIR-1009", "ethernet", "Cogent", 1.0, "DC-Ashburn", "POP-Ashburn", "active", "COG-66666-I", 3500.0, "2024-03-01", "2026-02-28", 67.8),
    ("CIR-1010", "100g", "Verizon", 100.0, "POP-Dallas", "POP-Houston", "pending", "VZ-77777-J", 46000.0, "2025-01-01", "2028-12-31", 0.0),
    ("CIR-1011", "ip_vpn", "Lumen", 1.0, "DC-Chicago", "DC-Dallas", "active", "LUM-88888-K", 6500.0, "2023-09-01", "2026-08-31", 41.2),
    ("CIR-1012", "wavelength", "ATT", 100.0, "POP-London", "POP-Frankfurt", "active", "ATT-99999-L", 55000.0, "2023-04-01", "2027-03-31", 55.5),
    ("CIR-1013", "ethernet", "Zayo", 10.0, "POP-Amsterdam", "DC-Amsterdam", "active", "ZAY-00000-M", 9200.0, "2024-04-01", "2027-03-31", 19.7),
    ("CIR-1014", "100g", "Cogent", 100.0, "POP-Seattle", "POP-Tokyo", "test", "COG-12121-N", 42000.0, "2025-06-01", "2028-05-31", 5.0),
    ("CIR-1015", "dark_fiber", "Verizon", 400.0, "POP-Ashburn", "DC-Ashburn", "active", "VZ-23232-O", 88000.0, "2022-01-01", "2027-12-31", 8.5),
]

_CROSS_CONNECTS = [
    ("XC-001", "equinix", "EQ-ASH-01", "Port-A1", "Port-Z1", 10.0, "active", 1, 1200.0, "PP-A1", "Cross-connect for CIR-1001"),
    ("XC-002", "megaport", "MP-DAL-01", "Port-A2", "Port-Z2", 1.0, "active", 3, 450.0, "PP-A2", "Dallas local handoff"),
    ("XC-003", "zayo", "ZAY-SEA-01", "Port-A3", "Port-Z3", 10.0, "active", 2, 850.0, "PP-A3", "Seattle aggregation"),
    ("XC-004", "equinix", "EQ-LON-01", "Port-A4", "Port-Z4", 10.0, "active", 5, 1500.0, "PP-A4", "London IX patch"),
    ("XC-005", "coresite", "CS-CHI-01", "Port-A5", "Port-Z5", 1.0, "pending_loa", 4, 600.0, "PP-A5", "Chicago pending LOA"),
    ("XC-006", "digital_realty", "DR-AMS-01", "Port-A6", "Port-Z6", 10.0, "installing", 13, 1100.0, "PP-A6", "Amsterdam install in progress"),
    ("XC-007", "lumen", "LUM-TYO-01", "Port-A7", "Port-Z7", 100.0, "active", 14, 3200.0, "PP-A7", "Tokyo 100G handoff"),
    ("XC-008", "equinix", "EQ-ASH-02", "Port-A8", "Port-Z8", 1.0, "active", 11, 500.0, "PP-A8", "Ashburn backup link"),
    ("XC-009", "zayo", "ZAY-POR-01", "Port-A9", "Port-Z9", 10.0, "active", 7, 750.0, "PP-A9", "Portland dark fiber patch"),
    ("XC-010", "cogent", "COG-MIA-01", "Port-A10", "Port-Z10", 10.0, "suspended", 8, 900.0, "PP-A10", "Miami maintenance"),
]

_LOA_REQUESTS = [
    ("LOA-001", 1, 1, "equinix", "Rack-A1", "Rack-Z1", "PP-A1", "PP-Z1", "John Smith", "jsmith@example.com", "Example Corp", "approved", 30, _ts(0), _ts(2), "", "Approved for CIR-1001"),
    ("LOA-002", 5, 5, "coresite", "Rack-A5", "Rack-Z5", "PP-A5", "PP-Z5", "Jane Doe", "jdoe@example.com", "Example Corp", "submitted", 30, _ts(4), "", "", "Pending approval"),
    ("LOA-003", 13, 6, "digital_realty", "Rack-A6", "Rack-Z6", "PP-A6", "PP-Z6", "Bob Brown", "bbrown@example.com", "Example Corp", "approved", 30, _ts(8), _ts(10), "", "Amsterdam install"),
    ("LOA-004", 10, 0, "equinix", "Rack-A8", "Rack-Z8", "PP-A8", "PP-Z8", "Alice Green", "agreen@example.com", "Example Corp", "draft", 30, "", "", "", "Not yet submitted"),
    ("LOA-005", 14, 7, "lumen", "Rack-A7", "Rack-Z7", "PP-A7", "PP-Z7", "Tom White", "twhite@example.com", "Example Corp", "approved", 30, _ts(12), _ts(14), "", "Tokyo 100G"),
]

_CAPACITY_PLANS = []
for i in range(8):
    cid = i + 1
    current = round(random.uniform(20.0, 85.0), 1)
    projected = min(current + round(random.uniform(5.0, 25.0), 1), 98.0)
    months = max(0, int((100.0 - projected) / 5))
    action = random.choice(["upgrade", "monitor", "provision", "no_action", "upgrade"])
    _CAPACITY_PLANS.append({
        "id": _uid(),
        "circuit_id": cid,
        "plan_date": _ts(i * 6).split("T")[0],
        "current_utilization_pct": current,
        "projected_utilization_pct": projected,
        "months_to_saturation": months,
        "recommended_action": action,
        "growth_rate_pct": round(random.uniform(2.0, 15.0), 1),
        "confidence_score": round(random.uniform(0.6, 0.95), 2),
        "classification": "CUI",
    })

_DWDM_SPANS = [
    ("DWDM-001", "Ashburn-Dallas", 8.0, 4.5, 80, 12, 18.5, "operational", "Lumen long-haul"),
    ("DWDM-002", "Dallas-Seattle", 12.0, 7.0, 96, 18, 16.2, "operational", "Zayo backbone"),
    ("DWDM-003", "London-Amsterdam", 6.0, 3.0, 40, 6, 20.1, "operational", "Euro fiber"),
    ("DWDM-004", "Ashburn-Miami", 10.0, 2.0, 160, 14, 17.8, "degraded", "Amplifier replacement needed"),
    ("DWDM-005", "Seattle-Tokyo", 20.0, 8.0, 192, 24, 15.5, "operational", "Trans-Pacific"),
    ("DWDM-006", "Chicago-Dallas", 8.0, 1.5, 160, 10, 19.2, "operational", "ATT metro"),
]

_ORDER_EVENTS = []
for i in range(15):
    xc_id = random.randint(1, 10)
    old_st = random.choice(["not_ordered", "pending_loa", "installing", "active"])
    new_st = random.choice(["pending_loa", "installing", "active", "active"])
    _ORDER_EVENTS.append({
        "id": _uid(),
        "xc_id": xc_id,
        "event_type": random.choice(["status_change", "carrier_update", "loa_approved", "installation_complete"]),
        "old_status": old_st,
        "new_status": new_st,
        "note": random.choice(["", "Carrier confirmed delivery", "LOA received", "Installation scheduled"]),
        "actor": random.choice(["system", "noc-smith", "carrier-portal", "noc-jones"]),
        "carrier_ref": f"TKT-{random.randint(1000, 9999)}",
        "classification": "CUI",
    })


def seed_circuits(conn) -> int:
    sql = """INSERT OR IGNORE INTO ccc_circuits (
        circuit_id, circuit_type, carrier, bandwidth_gbps, endpoint_a, endpoint_z, status,
        provider_circuit_id, mrr_usd, install_date, contract_end, utilization_pct, classification, created_at, updated_at
    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"""
    count = 0
    for row in _CIRCUITS:
        conn.execute(sql, (*row, "CUI", _ts(count * 2), _ts(count * 2)))
        count += 1
    return count


def seed_cross_connects(conn) -> int:
    sql = """INSERT OR IGNORE INTO ccc_cross_connects (
        xc_number, facility, facility_id, port_a, port_z, speed_gbps, status, circuit_id,
        monthly_cost_usd, patch_panel, notes, classification, created_at
    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)"""
    count = 0
    for row in _CROSS_CONNECTS:
        conn.execute(sql, (*row, "CUI", _ts(count * 3)))
        count += 1
    return count


def seed_loa_requests(conn) -> int:
    sql = """INSERT OR IGNORE INTO ccc_loa_requests (
        loa_number, circuit_id, xc_id, facility, rack_a, rack_z, patch_panel_a, patch_panel_z,
        requester_name, requester_email, requester_company, status, valid_days, submitted_at,
        approved_at, loa_document_path, notes, classification, created_at
    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"""
    count = 0
    for row in _LOA_REQUESTS:
        conn.execute(sql, (*row, "CUI", _ts(count * 5)))
        count += 1
    return count


def seed_capacity_plans(conn) -> int:
    sql = """INSERT OR IGNORE INTO ccc_capacity_plans (
        circuit_id, plan_date, current_utilization_pct, projected_utilization_pct,
        months_to_saturation, recommended_action, growth_rate_pct, confidence_score, classification, created_at
    ) VALUES (?,?,?,?,?,?,?,?,?,?)"""
    count = 0
    for row in _CAPACITY_PLANS:
        vals = list(row.values())
        conn.execute(sql, vals)
        count += 1
    return count


def seed_dwdm_spans(conn) -> int:
    sql = """INSERT OR IGNORE INTO ccc_dwdm_spans (
        span_id, fiber_route, total_capacity_tbps, used_capacity_tbps, available_wavelengths,
        amplifier_count, osnr_db, status, notes, classification, created_at, updated_at
    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)"""
    count = 0
    for row in _DWDM_SPANS:
        conn.execute(sql, (*row, "CUI", _ts(count * 4), _ts(count * 4)))
        count += 1
    return count


def seed_order_events(conn) -> int:
    sql = """INSERT OR IGNORE INTO ccc_xc_order_events (
        xc_id, event_type, old_status, new_status, note, actor, carrier_ref, classification, ts
    ) VALUES (?,?,?,?,?,?,?,?,?)"""
    count = 0
    for row in _ORDER_EVENTS:
        conn.execute(sql, (
            row["xc_id"], row["event_type"], row["old_status"], row["new_status"],
            row["note"], row["actor"], row["carrier_ref"], row["classification"], _ts(count * 1.5),
        ))
        count += 1
    return count


def verify(conn) -> dict:
    result = {}
    for tbl in ("ccc_circuits", "ccc_cross_connects", "ccc_loa_requests", "ccc_capacity_plans", "ccc_dwdm_spans", "ccc_xc_order_events"):
        try:
            row = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()
            result[tbl] = row[0]
        except Exception as exc:
            result[tbl] = f"error: {exc}"
    return result


def main():
    parser = argparse.ArgumentParser(description="CCC Demo Seed")
    parser.add_argument("--reset", action="store_true", help="Clear existing demo data")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    parser.add_argument("--verify", action="store_true", help="Only verify counts")
    args = parser.parse_args()

    conn = _get_conn()
    try:
        if args.verify:
            result = verify(conn)
            print(json.dumps(result, indent=2) if args.json else result)
            return

        if args.reset:
            _reset_demo_data(conn)

        counts = {
            "ccc_circuits": seed_circuits(conn),
            "ccc_cross_connects": seed_cross_connects(conn),
            "ccc_loa_requests": seed_loa_requests(conn),
            "ccc_capacity_plans": seed_capacity_plans(conn),
            "ccc_dwdm_spans": seed_dwdm_spans(conn),
            "ccc_xc_order_events": seed_order_events(conn),
        }
        conn.commit()

        if args.json:
            print(json.dumps({"success": True, "seeded": counts, "verify": verify(conn)}, indent=2))
        else:
            print(f"[seed_ccc] Seeded {counts}")
            print(f"[seed_ccc] Verify: {verify(conn)}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
