#!/usr/bin/env python3
# CUI // SP-PROPIN
# Controlled by: GovProposal Portal
# CUI Category: PROPIN
# Distribution: D
# POC: GovProposal System Administrator
"""Task Order / IDIQ Vehicle Management for GovProposal.

Tracks IDIQ (Indefinite Delivery/Indefinite Quantity) contract vehicles,
BPAs (Blanket Purchase Agreements), GWACs (Government-Wide Acquisition
Contracts), and MACs (Multiple Award Contracts).  Manages individual task
orders issued under those vehicles, links them to opportunities and
proposals, and provides utilization analytics, pipeline views, and
performance metrics.

Major vehicles tracked: OASIS (GSA), Alliant 2 (GSA), SEWP V (NASA),
CIO-SP3 (NIH), ITES-3S (Army), STARS III (GSA), etc.

Functions:
    create_vehicle       -- Register a contract vehicle
    update_vehicle       -- Update vehicle information
    get_vehicle          -- Get vehicle with task order summary
    list_vehicles        -- List vehicles with filters
    create_task_order    -- Create task order under a vehicle
    update_task_order    -- Update task order (handles awarded transitions)
    get_task_order       -- Get task order with linked vehicle/proposal info
    list_task_orders     -- List task orders with filters
    vehicle_utilization  -- Ceiling vs awarded, burn rate, capacity forecast
    upcoming_deadlines   -- Task orders with approaching response deadlines
    pipeline_by_vehicle  -- Pipeline view grouped by vehicle
    vehicle_performance  -- Win rate, competitive position per vehicle
    dashboard_data       -- Aggregate summary for dashboard cards

Usage:
    python tools/capture/idiq_manager.py --create-vehicle --name "OASIS Pool 1" --agency GSA --type idiq --json
    python tools/capture/idiq_manager.py --list-vehicles --type idiq --status active --json
    python tools/capture/idiq_manager.py --create-to --vehicle-id VEH-abc --title "Cloud Migration" --json
    python tools/capture/idiq_manager.py --utilization --vehicle-id VEH-abc --json
    python tools/capture/idiq_manager.py --pipeline --json
    python tools/capture/idiq_manager.py --dashboard --json
"""

import json
import os
import secrets
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = Path(os.environ.get(
    "GOVPROPOSAL_DB_PATH", str(BASE_DIR / "data" / "govproposal.db")
))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _veh_id():
    """Generate a vehicle ID: VEH- followed by 12 hex characters."""
    return "VEH-" + secrets.token_hex(6)


def _to_id():
    """Generate a task-order ID: TO- followed by 12 hex characters."""
    return "TO-" + secrets.token_hex(6)


def _now():
    """Return current UTC timestamp as ISO-8601 string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _get_db(db_path=None):
    """Open a database connection with WAL mode and foreign keys enabled."""
    path = str(db_path or DB_PATH)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _audit(conn, event_type, action, entity_type=None, entity_id=None,
           details=None):
    """Write an append-only audit trail record."""
    conn.execute(
        "INSERT INTO audit_trail (event_type, actor, action, entity_type, "
        "entity_id, details, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            event_type,
            "idiq_manager",
            action,
            entity_type,
            entity_id,
            json.dumps(details) if details else None,
            _now(),
        ),
    )


def _row_to_dict(row):
    """Convert a sqlite3.Row to a plain dict."""
    if row is None:
        return None
    return dict(row)


def _parse_json_field(value):
    """Safely parse a JSON string field."""
    if value is None:
        return None
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return value


def _serialize_list(value):
    """Serialize a list or comma-separated string to JSON array for storage."""
    if value is None:
        return None
    if isinstance(value, str):
        value = [v.strip() for v in value.split(",") if v.strip()]
    if isinstance(value, (list, tuple)):
        return json.dumps(list(value))
    return json.dumps([str(value)])


def _parse_date(date_str):
    """Parse a YYYY-MM-DD date string to a datetime, or None."""
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str[:10], "%Y-%m-%d")
    except (ValueError, TypeError):
        return None


def _days_between(start_str, end_str):
    """Return number of days between two date strings, or None."""
    s = _parse_date(start_str)
    e = _parse_date(end_str)
    if s and e:
        return (e - s).days
    return None


# ---------------------------------------------------------------------------
# Vehicle Functions
# ---------------------------------------------------------------------------

def create_vehicle(vehicle_name, agency, vehicle_type, contract_number=None,
                   ceiling_value=None, our_position="prime", naics_codes=None,
                   set_aside_type=None, ordering_period_start=None,
                   ordering_period_end=None, holders=None, notes=None,
                   db_path=None):
    """Register a contract vehicle (IDIQ, BPA, GWAC, MAC, etc.).

    Args:
        vehicle_name: Vehicle name (e.g. "OASIS Pool 1").
        agency: Awarding agency (e.g. "GSA", "NASA", "NIH").
        vehicle_type: One of idiq, bpa, gwac, mac, single_award, other.
        contract_number: Optional contract number.
        ceiling_value: Optional total ceiling value in dollars.
        our_position: Our position on the vehicle (default: prime).
        naics_codes: Comma-separated or list of NAICS codes.
        set_aside_type: Set-aside designation if applicable.
        ordering_period_start: Start of ordering period (YYYY-MM-DD).
        ordering_period_end: End of ordering period (YYYY-MM-DD).
        holders: JSON list of vehicle holders or comma-separated names.
        notes: Free-text notes.
        db_path: Optional database path override.

    Returns:
        dict of the created vehicle record.
    """
    vid = _veh_id()
    now = _now()
    remaining = ceiling_value if ceiling_value else None

    conn = _get_db(db_path)
    try:
        conn.execute(
            "INSERT INTO idiq_vehicles "
            "(id, vehicle_name, contract_number, agency, vehicle_type, "
            " ceiling_value, awarded_value, remaining_value, "
            " ordering_period_start, ordering_period_end, naics_codes, "
            " set_aside_type, holders, our_position, status, notes, "
            " classification, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 0.0, ?, ?, ?, ?, ?, ?, ?, "
            " 'active', ?, 'CUI // SP-PROPIN', ?, ?)",
            (
                vid, vehicle_name, contract_number, agency, vehicle_type,
                ceiling_value, remaining, ordering_period_start,
                ordering_period_end, _serialize_list(naics_codes),
                set_aside_type, _serialize_list(holders), our_position,
                notes, now, now,
            ),
        )
        _audit(conn, "idiq.vehicle_created",
               f"Registered vehicle: {vehicle_name}",
               "idiq_vehicle", vid,
               {"agency": agency, "type": vehicle_type,
                "ceiling": ceiling_value})
        conn.commit()

        return {
            "id": vid,
            "vehicle_name": vehicle_name,
            "contract_number": contract_number,
            "agency": agency,
            "vehicle_type": vehicle_type,
            "ceiling_value": ceiling_value,
            "our_position": our_position,
            "status": "active",
            "created_at": now,
        }
    finally:
        conn.close()


def update_vehicle(vehicle_id, updates, db_path=None):
    """Update vehicle information.

    Args:
        vehicle_id: Vehicle ID (e.g. 'VEH-abc123def456').
        updates: dict of field name -> new value.
        db_path: Optional database path override.

    Returns:
        dict with updated fields, or error.
    """
    allowed = {
        "vehicle_name", "contract_number", "agency", "vehicle_type",
        "ceiling_value", "awarded_value", "remaining_value",
        "ordering_period_start", "ordering_period_end", "naics_codes",
        "set_aside_type", "holders", "our_position", "status", "notes",
    }
    json_fields = {"naics_codes", "holders"}

    conn = _get_db(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM idiq_vehicles WHERE id = ?", (vehicle_id,)
        ).fetchone()
        if row is None:
            return {"error": f"Vehicle not found: {vehicle_id}"}

        sets = []
        params = []
        for key, val in updates.items():
            if key not in allowed:
                continue
            if key in json_fields:
                val = _serialize_list(val)
            sets.append(f"{key} = ?")
            params.append(val)

        if not sets:
            return {"error": "No valid fields to update"}

        sets.append("updated_at = ?")
        params.append(_now())
        params.append(vehicle_id)

        conn.execute(
            f"UPDATE idiq_vehicles SET {', '.join(sets)} WHERE id = ?",
            params,
        )

        # Recalculate remaining_value if ceiling or awarded changed
        if "ceiling_value" in updates or "awarded_value" in updates:
            veh = conn.execute(
                "SELECT ceiling_value, awarded_value FROM idiq_vehicles "
                "WHERE id = ?", (vehicle_id,)
            ).fetchone()
            if veh["ceiling_value"] is not None:
                remaining = max(
                    (veh["ceiling_value"] or 0) - (veh["awarded_value"] or 0),
                    0,
                )
                conn.execute(
                    "UPDATE idiq_vehicles SET remaining_value = ? WHERE id = ?",
                    (remaining, vehicle_id),
                )

        _audit(conn, "idiq.vehicle_updated",
               f"Updated vehicle {vehicle_id}",
               "idiq_vehicle", vehicle_id,
               {"fields": list(updates.keys())})
        conn.commit()

        return {"updated": True, "vehicle_id": vehicle_id,
                "fields": list(updates.keys())}
    finally:
        conn.close()


def get_vehicle(vehicle_id, db_path=None):
    """Get vehicle with task order summary, remaining capacity, and deadlines.

    Args:
        vehicle_id: Vehicle ID.
        db_path: Optional database path override.

    Returns:
        dict with vehicle info, task_order_summary, and upcoming deadlines.
    """
    conn = _get_db(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM idiq_vehicles WHERE id = ?", (vehicle_id,)
        ).fetchone()
        if row is None:
            return {"error": f"Vehicle not found: {vehicle_id}"}

        veh = _row_to_dict(row)
        veh["naics_codes"] = _parse_json_field(veh.get("naics_codes"))
        veh["holders"] = _parse_json_field(veh.get("holders"))

        # Task order summary
        tos = conn.execute(
            "SELECT status, estimated_value, awarded_value, awarded_to, "
            "our_role, response_deadline "
            "FROM task_orders WHERE vehicle_id = ?",
            (vehicle_id,),
        ).fetchall()

        total_to = len(tos)
        total_estimated = sum(
            (t["estimated_value"] or 0) for t in tos
        )
        total_awarded = sum(
            (t["awarded_value"] or 0) for t in tos
            if t["status"] in ("awarded", "active", "completed")
        )
        won = sum(
            1 for t in tos
            if t["status"] in ("awarded", "active", "completed")
            and t["our_role"] in ("prime", "subcontractor", "teaming")
        )
        competed = sum(
            1 for t in tos
            if t["status"] in ("awarded", "not_awarded", "active",
                               "completed")
        )
        win_rate = round(won / competed, 3) if competed > 0 else None

        # Upcoming deadlines on this vehicle
        upcoming = conn.execute(
            "SELECT id, title, response_deadline, status, estimated_value "
            "FROM task_orders WHERE vehicle_id = ? "
            "AND response_deadline >= date('now') "
            "AND status NOT IN ('awarded', 'not_awarded', 'active', "
            "  'completed', 'cancelled') "
            "ORDER BY response_deadline ASC LIMIT 10",
            (vehicle_id,),
        ).fetchall()

        veh["task_order_summary"] = {
            "total": total_to,
            "total_estimated_value": round(total_estimated, 2),
            "total_awarded_value": round(total_awarded, 2),
            "won": won,
            "competed": competed,
            "win_rate": win_rate,
        }
        veh["upcoming_deadlines"] = [_row_to_dict(u) for u in upcoming]
        return veh
    finally:
        conn.close()


def list_vehicles(vehicle_type=None, status=None, our_position=None,
                  db_path=None):
    """List vehicles with optional filters.

    Args:
        vehicle_type: Filter by vehicle type (idiq, bpa, gwac, etc.).
        status: Filter by status (active, expired, etc.).
        our_position: Filter by our position on the vehicle.
        db_path: Optional database path override.

    Returns:
        list of vehicle dicts with task order counts.
    """
    conn = _get_db(db_path)
    try:
        query = (
            "SELECT v.*, "
            " (SELECT COUNT(*) FROM task_orders t "
            "  WHERE t.vehicle_id = v.id) AS to_count, "
            " (SELECT COUNT(*) FROM task_orders t "
            "  WHERE t.vehicle_id = v.id "
            "  AND t.status IN ('awarded','active','completed') "
            "  AND t.our_role IN ('prime','subcontractor','teaming')) "
            "  AS to_won "
            "FROM idiq_vehicles v WHERE 1=1 "
        )
        params = []

        if vehicle_type:
            query += "AND v.vehicle_type = ? "
            params.append(vehicle_type)
        if status:
            query += "AND v.status = ? "
            params.append(status)
        if our_position:
            query += "AND v.our_position = ? "
            params.append(our_position)

        query += "ORDER BY v.updated_at DESC"

        rows = conn.execute(query, params).fetchall()
        results = []
        for r in rows:
            d = _row_to_dict(r)
            d["naics_codes"] = _parse_json_field(d.get("naics_codes"))
            d["holders"] = _parse_json_field(d.get("holders"))
            results.append(d)
        return results
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Task Order Functions
# ---------------------------------------------------------------------------

def create_task_order(vehicle_id, title, agency=None, estimated_value=None,
                      response_deadline=None, order_type=None,
                      fair_opportunity=None, description=None,
                      our_role=None, db_path=None):
    """Create a task order under a vehicle.

    Auto-links to an existing opportunity if one matches the title and
    agency.

    Args:
        vehicle_id: Parent vehicle ID.
        title: Task order title.
        agency: Issuing agency (defaults to vehicle agency).
        estimated_value: Estimated dollar value.
        response_deadline: Response due date (YYYY-MM-DD).
        order_type: ffp, cpff, cpaf, t_m, labor_hour, hybrid, other.
        fair_opportunity: full_competition, limited_sources, sole_source,
            exception.
        description: Task order description.
        our_role: prime, subcontractor, teaming, no_bid.
        db_path: Optional database path override.

    Returns:
        dict of the created task order.
    """
    conn = _get_db(db_path)
    try:
        # Validate vehicle exists
        veh = conn.execute(
            "SELECT id, agency, status FROM idiq_vehicles WHERE id = ?",
            (vehicle_id,),
        ).fetchone()
        if veh is None:
            return {"error": f"Vehicle not found: {vehicle_id}"}

        to_agency = agency or veh["agency"]
        toid = _to_id()
        now = _now()

        # Auto-link to opportunity by title similarity
        opp_id = None
        opp_row = conn.execute(
            "SELECT id FROM opportunities WHERE title = ? AND agency = ? "
            "LIMIT 1",
            (title, to_agency),
        ).fetchone()
        if opp_row:
            opp_id = opp_row["id"]

        conn.execute(
            "INSERT INTO task_orders "
            "(id, vehicle_id, opportunity_id, title, agency, description, "
            " order_type, estimated_value, fair_opportunity, "
            " response_deadline, our_role, status, classification, "
            " created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'forecasted', "
            " 'CUI // SP-PROPIN', ?, ?)",
            (
                toid, vehicle_id, opp_id, title, to_agency, description,
                order_type, estimated_value, fair_opportunity,
                response_deadline, our_role, now, now,
            ),
        )

        _audit(conn, "idiq.task_order_created",
               f"Created task order: {title}",
               "task_order", toid,
               {"vehicle_id": vehicle_id, "estimated_value": estimated_value})
        conn.commit()

        return {
            "id": toid,
            "vehicle_id": vehicle_id,
            "opportunity_id": opp_id,
            "title": title,
            "agency": to_agency,
            "estimated_value": estimated_value,
            "status": "forecasted",
            "created_at": now,
        }
    finally:
        conn.close()


def update_task_order(task_order_id, updates, db_path=None):
    """Update a task order.

    When status transitions to 'awarded' and awarded_value is provided,
    the parent vehicle's awarded_value and remaining_value are
    automatically recalculated.

    Args:
        task_order_id: Task order ID.
        updates: dict of field name -> new value.
        db_path: Optional database path override.

    Returns:
        dict with update confirmation, or error.
    """
    allowed = {
        "task_order_number", "title", "agency", "issuing_office",
        "description", "order_type", "estimated_value", "awarded_value",
        "period_of_performance_start", "period_of_performance_end",
        "status", "fair_opportunity", "response_deadline", "awarded_to",
        "our_role", "win_themes", "notes", "opportunity_id", "proposal_id",
    }

    conn = _get_db(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM task_orders WHERE id = ?", (task_order_id,)
        ).fetchone()
        if row is None:
            return {"error": f"Task order not found: {task_order_id}"}

        old_status = row["status"]
        vehicle_id = row["vehicle_id"]

        sets = []
        params = []
        for key, val in updates.items():
            if key not in allowed:
                continue
            sets.append(f"{key} = ?")
            params.append(val)

        if not sets:
            return {"error": "No valid fields to update"}

        sets.append("updated_at = ?")
        params.append(_now())
        params.append(task_order_id)

        conn.execute(
            f"UPDATE task_orders SET {', '.join(sets)} WHERE id = ?",
            params,
        )

        # When task order is awarded, recalculate vehicle totals
        new_status = updates.get("status", old_status)
        if new_status in ("awarded", "active") or "awarded_value" in updates:
            _recalculate_vehicle_totals(conn, vehicle_id)

        _audit(conn, "idiq.task_order_updated",
               f"Updated task order {task_order_id}",
               "task_order", task_order_id,
               {"fields": list(updates.keys()),
                "old_status": old_status,
                "new_status": new_status})
        conn.commit()

        return {"updated": True, "task_order_id": task_order_id,
                "vehicle_id": vehicle_id,
                "fields": list(updates.keys())}
    finally:
        conn.close()


def _recalculate_vehicle_totals(conn, vehicle_id):
    """Recalculate a vehicle's awarded_value and remaining_value from TOs."""
    result = conn.execute(
        "SELECT COALESCE(SUM(awarded_value), 0) AS total "
        "FROM task_orders WHERE vehicle_id = ? "
        "AND status IN ('awarded', 'active', 'completed')",
        (vehicle_id,),
    ).fetchone()
    total_awarded = result["total"]

    veh = conn.execute(
        "SELECT ceiling_value FROM idiq_vehicles WHERE id = ?",
        (vehicle_id,),
    ).fetchone()

    remaining = None
    if veh and veh["ceiling_value"] is not None:
        remaining = max(veh["ceiling_value"] - total_awarded, 0)

    conn.execute(
        "UPDATE idiq_vehicles SET awarded_value = ?, remaining_value = ?, "
        "updated_at = ? WHERE id = ?",
        (total_awarded, remaining, _now(), vehicle_id),
    )


def get_task_order(task_order_id, db_path=None):
    """Get a task order with linked vehicle info and proposal status.

    Args:
        task_order_id: Task order ID.
        db_path: Optional database path override.

    Returns:
        dict with task order, vehicle summary, and proposal info.
    """
    conn = _get_db(db_path)
    try:
        row = conn.execute(
            "SELECT t.*, v.vehicle_name, v.contract_number AS vehicle_contract, "
            "v.agency AS vehicle_agency, v.vehicle_type "
            "FROM task_orders t "
            "LEFT JOIN idiq_vehicles v ON t.vehicle_id = v.id "
            "WHERE t.id = ?",
            (task_order_id,),
        ).fetchone()
        if row is None:
            return {"error": f"Task order not found: {task_order_id}"}

        d = _row_to_dict(row)

        # Linked proposal info
        if d.get("proposal_id"):
            prop = conn.execute(
                "SELECT id, title, status, due_date FROM proposals "
                "WHERE id = ?", (d["proposal_id"],)
            ).fetchone()
            d["proposal"] = _row_to_dict(prop)
        else:
            d["proposal"] = None

        # Linked opportunity info
        if d.get("opportunity_id"):
            opp = conn.execute(
                "SELECT id, title, status, response_deadline, fit_score "
                "FROM opportunities WHERE id = ?", (d["opportunity_id"],)
            ).fetchone()
            d["opportunity"] = _row_to_dict(opp)
        else:
            d["opportunity"] = None

        return d
    finally:
        conn.close()


def list_task_orders(vehicle_id=None, status=None, agency=None,
                     db_path=None):
    """List task orders with optional filters.

    Args:
        vehicle_id: Filter by parent vehicle.
        status: Filter by task order status.
        agency: Filter by issuing agency.
        db_path: Optional database path override.

    Returns:
        list of task order dicts with vehicle name.
    """
    conn = _get_db(db_path)
    try:
        query = (
            "SELECT t.*, v.vehicle_name, v.vehicle_type "
            "FROM task_orders t "
            "LEFT JOIN idiq_vehicles v ON t.vehicle_id = v.id "
            "WHERE 1=1 "
        )
        params = []

        if vehicle_id:
            query += "AND t.vehicle_id = ? "
            params.append(vehicle_id)
        if status:
            query += "AND t.status = ? "
            params.append(status)
        if agency:
            query += "AND t.agency = ? "
            params.append(agency)

        query += "ORDER BY t.response_deadline ASC NULLS LAST, t.updated_at DESC"

        rows = conn.execute(query, params).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Analytics Functions
# ---------------------------------------------------------------------------

def vehicle_utilization(vehicle_id=None, db_path=None):
    """Analyze vehicle utilization: ceiling vs awarded, burn rate, forecast.

    If vehicle_id is provided, analyzes a single vehicle.  Otherwise,
    analyzes all active vehicles.

    Returns:
        dict (single vehicle) or list of dicts with:
        - utilization_pct: awarded / ceiling
        - win_rate: won TOs / competed TOs
        - avg_order: average awarded TO value
        - burn_rate: awarded dollars per month
        - remaining_months: months until ceiling exhausted at current rate
        - forecast: textual assessment
    """
    conn = _get_db(db_path)
    try:
        if vehicle_id:
            vehicles = conn.execute(
                "SELECT * FROM idiq_vehicles WHERE id = ?", (vehicle_id,)
            ).fetchall()
        else:
            vehicles = conn.execute(
                "SELECT * FROM idiq_vehicles WHERE status = 'active'"
            ).fetchall()

        if not vehicles:
            return {"error": "No vehicles found"} if vehicle_id else []

        results = []
        now = datetime.now(timezone.utc)

        for v in vehicles:
            vd = _row_to_dict(v)
            vid = vd["id"]
            ceiling = vd.get("ceiling_value") or 0
            awarded = vd.get("awarded_value") or 0

            # Task order stats
            tos = conn.execute(
                "SELECT status, awarded_value, our_role, created_at "
                "FROM task_orders WHERE vehicle_id = ?",
                (vid,),
            ).fetchall()

            won = sum(
                1 for t in tos
                if t["status"] in ("awarded", "active", "completed")
                and t["our_role"] in ("prime", "subcontractor", "teaming")
            )
            competed = sum(
                1 for t in tos
                if t["status"] in ("awarded", "not_awarded", "active",
                                   "completed")
            )
            win_rate = round(won / competed, 3) if competed > 0 else None

            awarded_values = [
                t["awarded_value"] for t in tos
                if t["awarded_value"] and t["awarded_value"] > 0
                and t["status"] in ("awarded", "active", "completed")
            ]
            avg_order = (
                round(sum(awarded_values) / len(awarded_values), 2)
                if awarded_values else 0
            )

            # Utilization percentage
            utilization_pct = (
                round(awarded / ceiling, 4) if ceiling > 0 else None
            )

            # Burn rate: total awarded / months since vehicle start
            burn_rate = 0.0
            remaining_months = None
            start = _parse_date(vd.get("ordering_period_start"))
            end = _parse_date(vd.get("ordering_period_end"))

            if start and awarded > 0:
                months_active = max(
                    (now - start.replace(tzinfo=timezone.utc)).days / 30.44,
                    1,
                )
                burn_rate = round(awarded / months_active, 2)

                if ceiling > 0 and burn_rate > 0:
                    remaining_dollars = max(ceiling - awarded, 0)
                    remaining_months = round(
                        remaining_dollars / burn_rate, 1
                    )

            # Ordering period remaining
            ordering_months_left = None
            if end:
                days_left = (end.replace(tzinfo=timezone.utc) - now).days
                ordering_months_left = round(max(days_left, 0) / 30.44, 1)

            # Forecast assessment
            if ceiling == 0:
                forecast = "No ceiling defined -- track task orders only"
            elif utilization_pct is not None and utilization_pct >= 0.90:
                forecast = "Near ceiling -- limited remaining capacity"
            elif utilization_pct is not None and utilization_pct >= 0.70:
                forecast = "Healthy utilization -- continue pursuit"
            elif (remaining_months is not None
                  and ordering_months_left is not None
                  and remaining_months > ordering_months_left * 1.5):
                forecast = (
                    "Under-utilized -- increase pursuit to consume "
                    "ceiling before ordering period ends"
                )
            elif utilization_pct is not None and utilization_pct < 0.20:
                forecast = (
                    "Low utilization -- vehicle at risk of under-performance"
                )
            else:
                forecast = "On track"

            results.append({
                "vehicle_id": vid,
                "vehicle_name": vd["vehicle_name"],
                "agency": vd["agency"],
                "vehicle_type": vd["vehicle_type"],
                "ceiling_value": ceiling,
                "awarded_value": awarded,
                "utilization_pct": utilization_pct,
                "win_rate": win_rate,
                "task_orders_total": len(tos),
                "task_orders_won": won,
                "task_orders_competed": competed,
                "avg_order_value": avg_order,
                "burn_rate_per_month": burn_rate,
                "remaining_months_at_burn_rate": remaining_months,
                "ordering_months_remaining": ordering_months_left,
                "forecast": forecast,
            })

        if vehicle_id:
            return results[0] if results else {"error": "Vehicle not found"}
        return results
    finally:
        conn.close()


def upcoming_deadlines(days_ahead=30, db_path=None):
    """Task orders with response deadlines in the next N days.

    Args:
        days_ahead: Number of days to look ahead (default 30).
        db_path: Optional database path override.

    Returns:
        dict with count and list of upcoming task orders.
    """
    conn = _get_db(db_path)
    try:
        rows = conn.execute(
            "SELECT t.id, t.title, t.agency, t.response_deadline, "
            "t.estimated_value, t.status, t.our_role, t.fair_opportunity, "
            "v.vehicle_name, v.vehicle_type, v.id AS vehicle_id "
            "FROM task_orders t "
            "LEFT JOIN idiq_vehicles v ON t.vehicle_id = v.id "
            "WHERE t.response_deadline >= date('now') "
            "AND t.response_deadline <= date('now', '+' || ? || ' days') "
            "AND t.status NOT IN ('awarded', 'not_awarded', 'active', "
            "  'completed', 'cancelled') "
            "ORDER BY t.response_deadline ASC",
            (days_ahead,),
        ).fetchall()

        return {
            "days_ahead": days_ahead,
            "count": len(rows),
            "task_orders": [_row_to_dict(r) for r in rows],
        }
    finally:
        conn.close()


def pipeline_by_vehicle(db_path=None):
    """Pipeline view grouped by vehicle: active TOs, forecasted, total value.

    Returns:
        list of dicts, one per active vehicle, with pipeline breakdown.
    """
    conn = _get_db(db_path)
    try:
        vehicles = conn.execute(
            "SELECT id, vehicle_name, agency, vehicle_type, ceiling_value, "
            "awarded_value, our_position, status "
            "FROM idiq_vehicles WHERE status = 'active' "
            "ORDER BY vehicle_name"
        ).fetchall()

        results = []
        for v in vehicles:
            vid = v["id"]
            tos = conn.execute(
                "SELECT status, estimated_value, awarded_value, our_role "
                "FROM task_orders WHERE vehicle_id = ?",
                (vid,),
            ).fetchall()

            status_groups = defaultdict(list)
            for t in tos:
                status_groups[t["status"]].append(t)

            forecasted_count = len(status_groups.get("forecasted", []))
            forecasted_value = sum(
                (t["estimated_value"] or 0)
                for t in status_groups.get("forecasted", [])
            )
            active_count = sum(
                len(status_groups.get(s, []))
                for s in ("rfq_released", "proposal_submitted", "evaluating")
            )
            active_value = sum(
                (t["estimated_value"] or 0)
                for s in ("rfq_released", "proposal_submitted", "evaluating")
                for t in status_groups.get(s, [])
            )
            won_count = sum(
                len(status_groups.get(s, []))
                for s in ("awarded", "active", "completed")
            )
            won_value = sum(
                (t["awarded_value"] or 0)
                for s in ("awarded", "active", "completed")
                for t in status_groups.get(s, [])
            )

            total_pipeline = forecasted_value + active_value

            results.append({
                "vehicle_id": vid,
                "vehicle_name": v["vehicle_name"],
                "agency": v["agency"],
                "vehicle_type": v["vehicle_type"],
                "our_position": v["our_position"],
                "forecasted": {
                    "count": forecasted_count,
                    "value": round(forecasted_value, 2),
                },
                "in_progress": {
                    "count": active_count,
                    "value": round(active_value, 2),
                },
                "won": {
                    "count": won_count,
                    "value": round(won_value, 2),
                },
                "total_pipeline_value": round(total_pipeline, 2),
                "ceiling_value": v["ceiling_value"],
                "awarded_value": v["awarded_value"],
            })

        return results
    finally:
        conn.close()


def vehicle_performance(vehicle_id=None, db_path=None):
    """Performance metrics: win rate, competitive position, per vehicle.

    Args:
        vehicle_id: Optional vehicle ID for single vehicle analysis.
        db_path: Optional database path override.

    Returns:
        dict (single) or list of performance dicts.
    """
    conn = _get_db(db_path)
    try:
        if vehicle_id:
            vehicles = conn.execute(
                "SELECT * FROM idiq_vehicles WHERE id = ?", (vehicle_id,)
            ).fetchall()
        else:
            vehicles = conn.execute(
                "SELECT * FROM idiq_vehicles WHERE status = 'active'"
            ).fetchall()

        results = []
        for v in vehicles:
            vid = v["id"]
            tos = conn.execute(
                "SELECT status, awarded_value, estimated_value, "
                "awarded_to, our_role "
                "FROM task_orders WHERE vehicle_id = ?",
                (vid,),
            ).fetchall()

            total = len(tos)
            won = sum(
                1 for t in tos
                if t["status"] in ("awarded", "active", "completed")
                and t["our_role"] in ("prime", "subcontractor", "teaming")
            )
            lost = sum(
                1 for t in tos if t["status"] == "not_awarded"
            )
            no_bid = sum(
                1 for t in tos
                if t["our_role"] == "no_bid" or t["status"] == "cancelled"
            )
            competed = won + lost

            win_rate = round(won / competed, 3) if competed > 0 else None
            bid_rate = (
                round(competed / max(total - no_bid, 1), 3)
                if total > 0 else None
            )

            won_values = [
                t["awarded_value"] for t in tos
                if t["awarded_value"] and t["awarded_value"] > 0
                and t["status"] in ("awarded", "active", "completed")
                and t["our_role"] in ("prime", "subcontractor", "teaming")
            ]
            total_won_value = sum(won_values)
            avg_won = (
                round(total_won_value / len(won_values), 2)
                if won_values else 0
            )

            # Competitor analysis from task orders won by others
            competitors = defaultdict(int)
            for t in tos:
                if (t["status"] in ("awarded", "active", "completed")
                        and t["awarded_to"]
                        and t["our_role"] not in ("prime", "subcontractor",
                                                   "teaming")):
                    competitors[t["awarded_to"]] += 1

            top_competitors = sorted(
                competitors.items(), key=lambda x: x[1], reverse=True
            )[:5]

            # Assessment
            if win_rate is not None and win_rate >= 0.50:
                assessment = "Strong -- above 50% win rate"
            elif win_rate is not None and win_rate >= 0.30:
                assessment = "Competitive -- 30-50% win rate"
            elif win_rate is not None:
                assessment = "Below average -- review pricing and themes"
            else:
                assessment = "Insufficient data"

            results.append({
                "vehicle_id": vid,
                "vehicle_name": v["vehicle_name"],
                "agency": v["agency"],
                "total_task_orders": total,
                "competed": competed,
                "won": won,
                "lost": lost,
                "no_bid": no_bid,
                "win_rate": win_rate,
                "bid_rate": bid_rate,
                "total_won_value": round(total_won_value, 2),
                "avg_won_value": avg_won,
                "top_competitors": [
                    {"name": name, "wins": count}
                    for name, count in top_competitors
                ],
                "assessment": assessment,
            })

        if vehicle_id:
            return results[0] if results else {"error": "Vehicle not found"}
        return results
    finally:
        conn.close()


def dashboard_data(db_path=None):
    """Dashboard summary: active vehicles, open TOs, deadlines, pipeline, win rate.

    Returns:
        dict with aggregate metrics for dashboard cards.
    """
    conn = _get_db(db_path)
    try:
        # Active vehicles
        active_vehicles = conn.execute(
            "SELECT COUNT(*) AS cnt FROM idiq_vehicles WHERE status = 'active'"
        ).fetchone()["cnt"]

        # Total vehicles
        total_vehicles = conn.execute(
            "SELECT COUNT(*) AS cnt FROM idiq_vehicles"
        ).fetchone()["cnt"]

        # Open task orders (not terminal status)
        open_tos = conn.execute(
            "SELECT COUNT(*) AS cnt FROM task_orders "
            "WHERE status NOT IN ('awarded', 'not_awarded', 'active', "
            "  'completed', 'cancelled')"
        ).fetchone()["cnt"]

        # Upcoming deadlines (next 30 days)
        upcoming = conn.execute(
            "SELECT COUNT(*) AS cnt FROM task_orders "
            "WHERE response_deadline >= date('now') "
            "AND response_deadline <= date('now', '+30 days') "
            "AND status NOT IN ('awarded', 'not_awarded', 'active', "
            "  'completed', 'cancelled')"
        ).fetchone()["cnt"]

        # Total pipeline value (forecasted + in-progress)
        pipeline_row = conn.execute(
            "SELECT COALESCE(SUM(estimated_value), 0) AS val "
            "FROM task_orders "
            "WHERE status IN ('forecasted', 'rfq_released', "
            "  'proposal_submitted', 'evaluating')"
        ).fetchone()
        total_pipeline = round(pipeline_row["val"], 2)

        # Overall win rate
        won = conn.execute(
            "SELECT COUNT(*) AS cnt FROM task_orders "
            "WHERE status IN ('awarded', 'active', 'completed') "
            "AND our_role IN ('prime', 'subcontractor', 'teaming')"
        ).fetchone()["cnt"]
        competed = conn.execute(
            "SELECT COUNT(*) AS cnt FROM task_orders "
            "WHERE status IN ('awarded', 'not_awarded', 'active', "
            "  'completed')"
        ).fetchone()["cnt"]
        overall_win_rate = round(won / competed, 3) if competed > 0 else None

        # Total awarded across all vehicles
        total_awarded = conn.execute(
            "SELECT COALESCE(SUM(awarded_value), 0) AS val "
            "FROM idiq_vehicles"
        ).fetchone()["val"]

        # Total ceiling across active vehicles
        total_ceiling = conn.execute(
            "SELECT COALESCE(SUM(ceiling_value), 0) AS val "
            "FROM idiq_vehicles WHERE status = 'active'"
        ).fetchone()["val"]

        return {
            "active_vehicles": active_vehicles,
            "total_vehicles": total_vehicles,
            "open_task_orders": open_tos,
            "upcoming_deadlines_30d": upcoming,
            "total_pipeline_value": total_pipeline,
            "overall_win_rate": overall_win_rate,
            "total_awarded_value": round(total_awarded, 2),
            "total_ceiling_value": round(total_ceiling, 2),
            "overall_utilization_pct": (
                round(total_awarded / total_ceiling, 4)
                if total_ceiling > 0 else None
            ),
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser():
    """Build argument parser for the CLI."""
    import argparse
    parser = argparse.ArgumentParser(
        description="GovProposal IDIQ / Task Order Manager",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  %(prog)s --create-vehicle --name 'OASIS Pool 1' "
            "--agency GSA --type idiq --ceiling 15000000000 --json\n"
            "  %(prog)s --list-vehicles --type idiq --status active --json\n"
            "  %(prog)s --create-to --vehicle-id VEH-abc "
            "--title 'Cloud Migration' --value 5000000 --json\n"
            "  %(prog)s --utilization --vehicle-id VEH-abc --json\n"
            "  %(prog)s --pipeline --json\n"
            "  %(prog)s --dashboard --json\n"
        ),
    )

    action = parser.add_mutually_exclusive_group(required=True)
    # Vehicle actions
    action.add_argument("--create-vehicle", action="store_true",
                        help="Register a new contract vehicle")
    action.add_argument("--update-vehicle", action="store_true",
                        help="Update a vehicle's information")
    action.add_argument("--get-vehicle", action="store_true",
                        help="Get vehicle detail with TO summary")
    action.add_argument("--list-vehicles", action="store_true",
                        help="List vehicles with optional filters")
    # Task order actions
    action.add_argument("--create-to", action="store_true",
                        help="Create a task order under a vehicle")
    action.add_argument("--update-to", action="store_true",
                        help="Update a task order")
    action.add_argument("--get-to", action="store_true",
                        help="Get task order detail")
    action.add_argument("--list-to", action="store_true",
                        help="List task orders with filters")
    # Analytics
    action.add_argument("--utilization", action="store_true",
                        help="Vehicle utilization analysis")
    action.add_argument("--deadlines", action="store_true",
                        help="Upcoming task order deadlines")
    action.add_argument("--pipeline", action="store_true",
                        help="Pipeline view grouped by vehicle")
    action.add_argument("--performance", action="store_true",
                        help="Vehicle performance metrics")
    action.add_argument("--dashboard", action="store_true",
                        help="Dashboard summary data")

    # Common arguments
    parser.add_argument("--vehicle-id", help="Vehicle ID (VEH-...)")
    parser.add_argument("--to-id", help="Task order ID (TO-...)")
    parser.add_argument("--name", help="Vehicle name (--create-vehicle)")
    parser.add_argument("--agency", help="Agency name")
    parser.add_argument("--type", dest="vtype",
                        help="Vehicle type (idiq, bpa, gwac, mac, "
                             "single_award, other)")
    parser.add_argument("--contract", help="Contract number")
    parser.add_argument("--ceiling", type=float,
                        help="Ceiling value in dollars")
    parser.add_argument("--position",
                        help="Our position (prime, subcontractor, teaming, "
                             "not_on_vehicle, pending)")
    parser.add_argument("--naics", help="Comma-separated NAICS codes")
    parser.add_argument("--set-aside", help="Set-aside type")
    parser.add_argument("--period-start", help="Ordering period start")
    parser.add_argument("--period-end", help="Ordering period end")

    # Task order arguments
    parser.add_argument("--title", help="Task order title")
    parser.add_argument("--value", type=float,
                        help="Estimated value (--create-to)")
    parser.add_argument("--awarded-value", type=float,
                        help="Awarded value (--update-to)")
    parser.add_argument("--deadline",
                        help="Response deadline (YYYY-MM-DD)")
    parser.add_argument("--order-type",
                        help="Order type (ffp, cpff, cpaf, t_m, "
                             "labor_hour, hybrid, other)")
    parser.add_argument("--fair-opportunity",
                        help="Fair opportunity (full_competition, "
                             "limited_sources, sole_source, exception)")
    parser.add_argument("--status", help="Status value (for updates)")
    parser.add_argument("--awarded-to", help="Awarded to company name")
    parser.add_argument("--our-role",
                        help="Our role (prime, subcontractor, teaming, "
                             "no_bid)")
    parser.add_argument("--notes", help="Free-text notes")

    # Analytics arguments
    parser.add_argument("--days", type=int, default=30,
                        help="Days ahead for --deadlines (default: 30)")

    # Output
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--db-path", help="Override database path")

    return parser


def main():
    """CLI entry point."""
    parser = _build_parser()
    args = parser.parse_args()
    db = args.db_path

    try:
        # -- Vehicle actions --
        if args.create_vehicle:
            if not args.name:
                parser.error("--create-vehicle requires --name")
            if not args.agency:
                parser.error("--create-vehicle requires --agency")
            if not args.vtype:
                parser.error("--create-vehicle requires --type")
            result = create_vehicle(
                vehicle_name=args.name,
                agency=args.agency,
                vehicle_type=args.vtype,
                contract_number=args.contract,
                ceiling_value=args.ceiling,
                our_position=args.position or "prime",
                naics_codes=args.naics,
                set_aside_type=args.set_aside,
                ordering_period_start=args.period_start,
                ordering_period_end=args.period_end,
                notes=args.notes,
                db_path=db,
            )

        elif args.update_vehicle:
            if not args.vehicle_id:
                parser.error("--update-vehicle requires --vehicle-id")
            updates = {}
            if args.status:
                updates["status"] = args.status
            if args.name:
                updates["vehicle_name"] = args.name
            if args.ceiling is not None:
                updates["ceiling_value"] = args.ceiling
            if args.position:
                updates["our_position"] = args.position
            if args.notes:
                updates["notes"] = args.notes
            if args.contract:
                updates["contract_number"] = args.contract
            if args.agency:
                updates["agency"] = args.agency
            if args.period_start:
                updates["ordering_period_start"] = args.period_start
            if args.period_end:
                updates["ordering_period_end"] = args.period_end
            if not updates:
                parser.error("--update-vehicle requires at least one "
                             "field to update")
            result = update_vehicle(args.vehicle_id, updates, db_path=db)

        elif args.get_vehicle:
            if not args.vehicle_id:
                parser.error("--get-vehicle requires --vehicle-id")
            result = get_vehicle(args.vehicle_id, db_path=db)

        elif args.list_vehicles:
            result = list_vehicles(
                vehicle_type=args.vtype,
                status=args.status,
                our_position=args.position,
                db_path=db,
            )

        # -- Task order actions --
        elif args.create_to:
            if not args.vehicle_id:
                parser.error("--create-to requires --vehicle-id")
            if not args.title:
                parser.error("--create-to requires --title")
            result = create_task_order(
                vehicle_id=args.vehicle_id,
                title=args.title,
                agency=args.agency,
                estimated_value=args.value,
                response_deadline=args.deadline,
                order_type=args.order_type,
                fair_opportunity=args.fair_opportunity,
                our_role=args.our_role,
                db_path=db,
            )

        elif args.update_to:
            if not args.to_id:
                parser.error("--update-to requires --to-id")
            updates = {}
            if args.status:
                updates["status"] = args.status
            if args.awarded_value is not None:
                updates["awarded_value"] = args.awarded_value
            if args.awarded_to:
                updates["awarded_to"] = args.awarded_to
            if args.our_role:
                updates["our_role"] = args.our_role
            if args.title:
                updates["title"] = args.title
            if args.value is not None:
                updates["estimated_value"] = args.value
            if args.deadline:
                updates["response_deadline"] = args.deadline
            if args.order_type:
                updates["order_type"] = args.order_type
            if args.notes:
                updates["notes"] = args.notes
            if not updates:
                parser.error("--update-to requires at least one "
                             "field to update")
            result = update_task_order(args.to_id, updates, db_path=db)

        elif args.get_to:
            if not args.to_id:
                parser.error("--get-to requires --to-id")
            result = get_task_order(args.to_id, db_path=db)

        elif args.list_to:
            result = list_task_orders(
                vehicle_id=args.vehicle_id,
                status=args.status,
                agency=args.agency,
                db_path=db,
            )

        # -- Analytics --
        elif args.utilization:
            result = vehicle_utilization(
                vehicle_id=args.vehicle_id, db_path=db,
            )

        elif args.deadlines:
            result = upcoming_deadlines(days_ahead=args.days, db_path=db)

        elif args.pipeline:
            result = pipeline_by_vehicle(db_path=db)

        elif args.performance:
            result = vehicle_performance(
                vehicle_id=args.vehicle_id, db_path=db,
            )

        elif args.dashboard:
            result = dashboard_data(db_path=db)

        # -- Output --
        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            if isinstance(result, list):
                print(f"Results: {len(result)} item(s)")
                for item in result:
                    name = item.get("vehicle_name", item.get("title", "?"))
                    vid = item.get("vehicle_id", item.get("id", "?"))
                    print(f"  [{vid}] {name}")
            elif isinstance(result, dict):
                if "error" in result:
                    print(f"ERROR: {result['error']}", file=sys.stderr)
                    sys.exit(1)
                for key, value in result.items():
                    if isinstance(value, (list, dict)):
                        print(f"  {key}: {json.dumps(value, default=str)}")
                    else:
                        print(f"  {key}: {value}")

    except ValueError as exc:
        if args.json:
            print(json.dumps({"error": str(exc)}, indent=2))
        else:
            print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    except sqlite3.Error as exc:
        if args.json:
            print(json.dumps({"error": f"Database error: {exc}"}, indent=2))
        else:
            print(f"Database error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
