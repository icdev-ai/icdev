#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV™ System Administrator
"""
Industry Research Engine — orchestrates 8-stage deep research pipeline.

Pipeline: SCOPE → LANDSCAPE → REGULATE → COMMUNITY → ACADEMIC → BUILD_BUY → SYNTHESIZE → DOSSIER

Architecture:
  D-RES-1:  Session-based lifecycle
  D-RES-2:  Declarative vertical configs
  D-RES-3:  SOURCE_SCANNERS function registry
  D-RES-4:  6-dimension weighted challenge scoring
  D-RES-9:  Template-based dossier (no LLM, air-gap safe)
  D-RES-10: Dossier → child app fitness via HITL
  D-RES-13: Parent-only (excluded from child apps)
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import sqlite3
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection  # noqa: E402

DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(BASE_DIR / "data" / "icdev.db")))
CONFIG_PATH = BASE_DIR / "args" / "research_config.yaml"

# --- Graceful imports ---
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


def _now():
    """ISO-8601 UTC timestamp."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _get_db(db_path=None):
    path = db_path or DB_PATH
    if not Path(str(path)).exists():
        raise FileNotFoundError(f"Database not found: {path}\nRun: python tools/db/init_icdev_db.py")
    conn = get_connection(db_path=str(path))
    return conn


def _audit(event_type, action, details=None):
    if _HAS_AUDIT:
        try:
            audit_log_event(
                event_type=event_type,
                actor="research-engine",
                action=action,
                details=json.dumps(details) if details else None,
                project_id="research-engine",
            )
        except Exception:
            pass


def _load_config():
    if not _HAS_YAML or not CONFIG_PATH.exists():
        return {}
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _try_import(module_path, func_name):
    """Dynamically import a function with graceful fallback."""
    try:
        mod = importlib.import_module(module_path)
        return getattr(mod, func_name, None)
    except (ImportError, ModuleNotFoundError, AttributeError):
        return None


def _in_quiet_hours(config):
    """Check if current time is within quiet hours."""
    quiet = config.get("scheduling", {}).get("quiet_hours", {})
    if not quiet or not quiet.get("enabled", False):
        return False
    start_str = quiet.get("start", "22:00")
    end_str = quiet.get("end", "06:00")
    current_time = datetime.now(timezone.utc).strftime("%H:%M")
    if start_str <= end_str:
        return start_str <= current_time < end_str
    else:
        return current_time >= start_str or current_time < end_str


# =========================================================================
# Pipeline Stages
# =========================================================================


def stage_scope(session_id, vertical_slug, db_path=None):
    """Stage 1: SCOPE — Create session, load vertical config, define focus."""
    result = {"stage": "SCOPE", "started_at": _now()}

    # Load vertical config
    load_verticals = _try_import("tools.research.vertical_loader", "load_verticals_to_db")
    if load_verticals:
        try:
            result["verticals"] = load_verticals(db_path=db_path)
        except Exception as e:
            result["verticals"] = {"error": str(e)}
    else:
        result["verticals"] = {"error": "vertical_loader not available"}

    # Advance session to LANDSCAPE
    advance = _try_import("tools.research.session_manager", "advance_stage")
    if advance:
        try:
            result["advance"] = advance(session_id, db_path=db_path)
        except Exception as e:
            result["advance"] = {"error": str(e)}

    result["completed_at"] = _now()
    _audit("research.scope", f"Scope complete for session {session_id}", result)
    return result


def stage_landscape(session_id, db_path=None):
    """Stage 2: LANDSCAPE — Scan review sites and commercial landscape."""
    result = {"stage": "LANDSCAPE", "started_at": _now()}

    run_scan = _try_import("tools.research.source_scanner", "run_scan")
    if run_scan:
        for source in ["review_site", "saas_commercial", "open_source"]:
            try:
                result[source] = run_scan(session_id, source=source, db_path=db_path)
            except Exception as e:
                result[source] = {"error": str(e)}
    else:
        result["scan"] = {"error": "source_scanner not available"}

    advance = _try_import("tools.research.session_manager", "advance_stage")
    if advance:
        try:
            result["advance"] = advance(session_id, db_path=db_path)
        except Exception as e:
            result["advance"] = {"error": str(e)}

    result["completed_at"] = _now()
    _audit("research.landscape", f"Landscape scan complete for {session_id}", result)
    return result


def stage_regulate(session_id, db_path=None):
    """Stage 3: REGULATE — Scan regulatory bodies and map to ICDEV™ frameworks."""
    result = {"stage": "REGULATE", "started_at": _now()}

    run_scan = _try_import("tools.research.source_scanner", "run_scan")
    if run_scan:
        try:
            result["regulatory_scan"] = run_scan(session_id, source="regulatory_body", db_path=db_path)
        except Exception as e:
            result["regulatory_scan"] = {"error": str(e)}

    map_regs = _try_import("tools.research.regulatory_mapper", "map_regulatory_signals")
    if map_regs:
        try:
            result["regulatory_mapping"] = map_regs(session_id, db_path=db_path)
        except Exception as e:
            result["regulatory_mapping"] = {"error": str(e)}

    advance = _try_import("tools.research.session_manager", "advance_stage")
    if advance:
        try:
            result["advance"] = advance(session_id, db_path=db_path)
        except Exception as e:
            result["advance"] = {"error": str(e)}

    result["completed_at"] = _now()
    _audit("research.regulate", f"Regulatory scan complete for {session_id}", result)
    return result


def stage_community(session_id, db_path=None):
    """Stage 4: COMMUNITY — Scan forums, Reddit, Stack Exchange."""
    result = {"stage": "COMMUNITY", "started_at": _now()}

    run_scan = _try_import("tools.research.source_scanner", "run_scan")
    if run_scan:
        for source in ["community_forum", "news_blog"]:
            try:
                result[source] = run_scan(session_id, source=source, db_path=db_path)
            except Exception as e:
                result[source] = {"error": str(e)}
    else:
        result["scan"] = {"error": "source_scanner not available"}

    advance = _try_import("tools.research.session_manager", "advance_stage")
    if advance:
        try:
            result["advance"] = advance(session_id, db_path=db_path)
        except Exception as e:
            result["advance"] = {"error": str(e)}

    result["completed_at"] = _now()
    _audit("research.community", f"Community scan complete for {session_id}", result)
    return result


def stage_academic(session_id, db_path=None):
    """Stage 5: ACADEMIC — Scan arXiv, IEEE, ACM, patents."""
    result = {"stage": "ACADEMIC", "started_at": _now()}

    run_scan = _try_import("tools.research.source_scanner", "run_scan")
    if run_scan:
        for source in ["academic_paper", "patent"]:
            try:
                result[source] = run_scan(session_id, source=source, db_path=db_path)
            except Exception as e:
                result[source] = {"error": str(e)}
    else:
        result["scan"] = {"error": "source_scanner not available"}

    advance = _try_import("tools.research.session_manager", "advance_stage")
    if advance:
        try:
            result["advance"] = advance(session_id, db_path=db_path)
        except Exception as e:
            result["advance"] = {"error": str(e)}

    result["completed_at"] = _now()
    _audit("research.academic", f"Academic scan complete for {session_id}", result)
    return result


def stage_video(session_id, db_path=None):
    """Stage 5b: VIDEO — Scan YouTube channels, search, and manual URLs (D-RES-14)."""
    result = {"stage": "VIDEO", "started_at": _now()}

    run_scan = _try_import("tools.research.source_scanner", "run_scan")
    if run_scan:
        try:
            result["video"] = run_scan(session_id, source="video", db_path=db_path)
        except Exception as e:
            result["video"] = {"error": str(e)}
    else:
        result["scan"] = {"error": "source_scanner not available"}

    advance = _try_import("tools.research.session_manager", "advance_stage")
    if advance:
        try:
            result["advance"] = advance(session_id, db_path=db_path)
        except Exception as e:
            result["advance"] = {"error": str(e)}

    result["completed_at"] = _now()
    _audit("research.video", f"Video scan complete for {session_id}", result)
    return result


def stage_build_buy(session_id, db_path=None):
    """Stage 6: BUILD_BUY — Cluster signals, score challenges, run build/buy analysis."""
    result = {"stage": "BUILD_BUY", "started_at": _now()}

    # Cluster signals into challenges
    cluster = _try_import("tools.research.challenge_scorer", "cluster_signals")
    if cluster:
        try:
            result["clustering"] = cluster(session_id, db_path=db_path)
        except Exception as e:
            result["clustering"] = {"error": str(e)}

    # Score all challenges
    score_all = _try_import("tools.research.challenge_scorer", "score_all_new")
    if score_all:
        try:
            result["scoring"] = score_all(session_id=session_id, db_path=db_path)
        except Exception as e:
            result["scoring"] = {"error": str(e)}

    # Map capabilities
    map_caps = _try_import("tools.research.capability_mapper", "map_all_challenges")
    if map_caps:
        try:
            result["capability_mapping"] = map_caps(session_id, db_path=db_path)
        except Exception as e:
            result["capability_mapping"] = {"error": str(e)}

    # Run build/buy analysis
    analyze = _try_import("tools.research.build_buy_analyzer", "analyze_all")
    if analyze:
        try:
            result["build_buy"] = analyze(session_id, db_path=db_path)
        except Exception as e:
            result["build_buy"] = {"error": str(e)}

    advance = _try_import("tools.research.session_manager", "advance_stage")
    if advance:
        try:
            result["advance"] = advance(session_id, db_path=db_path)
        except Exception as e:
            result["advance"] = {"error": str(e)}

    result["completed_at"] = _now()
    _audit("research.build_buy", f"Build/buy analysis complete for {session_id}", result)
    return result


def stage_synthesize(session_id, db_path=None):
    """Stage 7: SYNTHESIZE — Detect trends, cross-register to other engines."""
    result = {"stage": "SYNTHESIZE", "started_at": _now()}

    # Map challenge regulations
    map_challenge_regs = _try_import("tools.research.regulatory_mapper", "map_challenge_regulations")
    if map_challenge_regs:
        conn = _get_db(db_path)
        try:
            challenges = conn.execute(
                "SELECT id FROM research_challenges WHERE session_id = %s",
                (session_id,),
            ).fetchall()
            mapped = 0
            for c in challenges:
                try:
                    map_challenge_regs(c["id"], session_id, db_path=db_path)
                    mapped += 1
                except Exception:
                    pass
            result["regulation_mapping"] = {"challenges_mapped": mapped}
        finally:
            conn.close()

    # Detect trends
    detect = _try_import("tools.research.trend_detector", "detect_trends")
    if detect:
        try:
            result["trends"] = detect(session_id=session_id, db_path=db_path)
        except Exception as e:
            result["trends"] = {"error": str(e)}

    # Update session counts
    update_counts = _try_import("tools.research.session_manager", "update_session_counts")
    if update_counts:
        try:
            result["counts"] = update_counts(session_id, db_path=db_path)
        except Exception as e:
            result["counts"] = {"error": str(e)}

    advance = _try_import("tools.research.session_manager", "advance_stage")
    if advance:
        try:
            result["advance"] = advance(session_id, db_path=db_path)
        except Exception as e:
            result["advance"] = {"error": str(e)}

    result["completed_at"] = _now()
    _audit("research.synthesize", f"Synthesis complete for {session_id}", result)
    return result


def stage_forecast(session_id, db_path=None):
    """Stage 8: FORECAST — Generate AI-assisted predictions (D-RES-17)."""
    result = {"stage": "FORECAST", "started_at": _now()}

    generate = _try_import("tools.research.forecast_generator", "generate_forecasts")
    if generate:
        try:
            result["forecasts"] = generate(session_id, db_path=db_path)
        except Exception as e:
            result["forecasts"] = {"error": str(e)}
    else:
        result["forecasts"] = {"error": "forecast_generator not available"}

    result["completed_at"] = _now()
    _audit("research.forecast", f"Forecasts generated for {session_id}", result)
    return result


def stage_dossier(session_id, db_path=None):
    """Stage 9: DOSSIER — Generate the research dossier."""
    result = {"stage": "DOSSIER", "started_at": _now()}

    generate = _try_import("tools.research.dossier_generator", "generate_dossier")
    if generate:
        try:
            result["dossier"] = generate(session_id, db_path=db_path)
        except Exception as e:
            result["dossier"] = {"error": str(e)}
    else:
        result["dossier"] = {"error": "dossier_generator not available"}

    result["completed_at"] = _now()
    _audit("research.dossier", f"Dossier generated for {session_id}", result)
    return result


# =========================================================================
# Pipeline Orchestration
# =========================================================================

STAGE_FUNCTIONS = {
    "LANDSCAPE": stage_landscape,
    "REGULATE": stage_regulate,
    "COMMUNITY": stage_community,
    "ACADEMIC": stage_academic,
    "VIDEO": stage_video,
    "BUILD_BUY": stage_build_buy,
    "SYNTHESIZE": stage_synthesize,
    "FORECAST": stage_forecast,
    "DOSSIER": stage_dossier,
}


def run_pipeline(session_id=None, vertical=None, name=None, description=None, focus_areas=None, db_path=None):
    """Run the full 9-stage research pipeline."""
    config = _load_config()
    pipeline_id = f"rpipe-{uuid.uuid4().hex[:8]}"
    result = {"pipeline_id": pipeline_id, "started_at": _now(), "stages": {}}

    # Create session if needed
    if not session_id:
        if not vertical:
            return {"error": "Provide --session-id or --vertical to create a new session"}
        # Pre-load verticals from JSON so new configs are visible before session creation
        load_verticals = _try_import("tools.research.vertical_loader", "load_verticals_to_db")
        if load_verticals:
            try:
                load_verticals(db_path=db_path)
            except Exception:
                pass
        create = _try_import("tools.research.session_manager", "create_session")
        if create:
            try:
                session_result = create(
                    name=name or f"Research: {vertical}",
                    vertical_slug=vertical,
                    description=description,
                    focus_areas=focus_areas,
                    db_path=db_path,
                )
                if "error" in session_result:
                    return session_result
                session_id = session_result.get("session_id") or session_result.get("id")
                result["session"] = session_result
            except Exception as e:
                return {"error": f"Failed to create session: {e}"}
        else:
            return {"error": "session_manager not available"}

    result["session_id"] = session_id

    # Stage 1: SCOPE
    result["stages"]["SCOPE"] = stage_scope(session_id, vertical or "", db_path=db_path)

    # Stages 2-8
    for stage_name, stage_fn in STAGE_FUNCTIONS.items():
        if _in_quiet_hours(config):
            result["stages"][stage_name] = {"skipped": "quiet_hours"}
            continue
        try:
            result["stages"][stage_name] = stage_fn(session_id, db_path=db_path)
        except Exception as e:
            result["stages"][stage_name] = {"error": str(e)}

    result["completed_at"] = _now()
    _audit("research.pipeline.complete", f"Pipeline {pipeline_id} completed", result)
    return result


def run_stage(session_id, stage, db_path=None):
    """Run a single pipeline stage."""
    if stage == "SCOPE":
        # Need vertical for scope
        conn = _get_db(db_path)
        try:
            session = conn.execute(
                "SELECT vertical_name FROM research_sessions WHERE id = %s",
                (session_id,),
            ).fetchone()
            vertical = session["vertical_name"] if session else ""
        finally:
            conn.close()
        return stage_scope(session_id, vertical, db_path=db_path)

    stage_fn = STAGE_FUNCTIONS.get(stage)
    if not stage_fn:
        return {"error": f"Unknown stage: {stage}. Valid: SCOPE, {', '.join(STAGE_FUNCTIONS.keys())}"}
    return stage_fn(session_id, db_path=db_path)


def get_status(session_id=None, db_path=None):
    """Get engine or session status."""
    conn = _get_db(db_path)
    try:
        if session_id:
            get_session = _try_import("tools.research.session_manager", "get_session_status")
            if get_session:
                return get_session(session_id, db_path=db_path)
            # Fallback
            row = conn.execute("SELECT * FROM research_sessions WHERE id = %s", (session_id,)).fetchone()
            if not row:
                return {"error": f"Session not found: {session_id}"}
            return dict(row)

        # Engine-wide status
        sessions = conn.execute("SELECT status, COUNT(*) as cnt FROM research_sessions GROUP BY status").fetchall()
        verticals = conn.execute("SELECT COUNT(*) as cnt FROM research_verticals WHERE active = 1").fetchone()
        signals = conn.execute("SELECT COUNT(*) as cnt FROM research_signals").fetchone()
        challenges = conn.execute("SELECT COUNT(*) as cnt FROM research_challenges").fetchone()
        dossiers = conn.execute("SELECT COUNT(*) as cnt FROM research_dossiers").fetchone()

        return {
            "engine": "research",
            "version": "1.0.0",
            "status": "operational",
            "sessions": {r["status"]: r["cnt"] for r in sessions},
            "total_sessions": sum(r["cnt"] for r in sessions),
            "active_verticals": verticals["cnt"] if verticals else 0,
            "total_signals": signals["cnt"] if signals else 0,
            "total_challenges": challenges["cnt"] if challenges else 0,
            "total_dossiers": dossiers["cnt"] if dossiers else 0,
            "checked_at": _now(),
        }
    except sqlite3.OperationalError:
        return {
            "engine": "research",
            "status": "not_initialized",
            "hint": "Run: python tools/db/init_icdev_db.py",
            "checked_at": _now(),
        }
    finally:
        conn.close()


def run_daemon(db_path=None):
    """Run research engine in continuous daemon mode."""
    config = _load_config()
    sched = config.get("scheduling", {})
    if not sched.get("daemon_mode", False):
        print("Daemon mode not enabled in config. Set scheduling.daemon_mode: true")
        return

    interval = sched.get("daemon_interval_seconds", 3600)
    _audit("research.daemon.start", "Daemon started")

    try:
        while True:
            if _in_quiet_hours(config):
                time.sleep(600)
                continue
            # In daemon mode, check for sessions in 'created' status and run them
            conn = _get_db(db_path)
            try:
                pending = conn.execute(
                    "SELECT id FROM research_sessions WHERE status = 'created' ORDER BY created_at ASC LIMIT 1"
                ).fetchone()
            finally:
                conn.close()

            if pending:
                run_pipeline(session_id=pending["id"], db_path=db_path)

            time.sleep(interval)
    except KeyboardInterrupt:
        _audit("research.daemon.stop", "Daemon stopped by user")


# =========================================================================
# CLI
# =========================================================================


def _print_human(args, result):
    print("=" * 70)
    print("  ICDEV™ Industry Research Engine — CUI // SP-CTI")
    print("=" * 70)

    if isinstance(result, dict) and "error" in result:
        print(f"\n  ERROR: {result['error']}")
        if "hint" in result:
            print(f"  HINT:  {result['hint']}")
        print()
        print("=" * 70)
        return

    if args.run:
        print(f"\n  Pipeline: {result.get('pipeline_id', '')}")
        print(f"  Session:  {result.get('session_id', '')}")
        print(f"  Started:  {result.get('started_at', '')}")
        print(f"  Completed: {result.get('completed_at', '')}")
        stages = result.get("stages", {})
        print(f"\n  Stages ({len(stages)}):")
        for name, data in stages.items():
            status = "OK" if "error" not in data else "ERR"
            if data.get("skipped"):
                status = "SKIP"
            print(f"    [{status:4s}] {name}")

    elif args.status:
        if "engine" in result:
            print(f"\n  Engine:     {result.get('engine', '')}")
            print(f"  Status:     {result.get('status', '')}")
            print(f"  Verticals:  {result.get('active_verticals', 0)}")
            print(f"  Sessions:   {result.get('total_sessions', 0)}")
            print(f"  Signals:    {result.get('total_signals', 0)}")
            print(f"  Challenges: {result.get('total_challenges', 0)}")
            print(f"  Dossiers:   {result.get('total_dossiers', 0)}")
            sessions = result.get("sessions", {})
            if sessions:
                print("\n  Sessions by Status:")
                for st, cnt in sessions.items():
                    print(f"    {st:25s} {cnt}")
        else:
            print(f"\n  Session: {result.get('id', result.get('session_id', ''))}")
            print(f"  Status:  {result.get('status', '')}")
            print(f"  Stage:   {result.get('pipeline_stage', '')}")

    elif args.run_stage:
        stage_data = result
        print(f"\n  Stage: {stage_data.get('stage', args.stage)}")
        print(f"  Started:   {stage_data.get('started_at', '')}")
        print(f"  Completed: {stage_data.get('completed_at', '')}")

    print()
    print("=" * 70)


def main():
    parser = argparse.ArgumentParser(
        description="ICDEV™ Industry Research Engine — CUI // SP-CTI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  %(prog)s --status --json\n"
            "  %(prog)s --run --vertical trading --json\n"
            "  %(prog)s --run --session-id rsess-abc123 --json\n"
            "  %(prog)s --run-stage LANDSCAPE --session-id rsess-abc123 --json\n"
        ),
    )
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--human", action="store_true", help="Human-readable output")
    parser.add_argument("--db-path", type=Path, default=None, help="Database path override")

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--run", action="store_true", help="Run full pipeline")
    group.add_argument(
        "--run-stage",
        type=str,
        metavar="STAGE",
        dest="run_stage",
        help="Run single stage (SCOPE, LANDSCAPE, REGULATE, COMMUNITY, ACADEMIC, BUILD_BUY, SYNTHESIZE, DOSSIER)",
    )
    group.add_argument("--status", action="store_true", help="Engine or session status")
    group.add_argument("--daemon", action="store_true", help="Run in daemon mode")

    parser.add_argument("--session-id", type=str, help="Research session ID")
    parser.add_argument("--vertical", type=str, help="Vertical slug (for --run without session)")
    parser.add_argument("--name", type=str, help="Session name (for --run without session)")
    parser.add_argument("--description", type=str, help="Session description")
    parser.add_argument("--focus-areas", type=str, help="Comma-separated focus areas")
    parser.add_argument("--stage", type=str, help="Stage name (for --run-stage)")

    args = parser.parse_args()

    # Resolve stage from positional or named arg

    try:
        if args.run:
            focus = args.focus_areas.split(",") if args.focus_areas else None
            result = run_pipeline(
                session_id=args.session_id,
                vertical=args.vertical,
                name=args.name,
                description=args.description,
                focus_areas=focus,
                db_path=args.db_path,
            )
        elif args.run_stage:
            if not args.session_id:
                result = {"error": "--run-stage requires --session-id"}
            else:
                result = run_stage(args.session_id, args.run_stage, db_path=args.db_path)
        elif args.status:
            result = get_status(session_id=args.session_id, db_path=args.db_path)
        elif args.daemon:
            run_daemon(db_path=args.db_path)
            return
        else:
            result = {"error": "No action specified"}

        if args.human:
            _print_human(args, result)
        else:
            print(json.dumps(result, indent=2, default=str))

    except FileNotFoundError as e:
        error = {"error": str(e), "hint": "Run: python tools/db/init_icdev_db.py"}
        if args.human:
            print(f"ERROR: {e}", file=sys.stderr)
        else:
            print(json.dumps(error, indent=2))
        sys.exit(1)
    except Exception as e:
        error = {"error": str(e)}
        if args.human:
            print(f"ERROR: {e}", file=sys.stderr)
        else:
            print(json.dumps(error, indent=2))
        sys.exit(1)


if __name__ == "__main__":
    main()
