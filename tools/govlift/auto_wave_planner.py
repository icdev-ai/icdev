# CUI // SP-CTI
"""GovLift — Automatic Wave Planner.

Generates prioritized migration waves from assessed workloads using
MAP scores, risk levels, business units, dependencies, and resource sizing.

Wave assignment algorithm:
  1. Filter to assessed workloads not yet assigned to a wave.
  2. Group by business_unit (keeps related workloads together).
  3. Sort groups by: avg MAP score DESC, avg risk ASC, total resource weight ASC.
  4. Allocate groups into waves, respecting max_workloads and max_resource_units.
  5. Wave sequence = priority order (wave 1 = highest priority).

All DB access via get_connection() — never sqlite3.connect().
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from uuid import uuid4

_ICDEV_ROOT = Path(__file__).resolve().parents[2]
if str(_ICDEV_ROOT) not in sys.path:
    sys.path.insert(0, str(_ICDEV_ROOT))

from tools.db.storage import get_connection, translate_sql


# ── Default planning constraints ───────────────────────────────────────────
_DEFAULT_MAX_WORKLOADS = 10
_DEFAULT_MAX_RESOURCE_UNITS = 100  # cpu_cores sum
_DEFAULT_WAVE_GAP_DAYS = 14

_RISK_PRIORITY = {"low": 0, "medium": 1, "high": 2, "critical": 3}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _wave_id() -> str:
    return "wave-" + uuid4().hex[:8]


def _row_to_dict(row) -> dict:
    if row is None:
        return {}
    try:
        return dict(row)
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_unassigned_workloads() -> list[dict]:
    """Return all assessed workloads with no wave assignment."""
    try:
        conn = get_connection()
        try:
            rows = conn.execute(
                translate_sql(
                    "SELECT * FROM govlift_workloads "
                    "WHERE migration_status = 'assessed' AND (wave_id IS NULL OR wave_id = '') "
                    "ORDER BY map_score DESC, risk_level ASC, created_at ASC"
                )
            ).fetchall()
            return [_row_to_dict(r) for r in rows]
        finally:
            conn.close()
    except Exception as exc:
        print(f"auto_wave_planner.get_unassigned_workloads error: {exc}", file=sys.stderr)
        return []


def generate_waves(
    max_workloads: int = _DEFAULT_MAX_WORKLOADS,
    max_resource_units: int = _DEFAULT_MAX_RESOURCE_UNITS,
    wave_gap_days: int = _DEFAULT_WAVE_GAP_DAYS,
    base_start_date: str | None = None,
    dry_run: bool = False,
) -> dict:
    """Automatically create prioritized migration waves from unassigned assessed workloads.

    Args:
        max_workloads:     Max number of workloads per wave.
        max_resource_units: Max sum of cpu_cores per wave.
        wave_gap_days:     Days between wave start dates.
        base_start_date:   ISO date string for wave 1 start (default = tomorrow).
        dry_run:           If True, return plan without writing to DB.

    Returns:
        {
            "waves_created": int,
            "workloads_assigned": int,
            "waves": [
                {
                    "wave_id": str,
                    "name": str,
                    "sequence_num": int,
                    "planned_start": str,
                    "planned_end": str,
                    "workloads": [workload_dict, ...],
                    "total_cpu": int,
                    "total_memory_gb": float,
                    "total_storage_tb": float,
                    "avg_map_score": float,
                    "risk_breakdown": {"low": int, ...},
                    "strategy_breakdown": {"rehost": int, ...},
                },
            ],
        }
    """
    workloads = get_unassigned_workloads()
    if not workloads:
        return {"waves_created": 0, "workloads_assigned": 0, "waves": []}

    # Group by business_unit (empty BU goes to "Unclassified")
    groups: dict[str, list[dict]] = {}
    for wl in workloads:
        bu = (wl.get("business_unit") or "").strip() or "Unclassified"
        groups.setdefault(bu, []).append(wl)

    # Sort each group internally by MAP score DESC, risk ASC
    for bu in groups:
        groups[bu].sort(key=_workload_sort_key, reverse=True)

    # Sort business units by aggregate priority
    bu_list = sorted(groups.keys(), key=lambda bu: _bu_priority_key(groups[bu]), reverse=True)

    # Allocate into waves
    waves: list[dict] = []
    current_wave_workloads: list[dict] = []
    current_cpu = 0

    for bu in bu_list:
        for wl in groups[bu]:
            cpu = wl.get("cpu_cores", 4)
            # Start a new wave if constraints would be exceeded
            if current_wave_workloads and (
                len(current_wave_workloads) >= max_workloads
                or (current_cpu + cpu) > max_resource_units
            ):
                waves.append(_finalize_wave(current_wave_workloads))
                current_wave_workloads = []
                current_cpu = 0
            current_wave_workloads.append(wl)
            current_cpu += cpu

    if current_wave_workloads:
        waves.append(_finalize_wave(current_wave_workloads))

    if not waves:
        return {"waves_created": 0, "workloads_assigned": 0, "waves": []}

    # Assign sequence numbers and dates
    base = _parse_start_date(base_start_date)
    for i, wave in enumerate(waves, start=1):
        wave["sequence_num"] = i
        wave["name"] = f"Wave {i:02d} — {wave['name_suffix']}"
        start_dt = base + timedelta(days=(i - 1) * wave_gap_days)
        end_dt = start_dt + timedelta(days=wave_gap_days - 1)
        wave["planned_start"] = start_dt.strftime("%Y-%m-%d")
        wave["planned_end"] = end_dt.strftime("%Y-%m-%d")

    if dry_run:
        return {
            "waves_created": len(waves),
            "workloads_assigned": sum(len(w["workloads"]) for w in waves),
            "waves": waves,
            "dry_run": True,
        }

    # Persist waves and assign workloads
    created_waves = []
    total_assigned = 0
    for wave in waves:
        persisted = _persist_wave(wave)
        wave_id = persisted.get("id")
        assigned = 0
        for wl in wave["workloads"]:
            try:
                _assign_workload_to_wave(wl["id"], wave_id)
                assigned += 1
            except Exception as exc:
                print(f"auto_wave_planner assign error: {exc}", file=sys.stderr)
        wave["wave_id"] = wave_id
        wave["workloads_assigned"] = assigned
        total_assigned += assigned
        created_waves.append(wave)

    return {
        "waves_created": len(created_waves),
        "workloads_assigned": total_assigned,
        "waves": created_waves,
        "dry_run": False,
    }


def get_wave_plan_preview() -> dict:
    """Return a dry-run preview of what auto-planning would produce."""
    return generate_waves(dry_run=True)


def get_planning_summary() -> dict:
    """Return high-level stats for planning dashboard."""
    try:
        conn = get_connection()
        try:
            unassigned_row = conn.execute(
                translate_sql(
                    "SELECT COUNT(*) AS cnt FROM govlift_workloads "
                    "WHERE migration_status = 'assessed' AND (wave_id IS NULL OR wave_id = '')"
                )
            ).fetchone()
            assigned_row = conn.execute(
                translate_sql(
                    "SELECT COUNT(*) AS cnt FROM govlift_workloads WHERE wave_id IS NOT NULL AND wave_id != ''"
                )
            ).fetchone()
            wave_row = conn.execute(
                translate_sql("SELECT COUNT(*) AS cnt FROM govlift_waves")
            ).fetchone()
            avg_score_row = conn.execute(
                translate_sql(
                    "SELECT AVG(map_score) AS avg FROM govlift_workloads WHERE map_score > 0"
                )
            ).fetchone()

            return {
                "unassigned_workloads": _row_to_dict(unassigned_row).get("cnt", 0) if unassigned_row else 0,
                "assigned_workloads": _row_to_dict(assigned_row).get("cnt", 0) if assigned_row else 0,
                "total_waves": _row_to_dict(wave_row).get("cnt", 0) if wave_row else 0,
                "average_map_score": round(_row_to_dict(avg_score_row).get("avg", 0) or 0, 2) if avg_score_row else 0,
            }
        finally:
            conn.close()
    except Exception as exc:
        print(f"auto_wave_planner.get_planning_summary error: {exc}", file=sys.stderr)
        return {
            "unassigned_workloads": 0,
            "assigned_workloads": 0,
            "total_waves": 0,
            "average_map_score": 0,
        }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _workload_sort_key(wl: dict) -> tuple:
    """Sort key: higher MAP score first, then lower risk priority, then smaller resources."""
    score = wl.get("map_score", 0)
    risk_pri = _RISK_PRIORITY.get(wl.get("risk_level", "medium"), 1)
    resource_weight = wl.get("cpu_cores", 4) + wl.get("memory_gb", 8) / 8
    return (score, -risk_pri, -resource_weight)


def _bu_priority_key(workloads: list[dict]) -> tuple:
    """Higher avg MAP score, lower avg risk, smaller total resource weight."""
    if not workloads:
        return (0, 0, 0)
    avg_score = sum(w.get("map_score", 0) for w in workloads) / len(workloads)
    avg_risk = sum(_RISK_PRIORITY.get(w.get("risk_level", "medium"), 1) for w in workloads) / len(workloads)
    total_cpu = sum(w.get("cpu_cores", 4) for w in workloads)
    return (avg_score, -avg_risk, -total_cpu)


def _finalize_wave(workloads: list[dict]) -> dict:
    """Build a wave dict from a list of workloads (before date/sequence assignment)."""
    total_cpu = sum(w.get("cpu_cores", 4) for w in workloads)
    total_mem = sum(w.get("memory_gb", 8.0) for w in workloads)
    total_storage = sum(w.get("storage_tb", 1.0) for w in workloads)
    avg_score = (
        sum(w.get("map_score", 0) for w in workloads) / len(workloads)
        if workloads else 0
    )
    risk_breakdown: dict[str, int] = {"low": 0, "medium": 0, "high": 0, "critical": 0}
    strategy_breakdown: dict[str, int] = {}
    bu_set: set[str] = set()
    for w in workloads:
        risk = w.get("risk_level", "medium")
        risk_breakdown[risk] = risk_breakdown.get(risk, 0) + 1
        strat = w.get("map_strategy", "rehost")
        strategy_breakdown[strat] = strategy_breakdown.get(strat, 0) + 1
        bu = (w.get("business_unit") or "").strip() or "Unclassified"
        bu_set.add(bu)

    name_suffix = ", ".join(sorted(bu_set)) if len(bu_set) <= 3 else f"{len(bu_set)} BUs"
    return {
        "wave_id": None,
        "name_suffix": name_suffix,
        "name": "",
        "sequence_num": 0,
        "planned_start": "",
        "planned_end": "",
        "workloads": workloads,
        "total_cpu": total_cpu,
        "total_memory_gb": round(total_mem, 1),
        "total_storage_tb": round(total_storage, 1),
        "avg_map_score": round(avg_score, 2),
        "risk_breakdown": risk_breakdown,
        "strategy_breakdown": strategy_breakdown,
    }


def _parse_start_date(base_start_date: str | None) -> datetime:
    if base_start_date:
        try:
            return datetime.strptime(base_start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    # Default: tomorrow
    now = datetime.now(timezone.utc)
    return (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)


def _persist_wave(wave: dict) -> dict:
    """Insert a wave record and return it."""
    wv_id = _wave_id()
    now = _now()
    try:
        conn = get_connection()
        try:
            sql = translate_sql(
                "INSERT INTO govlift_waves "
                "(id, name, sequence_num, status, planned_start, planned_end, "
                " workload_count, notes, created_at) "
                "VALUES (?,?,?,'planned',?,?,?,?,?)"
            )
            conn.execute(
                sql,
                (
                    wv_id,
                    wave["name"],
                    wave["sequence_num"],
                    wave["planned_start"],
                    wave["planned_end"],
                    len(wave["workloads"]),
                    f"Auto-generated: {wave['strategy_breakdown']}",
                    now,
                ),
            )
            conn.commit()
            row = conn.execute(
                translate_sql("SELECT * FROM govlift_waves WHERE id = ?"),
                (wv_id,),
            ).fetchone()
            return _row_to_dict(row) if row else {"id": wv_id}
        finally:
            conn.close()
    except Exception as exc:
        print(f"auto_wave_planner._persist_wave error: {exc}", file=sys.stderr)
        raise


def _assign_workload_to_wave(workload_id: str, wave_id: str) -> None:
    """Update workload to wave_assigned status and link to wave."""
    now = _now()
    try:
        conn = get_connection()
        try:
            conn.execute(
                translate_sql(
                    "UPDATE govlift_workloads SET "
                    "wave_id = ?, migration_status = 'wave_assigned', updated_at = ? "
                    "WHERE id = ?"
                ),
                (wave_id, now, workload_id),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        print(f"auto_wave_planner._assign_workload_to_wave error: {exc}", file=sys.stderr)
        raise
