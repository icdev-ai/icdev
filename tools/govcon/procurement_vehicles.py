"""Procurement contract vehicle tracker for GovCon initiatives.

Captures GWAC/BPA/IDIQ vehicle metadata: contract_number, NAICS code,
ceiling_value, expiration_date, plus supporting fields.
"""
from __future__ import annotations

import argparse
import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from tools.db.storage import get_connection
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.govcon.procurement_vehicles")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REQUIRED_FIELDS = [
    "vehicle_name",
    "contract_number",
    "ceiling_value",
    "expiration_date",
]

VALID_VEHICLE_TYPES = {"GWAC", "BPA", "IDIQ", "MATOC", "MAC", "SATOC", "OTHER"}

KNOWN_VEHICLE_SEEDS: dict[str, dict] = {
    "OASIS+": {
        "full_name": "One Acquisition Solution for Integrated Services Plus",
        "agency": "GSA",
        "vehicle_type": "GWAC",
        "contract_number": "GS00Q14OADU21",
        "naics_code": "541512",
        "ceiling_value": 60_000_000_000.0,
        "expiration_date": "2030-09-30",
    },
    "Polaris": {
        "full_name": "Polaris GWAC",
        "agency": "GSA",
        "vehicle_type": "GWAC",
        "contract_number": "47QTCB22D0014",
        "naics_code": "541512",
        "ceiling_value": 15_000_000_000.0,
        "expiration_date": "2031-12-19",
    },
    "CIO-SP4": {
        "full_name": "Chief Information Officer Solutions and Partners 4",
        "agency": "NIH",
        "vehicle_type": "GWAC",
        "contract_number": "75N98120D00001",
        "naics_code": "541512",
        "ceiling_value": 20_000_000_000.0,
        "expiration_date": "2032-06-01",
    },
    "GSA MAS": {
        "full_name": "General Services Administration Multiple Award Schedule",
        "agency": "GSA",
        "vehicle_type": "IDIQ",
        "contract_number": "GS-00F-0000X",
        "naics_code": "",
        "ceiling_value": 0.0,
        "expiration_date": "2030-12-31",
    },
    "SEWP": {
        "full_name": "Solutions for Enterprise-Wide Procurement V",
        "agency": "NASA",
        "vehicle_type": "GWAC",
        "contract_number": "NNG15SC98B",
        "naics_code": "519519",
        "ceiling_value": 20_000_000_000.0,
        "expiration_date": "2025-12-31",
    },
}

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS procurement_vehicles (
    id TEXT PRIMARY KEY,
    vehicle_name TEXT NOT NULL UNIQUE,
    full_name TEXT,
    agency TEXT,
    vehicle_type TEXT,
    contract_number TEXT NOT NULL,
    naics_code TEXT,
    ceiling_value REAL NOT NULL DEFAULT 0,
    expiration_date TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""

_IDX_VEHICLE_NAME = (
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_pv_vehicle_name "
    "ON procurement_vehicles(vehicle_name)"
)
_IDX_NAICS = (
    "CREATE INDEX IF NOT EXISTS idx_pv_naics "
    "ON procurement_vehicles(naics_code)"
)


def _ensure_tables() -> None:
    conn = get_connection()
    conn.execute(_CREATE_TABLE)
    conn.execute(_IDX_VEHICLE_NAME)
    conn.execute(_IDX_NAICS)
    # Idempotently add project_id to audit_trail so callers can query by it
    try:
        conn.execute("ALTER TABLE audit_trail ADD COLUMN project_id TEXT")
        conn.commit()
    except Exception:
        pass
    conn.commit()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _audit(action: str, details: str, project_id: str = "") -> None:
    try:
        conn = get_connection()
        conn.execute(
            "INSERT INTO audit_trail "
            "(id, tenant_id, user_id, action, resource, details, project_id, classification, recorded_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (str(uuid.uuid4()), None, None, action,
             "procurement_vehicles", details, project_id, "CUI", _now()),
        )
        conn.commit()
    except Exception:
        # Fallback: insert without project_id column in case schema differs
        try:
            conn = get_connection()
            conn.execute(
                "INSERT INTO audit_trail "
                "(id, tenant_id, user_id, action, resource, details, classification, recorded_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (str(uuid.uuid4()), None, None, action,
                 "procurement_vehicles", details, "CUI", _now()),
            )
            conn.commit()
        except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
            logger.warning("_audit: best-effort INSERT into audit_trail failed (non-blocking): %s", exc)


def _validate_create(
    vehicle_name: str,
    contract_number: str,
    naics_code: str,
    ceiling_value: float,
    expiration_date: str,
    vehicle_type: str,
) -> str | None:
    if not vehicle_name:
        return "vehicle_name is required"
    if not contract_number:
        return "contract_number is required"
    if not expiration_date:
        return "expiration_date is required"
    if ceiling_value < 0:
        return "ceiling_value must be >= 0"
    if naics_code and not re.fullmatch(r"\d{6}", naics_code):
        return "naics_code must be 6 digits or empty"
    if vehicle_type and vehicle_type not in VALID_VEHICLE_TYPES:
        return f"vehicle_type must be one of {sorted(VALID_VEHICLE_TYPES)}"
    return None


def _row_to_dict(row: Any) -> dict:
    try:
        return dict(row)
    except TypeError:
        return {k: row[k] for k in row.keys()}


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

def create_vehicle(
    vehicle_name: str,
    full_name: str = "",
    agency: str = "",
    vehicle_type: str = "",
    contract_number: str = "",
    naics_code: str = "",
    ceiling_value: float = 0.0,
    expiration_date: str = "",
) -> dict[str, Any]:
    _ensure_tables()
    err = _validate_create(
        vehicle_name, contract_number, naics_code, ceiling_value, expiration_date, vehicle_type
    )
    if err:
        return {"status": "error", "message": err}

    conn = get_connection()
    existing = conn.execute(
        "SELECT id FROM procurement_vehicles WHERE vehicle_name = %s", (vehicle_name,)
    ).fetchone()
    if existing:
        return {"status": "error", "message": f"Vehicle '{vehicle_name}' already exists"}

    vid = str(uuid.uuid4())
    now = _now()
    conn.execute(
        "INSERT INTO procurement_vehicles "
        "(id, vehicle_name, full_name, agency, vehicle_type, "
        "contract_number, naics_code, ceiling_value, expiration_date, created_at, updated_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (vid, vehicle_name, full_name, agency, vehicle_type,
         contract_number, naics_code, ceiling_value, expiration_date, now, now),
    )
    conn.commit()
    _audit("create_vehicle", vehicle_name, project_id=vehicle_name)
    return {"status": "ok", "vehicle_id": vid, "vehicle_name": vehicle_name}


def get_vehicle(vehicle_name: str) -> dict[str, Any]:
    _ensure_tables()
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM procurement_vehicles WHERE vehicle_name = %s", (vehicle_name,)
    ).fetchone()
    if not row:
        return {"status": "error", "message": f"Vehicle '{vehicle_name}' not found"}
    return {"status": "ok", "vehicle": _row_to_dict(row)}


def list_vehicles(agency: str | None = None) -> dict[str, Any]:
    _ensure_tables()
    conn = get_connection()
    if agency:
        rows = conn.execute(
            "SELECT * FROM procurement_vehicles WHERE agency = %s ORDER BY vehicle_name",
            (agency,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM procurement_vehicles ORDER BY vehicle_name"
        ).fetchall()
    vehicles = [_row_to_dict(r) for r in rows]
    return {"status": "ok", "count": len(vehicles), "vehicles": vehicles}


def update_vehicle(vehicle_name: str, **fields: Any) -> dict[str, Any]:
    _ensure_tables()
    conn = get_connection()
    existing = conn.execute(
        "SELECT id FROM procurement_vehicles WHERE vehicle_name = %s", (vehicle_name,)
    ).fetchone()
    if not existing:
        return {"status": "error", "message": f"Vehicle '{vehicle_name}' not found"}

    allowed = {"full_name", "agency", "vehicle_type", "contract_number",
               "naics_code", "ceiling_value", "expiration_date"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return {"status": "ok", "message": "nothing to update"}

    updates["updated_at"] = _now()
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [vehicle_name]
    conn.execute(
        f"UPDATE procurement_vehicles SET {set_clause} WHERE vehicle_name = %s",
        values,
    )
    conn.commit()
    _audit("update_vehicle", vehicle_name, project_id=vehicle_name)
    return {"status": "ok", "vehicle_name": vehicle_name, "updated": list(updates.keys())}


def delete_vehicle(vehicle_name: str) -> dict[str, Any]:
    _ensure_tables()
    conn = get_connection()
    existing = conn.execute(
        "SELECT id FROM procurement_vehicles WHERE vehicle_name = %s", (vehicle_name,)
    ).fetchone()
    if not existing:
        return {"status": "error", "message": f"Vehicle '{vehicle_name}' not found"}

    conn.execute(
        "DELETE FROM procurement_vehicles WHERE vehicle_name = %s", (vehicle_name,)
    )
    conn.commit()
    _audit("delete_vehicle", vehicle_name, project_id=vehicle_name)
    return {"status": "ok", "vehicle_name": vehicle_name}


# ---------------------------------------------------------------------------
# Seed
# ---------------------------------------------------------------------------

def seed_known_vehicles() -> dict[str, Any]:
    _ensure_tables()
    seeded = 0
    skipped = 0
    for name, meta in KNOWN_VEHICLE_SEEDS.items():
        result = create_vehicle(vehicle_name=name, **meta)
        if result["status"] == "ok":
            seeded += 1
        else:
            skipped += 1
    return {"status": "ok", "seeded": seeded, "skipped": skipped}


# ---------------------------------------------------------------------------
# Expiration helpers
# ---------------------------------------------------------------------------

def is_expired(expiration_date: str) -> bool:
    try:
        exp_date = datetime.fromisoformat(expiration_date).date()
        today = datetime.now(timezone.utc).date()
        return exp_date < today
    except (ValueError, TypeError):
        return True  # treat unparseable as expired


def days_to_expiration(expiration_date: str) -> int | None:
    try:
        exp_date = datetime.fromisoformat(expiration_date).date()
        today = datetime.now(timezone.utc).date()
        return (exp_date - today).days
    except (ValueError, TypeError):
        return None


def list_expiring_soon(days: int = 180) -> dict[str, Any]:
    _ensure_tables()
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM procurement_vehicles ORDER BY expiration_date"
    ).fetchall()
    result = []
    for row in rows:
        d = _row_to_dict(row)
        remaining = days_to_expiration(d.get("expiration_date", ""))
        if remaining is not None and 0 <= remaining <= days:
            result.append(d)
    return {"status": "ok", "count": len(result), "vehicles": result}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    _ensure_tables()
    parser = argparse.ArgumentParser(description="Procurement vehicle tracker")
    parser.add_argument("--create", action="store_true")
    parser.add_argument("--get", action="store_true")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--update", action="store_true")
    parser.add_argument("--delete", action="store_true")
    parser.add_argument("--seed", action="store_true")
    parser.add_argument("--expiring", action="store_true")
    parser.add_argument("--vehicle-name")
    parser.add_argument("--full-name", default="")
    parser.add_argument("--agency", default="")
    parser.add_argument("--vehicle-type", default="")
    parser.add_argument("--contract-number", default="")
    parser.add_argument("--naics-code", default="")
    parser.add_argument("--ceiling-value", type=float, default=0.0)
    parser.add_argument("--expiration-date", default="")
    parser.add_argument("--days", type=int, default=180)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    result: dict = {}
    if args.create:
        result = create_vehicle(
            vehicle_name=args.vehicle_name or "",
            full_name=args.full_name,
            agency=args.agency,
            vehicle_type=args.vehicle_type,
            contract_number=args.contract_number,
            naics_code=args.naics_code,
            ceiling_value=args.ceiling_value,
            expiration_date=args.expiration_date,
        )
    elif args.get:
        result = get_vehicle(vehicle_name=args.vehicle_name or "")
    elif args.list:
        result = list_vehicles(agency=args.agency or None)
    elif args.update:
        kw: dict[str, Any] = {}
        if args.contract_number:
            kw["contract_number"] = args.contract_number
        if args.ceiling_value:
            kw["ceiling_value"] = args.ceiling_value
        if args.expiration_date:
            kw["expiration_date"] = args.expiration_date
        result = update_vehicle(vehicle_name=args.vehicle_name or "", **kw)
    elif args.delete:
        result = delete_vehicle(vehicle_name=args.vehicle_name or "")
    elif args.seed:
        result = seed_known_vehicles()
    elif args.expiring:
        result = list_expiring_soon(days=args.days)

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
