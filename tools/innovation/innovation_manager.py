#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""Innovation Engine Manager — main orchestrator for autonomous self-improvement.

Coordinates the full innovation pipeline:
    DISCOVER → SCORE → TRIAGE → GENERATE → BUILD → PUBLISH → MEASURE → CALIBRATE

This is the single entry point for running the innovation engine, either as a
one-shot scan or as a continuous daemon.

Architecture:
    - Calls web_scanner, signal_ranker, trend_detector, triage_engine,
      solution_generator, introspective_analyzer, competitive_intel,
      standards_monitor in sequence
    - Respects quiet hours from args/innovation_config.yaml
    - Budget-limited: max auto-generated solutions per PI (default 10)
    - All operations logged to audit trail (append-only, D6)

Usage:
    # Full pipeline (one-shot)
    python tools/innovation/innovation_manager.py --run --json

    # Individual stages
    python tools/innovation/innovation_manager.py --discover --json
    python tools/innovation/innovation_manager.py --score --json
    python tools/innovation/innovation_manager.py --triage --json
    python tools/innovation/innovation_manager.py --generate --json

    # Continuous daemon mode
    python tools/innovation/innovation_manager.py --daemon --json

    # Dashboard / status
    python tools/innovation/innovation_manager.py --status --json
    python tools/innovation/innovation_manager.py --pipeline-report --json

    # Introspective + competitive intel
    python tools/innovation/innovation_manager.py --introspect --json
    python tools/innovation/innovation_manager.py --competitive --json
    python tools/innovation/innovation_manager.py --standards --json

    # Feedback calibration
    python tools/innovation/innovation_manager.py --calibrate --json
"""

import argparse
import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

# =========================================================================
# PATH SETUP
# =========================================================================
BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(BASE_DIR / "data" / "icdev.db")))
CONFIG_PATH = BASE_DIR / "args" / "innovation_config.yaml"

# =========================================================================
# GRACEFUL IMPORTS
# =========================================================================
try:
    import yaml
    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False

try:
    from tools.audit.audit_logger import log_event as audit_log_event
    _HAS_AUDIT = True
except ImportError:
    _HAS_AUDIT = False
    def audit_log_event(**kwargs):
        return -1

# Import innovation sub-modules (all optional — graceful degradation)
def _try_import(module_path, func_name):
    """Dynamically import a function with graceful fallback."""
    try:
        import importlib
        mod = importlib.import_module(module_path)
        return getattr(mod, func_name, None)
    except (ImportError, ModuleNotFoundError, AttributeError):
        return None


# =========================================================================
# HELPERS
# =========================================================================
def _now():
    """ISO-8601 timestamp."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_config():
    """Load innovation config from YAML."""
    if not _HAS_YAML or not CONFIG_PATH.exists():
        return {}
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _audit(event_type, action, details=None):
    """Write audit trail entry."""
    if _HAS_AUDIT:
        try:
            audit_log_event(
                event_type=event_type,
                actor="innovation-engine",
                action=action,
                details=json.dumps(details) if details else None,
                project_id="innovation-engine",
            )
        except Exception:
            pass


def _in_quiet_hours(config):
    """Check if current time is within quiet hours."""
    sched = config.get("scheduling", {})
    quiet = sched.get("quiet_hours", {})
    if not quiet:
        return False

    start_str = quiet.get("start", "02:00")
    end_str = quiet.get("end", "06:00")
    tz_name = quiet.get("timezone", "UTC")

    now = datetime.now(timezone.utc)
    current_time = now.strftime("%H:%M")

    # Simple string comparison for HH:MM (works for same-day ranges)
    if start_str <= end_str:
        return start_str <= current_time < end_str
    else:
        return current_time >= start_str or current_time < end_str


def _get_solution_budget(config):
    """Get remaining solution generation budget for current PI."""
    return config.get("scoring", {}).get("max_auto_solutions_per_pi", 10)


# =========================================================================
# PIPELINE STAGES
# =========================================================================
def stage_discover(sources=None, db_path=None):
    """Stage 1: Discover signals from web + introspective + competitive sources.

    Args:
        sources: List of sources to scan, or None for all.
        db_path: Optional DB path override.

    Returns:
        Dict with discovery results.
    """
    results = {"stage": "discover", "started_at": _now(), "sub_results": {}}

    # Web scanning
    run_scan = _try_import("tools.innovation.web_scanner", "run_scan")
    if run_scan:
        try:
            source = sources[0] if sources and len(sources) == 1 else None
            web_result = run_scan(source=source, db_path=db_path)
            results["sub_results"]["web_scanner"] = web_result
        except Exception as e:
            results["sub_results"]["web_scanner"] = {"error": str(e)}
    else:
        results["sub_results"]["web_scanner"] = {"error": "web_scanner not available"}

    results["completed_at"] = _now()
    _audit("innovation.discover", f"Discovery complete", results.get("sub_results"))
    return results


def stage_introspect(db_path=None):
    """Stage 1b: Introspective analysis — mine internal telemetry.

    Args:
        db_path: Optional DB path override.

    Returns:
        Dict with introspective analysis results.
    """
    analyze_all = _try_import("tools.innovation.introspective_analyzer", "analyze_all")
    if analyze_all:
        try:
            return analyze_all(db_path=db_path)
        except Exception as e:
            return {"error": str(e)}
    return {"error": "introspective_analyzer not available"}


def stage_competitive(db_path=None):
    """Stage 1c: Competitive intelligence scan.

    Args:
        db_path: Optional DB path override.

    Returns:
        Dict with competitive scan results.
    """
    scan_all = _try_import("tools.innovation.competitive_intel", "scan_all_competitors")
    if scan_all:
        try:
            return scan_all(db_path=db_path)
        except Exception as e:
            return {"error": str(e)}
    return {"error": "competitive_intel not available"}


def stage_standards(db_path=None):
    """Stage 1d: Standards body monitoring.

    Args:
        db_path: Optional DB path override.

    Returns:
        Dict with standards update results.
    """
    check_all = _try_import("tools.innovation.standards_monitor", "check_all_bodies")
    if check_all:
        try:
            return check_all(db_path=db_path)
        except Exception as e:
            return {"error": str(e)}
    return {"error": "standards_monitor not available"}


def stage_score(db_path=None):
    """Stage 2: Score all new signals.

    Args:
        db_path: Optional DB path override.

    Returns:
        Dict with scoring results.
    """
    score_all = _try_import("tools.innovation.signal_ranker", "score_all_new")
    if score_all:
        try:
            return score_all(db_path=db_path)
        except Exception as e:
            return {"error": str(e)}
    return {"error": "signal_ranker not available"}


def stage_triage(db_path=None):
    """Stage 3: Triage all scored signals through compliance gates.

    Args:
        db_path: Optional DB path override.

    Returns:
        Dict with triage results.
    """
    triage_all = _try_import("tools.innovation.triage_engine", "triage_all_scored")
    if triage_all:
        try:
            return triage_all(db_path=db_path)
        except Exception as e:
            return {"error": str(e)}
    return {"error": "triage_engine not available"}


def stage_detect_trends(db_path=None):
    """Stage 3b: Detect trends across signals.

    Args:
        db_path: Optional DB path override.

    Returns:
        Dict with trend detection results.
    """
    detect = _try_import("tools.innovation.trend_detector", "detect_trends")
    if detect:
        try:
            return detect(db_path=db_path)
        except Exception as e:
            return {"error": str(e)}
    return {"error": "trend_detector not available"}


def stage_generate(db_path=None):
    """Stage 4: Generate solution specs for approved signals.

    Args:
        db_path: Optional DB path override.

    Returns:
        Dict with solution generation results.
    """
    generate_all = _try_import("tools.innovation.solution_generator", "generate_all_approved")
    if generate_all:
        try:
            return generate_all(db_path=db_path)
        except Exception as e:
            return {"error": str(e)}
    return {"error": "solution_generator not available"}


def stage_calibrate(db_path=None):
    """Stage 7: Calibrate scoring weights based on feedback.

    Args:
        db_path: Optional DB path override.

    Returns:
        Dict with calibration results.
    """
    calibrate = _try_import("tools.innovation.signal_ranker", "calibrate_weights")
    if calibrate:
        try:
            return calibrate(db_path=db_path)
        except Exception as e:
            return {"error": str(e)}
    return {"error": "signal_ranker.calibrate_weights not available"}


# =========================================================================
# FULL PIPELINE
# =========================================================================
def run_full_pipeline(db_path=None):
    """Run the complete innovation pipeline: DISCOVER → SCORE → TRIAGE → GENERATE.

    Skips solution generation during quiet hours.

    Args:
        db_path: Optional DB path override.

    Returns:
        Dict with full pipeline results.
    """
    config = _load_config()
    pipeline_id = f"pipe-{uuid.uuid4().hex[:8]}"

    result = {
        "pipeline_id": pipeline_id,
        "started_at": _now(),
        "stages": {},
        "quiet_hours": False,
    }

    _audit("innovation.pipeline.start", f"Pipeline {pipeline_id} started")

    # Stage 1: Discover (web + introspective + competitive + standards)
    result["stages"]["discover"] = stage_discover(db_path=db_path)
    result["stages"]["introspect"] = stage_introspect(db_path=db_path)
    result["stages"]["competitive"] = stage_competitive(db_path=db_path)
    result["stages"]["standards"] = stage_standards(db_path=db_path)

    # Stage 2: Score
    result["stages"]["score"] = stage_score(db_path=db_path)

    # Stage 3: Triage + Trends
    result["stages"]["triage"] = stage_triage(db_path=db_path)
    result["stages"]["trends"] = stage_detect_trends(db_path=db_path)

    # Stage 4: Generate (skip during quiet hours)
    if _in_quiet_hours(config):
        result["quiet_hours"] = True
        result["stages"]["generate"] = {"skipped": "quiet_hours"}
    else:
        result["stages"]["generate"] = stage_generate(db_path=db_path)

    result["completed_at"] = _now()
    _audit("innovation.pipeline.complete", f"Pipeline {pipeline_id} completed", result)

    return result


# =========================================================================
# STATUS & REPORTING
# =========================================================================
def get_status(db_path=None):
    """Get innovation engine status overview.

    Args:
        db_path: Optional DB path override.

    Returns:
        Dict with signal counts by status, recent activity, and health.
    """
    import sqlite3

    path = db_path or DB_PATH
    if not path.exists():
        return {"error": f"Database not found: {path}", "healthy": False}

    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row

    try:
        status = {"healthy": True, "timestamp": _now()}

        # Signal counts by status
        try:
            rows = conn.execute(
                "SELECT status, COUNT(*) as count FROM innovation_signals GROUP BY status"
            ).fetchall()
            status["signals_by_status"] = {row["status"]: row["count"] for row in rows}
        except sqlite3.OperationalError:
            status["signals_by_status"] = {}
            status["healthy"] = False
            status["note"] = "innovation_signals table not found — run migration 004"

        # Total signals
        try:
            total = conn.execute("SELECT COUNT(*) as total FROM innovation_signals").fetchone()
            status["total_signals"] = total["total"] if total else 0
        except sqlite3.OperationalError:
            status["total_signals"] = 0

        # Recent signals (last 24h)
        try:
            from datetime import timedelta
            cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
            recent = conn.execute(
                "SELECT COUNT(*) as count FROM innovation_signals WHERE discovered_at >= ?",
                (cutoff,),
            ).fetchone()
            status["signals_last_24h"] = recent["count"] if recent else 0
        except sqlite3.OperationalError:
            status["signals_last_24h"] = 0

        # Solution counts
        try:
            rows = conn.execute(
                "SELECT status, COUNT(*) as count FROM innovation_solutions GROUP BY status"
            ).fetchall()
            status["solutions_by_status"] = {row["status"]: row["count"] for row in rows}
        except sqlite3.OperationalError:
            status["solutions_by_status"] = {}

        # Trend counts
        try:
            rows = conn.execute(
                "SELECT status, COUNT(*) as count FROM innovation_trends GROUP BY status"
            ).fetchall()
            status["trends_by_status"] = {row["status"]: row["count"] for row in rows}
        except sqlite3.OperationalError:
            status["trends_by_status"] = {}

        # Top sources
        try:
            rows = conn.execute(
                """SELECT source, COUNT(*) as count
                   FROM innovation_signals
                   GROUP BY source
                   ORDER BY count DESC
                   LIMIT 10"""
            ).fetchall()
            status["top_sources"] = {row["source"]: row["count"] for row in rows}
        except sqlite3.OperationalError:
            status["top_sources"] = {}

        return status

    finally:
        conn.close()


def get_pipeline_report(db_path=None):
    """Generate a comprehensive pipeline report.

    Args:
        db_path: Optional DB path override.

    Returns:
        Dict with full pipeline health, throughput, and recommendations.
    """
    status = get_status(db_path=db_path)

    signals_by_status = status.get("signals_by_status", {})
    total = status.get("total_signals", 0)

    # Compute pipeline throughput
    new = signals_by_status.get("new", 0)
    scored = signals_by_status.get("scored", 0)
    triaged = signals_by_status.get("triaged", 0)

    report = {
        "timestamp": _now(),
        "pipeline_health": status.get("healthy", False),
        "total_signals": total,
        "signals_by_status": signals_by_status,
        "solutions_by_status": status.get("solutions_by_status", {}),
        "trends_by_status": status.get("trends_by_status", {}),
        "top_sources": status.get("top_sources", {}),
        "pipeline_throughput": {
            "pending_scoring": new,
            "pending_triage": scored,
            "pending_generation": triaged,
        },
        "recommendations": [],
    }

    # Generate recommendations
    if new > 100:
        report["recommendations"].append(
            "High backlog of unscored signals — consider running --score"
        )
    if scored > 50:
        report["recommendations"].append(
            "High backlog of untriaged signals — consider running --triage"
        )
    if not status.get("healthy"):
        report["recommendations"].append(
            "Database tables missing — run: python tools/db/migrate.py --up"
        )

    return report


# =========================================================================
# DAEMON MODE
# =========================================================================
def run_daemon(db_path=None):
    """Run innovation engine as a continuous daemon.

    Respects scan intervals from config. Runs pipeline on schedule.
    Exits gracefully on KeyboardInterrupt.

    Args:
        db_path: Optional DB path override.
    """
    config = _load_config()
    default_interval = config.get("scheduling", {}).get("default_scan_interval_hours", 12)
    interval_seconds = default_interval * 3600

    print(f"Innovation Engine daemon started (interval: {default_interval}h)")
    _audit("innovation.daemon.start", "Daemon started")

    try:
        while True:
            print(f"\n[{_now()}] Running pipeline...")
            try:
                result = run_full_pipeline(db_path=db_path)
                print(f"[{_now()}] Pipeline complete. "
                      f"Stages: {len(result.get('stages', {}))}")
            except Exception as e:
                print(f"[{_now()}] Pipeline error: {e}", file=sys.stderr)
                _audit("innovation.daemon.error", f"Pipeline error: {e}")

            print(f"[{_now()}] Next run in {default_interval} hours...")
            time.sleep(interval_seconds)

    except KeyboardInterrupt:
        print(f"\n[{_now()}] Daemon stopped by user")
        _audit("innovation.daemon.stop", "Daemon stopped by user")


# =========================================================================
# CLI
# =========================================================================
def main():
    parser = argparse.ArgumentParser(
        description="ICDEV Innovation Engine — autonomous self-improvement orchestrator"
    )
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--db-path", type=Path, default=None, help="Database path override")

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--run", action="store_true", help="Run full pipeline (one-shot)")
    group.add_argument("--discover", action="store_true", help="Run discovery stage only")
    group.add_argument("--score", action="store_true", help="Run scoring stage only")
    group.add_argument("--triage", action="store_true", help="Run triage stage only")
    group.add_argument("--generate", action="store_true", help="Run solution generation only")
    group.add_argument("--introspect", action="store_true", help="Run introspective analysis")
    group.add_argument("--competitive", action="store_true", help="Run competitive intel scan")
    group.add_argument("--standards", action="store_true", help="Run standards monitoring")
    group.add_argument("--calibrate", action="store_true", help="Calibrate scoring weights")
    group.add_argument("--status", action="store_true", help="Show engine status")
    group.add_argument("--pipeline-report", action="store_true", help="Full pipeline report")
    group.add_argument("--daemon", action="store_true", help="Run as continuous daemon")

    args = parser.parse_args()

    try:
        if args.run:
            result = run_full_pipeline(db_path=args.db_path)
        elif args.discover:
            result = stage_discover(db_path=args.db_path)
        elif args.score:
            result = stage_score(db_path=args.db_path)
        elif args.triage:
            result = stage_triage(db_path=args.db_path)
        elif args.generate:
            result = stage_generate(db_path=args.db_path)
        elif args.introspect:
            result = stage_introspect(db_path=args.db_path)
        elif args.competitive:
            result = stage_competitive(db_path=args.db_path)
        elif args.standards:
            result = stage_standards(db_path=args.db_path)
        elif args.calibrate:
            result = stage_calibrate(db_path=args.db_path)
        elif args.status:
            result = get_status(db_path=args.db_path)
        elif args.pipeline_report:
            result = get_pipeline_report(db_path=args.db_path)
        elif args.daemon:
            run_daemon(db_path=args.db_path)
            return
        else:
            result = {"error": "No action specified"}

        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            # Human-readable output
            if args.status:
                print("Innovation Engine Status")
                print("=" * 40)
                print(f"  Healthy: {result.get('healthy', False)}")
                print(f"  Total signals: {result.get('total_signals', 0)}")
                print(f"  Last 24h: {result.get('signals_last_24h', 0)}")
                print(f"\n  Signals by status:")
                for status, count in result.get("signals_by_status", {}).items():
                    print(f"    {status}: {count}")
                print(f"\n  Solutions by status:")
                for status, count in result.get("solutions_by_status", {}).items():
                    print(f"    {status}: {count}")
                print(f"\n  Active trends:")
                for status, count in result.get("trends_by_status", {}).items():
                    print(f"    {status}: {count}")
            elif args.pipeline_report:
                print("Innovation Pipeline Report")
                print("=" * 40)
                print(f"  Health: {'OK' if result.get('pipeline_health') else 'DEGRADED'}")
                print(f"  Total signals: {result.get('total_signals', 0)}")
                throughput = result.get("pipeline_throughput", {})
                print(f"\n  Pipeline backlog:")
                print(f"    Pending scoring: {throughput.get('pending_scoring', 0)}")
                print(f"    Pending triage: {throughput.get('pending_triage', 0)}")
                print(f"    Pending generation: {throughput.get('pending_generation', 0)}")
                recs = result.get("recommendations", [])
                if recs:
                    print(f"\n  Recommendations:")
                    for rec in recs:
                        print(f"    - {rec}")
            else:
                # Generic JSON dump for other stages
                stages = result.get("stages", {})
                if stages:
                    print(f"Pipeline completed at {result.get('completed_at', '')}")
                    for stage_name, stage_result in stages.items():
                        if isinstance(stage_result, dict):
                            err = stage_result.get("error")
                            if err:
                                print(f"  {stage_name}: ERROR — {err}")
                            else:
                                print(f"  {stage_name}: OK")
                        else:
                            print(f"  {stage_name}: {stage_result}")
                else:
                    print(json.dumps(result, indent=2, default=str))

    except Exception as e:
        error = {"error": str(e)}
        if args.json:
            print(json.dumps(error, indent=2))
        else:
            print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
