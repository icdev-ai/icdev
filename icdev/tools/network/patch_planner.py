# CUI // SP-CTI
"""PVM — AI Patch Planner (pvm-pat-01).

Reads approved items from nc_triage_queue, clusters affected devices from
nc_attack_surface by site/subnet, finds matching maintenance windows, runs
remediation simulation for blast radius, and writes APPEND-ONLY nc_patch_plans
rows.

Public API
----------
create_patch_plan(approved_by=None) -> dict
get_patch_plans(plan_id=None, advisory_id=None) -> list[dict]
get_plan_summary(plan_id) -> dict

CLI
---
python tools/network/patch_planner.py --create-plan [--approved-by EMAIL] --json
python tools/network/patch_planner.py --plans [--plan-id ID] --json
python tools/network/patch_planner.py --plan-summary PLAN_ID --json
"""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from pathlib import Path

# kax-conflict-05: run by path, sys.path[0] is this file's own directory — never
# the import root. Bootstrap it before the first first-party import below.
# parents[N] is whatever holds this file's `tools` package: the repo root in
# tools/, and <repo>/icdev in the icdev/ mirror (which is what a wheel ships).
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.logging.icdev_logger import get_logger
from tools.network.db.init_db import get_connection

logger = get_logger(__name__)

# Priority score threshold for HITL requirement (from args/network_canvas_config.yaml default)
_HITL_THRESHOLD = 0.75
# Days to schedule ahead when no maintenance window is found
_DEFAULT_LAG_DAYS = 7


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _site_from_device(device_name: str) -> str:
    """Extract site prefix: first segment split by '-' or '.'."""
    for sep in ("-", "."):
        if sep in device_name:
            return device_name.split(sep)[0].lower()
    return device_name.lower()


def _subnet_from_ip(ip: str) -> str:
    """Return /24 prefix string, e.g. '10.1.2'."""
    parts = ip.split(".")
    if len(parts) == 4:
        return ".".join(parts[:3])
    return ip


def _action_from_guidance(guidance: str | None) -> str:
    if not guidance:
        return "patch"
    words = guidance.strip().split()
    if not words:
        return "patch"
    return words[0].lower()


def _days_ahead(days: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()


def _find_next_window(conn, site: str, after_utc: str) -> dict | None:
    """Find next active maintenance window for site (exact, then 'global', then any).

    For weekly/biweekly/monthly recurrences, project the window forward from
    start_utc until it is past after_utc.
    """
    candidates_sql = """
        SELECT * FROM nc_maintenance_windows
        WHERE active = 1
        ORDER BY
            CASE WHEN site = ? THEN 0 WHEN site = 'global' THEN 1 ELSE 2 END,
            start_utc ASC
    """
    try:
        rows = conn.execute(candidates_sql, (site,)).fetchall()
    except Exception:
        return None

    for row in rows:
        row = dict(row)
        start = row.get("start_utc", "")
        end = row.get("end_utc", "")
        recurrence = row.get("recurrence", "none") or "none"

        blackout_days: list[int] = []
        try:
            blackout_days = json.loads(row.get("blackout_days_json") or "[]")
        except Exception:
            pass

        # Project window forward until start > after_utc
        try:
            start_dt = datetime.fromisoformat(start)
            end_dt = datetime.fromisoformat(end)
            after_dt = datetime.fromisoformat(after_utc)
        except Exception:
            continue

        delta_map = {"weekly": 7, "biweekly": 14, "monthly": 30, "none": 0}
        delta_days = delta_map.get(recurrence, 0)

        while start_dt <= after_dt:
            if delta_days == 0:
                break
            start_dt += timedelta(days=delta_days)
            end_dt += timedelta(days=delta_days)

        if start_dt <= after_dt:
            continue

        # Check blackout day (0=Mon..6=Sun)
        if blackout_days and start_dt.weekday() in blackout_days:
            continue

        return {
            "id": row.get("id"),
            "site": row.get("site"),
            "start_utc": start_dt.isoformat(),
            "end_utc": end_dt.isoformat(),
        }

    return None


def _append_audit_log(conn, action: str, plan_id: str, advisory_id: int | None = None) -> None:
    try:
        conn.execute(
            """INSERT INTO nc_nqe_audit_log
               (action, input_text, nql_generated, data_source, confidence, advisory_id, created_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s)""",
            (
                action,
                f"patch_plan:{plan_id}",
                "",
                "patch_planner",
                1.0,
                advisory_id,
                _now(),
            ),
        )
    except Exception as exc:
        logger.warning("audit log write failed: %s", exc)


def _run_simulation(device_name: str) -> dict:
    """Try remediation simulation; degrade gracefully if unavailable."""
    try:
        from tools.network.remediation_simulator import simulate_remediation  # type: ignore
        result = simulate_remediation(-1)
        return {
            "simulation_status": result.get("twin_verdict", "skipped"),
            "blast_radius_json": json.dumps(result.get("blast_radius", [])),
        }
    except Exception as exc:
        logger.debug("simulation skipped for %s: %s", device_name, exc)
        return {"simulation_status": "skipped", "blast_radius_json": "[]"}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def create_patch_plan(approved_by: str | None = None) -> dict:
    """Create a full patch plan from approved triage queue items.

    Returns:
        {"plan_id": str, "batches": int, "devices": int, "plan": [rows]}
    """
    conn = get_connection()
    plan_id = str(uuid.uuid4())
    now = _now()
    plan_rows: list[dict] = []

    try:
        # 1. Get approved triage items ordered by rank
        queue = conn.execute(
            """SELECT tq.id AS tq_id, tq.advisory_id, tq.priority_score, tq.rank,
                      a.cve_id, a.vendor, a.remediation_guidance
               FROM nc_triage_queue tq
               LEFT JOIN nc_advisories a ON a.id = tq.advisory_id
               WHERE tq.status = 'approved'
               ORDER BY tq.rank ASC NULLS LAST, tq.priority_score DESC"""
        ).fetchall()

        if not queue:
            return {"plan_id": plan_id, "batches": 0, "devices": 0, "plan": []}

        batch_count = 0

        for q_row in queue:
            q = dict(q_row)
            advisory_id = q["advisory_id"]
            priority_score = q["priority_score"] or 0.0
            action = _action_from_guidance(q.get("remediation_guidance"))

            # 2. Get affected reachable/critical devices
            devices = conn.execute(
                """SELECT device_name, ip, criticality, surface_score
                   FROM nc_attack_surface
                   WHERE (advisory_id = %s OR cve_id = %s)
                     AND (reachable = 1 OR criticality >= 4)
                   ORDER BY criticality DESC, surface_score DESC""",
                (advisory_id, q.get("cve_id", "")),
            ).fetchall()

            if not devices:
                continue

            # 3. Cluster by site
            site_clusters: dict[str, list[dict]] = {}
            for d in devices:
                d = dict(d)
                site = _site_from_device(d["device_name"])
                if not site:
                    site = _subnet_from_ip(d.get("ip") or "unknown")
                site_clusters.setdefault(site, []).append(d)

            # 4. For each cluster create a batch
            for site, cluster_devices in site_clusters.items():
                batch_id = str(uuid.uuid4())
                batch_count += 1

                window = _find_next_window(conn, site, now)
                scheduled_at = window["start_utc"] if window else _days_ahead(_DEFAULT_LAG_DAYS)
                mw_id = window["id"] if window else None

                # 5. Simulate (one call per batch, use first device as representative)
                sim = _run_simulation(cluster_devices[0]["device_name"])

                # 6. Write one nc_patch_plans row per device
                for dev in cluster_devices:
                    risk_reduction = round(priority_score * (dev.get("surface_score") or 0.5), 4)
                    conn.execute(
                        # maintenance_window_id is window_id. action and
                        # blast_radius_json are real patch-plan attributes with no
                        # live column at all, so migration 329 adds them rather
                        # than folding them into simulation_json, which means
                        # something else (swp-scan-01).
                        """INSERT INTO nc_patch_plans
                           (plan_id, batch_id, advisory_id, device_name, action,
                            scheduled_at, window_id, blast_radius_json,
                            simulation_status, risk_reduction, approved_by, created_at)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (
                            plan_id,
                            batch_id,
                            advisory_id,
                            dev["device_name"],
                            action,
                            scheduled_at,
                            mw_id,
                            sim["blast_radius_json"],
                            sim["simulation_status"],
                            risk_reduction,
                            approved_by,
                            now,
                        ),
                    )
                    plan_rows.append(
                        {
                            "plan_id": plan_id,
                            "batch_id": batch_id,
                            "advisory_id": advisory_id,
                            "device_name": dev["device_name"],
                            "action": action,
                            "scheduled_at": scheduled_at,
                            "simulation_status": sim["simulation_status"],
                            "risk_reduction": risk_reduction,
                        }
                    )

        # 7. Audit log
        _append_audit_log(conn, "plan_create", plan_id)
        conn.commit()

    finally:
        conn.close()

    return {
        "plan_id": plan_id,
        "batches": batch_count,
        "devices": len(plan_rows),
        "plan": plan_rows,
    }


def get_patch_plans(
    plan_id: str | None = None,
    advisory_id: int | None = None,
    limit: int = 500,
) -> list[dict]:
    """Return nc_patch_plans rows with optional filters."""
    conn = get_connection()
    try:
        sql = "SELECT * FROM nc_patch_plans WHERE 1=1"
        params: list[Any] = []
        if plan_id:
            sql += " AND plan_id = ?"
            params.append(plan_id)
        if advisory_id is not None:
            sql += " AND advisory_id = ?"
            params.append(advisory_id)
        sql += " ORDER BY created_at DESC, batch_id, device_name LIMIT ?"
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_plan_summary(plan_id: str) -> dict:
    """Return aggregate for a plan: batches, devices, simulation breakdown, risk reduction."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM nc_patch_plans WHERE plan_id = %s", (plan_id,)
        ).fetchall()
        if not rows:
            return {"plan_id": plan_id, "found": False}

        rows = [dict(r) for r in rows]
        batch_ids = {r["batch_id"] for r in rows}
        sim_counts: dict[str, int] = {}
        total_risk = 0.0
        for r in rows:
            s = r.get("simulation_status") or "skipped"
            sim_counts[s] = sim_counts.get(s, 0) + 1
            total_risk += r.get("risk_reduction") or 0.0

        return {
            "plan_id": plan_id,
            "found": True,
            "batches": len(batch_ids),
            "devices": len(rows),
            "by_simulation_status": sim_counts,
            "risk_reduction_total": round(total_risk, 4),
            "scheduled_at_range": {
                "earliest": min((r["scheduled_at"] or "") for r in rows if r.get("scheduled_at")),
                "latest": max((r["scheduled_at"] or "") for r in rows if r.get("scheduled_at")),
            },
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="PVM Patch Planner")
    parser.add_argument("--create-plan", action="store_true")
    parser.add_argument("--approved-by", default=None)
    parser.add_argument("--plans", action="store_true")
    parser.add_argument("--plan-id", default=None)
    parser.add_argument("--advisory-id", type=int, default=None)
    parser.add_argument("--plan-summary", default=None, metavar="PLAN_ID")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)

    if args.create_plan:
        result = create_patch_plan(approved_by=args.approved_by)
    elif args.plans:
        result = get_patch_plans(plan_id=args.plan_id, advisory_id=args.advisory_id)
    elif args.plan_summary:
        result = get_plan_summary(args.plan_summary)
    else:
        parser.print_help()
        return

    if args.as_json:
        print(json.dumps(result, indent=2, default=str))
    else:
        print(result)


if __name__ == "__main__":
    _main(sys.argv[1:])
