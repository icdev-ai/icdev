#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""Creative Engine — customer-centric feature opportunity discovery orchestrator.

Coordinates the full creative pipeline:
    DISCOVER → EXTRACT → SCORE → RANK → GENERATE

This is the single entry point for the Creative Engine, either as a one-shot
scan or as a continuous daemon.

Architecture:
    - Calls competitor_discoverer, source_scanner, pain_extractor, gap_scorer,
      trend_tracker, spec_generator in sequence (D351)
    - Source adapters via function registry dict (D352)
    - Competitor auto-discovery is advisory-only (D353)
    - Pain extraction is deterministic keyword/regex (D354)
    - 3-dimension scoring: pain_frequency(0.40) + gap_uniqueness(0.35) +
      effort_to_impact(0.25) (D355)
    - Feature specs are template-based (D356)
    - All tables append-only except creative_competitors (D357)
    - Reuses _safe_get(), _get_db(), _now(), _audit() helpers (D358)
    - Daemon mode respects quiet hours from config (D359)
    - High-scoring signals cross-register to innovation_signals (D360)

Usage:
    # Full pipeline (one-shot)
    python tools/creative/creative_engine.py --run --json

    # Individual stages
    python tools/creative/creative_engine.py --discover --domain "proposal management" --json
    python tools/creative/creative_engine.py --scan --all --json
    python tools/creative/creative_engine.py --extract --json
    python tools/creative/creative_engine.py --score --json
    python tools/creative/creative_engine.py --rank --top-k 20 --json
    python tools/creative/creative_engine.py --generate --json

    # Status and queries
    python tools/creative/creative_engine.py --status --json
    python tools/creative/creative_engine.py --competitors --json
    python tools/creative/creative_engine.py --trends --json
    python tools/creative/creative_engine.py --specs --json

    # Continuous daemon mode
    python tools/creative/creative_engine.py --daemon --json
"""

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

# =========================================================================
# PATH SETUP
# =========================================================================
BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(BASE_DIR / "data" / "icdev.db")))
CONFIG_PATH = BASE_DIR / "args" / "creative_config.yaml"

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
    """Load creative engine config from YAML."""
    if not _HAS_YAML or not CONFIG_PATH.exists():
        return {}
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _get_db(db_path=None):
    """Get SQLite connection with row factory."""
    path = db_path or DB_PATH
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _audit(event_type, action, details=None):
    """Write audit trail entry."""
    if _HAS_AUDIT:
        try:
            audit_log_event(
                event_type=event_type,
                actor="creative-engine",
                action=action,
                details=json.dumps(details) if details else None,
                project_id="creative-engine",
            )
        except Exception:
            pass


def _in_quiet_hours(config):
    """Check if current time is within quiet hours (D359)."""
    sched = config.get("scheduling", {})
    quiet = sched.get("quiet_hours", {})
    if not quiet:
        return False

    start_str = quiet.get("start", "02:00")
    end_str = quiet.get("end", "06:00")

    now = datetime.now(timezone.utc)
    current_time = now.strftime("%H:%M")

    if start_str <= end_str:
        return start_str <= current_time < end_str
    else:
        return current_time >= start_str or current_time < end_str


# =========================================================================
# PIPELINE STAGES
# =========================================================================
def stage_discover(domain=None, db_path=None):
    """Stage 1: Auto-discover competitors for the given product domain.

    Scrapes G2/Capterra/TrustRadius category pages for competitor names,
    ratings, and review counts.  Stores as status='discovered' — human
    must --confirm before tracking activates (D353).

    Args:
        domain: Product domain string (e.g. "proposal management").
        db_path: Optional DB path override.

    Returns:
        Dict with discovery results.
    """
    result = {"stage": "discover", "started_at": _now()}

    run_discovery = _try_import("tools.creative.competitor_discoverer", "run_discovery")
    if run_discovery:
        try:
            result["discovery"] = run_discovery(domain=domain, db_path=db_path)
        except Exception as e:
            result["discovery"] = {"error": str(e)}
    else:
        result["discovery"] = {"error": "competitor_discoverer not available"}

    result["completed_at"] = _now()
    _audit("creative.discover", f"Discovery complete for domain={domain}", result)
    return result


def stage_scan(source=None, db_path=None):
    """Stage 1b: Scan all configured sources for customer signals.

    Runs source adapters (G2, Capterra, TrustRadius, Reddit, GitHub Issues,
    Product Hunt, GovCon blogs) and stores normalized signals.

    Args:
        source: Specific source name to scan, or None for all.
        db_path: Optional DB path override.

    Returns:
        Dict with scan results.
    """
    result = {"stage": "scan", "started_at": _now()}

    run_scan = _try_import("tools.creative.source_scanner", "run_scan")
    if run_scan:
        try:
            result["scan"] = run_scan(source=source, db_path=db_path)
        except Exception as e:
            result["scan"] = {"error": str(e)}
    else:
        result["scan"] = {"error": "source_scanner not available"}

    result["completed_at"] = _now()
    _audit("creative.scan", f"Scan complete source={source or 'all'}", result)
    return result


def stage_extract(db_path=None):
    """Stage 2: Extract pain points from unprocessed signals.

    Uses deterministic keyword matching + sentiment detection (D354).
    Groups signals with >= 3 shared keywords into pain points.

    Args:
        db_path: Optional DB path override.

    Returns:
        Dict with extraction results.
    """
    result = {"stage": "extract", "started_at": _now()}

    extract_all = _try_import("tools.creative.pain_extractor", "extract_all_new")
    if extract_all:
        try:
            result["extraction"] = extract_all(db_path=db_path)
        except Exception as e:
            result["extraction"] = {"error": str(e)}
    else:
        result["extraction"] = {"error": "pain_extractor not available"}

    result["completed_at"] = _now()
    _audit("creative.extract", "Pain point extraction complete", result)
    return result


def stage_score(db_path=None):
    """Stage 3: Score pain points + identify feature gaps.

    3-dimension composite scoring (D355):
        pain_frequency (0.40) + gap_uniqueness (0.35) + effort_to_impact (0.25)

    Also identifies feature gaps from scored pain points.

    Args:
        db_path: Optional DB path override.

    Returns:
        Dict with scoring results.
    """
    result = {"stage": "score", "started_at": _now(), "sub_results": {}}

    # Score all unscored pain points
    score_all = _try_import("tools.creative.gap_scorer", "score_all_new")
    if score_all:
        try:
            result["sub_results"]["scoring"] = score_all(db_path=db_path)
        except Exception as e:
            result["sub_results"]["scoring"] = {"error": str(e)}
    else:
        result["sub_results"]["scoring"] = {"error": "gap_scorer not available"}

    # Identify feature gaps from high-scoring pain points
    identify_gaps = _try_import("tools.creative.gap_scorer", "identify_feature_gaps")
    if identify_gaps:
        try:
            result["sub_results"]["gaps"] = identify_gaps(db_path=db_path)
        except Exception as e:
            result["sub_results"]["gaps"] = {"error": str(e)}
    else:
        result["sub_results"]["gaps"] = {"error": "gap_scorer.identify_feature_gaps not available"}

    result["completed_at"] = _now()
    _audit("creative.score", "Scoring and gap identification complete", result)
    return result


def stage_rank(top_k=20, db_path=None):
    """Stage 4: Rank top pain points and detect trends.

    Deduplicates, clusters, and ranks by composite score.
    Also runs trend detection for velocity/acceleration tracking.

    Args:
        top_k: Number of top items to return.
        db_path: Optional DB path override.

    Returns:
        Dict with ranked results and trends.
    """
    result = {"stage": "rank", "started_at": _now(), "sub_results": {}}

    # Get top scored pain points
    get_top = _try_import("tools.creative.gap_scorer", "get_top_scored")
    if get_top:
        try:
            result["sub_results"]["top_pain_points"] = get_top(
                limit=top_k, min_score=0.0, db_path=db_path
            )
        except Exception as e:
            result["sub_results"]["top_pain_points"] = {"error": str(e)}
    else:
        result["sub_results"]["top_pain_points"] = {"error": "gap_scorer.get_top_scored not available"}

    # Detect trends
    detect = _try_import("tools.creative.trend_tracker", "detect_trends")
    if detect:
        try:
            result["sub_results"]["trends"] = detect(db_path=db_path)
        except Exception as e:
            result["sub_results"]["trends"] = {"error": str(e)}
    else:
        result["sub_results"]["trends"] = {"error": "trend_tracker not available"}

    result["completed_at"] = _now()
    _audit("creative.rank", f"Ranking complete (top_k={top_k})", result)
    return result


def stage_generate(db_path=None):
    """Stage 5: Generate feature specs for eligible gaps.

    Template-based spec generation (D356) for pain points with
    composite_score >= auto_spec threshold (default 0.75).

    Args:
        db_path: Optional DB path override.

    Returns:
        Dict with generation results.
    """
    result = {"stage": "generate", "started_at": _now()}

    generate_all = _try_import("tools.creative.spec_generator", "generate_all_eligible")
    if generate_all:
        try:
            result["generation"] = generate_all(db_path=db_path)
        except Exception as e:
            result["generation"] = {"error": str(e)}
    else:
        result["generation"] = {"error": "spec_generator not available"}

    # Cross-register high-scoring signals to Innovation Engine (D360)
    _cross_register_to_innovation(db_path=db_path)

    result["completed_at"] = _now()
    _audit("creative.generate", "Spec generation complete", result)
    return result


# =========================================================================
# INNOVATION ENGINE BRIDGE (D360)
# =========================================================================
def _cross_register_to_innovation(db_path=None):
    """Cross-register high-scoring creative signals to innovation_signals.

    Pain points scoring above the innovation_bridge.min_score threshold
    (default 0.60) are registered as innovation signals with
    source='creative_engine' for trend detection by the Innovation Engine.

    Args:
        db_path: Optional DB path override.
    """
    config = _load_config()
    bridge = config.get("innovation_bridge", {})
    if not bridge.get("enabled", True):
        return

    min_score = bridge.get("min_score", 0.60)
    path = db_path or DB_PATH

    if not Path(path).exists():
        return

    conn = _get_db(db_path)
    try:
        # Find pain points above threshold that haven't been cross-registered
        try:
            rows = conn.execute(
                """SELECT id, title, description, composite_score, category,
                          keywords, metadata
                   FROM creative_pain_points
                   WHERE composite_score >= ?
                   AND id NOT IN (
                       SELECT json_extract(metadata, '$.creative_pain_point_id')
                       FROM innovation_signals
                       WHERE source = 'creative_engine'
                       AND json_extract(metadata, '$.creative_pain_point_id') IS NOT NULL
                   )
                   ORDER BY composite_score DESC
                   LIMIT 50""",
                (min_score,),
            ).fetchall()
        except sqlite3.OperationalError:
            # innovation_signals table may not exist
            return

        registered = 0
        for row in rows:
            signal_id = f"isig-{uuid.uuid4().hex[:12]}"
            content = f"{row['title']}: {row['description'] or ''}"
            content_hash = hashlib.sha256(content.encode()).hexdigest()[:16]

            metadata = {
                "creative_pain_point_id": row["id"],
                "creative_score": row["composite_score"],
                "creative_category": row["category"],
            }

            try:
                conn.execute(
                    """INSERT INTO innovation_signals
                       (id, source, source_type, category, title, body, url,
                        content_hash, composite_score, status, metadata,
                        discovered_at, classification)
                       VALUES (?, 'creative_engine', 'external_framework_analysis',
                               ?, ?, ?, NULL, ?, ?, 'new', ?, ?, 'CUI')""",
                    (
                        signal_id,
                        row["category"] or "feature_gap",
                        row["title"],
                        row["description"] or "",
                        content_hash,
                        row["composite_score"],
                        json.dumps(metadata),
                        _now(),
                    ),
                )
                registered += 1
            except (sqlite3.IntegrityError, sqlite3.OperationalError):
                continue

        if registered > 0:
            conn.commit()
            _audit(
                "creative.innovation_bridge",
                f"Cross-registered {registered} signals to Innovation Engine",
                {"count": registered, "min_score": min_score},
            )

    except Exception:
        pass
    finally:
        conn.close()


# =========================================================================
# FULL PIPELINE
# =========================================================================
def run_full_pipeline(domain=None, db_path=None):
    """Run the complete creative pipeline: DISCOVER → EXTRACT → SCORE → RANK → GENERATE.

    Skips spec generation during quiet hours (D359).

    Args:
        domain: Product domain for competitor discovery.
        db_path: Optional DB path override.

    Returns:
        Dict with full pipeline results.
    """
    config = _load_config()
    pipeline_id = f"cpipe-{uuid.uuid4().hex[:8]}"

    result = {
        "pipeline_id": pipeline_id,
        "started_at": _now(),
        "stages": {},
        "quiet_hours": False,
    }

    _audit("creative.pipeline.start", f"Pipeline {pipeline_id} started")

    # Use domain from config if not provided
    if not domain:
        domain = config.get("domain", {}).get("name")

    # Stage 1: Discover competitors + scan sources
    result["stages"]["discover"] = stage_discover(domain=domain, db_path=db_path)
    result["stages"]["scan"] = stage_scan(db_path=db_path)

    # Stage 2: Extract pain points
    result["stages"]["extract"] = stage_extract(db_path=db_path)

    # Stage 3: Score + identify gaps
    result["stages"]["score"] = stage_score(db_path=db_path)

    # Stage 4: Rank + detect trends
    result["stages"]["rank"] = stage_rank(db_path=db_path)

    # Stage 5: Generate specs (skip during quiet hours)
    if _in_quiet_hours(config):
        result["quiet_hours"] = True
        result["stages"]["generate"] = {"skipped": "quiet_hours"}
    else:
        result["stages"]["generate"] = stage_generate(db_path=db_path)

    result["completed_at"] = _now()
    _audit("creative.pipeline.complete", f"Pipeline {pipeline_id} completed", result)

    return result


# =========================================================================
# STATUS & QUERIES
# =========================================================================
def get_status(db_path=None):
    """Get creative engine status overview.

    Returns:
        Dict with counts, health, and last activity timestamps.
    """
    path = db_path or DB_PATH
    if not Path(path).exists():
        return {"error": f"Database not found: {path}", "healthy": False}

    conn = _get_db(db_path)
    try:
        status = {"healthy": True, "timestamp": _now()}

        # Table counts
        tables = {
            "competitors": "creative_competitors",
            "signals": "creative_signals",
            "pain_points": "creative_pain_points",
            "feature_gaps": "creative_feature_gaps",
            "specs": "creative_specs",
            "trends": "creative_trends",
        }

        for key, table in tables.items():
            try:
                row = conn.execute(f"SELECT COUNT(*) as cnt FROM {table}").fetchone()
                status[f"total_{key}"] = row["cnt"] if row else 0
            except sqlite3.OperationalError:
                status[f"total_{key}"] = 0
                status["healthy"] = False
                status.setdefault("missing_tables", []).append(table)

        # Competitors by status
        try:
            rows = conn.execute(
                "SELECT status, COUNT(*) as cnt FROM creative_competitors GROUP BY status"
            ).fetchall()
            status["competitors_by_status"] = {r["status"]: r["cnt"] for r in rows}
        except sqlite3.OperationalError:
            status["competitors_by_status"] = {}

        # Pain points by status
        try:
            rows = conn.execute(
                "SELECT status, COUNT(*) as cnt FROM creative_pain_points GROUP BY status"
            ).fetchall()
            status["pain_points_by_status"] = {r["status"]: r["cnt"] for r in rows}
        except sqlite3.OperationalError:
            status["pain_points_by_status"] = {}

        # Specs by status
        try:
            rows = conn.execute(
                "SELECT status, COUNT(*) as cnt FROM creative_specs GROUP BY status"
            ).fetchall()
            status["specs_by_status"] = {r["status"]: r["cnt"] for r in rows}
        except sqlite3.OperationalError:
            status["specs_by_status"] = {}

        # Trends by status
        try:
            rows = conn.execute(
                "SELECT status, COUNT(*) as cnt FROM creative_trends GROUP BY status"
            ).fetchall()
            status["trends_by_status"] = {r["status"]: r["cnt"] for r in rows}
        except sqlite3.OperationalError:
            status["trends_by_status"] = {}

        # Signals last 24h
        try:
            cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM creative_signals WHERE discovered_at >= ?",
                (cutoff,),
            ).fetchone()
            status["signals_last_24h"] = row["cnt"] if row else 0
        except sqlite3.OperationalError:
            status["signals_last_24h"] = 0

        # Top sources
        try:
            rows = conn.execute(
                """SELECT source, COUNT(*) as cnt
                   FROM creative_signals
                   GROUP BY source ORDER BY cnt DESC LIMIT 10"""
            ).fetchall()
            status["top_sources"] = {r["source"]: r["cnt"] for r in rows}
        except sqlite3.OperationalError:
            status["top_sources"] = {}

        # Average composite score
        try:
            row = conn.execute(
                "SELECT AVG(composite_score) as avg_score FROM creative_pain_points WHERE composite_score IS NOT NULL"
            ).fetchone()
            status["avg_composite_score"] = round(row["avg_score"], 3) if row and row["avg_score"] else 0.0
        except sqlite3.OperationalError:
            status["avg_composite_score"] = 0.0

        if status.get("missing_tables"):
            status["note"] = "Run: python tools/db/init_icdev_db.py to create missing tables"

        return status

    finally:
        conn.close()


def get_competitors(status_filter=None, db_path=None):
    """List tracked competitors.

    Args:
        status_filter: Filter by status (discovered/confirmed/archived).
        db_path: Optional DB path override.

    Returns:
        Dict with competitor list.
    """
    fn = _try_import("tools.creative.competitor_discoverer", "get_competitors")
    if fn:
        try:
            return fn(status=status_filter, db_path=db_path)
        except Exception as e:
            return {"error": str(e)}
    return {"error": "competitor_discoverer not available"}


def get_trends(db_path=None):
    """Get trending pain point clusters.

    Args:
        db_path: Optional DB path override.

    Returns:
        Dict with trend report.
    """
    fn = _try_import("tools.creative.trend_tracker", "get_trend_report")
    if fn:
        try:
            return fn(db_path=db_path)
        except Exception as e:
            return {"error": str(e)}
    return {"error": "trend_tracker not available"}


def get_specs(status_filter=None, limit=20, db_path=None):
    """List generated feature specs.

    Args:
        status_filter: Filter by status (generated/reviewed/approved/building/rejected).
        limit: Max specs to return.
        db_path: Optional DB path override.

    Returns:
        Dict with spec list.
    """
    fn = _try_import("tools.creative.spec_generator", "list_specs")
    if fn:
        try:
            return fn(status=status_filter, limit=limit, db_path=db_path)
        except Exception as e:
            return {"error": str(e)}
    return {"error": "spec_generator not available"}


def get_pipeline_report(db_path=None):
    """Generate comprehensive pipeline health report.

    Returns:
        Dict with pipeline health, throughput, and recommendations.
    """
    status = get_status(db_path=db_path)

    pp_by_status = status.get("pain_points_by_status", {})
    total_pp = status.get("total_pain_points", 0)

    report = {
        "timestamp": _now(),
        "pipeline_health": status.get("healthy", False),
        "total_signals": status.get("total_signals", 0),
        "total_pain_points": total_pp,
        "total_feature_gaps": status.get("total_feature_gaps", 0),
        "total_specs": status.get("total_specs", 0),
        "total_trends": status.get("total_trends", 0),
        "avg_composite_score": status.get("avg_composite_score", 0.0),
        "competitors_by_status": status.get("competitors_by_status", {}),
        "pain_points_by_status": pp_by_status,
        "specs_by_status": status.get("specs_by_status", {}),
        "trends_by_status": status.get("trends_by_status", {}),
        "top_sources": status.get("top_sources", {}),
        "pipeline_throughput": {
            "pending_extraction": status.get("total_signals", 0),
            "pending_scoring": pp_by_status.get("new", 0),
            "pending_generation": pp_by_status.get("scored", 0),
        },
        "recommendations": [],
    }

    # Generate recommendations
    new_pp = pp_by_status.get("new", 0)
    scored_pp = pp_by_status.get("scored", 0)

    if status.get("total_competitors", 0) == 0:
        report["recommendations"].append(
            "No competitors tracked — run: --discover --domain '<your domain>'"
        )
    elif status.get("competitors_by_status", {}).get("confirmed", 0) == 0:
        report["recommendations"].append(
            "No confirmed competitors — run competitor_discoverer.py --confirm <id>"
        )
    if status.get("total_signals", 0) == 0:
        report["recommendations"].append(
            "No signals collected — run: --scan --all"
        )
    if new_pp > 50:
        report["recommendations"].append(
            f"{new_pp} unscored pain points — run: --score"
        )
    if scored_pp > 20:
        report["recommendations"].append(
            f"{scored_pp} scored pain points pending spec generation — run: --generate"
        )
    if not status.get("healthy"):
        report["recommendations"].append(
            "Database tables missing — run: python tools/db/init_icdev_db.py"
        )

    return report


# =========================================================================
# DAEMON MODE
# =========================================================================
def run_daemon(db_path=None):
    """Run creative engine as a continuous daemon.

    Respects scan intervals and quiet hours from config (D359).
    Exits gracefully on KeyboardInterrupt.

    Args:
        db_path: Optional DB path override.
    """
    config = _load_config()
    sched = config.get("scheduling", {})
    default_interval = sched.get("default_scan_interval_hours", 12)
    interval_seconds = default_interval * 3600

    domain = config.get("domain", {}).get("name")

    print(f"Creative Engine daemon started (interval: {default_interval}h)")
    _audit("creative.daemon.start", "Daemon started")

    try:
        while True:
            if _in_quiet_hours(config):
                print(f"[{_now()}] In quiet hours — skipping pipeline run")
                time.sleep(600)  # Re-check every 10 minutes during quiet hours
                continue

            print(f"\n[{_now()}] Running pipeline...")
            try:
                result = run_full_pipeline(domain=domain, db_path=db_path)
                stage_count = len(result.get("stages", {}))
                print(f"[{_now()}] Pipeline complete. Stages: {stage_count}")
            except Exception as e:
                print(f"[{_now()}] Pipeline error: {e}", file=sys.stderr)
                _audit("creative.daemon.error", f"Pipeline error: {e}")

            print(f"[{_now()}] Next run in {default_interval} hours...")
            time.sleep(interval_seconds)

    except KeyboardInterrupt:
        print(f"\n[{_now()}] Daemon stopped by user")
        _audit("creative.daemon.stop", "Daemon stopped by user")


# =========================================================================
# CLI
# =========================================================================
def main():
    parser = argparse.ArgumentParser(
        description="ICDEV Creative Engine — customer-centric feature opportunity discovery"
    )
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--human", action="store_true", help="Human-readable output")
    parser.add_argument("--db-path", type=Path, default=None, help="Database path override")

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--run", action="store_true", help="Run full pipeline (one-shot)")
    group.add_argument("--discover", action="store_true", help="Discover competitors")
    group.add_argument("--scan", action="store_true", help="Scan sources for signals")
    group.add_argument("--extract", action="store_true", help="Extract pain points from signals")
    group.add_argument("--score", action="store_true", help="Score pain points + identify gaps")
    group.add_argument("--rank", action="store_true", help="Rank top pain points + detect trends")
    group.add_argument("--generate", action="store_true", help="Generate feature specs")
    group.add_argument("--status", action="store_true", help="Show engine status")
    group.add_argument("--pipeline-report", action="store_true", help="Full pipeline report")
    group.add_argument("--competitors", action="store_true", help="List tracked competitors")
    group.add_argument("--trends", action="store_true", help="Show trending pain points")
    group.add_argument("--specs", action="store_true", help="List generated specs")
    group.add_argument("--daemon", action="store_true", help="Run as continuous daemon")

    # Optional flags
    parser.add_argument("--domain", type=str, default=None,
                        help="Product domain for competitor discovery")
    parser.add_argument("--source", type=str, default=None,
                        help="Specific source to scan (g2, capterra, reddit, etc.)")
    parser.add_argument("--all", action="store_true", dest="scan_all",
                        help="Scan all configured sources")
    parser.add_argument("--top-k", type=int, default=20,
                        help="Number of top items to return (default: 20)")
    parser.add_argument("--spec-status", type=str, default=None,
                        help="Filter specs by status")

    args = parser.parse_args()

    try:
        if args.run:
            result = run_full_pipeline(domain=args.domain, db_path=args.db_path)
        elif args.discover:
            result = stage_discover(domain=args.domain, db_path=args.db_path)
        elif args.scan:
            source = None if args.scan_all else args.source
            result = stage_scan(source=source, db_path=args.db_path)
        elif args.extract:
            result = stage_extract(db_path=args.db_path)
        elif args.score:
            result = stage_score(db_path=args.db_path)
        elif args.rank:
            result = stage_rank(top_k=args.top_k, db_path=args.db_path)
        elif args.generate:
            result = stage_generate(db_path=args.db_path)
        elif args.status:
            result = get_status(db_path=args.db_path)
        elif args.pipeline_report:
            result = get_pipeline_report(db_path=args.db_path)
        elif args.competitors:
            result = get_competitors(db_path=args.db_path)
        elif args.trends:
            result = get_trends(db_path=args.db_path)
        elif args.specs:
            result = get_specs(status_filter=args.spec_status, db_path=args.db_path)
        elif args.daemon:
            run_daemon(db_path=args.db_path)
            return
        else:
            result = {"error": "No action specified"}

        # Output formatting
        if args.json:
            print(json.dumps(result, indent=2, default=str))
        elif args.human or not args.json:
            _print_human(args, result)

    except Exception as e:
        error = {"error": str(e)}
        if args.json:
            print(json.dumps(error, indent=2))
        else:
            print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


def _print_human(args, result):
    """Format result as human-readable terminal output."""
    if args.status:
        print("Creative Engine Status")
        print("=" * 50)
        print(f"  Healthy: {result.get('healthy', False)}")
        print(f"  Timestamp: {result.get('timestamp', '')}")
        print()
        print("  Table Counts:")
        for key in ["competitors", "signals", "pain_points", "feature_gaps", "specs", "trends"]:
            print(f"    {key:20s} {result.get(f'total_{key}', 0)}")
        print(f"\n  Avg Composite Score: {result.get('avg_composite_score', 0.0):.3f}")
        print(f"  Signals (last 24h):  {result.get('signals_last_24h', 0)}")

        comp = result.get("competitors_by_status", {})
        if comp:
            print("\n  Competitors by Status:")
            for s, c in comp.items():
                print(f"    {s:15s} {c}")

        pp = result.get("pain_points_by_status", {})
        if pp:
            print("\n  Pain Points by Status:")
            for s, c in pp.items():
                print(f"    {s:15s} {c}")

        specs = result.get("specs_by_status", {})
        if specs:
            print("\n  Specs by Status:")
            for s, c in specs.items():
                print(f"    {s:15s} {c}")

        trends = result.get("trends_by_status", {})
        if trends:
            print("\n  Trends by Status:")
            for s, c in trends.items():
                print(f"    {s:15s} {c}")

        top = result.get("top_sources", {})
        if top:
            print("\n  Top Sources:")
            for s, c in top.items():
                print(f"    {s:15s} {c}")

        if result.get("missing_tables"):
            print(f"\n  WARNING: Missing tables: {', '.join(result['missing_tables'])}")
            print(f"  {result.get('note', '')}")

    elif args.pipeline_report:
        print("Creative Engine Pipeline Report")
        print("=" * 50)
        health = "OK" if result.get("pipeline_health") else "DEGRADED"
        print(f"  Health:          {health}")
        print(f"  Total Signals:   {result.get('total_signals', 0)}")
        print(f"  Pain Points:     {result.get('total_pain_points', 0)}")
        print(f"  Feature Gaps:    {result.get('total_feature_gaps', 0)}")
        print(f"  Specs Generated: {result.get('total_specs', 0)}")
        print(f"  Active Trends:   {result.get('total_trends', 0)}")
        print(f"  Avg Score:       {result.get('avg_composite_score', 0.0):.3f}")

        tp = result.get("pipeline_throughput", {})
        print(f"\n  Pipeline Backlog:")
        print(f"    Pending extraction:  {tp.get('pending_extraction', 0)}")
        print(f"    Pending scoring:     {tp.get('pending_scoring', 0)}")
        print(f"    Pending generation:  {tp.get('pending_generation', 0)}")

        recs = result.get("recommendations", [])
        if recs:
            print(f"\n  Recommendations:")
            for r in recs:
                print(f"    - {r}")

    elif hasattr(args, "competitors") and args.competitors:
        items = result.get("competitors", [])
        print(f"Tracked Competitors ({len(items)})")
        print("=" * 50)
        for c in items:
            status = c.get("status", "unknown")
            name = c.get("name", "")
            rating = c.get("rating", "N/A")
            reviews = c.get("review_count", 0)
            print(f"  [{status:10s}] {name:30s} rating={rating}  reviews={reviews}")

    elif hasattr(args, "trends") and args.trends:
        items = result.get("trends", [])
        print(f"Trending Pain Point Clusters ({len(items)})")
        print("=" * 50)
        for t in items:
            name = t.get("name", "")
            status = t.get("status", "")
            velocity = t.get("velocity", 0.0)
            signals = t.get("signal_count", 0)
            print(f"  [{status:10s}] {name:30s} v={velocity:.2f}  signals={signals}")

    elif hasattr(args, "specs") and args.specs:
        items = result.get("specs", [])
        print(f"Generated Feature Specs ({len(items)})")
        print("=" * 50)
        for s in items:
            title = s.get("title", "")
            score = s.get("composite_score", 0.0)
            effort = s.get("estimated_effort", "?")
            status = s.get("status", "")
            print(f"  [{status:10s}] {title:40s} score={score:.2f}  effort={effort}")

    else:
        # Generic pipeline stage output
        stages = result.get("stages", {})
        if stages:
            print(f"Pipeline completed at {result.get('completed_at', '')}")
            if result.get("quiet_hours"):
                print("  (spec generation skipped — quiet hours)")
            for stage_name, stage_result in stages.items():
                if isinstance(stage_result, dict):
                    err = stage_result.get("error")
                    if err:
                        print(f"  {stage_name:15s} ERROR — {err}")
                    elif stage_result.get("skipped"):
                        print(f"  {stage_name:15s} SKIPPED — {stage_result['skipped']}")
                    else:
                        print(f"  {stage_name:15s} OK")
                else:
                    print(f"  {stage_name:15s} {stage_result}")
        else:
            print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
