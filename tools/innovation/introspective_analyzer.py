#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""Introspective Analyzer — Internal Telemetry Mining for ICDEV self-improvement.

Mines ICDEV's own internal telemetry (audit trail, self-healing history, NLQ queries,
knowledge base, pipeline events) to discover self-improvement opportunities. Looks
INWARD rather than outward — produces innovation signals with source='introspective'
that flow through the same scoring -> triage -> solution pipeline as web signals.

7 Introspective analyses:
    1. Failed Self-Heals    — Problems ICDEV can't solve yet (confidence < 0.3)
    2. Gate Failure Frequency — Gates that fail >= N times in last 30 days
    3. Unused Tools          — Tools with 0 invocations in last 90 days
    4. Slow Pipeline Stages  — Build/test/deploy stages exceeding time threshold
    5. NLQ Gaps              — Questions that returned 0 results (knowledge gaps)
    6. Knowledge Gaps        — Self-heal patterns with no resolution
    7. CLI Harmonization     — CLI pattern drift (--json, --project-id, db_utils)

Usage:
    python tools/innovation/introspective_analyzer.py --analyze --all --json
    python tools/innovation/introspective_analyzer.py --analyze --type gate_failures --json
    python tools/innovation/introspective_analyzer.py --analyze --type unused_tools --json
    python tools/innovation/introspective_analyzer.py --analyze --type slow_pipelines --json
    python tools/innovation/introspective_analyzer.py --analyze --type nlq_gaps --min-occurrences 3 --json
    python tools/innovation/introspective_analyzer.py --analyze --type failed_self_heals --json
    python tools/innovation/introspective_analyzer.py --analyze --type knowledge_gaps --json
"""

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ---- PATH SETUP ----
BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(BASE_DIR / "data" / "icdev.db")))

# ---- GRACEFUL IMPORTS ----
try:
    import yaml  # noqa: F401
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

# ---- CONSTANTS ----
ANALYSIS_TYPES = [
    "failed_self_heals", "gate_failures", "unused_tools",
    "slow_pipelines", "nlq_gaps", "knowledge_gaps", "cli_harmonization",
    "code_quality",
]
DEFAULT_GATE_FAILURE_MIN = 3
DEFAULT_GATE_FAILURE_DAYS = 30
DEFAULT_UNUSED_TOOLS_DAYS = 90
DEFAULT_SLOW_THRESHOLD_SEC = 300
DEFAULT_NLQ_MIN_OCCURRENCES = 2
DEFAULT_CONFIDENCE_CEILING = 0.3

TOOL_EVENT_TYPES = [
    "code_generated", "code_reviewed", "test_written", "test_executed",
    "security_scan", "compliance_check", "ssp_generated", "poam_generated",
    "stig_checked", "sbom_generated", "deployment_initiated",
    "cssp_assessed", "fedramp_assessed", "cmmc_assessed", "oscal_generated",
    "emass_sync", "des_assessed", "legacy_analyzed", "migration_assessed",
    "intake_session_created", "boundary_assessed", "scrm_assessed",
    "cve_triaged", "simulation_created", "monte_carlo_completed",
    "coa_generated", "nlq_query_executed", "marketplace_asset_published",
    "multi_regime_assessed", "spec_quality_check",
]

STAGE_EVENTS = {
    "build": (["code_generated"], ["code_reviewed", "code_approved"]),
    "test": (["test_written", "test_executed"], ["test_passed", "test_failed"]),
    "deploy": (["deployment_initiated"], ["deployment_succeeded", "deployment_failed"]),
    "security": (["security_scan"], ["vulnerability_found", "vulnerability_resolved"]),
    "compliance": (["compliance_check"], ["ssp_generated", "poam_generated"]),
}


# ---- HELPERS ----
def _get_db(db_path=None):
    """Get database connection with dict-like row access."""
    path = Path(db_path) if db_path else DB_PATH
    if not path.exists():
        raise FileNotFoundError(f"Database not found: {path}")
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _audit(event_type, actor, action, details=None, project_id=None):
    if _HAS_AUDIT:
        try:
            audit_log_event(event_type=event_type, actor=actor, action=action,
                            details=json.dumps(details) if details else None,
                            project_id=project_id or "innovation-engine")
        except Exception:
            pass


def _table_exists(conn, name):
    return conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()[0] > 0


def _sig_id():
    return f"sig-{uuid.uuid4().hex[:12]}"


def _chash(content):
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _make_result(atype, **extra):
    return {"analysis_type": atype, "findings": [], "signal_count": 0,
            "recommendations": [], "skipped": False, **extra}


def _skip(result, reason):
    result["skipped"] = True
    result["skip_reason"] = reason
    return result


# ---- ANALYSIS #1: Failed Self-Heals ----
def analyze_failed_self_heals(db_path=None):
    """Find self-heal attempts with confidence < 0.3 or outcome=escalated/failure."""
    r = _make_result("failed_self_heals")
    try:
        conn = _get_db(db_path)
    except FileNotFoundError as e:
        return _skip(r, str(e))
    try:
        for t in ("self_healing_events", "knowledge_patterns"):
            if not _table_exists(conn, t):
                return _skip(r, f"{t} table not found")
        rows = conn.execute(
            """SELECT she.id AS eid, she.project_id, she.trigger_source, she.outcome,
                      she.created_at, kp.id AS pid, kp.pattern_type,
                      kp.description AS pdesc, kp.root_cause, kp.confidence, kp.occurrence_count
               FROM self_healing_events she
               LEFT JOIN knowledge_patterns kp ON she.pattern_id = kp.id
               WHERE she.outcome IN ('escalated','failure')
                  OR (kp.confidence IS NOT NULL AND kp.confidence < ?)
               ORDER BY she.created_at DESC LIMIT 100""",
            (DEFAULT_CONFIDENCE_CEILING,)).fetchall()
        groups = {}
        for row in rows:
            key = row["pid"] or f"u_{row['eid']}"
            g = groups.setdefault(key, {"pattern_id": row["pid"], "pattern_type": row["pattern_type"],
                "description": row["pdesc"] or "No pattern matched", "root_cause": row["root_cause"],
                "confidence": row["confidence"] or 0.0, "occurrences": 0,
                "projects": set(), "sources": set(), "last_seen": row["created_at"]})
            g["occurrences"] += 1
            if row["project_id"]: g["projects"].add(row["project_id"])
            if row["trigger_source"]: g["sources"].add(row["trigger_source"])
        for g in sorted(groups.values(), key=lambda x: x["occurrences"], reverse=True):
            r["findings"].append({
                "pattern_id": g["pattern_id"], "pattern_type": g["pattern_type"],
                "description": g["description"], "root_cause": g["root_cause"],
                "confidence": g["confidence"], "failure_count": g["occurrences"],
                "projects_affected": list(g["projects"]),
                "trigger_sources": list(g["sources"]), "last_seen": g["last_seen"]})
        if r["findings"]:
            top = r["findings"][0]
            r["recommendations"].append(
                f"Top unsolved: '{top['description']}' ({top['failure_count']}x). Build dedicated fix.")
            no_rc = [f for f in r["findings"] if not f["root_cause"]]
            if no_rc:
                r["recommendations"].append(f"{len(no_rc)} patterns lack root cause. Invest in classification.")
    except sqlite3.OperationalError as e:
        _skip(r, f"SQL: {e}")
    finally:
        conn.close()
    return r


# ---- ANALYSIS #2: Gate Failure Frequency ----
def analyze_gate_failures(db_path=None, min_failures=None, days=None):
    """Find gates failing >= min_failures times in the last N days."""
    mf = min_failures or DEFAULT_GATE_FAILURE_MIN
    d = days or DEFAULT_GATE_FAILURE_DAYS
    r = _make_result("gate_failures", params={"min_failures": mf, "days": d})
    try:
        conn = _get_db(db_path)
    except FileNotFoundError as e:
        return _skip(r, str(e))
    try:
        if not _table_exists(conn, "audit_trail"):
            return _skip(r, "audit_trail table not found")
        cutoff = (datetime.now(timezone.utc) - timedelta(days=d)).strftime("%Y-%m-%dT%H:%M:%SZ")
        gate_evts = ["test_failed", "security_scan", "vulnerability_found",
                     "code_rejected", "deployment_failed", "approval_denied"]
        ph = ",".join("?" * len(gate_evts))
        rows = conn.execute(
            f"""SELECT event_type, project_id, COUNT(*) AS cnt,
                       MIN(created_at) AS first_f, MAX(created_at) AS last_f
                FROM audit_trail WHERE event_type IN ({ph}) AND created_at >= ?
                GROUP BY event_type, project_id HAVING COUNT(*) >= ?
                ORDER BY cnt DESC LIMIT 50""",
            (*gate_evts, cutoff, mf)).fetchall()
        for row in rows:
            r["findings"].append({"gate_event_type": row["event_type"],
                "project_id": row["project_id"], "failure_count": row["cnt"],
                "first_failure": row["first_f"], "last_failure": row["last_f"]})
        if r["findings"]:
            top = r["findings"][0]
            r["recommendations"].append(
                f"Most frequent: '{top['gate_event_type']}' ({top['failure_count']}x/{d}d "
                f"in '{top['project_id']}'). Build pre-check tool or improve docs.")
            evts = {f["gate_event_type"] for f in r["findings"]}
            if "test_failed" in evts:
                r["recommendations"].append("Recurring test failures. Add scaffolding guidance.")
            if "vulnerability_found" in evts:
                r["recommendations"].append("Recurring security failures. Add pre-scan checklists.")
    except sqlite3.OperationalError as e:
        _skip(r, f"SQL: {e}")
    finally:
        conn.close()
    return r


# ---- ANALYSIS #3: Unused Tools ----
def analyze_unused_tools(db_path=None, days=None):
    """Find tools with 0 audit_trail invocations in the lookback period."""
    d = days or DEFAULT_UNUSED_TOOLS_DAYS
    r = _make_result("unused_tools", params={"days": d})
    try:
        conn = _get_db(db_path)
    except FileNotFoundError as e:
        return _skip(r, str(e))
    try:
        if not _table_exists(conn, "audit_trail"):
            return _skip(r, "audit_trail table not found")
        cutoff = (datetime.now(timezone.utc) - timedelta(days=d)).strftime("%Y-%m-%dT%H:%M:%SZ")
        used = {row["event_type"] for row in conn.execute(
            "SELECT DISTINCT event_type FROM audit_trail WHERE created_at >= ?", (cutoff,)).fetchall()}
        for evt in TOOL_EVENT_TYPES:
            if evt not in used:
                row = conn.execute(
                    "SELECT COUNT(*) AS c, MAX(created_at) AS lu FROM audit_trail WHERE event_type=?",
                    (evt,)).fetchone()
                r["findings"].append({"event_type": evt, "ever_used": row["c"] > 0,
                    "last_used": row["lu"], "total_historical_uses": row["c"]})
        never = [f for f in r["findings"] if not f["ever_used"]]
        dormant = [f for f in r["findings"] if f["ever_used"]]
        if never:
            r["recommendations"].append(
                f"{len(never)} tools NEVER used: {', '.join(f['event_type'] for f in never[:5])}. "
                f"Improve discoverability or deprecate.")
        if dormant:
            r["recommendations"].append(
                f"{len(dormant)} tools dormant {d}+d: {', '.join(f['event_type'] for f in dormant[:5])}.")
    except sqlite3.OperationalError as e:
        _skip(r, f"SQL: {e}")
    finally:
        conn.close()
    return r


# ---- ANALYSIS #4: Slow Pipeline Stages ----
def analyze_slow_pipelines(db_path=None, threshold_seconds=None):
    """Find pipeline stages exceeding a time threshold via audit event pairs."""
    th = threshold_seconds or DEFAULT_SLOW_THRESHOLD_SEC
    r = _make_result("slow_pipelines", params={"threshold_seconds": th})
    try:
        conn = _get_db(db_path)
    except FileNotFoundError as e:
        return _skip(r, str(e))
    try:
        if not _table_exists(conn, "audit_trail"):
            return _skip(r, "audit_trail table not found")
        for stage, (starts, ends) in STAGE_EVENTS.items():
            sph = ",".join("?" * len(starts))
            eph = ",".join("?" * len(ends))
            try:
                rows = conn.execute(
                    f"""SELECT s.project_id, s.event_type AS se, s.created_at AS st,
                               e.event_type AS ee, e.created_at AS et,
                               CAST((julianday(e.created_at)-julianday(s.created_at))*86400 AS INTEGER) AS dur
                        FROM audit_trail s JOIN audit_trail e
                          ON s.project_id=e.project_id AND e.created_at>s.created_at
                             AND e.event_type IN ({eph})
                        WHERE s.event_type IN ({sph})
                          AND CAST((julianday(e.created_at)-julianday(s.created_at))*86400 AS INTEGER) > ?
                        ORDER BY dur DESC LIMIT 20""",
                    (*ends, *starts, th)).fetchall()
            except sqlite3.OperationalError:
                continue
            for row in rows:
                r["findings"].append({"stage": stage, "project_id": row["project_id"],
                    "start_event": row["se"], "end_event": row["ee"],
                    "duration_seconds": row["dur"]})
        r["findings"].sort(key=lambda f: f.get("duration_seconds", 0), reverse=True)
        if r["findings"]:
            s = r["findings"][0]
            r["recommendations"].append(
                f"Slowest: '{s['stage']}' {s['duration_seconds']}s in '{s['project_id']}'. "
                f"Investigate parallelization/caching.")
    except sqlite3.OperationalError as e:
        _skip(r, f"SQL: {e}")
    finally:
        conn.close()
    return r


# ---- ANALYSIS #5: NLQ Gaps ----
def analyze_nlq_gaps(db_path=None, min_occurrences=None):
    """Find NLQ queries returning 0 results repeatedly (knowledge gaps)."""
    mo = min_occurrences or DEFAULT_NLQ_MIN_OCCURRENCES
    r = _make_result("nlq_gaps", params={"min_occurrences": mo})
    try:
        conn = _get_db(db_path)
    except FileNotFoundError as e:
        return _skip(r, str(e))
    try:
        if not _table_exists(conn, "nlq_queries"):
            return _skip(r, "nlq_queries table not found")
        rows = conn.execute(
            """SELECT LOWER(TRIM(query_text)) AS nq, COUNT(*) AS cnt,
                      SUM(CASE WHEN result_count=0 THEN 1 ELSE 0 END) AS zr,
                      SUM(CASE WHEN status='error' THEN 1 ELSE 0 END) AS ec,
                      MIN(created_at) AS fa, MAX(created_at) AS la,
                      GROUP_CONCAT(DISTINCT actor) AS actors
               FROM nlq_queries WHERE result_count=0 OR status='error'
               GROUP BY nq HAVING COUNT(*) >= ? ORDER BY cnt DESC LIMIT 50""",
            (mo,)).fetchall()
        for row in rows:
            r["findings"].append({"query": row["nq"], "occurrence_count": row["cnt"],
                "zero_result_count": row["zr"], "error_count": row["ec"],
                "first_asked": row["fa"], "last_asked": row["la"], "actors": row["actors"]})
        if r["findings"]:
            top = r["findings"][0]
            r["recommendations"].append(
                f"{len(r['findings'])} queries return 0 results. Top: '{top['query']}' "
                f"({top['occurrence_count']}x). Add data or improve SQL generation.")
        else:
            r["recommendations"].append("No recurring NLQ gaps detected.")
    except sqlite3.OperationalError as e:
        _skip(r, f"SQL: {e}")
    finally:
        conn.close()
    return r


# ---- ANALYSIS #6: Knowledge Gaps ----
def analyze_knowledge_gaps(db_path=None):
    """Find knowledge patterns with no remediation or confidence < 0.3."""
    r = _make_result("knowledge_gaps")
    try:
        conn = _get_db(db_path)
    except FileNotFoundError as e:
        return _skip(r, str(e))
    try:
        if not _table_exists(conn, "knowledge_patterns"):
            return _skip(r, "knowledge_patterns table not found")
        rows = conn.execute(
            """SELECT id, pattern_type, description, root_cause, remediation,
                      confidence, occurrence_count, auto_healable, last_occurrence
               FROM knowledge_patterns
               WHERE remediation IS NULL OR remediation='' OR remediation='{}'
                  OR confidence < ?
               ORDER BY occurrence_count DESC LIMIT 50""",
            (DEFAULT_CONFIDENCE_CEILING,)).fetchall()
        for row in rows:
            has_fix = bool(row["remediation"] and row["remediation"] not in ("", "{}", "null"))
            r["findings"].append({"pattern_id": row["id"], "pattern_type": row["pattern_type"],
                "description": row["description"], "root_cause": row["root_cause"],
                "has_remediation": has_fix, "confidence": row["confidence"],
                "occurrence_count": row["occurrence_count"],
                "auto_healable": bool(row["auto_healable"]),
                "gap_type": "no_remediation" if not has_fix else "low_confidence"})
        no_fix = [f for f in r["findings"] if not f["has_remediation"]]
        if no_fix:
            r["recommendations"].append(
                f"{len(no_fix)} patterns lack remediation. Top: '{no_fix[0]['description']}' "
                f"({no_fix[0]['occurrence_count']}x).")
        hi = [f for f in r["findings"] if f["occurrence_count"] >= 5]
        if hi:
            r["recommendations"].append(f"{len(hi)} gap patterns have 5+ occurrences — high-impact.")
        if not r["findings"]:
            r["recommendations"].append("No knowledge gaps. Self-healing coverage is healthy.")
    except sqlite3.OperationalError as e:
        _skip(r, f"SQL: {e}")
    finally:
        conn.close()
    return r


# ---- ANALYSIS #7: CLI Harmonization Drift ----
def analyze_cli_harmonization(db_path=None):
    """Detect CLI pattern drift using governance validator checks.

    Runs the 3 CLI harmonization checks from claude_dir_validator.py:
    - cli_json_flag: tools missing --json support
    - cli_project_naming: tools using --project instead of --project-id
    - db_path_centralization: tools hardcoding DB paths
    """
    r = _make_result("cli_harmonization")
    try:
        from tools.testing.claude_dir_validator import (
            check_cli_json_flag, check_cli_project_naming, check_db_path_centralization,
        )
    except ImportError:
        return _skip(r, "claude_dir_validator not importable")

    checks = {
        "cli_json_flag": check_cli_json_flag,
        "cli_project_naming": check_cli_project_naming,
        "db_path_centralization": check_db_path_centralization,
    }
    for check_name, check_fn in checks.items():
        try:
            result = check_fn()
            if result.missing:
                for item in result.missing:
                    r["findings"].append({
                        "check": check_name,
                        "file": item,
                        "message": result.message,
                        "status": result.status,
                    })
        except Exception as e:
            r["findings"].append({"check": check_name, "error": str(e)})

    if r["findings"]:
        json_missing = [f for f in r["findings"] if f.get("check") == "cli_json_flag"]
        naming_issues = [f for f in r["findings"] if f.get("check") == "cli_project_naming"]
        db_issues = [f for f in r["findings"] if f.get("check") == "db_path_centralization"]
        if json_missing:
            r["recommendations"].append(f"{len(json_missing)} tool(s) missing --json flag.")
        if naming_issues:
            r["recommendations"].append(f"{len(naming_issues)} tool(s) use --project instead of --project-id.")
        if db_issues:
            r["recommendations"].append(f"{len(db_issues)} tool(s) hardcode DB paths.")
    else:
        r["recommendations"].append("CLI harmonization is fully compliant.")
    return r


# ---- SIGNAL GENERATION ----
def _signal_title(atype, f):
    """Build human-readable signal title."""
    titles = {
        "failed_self_heals": lambda: f"[Self-Heal Gap] {f.get('description','?')[:70]} ({f.get('failure_count',0)} failures)",
        "gate_failures": lambda: f"[Gate Failure] {f.get('gate_event_type','?')} failed {f.get('failure_count',0)}x in {f.get('project_id','?')}",
        "unused_tools": lambda: f"[Unused Tool] {f.get('event_type','?')} — {'never used' if not f.get('ever_used') else 'dormant'}",
        "slow_pipelines": lambda: f"[Slow Pipeline] {f.get('stage','?')} stage took {f.get('duration_seconds',0)}s",
        "nlq_gaps": lambda: f"[NLQ Gap] '{f.get('query','?')[:50]}' — {f.get('occurrence_count',0)} unanswered",
        "knowledge_gaps": lambda: f"[Knowledge Gap] {f.get('description','?')[:50]} ({f.get('gap_type','?')})",
        "cli_harmonization": lambda: f"[CLI Drift] {f.get('check','?')}: {f.get('file','?')}",
    }
    return titles.get(atype, lambda: f"[Introspective] {atype}")()


def _signal_score(atype, f):
    """Calculate relevance score 0.0-1.0."""
    if atype == "failed_self_heals":
        return min(f.get("failure_count", 0) * 0.1 + len(f.get("projects_affected", [])) * 0.15, 1.0)
    if atype == "gate_failures":
        return min(f.get("failure_count", 0) / 20.0, 1.0)
    if atype == "unused_tools":
        return 0.6 if not f.get("ever_used") else 0.3
    if atype == "slow_pipelines":
        return min(f.get("duration_seconds", 0) / 3600.0, 1.0)
    if atype == "nlq_gaps":
        return min(f.get("occurrence_count", 0) / 10.0, 1.0)
    if atype == "knowledge_gaps":
        return min(f.get("occurrence_count", 0) * 0.1 + (0.3 if not f.get("has_remediation") else 0.0), 1.0)
    if atype == "cli_harmonization":
        # Score by check type: missing --json is medium, naming/DB is higher
        check = f.get("check", "")
        if check == "cli_json_flag":
            return 0.6
        if check == "cli_project_naming":
            return 0.7
        if check == "db_path_centralization":
            return 0.7
        return 0.5
    return 0.5


def generate_introspective_signals(analysis_results, db_path=None):
    """Convert analysis findings into innovation_signals with source='introspective'.

    Each finding becomes a signal that flows through the standard scoring/triage pipeline.

    Args:
        analysis_results: Dict mapping analysis_type -> analysis result dict.
        db_path: Optional database path override.

    Returns:
        dict with signals_generated, signals_stored, duplicates, errors.
    """
    signals = []
    for atype, analysis in analysis_results.items():
        if analysis.get("skipped"):
            continue
        recs = analysis.get("recommendations", [])
        for finding in analysis.get("findings", []):
            ckey = f"introspective_{atype}_{json.dumps(finding, sort_keys=True, default=str)}"
            desc_parts = [f"Analysis: {atype}", json.dumps(finding, indent=2, default=str)]
            if recs:
                desc_parts.append(f"Recommendation: {recs[0]}")
            signals.append({"id": _sig_id(), "source": "introspective", "source_type": atype,
                "title": _signal_title(atype, finding), "description": "\n".join(desc_parts),
                "url": "", "metadata": json.dumps(finding, default=str),
                "community_score": _signal_score(atype, finding),
                "content_hash": _chash(ckey), "discovered_at": _now()})

    stored = duplicates = errors = 0
    if not signals:
        return {"signals_generated": 0, "signals_stored": 0, "duplicates": 0, "errors": 0}
    try:
        conn = _get_db(db_path)
    except FileNotFoundError:
        return {"signals_generated": len(signals), "signals_stored": 0, "duplicates": 0,
                "errors": 1, "error_detail": "Database not found"}
    try:
        if not _table_exists(conn, "innovation_signals"):
            return {"signals_generated": len(signals), "signals_stored": 0, "duplicates": 0,
                    "errors": 0, "skipped": True, "skip_reason": "innovation_signals table not found"}
        for sig in signals:
            try:
                if conn.execute("SELECT id FROM innovation_signals WHERE content_hash=?",
                                (sig["content_hash"],)).fetchone():
                    duplicates += 1
                    continue
                conn.execute(
                    """INSERT INTO innovation_signals
                       (id, source, source_type, title, description, url,
                        metadata, community_score, content_hash, discovered_at, status, category)
                       VALUES (?,?,?,?,?,?,?,?,?,?,'new',NULL)""",
                    (sig["id"], sig["source"], sig["source_type"], sig["title"],
                     sig["description"], sig["url"], sig["metadata"],
                     sig["community_score"], sig["content_hash"], sig["discovered_at"]))
                stored += 1
            except sqlite3.Error:
                errors += 1
        conn.commit()
    finally:
        conn.close()
    _audit("innovation.scan", "introspective-analyzer",
           f"Generated {stored} introspective signals ({duplicates} dup, {errors} err)",
           {"stored": stored, "duplicates": duplicates, "errors": errors})
    return {"signals_generated": len(signals), "signals_stored": stored,
            "duplicates": duplicates, "errors": errors}


# ---- ANALYSIS #8: Code Quality (Phase 52 — D335) ----
def analyze_code_quality(db_path=None):
    """Cross-reference code_quality_metrics with runtime_feedback to find functions
    with high complexity AND low test pass rate. Generates innovation signals for
    guided refactoring."""
    r = _make_result("code_quality")
    try:
        conn = _get_db(db_path)
    except FileNotFoundError as e:
        return _skip(r, str(e))
    try:
        for t in ("code_quality_metrics", "runtime_feedback"):
            if not _table_exists(conn, t):
                return _skip(r, f"{t} table not found")

        # Find functions with high complexity (CC >= 10) that also have failing tests
        rows = conn.execute(
            """SELECT cq.function_name, cq.file_path, cq.cyclomatic_complexity,
                      cq.cognitive_complexity, cq.nesting_depth, cq.smell_count,
                      cq.maintainability_score,
                      COUNT(rf.id) AS test_total,
                      SUM(CASE WHEN rf.test_passed = 1 THEN 1 ELSE 0 END) AS test_passed,
                      AVG(rf.test_duration_ms) AS avg_duration
               FROM code_quality_metrics cq
               LEFT JOIN runtime_feedback rf
                   ON cq.function_name = rf.source_function
               WHERE cq.function_name IS NOT NULL
                 AND cq.cyclomatic_complexity >= 10
               GROUP BY cq.function_name, cq.file_path
               ORDER BY cq.cyclomatic_complexity DESC
               LIMIT 50"""
        ).fetchall()

        for row in rows:
            total = row["test_total"] or 0
            passed = row["test_passed"] or 0
            pass_rate = passed / max(total, 1)
            # Flag if complexity is high AND (no tests OR failing tests)
            if total == 0 or pass_rate < 0.8:
                r["findings"].append({
                    "function_name": row["function_name"],
                    "file_path": row["file_path"],
                    "cyclomatic_complexity": row["cyclomatic_complexity"],
                    "cognitive_complexity": row["cognitive_complexity"],
                    "nesting_depth": row["nesting_depth"],
                    "smell_count": row["smell_count"],
                    "maintainability_score": row["maintainability_score"],
                    "test_total": total,
                    "test_passed": passed,
                    "test_pass_rate": round(pass_rate, 4),
                    "avg_test_duration_ms": round(row["avg_duration"] or 0, 2),
                    "reason": "no_tests" if total == 0 else "low_pass_rate",
                })

        if r["findings"]:
            no_tests = [f for f in r["findings"] if f["reason"] == "no_tests"]
            failing = [f for f in r["findings"] if f["reason"] == "low_pass_rate"]
            if no_tests:
                r["recommendations"].append(
                    f"{len(no_tests)} complex functions have NO tests. Prioritize test coverage.")
            if failing:
                r["recommendations"].append(
                    f"{len(failing)} complex functions have failing tests. Consider refactoring.")
            r["recommendations"].append(
                "Run: python tools/analysis/code_analyzer.py --project-dir tools/ --json")
    except sqlite3.OperationalError as e:
        _skip(r, f"SQL: {e}")
    finally:
        conn.close()
    return r


# ---- ORCHESTRATOR ----
def analyze_all(db_path=None, **kw):
    """Run all 8 introspective analyses and generate signals.

    Args:
        db_path: Optional database path override.
        **kw: Threshold overrides (min_failures, days, threshold_seconds, min_occurrences).

    Returns:
        dict with per-analysis results, signal generation summary, and totals.
    """
    analyses = {
        "failed_self_heals": analyze_failed_self_heals(db_path),
        "gate_failures": analyze_gate_failures(db_path, kw.get("min_failures"), kw.get("days")),
        "unused_tools": analyze_unused_tools(db_path, kw.get("unused_tools_days")),
        "slow_pipelines": analyze_slow_pipelines(db_path, kw.get("threshold_seconds")),
        "nlq_gaps": analyze_nlq_gaps(db_path, kw.get("min_occurrences")),
        "knowledge_gaps": analyze_knowledge_gaps(db_path),
        "cli_harmonization": analyze_cli_harmonization(db_path),
        "code_quality": analyze_code_quality(db_path),
    }
    sig_result = generate_introspective_signals(analyses, db_path)
    for a in analyses.values():
        a["signal_count"] = len(a.get("findings", []))
    total_f = sum(len(a.get("findings", [])) for a in analyses.values())
    skipped = sum(1 for a in analyses.values() if a.get("skipped"))
    return {"analysis_time": _now(), "analyses": analyses, "signal_generation": sig_result,
            "totals": {"analyses_run": len(analyses) - skipped, "analyses_skipped": skipped,
                       "total_findings": total_f,
                       "signals_generated": sig_result.get("signals_generated", 0),
                       "signals_stored": sig_result.get("signals_stored", 0)}}


# ---- CLI ----
def main():
    parser = argparse.ArgumentParser(
        description="ICDEV Introspective Analyzer — internal telemetry mining for self-improvement")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--db-path", type=Path, default=None, help="Database path override")

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--analyze", action="store_true", help="Run introspective analysis")

    parser.add_argument("--all", action="store_true", help="Run all 6 analyses")
    parser.add_argument("--type", type=str, choices=ANALYSIS_TYPES, help="Specific analysis type")
    parser.add_argument("--min-failures", type=int, default=None, help="Gate failure threshold")
    parser.add_argument("--days", type=int, default=None, help="Lookback window in days")
    parser.add_argument("--threshold-seconds", type=int, default=None, help="Slow pipeline threshold")
    parser.add_argument("--min-occurrences", type=int, default=None, help="NLQ gap min occurrences")

    args = parser.parse_args()
    try:
        if args.analyze:
            if args.all or not args.type:
                result = analyze_all(db_path=args.db_path, min_failures=args.min_failures,
                    days=args.days, threshold_seconds=args.threshold_seconds,
                    min_occurrences=args.min_occurrences)
            else:
                dispatch = {
                    "failed_self_heals": lambda: analyze_failed_self_heals(args.db_path),
                    "gate_failures": lambda: analyze_gate_failures(args.db_path, args.min_failures, args.days),
                    "unused_tools": lambda: analyze_unused_tools(args.db_path, args.days),
                    "slow_pipelines": lambda: analyze_slow_pipelines(args.db_path, args.threshold_seconds),
                    "nlq_gaps": lambda: analyze_nlq_gaps(args.db_path, args.min_occurrences),
                    "knowledge_gaps": lambda: analyze_knowledge_gaps(args.db_path),
                    "cli_harmonization": lambda: analyze_cli_harmonization(args.db_path),
                    "code_quality": lambda: analyze_code_quality(args.db_path),
                }
                result = dispatch[args.type]()
                result["signal_generation"] = generate_introspective_signals(
                    {args.type: result}, args.db_path)
        else:
            result = {"error": "No action specified. Use --analyze."}

        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            _print_human(result)
    except Exception as e:
        out = {"error": str(e)}
        if args.json:
            print(json.dumps(out, indent=2))
        else:
            print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


def _print_human(result):
    """Print human-readable output."""
    if "analyses" in result:
        totals = result.get("totals", {})
        print("=" * 60)
        print("  ICDEV Introspective Analysis")
        print(f"  Time: {result.get('analysis_time', '')}")
        print(f"  Run: {totals.get('analyses_run', 0)}  Skipped: {totals.get('analyses_skipped', 0)}")
        print(f"  Findings: {totals.get('total_findings', 0)}  Signals: {totals.get('signals_stored', 0)}")
        print("=" * 60)
        for atype, analysis in result.get("analyses", {}).items():
            _print_one(atype, analysis)
    elif "analysis_type" in result:
        _print_one(result["analysis_type"], result)
        sig = result.get("signal_generation", {})
        if sig:
            print(f"\n  Signals: {sig.get('signals_generated', 0)} generated, "
                  f"{sig.get('signals_stored', 0)} stored")
    else:
        print(json.dumps(result, indent=2, default=str))


def _print_one(atype, a):
    """Print single analysis."""
    print(f"\n--- {atype.upper().replace('_', ' ')} ---")
    if a.get("skipped"):
        print(f"  SKIPPED: {a.get('skip_reason', '?')}")
        return
    findings = a.get("findings", [])
    print(f"  Findings: {len(findings)}")
    for i, f in enumerate(findings[:5]):
        label = _signal_title(atype, f)
        print(f"    {i+1}. {label[:100]}")
    if len(findings) > 5:
        print(f"    ... +{len(findings) - 5} more")
    for rec in a.get("recommendations", [])[:2]:
        print(f"  >> {rec[:120]}")


if __name__ == "__main__":
    main()
