# CUI // SP-CTI
# ICDEV GovCon Intelligence Engine — Phase 59 (Sub-Phase 59E)
# Pipeline orchestrator: DISCOVER → EXTRACT → MAP → DRAFT

"""
GovCon Intelligence Engine — unified orchestrator for the capture-to-delivery flywheel.

Pipeline stages:
    1. DISCOVER  — Scan SAM.gov for new opportunities + award notices
    2. EXTRACT   — Mine "shall/must/will" requirements from opportunity descriptions
    3. MAP       — Match requirements to ICDEV capability catalog, identify gaps
    4. DRAFT     — Auto-draft responses via two-tier LLM (qwen3 → Claude review)

Daemon mode runs on schedule with quiet hours.

Usage:
    python tools/govcon/govcon_engine.py --run --json
    python tools/govcon/govcon_engine.py --status --json
    python tools/govcon/govcon_engine.py --pipeline-report --json
    python tools/govcon/govcon_engine.py --stage discover --json
    python tools/govcon/govcon_engine.py --stage extract --json
    python tools/govcon/govcon_engine.py --stage map --json
    python tools/govcon/govcon_engine.py --stage draft --json
    python tools/govcon/govcon_engine.py --daemon --json
"""

import argparse
import json
import os
import sqlite3
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
_DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(_ROOT / "data" / "icdev.db")))
_CONFIG_PATH = _ROOT / "args" / "govcon_config.yaml"

STAGES = ["discover", "extract", "map", "draft"]


# ── helpers ───────────────────────────────────────────────────────────

def _get_db():
    conn = sqlite3.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _now():
    return datetime.now(timezone.utc).isoformat()


def _audit(conn, action, details="", actor="govcon_engine"):
    try:
        conn.execute(
            "INSERT INTO audit_trail (id, timestamp, event_type, actor, action, details, session_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), _now(), "govcon.pipeline", actor, action, details, "govcon"),
        )
    except Exception:
        pass


def _load_config():
    """Load govcon_config.yaml with graceful fallback."""
    try:
        import yaml
        with open(_CONFIG_PATH, "r") as f:
            return yaml.safe_load(f)
    except Exception:
        return {}


def _in_quiet_hours(config):
    """Check if current time is within quiet hours."""
    sched = config.get("scheduling", {})
    qh = sched.get("quiet_hours", {})
    if not qh:
        return False
    try:
        start = qh.get("start", "02:00")
        end = qh.get("end", "06:00")
        now = datetime.now(timezone.utc).strftime("%H:%M")
        if start <= end:
            return start <= now <= end
        return now >= start or now <= end
    except Exception:
        return False


# ── pipeline stages ──────────────────────────────────────────────────

def stage_discover(config):
    """Stage 1: Scan SAM.gov for opportunities and awards."""
    results = {"stage": "discover", "opportunities": 0, "awards": 0, "errors": []}

    # Scan opportunities
    try:
        from tools.govcon.sam_scanner import scan_opportunities
        opp_result = scan_opportunities()
        results["opportunities"] = opp_result.get("new_opportunities", 0)
        results["opp_details"] = {
            "total_cached": opp_result.get("total_cached", 0),
            "new": opp_result.get("new_opportunities", 0),
        }
    except Exception as e:
        results["errors"].append(f"opportunity_scan: {str(e)}")

    # Scan award notices
    try:
        from tools.govcon.award_tracker import scan_awards
        award_result = scan_awards()
        results["awards"] = award_result.get("new_awards", 0)
        results["award_details"] = {
            "new": award_result.get("new_awards", 0),
            "competitors_created": award_result.get("competitors_created", 0),
        }
    except Exception as e:
        results["errors"].append(f"award_scan: {str(e)}")

    results["status"] = "ok" if not results["errors"] else "partial"
    return results


def stage_extract(config):
    """Stage 2: Extract shall/must/will requirements from opportunities."""
    results = {"stage": "extract", "extracted": 0, "patterns": 0, "errors": []}

    try:
        from tools.govcon.requirement_extractor import extract_all_requirements
        ext_result = extract_all_requirements()
        results["extracted"] = ext_result.get("total_extracted", 0)
        results["by_domain"] = ext_result.get("by_domain", {})
    except Exception as e:
        results["errors"].append(f"extraction: {str(e)}")

    # Run clustering
    try:
        from tools.govcon.requirement_extractor import cluster_patterns
        pat_result = cluster_patterns()
        results["patterns"] = pat_result.get("total_patterns", 0)
    except Exception as e:
        results["errors"].append(f"clustering: {str(e)}")

    results["status"] = "ok" if not results["errors"] else "partial"
    return results


def stage_map(config):
    """Stage 3: Map requirements to ICDEV capabilities, analyze gaps."""
    results = {"stage": "map", "mapped": 0, "gaps": 0, "errors": []}

    # Map capabilities
    try:
        from tools.govcon.capability_mapper import map_all_requirements
        map_result = map_all_requirements()
        results["mapped"] = map_result.get("total_mapped", 0)
        results["coverage"] = {
            "L_compliant": map_result.get("L_compliant", 0),
            "M_partial": map_result.get("M_partial", 0),
            "N_gap": map_result.get("N_gap", 0),
        }
    except Exception as e:
        results["errors"].append(f"capability_mapping: {str(e)}")

    # Analyze gaps
    try:
        from tools.govcon.gap_analyzer import analyze_gaps
        gap_result = analyze_gaps()
        results["gaps"] = gap_result.get("total_gaps", 0)
        results["gap_details"] = {
            "critical_gaps": gap_result.get("critical_gaps", 0),
            "recommendations": gap_result.get("total_recommendations", 0),
        }
    except Exception as e:
        results["errors"].append(f"gap_analysis: {str(e)}")

    # Cross-register gaps to Innovation Engine
    try:
        from tools.govcon.gap_analyzer import register_gaps_as_innovation_signals
        cross_result = register_gaps_as_innovation_signals()
        results["innovation_signals_registered"] = cross_result.get("registered", 0)
    except Exception as e:
        results["errors"].append(f"cross_registration: {str(e)}")

    results["status"] = "ok" if not results["errors"] else "partial"
    return results


def stage_draft(config):
    """Stage 4: Auto-draft responses using two-tier LLM pipeline."""
    results = {"stage": "draft", "drafted": 0, "compliance_populated": 0, "errors": []}

    conn = _get_db()
    try:
        # Find opportunities with extracted requirements but no drafts
        opps = conn.execute(
            """SELECT DISTINCT s.sam_opportunity_id as opp_id
               FROM rfp_shall_statements s
               LEFT JOIN proposal_section_drafts d ON d.opportunity_id = s.sam_opportunity_id
               WHERE d.id IS NULL AND s.sam_opportunity_id IS NOT NULL
               LIMIT 5"""
        ).fetchall()
    except Exception:
        opps = []
    finally:
        conn.close()

    for opp_row in opps:
        opp_id = opp_row["opp_id"]

        # Draft responses
        try:
            from tools.govcon.response_drafter import draft_all_for_opportunity
            draft_result = draft_all_for_opportunity(opp_id)
            results["drafted"] += draft_result.get("drafts_created", 0)
        except Exception as e:
            results["errors"].append(f"drafting[{opp_id}]: {str(e)}")

        # Auto-populate compliance matrix
        try:
            from tools.govcon.compliance_populator import populate_compliance_matrix
            comp_result = populate_compliance_matrix(opp_id)
            results["compliance_populated"] += comp_result.get("populated_items", 0)
        except Exception as e:
            results["errors"].append(f"compliance[{opp_id}]: {str(e)}")

    results["opportunities_processed"] = len(opps)
    results["status"] = "ok" if not results["errors"] else "partial"
    return results


# ── pipeline orchestration ───────────────────────────────────────────

def run_pipeline(stages=None):
    """Execute the full GovCon pipeline or specific stages."""
    config = _load_config()
    stages = stages or STAGES

    pipeline_id = str(uuid.uuid4())[:8]
    started = _now()
    results = {
        "status": "ok",
        "pipeline_id": pipeline_id,
        "started": started,
        "stages": {},
        "summary": {},
    }

    conn = _get_db()
    _audit(conn, "pipeline_start", f"Pipeline {pipeline_id}: stages={stages}")
    conn.commit()
    conn.close()

    stage_funcs = {
        "discover": stage_discover,
        "extract": stage_extract,
        "map": stage_map,
        "draft": stage_draft,
    }

    total_errors = []

    for stage_name in stages:
        if stage_name not in stage_funcs:
            continue
        try:
            stage_result = stage_funcs[stage_name](config)
            results["stages"][stage_name] = stage_result
            if stage_result.get("errors"):
                total_errors.extend(stage_result["errors"])
        except Exception as e:
            results["stages"][stage_name] = {"status": "error", "error": str(e)}
            total_errors.append(f"{stage_name}: {str(e)}")

    results["completed"] = _now()
    results["total_errors"] = len(total_errors)

    if total_errors:
        results["status"] = "partial"
        results["errors"] = total_errors

    # Summary
    discover = results["stages"].get("discover", {})
    extract = results["stages"].get("extract", {})
    mapping = results["stages"].get("map", {})
    draft = results["stages"].get("draft", {})

    results["summary"] = {
        "new_opportunities": discover.get("opportunities", 0),
        "new_awards": discover.get("awards", 0),
        "requirements_extracted": extract.get("extracted", 0),
        "patterns_found": extract.get("patterns", 0),
        "capabilities_mapped": mapping.get("mapped", 0),
        "gaps_identified": mapping.get("gaps", 0),
        "drafts_created": draft.get("drafted", 0),
        "compliance_items": draft.get("compliance_populated", 0),
    }

    conn = _get_db()
    _audit(conn, "pipeline_complete",
           f"Pipeline {pipeline_id}: opps={discover.get('opportunities', 0)} "
           f"reqs={extract.get('extracted', 0)} gaps={mapping.get('gaps', 0)} "
           f"drafts={draft.get('drafted', 0)} errors={len(total_errors)}")
    conn.commit()
    conn.close()

    return results


def get_status():
    """Get pipeline health stats."""
    conn = _get_db()
    try:
        stats = {}

        # Opportunity count
        try:
            r = conn.execute("SELECT COUNT(*) as cnt FROM sam_gov_opportunities").fetchone()
            stats["total_opportunities"] = r["cnt"] if r else 0
        except Exception:
            stats["total_opportunities"] = 0

        # Requirement count
        try:
            r = conn.execute("SELECT COUNT(*) as cnt FROM rfp_shall_statements").fetchone()
            stats["total_requirements"] = r["cnt"] if r else 0
        except Exception:
            stats["total_requirements"] = 0

        # Pattern count
        try:
            r = conn.execute("SELECT COUNT(*) as cnt FROM rfp_requirement_patterns").fetchone()
            stats["total_patterns"] = r["cnt"] if r else 0
        except Exception:
            stats["total_patterns"] = 0

        # Capability map count
        try:
            r = conn.execute("SELECT COUNT(*) as cnt FROM icdev_capability_map").fetchone()
            stats["total_capability_maps"] = r["cnt"] if r else 0
        except Exception:
            stats["total_capability_maps"] = 0

        # Draft count
        try:
            r = conn.execute("SELECT COUNT(*) as cnt FROM proposal_section_drafts").fetchone()
            stats["total_drafts"] = r["cnt"] if r else 0
            r2 = conn.execute("SELECT COUNT(*) as cnt FROM proposal_section_drafts WHERE status='approved'").fetchone()
            stats["approved_drafts"] = r2["cnt"] if r2 else 0
        except Exception:
            stats["total_drafts"] = 0
            stats["approved_drafts"] = 0

        # Award count
        try:
            r = conn.execute("SELECT COUNT(*) as cnt FROM govcon_awards").fetchone()
            stats["total_awards"] = r["cnt"] if r else 0
        except Exception:
            stats["total_awards"] = 0

        # KB count
        try:
            r = conn.execute("SELECT COUNT(*) as cnt FROM proposal_knowledge_base").fetchone()
            stats["knowledge_blocks"] = r["cnt"] if r else 0
        except Exception:
            stats["knowledge_blocks"] = 0

        # Proposal linkage
        try:
            r = conn.execute(
                "SELECT COUNT(*) as cnt FROM proposal_opportunities WHERE sam_gov_opportunity_id IS NOT NULL"
            ).fetchone()
            stats["linked_proposals"] = r["cnt"] if r else 0
        except Exception:
            stats["linked_proposals"] = 0

        # Last pipeline run (from audit trail)
        try:
            r = conn.execute(
                "SELECT timestamp FROM audit_trail WHERE event_type='govcon.pipeline' "
                "AND action='pipeline_complete' ORDER BY timestamp DESC LIMIT 1"
            ).fetchone()
            stats["last_pipeline_run"] = r["timestamp"] if r else None
        except Exception:
            stats["last_pipeline_run"] = None

        # Domain distribution
        try:
            rows = conn.execute(
                "SELECT domain_category, COUNT(*) as cnt FROM rfp_shall_statements "
                "GROUP BY domain_category ORDER BY cnt DESC"
            ).fetchall()
            stats["domain_distribution"] = {r["domain_category"]: r["cnt"] for r in rows}
        except Exception:
            stats["domain_distribution"] = {}

        return {"status": "ok", **stats}
    finally:
        conn.close()


def get_pipeline_report():
    """Detailed pipeline report with trends."""
    status = get_status()
    config = _load_config()

    conn = _get_db()
    try:
        # Recent pipeline runs
        try:
            runs = conn.execute(
                "SELECT timestamp, details FROM audit_trail "
                "WHERE event_type='govcon.pipeline' AND action='pipeline_complete' "
                "ORDER BY timestamp DESC LIMIT 10"
            ).fetchall()
            status["recent_runs"] = [{"timestamp": r["timestamp"], "details": r["details"]} for r in runs]
        except Exception:
            status["recent_runs"] = []

        # Top requirement patterns
        try:
            patterns = conn.execute(
                "SELECT pattern_name, domain_category, frequency, capability_coverage, status "
                "FROM rfp_requirement_patterns ORDER BY frequency DESC LIMIT 10"
            ).fetchall()
            status["top_patterns"] = [dict(p) for p in patterns]
        except Exception:
            status["top_patterns"] = []

        # Coverage by domain
        try:
            domains = conn.execute(
                """SELECT s.domain_category,
                          COUNT(*) as total,
                          SUM(CASE WHEN m.coverage_score >= 0.80 THEN 1 ELSE 0 END) as L,
                          SUM(CASE WHEN m.coverage_score >= 0.40 AND m.coverage_score < 0.80 THEN 1 ELSE 0 END) as M,
                          SUM(CASE WHEN m.coverage_score < 0.40 OR m.coverage_score IS NULL THEN 1 ELSE 0 END) as N
                   FROM rfp_shall_statements s
                   LEFT JOIN icdev_capability_map m ON s.id = m.pattern_id
                   GROUP BY s.domain_category"""
            ).fetchall()
            status["coverage_by_domain"] = [dict(d) for d in domains]
        except Exception:
            status["coverage_by_domain"] = []

        # Config info
        status["config"] = {
            "naics_codes": config.get("sam_gov", {}).get("naics_codes", []),
            "poll_interval_hours": config.get("scheduling", {}).get("opportunity_scan_interval_hours", 6),
            "daemon_mode": config.get("scheduling", {}).get("daemon_mode", False),
        }

        return status
    finally:
        conn.close()


# ── daemon mode ──────────────────────────────────────────────────────

def run_daemon():
    """Run as daemon with scheduled scans and quiet hours."""
    config = _load_config()
    sched = config.get("scheduling", {})
    opp_interval = sched.get("opportunity_scan_interval_hours", 6) * 3600
    last_run = 0

    print(f"[govcon_engine] Daemon started at {_now()}")
    print(f"[govcon_engine] Opportunity scan interval: {opp_interval // 3600}h")
    print(f"[govcon_engine] Quiet hours: {sched.get('quiet_hours', {})}")

    while True:
        now = time.time()
        if now - last_run >= opp_interval:
            if _in_quiet_hours(config):
                print(f"[govcon_engine] {_now()} — Skipping (quiet hours)")
            else:
                print(f"[govcon_engine] {_now()} — Running pipeline...")
                try:
                    result = run_pipeline()
                    summary = result.get("summary", {})
                    print(f"[govcon_engine] Pipeline complete: "
                          f"opps={summary.get('new_opportunities', 0)} "
                          f"reqs={summary.get('requirements_extracted', 0)} "
                          f"gaps={summary.get('gaps_identified', 0)} "
                          f"drafts={summary.get('drafts_created', 0)}")
                except Exception as e:
                    print(f"[govcon_engine] Pipeline error: {e}")
            last_run = now
        time.sleep(60)  # Check every minute


# ── CLI ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="ICDEV GovCon Intelligence Engine (Phase 59E)")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--run", action="store_true", help="Run full pipeline")
    group.add_argument("--stage", choices=STAGES, help="Run a specific stage")
    group.add_argument("--status", action="store_true", help="Pipeline health stats")
    group.add_argument("--pipeline-report", action="store_true", help="Detailed pipeline report")
    group.add_argument("--daemon", action="store_true", help="Run as daemon with scheduling")

    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--human", action="store_true", help="Human-readable output")

    args = parser.parse_args()

    if args.run:
        result = run_pipeline()
    elif args.stage:
        result = run_pipeline(stages=[args.stage])
    elif args.status:
        result = get_status()
    elif args.pipeline_report:
        result = get_pipeline_report()
    elif args.daemon:
        run_daemon()
        return

    if args.human:
        print(f"\n{'=' * 60}")
        print("  ICDEV GovCon Intelligence Engine")
        print(f"{'=' * 60}")
        if "summary" in result:
            s = result["summary"]
            print(f"  New Opportunities:     {s.get('new_opportunities', 0)}")
            print(f"  New Awards:            {s.get('new_awards', 0)}")
            print(f"  Requirements Extracted: {s.get('requirements_extracted', 0)}")
            print(f"  Patterns Found:        {s.get('patterns_found', 0)}")
            print(f"  Capabilities Mapped:   {s.get('capabilities_mapped', 0)}")
            print(f"  Gaps Identified:       {s.get('gaps_identified', 0)}")
            print(f"  Drafts Created:        {s.get('drafts_created', 0)}")
            print(f"  Compliance Items:      {s.get('compliance_items', 0)}")
        elif "total_opportunities" in result:
            print(f"  Opportunities:  {result.get('total_opportunities', 0)}")
            print(f"  Requirements:   {result.get('total_requirements', 0)}")
            print(f"  Patterns:       {result.get('total_patterns', 0)}")
            print(f"  Drafts:         {result.get('total_drafts', 0)} ({result.get('approved_drafts', 0)} approved)")
            print(f"  Awards:         {result.get('total_awards', 0)}")
            print(f"  KB Blocks:      {result.get('knowledge_blocks', 0)}")
            print(f"  Linked Proposals: {result.get('linked_proposals', 0)}")
            if result.get("domain_distribution"):
                print(f"\n  Domain Distribution:")
                for domain, cnt in result["domain_distribution"].items():
                    print(f"    {domain:20s} {cnt}")
        if result.get("total_errors", 0) > 0:
            print(f"\n  Errors ({result['total_errors']}):")
            for err in result.get("errors", []):
                print(f"    - {err}")
        print()
    else:
        print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
