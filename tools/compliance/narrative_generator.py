#!/usr/bin/env python3
"""Generate compliance narratives for ATO packages.

This module provides two levels of narrative generation:

1. **Per-control narratives** (original) -- Renders SSP Section 13 control
   implementation narratives from project_controls, audit_trail, fedramp/cmmc
   assessment data using Jinja2 templates.  Optional ``--use-llm`` refines
   output via LLMRouter for SSP-quality prose.

2. **Document-level narratives** (new) -- Generates complete SSP prose sections,
   POAM milestone descriptions, and executive summaries from assessment JSON
   data across all active compliance frameworks.

CLI:
    # Per-control narratives (existing)
    python tools/compliance/narrative_generator.py --project-id proj-123 --all --json
    python tools/compliance/narrative_generator.py --project-id proj-123 --control AC-2

    # Document-level narratives (new)
    python tools/compliance/narrative_generator.py --project-id proj-123 --type ssp --json
    python tools/compliance/narrative_generator.py --project-id proj-123 --type poam --json
    python tools/compliance/narrative_generator.py --project-id proj-123 --type executive --json
"""

import argparse
import json
import sqlite3
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

try:
    from jinja2 import Template
except ImportError:
    Template = None  # type: ignore[assignment,misc]

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"
TEMPLATE_DIR = BASE_DIR / "context" / "compliance" / "narrative_templates"

DEFAULT_TEMPLATE = """\
## {{ control_id }} — {{ control_title }}
**Implementation Status:** {{ implementation_status | default('Not Assessed', true) }}
{% if implementation_description -%}
### Implementation Description
{{ implementation_description }}
{% endif -%}
{% if responsible_role %}**Responsible Role:** {{ responsible_role }}
{% endif -%}
{% if evidence_path %}**Evidence:** {{ evidence_path }}
{% endif -%}
{% if fedramp -%}
### FedRAMP Assessment
- **Baseline:** {{ fedramp.baseline | upper }}  **Status:** {{ fedramp.status | replace('_', ' ') | title }}
{% if fedramp.evidence_description %}- **Evidence:** {{ fedramp.evidence_description }}
{% endif %}{% if fedramp.notes %}- **Notes:** {{ fedramp.notes }}
{% endif %}{% endif -%}
{% if cmmc -%}
### CMMC Assessment
- **Level {{ cmmc.level }}** / {{ cmmc.practice_id }} ({{ cmmc.domain }}) — {{ cmmc.status | replace('_', ' ') | title }}
{% if cmmc.evidence_description %}- **Evidence:** {{ cmmc.evidence_description }}
{% endif %}{% endif -%}
{% if audit_events -%}
### Supporting Audit Evidence
{% for evt in audit_events -%}
- **{{ evt.event_type }}** by *{{ evt.actor }}* on {{ evt.created_at }} — {{ evt.details }}
{% endfor %}{% endif -%}
{% if last_assessed %}*Last assessed: {{ last_assessed }}*{% endif %}
"""


# -- Fallback templates for document-level narratives ---------------------

_FALLBACK_SSP_SECTION = """\
## {control_id}: {control_name}

### Implementation Status: {status}

{description}

**Implementation Details:**
{implementation_narrative}

**Responsible Role:** {responsible_role}
**Assessment Date:** {assessment_date}
"""

_FALLBACK_POAM_MILESTONE = """\
### POA&M Item: {poam_id}

**Control:** {control_id} -- {control_name}
**Status:** {status}
**Risk Level:** {risk_level}

**Weakness Description:**
{weakness_description}

**Remediation Plan:**
{remediation_plan}

**Milestones:**
{milestones_text}

**Responsible Party:** {responsible_party}
**Scheduled Completion:** {scheduled_completion}
"""

_FALLBACK_EXECUTIVE_SUMMARY = """\
# Compliance Executive Summary

**Project:** {project_name}
**Date:** {report_date}
**Overall Posture:** {overall_posture}

## Framework Status

{framework_sections}

## Risk Assessment

{risk_narrative}

## Priority Recommendations

{recommendations_text}
"""

REMEDIATION_WINDOWS = {
    "critical": 15, "high": 30, "moderate": 90, "medium": 90, "low": 180,
}

CONTROL_FAMILIES = {
    "AC": "Access Control", "AT": "Awareness and Training",
    "AU": "Audit and Accountability",
    "CA": "Assessment, Authorization, and Monitoring",
    "CM": "Configuration Management", "CP": "Contingency Planning",
    "IA": "Identification and Authentication", "IR": "Incident Response",
    "MA": "Maintenance", "MP": "Media Protection",
    "PE": "Physical and Environmental Protection", "PL": "Planning",
    "PS": "Personnel Security", "RA": "Risk Assessment",
    "SA": "System and Services Acquisition",
    "SC": "System and Communications Protection",
    "SI": "System and Information Integrity",
}


def _render_template_str(template_str: str, context: dict) -> str:
    """Render a Jinja2 template string with graceful fallback."""
    if Template is not None:
        return Template(template_str).render(**context)
    result = template_str
    for key, value in context.items():
        if isinstance(value, (str, int, float)):
            result = result.replace("{{ " + key + " }}", str(value))
    return result


def _load_template_file(filename: str) -> Optional[str]:
    """Load a Jinja2 template file from the narrative_templates directory."""
    path = TEMPLATE_DIR / filename
    if path.exists():
        return path.read_text(encoding="utf-8")
    return None


@dataclass
class ControlEvidence:
    """Evidence bundle gathered for a single control."""
    control_id: str = ""
    control_title: str = ""
    control_description: str = ""
    implementation_status: str = ""
    implementation_description: str = ""
    responsible_role: str = ""
    evidence_path: str = ""
    last_assessed: str = ""
    fedramp: Optional[Dict] = None
    cmmc: Optional[Dict] = None
    audit_events: List[Dict] = field(default_factory=list)


@dataclass
class NarrativeResult:
    """Result of a single narrative generation."""
    project_id: str
    control_id: str
    narrative: str
    method: str = "template"
    generated_at: str = ""
    stored: bool = False


class NarrativeGenerator:
    """Generate and store control narratives for SSP Section 13."""

    def __init__(self, db_path: Optional[str] = None):
        self._db_path = Path(db_path) if db_path else DB_PATH
        if not self._db_path.exists():
            raise FileNotFoundError(
                f"Database not found: {self._db_path}\n"
                "Run: python tools/db/init_icdev_db.py"
            )
        self._ensure_table()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_table(self) -> None:
        """Create control_narratives table if it does not exist."""
        conn = self._conn()
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS control_narratives (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id TEXT NOT NULL,
                    control_id TEXT NOT NULL,
                    narrative_text TEXT NOT NULL,
                    generation_method TEXT DEFAULT 'template',
                    generated_at TEXT DEFAULT (datetime('now')),
                    UNIQUE(project_id, control_id)
                )
            """)
            conn.commit()
        finally:
            conn.close()

    def _verify_project(self, conn: sqlite3.Connection, project_id: str) -> None:
        row = conn.execute(
            "SELECT id FROM projects WHERE id = ?", (project_id,)
        ).fetchone()
        if not row:
            raise ValueError(f"Project '{project_id}' not found in database.")

    def gather_evidence(self, project_id: str, control_id: str) -> ControlEvidence:
        """Query project_controls, audit_trail, fedramp/cmmc assessments."""
        conn = self._conn()
        try:
            self._verify_project(conn, project_id)
            ev = ControlEvidence(control_id=control_id)

            row = conn.execute(
                "SELECT title, description FROM compliance_controls WHERE id = ?",
                (control_id,),
            ).fetchone()
            if row:
                ev.control_title = row["title"] or ""
                ev.control_description = row["description"] or ""
            row = conn.execute(
                """SELECT implementation_status, implementation_description,
                          responsible_role, evidence_path, last_assessed
                   FROM project_controls
                   WHERE project_id = ? AND control_id = ?""",
                (project_id, control_id),
            ).fetchone()
            if row:
                for col in ("implementation_status", "implementation_description",
                            "responsible_role", "evidence_path", "last_assessed"):
                    setattr(ev, col, row[col] or "")
            row = conn.execute(
                """SELECT baseline, status, evidence_description, notes
                   FROM fedramp_assessments
                   WHERE project_id = ? AND control_id = ?
                   ORDER BY assessment_date DESC LIMIT 1""",
                (project_id, control_id),
            ).fetchone()
            if row:
                ev.fedramp = dict(row)
            row = conn.execute(
                """SELECT level, practice_id, domain, status, evidence_description
                   FROM cmmc_assessments
                   WHERE project_id = ? AND nist_171_id LIKE ?
                   ORDER BY assessment_date DESC LIMIT 1""",
                (project_id, f"%{control_id}%"),
            ).fetchone()
            if row:
                ev.cmmc = dict(row)
            rows = conn.execute(
                """SELECT event_type, actor, details, created_at
                   FROM audit_trail WHERE project_id = ? AND event_type IN (
                       'compliance_check', 'fedramp_assessed', 'cmmc_assessed',
                       'stig_checked', 'crosswalk_mapped', 'cato_evidence_collected')
                   ORDER BY created_at DESC LIMIT 10""",
                (project_id,),
            ).fetchall()
            ev.audit_events = [dict(r) for r in rows]

            return ev
        finally:
            conn.close()

    def render_narrative(
        self, project_id: str, control_id: str, template_path: Optional[str] = None,
    ) -> str:
        """Render a Jinja2 template with gathered evidence."""
        if Template is None:
            raise ImportError(
                "jinja2 is required for narrative generation. "
                "Install with: pip install jinja2"
            )
        evidence = self.gather_evidence(project_id, control_id)
        tpl_text = (
            Path(template_path).read_text(encoding="utf-8")
            if template_path
            else DEFAULT_TEMPLATE
        )
        return Template(tpl_text).render(**asdict(evidence))

    def generate_for_project(
        self, project_id: str, control_ids: Optional[List[str]] = None,
        use_llm: bool = False,
    ) -> List[NarrativeResult]:
        """Generate narratives for all controls (or a specified list)."""
        conn = self._conn()
        try:
            self._verify_project(conn, project_id)
            if control_ids:
                ids = control_ids
            else:
                rows = conn.execute(
                    "SELECT control_id FROM project_controls WHERE project_id = ?",
                    (project_id,),
                ).fetchall()
                ids = [r["control_id"] for r in rows]
        finally:
            conn.close()

        if not ids:
            return []

        results: List[NarrativeResult] = []
        for cid in ids:
            narrative_text = self.render_narrative(project_id, cid)
            method = "template"
            if use_llm:
                refined = self._refine_with_llm(narrative_text, cid)
                if refined:
                    narrative_text = refined
                    method = "llm"
            self.store_narrative(project_id, cid, narrative_text, method=method)
            results.append(NarrativeResult(
                project_id=project_id, control_id=cid,
                narrative=narrative_text, method=method,
                generated_at=datetime.now(timezone.utc).isoformat(), stored=True,
            ))
        return results

    def store_narrative(
        self, project_id: str, control_id: str,
        narrative_text: str, method: str = "template",
    ) -> None:
        """Persist a narrative in the control_narratives table."""
        conn = self._conn()
        try:
            conn.execute("""
                INSERT INTO control_narratives
                    (project_id, control_id, narrative_text, generation_method)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(project_id, control_id) DO UPDATE SET
                    narrative_text = excluded.narrative_text,
                    generation_method = excluded.generation_method,
                    generated_at = datetime('now')
            """, (project_id, control_id, narrative_text, method))
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _refine_with_llm(raw_narrative: str, control_id: str) -> Optional[str]:
        """Pass template output through LLM router for SSP-quality prose."""
        try:
            from tools.llm.router import LLMRouter
            from tools.llm.provider import LLMRequest

            router = LLMRouter()
            req = LLMRequest(
                messages=[{
                    "role": "user",
                    "content": (
                        "You are a government cybersecurity professional writing "
                        "an SSP Section 13 control narrative. Rewrite the "
                        "following draft into formal, concise SSP prose suitable "
                        "for an ISSO or AO review. Preserve all factual content. "
                        "Do not add information that is not present. "
                        f"Control: {control_id}\n\nDraft:\n{raw_narrative}"
                    ),
                }],
                system_prompt=(
                    "Output only the rewritten narrative. "
                    "Use third-person present tense. "
                    "Reference the information system as 'the system'."
                ),
                max_tokens=2048,
                temperature=0.3,
            )
            resp = router.invoke("narrative_generation", req)
            if resp and resp.content:
                return resp.content.strip()
        except Exception:
            pass  # Graceful degradation -- fall back to template output
        return None

    # -----------------------------------------------------------------
    # Database helpers for document-level narratives
    # -----------------------------------------------------------------

    def _get_project_data(self, project_id: str) -> dict:
        """Load project info from DB."""
        conn = self._conn()
        try:
            self._verify_project(conn, project_id)
            row = conn.execute(
                "SELECT * FROM projects WHERE id = ?", (project_id,)
            ).fetchone()
            return dict(row) if row else {}
        finally:
            conn.close()

    def _get_control_implementations(self, project_id: str) -> list:
        """Load control implementation status from project_controls table."""
        conn = self._conn()
        try:
            rows = conn.execute(
                "SELECT * FROM project_controls WHERE project_id = ? "
                "ORDER BY control_id", (project_id,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def _get_open_findings(self, project_id: str) -> list:
        """Load open/partially_satisfied findings."""
        conn = self._conn()
        try:
            results = []
            try:
                rows = conn.execute(
                    """SELECT id, weakness_id, weakness_description, severity,
                              source, control_id, status, corrective_action,
                              milestone_date, responsible_party
                       FROM poam_items
                       WHERE project_id = ? AND status IN ('open', 'in_progress')
                       ORDER BY CASE severity WHEN 'critical' THEN 0
                           WHEN 'high' THEN 1 WHEN 'moderate' THEN 2
                           WHEN 'low' THEN 3 END, milestone_date""",
                    (project_id,),
                ).fetchall()
                results = [dict(r) for r in rows]
            except sqlite3.OperationalError:
                pass
            if not results:
                try:
                    rows = conn.execute(
                        """SELECT control_id, implementation_status,
                                  implementation_description, responsible_role
                           FROM project_controls
                           WHERE project_id = ?
                             AND implementation_status IN (
                                 'planned', 'partially_implemented')
                           ORDER BY control_id""",
                        (project_id,),
                    ).fetchall()
                    for r in rows:
                        d = dict(r)
                        severity = ("high" if d["implementation_status"] == "planned"
                                    else "moderate")
                        results.append({
                            "weakness_id": f"CTRL-{d['control_id']}",
                            "weakness_description": (
                                d.get("implementation_description")
                                or f"Control {d['control_id']} is "
                                   f"{d['implementation_status']}"),
                            "severity": severity,
                            "source": "project_controls",
                            "control_id": d["control_id"],
                            "status": "open",
                            "corrective_action": f"Complete implementation of {d['control_id']}",
                            "milestone_date": None,
                            "responsible_party": d.get("responsible_role", "ISSO"),
                        })
                except sqlite3.OperationalError:
                    pass
            return results
        finally:
            conn.close()

    def _get_framework_status(self, project_id: str) -> list:
        """Load per-framework compliance status."""
        conn = self._conn()
        try:
            try:
                rows = conn.execute(
                    """SELECT framework_id, total_controls,
                              implemented_count AS implemented_controls,
                              coverage_pct, gate_status, last_assessed
                       FROM project_framework_status WHERE project_id = ?
                       ORDER BY framework_id""",
                    (project_id,),
                ).fetchall()
                return [dict(r) for r in rows]
            except sqlite3.OperationalError:
                return []
        finally:
            conn.close()

    def _get_control_title(self, conn: sqlite3.Connection, control_id: str) -> str:
        """Look up a control title from compliance_controls."""
        try:
            row = conn.execute(
                "SELECT title FROM compliance_controls WHERE id = ?",
                (control_id,),
            ).fetchone()
            return row["title"] if row else control_id
        except sqlite3.OperationalError:
            return control_id

    # -----------------------------------------------------------------
    # Document-level narrative methods
    # -----------------------------------------------------------------

    def generate_ssp_narrative(
        self, project_id: str, framework: str = "nist_800_53",
    ) -> dict:
        """Generate SSP prose sections from assessment data.

        Returns dict with sections:
        - system_description: System name, purpose, boundaries
        - information_types: Data categories and sensitivity
        - security_controls: Per-control implementation narrative
        - authorization_boundary: Scope and interfaces
        - continuous_monitoring: Monitoring strategy
        """
        project = self._get_project_data(project_id)
        controls = self._get_control_implementations(project_id)
        conn = self._conn()
        try:
            system_name = project.get("name", "Unknown System")
            system_type = project.get("type", "webapp")
            description = project.get("description", "")
            impact_level = project.get("impact_level", "IL5")
            classification = project.get("classification", "CUI")

            system_description = (
                f"The {system_name} is a {system_type} information system "
                f"operating at impact level {impact_level} with "
                f"{classification} classification. ")
            if description:
                system_description += f"{description} "
            system_description += (
                f"The system is deployed on "
                f"{project.get('cloud_environment', 'AWS GovCloud')} "
                f"and is currently in {project.get('status', 'active')} status.")

            info_types = (
                f"The system processes {classification} data at the "
                f"{impact_level} impact level. Data categories include "
                f"Controlled Technical Information (CTI) as indicated "
                f"by the system classification designation.")

            control_sections = []
            template_str = _load_template_file("ssp_section.j2")
            for ctrl in controls:
                ctrl_id = ctrl.get("control_id", "")
                ctrl_name = self._get_control_title(conn, ctrl_id)
                status = ctrl.get("implementation_status", "not_assessed")
                impl_desc = ctrl.get("implementation_description", "")
                role = ctrl.get("responsible_role",
                                "Information System Security Officer (ISSO)")
                assessed = ctrl.get("last_assessed", "Not assessed")
                narrative = impl_desc or f"Implementation of {ctrl_id} is {status}."
                family_prefix = ctrl_id.split("-")[0] if "-" in ctrl_id else ""
                ctx = {
                    "control_id": ctrl_id,
                    "control_name": ctrl_name,
                    "status": status.replace("_", " ").title(),
                    "description": (f"This control belongs to the "
                                    f"{CONTROL_FAMILIES.get(family_prefix, '')} family."),
                    "implementation_narrative": narrative,
                    "responsible_role": role,
                    "assessment_date": assessed or "Not assessed",
                }
                if template_str and Template is not None:
                    section = _render_template_str(template_str, ctx)
                else:
                    section = _FALLBACK_SSP_SECTION.format(**ctx)
                control_sections.append(section)

            auth_boundary = (
                f"The authorization boundary for {system_name} encompasses "
                f"all components deployed within the "
                f"{project.get('cloud_environment', 'AWS GovCloud')} "
                f"environment. The boundary includes application servers, "
                f"databases, and supporting infrastructure operating at "
                f"{impact_level}.")

            total_controls = len(controls)
            implemented = sum(1 for c in controls
                              if c.get("implementation_status") == "implemented")
            continuous_monitoring = (
                f"Continuous monitoring is maintained through automated "
                f"compliance scanning, STIG verification, and periodic "
                f"control assessments. Currently {implemented} of "
                f"{total_controls} controls are fully implemented. "
                f"The organization conducts ongoing assessment of "
                f"security controls per NIST SP 800-137 guidelines.")

            return {
                "status": "success",
                "project_id": project_id,
                "framework": framework,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "sections": {
                    "system_description": system_description,
                    "information_types": info_types,
                    "security_controls": control_sections,
                    "authorization_boundary": auth_boundary,
                    "continuous_monitoring": continuous_monitoring,
                },
                "statistics": {
                    "total_controls": total_controls,
                    "implemented": implemented,
                    "coverage_pct": round(
                        (implemented / total_controls * 100)
                        if total_controls > 0 else 0, 1),
                },
            }
        finally:
            conn.close()

    def generate_poam_narrative(self, project_id: str) -> dict:
        """Generate POAM milestone descriptions from open findings.

        Returns dict with:
        - executive_summary: Number of findings, risk overview
        - milestones: List of milestone dicts
        """
        project = self._get_project_data(project_id)
        findings = self._get_open_findings(project_id)
        conn = self._conn()
        try:
            now = datetime.now(timezone.utc)
            system_name = project.get("name", "Unknown System")
            milestones = []
            template_str = _load_template_file("poam_milestone.j2")
            severity_counts: Dict[str, int] = {
                "critical": 0, "high": 0, "moderate": 0, "low": 0}

            for idx, finding in enumerate(findings, start=1):
                severity = finding.get("severity", "moderate")
                severity_counts[severity] = severity_counts.get(severity, 0) + 1
                ctrl_id = finding.get("control_id", "N/A")
                ctrl_name = (self._get_control_title(conn, ctrl_id)
                             if ctrl_id != "N/A" else "N/A")
                weakness = finding.get("weakness_description",
                                       f"Control {ctrl_id} requires remediation.")
                remediation = finding.get(
                    "corrective_action",
                    f"Complete implementation of {ctrl_id} to satisfy requirements.")
                target_days = REMEDIATION_WINDOWS.get(severity, 90)
                target_date = (now + timedelta(days=target_days)).strftime("%Y-%m-%d")
                milestone_date = finding.get("milestone_date") or target_date

                milestone_entry = {
                    "poam_id": f"POAM-{idx:04d}",
                    "control_id": ctrl_id,
                    "control_name": ctrl_name,
                    "status": finding.get("status", "open").replace("_", " ").title(),
                    "risk_level": severity.title(),
                    "weakness_description": weakness,
                    "remediation_plan": remediation,
                    "milestones": [{"description": f"Remediate {ctrl_id} to satisfied",
                                    "target_date": milestone_date}],
                    "responsible_party": finding.get("responsible_party", "ISSO"),
                    "scheduled_completion": milestone_date,
                }
                milestones_text = "\n".join(
                    f"- {m['description']} (Target: {m['target_date']})"
                    for m in milestone_entry["milestones"])
                if template_str and Template is not None:
                    ctx = dict(milestone_entry)
                    ctx["milestones_text"] = milestones_text
                    milestone_entry["narrative"] = _render_template_str(template_str, ctx)
                else:
                    fmt_ctx = {k: v for k, v in milestone_entry.items()
                               if k not in ("milestones", "narrative")}
                    fmt_ctx["milestones_text"] = milestones_text
                    milestone_entry["narrative"] = _FALLBACK_POAM_MILESTONE.format(**fmt_ctx)
                milestones.append(milestone_entry)

            total_findings = len(findings)
            risk_level = "Low"
            if severity_counts.get("critical", 0) > 0:
                risk_level = "Critical"
            elif severity_counts.get("high", 0) > 0:
                risk_level = "High"
            elif severity_counts.get("moderate", 0) > 0:
                risk_level = "Moderate"

            executive_summary = (
                f"The Plan of Action and Milestones (POA&M) for {system_name} "
                f"identifies {total_findings} open finding(s) requiring remediation. "
                f"Overall risk level: {risk_level}. ")
            if severity_counts.get("critical", 0) > 0:
                executive_summary += (
                    f"{severity_counts['critical']} critical finding(s) require "
                    f"immediate remediation within {REMEDIATION_WINDOWS['critical']} days. ")
            if severity_counts.get("high", 0) > 0:
                executive_summary += (
                    f"{severity_counts['high']} high-severity finding(s) require "
                    f"remediation within {REMEDIATION_WINDOWS['high']} days. ")

            return {
                "status": "success",
                "project_id": project_id,
                "generated_at": now.isoformat(),
                "executive_summary": executive_summary,
                "severity_counts": severity_counts,
                "total_findings": total_findings,
                "risk_level": risk_level,
                "milestones": milestones,
            }
        finally:
            conn.close()

    def generate_executive_summary(self, project_id: str) -> dict:
        """Generate executive summary across all active compliance frameworks.

        Returns dict with:
        - overall_posture: Overall compliance status
        - framework_summaries: Per-framework status + key findings
        - risk_assessment: Aggregate risk level
        - recommendations: Top priority actions
        """
        project = self._get_project_data(project_id)
        controls = self._get_control_implementations(project_id)
        framework_status = self._get_framework_status(project_id)
        findings = self._get_open_findings(project_id)

        now = datetime.now(timezone.utc)
        system_name = project.get("name", "Unknown System")
        report_date = now.strftime("%Y-%m-%d")
        total_controls = len(controls)
        implemented = sum(1 for c in controls
                          if c.get("implementation_status") == "implemented")
        partial = sum(1 for c in controls
                      if c.get("implementation_status") == "partially_implemented")
        overall_pct = round(
            (implemented / total_controls * 100) if total_controls > 0 else 0, 1)

        if overall_pct >= 95:
            overall_posture = "Strong"
        elif overall_pct >= 80:
            overall_posture = "Satisfactory"
        elif overall_pct >= 50:
            overall_posture = "Needs Improvement"
        else:
            overall_posture = "At Risk"

        _fw_names = {
            "fedramp_moderate": "FedRAMP Moderate", "fedramp_high": "FedRAMP High",
            "nist_800_171": "NIST 800-171", "cmmc_level_2": "CMMC Level 2",
            "cmmc_level_3": "CMMC Level 3", "cjis": "CJIS Security Policy",
            "hipaa": "HIPAA Security Rule", "hitrust": "HITRUST CSF v11",
            "soc2": "SOC 2 Type II", "pci_dss": "PCI DSS v4.0",
            "iso_27001": "ISO/IEC 27001:2022",
            "nist_800_207": "NIST SP 800-207 (ZTA)", "mosa": "DoD MOSA",
        }

        frameworks = []
        for fw in framework_status:
            fw_id = fw.get("framework_id", "")
            fw_total = fw.get("total_controls", 0)
            fw_impl = fw.get("implemented_controls", 0)
            fw_pct = fw.get("coverage_pct", 0)
            fw_risk = "Low" if fw_pct >= 80 else ("Moderate" if fw_pct >= 50 else "High")
            frameworks.append({
                "framework_id": fw_id,
                "name": _fw_names.get(fw_id, fw_id),
                "total_controls": fw_total,
                "satisfied": fw_impl,
                "satisfied_pct": round(fw_pct, 1),
                "partial": 0,
                "not_satisfied": fw_total - fw_impl,
                "risk_level": fw_risk,
                "gate_status": fw.get("gate_status", "unknown"),
            })
        if not frameworks:
            frameworks.append({
                "framework_id": "nist_800_53", "name": "NIST 800-53 Rev 5",
                "total_controls": total_controls, "satisfied": implemented,
                "satisfied_pct": overall_pct, "partial": partial,
                "not_satisfied": total_controls - implemented - partial,
                "risk_level": ("Low" if overall_pct >= 80
                               else ("Moderate" if overall_pct >= 50 else "High")),
                "gate_status": "compliant" if overall_pct >= 100 else "in_progress",
            })

        open_findings = len(findings)
        risk_narrative = (
            f"The {system_name} has {total_controls} security controls mapped, "
            f"of which {implemented} ({overall_pct}%) are fully implemented. ")
        if open_findings > 0:
            risk_narrative += (
                f"There are {open_findings} open finding(s) tracked in the POA&M. ")
        if overall_pct >= 80:
            risk_narrative += (
                "The overall risk posture is acceptable for continued "
                "operation under current authorization.")
        else:
            risk_narrative += (
                "The organization should prioritize remediation of open "
                "findings to improve the security posture to an acceptable level.")

        recommendations = []
        if open_findings > 0:
            recommendations.append({
                "title": "Remediate Open Findings",
                "description": f"Address {open_findings} open POA&M item(s) "
                               f"within their scheduled remediation windows."})
        if partial > 0:
            recommendations.append({
                "title": "Complete Partial Implementations",
                "description": f"Finalize {partial} partially implemented control(s) "
                               f"to achieve full compliance."})
        not_impl = total_controls - implemented - partial
        if not_impl > 0:
            recommendations.append({
                "title": "Implement Planned Controls",
                "description": f"Begin implementation of {not_impl} planned control(s) "
                               f"to increase overall coverage."})
        for fw in frameworks:
            if fw.get("satisfied_pct", 0) < 80:
                recommendations.append({
                    "title": f"Improve {fw['name']} Coverage",
                    "description": (f"{fw['name']} coverage is at {fw['satisfied_pct']}%. "
                                    f"Target 80% minimum for gate passage.")})
        if not recommendations:
            recommendations.append({
                "title": "Maintain Compliance Posture",
                "description": "Continue continuous monitoring and periodic assessments "
                               "to sustain the current compliance posture."})

        template_str = _load_template_file("executive_summary.j2")
        if template_str and Template is not None:
            rendered_narrative = _render_template_str(template_str, {
                "project_name": system_name, "report_date": report_date,
                "overall_posture": overall_posture, "frameworks": frameworks,
                "risk_narrative": risk_narrative, "recommendations": recommendations,
            })
        else:
            fw_sections = []
            for fw in frameworks:
                fw_sections.append(
                    f"### {fw['name']}\n"
                    f"- **Controls Assessed:** {fw['total_controls']}\n"
                    f"- **Satisfied:** {fw['satisfied']} ({fw['satisfied_pct']}%)\n"
                    f"- **Not Satisfied:** {fw['not_satisfied']}\n"
                    f"- **Risk Level:** {fw['risk_level']}\n")
            recs_text = "\n".join(
                f"{i}. **{r['title']}** -- {r['description']}"
                for i, r in enumerate(recommendations, 1))
            rendered_narrative = _FALLBACK_EXECUTIVE_SUMMARY.format(
                project_name=system_name, report_date=report_date,
                overall_posture=overall_posture,
                framework_sections="\n".join(fw_sections),
                risk_narrative=risk_narrative, recommendations_text=recs_text)

        return {
            "status": "success", "project_id": project_id,
            "generated_at": now.isoformat(), "overall_posture": overall_posture,
            "rendered_narrative": rendered_narrative,
            "framework_summaries": frameworks,
            "risk_assessment": {
                "total_controls": total_controls, "implemented": implemented,
                "partial": partial, "coverage_pct": overall_pct,
                "open_findings": open_findings,
                "risk_level": ("High" if overall_pct < 50
                               else ("Moderate" if overall_pct < 80 else "Low")),
            },
            "risk_narrative": risk_narrative,
            "recommendations": recommendations,
        }


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Generate compliance narratives for ATO packages. "
                    "Supports per-control (--control/--all) and "
                    "document-level (--type ssp|poam|executive) modes.")
    p.add_argument("--project-id", required=True, help="Project identifier")
    p.add_argument("--control", help="Single control ID (e.g. AC-2)")
    p.add_argument("--all", action="store_true", help="Generate for all project controls")
    p.add_argument("--use-llm", action="store_true", help="Refine output via LLM router")
    p.add_argument("--type", dest="narrative_type", choices=["ssp", "poam", "executive"],
                    help="Document-level narrative type")
    p.add_argument("--framework", default="nist_800_53",
                    help="Compliance framework for SSP narrative (default: nist_800_53)")
    p.add_argument("--json", action="store_true", help="JSON output")
    p.add_argument("--output-dir", help="Write individual narrative files to directory")
    p.add_argument("--db-path", help="Path to icdev.db (default: data/icdev.db)")
    return p


def _print_document_narrative(narrative_type: str, result: dict) -> None:
    """Pretty-print a document-level narrative result."""
    print(f"{'=' * 65}")
    print(f"  Narrative: {narrative_type.upper()}")
    print(f"  Project: {result.get('project_id', 'unknown')}")
    print(f"  Generated: {result.get('generated_at', '')}")
    print(f"{'=' * 65}")
    if narrative_type == "ssp":
        stats = result.get("statistics", {})
        print(f"\n  Controls: {stats.get('total_controls', 0)} total, "
              f"{stats.get('implemented', 0)} implemented "
              f"({stats.get('coverage_pct', 0)}%)\n")
        for name, content in result.get("sections", {}).items():
            print(f"\n--- {name.replace('_', ' ').title()} ---\n")
            if isinstance(content, list):
                for item in content[:5]:
                    print(item)
                if len(content) > 5:
                    print(f"\n  ... and {len(content) - 5} more control(s)")
            else:
                print(content)
    elif narrative_type == "poam":
        print(f"\n  Findings: {result.get('total_findings', 0)}")
        print(f"  Risk Level: {result.get('risk_level', 'Unknown')}")
        print(f"\n{result.get('executive_summary', '')}\n")
        for m in result.get("milestones", [])[:10]:
            print(f"\n  {m['poam_id']}: {m['control_id']} "
                  f"({m['risk_level']}) -- {m['scheduled_completion']}")
    elif narrative_type == "executive":
        print(f"\n  Overall Posture: {result.get('overall_posture', 'Unknown')}")
        rendered = result.get("rendered_narrative", "")
        if rendered:
            print(f"\n{rendered}")
    print(f"\n{'=' * 65}")


def main(argv: Optional[List[str]] = None) -> int:
    args = _build_parser().parse_args(argv)

    # Document-level narrative mode
    if args.narrative_type:
        try:
            gen = NarrativeGenerator(db_path=args.db_path)
            if args.narrative_type == "ssp":
                result = gen.generate_ssp_narrative(
                    args.project_id, framework=args.framework)
            elif args.narrative_type == "poam":
                result = gen.generate_poam_narrative(args.project_id)
            else:
                result = gen.generate_executive_summary(args.project_id)
        except (FileNotFoundError, ValueError) as e:
            if args.json:
                print(json.dumps({"status": "error", "error": str(e)}))
            else:
                print(f"Error: {e}", file=sys.stderr)
            return 1
        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            _print_document_narrative(args.narrative_type, result)
        return 0

    # Per-control narrative mode (original)
    if not args.control and not args.all:
        print("Error: specify --control <ID>, --all, or --type <ssp|poam|executive>",
              file=sys.stderr)
        return 1

    gen = NarrativeGenerator(db_path=args.db_path)
    control_ids = [args.control] if args.control else None
    results = gen.generate_for_project(
        project_id=args.project_id,
        control_ids=control_ids,
        use_llm=args.use_llm,
    )

    if args.output_dir:
        out = Path(args.output_dir)
        out.mkdir(parents=True, exist_ok=True)
        for r in results:
            (out / f"{r.control_id}.md").write_text(r.narrative, encoding="utf-8")

    if args.json:
        payload = {
            "status": "success",
            "project_id": args.project_id,
            "narratives_generated": len(results),
            "method": "llm" if args.use_llm else "template",
            "results": [asdict(r) for r in results],
        }
        print(json.dumps(payload, indent=2))
    else:
        if not results:
            print(f"No controls found for project '{args.project_id}'.")
        for r in results:
            print(f"\n{'=' * 60}")
            print(f"Control: {r.control_id}  |  Method: {r.method}")
            print(f"{'=' * 60}")
            print(r.narrative)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
