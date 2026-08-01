#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV™ System Administrator
"""Procurement Quote vs IGCE Comparison Engine.

Captures the Independent Government Cost Estimate (IGCE) line items for a
procurement and the vendor quotes submitted against each line item, then
performs side-by-side comparison, rollup, and variance gating.

The IGCE is the Government's pre-solicitation estimate of the cost of an
acquisition (FAR 15.404-1, DoD IGCE Handbook). Comparing vendor quotes
against the IGCE line by line detects overpriced bids, unrealistic low
bids, and pricing trends across vendors.

Deterministic, air-gap safe (no LLM).

DB tables (write): proc_igce_line_items, proc_vendor_quotes, audit_trail

The 9 required BOM line-item fields map onto the schema as follows:
    Field 1  Vendor           -> proc_vendor_quotes.vendor_name
    Field 2  Item             -> proc_igce_line_items.description (line item)
    Field 3  Qty              -> proc_igce_line_items.quantity
    Field 4  Estimate ($) IGCE -> proc_igce_line_items.unit_cost (x qty = extended_cost)
    Field 5  Quotation ($)    -> proc_vendor_quotes.unit_price (x qty = total_price)
    Field 6  Expiration       -> proc_vendor_quotes.valid_until
    Field 7  POC              -> proc_igce_line_items.poc  (govt point of contact)
    Field 8  Description      -> proc_igce_line_items.description
    Field 9  Notes            -> proc_igce_line_items.notes (+ proc_vendor_quotes.notes)

The ``add_bom_line()`` helper captures the full 9-field set in a single call
by writing one IGCE line and one vendor quote.

Usage:
    # 1) Create a procurement with IGCE line items
    python tools/govcon/procurement_quote_compare.py \\
        --create-procurement --procurement-id "PROC-2026-001" \\
        --solicitation "W912DY-26-R-0007" --agency "USACE" --json

    python tools/govcon/procurement_quote_compare.py \\
        --add-igce-line --procurement-id "PROC-2026-001" --clin "0001" \\
        --description "Junior Software Engineer (160 hrs)" \\
        --unit "hour" --quantity 160 --unit_cost 175.00 \\
        --basis "GSA CALC+ 2026 SOC 15-1252 mean" --json

    # 2) Capture vendor quotes (one or more per CLIN)
    python tools/govcon/procurement_quote_compare.py \\
        --add-quote --procurement-id "PROC-2026-001" \\
        --vendor "Acme Federal LLC" --quote-ref "AF-2026-Q-014" \\
        --clin "0001" --unit_price 165.00 --total_price 26400.00 \\
        --quote-date "2026-05-30" --json

    # 3) Compare line by line
    python tools/govcon/procurement_quote_compare.py \\
        --compare --procurement-id "PROC-2026-001" --json

    # 4) Rollup by vendor with variance flags
    python tools/govcon/procurement_quote_compare.py \\
        --summary --procurement-id "PROC-2026-001" --json

    # 5) Pass/Warn/Fail gate
    python tools/govcon/procurement_quote_compare.py \\
        --gate --procurement-id "PROC-2026-001" --max-variance-pct 15.0 --json

    # 6) List
    python tools/govcon/procurement_quote_compare.py \\
        --list-procurements --json
    python tools/govcon/procurement_quote_compare.py \\
        --list-igce --procurement-id "PROC-2026-001" --json
    python tools/govcon/procurement_quote_compare.py \\
        --list-quotes --procurement-id "PROC-2026-001" --json
"""

import argparse
import json
import statistics
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.db.storage import get_connection  # noqa: E402
from tools.common.helpers import row_to_dict  # noqa: E402


# ── Constants ─────────────────────────────────────────────────────────

# Variance thresholds (percent vs IGCE unit price) used for color flags.
#   green  = at-or-below IGCE (within tolerance)
#   yellow = over IGCE within warning band (0% to +MAX_WARN_PCT)
#   red    = over IGCE beyond warning band (>+MAX_WARN_PCT)
#   low    = unrealistically low (e.g. < -25%), may signal scope misunderstanding
MAX_WARN_PCT = 5.0      # above 0% and <= 5% over IGCE -> yellow
MAX_FAIL_PCT = 15.0     # above 5% and <= 15% over IGCE -> red (warns)
                         # > 15% over IGCE -> red (fails)
UNREASONABLE_LOW_PCT = -25.0  # more than 25% below IGCE -> "low" (red)

# Gate verdict
GATE_VERDICTS = {"pass", "warn", "fail"}

# Quote status (lifecycle)
QUOTE_STATUSES = {"submitted", "under_review", "awarded", "rejected"}


# ── DB bootstrap ──────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _gen_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _get_db():
    return get_connection()


def _audit(
    conn,
    event_type: str,
    action: str,
    details: Dict[str, Any],
    procurement_id: Optional[str] = None,
) -> None:
    """Append-only audit row. Never UPDATE/DELETE."""
    det = json.dumps(details) if isinstance(details, (dict, list)) else str(details)
    try:
        conn.execute(
            "INSERT INTO audit_trail "
            "(created_at, event_type, actor, action, details, project_id, session_id) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (
                _now(),
                event_type,
                "procurement_quote_compare",
                action,
                det,
                procurement_id,
                None,
            ),
        )
    except Exception:
        # Audit must never break the operation; swallow to keep determinism.
        pass


# Equipment category values used for BOM rollup. Operators may add
# free-form values via set_equipment_category(); the rollup groups by
# whatever string is stored, so the taxonomy is open-ended but this
# list documents the standard categories used in the year-end budget
# sprint use case.
EQUIPMENT_CATEGORIES = (
    "network",
    "compute",
    "storage",
    "peripheral",
    "cabling",
    "power",
    "software",
    "services",
    "other",
    "unspecified",
)


def _ensure_tables(conn) -> None:
    """Create procurement IGCE / quote tables if they don't exist."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS proc_igce_line_items (
            id                  TEXT PRIMARY KEY,
            procurement_id      TEXT NOT NULL,
            clin                TEXT NOT NULL DEFAULT '',
            description         TEXT NOT NULL DEFAULT '',
            unit                TEXT NOT NULL DEFAULT 'each',
            quantity            REAL NOT NULL DEFAULT 1.0,
            unit_cost           REAL NOT NULL DEFAULT 0.0,
            extended_cost       REAL NOT NULL DEFAULT 0.0,
            basis               TEXT NOT NULL DEFAULT '',
            poc                 TEXT NOT NULL DEFAULT '',
            notes               TEXT,
            equipment_category  TEXT NOT NULL DEFAULT 'unspecified',
            metadata            TEXT DEFAULT '{}',
            created_at          TEXT NOT NULL,
            updated_at          TEXT NOT NULL,
            classification      TEXT DEFAULT 'CUI',
            UNIQUE (procurement_id, clin)
        )
        """
    )
    # Backfill columns: if the table existed before these were added,
    # add them now. Safe no-op when the column already exists.
    for _ddl in (
        "ALTER TABLE proc_igce_line_items ADD COLUMN poc TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE proc_igce_line_items ADD COLUMN equipment_category TEXT NOT NULL DEFAULT 'unspecified'",
    ):
        try:
            conn.execute(_ddl)
        except Exception:
            pass
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS proc_vendor_quotes (
            id              TEXT PRIMARY KEY,
            procurement_id  TEXT NOT NULL,
            vendor_name     TEXT NOT NULL,
            quote_ref       TEXT NOT NULL DEFAULT '',
            clin            TEXT NOT NULL DEFAULT '',
            unit_price      REAL NOT NULL DEFAULT 0.0,
            quantity        REAL,
            total_price     REAL NOT NULL DEFAULT 0.0,
            quote_date      TEXT,
            valid_until     TEXT,
            status          TEXT NOT NULL DEFAULT 'submitted',
            notes           TEXT,
            metadata        TEXT DEFAULT '{}',
            created_at      TEXT NOT NULL,
            updated_at      TEXT NOT NULL,
            classification  TEXT DEFAULT 'CUI',
            UNIQUE (procurement_id, vendor_name, quote_ref, clin)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS proc_procurements (
            id              TEXT PRIMARY KEY,
            solicitation    TEXT NOT NULL DEFAULT '',
            title           TEXT NOT NULL DEFAULT '',
            agency          TEXT NOT NULL DEFAULT '',
            contract_type   TEXT NOT NULL DEFAULT 'ffp',
            description     TEXT,
            status          TEXT NOT NULL DEFAULT 'open',
            allocation_id   TEXT,
            metadata        TEXT DEFAULT '{}',
            created_at      TEXT NOT NULL,
            updated_at      TEXT NOT NULL,
            classification  TEXT DEFAULT 'CUI'
        )
        """
    )
    # Backfill: allocation_id FK added later. Nullable — existing rows
    # remain unlinked.
    try:
        conn.execute(
            "ALTER TABLE proc_procurements ADD COLUMN allocation_id TEXT"
        )
    except Exception:
        pass
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_proc_alloc ON proc_procurements(allocation_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_igce_category "
        "ON proc_igce_line_items(equipment_category)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_igce_proc ON proc_igce_line_items(procurement_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_quote_proc ON proc_vendor_quotes(procurement_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_quote_vendor ON proc_vendor_quotes(vendor_name)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_quote_clin ON proc_vendor_quotes(clin)"
    )


# ── Variance helpers ──────────────────────────────────────────────────

def _variance_pct(quote_price: float, igce_price: float) -> Optional[float]:
    """Return (quote - igce) / igce * 100, or None when IGCE is zero/negative."""
    if igce_price is None or igce_price <= 0:
        return None
    if quote_price is None:
        return None
    return round(((quote_price - igce_price) / igce_price) * 100.0, 2)


def _classify_variance(variance_pct: Optional[float]) -> str:
    """Color-code the variance for human review.

    green  = at or below IGCE (quote is fair or better)
    yellow = slight over (0% to MAX_WARN_PCT) — review
    red    = > MAX_WARN_PCT over IGCE (overpriced) OR < UNREASONABLE_LOW_PCT
             (unrealistic low — possible scope misread or unbalanced bid)
    """
    if variance_pct is None:
        return "unknown"
    if variance_pct <= 0:
        if variance_pct < UNREASONABLE_LOW_PCT:
            return "red"
        return "green"
    if variance_pct <= MAX_WARN_PCT:
        return "green"
    if variance_pct <= MAX_FAIL_PCT:
        return "yellow"
    return "red"


# ── Procurement lifecycle ─────────────────────────────────────────────

def create_procurement(
    procurement_id: str,
    solicitation: str = "",
    agency: str = "",
    title: str = "",
    contract_type: str = "ffp",
    description: str = "",
    allocation_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Register a new procurement (the parent container for IGCE + quotes).

    The optional ``allocation_id`` argument links the procurement to a
    budget initiative allocation (cpmp_budget_allocations.id) so that
    bom_by_tier_and_category() can roll up the BOM by tier × category.
    Pass it at creation time, or call link_procurement_to_initiative()
    later to set or change it.
    """
    if not procurement_id:
        return {"status": "error", "message": "procurement_id is required"}

    conn = _get_db()
    _ensure_tables(conn)

    existing = conn.execute(
        "SELECT id FROM proc_procurements WHERE id = %s", (procurement_id,)
    ).fetchone()
    if existing:
        return {
            "status": "error",
            "message": f"procurement_id {procurement_id} already exists",
        }

    now = _now()
    conn.execute(
        """
        INSERT INTO proc_procurements
            (id, solicitation, title, agency, contract_type, description, status,
             allocation_id, metadata, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, 'open', %s, '{}', %s, %s)
        """,
        (procurement_id, solicitation, title, agency, contract_type,
         description, allocation_id, now, now),
    )
    _audit(conn, "procurement.created", "create_procurement", {
        "procurement_id": procurement_id,
        "solicitation": solicitation,
        "agency": agency,
        "contract_type": contract_type,
        "allocation_id": allocation_id,
    }, procurement_id)
    conn.commit()

    return {
        "status": "ok",
        "procurement_id": procurement_id,
        "solicitation": solicitation,
        "agency": agency,
        "contract_type": contract_type,
        "allocation_id": allocation_id,
        "created_at": now,
    }


def list_procurements() -> Dict[str, Any]:
    conn = _get_db()
    _ensure_tables(conn)
    rows = conn.execute(
        "SELECT * FROM proc_procurements ORDER BY created_at DESC"
    ).fetchall()
    return {
        "status": "ok",
        "count": len(rows),
        "procurements": [row_to_dict(r) for r in rows],
    }


# ── IGCE line items ──────────────────────────────────────────────────

def add_igce_line(
    procurement_id: str,
    clin: str,
    description: str,
    unit: str = "each",
    quantity: float = 1.0,
    unit_cost: float = 0.0,
    basis: str = "",
    notes: str = "",
    poc: str = "",
    equipment_category: str = "unspecified",
) -> Dict[str, Any]:
    """Add (or upsert) an IGCE line item for the procurement.

    The optional ``poc`` argument captures the Government Point of Contact
    (name, email, phone) for this line item — one of the 9 required BOM
    fields. Defaults to empty string.

    The optional ``equipment_category`` argument is used to roll up the
    BOM by category (e.g. "network", "compute", "cabling") when grouped
    with the initiative tier. Defaults to "unspecified" so legacy
    callers continue to work without modification.
    """
    if not procurement_id or not clin:
        return {
            "status": "error",
            "message": "procurement_id and clin are required",
        }
    if quantity <= 0:
        return {"status": "error", "message": "quantity must be > 0"}
    if unit_cost < 0:
        return {"status": "error", "message": "unit_cost must be >= 0"}

    conn = _get_db()
    _ensure_tables(conn)

    proc = conn.execute(
        "SELECT id FROM proc_procurements WHERE id = %s", (procurement_id,)
    ).fetchone()
    if not proc:
        return {
            "status": "error",
            "message": f"procurement_id {procurement_id} not found (create first)",
        }

    extended_cost = round(quantity * unit_cost, 2)
    now = _now()

    existing = conn.execute(
        "SELECT id FROM proc_igce_line_items WHERE procurement_id = %s AND clin = %s",
        (procurement_id, clin),
    ).fetchone()

    if existing:
        conn.execute(
            """
            UPDATE proc_igce_line_items
            SET description = %s, unit = %s, quantity = %s, unit_cost = %s,
                extended_cost = %s, basis = %s, poc = %s, equipment_category = %s,
                notes = %s, updated_at = %s
            WHERE id = %s
            """,
            (description, unit, quantity, unit_cost, extended_cost,
             basis, poc, equipment_category, notes, now, existing["id"]),
        )
        _audit(conn, "igce.updated", "add_igce_line", {
            "procurement_id": procurement_id,
            "clin": clin,
            "unit_cost": unit_cost,
            "extended_cost": extended_cost,
            "equipment_category": equipment_category,
        }, procurement_id)
        action = "updated"
    else:
        conn.execute(
            """
            INSERT INTO proc_igce_line_items
                (id, procurement_id, clin, description, unit, quantity, unit_cost,
                 extended_cost, basis, poc, equipment_category, notes, metadata,
                 created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, '{}', %s, %s)
            """,
            (_gen_id("igce"), procurement_id, clin, description, unit,
             quantity, unit_cost, extended_cost, basis, poc,
             equipment_category, notes, now, now),
        )
        _audit(conn, "igce.created", "add_igce_line", {
            "procurement_id": procurement_id,
            "clin": clin,
            "unit_cost": unit_cost,
            "extended_cost": extended_cost,
            "equipment_category": equipment_category,
        }, procurement_id)
        action = "created"

    conn.commit()
    return {
        "status": "ok",
        "action": action,
        "procurement_id": procurement_id,
        "clin": clin,
        "unit_cost": unit_cost,
        "quantity": quantity,
        "extended_cost": extended_cost,
        "equipment_category": equipment_category,
    }


def list_igce(procurement_id: str) -> Dict[str, Any]:
    conn = _get_db()
    _ensure_tables(conn)
    rows = conn.execute(
        "SELECT * FROM proc_igce_line_items WHERE procurement_id = %s ORDER BY clin",
        (procurement_id,),
    ).fetchall()
    total = round(sum(float(r["extended_cost"] or 0) for r in rows), 2)
    return {
        "status": "ok",
        "procurement_id": procurement_id,
        "count": len(rows),
        "total_igce": total,
        "lines": [row_to_dict(r) for r in rows],
    }


# ── Vendor quotes ────────────────────────────────────────────────────

def add_quote(
    procurement_id: str,
    vendor_name: str,
    clin: str,
    unit_price: float,
    quote_ref: str = "",
    quantity: Optional[float] = None,
    total_price: Optional[float] = None,
    quote_date: str = "",
    valid_until: str = "",
    status: str = "submitted",
    notes: str = "",
) -> Dict[str, Any]:
    """Capture a vendor quote for a specific CLIN."""
    if not procurement_id or not vendor_name or not clin:
        return {
            "status": "error",
            "message": "procurement_id, vendor_name, and clin are required",
        }
    if unit_price < 0:
        return {"status": "error", "message": "unit_price must be >= 0"}
    if status not in QUOTE_STATUSES:
        return {
            "status": "error",
            "message": f"status must be one of {sorted(QUOTE_STATUSES)}",
        }

    conn = _get_db()
    _ensure_tables(conn)

    # Verify procurement + matching IGCE line exist
    proc = conn.execute(
        "SELECT id FROM proc_procurements WHERE id = %s", (procurement_id,)
    ).fetchone()
    if not proc:
        return {
            "status": "error",
            "message": f"procurement_id {procurement_id} not found",
        }
    igce = conn.execute(
        "SELECT id, unit_cost, quantity FROM proc_igce_line_items "
        "WHERE procurement_id = %s AND clin = %s",
        (procurement_id, clin),
    ).fetchone()
    if not igce:
        return {
            "status": "error",
            "message": f"no IGCE line for clin {clin} in {procurement_id} "
                       f"(add IGCE first)",
        }

    # If quantity/total_price omitted, derive from IGCE quantity
    if quantity is None:
        quantity = float(igce["quantity"])
    if total_price is None:
        total_price = round(quantity * unit_price, 2)

    now = _now()
    try:
        conn.execute(
            """
            INSERT INTO proc_vendor_quotes
                (id, procurement_id, vendor_name, quote_ref, clin, unit_price,
                 quantity, total_price, quote_date, valid_until, status, notes,
                 metadata, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, '{}', %s, %s)
            """,
            (_gen_id("quote"), procurement_id, vendor_name, quote_ref, clin,
             unit_price, quantity, total_price, quote_date, valid_until,
             status, notes, now, now),
        )
    except Exception as exc:
        # Unique constraint = duplicate quote
        if "UNIQUE" in str(exc) or "unique" in str(exc).lower():
            return {
                "status": "error",
                "message": "duplicate quote (same vendor+quote_ref+clin)",
            }
        raise

    variance_pct = _variance_pct(unit_price, float(igce["unit_cost"]))
    flag = _classify_variance(variance_pct)

    _audit(conn, "quote.created", "add_quote", {
        "procurement_id": procurement_id,
        "vendor_name": vendor_name,
        "quote_ref": quote_ref,
        "clin": clin,
        "unit_price": unit_price,
        "variance_pct": variance_pct,
        "flag": flag,
    }, procurement_id)
    conn.commit()

    return {
        "status": "ok",
        "procurement_id": procurement_id,
        "vendor_name": vendor_name,
        "quote_ref": quote_ref,
        "clin": clin,
        "unit_price": unit_price,
        "quantity": quantity,
        "total_price": total_price,
        "igce_unit_cost": float(igce["unit_cost"]),
        "variance_pct": variance_pct,
        "flag": flag,
    }


# ── Unified BOM 9-field capture ───────────────────────────────────────

def add_bom_line(
    procurement_id: str,
    clin: str,
    item: str,
    unit: str,
    quantity: float,
    unit_cost: float,
    vendor_name: str,
    quote_ref: str,
    unit_price: float,
    quote_date: str,
    valid_until: str,
    poc: str = "",
    description: str = "",
    notes: str = "",
    basis: str = "",
    quantity_override: Optional[float] = None,
    total_price_override: Optional[float] = None,
    equipment_category: str = "unspecified",
) -> Dict[str, Any]:
    """Unified capture for one BOM line item — all 9 required fields in one call.

    The 9 fields:
        1. Vendor           -> vendor_name (also written to proc_vendor_quotes)
        2. Item             -> item (stored in proc_igce_line_items.description)
        3. Qty              -> quantity (proc_igce_line_items.quantity)
        4. Estimate ($) IGCE-> unit_cost (proc_igce_line_items.unit_cost;
                                            x quantity = extended_cost)
        5. Quotation ($)    -> unit_price (proc_vendor_quotes.unit_price;
                                            x quantity = total_price)
        6. Expiration       -> valid_until (proc_vendor_quotes.valid_until)
        7. POC              -> poc (proc_igce_line_items.poc)
        8. Description      -> description (proc_igce_line_items.description)
        9. Notes            -> notes (proc_igce_line_items.notes + quote.notes)

    Plus an optional ``equipment_category`` (10th, defaults to
    "unspecified") used by bom_by_tier_and_category() to roll up the
    BOM by category. The 9-field contract is preserved; callers that
    do not pass equipment_category continue to work.

    If a quote already exists for (procurement, vendor, quote_ref, clin) it is
    REPLACED (last-write-wins), matching the upsert semantics of add_igce_line.
    """
    if not (procurement_id and clin and vendor_name):
        return {
            "status": "error",
            "message": "procurement_id, clin, and vendor_name are required",
        }
    if quantity <= 0:
        return {"status": "error", "message": "quantity must be > 0"}
    if unit_cost < 0 or unit_price < 0:
        return {"status": "error", "message": "unit_cost and unit_price must be >= 0"}

    # 1) Upsert the IGCE row with the IGCE-side BOM fields (2, 3, 4, 7, 8, 9, +category)
    igce_result = add_igce_line(
        procurement_id=procurement_id,
        clin=clin,
        description=description or item,
        unit=unit,
        quantity=quantity,
        unit_cost=unit_cost,
        basis=basis,
        notes=notes,
        poc=poc,
        equipment_category=equipment_category,
    )
    if igce_result.get("status") != "ok":
        return igce_result

    estimate_extended = igce_result["extended_cost"]

    # 2) Upsert the vendor quote (1, 5, 6, partial 9). If a quote already
    #    exists for the (proc, vendor, quote_ref, clin) tuple, replace it so
    #    callers get last-write-wins semantics.
    conn = _get_db()
    _ensure_tables(conn)
    existing_quote = conn.execute(
        "SELECT id FROM proc_vendor_quotes "
        "WHERE procurement_id = %s AND vendor_name = %s AND quote_ref = %s AND clin = %s",
        (procurement_id, vendor_name, quote_ref, clin),
    ).fetchone()
    if existing_quote:
        now = _now()
        conn.execute(
            """
            UPDATE proc_vendor_quotes
            SET unit_price = %s, quantity = %s, total_price = %s, quote_date = %s,
                valid_until = %s, status = 'submitted', notes = %s, updated_at = %s
            WHERE id = %s
            """,
            (unit_price,
             quantity_override if quantity_override is not None else quantity,
             total_price_override if total_price_override is not None
                else round((quantity_override or quantity) * unit_price, 2),
             quote_date, valid_until, notes, now, existing_quote["id"]),
        )
        conn.commit()
        quote_action = "updated"
        # Recompute variance against the IGCE unit cost
        var = _variance_pct(unit_price, unit_cost)
        flag = _classify_variance(var)
    else:
        quote_result = add_quote(
            procurement_id=procurement_id,
            vendor_name=vendor_name,
            clin=clin,
            unit_price=unit_price,
            quote_ref=quote_ref,
            quantity=quantity_override,
            total_price=total_price_override,
            quote_date=quote_date,
            valid_until=valid_until,
            status="submitted",
            notes=notes,
        )
        if quote_result.get("status") != "ok":
            return quote_result
        quote_action = quote_result.get("action", "created")
        var = quote_result.get("variance_pct")
        flag = quote_result.get("flag")

    return {
        "status": "ok",
        "action": igce_result["action"],
        "procurement_id": procurement_id,
        "clin": clin,
        "vendor_name": vendor_name,
        "quote_ref": quote_ref,
        # Echo all 9 fields so callers can verify capture without re-reading
        "fields": {
            "vendor": vendor_name,
            "item": item,
            "quantity": quantity,
            "unit_cost": unit_cost,
            "unit_price": unit_price,
            "valid_until": valid_until,
            "poc": poc,
            "description": description or item,
            "notes": notes,
        },
        "estimate_extended": estimate_extended,
        "quotation_total": (
            total_price_override if total_price_override is not None
            else round((quantity_override or quantity) * unit_price, 2)
        ),
        "variance_pct": var,
        "flag": flag,
        "quote_action": quote_action,
    }


def list_quotes(
    procurement_id: str,
    vendor_name: Optional[str] = None,
    clin: Optional[str] = None,
) -> Dict[str, Any]:
    conn = _get_db()
    _ensure_tables(conn)

    query = "SELECT * FROM proc_vendor_quotes WHERE procurement_id = ?"
    params: List[Any] = [procurement_id]
    if vendor_name:
        query += " AND vendor_name = ?"
        params.append(vendor_name)
    if clin:
        query += " AND clin = ?"
        params.append(clin)
    query += " ORDER BY vendor_name, clin"

    rows = conn.execute(query, params).fetchall()
    return {
        "status": "ok",
        "procurement_id": procurement_id,
        "count": len(rows),
        "quotes": [row_to_dict(r) for r in rows],
    }


# ── Comparison & rollup ───────────────────────────────────────────────

def _igce_map(conn, procurement_id: str) -> Dict[str, Dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM proc_igce_line_items WHERE procurement_id = %s",
        (procurement_id,),
    ).fetchall()
    return {r["clin"]: row_to_dict(r) for r in rows}


def compare_procurement(procurement_id: str) -> Dict[str, Any]:
    """Side-by-side IGCE vs all quotes, line by line."""
    conn = _get_db()
    _ensure_tables(conn)

    igce = _igce_map(conn, procurement_id)
    quote_rows = conn.execute(
        "SELECT * FROM proc_vendor_quotes WHERE procurement_id = %s "
        "ORDER BY clin, vendor_name",
        (procurement_id,),
    ).fetchall()

    lines: List[Dict[str, Any]] = []
    for clin, igce_line in sorted(igce.items()):
        clin_quotes = [row_to_dict(q) for q in quote_rows if q["clin"] == clin]
        igce_unit = float(igce_line["unit_cost"])
        igce_ext = float(igce_line["extended_cost"])

        vendor_entries = []
        for q in clin_quotes:
            v_pct = _variance_pct(float(q["unit_price"]), igce_unit)
            vendor_entries.append({
                "vendor_name": q["vendor_name"],
                "quote_ref": q["quote_ref"],
                "unit_price": q["unit_price"],
                "quantity": q["quantity"],
                "total_price": q["total_price"],
                "status": q["status"],
                "variance_pct": v_pct,
                "flag": _classify_variance(v_pct),
            })

        # Lowest qualified quote (only consider submitted/under_review)
        eligible = [
            e for e in vendor_entries if e["status"] in ("submitted", "under_review")
        ]
        lowest = None
        if eligible:
            lowest_entry = min(eligible, key=lambda e: e["unit_price"] or 0)
            lowest = {
                "vendor_name": lowest_entry["vendor_name"],
                "unit_price": lowest_entry["unit_price"],
                "variance_pct": lowest_entry["variance_pct"],
                "flag": lowest_entry["flag"],
            }

        lines.append({
            "clin": clin,
            "description": igce_line["description"],
            "unit": igce_line["unit"],
            "igce_quantity": igce_line["quantity"],
            "igce_unit_cost": igce_unit,
            "igce_extended_cost": igce_ext,
            "quote_count": len(vendor_entries),
            "lowest_quote": lowest,
            "quotes": vendor_entries,
        })

    total_igce = round(sum(float(l["igce_extended_cost"]) for l in lines), 2)

    return {
        "status": "ok",
        "procurement_id": procurement_id,
        "line_count": len(lines),
        "igce_total": total_igce,
        "lines": lines,
    }


def vendor_summary(procurement_id: str) -> Dict[str, Any]:
    """Per-vendor rollup: total quoted, mean/median variance, best/worst line."""
    conn = _get_db()
    _ensure_tables(conn)

    igce = _igce_map(conn, procurement_id)
    quote_rows = conn.execute(
        "SELECT * FROM proc_vendor_quotes WHERE procurement_id = %s",
        (procurement_id,),
    ).fetchall()

    by_vendor: Dict[str, List[Dict[str, Any]]] = {}
    for q in quote_rows:
        by_vendor.setdefault(q["vendor_name"], []).append(row_to_dict(q))

    summaries: List[Dict[str, Any]] = []
    for vendor, q_list in sorted(by_vendor.items()):
        variances: List[float] = []
        total_quoted = 0.0
        line_variances: List[Dict[str, Any]] = []
        for q in q_list:
            clin = q["clin"]
            igce_line = igce.get(clin)
            igce_unit = float(igce_line["unit_cost"]) if igce_line else 0.0
            v_pct = _variance_pct(float(q["unit_price"]), igce_unit) if igce_unit > 0 else None
            if v_pct is not None:
                variances.append(v_pct)
            total_quoted += float(q["total_price"] or 0)
            line_variances.append({
                "clin": clin,
                "igce_unit_cost": igce_unit if igce_unit > 0 else None,
                "unit_price": q["unit_price"],
                "variance_pct": v_pct,
                "flag": _classify_variance(v_pct),
            })

        total_igce_matched = round(
            sum(
                float(igce[c]["unit_cost"]) * float(igce[c]["quantity"])
                for c in [q["clin"] for q in q_list]
                if c in igce
            ),
            2,
        )

        mean_var = round(statistics.mean(variances), 2) if variances else None
        median_var = round(statistics.median(variances), 2) if variances else None
        max_var = max(variances) if variances else None
        min_var = min(variances) if variances else None
        red_count = sum(1 for x in line_variances if x["flag"] == "red")
        yellow_count = sum(1 for x in line_variances if x["flag"] == "yellow")
        green_count = sum(1 for x in line_variances if x["flag"] == "green")

        summaries.append({
            "vendor_name": vendor,
            "quote_count": len(q_list),
            "total_quoted": round(total_quoted, 2),
            "total_igce_matched": total_igce_matched,
            "overall_variance_pct": (
                round((total_quoted - total_igce_matched) / total_igce_matched * 100, 2)
                if total_igce_matched > 0 else None
            ),
            "mean_variance_pct": mean_var,
            "median_variance_pct": median_var,
            "min_variance_pct": min_var,
            "max_variance_pct": max_var,
            "flags": {
                "green": green_count,
                "yellow": yellow_count,
                "red": red_count,
            },
            "lines": line_variances,
        })

    # Sort by total_quoted ascending so cheapest vendor surfaces first
    summaries.sort(key=lambda s: s["total_quoted"])

    return {
        "status": "ok",
        "procurement_id": procurement_id,
        "vendor_count": len(summaries),
        "vendors": summaries,
    }


def gate_procurement(
    procurement_id: str,
    max_variance_pct: float = MAX_FAIL_PCT,
) -> Dict[str, Any]:
    """Pass/warn/fail gate: are any quotes over IGCE by more than max_variance_pct?

    - pass : no line flagged red
    - warn : at least one line flagged yellow (within warn band, 0-5%)
    - fail : at least one line flagged red (over MAX_FAIL_PCT or below low-pct)
    """
    if max_variance_pct <= 0:
        return {
            "status": "error",
            "message": "max_variance_pct must be > 0",
        }

    summary = vendor_summary(procurement_id)
    if summary.get("status") != "ok":
        return summary
    if summary.get("vendor_count", 0) == 0:
        return {
            "status": "ok",
            "verdict": "pass",
            "message": "no quotes captured yet",
            "procurement_id": procurement_id,
        }

    red_lines: List[Dict[str, Any]] = []
    yellow_lines: List[Dict[str, Any]] = []
    for v in summary["vendors"]:
        for line in v["lines"]:
            if line["flag"] == "red":
                red_lines.append({
                    "vendor_name": v["vendor_name"],
                    "clin": line["clin"],
                    "variance_pct": line["variance_pct"],
                })
            elif line["flag"] == "yellow":
                yellow_lines.append({
                    "vendor_name": v["vendor_name"],
                    "clin": line["clin"],
                    "variance_pct": line["variance_pct"],
                })

    if red_lines:
        verdict = "fail"
    elif yellow_lines:
        verdict = "warn"
    else:
        verdict = "pass"

    return {
        "status": "ok",
        "procurement_id": procurement_id,
        "verdict": verdict,
        "max_variance_pct": max_variance_pct,
        "red_count": len(red_lines),
        "yellow_count": len(yellow_lines),
        "red_lines": red_lines,
        "yellow_lines": yellow_lines,
        "thresholds": {
            "max_warn_pct": MAX_WARN_PCT,
            "max_fail_pct": MAX_FAIL_PCT,
            "unreasonable_low_pct": UNREASONABLE_LOW_PCT,
        },
    }


# ── BOM by initiative tier + equipment category ───────────────────────


def set_equipment_category(igce_id: str, category: str) -> Dict[str, Any]:
    """Patch the equipment_category on an existing IGCE line item.

    Use this to (re)classify a line item after creation. The category
    is a free-form string; standard values are listed in
    ``EQUIPMENT_CATEGORIES`` but the BOM rollup groups by whatever
    string is stored, so any consistent taxonomy is supported.

    Returns the updated line item row, or an error if the IGCE id is
    not found.
    """
    if not igce_id:
        return {"status": "error", "message": "igce_id is required"}
    if category is None:
        category = "unspecified"
    category = (category or "unspecified").strip().lower() or "unspecified"

    conn = _get_db()
    _ensure_tables(conn)

    existing = conn.execute(
        "SELECT id, procurement_id, clin FROM proc_igce_line_items WHERE id = %s",
        (igce_id,),
    ).fetchone()
    if not existing:
        return {
            "status": "error",
            "message": f"igce_id {igce_id} not found",
        }

    now = _now()
    conn.execute(
        """
        UPDATE proc_igce_line_items
        SET equipment_category = %s, updated_at = %s
        WHERE id = %s
        """,
        (category, now, igce_id),
    )
    _audit(conn, "igce.category_updated", "set_equipment_category", {
        "igce_id": igce_id,
        "procurement_id": existing["procurement_id"],
        "clin": existing["clin"],
        "equipment_category": category,
    }, existing["procurement_id"])
    conn.commit()

    return {
        "status": "ok",
        "igce_id": igce_id,
        "procurement_id": existing["procurement_id"],
        "clin": existing["clin"],
        "equipment_category": category,
    }


def link_procurement_to_initiative(
    procurement_id: str,
    allocation_id: str,
) -> Dict[str, Any]:
    """Link a procurement to a budget-initiative allocation.

    The allocation_id must reference a row in cpmp_budget_allocations
    (the initiative budget table owned by tools/budget/initiative_allocator).
    This function does NOT validate the FK against the budget table —
    it just stores the link. bom_by_tier_and_category() will skip
    procurements whose allocation_id does not resolve to a real
    allocation, so dangling links silently disappear from the rollup
    (which is the right behavior for a forward-linkable work-in-progress).

    The link change is recorded in audit_trail.
    """
    if not procurement_id or not allocation_id:
        return {
            "status": "error",
            "message": "procurement_id and allocation_id are required",
        }

    conn = _get_db()
    _ensure_tables(conn)

    proc = conn.execute(
        "SELECT id, allocation_id FROM proc_procurements WHERE id = %s",
        (procurement_id,),
    ).fetchone()
    if not proc:
        return {
            "status": "error",
            "message": f"procurement_id {procurement_id} not found",
        }

    previous_allocation_id = proc["allocation_id"]
    now = _now()
    conn.execute(
        """
        UPDATE proc_procurements
        SET allocation_id = %s, updated_at = %s
        WHERE id = %s
        """,
        (allocation_id, now, procurement_id),
    )
    _audit(conn, "procurement.linked_to_initiative",
           "link_procurement_to_initiative", {
               "procurement_id": procurement_id,
               "previous_allocation_id": previous_allocation_id,
               "allocation_id": allocation_id,
           }, procurement_id)
    conn.commit()

    return {
        "status": "ok",
        "procurement_id": procurement_id,
        "previous_allocation_id": previous_allocation_id,
        "allocation_id": allocation_id,
    }


def bom_by_tier_and_category(
    fiscal_year: Optional[int] = None,
    allocation_ids: Optional[List[str]] = None,
    initiative_codes: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Return the BOM rolled up by initiative tier × equipment category.

    This is the headline query for the year-end budget sprint use case
    (args/use_cases.yaml). It joins:

        proc_procurements (allocation_id)
        -> cpmp_budget_allocations (tier, fiscal_year, initiative_code)
        -> proc_igce_line_items (equipment_category, extended_cost)
        -> proc_vendor_quotes (total_price, vendor_name)

    The output is keyed by "<tier>.<equipment_category>" and contains,
    per group:
        - line_item_count: number of IGCE rows
        - quote_count: number of vendor-quote rows
        - igce_total_usd: sum of extended_cost (government estimate)
        - quote_total_usd: sum of total_price (best vendor commitment)
        - variance_usd: quote_total - igce_total (negative = under IGCE)
        - variance_pct: variance_usd / igce_total * 100
        - vendors: deduped list of vendor names
        - procurements: list of procurement_ids contributing
        - initiative_codes: list of initiative_codes contributing

    Top-level summary:
        - tier_1_total_usd, tier_2_total_usd (use the igce_total)
        - by_group: dict keyed by "<tier>.<category>" as above
        - filters_applied: echo of the input filters (for the CLI/log)

    Filters are AND-combined:
        - fiscal_year: only allocations in that FY
        - allocation_ids: only those specific allocation ids (if provided)
        - initiative_codes: only those specific initiative codes (if provided)

    A procurement whose allocation_id does not resolve to a row in
    cpmp_budget_allocations is silently skipped — that is the "no
    initiative context" case and we don't want to invent a tier for it.

    This function does NOT call _audit() — it is a read-only rollup
    and emits no audit row. The link_procurement_to_initiative()
    call that fed data into it already wrote an audit row.
    """
    conn = _get_db()
    _ensure_tables(conn)

    # Build the where clause against cpmp_budget_allocations.
    alloc_where: List[str] = []
    alloc_params: List[Any] = []
    if fiscal_year is not None:
        alloc_where.append("fiscal_year = ?")
        alloc_params.append(int(fiscal_year))
    if allocation_ids:
        placeholders = ",".join("?" for _ in allocation_ids)
        alloc_where.append(f"id IN ({placeholders})")
        alloc_params.extend(allocation_ids)
    if initiative_codes:
        placeholders = ",".join("?" for _ in initiative_codes)
        alloc_where.append(f"initiative_code IN ({placeholders})")
        alloc_params.extend(initiative_codes)

    where_sql = ("WHERE " + " AND ".join(alloc_where)) if alloc_where else ""

    # Single query: join everything. Quote totals come from the BEST
    # (lowest) vendor quote per (procurement, clin); we use a correlated
    # subquery to pick the minimum total_price for each CLIN, then sum
    # those into the rollup.
    sql = f"""
        SELECT
            ba.id                AS allocation_id,
            ba.initiative_code   AS initiative_code,
            ba.tier              AS tier,
            ba.fiscal_year       AS fiscal_year,
            pp.id                AS procurement_id,
            ig.id                AS igce_id,
            ig.clin              AS clin,
            ig.equipment_category AS equipment_category,
            ig.quantity          AS igce_quantity,
            ig.unit_cost         AS igce_unit_cost,
            ig.extended_cost     AS igce_extended_cost,
            ig.description       AS description
        FROM cpmp_budget_allocations ba
        JOIN proc_procurements pp
          ON pp.allocation_id = ba.id
        JOIN proc_igce_line_items ig
          ON ig.procurement_id = pp.id
        {where_sql}
        ORDER BY ba.tier, ig.equipment_category, ig.clin
    """
    rows = conn.execute(sql, alloc_params).fetchall()

    # Pre-fetch all quotes into a dict to avoid N+1.
    all_quotes = {
        (r["procurement_id"], r["clin"]): r
        for r in conn.execute(
            "SELECT procurement_id, clin, vendor_name, total_price "
            "FROM proc_vendor_quotes WHERE total_price > 0"
        ).fetchall()
    }
    # Filter to best-per-(proc,clin).
    best_quote: Dict[tuple, Any] = {}
    for (proc_id, clin), q in all_quotes.items():
        existing = best_quote.get((proc_id, clin))
        if existing is None or q["total_price"] < existing["total_price"]:
            best_quote[(proc_id, clin)] = q

    # Roll up.
    by_group: Dict[str, Dict[str, Any]] = {}
    tier_totals: Dict[str, float] = {"tier_1": 0.0, "tier_2": 0.0, "tier_unknown": 0.0}
    contributing_procs: set = set()
    contributing_inits: set = set()

    for r in rows:
        tier = r["tier"] or "tier_unknown"
        category = r["equipment_category"] or "unspecified"
        group_key = f"{tier}.{category}"
        bucket = by_group.setdefault(group_key, {
            "tier": tier,
            "equipment_category": category,
            "line_item_count": 0,
            "quote_count": 0,
            "igce_total_usd": 0.0,
            "quote_total_usd": 0.0,
            "variance_usd": 0.0,
            "variance_pct": 0.0,
            "vendors": set(),
            "procurements": set(),
            "initiative_codes": set(),
            "clins": [],
        })
        bucket["line_item_count"] += 1
        bucket["igce_total_usd"] = round(
            bucket["igce_total_usd"] + float(r["igce_extended_cost"] or 0.0), 2
        )
        bucket["clins"].append({
            "procurement_id": r["procurement_id"],
            "clin": r["clin"],
            "description": r["description"],
            "igce_extended_cost": float(r["igce_extended_cost"] or 0.0),
        })
        bucket["procurements"].add(r["procurement_id"])
        bucket["initiative_codes"].add(r["initiative_code"])
        contributing_procs.add(r["procurement_id"])
        contributing_inits.add(r["initiative_code"])

        # Attach best quote if any.
        q = best_quote.get((r["procurement_id"], r["clin"]))
        if q is not None:
            bucket["quote_count"] += 1
            bucket["quote_total_usd"] = round(
                bucket["quote_total_usd"] + float(q["total_price"] or 0.0), 2
            )
            bucket["vendors"].add(q["vendor_name"])

        tier_totals[tier] = round(
            tier_totals[tier] + float(r["igce_extended_cost"] or 0.0), 2
        )

    # Finalize variance + serialize sets.
    for bucket in by_group.values():
        igce = bucket["igce_total_usd"]
        quote = bucket["quote_total_usd"]
        bucket["variance_usd"] = round(quote - igce, 2)
        bucket["variance_pct"] = round(
            (quote - igce) / igce * 100, 2
        ) if igce > 0 else 0.0
        bucket["vendors"] = sorted(bucket["vendors"])
        bucket["procurements"] = sorted(bucket["procurements"])
        bucket["initiative_codes"] = sorted(bucket["initiative_codes"])

    return {
        "status": "ok",
        "filters": {
            "fiscal_year": fiscal_year,
            "allocation_ids": allocation_ids or [],
            "initiative_codes": initiative_codes or [],
        },
        "tier_1_total_usd": tier_totals.get("tier_1", 0.0),
        "tier_2_total_usd": tier_totals.get("tier_2", 0.0),
        "tier_unknown_total_usd": tier_totals.get("tier_unknown", 0.0),
        "line_item_count": len(rows),
        "procurement_count": len(contributing_procs),
        "initiative_count": len(contributing_inits),
        "by_group": by_group,
    }


# ── CLI ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Procurement Quote vs IGCE Comparison Engine",
    )

    group = parser.add_mutually_exclusive_group()
    group.add_argument("--create-procurement", action="store_true",
                       help="Register a new procurement container")
    group.add_argument("--list-procurements", action="store_true",
                       help="List all procurements")
    group.add_argument("--add-igce-line", action="store_true",
                       help="Add or update an IGCE line item")
    group.add_argument("--list-igce", action="store_true",
                       help="List IGCE line items for a procurement")
    group.add_argument("--add-quote", action="store_true",
                       help="Capture a vendor quote")
    group.add_argument("--list-quotes", action="store_true",
                       help="List vendor quotes for a procurement")
    group.add_argument("--compare", action="store_true",
                       help="Side-by-side IGCE vs all quotes")
    group.add_argument("--summary", action="store_true",
                       help="Per-vendor rollup with variance stats")
    group.add_argument("--gate", action="store_true",
                       help="Pass/warn/fail gate on variance")
    group.add_argument("--add-bom-line", action="store_true",
                       help="Capture one BOM line item with all 9 required fields "
                            "(Vendor, Item, Qty, Estimate, Quotation, Expiration, "
                            "POC, Description, Notes)")

    parser.add_argument("--procurement-id", help="Procurement ID")
    parser.add_argument("--solicitation", help="Solicitation number")
    parser.add_argument("--agency", help="Buying agency")
    parser.add_argument("--title", help="Procurement title")
    parser.add_argument("--contract-type", default="ffp",
                        help="ffp, t_and_m, cpff, cpaf, idiq")
    parser.add_argument("--description", help="Procurement description")

    parser.add_argument("--clin", help="Contract Line Item Number (e.g. 0001)")
    parser.add_argument("--unit", default="each",
                        help="Unit of measure (each, hour, lot, etc.)")
    parser.add_argument("--quantity", type=float, help="IGCE quantity")
    parser.add_argument("--unit-cost", type=float, help="IGCE unit cost")
    parser.add_argument("--basis", default="",
                        help="Basis of estimate (e.g. GSA CALC+ mean, market survey)")
    parser.add_argument("--notes", default="", help="Free-text notes")
    parser.add_argument("--poc", default="",
                        help="Government Point of Contact (name, email, phone)")
    parser.add_argument("--item", default="",
                        help="BOM item name/description (Field 2 of 9)")
    parser.add_argument("--item-description", default="",
                        help="BOM item description (Field 8 of 9). "
                             "If omitted, falls back to --item.")

    parser.add_argument("--vendor", help="Vendor name")
    parser.add_argument("--quote-ref", default="", help="Vendor quote reference")
    parser.add_argument("--unit-price", type=float, help="Vendor's unit price")
    parser.add_argument("--total-price", type=float,
                        help="Vendor's total price (derived if omitted)")
    parser.add_argument("--quote-date", default="", help="ISO date the quote was issued")
    parser.add_argument("--valid-until", default="", help="ISO date the quote expires")
    parser.add_argument("--quote-status", default="submitted",
                        help="Quote status: submitted, under_review, awarded, rejected")
    parser.add_argument("--max-variance-pct", type=float, default=MAX_FAIL_PCT,
                        help="Variance threshold (%) for --gate")

    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    result: Dict[str, Any] = {}

    if args.create_procurement:
        if not args.procurement_id:
            result = {"status": "error", "message": "--procurement-id is required"}
        else:
            result = create_procurement(
                args.procurement_id,
                args.solicitation or "",
                args.agency or "",
                args.title or "",
                args.contract_type,
                args.description or "",
            )

    elif args.list_procurements:
        result = list_procurements()

    elif args.add_igce_line:
        if not (args.procurement_id and args.clin):
            result = {"status": "error",
                      "message": "--procurement-id and --clin are required"}
        elif args.quantity is None or args.unit_cost is None:
            result = {"status": "error",
                      "message": "--quantity and --unit-cost are required"}
        else:
            result = add_igce_line(
                args.procurement_id,
                args.clin,
                args.description or "",
                args.unit,
                args.quantity,
                args.unit_cost,
                args.basis,
                args.notes,
            )

    elif args.list_igce:
        if not args.procurement_id:
            result = {"status": "error", "message": "--procurement-id is required"}
        else:
            result = list_igce(args.procurement_id)

    elif args.add_quote:
        if not (args.procurement_id and args.vendor and args.clin):
            result = {"status": "error",
                      "message": "--procurement-id, --vendor, and --clin are required"}
        elif args.unit_price is None:
            result = {"status": "error", "message": "--unit-price is required"}
        else:
            result = add_quote(
                args.procurement_id,
                args.vendor,
                args.clin,
                args.unit_price,
                args.quote_ref,
                args.quantity,
                args.total_price,
                args.quote_date,
                args.valid_until,
                args.quote_status,
                args.notes,
            )

    elif args.list_quotes:
        if not args.procurement_id:
            result = {"status": "error", "message": "--procurement-id is required"}
        else:
            result = list_quotes(args.procurement_id, args.vendor, args.clin)

    elif args.compare:
        if not args.procurement_id:
            result = {"status": "error", "message": "--procurement-id is required"}
        else:
            result = compare_procurement(args.procurement_id)

    elif args.summary:
        if not args.procurement_id:
            result = {"status": "error", "message": "--procurement-id is required"}
        else:
            result = vendor_summary(args.procurement_id)

    elif args.gate:
        if not args.procurement_id:
            result = {"status": "error", "message": "--procurement-id is required"}
        else:
            result = gate_procurement(args.procurement_id, args.max_variance_pct)

    elif args.add_bom_line:
        # 9-field BOM capture
        if not (args.procurement_id and args.clin and args.vendor):
            result = {"status": "error",
                      "message": "--procurement-id, --clin, and --vendor are required"}
        elif args.quantity is None or args.unit_cost is None or args.unit_price is None:
            result = {"status": "error",
                      "message": "--quantity, --unit-cost, and --unit-price are required"}
        else:
            result = add_bom_line(
                procurement_id=args.procurement_id,
                clin=args.clin,
                item=args.item or args.item_description or "",
                unit=args.unit,
                quantity=args.quantity,
                unit_cost=args.unit_cost,
                vendor_name=args.vendor,
                quote_ref=args.quote_ref or "",
                unit_price=args.unit_price,
                quote_date=args.quote_date or "",
                valid_until=args.valid_until or "",
                poc=args.poc or "",
                description=args.item_description or "",
                notes=args.notes or "",
            )

    else:
        parser.print_help()
        return

    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()

# CUI // SP-CTI
