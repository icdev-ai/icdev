#!/usr/bin/env python3
from __future__ import annotations
# CUI // SP-CTI
"""Migration Intelligence Engine — Main Orchestrator.

8-stage pipeline: SCAN → ALIGN → SCORE → STRATEGIZE → ROADMAP → SIMULATE → PROMOTE
Daemon mode + one-shot + individual stage execution.
Air-gap safe: SCAN reads local canvas DBs only; SIMULATE uses local digital twin.

Usage:
    python tools/migration_intelligence/migration_manager.py --run --json
    python tools/migration_intelligence/migration_manager.py --scan --json
    python tools/migration_intelligence/migration_manager.py --status --json
    python tools/migration_intelligence/migration_manager.py --daemon --json
"""

import argparse
import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.migration_intelligence.migration_manager")

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

_DB_PATH = BASE_DIR / "data" / "migration_intel.db"
_CONFIG_PATH = BASE_DIR / "args" / "migration_intelligence_config.yaml"
_AIRGAP = os.environ.get("ICDEV_AIRGAP", "").lower() in ("true", "1", "yes")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _get_conn(db_path=None):
    from tools.migration_intelligence.db.init_db import get_connection, init_db
    path = db_path or str(_DB_PATH)
    init_db(path)
    return get_connection(path)


# =========================================================================
# CONFIG
# =========================================================================

def _load_config() -> dict:
    try:
        import yaml
        if _CONFIG_PATH.exists():
            with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
    except ImportError:
        pass
    return {}


def _in_quiet_hours(config: dict) -> bool:
    sched = config.get("scheduling", {})
    quiet = sched.get("quiet_hours", {})
    if not quiet:
        return False
    start_str = quiet.get("start", "02:00")
    end_str = quiet.get("end", "05:00")
    now_str = datetime.now(timezone.utc).strftime("%H:%M")
    if start_str <= end_str:
        return start_str <= now_str < end_str
    return now_str >= start_str or now_str < end_str


# =========================================================================
# AUDIT
# =========================================================================

def _audit(event_type: str, action: str, details=None):
    try:
        from tools.audit.audit_logger import log_event
        log_event(
            event_type=event_type,
            actor="migration-intel-engine",
            action=action,
            details=json.dumps(details) if details else None,
            project_id="migration-intelligence",
        )
    except Exception:
        pass


# =========================================================================
# PIPELINE STAGES
# =========================================================================

def stage_scan(config: dict | None = None, db_path=None) -> dict:
    """Stage 1 — SCAN: cross-canvas discovery (NDC EOL, MDC designs, topology)."""
    try:
        from tools.migration_intelligence.opportunity_scanner import run_full_scan
        return run_full_scan(config=config, db_path=db_path)
    except Exception as exc:
        return {"error": str(exc), "stage": "scan"}


def stage_align(db_path=None) -> dict:
    """Stage 2 — ALIGN: compute goal↔opportunity alignment scores."""
    try:
        from tools.migration_intelligence.goal_manager import compute_goal_alignment
        from tools.migration_intelligence.db.init_db import get_connection
        conn = get_connection(db_path)
        try:
            rows = conn.execute(
                "SELECT id FROM mi_opportunities WHERE status = 'identified'"
            ).fetchall()
            opp_ids = [r["id"] for r in rows]
        finally:
            conn.close()

        total = 0
        for oid in opp_ids:
            aligns = compute_goal_alignment(oid, db_path=db_path)
            total += len(aligns)
        return {"ok": True, "stage": "align", "opportunities_aligned": len(opp_ids), "alignment_rows": total}
    except Exception as exc:
        return {"error": str(exc), "stage": "align"}


def stage_score(config: dict | None = None, db_path=None) -> dict:
    """Stage 3 — SCORE: compute composite scores for all identified opportunities."""
    try:
        from tools.migration_intelligence.strategy_generator import score_all_opportunities
        return score_all_opportunities(config=config, db_path=db_path)
    except Exception as exc:
        return {"error": str(exc), "stage": "score"}


def stage_strategize(config: dict | None = None, db_path=None) -> dict:
    """Stage 4 — STRATEGIZE: generate 7R strategy options for scored opportunities."""
    try:
        from tools.migration_intelligence.strategy_generator import generate_all_strategies
        return generate_all_strategies(config=config, db_path=db_path)
    except Exception as exc:
        return {"error": str(exc), "stage": "strategize"}


def stage_roadmap(config: dict | None = None, db_path=None) -> dict:
    """Stage 5 — ROADMAP: build wave-based migration roadmap."""
    try:
        from tools.migration_intelligence.strategy_generator import build_roadmap
        horizon = (config or {}).get("roadmap", {}).get("default_horizon_years", 5)
        return build_roadmap(horizon_years=horizon, config=config, db_path=db_path)
    except Exception as exc:
        return {"error": str(exc), "stage": "roadmap"}


def stage_simulate(db_path=None) -> dict:
    """Stage 6 — SIMULATE: run digital twin what-if scenarios on top opportunities.

    Air-gap: local digital twin only (no external call).
    Non-air-gap: same — digital twin is always local.
    """
    try:
        from tools.migration_intelligence.db.init_db import get_connection
        conn = get_connection(db_path)
        try:
            top_opps = conn.execute(
                """SELECT id, title, composite_score
                   FROM mi_opportunities
                   WHERE status IN ('scored','strategy_generated')
                   ORDER BY composite_score DESC LIMIT 5"""
            ).fetchall()
            top_opps = [dict(r) for r in top_opps]
        finally:
            conn.close()

        simulated = []
        for opp in top_opps:
            try:
                from tools.network.twin import simulate_delta
                result = simulate_delta(
                    topology_id=None,
                    delta={"migration_opportunity": opp["id"]},
                    description=f"What-if: {opp['title'][:60]}",
                )
                simulated.append({"id": opp["id"], "sim_result": result})
            except Exception as exc:
                simulated.append({"id": opp["id"], "sim_result": {"error": str(exc)}})

        return {"ok": True, "stage": "simulate", "simulated": len(simulated), "results": simulated}
    except Exception as exc:
        return {"error": str(exc), "stage": "simulate"}


def stage_promote(db_path=None) -> dict:
    """Stage 7 — PROMOTE: create Kanban tasks for high-priority opportunities."""
    try:
        from tools.migration_intelligence.db.init_db import get_connection
        conn = get_connection(db_path)
        try:
            rows = conn.execute(
                """SELECT id, title, priority, composite_score
                   FROM mi_opportunities
                   WHERE status = 'strategy_generated'
                     AND priority IN ('critical','high')
                   ORDER BY composite_score DESC LIMIT 10"""
            ).fetchall()
            opps = [dict(r) for r in rows]
        finally:
            conn.close()

        promoted = 0
        for opp in opps:
            try:
                _promote_to_kanban(opp)
                promoted += 1
            except Exception:
                pass

        return {"ok": True, "stage": "promote", "promoted": promoted}
    except Exception as exc:
        return {"error": str(exc), "stage": "promote"}


def _promote_to_kanban(opp: dict) -> None:
    """Create a Kanban suggestion task for an opportunity."""
    # swp-scan-01: this targeted `tasks` (an 8-column legacy table with no
    # description/updated_at) rather than the `kanban_tasks` board, and used
    # SQLite-only `INSERT OR IGNORE`, which PostgreSQL cannot even parse. Both
    # failures landed in the bare `except` below, so no opportunity was ever
    # promoted. task_factory is the sanctioned writer and skips existing ids.
    try:
        from tools.kanban.task_factory import create_tasks

        task_id = f"mi-opp-{uuid.uuid4().hex[:8]}"
        create_tasks(
            [
                {
                    "id": task_id,
                    "title": f"[Migration Intel] {opp['title'][:100]}",
                    "description": (
                        f"Migration opportunity identified "
                        f"(score: {opp['composite_score']:.2f}). "
                        f"Review strategies and schedule for roadmap wave."
                    ),
                    "status": "suggested",
                    "priority": opp.get("priority", "medium"),
                    "dispatch_source": "migration_intelligence",
                }
            ]
        )
    except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
        logger.warning("_promote_to_kanban: best-effort task creation failed (non-blocking): %s", exc)


# =========================================================================
# FULL PIPELINE
# =========================================================================

def run_full_pipeline(db_path=None) -> dict:
    """SCAN → ALIGN → SCORE → STRATEGIZE → ROADMAP → SIMULATE → PROMOTE."""
    config = _load_config()
    pipeline_id = f"mipipe-{uuid.uuid4().hex[:8]}"
    result = {
        "pipeline_id": pipeline_id,
        "started_at": _now(),
        "airgap_mode": _AIRGAP,
        "stages": {},
        "quiet_hours": False,
    }
    _audit("migration_intel.pipeline.start", f"Pipeline {pipeline_id} started")

    result["stages"]["scan"] = stage_scan(config=config, db_path=db_path)
    result["stages"]["align"] = stage_align(db_path=db_path)
    result["stages"]["score"] = stage_score(config=config, db_path=db_path)

    if _in_quiet_hours(config):
        result["quiet_hours"] = True
        for s in ("strategize", "roadmap", "simulate", "promote"):
            result["stages"][s] = {"skipped": "quiet_hours"}
    else:
        result["stages"]["strategize"] = stage_strategize(config=config, db_path=db_path)
        result["stages"]["roadmap"] = stage_roadmap(config=config, db_path=db_path)
        result["stages"]["simulate"] = stage_simulate(db_path=db_path)
        result["stages"]["promote"] = stage_promote(db_path=db_path)

    result["completed_at"] = _now()
    _audit("migration_intel.pipeline.complete", f"Pipeline {pipeline_id} complete", result)
    return result


# =========================================================================
# STATUS
# =========================================================================

def get_status(db_path=None) -> dict:
    """Return MI engine status: opportunity counts, goal counts, scan history."""
    # cnr-mi-01: schema is ensured via _get_conn()/init_db and errors are handled
    # per-table, so no SQLite-only file-existence guard (which broke on the
    # PostgreSQL-primary backend where mi_* tables live in the shared icdev db).
    conn = _get_conn(db_path)
    try:
        status = {"healthy": True, "timestamp": _now(), "airgap_mode": _AIRGAP}

        for table, label in [
            ("mi_opportunities", "opportunities"),
            ("mi_goals", "goals"),
            ("mi_strategies", "strategies"),
            ("mi_roadmaps", "roadmaps"),
            ("mi_wishlist", "wishlist"),
            ("mi_scans", "scans"),
        ]:
            try:
                rows = conn.execute(f"SELECT status, COUNT(*) as c FROM {table} GROUP BY status").fetchall()
                status[f"{label}_by_status"] = {r["status"]: r["c"] for r in rows}
            except Exception:  # noqa: BLE001 — table may not exist yet (any backend)
                status[f"{label}_by_status"] = {}

        try:
            row = conn.execute(
                "SELECT * FROM mi_scans ORDER BY started_at DESC LIMIT 1"
            ).fetchone()
            status["last_scan"] = dict(row) if row else None
        except Exception:  # noqa: BLE001
            status["last_scan"] = None

        return status
    finally:
        conn.close()


def get_pipeline_report(db_path=None) -> dict:
    """Full pipeline health report with recommendations."""
    status = get_status(db_path=db_path)
    opps = status.get("opportunities_by_status", {})
    total_opps = sum(opps.values())

    report = {
        "timestamp": _now(),
        "pipeline_healthy": status.get("healthy", False),
        "airgap_mode": _AIRGAP,
        "totals": {
            "opportunities": total_opps,
            "goals": sum(status.get("goals_by_status", {}).values()),
            "strategies": sum(status.get("strategies_by_status", {}).values()),
            "roadmaps": sum(status.get("roadmaps_by_status", {}).values()),
            "wishlist": sum(status.get("wishlist_by_status", {}).values()),
        },
        "opportunities_by_status": opps,
        "last_scan": status.get("last_scan"),
        "recommendations": [],
    }

    if opps.get("identified", 0) > 20:
        report["recommendations"].append("High backlog of unscored opportunities — run --align --score")
    if opps.get("scored", 0) > 10:
        report["recommendations"].append("Scored opportunities need strategies — run --strategize")
    if not status.get("healthy"):
        report["recommendations"].append("DB missing — run: python tools/migration_intelligence/db/init_db.py")
    if _AIRGAP:
        report["recommendations"].append("Air-gap mode: NL goal parsing and external EOL feeds disabled")

    return report


# =========================================================================
# DAEMON
# =========================================================================

def run_daemon(db_path=None) -> None:
    """Run as continuous daemon — executes pipeline on schedule."""
    config = _load_config()
    interval_h = config.get("scheduling", {}).get("default_scan_interval_hours", 24)
    interval_s = interval_h * 3600

    print(f"[{_now()}] Migration Intelligence daemon started (interval: {interval_h}h, airgap: {_AIRGAP})")
    _audit("migration_intel.daemon.start", "Daemon started")

    try:
        while True:
            print(f"\n[{_now()}] Running pipeline...")
            try:
                result = run_full_pipeline(db_path=db_path)
                stages = result.get("stages", {})
                scan_count = result.get("stages", {}).get("scan", {}).get("total_found", 0)
                print(f"[{_now()}] Pipeline complete. Opportunities found: {scan_count}. Stages: {len(stages)}")
            except Exception as exc:
                print(f"[{_now()}] Pipeline error: {exc}", file=sys.stderr)
                _audit("migration_intel.daemon.error", str(exc))

            print(f"[{_now()}] Next run in {interval_h}h...")
            time.sleep(interval_s)
    except KeyboardInterrupt:
        print(f"\n[{_now()}] Daemon stopped")
        _audit("migration_intel.daemon.stop", "Daemon stopped by user")


# =========================================================================
# CLI
# =========================================================================

def main():
    parser = argparse.ArgumentParser(description="ICDEV™ Migration Intelligence Engine")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--db-path", type=Path, default=None)

    grp = parser.add_mutually_exclusive_group(required=True)
    grp.add_argument("--run", action="store_true", help="Run full pipeline")
    grp.add_argument("--scan", action="store_true", help="Canvas scan only")
    grp.add_argument("--align", action="store_true", help="Goal alignment only")
    grp.add_argument("--score", action="store_true", help="Scoring only")
    grp.add_argument("--strategize", action="store_true", help="Strategy generation only")
    grp.add_argument("--roadmap", action="store_true", help="Roadmap build only")
    grp.add_argument("--simulate", action="store_true", help="Digital twin simulation")
    grp.add_argument("--promote", action="store_true", help="Kanban promotion only")
    grp.add_argument("--status", action="store_true", help="Engine status")
    grp.add_argument("--pipeline-report", action="store_true", help="Full report")
    grp.add_argument("--daemon", action="store_true", help="Continuous daemon mode")
    grp.add_argument("--init-db", action="store_true", help="Initialize DB schema")

    args = parser.parse_args()
    db = str(args.db_path) if args.db_path else None

    if args.init_db:
        from tools.migration_intelligence.db.init_db import init_db
        init_db(db)
        return

    if args.daemon:
        run_daemon(db_path=db)
        return

    dispatch = {
        "run": lambda: run_full_pipeline(db_path=db),
        "scan": lambda: stage_scan(db_path=db),
        "align": lambda: stage_align(db_path=db),
        "score": lambda: stage_score(db_path=db),
        "strategize": lambda: stage_strategize(db_path=db),
        "roadmap": lambda: stage_roadmap(db_path=db),
        "simulate": lambda: stage_simulate(db_path=db),
        "promote": lambda: stage_promote(db_path=db),
        "status": lambda: get_status(db_path=db),
        "pipeline_report": lambda: get_pipeline_report(db_path=db),
    }

    for key, fn in dispatch.items():
        if getattr(args, key.replace("-", "_"), False):
            result = fn()
            if args.json:
                print(json.dumps(result, indent=2, default=str))
            else:
                print(json.dumps(result, indent=2, default=str))
            return


if __name__ == "__main__":
    main()
