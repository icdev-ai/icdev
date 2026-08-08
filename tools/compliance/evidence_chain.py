#!/usr/bin/env python3
# CUI // SP-CTI
"""Continuous Compliance Evidence Chain — connects PDC/NDC/SDC audit trails
into an OSCAL 1.1.2-aligned evidence timeline.

Collects audit events from all three ICDEV™ design canvases:
  - PDC (Pipeline Design Canvas) — pc_audit, pc_compliance_findings
  - NDC (Network Design Canvas)  — nc_audit
  - SDC (Security Design Canvas) — sc_audit, sc_assessments
  - ICDEV main audit_trail        — compliance-class events

Maps each event to NIST 800-53 control families and exports an
OSCAL Assessment Results document with chronological observations.

Architecture Decision: D-CHAIN-1 — Evidence chain is read-only across canvas
DBs; never writes to PDC/NDC/SDC databases. Snapshots stored in
compliance_evidence_chain table in icdev.db.

Usage:
  python tools/compliance/evidence_chain.py --json
  python tools/compliance/evidence_chain.py --gate
  python tools/compliance/evidence_chain.py --export-oscal --output /tmp/ar.json
  python tools/compliance/evidence_chain.py --project-id proj-123 --since 24h --json
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent
ICDEV_DB = BASE_DIR / "data" / "icdev.db"
PDC_DB = BASE_DIR / "data" / "pipeline_canvas.db"
NDC_DB = BASE_DIR / "data" / "network_canvas.db"
SDC_DB = BASE_DIR / "data" / "security_canvas.db"

OSCAL_VERSION = "1.1.2"
CLASSIFICATION = "CUI // SP-CTI"

# ---------------------------------------------------------------------------
# NIST 800-53 control family labels
# ---------------------------------------------------------------------------
CONTROL_FAMILIES = {
    "ac": "Access Control",
    "au": "Audit and Accountability",
    "ca": "Assessment, Authorization, and Monitoring",
    "cm": "Configuration Management",
    "ia": "Identification and Authentication",
    "ir": "Incident Response",
    "pe": "Physical and Environmental Protection",
    "pl": "Planning",
    "ra": "Risk Assessment",
    "sa": "System and Services Acquisition",
    "sc": "System and Communications Protection",
    "si": "System and Information Integrity",
    "sr": "Supply Chain Risk Management",
}

# ---------------------------------------------------------------------------
# Event → OSCAL control mapping
# Each entry: (list_of_control_ids, primary_family, evidence_type)
# evidence_type: 'audit' | 'test' | 'assessment' | 'deployment' | 'compliance'
# ---------------------------------------------------------------------------
_PDC_ACTION_MAP: List[tuple[str, list[str], str, str]] = [
    # keyword_in_action,  controls,               family, ev_type
    ("pipeline_created", ["cm-2", "cm-3"], "cm", "audit"),
    ("pipeline_updated", ["cm-2", "cm-3", "cm-4"], "cm", "audit"),
    ("stage_added", ["cm-2", "sa-10"], "cm", "audit"),
    ("snippet_added", ["sa-11", "sr-4"], "sa", "audit"),
    ("compliance_check", ["ca-7", "ca-2"], "ca", "compliance"),
    ("compliance_finding", ["ca-5", "ca-7"], "ca", "compliance"),
    ("deploy", ["cm-2", "sa-10", "cm-4"], "cm", "deployment"),
    ("version", ["cm-3", "cm-4"], "cm", "audit"),
    ("slsa", ["sa-11", "sr-4", "sr-5"], "sa", "compliance"),
    ("ssdf", ["sa-15", "sa-11"], "sa", "compliance"),
    ("supply_chain", ["sr-3", "sr-4", "sr-5"], "sr", "compliance"),
    ("gate", ["cm-4", "ca-2"], "cm", "compliance"),
]

_NDC_ACTION_MAP: List[tuple[str, list[str], str, str]] = [
    ("topology_created", ["cm-8", "sc-7"], "cm", "audit"),
    ("topology_updated", ["cm-8", "cm-3"], "cm", "audit"),
    ("object_added", ["cm-8"], "cm", "audit"),
    ("compliance", ["sc-7", "ca-7"], "sc", "compliance"),
    ("simulation", ["ca-3", "sc-7"], "ca", "test"),
    ("vpn", ["sc-8", "sc-12"], "sc", "audit"),
    ("firewall", ["sc-7", "ac-4"], "sc", "audit"),
    ("segment", ["ac-4", "sc-7"], "ac", "audit"),
    ("circuit", ["sc-8", "pe-4"], "sc", "audit"),
    ("encryption", ["sc-8", "sc-28"], "sc", "audit"),
    ("audit", ["au-2", "au-12"], "au", "audit"),
]

_SDC_ACTION_MAP: List[tuple[str, list[str], str, str]] = [
    ("design_created", ["ra-3", "ca-2"], "ra", "audit"),
    ("design_updated", ["ra-3", "cm-3"], "ra", "audit"),
    ("threat_added", ["ra-3", "ra-5"], "ra", "assessment"),
    ("threat_resolved", ["ca-5", "ra-7"], "ca", "assessment"),
    ("control_added", ["ca-5", "si-2"], "ca", "compliance"),
    ("control_updated", ["ca-5"], "ca", "compliance"),
    ("assessment", ["ca-2", "ca-7"], "ca", "assessment"),
    ("boundary", ["ac-4", "sc-7"], "ac", "audit"),
    ("data_flow", ["sc-8", "ac-4"], "sc", "audit"),
    ("stride", ["ra-3", "ra-5"], "ra", "assessment"),
    ("remediation", ["ca-5", "ra-7"], "ca", "compliance"),
    ("version", ["cm-3", "ca-2"], "cm", "audit"),
]

# icdev audit_trail compliance-relevant event types and their mappings
_ICDEV_EVENT_MAP: Dict[str, tuple[list[str], str, str]] = {
    "compliance_check": (["ca-7"], "ca", "compliance"),
    "ssp_generated": (["ca-6", "pl-2"], "ca", "compliance"),
    "poam_generated": (["ca-5"], "ca", "compliance"),
    "stig_checked": (["ca-2", "si-2"], "ca", "compliance"),
    "sbom_generated": (["sr-3", "sa-12"], "sr", "compliance"),
    "security_scan": (["ca-2", "si-2"], "ca", "test"),
    "vulnerability_found": (["ra-5", "si-2"], "ra", "assessment"),
    "vulnerability_resolved": (["si-2", "ca-5"], "si", "compliance"),
    "deployment_initiated": (["cm-2", "sa-10"], "cm", "deployment"),
    "deployment_succeeded": (["cm-2", "sa-10"], "cm", "deployment"),
    "deployment_failed": (["ca-5", "ir-4"], "ca", "audit"),
    "rollback_executed": (["cp-10", "ca-5"], "cp", "audit"),
    "code_reviewed": (["sa-11", "cm-4"], "sa", "test"),
    "code_approved": (["sa-11", "cm-4"], "sa", "compliance"),
    "test_executed": (["ca-2", "si-3"], "ca", "test"),
    "test_passed": (["ca-2", "si-3"], "ca", "test"),
    "test_failed": (["ca-5", "si-3"], "ca", "test"),
    "boundary_assessed": (["ca-3", "sc-7"], "ca", "assessment"),
    "scrm_assessed": (["sr-3", "sr-4"], "sr", "assessment"),
    "cve_triaged": (["ra-5", "si-2"], "ra", "assessment"),
    "supply_chain_risk_escalated": (["sr-5", "ca-5"], "sr", "compliance"),
    "simulation_completed": (["ca-3", "ra-3"], "ca", "test"),
    "coa_selected": (["ra-7", "ca-5"], "ra", "compliance"),
}


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


def _open_sqlite(path: Path) -> Optional[sqlite3.Connection]:
    """Open SQLite connection if file exists, else return None.

    NOTE: Uses direct sqlite3 because canvas DBs (PDC/NDC/SDC) are isolated
    SQLite files. _table_exists() below is backend-aware (shared helper), so it
    speaks sqlite_master with ``?`` against these raw connections; the icdev
    audit DB goes through get_connection() and can be PostgreSQL.
    """
    if not path.exists():
        return None
    conn = sqlite3.connect(str(path))  # sqlite3-ok — isolated canvas SQLite DB, sqlite_master dependency
    conn.row_factory = sqlite3.Row
    return conn


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    """Backend-aware table existence probe.

    Delegates to the shared ``tools.db.storage.table_exists`` helper, which
    speaks the right dialect for the connection: ``sqlite_master`` with a ``?``
    placeholder for the raw canvas SQLite files opened by ``_open_sqlite`` and
    ``information_schema`` for the PostgreSQL-backed icdev audit connection.

    The previous inline probe used a ``%s`` placeholder, which raises
    ``sqlite3.ProgrammingError`` on a raw sqlite3 connection (qmark paramstyle)
    — so every ``_open_sqlite`` caller (PDC/NDC/SDC canvas evidence) silently
    dropped its events, and on PostgreSQL the bare ``sqlite_master`` reference
    raised as well.
    """
    from tools.db.storage import table_exists
    return table_exists(conn, name)


def _parse_ts(ts_str: Optional[str]) -> Optional[datetime]:
    """Parse timestamp string to UTC datetime."""
    if not ts_str:
        return None
    for fmt in (
        "%Y-%m-%dT%H:%M:%S.%f+00:00",
        "%Y-%m-%dT%H:%M:%S+00:00",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M:%S.%f",
    ):
        try:
            dt = datetime.strptime(
                ts_str.replace("+00:00", "").replace("Z", ""), fmt.replace("+00:00", "").replace("Z", "")
            )
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _get_icdev_conn(db_path: Optional[Path] = None) -> sqlite3.Connection:
    """Get connection to icdev.db with WAL mode and 30s busy timeout."""
    target = str(db_path or ICDEV_DB)
    try:
        from tools.db.storage import get_connection

        conn = get_connection(db_path=target)
        # Apply busy timeout so concurrent writers don't immediately fail
        try:
            conn.execute("PRAGMA busy_timeout=30000")
            conn.execute("PRAGMA journal_mode=WAL")
        except Exception:
            pass
        return conn
    except ImportError:
        conn = sqlite3.connect(target, timeout=30)  # sqlite3-ok — ImportError fallback when storage module unavailable
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn


# ---------------------------------------------------------------------------
# OSCAL control mapping helpers
# ---------------------------------------------------------------------------


def _map_action_keyword(
    action: str,
    mapping: List[tuple[str, list[str], str, str]],
) -> tuple[list[str], str, str]:
    """Find best matching controls for an action string via keyword scan."""
    action_lower = (action or "").lower()
    for keyword, controls, family, ev_type in mapping:
        if keyword in action_lower:
            return controls, family, ev_type
    return ["ca-7"], "ca", "audit"  # default: continuous monitoring


def _map_icdev_event(event_type: str) -> tuple[list[str], str, str]:
    """Map an icdev audit_trail event_type to OSCAL controls."""
    return _ICDEV_EVENT_MAP.get(event_type, (["au-2"], "au", "audit"))


# ---------------------------------------------------------------------------
# Canvas event collectors
# ---------------------------------------------------------------------------

EvidenceEvent = Dict[str, Any]


def collect_pdc_events(since_dt: Optional[datetime] = None) -> List[EvidenceEvent]:
    """Collect audit events from Pipeline Design Canvas."""
    conn = _open_sqlite(PDC_DB)
    if conn is None:
        return []
    events: List[EvidenceEvent] = []

    try:
        # pc_audit
        if _table_exists(conn, "pc_audit"):
            sql = "SELECT id, action, entity_type, entity_id, details, user_id, classification, ts FROM pc_audit"
            params: list = []
            if since_dt:
                sql += " WHERE ts >= ?"
                params.append(since_dt.strftime("%Y-%m-%d %H:%M:%S"))
            sql += " ORDER BY ts ASC"
            for row in conn.execute(sql, params).fetchall():
                controls, family, ev_type = _map_action_keyword(row["action"], _PDC_ACTION_MAP)
                events.append(
                    {
                        "source": "pdc",
                        "event_id": str(row["id"]),
                        "event_type": f"pdc.{row['entity_type'] or 'pipeline'}.{row['action'][:30]}",
                        "actor": row["user_id"] or "system",
                        "action": row["action"],
                        "oscal_controls": controls,
                        "oscal_family": family,
                        "evidence_type": ev_type,
                        "classification": row["classification"] or CLASSIFICATION,
                        "event_ts": row["ts"] or datetime.now(timezone.utc).isoformat(),
                        "details_json": row["details"],
                    }
                )

        # pc_compliance_findings
        if _table_exists(conn, "pc_compliance_findings"):
            sql = (
                "SELECT id, pipeline_id, rule_id, framework, severity, title, description, "
                "affected_entity, affected_type, status, fix_action, remediated_at, created_at "
                "FROM pc_compliance_findings"
            )
            params = []
            if since_dt:
                sql += " WHERE created_at >= ?"
                params.append(since_dt.strftime("%Y-%m-%d %H:%M:%S"))
            sql += " ORDER BY created_at ASC"
            for row in conn.execute(sql, params).fetchall():
                controls, family, _ = _map_action_keyword(
                    row["framework"] + " " + (row["rule_id"] or ""), _PDC_ACTION_MAP
                )
                events.append(
                    {
                        "source": "pdc",
                        "event_id": f"pcf-{row['id']}",
                        "event_type": f"pdc.compliance_finding.{row['framework']}",
                        "actor": "pdc-scanner",
                        "action": f"[{row['severity']}] {row['title']}",
                        "oscal_controls": controls,
                        "oscal_family": family,
                        "evidence_type": "compliance",
                        "classification": CLASSIFICATION,
                        "event_ts": row["created_at"] or datetime.now(timezone.utc).isoformat(),
                        "details_json": json.dumps(
                            {
                                "pipeline_id": row["pipeline_id"],
                                "rule_id": row["rule_id"],
                                "framework": row["framework"],
                                "severity": row["severity"],
                                "status": row["status"],
                                "fix_action": row["fix_action"],
                                "remediated_at": row["remediated_at"],
                            }
                        ),
                    }
                )
    finally:
        conn.close()

    return events


def collect_ndc_events(since_dt: Optional[datetime] = None) -> List[EvidenceEvent]:
    """Collect audit events from Network Design Canvas."""
    conn = _open_sqlite(NDC_DB)
    if conn is None:
        return []
    events: List[EvidenceEvent] = []

    try:
        if _table_exists(conn, "nc_audit"):
            sql = "SELECT id, action, entity_type, entity_id, details, user_id, classification, ts FROM nc_audit"
            params: list = []
            if since_dt:
                sql += " WHERE ts >= ?"
                params.append(since_dt.strftime("%Y-%m-%d %H:%M:%S"))
            sql += " ORDER BY ts ASC"
            for row in conn.execute(sql, params).fetchall():
                controls, family, ev_type = _map_action_keyword(row["action"], _NDC_ACTION_MAP)
                events.append(
                    {
                        "source": "ndc",
                        "event_id": str(row["id"]),
                        "event_type": f"ndc.{row['entity_type'] or 'topology'}.{row['action'][:30]}",
                        "actor": row["user_id"] or "system",
                        "action": row["action"],
                        "oscal_controls": controls,
                        "oscal_family": family,
                        "evidence_type": ev_type,
                        "classification": row["classification"] or CLASSIFICATION,
                        "event_ts": row["ts"] or datetime.now(timezone.utc).isoformat(),
                        "details_json": row["details"],
                    }
                )
    finally:
        conn.close()

    return events


def collect_sdc_events(since_dt: Optional[datetime] = None) -> List[EvidenceEvent]:
    """Collect audit events from Security Design Canvas."""
    conn = _open_sqlite(SDC_DB)
    if conn is None:
        return []
    events: List[EvidenceEvent] = []

    try:
        # sc_audit
        if _table_exists(conn, "sc_audit"):
            sql = "SELECT id, action, entity_type, entity_id, details, user_id, classification, ts FROM sc_audit"
            params: list = []
            if since_dt:
                sql += " WHERE ts >= ?"
                params.append(since_dt.strftime("%Y-%m-%d %H:%M:%S"))
            sql += " ORDER BY ts ASC"
            for row in conn.execute(sql, params).fetchall():
                controls, family, ev_type = _map_action_keyword(row["action"], _SDC_ACTION_MAP)
                events.append(
                    {
                        "source": "sdc",
                        "event_id": str(row["id"]),
                        "event_type": f"sdc.{row['entity_type'] or 'design'}.{row['action'][:30]}",
                        "actor": row["user_id"] or "system",
                        "action": row["action"],
                        "oscal_controls": controls,
                        "oscal_family": family,
                        "evidence_type": ev_type,
                        "classification": row["classification"] or CLASSIFICATION,
                        "event_ts": row["ts"] or datetime.now(timezone.utc).isoformat(),
                        "details_json": row["details"],
                    }
                )

        # sc_assessments — STRIDE/compliance assessment records
        if _table_exists(conn, "sc_assessments"):
            sql = (
                "SELECT id, design_id, assessment_type, trigger_source, "
                "total_threats, total_controls, risk_score, posture_grade, ran_at "
                "FROM sc_assessments"
            )
            params = []
            if since_dt:
                sql += " WHERE ran_at >= ?"
                params.append(since_dt.strftime("%Y-%m-%d %H:%M:%S"))
            sql += " ORDER BY ran_at ASC"
            for row in conn.execute(sql, params).fetchall():
                events.append(
                    {
                        "source": "sdc",
                        "event_id": f"sca-{row['id']}",
                        "event_type": f"sdc.assessment.{row['assessment_type']}",
                        "actor": row["trigger_source"] or "sdc-engine",
                        "action": (
                            f"Security assessment [{row['assessment_type']}]: "
                            f"grade={row['posture_grade']}, threats={row['total_threats']}, "
                            f"controls={row['total_controls']}, risk={row['risk_score']}"
                        ),
                        "oscal_controls": ["ca-2", "ca-7", "ra-3"],
                        "oscal_family": "ca",
                        "evidence_type": "assessment",
                        "classification": CLASSIFICATION,
                        "event_ts": row["ran_at"] or datetime.now(timezone.utc).isoformat(),
                        "details_json": json.dumps(
                            {
                                "design_id": row["design_id"],
                                "assessment_type": row["assessment_type"],
                                "risk_score": row["risk_score"],
                                "posture_grade": row["posture_grade"],
                                "total_threats": row["total_threats"],
                                "total_controls": row["total_controls"],
                            }
                        ),
                    }
                )
    finally:
        conn.close()

    return events


# Compliance-relevant event types to pull from icdev audit_trail
_ICDEV_RELEVANT_EVENTS = frozenset(_ICDEV_EVENT_MAP.keys())


def collect_icdev_events(
    project_id: Optional[str] = None,
    since_dt: Optional[datetime] = None,
) -> List[EvidenceEvent]:
    """Collect compliance-relevant events from the main icdev audit_trail."""
    conn = _get_icdev_conn()
    events: List[EvidenceEvent] = []

    try:
        if not _table_exists(conn, "audit_trail"):
            return events

        placeholders = ",".join("?" * len(_ICDEV_RELEVANT_EVENTS))
        sql = (
            f"SELECT id, project_id, event_type, actor, action, details, "  # nosec B608
            f"classification, session_id, created_at "
            f"FROM audit_trail WHERE event_type IN ({placeholders})"
        )
        params: list = list(_ICDEV_RELEVANT_EVENTS)

        if project_id:
            sql += " AND project_id = ?"
            params.append(project_id)
        if since_dt:
            sql += " AND created_at >= ?"
            params.append(since_dt.strftime("%Y-%m-%d %H:%M:%S"))

        sql += " ORDER BY created_at ASC"

        for row in conn.execute(sql, params).fetchall():
            controls, family, ev_type = _map_icdev_event(row["event_type"])
            events.append(
                {
                    "source": "icdev",
                    "event_id": str(row["id"]),
                    "event_type": row["event_type"],
                    "actor": row["actor"] or "system",
                    "action": row["action"],
                    "oscal_controls": controls,
                    "oscal_family": family,
                    "evidence_type": ev_type,
                    "classification": row["classification"] or CLASSIFICATION,
                    "event_ts": row["created_at"] or datetime.now(timezone.utc).isoformat(),
                    "details_json": row["details"],
                }
            )
    finally:
        conn.close()

    return events


# ---------------------------------------------------------------------------
# Chain builder
# ---------------------------------------------------------------------------


def build_evidence_chain(
    project_id: Optional[str] = None,
    since_hours: float = 168.0,  # 7 days default
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Build a unified compliance evidence chain from PDC/NDC/SDC + ICDEV.

    Args:
        project_id: Optional project filter for icdev events.
        since_hours: Look-back window in hours (default 168 = 7 days).
        db_path: Override icdev.db path.

    Returns:
        Evidence chain manifest with timeline and control coverage summary.
    """
    since_dt = datetime.now(timezone.utc) - timedelta(hours=since_hours)
    chain_id = str(uuid.uuid4())
    built_at = datetime.now(timezone.utc).isoformat()

    # Collect from all sources in parallel logical order
    pdc_events = collect_pdc_events(since_dt)
    ndc_events = collect_ndc_events(since_dt)
    sdc_events = collect_sdc_events(since_dt)
    icdev_events = collect_icdev_events(project_id, since_dt)

    all_events = pdc_events + ndc_events + sdc_events + icdev_events

    # Sort chronologically
    def _sort_key(ev: EvidenceEvent) -> str:
        return ev.get("event_ts") or "1970-01-01 00:00:00"

    all_events.sort(key=_sort_key)

    # Persist to icdev.db (best-effort — DB may be locked by dashboard)
    persisted = _persist_chain(chain_id, all_events, db_path)

    # Compute control coverage
    covered_controls: set[str] = set()
    covered_families: set[str] = set()
    source_counts: Dict[str, int] = {"pdc": 0, "ndc": 0, "sdc": 0, "icdev": 0}
    ev_type_counts: Dict[str, int] = {}

    for ev in all_events:
        covered_controls.update(ev.get("oscal_controls") or [])
        if ev.get("oscal_family"):
            covered_families.add(ev["oscal_family"])
        source_counts[ev["source"]] = source_counts.get(ev["source"], 0) + 1
        et = ev["evidence_type"]
        ev_type_counts[et] = ev_type_counts.get(et, 0) + 1

    # Determine date range
    first_ts = all_events[0]["event_ts"] if all_events else built_at
    last_ts = all_events[-1]["event_ts"] if all_events else built_at

    # Gate assessment
    gate_pass, gate_findings = _evaluate_gate(pdc_events, ndc_events, sdc_events, icdev_events)

    return {
        "chain_id": chain_id,
        "classification": CLASSIFICATION,
        "built_at": built_at,
        "project_id": project_id,
        "since_hours": since_hours,
        "timeline": {
            "first_event": first_ts,
            "last_event": last_ts,
            "total_events": len(all_events),
            "events": all_events,
        },
        "coverage": {
            "oscal_controls": sorted(covered_controls),
            "nist_families": sorted(covered_families),
            "family_names": {f: CONTROL_FAMILIES.get(f, f) for f in sorted(covered_families)},
            "control_count": len(covered_controls),
            "family_count": len(covered_families),
        },
        "sources": {
            "pdc": {"events": source_counts.get("pdc", 0)},
            "ndc": {"events": source_counts.get("ndc", 0)},
            "sdc": {"events": source_counts.get("sdc", 0)},
            "icdev": {"events": source_counts.get("icdev", 0)},
        },
        "evidence_types": ev_type_counts,
        "persisted": persisted,
        "gate": {
            "pass": gate_pass,
            "findings": gate_findings,
        },
    }


def _persist_chain(
    chain_id: str,
    events: List[EvidenceEvent],
    db_path: Optional[Path] = None,
) -> bool:
    """Persist evidence chain events to compliance_evidence_chain table.

    Returns True on success, False if DB is locked (non-fatal — chain is still
    available in-memory for OSCAL export).
    """
    # Use _get_icdev_conn which handles both SQLite and Postgres via get_connection
    try:
        conn = _get_icdev_conn(db_path)
    except Exception:  # pragma: no cover
        return False

    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS compliance_evidence_chain (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                chain_id       TEXT NOT NULL,
                event_id       TEXT NOT NULL,
                source         TEXT NOT NULL,
                event_type     TEXT NOT NULL,
                actor          TEXT,
                action         TEXT NOT NULL,
                oscal_controls TEXT DEFAULT '[]',
                oscal_family   TEXT,
                evidence_type  TEXT DEFAULT 'audit',
                classification TEXT DEFAULT 'CUI // SP-CTI',
                event_ts       TEXT NOT NULL,
                details_json   TEXT,
                created_at     TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_cec_chain ON compliance_evidence_chain(chain_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_cec_source ON compliance_evidence_chain(source)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_cec_ts ON compliance_evidence_chain(event_ts)")

        for ev in events:
            conn.execute(
                """INSERT INTO compliance_evidence_chain
                   (chain_id, event_id, source, event_type, actor, action,
                    oscal_controls, oscal_family, evidence_type, classification,
                    event_ts, details_json)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    chain_id,
                    ev["event_id"],
                    ev["source"],
                    ev["event_type"],
                    ev.get("actor"),
                    ev["action"],
                    json.dumps(ev.get("oscal_controls") or []),
                    ev.get("oscal_family"),
                    ev.get("evidence_type", "audit"),
                    ev.get("classification", CLASSIFICATION),
                    ev["event_ts"],
                    ev.get("details_json"),
                ),
            )
        conn.commit()
        return True
    except sqlite3.OperationalError:
        # DB locked by another writer — chain data is still valid in-memory
        return False
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Gate evaluation
# ---------------------------------------------------------------------------


def _evaluate_gate(
    pdc_events: List[EvidenceEvent],
    ndc_events: List[EvidenceEvent],
    sdc_events: List[EvidenceEvent],
    icdev_events: List[EvidenceEvent],
) -> tuple[bool, List[str]]:
    """Evaluate evidence chain completeness gate.

    Returns:
        (pass_bool, list_of_finding_strings)
    """
    findings: List[str] = []

    if not pdc_events:
        findings.append("WARN: No PDC (Pipeline Design Canvas) audit events found — CM/SA/SR control evidence missing")
    if not ndc_events:
        findings.append("WARN: No NDC (Network Design Canvas) audit events found — SC/AC control evidence missing")
    if not sdc_events:
        findings.append("WARN: No SDC (Security Design Canvas) audit events found — RA/CA control evidence missing")
    if not icdev_events:
        findings.append("WARN: No ICDEV compliance audit events found — AU/CA continuous monitoring evidence missing")

    # Check that at minimum some assessment evidence exists
    all_events = pdc_events + ndc_events + sdc_events + icdev_events
    assessment_events = [e for e in all_events if e["evidence_type"] == "assessment"]
    if not assessment_events:
        findings.append(
            "FAIL: No assessment-type evidence found — CA-2 (Security Assessments) requires periodic testing"
        )

    # Check for compliance findings without remediation
    unresolved = [
        e
        for e in all_events
        if e["evidence_type"] == "compliance"
        and "finding" in e["event_type"].lower()
        and "resolved" not in (e.get("action") or "").lower()
    ]
    if len(unresolved) > 10:
        findings.append(
            f"FAIL: {len(unresolved)} unresolved compliance findings — CA-5 (POA&M) requires remediation tracking"
        )

    # Hard fail only on FAIL-level findings
    fail_count = sum(1 for f in findings if f.startswith("FAIL"))
    return fail_count == 0, findings


# ---------------------------------------------------------------------------
# OSCAL Assessment Results exporter
# ---------------------------------------------------------------------------


def export_oscal_assessment_results(chain: Dict[str, Any]) -> Dict[str, Any]:
    """Convert evidence chain to OSCAL 1.1.2 Assessment Results document."""
    now = datetime.now(timezone.utc)
    ar_uuid = str(uuid.uuid4())
    result_uuid = str(uuid.uuid4())

    observations = []
    findings = []

    for ev in chain["timeline"]["events"]:
        obs_uuid = str(uuid.uuid4())
        controls = ev.get("oscal_controls") or []
        family = ev.get("oscal_family", "ca")
        family_name = CONTROL_FAMILIES.get(family, family.upper())

        obs = {
            "uuid": obs_uuid,
            "title": ev["action"][:120] if ev.get("action") else ev["event_type"],
            "description": (f"[{ev['source'].upper()}] {ev['event_type']} — actor: {ev.get('actor', 'unknown')}"),
            "methods": _evidence_methods(ev["evidence_type"]),
            "types": [ev["evidence_type"]],
            "subjects": [
                {
                    "subject-uuid": str(uuid.uuid5(uuid.NAMESPACE_DNS, ev["source"])),
                    "type": "component",
                    "title": ev["source"].upper() + " Canvas",
                }
            ],
            "relevant-evidence": [
                {
                    "description": ev["action"],
                    "props": [
                        {"name": "source", "ns": OSCAL_NS, "value": ev["source"]},
                        {"name": "event-type", "ns": OSCAL_NS, "value": ev["event_type"]},
                        {"name": "actor", "ns": OSCAL_NS, "value": ev.get("actor", "system")},
                        {"name": "oscal-family", "ns": OSCAL_NS, "value": family_name},
                        {"name": "classification", "ns": OSCAL_NS, "value": ev.get("classification", CLASSIFICATION)},
                    ],
                }
            ],
            "related-controls": {"control-selections": [{"include-controls": [{"control-id": c} for c in controls]}]},
            "collected": _normalise_ts(ev["event_ts"]),
            "expires": (now + timedelta(days=365)).isoformat(),
        }
        observations.append(obs)

        # Gate findings become OSCAL findings
        if ev["evidence_type"] == "compliance" and "finding" in ev["event_type"].lower():
            findings.append(
                {
                    "uuid": str(uuid.uuid4()),
                    "title": ev["action"][:120],
                    "description": ev["action"],
                    "related-observations": [{"observation-uuid": obs_uuid}],
                    "related-controls": {
                        "control-selections": [{"include-controls": [{"control-id": c} for c in controls]}]
                    },
                    "target": {
                        "type": "statement-id",
                        "target-id": f"{controls[0]}_stmt" if controls else "ca-7_stmt",
                        "status": {"state": "not-satisfied"},
                    },
                }
            )

    # Collect all reviewed control IDs
    reviewed_ids = sorted(chain["coverage"]["oscal_controls"])

    return {
        "assessment-results": {
            "uuid": ar_uuid,
            "metadata": {
                "title": "ICDEV™ Continuous Compliance Evidence Chain",
                "published": now.isoformat(),
                "last-modified": now.isoformat(),
                "version": chain["chain_id"],
                "oscal-version": OSCAL_VERSION,
                "props": [
                    {"name": "classification", "value": CLASSIFICATION},
                    {"name": "chain-id", "value": chain["chain_id"]},
                    {"name": "sources", "value": "PDC,NDC,SDC,ICDEV"},
                ],
                "roles": [{"id": "assessor", "title": "ICDEV™ Automated Assessor"}],
                "parties": [
                    {
                        "uuid": str(uuid.uuid5(uuid.NAMESPACE_DNS, "icdev.ai")),
                        "type": "organization",
                        "name": "ICDEV™ Intelligent Certified Development",
                    }
                ],
            },
            "import-ap": {"href": "#"},
            "results": [
                {
                    "uuid": result_uuid,
                    "title": "PDC/NDC/SDC Evidence Timeline",
                    "description": (
                        f"Continuous compliance evidence chain spanning "
                        f"{chain['timeline']['total_events']} events from "
                        f"{chain['timeline']['first_event']} to "
                        f"{chain['timeline']['last_event']}"
                    ),
                    "start": _normalise_ts(chain["timeline"]["first_event"]),
                    "end": _normalise_ts(chain["timeline"]["last_event"]),
                    "props": [
                        {"name": "total-events", "value": str(chain["timeline"]["total_events"])},
                        {"name": "gate-pass", "value": str(chain["gate"]["pass"]).lower()},
                        {"name": "pdc-events", "value": str(chain["sources"]["pdc"]["events"])},
                        {"name": "ndc-events", "value": str(chain["sources"]["ndc"]["events"])},
                        {"name": "sdc-events", "value": str(chain["sources"]["sdc"]["events"])},
                    ],
                    "reviewed-controls": {
                        "description": "NIST 800-53 controls evidenced by PDC/NDC/SDC audit trails",
                        "control-selections": [{"include-controls": [{"control-id": c} for c in reviewed_ids]}],
                    },
                    "observations": observations,
                    "findings": findings,
                    "risks": _build_risks(chain),
                }
            ],
        }
    }


OSCAL_NS = "https://icdev.ai/ns/oscal/1.0"


def _normalise_ts(ts_str: str) -> str:
    """Normalise a timestamp string to ISO 8601 with Z suffix."""
    if not ts_str:
        return datetime.now(timezone.utc).isoformat()
    dt = _parse_ts(ts_str)
    if dt:
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    return ts_str


def _evidence_methods(evidence_type: str) -> List[str]:
    """Map evidence type to OSCAL assessment methods."""
    mapping = {
        "audit": ["EXAMINE"],
        "test": ["TEST"],
        "assessment": ["EXAMINE", "TEST"],
        "compliance": ["EXAMINE", "INTERVIEW"],
        "deployment": ["EXAMINE"],
    }
    return mapping.get(evidence_type, ["EXAMINE"])


def _build_risks(chain: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Build OSCAL risk entries from gate findings."""
    risks = []
    for finding in chain["gate"]["findings"]:
        level = "high" if finding.startswith("FAIL") else "medium"
        risks.append(
            {
                "uuid": str(uuid.uuid4()),
                "title": finding[:120],
                "description": finding,
                "status": "open",
                "characterizations": [
                    {
                        "origin": {
                            "actors": [
                                {"type": "tool", "actor-uuid": str(uuid.uuid5(uuid.NAMESPACE_DNS, "evidence-chain"))}
                            ]
                        },
                        "facets": [{"name": "likelihood", "system": "https://fedramp.gov", "value": level}],
                    }
                ],
            }
        )
    return risks


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_since(since_arg: str) -> float:
    """Parse '--since' argument like '24h', '7d', '168h' to float hours."""
    since_arg = since_arg.strip().lower()
    if since_arg.endswith("d"):
        return float(since_arg[:-1]) * 24
    if since_arg.endswith("h"):
        return float(since_arg[:-1])
    return float(since_arg)  # assume hours if bare number


def run_cli() -> None:
    parser = argparse.ArgumentParser(description="Continuous Compliance Evidence Chain (PDC/NDC/SDC → OSCAL)")
    parser.add_argument("--project-id", help="Filter icdev events by project ID")
    parser.add_argument(
        "--since",
        default="168h",
        help="Look-back window, e.g. 24h, 7d (default: 168h = 7 days)",
    )
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument(
        "--gate",
        action="store_true",
        help="Gate mode: exit 1 if FAIL findings present",
    )
    parser.add_argument(
        "--export-oscal",
        action="store_true",
        help="Export OSCAL Assessment Results document",
    )
    parser.add_argument(
        "--output",
        help="Output file path for --export-oscal (default: stdout)",
    )
    parser.add_argument("--db-path", help="Override icdev.db path")
    args = parser.parse_args()

    since_hours = _parse_since(args.since)
    db_path = Path(args.db_path) if args.db_path else None

    chain = build_evidence_chain(
        project_id=args.project_id,
        since_hours=since_hours,
        db_path=db_path,
    )

    if args.export_oscal:
        ar_doc = export_oscal_assessment_results(chain)
        doc_str = json.dumps(ar_doc, indent=2, default=str)
        if args.output:
            Path(args.output).write_text(doc_str, encoding="utf-8", newline="")
            print(f"OSCAL Assessment Results written to {args.output}")
        else:
            print(doc_str)
        return

    if args.json:
        # Omit full event list in summary mode unless explicitly requested
        output = {k: v for k, v in chain.items() if k != "timeline"}
        output["timeline_summary"] = {
            "first_event": chain["timeline"]["first_event"],
            "last_event": chain["timeline"]["last_event"],
            "total_events": chain["timeline"]["total_events"],
        }
        print(json.dumps(output, indent=2, default=str))
    else:
        t = chain["timeline"]
        cov = chain["coverage"]
        src = chain["sources"]
        gate = chain["gate"]
        print("\nContinuous Compliance Evidence Chain")
        print(f"  Chain ID  : {chain['chain_id']}")
        print(f"  Built at  : {chain['built_at']}")
        print(f"  Window    : last {since_hours:.0f}h")
        print("\nSources:")
        print(f"  PDC (pipeline) : {src['pdc']['events']} events")
        print(f"  NDC (network)  : {src['ndc']['events']} events")
        print(f"  SDC (security) : {src['sdc']['events']} events")
        print(f"  ICDEV audit    : {src['icdev']['events']} events")
        print(f"  Total          : {t['total_events']} events")
        print(f"\nTimeline: {t['first_event']} -> {t['last_event']}")
        print("\nNIST 800-53 Coverage:")
        print(f"  Controls : {cov['control_count']} ({', '.join(cov['oscal_controls'])})")
        print(f"  Families : {cov['family_count']} ({', '.join(cov['nist_families'])})")
        print(f"\nGate: {'PASS' if gate['pass'] else 'FAIL'}")
        for f in gate["findings"]:
            print(f"  {f}")

    if args.gate and not chain["gate"]["pass"]:
        import sys

        sys.exit(1)


if __name__ == "__main__":
    run_cli()
