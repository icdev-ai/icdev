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
            "(id, timestamp, event_type, actor, action, details, project_id, session_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                _gen_id("aud"),
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


def _ensure_tables(conn) -> None:
    """Create procurement IGCE / quote tables if they don't exist."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS proc_igce_line_items (
            id              TEXT PRIMARY KEY,
            procurement_id  TEXT NOT NULL,
            clin            TEXT NOT NULL DEFAULT '',
            description     TEXT NOT NULL DEFAULT '',
            unit            TEXT NOT NULL DEFAULT 'each',
            quantity        REAL NOT NULL DEFAULT 1.0,
            unit_cost       REAL NOT NULL DEFAULT 0.0,
            extended_cost   REAL NOT NULL DEFAULT 0.0,
            basis           TEXT NOT NULL DEFAULT '',
            poc             TEXT NOT NULL DEFAULT '',
            notes           TEXT,
            metadata        TEXT DEFAULT '{}',
            created_at      TEXT NOT NULL,
            updated_at      TEXT NOT NULL,
            classification  TEXT DEFAULT 'CUI',
            UNIQUE (procurement_id, clin)
        )
        """
    )
    # Backfill: if the table existed before the poc column was added, add it now.
    # Safe no-op when the column already exists.
    try:
        conn.execute(
            "ALTER TABLE proc_igce_line_items ADD COLUMN poc TEXT NOT NULL DEFAULT ''"
        )
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
            metadata        TEXT DEFAULT '{}',
            created_at      TEXT NOT NULL,
            updated_at      TEXT NOT NULL,
            classification  TEXT DEFAULT 'CUI'
        )
        """
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
) -> Dict[str, Any]:
    """Register a new procurement (the parent container for IGCE + quotes)."""
    if not procurement_id:
        return {"status": "error", "message": "procurement_id is required"}

    conn = _get_db()
    _ensure_tables(conn)

    existing = conn.execute(
        "SELECT id FROM proc_procurements WHERE id = ?", (procurement_id,)
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
             metadata, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, 'open', '{}', ?, ?)
        """,
        (procurement_id, solicitation, title, agency, contract_type,
         description, now, now),
    )
    _audit(conn, "procurement.created", "create_procurement", {
        "procurement_id": procurement_id,
        "solicitation": solicitation,
        "agency": agency,
        "contract_type": contract_type,
    }, procurement_id)
    conn.commit()

    return {
        "status": "ok",
        "procurement_id": procurement_id,
        "solicitation": solicitation,
        "agency": agency,
        "contract_type": contract_type,
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
) -> Dict[str, Any]:
    """Add (or upsert) an IGCE line item for the procurement.

    The optional ``poc`` argument captures the Government Point of Contact
    (name, email, phone) for this line item — one of the 9 required BOM
    fields. Defaults to empty string.
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
        "SELECT id FROM proc_procurements WHERE id = ?", (procurement_id,)
    ).fetchone()
    if not proc:
        return {
            "status": "error",
            "message": f"procurement_id {procurement_id} not found (create first)",
        }

    extended_cost = round(quantity * unit_cost, 2)
    now = _now()

    existing = conn.execute(
        "SELECT id FROM proc_igce_line_items WHERE procurement_id = ? AND clin = ?",
        (procurement_id, clin),
    ).fetchone()

    if existing:
        conn.execute(
            """
            UPDATE proc_igce_line_items
            SET description = ?, unit = ?, quantity = ?, unit_cost = ?,
                extended_cost = ?, basis = ?, poc = ?, notes = ?, updated_at = ?
            WHERE id = ?
            """,
            (description, unit, quantity, unit_cost, extended_cost,
             basis, poc, notes, now, existing["id"]),
        )
        _audit(conn, "igce.updated", "add_igce_line", {
            "procurement_id": procurement_id,
            "clin": clin,
            "unit_cost": unit_cost,
            "extended_cost": extended_cost,
        }, procurement_id)
        action = "updated"
    else:
        conn.execute(
            """
            INSERT INTO proc_igce_line_items
                (id, procurement_id, clin, description, unit, quantity, unit_cost,
                 extended_cost, basis, poc, notes, metadata, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '{}', ?, ?)
            """,
            (_gen_id("igce"), procurement_id, clin, description, unit,
             quantity, unit_cost, extended_cost, basis, poc, notes, now, now),
        )
        _audit(conn, "igce.created", "add_igce_line", {
            "procurement_id": procurement_id,
            "clin": clin,
            "unit_cost": unit_cost,
            "extended_cost": extended_cost,
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
    }


def list_igce(procurement_id: str) -> Dict[str, Any]:
    conn = _get_db()
    _ensure_tables(conn)
    rows = conn.execute(
        "SELECT * FROM proc_igce_line_items WHERE procurement_id = ? ORDER BY clin",
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
        "SELECT id FROM proc_procurements WHERE id = ?", (procurement_id,)
    ).fetchone()
    if not proc:
        return {
            "status": "error",
            "message": f"procurement_id {procurement_id} not found",
        }
    igce = conn.execute(
        "SELECT id, unit_cost, quantity FROM proc_igce_line_items "
        "WHERE procurement_id = ? AND clin = ?",
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
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '{}', ?, ?)
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

    # 1) Upsert the IGCE row with the IGCE-side BOM fields (2, 3, 4, 7, 8, 9)
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
        "WHERE procurement_id = ? AND vendor_name = ? AND quote_ref = ? AND clin = ?",
        (procurement_id, vendor_name, quote_ref, clin),
    ).fetchone()
    if existing_quote:
        now = _now()
        conn.execute(
            """
            UPDATE proc_vendor_quotes
            SET unit_price = ?, quantity = ?, total_price = ?, quote_date = ?,
                valid_until = ?, status = 'submitted', notes = ?, updated_at = ?
            WHERE id = ?
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
        "SELECT * FROM proc_igce_line_items WHERE procurement_id = ?",
        (procurement_id,),
    ).fetchall()
    return {r["clin"]: row_to_dict(r) for r in rows}


def compare_procurement(procurement_id: str) -> Dict[str, Any]:
    """Side-by-side IGCE vs all quotes, line by line."""
    conn = _get_db()
    _ensure_tables(conn)

    igce = _igce_map(conn, procurement_id)
    quote_rows = conn.execute(
        "SELECT * FROM proc_vendor_quotes WHERE procurement_id = ? "
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
        "SELECT * FROM proc_vendor_quotes WHERE procurement_id = ?",
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
