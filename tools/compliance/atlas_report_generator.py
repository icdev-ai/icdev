#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""MITRE ATLAS v5.4.0 compliance report generator.

Loads ATLAS assessment data from atlas_assessments table, builds mitigation
coverage analysis, technique exposure, OWASP LLM Top 10 cross-reference,
gap analysis, remediation recommendations, and NIST 800-53 control mapping.
Generates a comprehensive ATLAS compliance report with CUI markings.

Usage:
    python tools/compliance/atlas_report_generator.py --project-id proj-123
    python tools/compliance/atlas_report_generator.py --project-id proj-123 --json
    python tools/compliance/atlas_report_generator.py --project-id proj-123 \\
        --output-path /path/to/output
    python tools/compliance/atlas_report_generator.py --project-id proj-123 --human

Databases:
    - data/icdev.db: atlas_assessments, projects, audit_trail

See also:
    - tools/compliance/atlas_assessor.py (assessment engine)
    - tools/compliance/cmmc_report_generator.py (pattern reference)
    - context/compliance/atlas_mitigations.json (mitigation catalog)
    - context/compliance/owasp_llm_top10.json (OWASP LLM cross-ref)
"""

import argparse
import json
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"
CATALOG_DIR = BASE_DIR / "context" / "compliance"
MITIGATIONS_PATH = CATALOG_DIR / "atlas_mitigations.json"
TECHNIQUES_PATH = CATALOG_DIR / "atlas_techniques.json"
OWASP_PATH = CATALOG_DIR / "owasp_llm_top10.json"

# Classification manager import (graceful fallback)
try:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from classification_manager import ClassificationManager
    _cm = ClassificationManager()
except Exception:
    _cm = None

# ATLAS mitigation categories for grouping
MITIGATION_CATEGORIES = [
    {"code": "access_control", "name": "Access Control"},
    {"code": "data_protection", "name": "Data Protection"},
    {"code": "model_security", "name": "Model Security"},
    {"code": "monitoring", "name": "Monitoring & Detection"},
    {"code": "deployment", "name": "Deployment Security"},
    {"code": "supply_chain", "name": "Supply Chain"},
    {"code": "governance", "name": "AI Governance"},
]

CATEGORY_NAMES = {c["code"]: c["name"] for c in MITIGATION_CATEGORIES}

# Valid assessment statuses (BaseAssessor pattern)
VALID_STATUSES = (
    "not_assessed", "satisfied", "partially_satisfied",
    "not_satisfied", "not_applicable", "risk_accepted",
)

# OWASP LLM Top 10 to ATLAS mitigation mapping
# Maps OWASP risk codes to the ATLAS mitigations that address them
OWASP_TO_ATLAS_MAP = {
    "prompt_injection": ["AML.M0015", "AML.M0026", "AML.M0013"],
    "sensitive_info_disclosure": ["AML.M0000", "AML.M0001", "AML.M0005"],
    "supply_chain": ["AML.M0013", "AML.M0005", "AML.M0014"],
    "data_model_poisoning": ["AML.M0007", "AML.M0008", "AML.M0009"],
    "improper_output_handling": ["AML.M0015", "AML.M0002", "AML.M0010"],
    "excessive_agency": ["AML.M0026", "AML.M0019", "AML.M0001"],
    "system_prompt_leakage": ["AML.M0015", "AML.M0000", "AML.M0005"],
    "vector_embedding_weaknesses": ["AML.M0007", "AML.M0005", "AML.M0012"],
    "misinformation": ["AML.M0008", "AML.M0002", "AML.M0024"],
    "unbounded_consumption": ["AML.M0004", "AML.M0001", "AML.M0024"],
}


# ---------------------------------------------------------------------------
# Report template (inline — no external template file required)
# ---------------------------------------------------------------------------

REPORT_TEMPLATE = """{{cui_banner_top}}

# MITRE ATLAS Compliance Report

**System Name:** {{system_name}}
**Project ID:** {{project_id}}
**Impact Level:** {{impact_level}}
**Framework Version:** ATLAS v5.4.0
**Assessment Date:** {{assessment_date}}
**Report Version:** {{version}}
**Assessor:** {{assessor}}

---

## 1. Executive Summary

{{executive_summary}}

---

## 2. Mitigation Coverage Analysis

{{mitigation_coverage}}

---

## 3. Technique Exposure Analysis

{{technique_exposure}}

---

## 4. OWASP LLM Top 10 Cross-Reference

{{owasp_crossref}}

---

## 5. Gap Analysis

{{gap_analysis}}

---

## 6. Remediation Recommendations

{{remediation_recommendations}}

---

## 7. NIST 800-53 Control Mapping

{{nist_mapping}}

---

**Prepared by:** {{assessor}}
**Date:** {{assessment_date}}
**Classification:** {{classification}}

{{cui_banner_bottom}}
"""


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def _get_connection(db_path: Optional[Path] = None) -> sqlite3.Connection:
    """Get a database connection with Row factory."""
    path = db_path or DB_PATH
    if not Path(path).exists():
        raise FileNotFoundError(
            f"Database not found: {path}\n"
            "Run: python tools/db/init_icdev_db.py"
        )
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _substitute_variables(template: str, variables: Dict) -> str:
    """Replace {{variable_name}} placeholders in the template."""
    def replacer(match):
        key = match.group(1).strip()
        return str(variables.get(key, match.group(0)))
    return re.sub(r"\{\{(\w+)\}\}", replacer, template)


def _load_cui_config() -> Dict:
    """Load CUI marking configuration with graceful fallback."""
    if _cm:
        try:
            markings = _cm.get_markings("CUI")
            return {
                "banner_top": markings.get("banner_top", "CUI // SP-CTI"),
                "banner_bottom": markings.get("banner_bottom", "CUI // SP-CTI"),
                "document_header": markings.get("document_header", "CUI // SP-CTI"),
                "document_footer": markings.get("document_footer", "CUI // SP-CTI"),
            }
        except Exception:
            pass

    # Fallback to cui_marker import
    try:
        from tools.compliance.cui_marker import load_cui_config as _load
        return _load()
    except Exception:
        pass

    return {
        "banner_top": "CUI // SP-CTI",
        "banner_bottom": "CUI // SP-CTI",
        "document_header": (
            "////////////////////////////////////////////////////////////////////\n"
            "CONTROLLED UNCLASSIFIED INFORMATION (CUI) // SP-CTI\n"
            "Distribution: Distribution D -- Authorized DoD Personnel Only\n"
            "////////////////////////////////////////////////////////////////////"
        ),
        "document_footer": (
            "////////////////////////////////////////////////////////////////////\n"
            "CUI // SP-CTI | Department of Defense\n"
            "////////////////////////////////////////////////////////////////////"
        ),
    }


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def _load_catalog(catalog_path: Path) -> List[Dict]:
    """Load a JSON catalog file. Returns empty list if unavailable."""
    if not catalog_path.exists():
        return []
    try:
        with open(catalog_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return (
            data.get("mitigations")
            or data.get("techniques")
            or data.get("requirements")
            or data.get("controls")
            or data.get("risk_categories")
            or []
        )
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        print(
            f"Warning: Could not load catalog {catalog_path.name}: {exc}",
            file=sys.stderr,
        )
        return []


def _load_owasp_catalog() -> List[Dict]:
    """Load OWASP LLM Top 10 catalog."""
    return _load_catalog(OWASP_PATH)


# ---------------------------------------------------------------------------
# ATLASReportGenerator class
# ---------------------------------------------------------------------------

class ATLASReportGenerator:
    """Generate MITRE ATLAS compliance reports from assessment data."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or DB_PATH

    # -----------------------------------------------------------------
    # Database helpers
    # -----------------------------------------------------------------

    def _get_connection(self) -> sqlite3.Connection:
        """Get a database connection."""
        return _get_connection(self.db_path)

    def _get_project(self, project_id: str) -> Dict:
        """Load project data from database."""
        conn = self._get_connection()
        try:
            row = conn.execute(
                "SELECT * FROM projects WHERE id = ?", (project_id,)
            ).fetchone()
            if not row:
                raise ValueError(
                    f"Project '{project_id}' not found in database."
                )
            return dict(row)
        finally:
            conn.close()

    def _load_assessments(self, project_id: str) -> List[Dict]:
        """Load ATLAS assessment records for a project.

        Attempts atlas_assessments table first (BaseAssessor format with
        requirement_id/status columns). Falls back to results_json in
        the summary row if individual rows don't exist.
        """
        conn = self._get_connection()
        try:
            # Try BaseAssessor-style rows (requirement_id, status per row)
            try:
                rows = conn.execute(
                    """SELECT * FROM atlas_assessments
                       WHERE project_id = ?
                       ORDER BY requirement_id""",
                    (project_id,),
                ).fetchall()
                if rows:
                    # Check if these are per-requirement rows (BaseAssessor)
                    first = dict(rows[0])
                    if "requirement_id" in first and "status" in first:
                        return [dict(r) for r in rows]

                    # These are summary rows; parse results_json
                    results = []
                    for r in rows:
                        row_dict = dict(r)
                        results_json = row_dict.get("results_json")
                        if results_json:
                            try:
                                parsed = json.loads(results_json)
                                if isinstance(parsed, list):
                                    results.extend(parsed)
                                elif isinstance(parsed, dict):
                                    results.append(parsed)
                            except json.JSONDecodeError:
                                pass
                        # Also include the summary row itself
                        results.append(row_dict)
                    return results
            except Exception:
                pass

            return []
        finally:
            conn.close()

    def _load_catalog(self, catalog_name: str) -> List[Dict]:
        """Load a catalog JSON file from context/compliance/."""
        path = CATALOG_DIR / catalog_name
        return _load_catalog(path)

    # -----------------------------------------------------------------
    # Section builders
    # -----------------------------------------------------------------

    def _build_executive_summary(
        self, assessments: List[Dict], project: Dict,
    ) -> str:
        """Build executive summary section."""
        total = len(assessments)
        if total == 0:
            return (
                "No ATLAS assessments have been recorded for this project. "
                "Run the ATLAS assessor before generating this report:\n\n"
                "```\npython tools/compliance/atlas_assessor.py "
                f"--project-id {project.get('id', 'proj-XXX')}\n```"
            )

        # Count statuses
        status_counts = {s: 0 for s in VALID_STATUSES}
        for a in assessments:
            status = a.get("status", "not_assessed")
            if status in status_counts:
                status_counts[status] += 1

        satisfied = status_counts.get("satisfied", 0)
        partial = status_counts.get("partially_satisfied", 0)
        not_satisfied = status_counts.get("not_satisfied", 0)
        not_assessed = status_counts.get("not_assessed", 0)
        na_count = status_counts.get("not_applicable", 0)

        # Coverage score
        assessable = total - na_count
        if assessable > 0:
            coverage = 100.0 * (satisfied + partial * 0.5) / assessable
        else:
            coverage = 100.0

        # Posture rating
        if coverage >= 90:
            posture = "Strong"
        elif coverage >= 70:
            posture = "Moderate"
        elif coverage >= 50:
            posture = "Developing"
        else:
            posture = "Weak"

        # Gate result
        gate = "PASS" if not_satisfied == 0 and coverage >= 80.0 else "FAIL"

        lines = [
            f"**Overall ATLAS Coverage:** {coverage:.1f}%",
            f"**AI Security Posture:** {posture}",
            f"**Gate Result:** {gate}",
            f"**Mitigations Assessed:** {total}",
            "",
            f"This assessment evaluated {total} MITRE ATLAS mitigations "
            f"for project **{project.get('name', project.get('id', 'N/A'))}**.",
            "",
            f"- **{satisfied}** mitigations satisfied",
            f"- **{partial}** partially satisfied",
            f"- **{not_satisfied}** not satisfied",
            f"- **{not_assessed}** not assessed",
            f"- **{na_count}** not applicable",
        ]

        if not_satisfied > 0:
            lines.append("")
            lines.append(
                f"**{not_satisfied} mitigation(s) are not satisfied** -- "
                "remediation is required before the ATLAS gate can pass."
            )

        return "\n".join(lines)

    def _build_mitigation_coverage(self, assessments: List[Dict]) -> str:
        """Build mitigation coverage analysis grouped by category."""
        if not assessments:
            return "*No assessment data available.*"

        mitigations_catalog = self._load_catalog("atlas_mitigations.json")
        catalog_by_id = {
            m["id"]: m for m in mitigations_catalog if "id" in m
        }

        # Group assessments by category
        by_category: Dict[str, List[Dict]] = {}
        for a in assessments:
            req_id = a.get("requirement_id", "")
            catalog_entry = catalog_by_id.get(req_id, {})
            category = catalog_entry.get("category", "uncategorized")
            if category not in by_category:
                by_category[category] = []
            by_category[category].append({**a, "_catalog": catalog_entry})

        lines = [
            "| Category | Mitigations | Satisfied | Partial | Not Satisfied | Coverage |",
            "|----------|------------|-----------|---------|---------------|----------|",
        ]

        total_all = 0
        satisfied_all = 0
        for cat in MITIGATION_CATEGORIES:
            code = cat["code"]
            name = cat["name"]
            items = by_category.get(code, [])
            if not items:
                continue

            cat_total = len(items)
            cat_satisfied = sum(
                1 for i in items if i.get("status") == "satisfied"
            )
            cat_partial = sum(
                1 for i in items if i.get("status") == "partially_satisfied"
            )
            cat_not = sum(
                1 for i in items if i.get("status") == "not_satisfied"
            )
            na = sum(
                1 for i in items if i.get("status") == "not_applicable"
            )
            assessable = cat_total - na
            cov = (
                f"{100.0 * (cat_satisfied + cat_partial * 0.5) / assessable:.0f}%"
                if assessable > 0 else "N/A"
            )

            lines.append(
                f"| {name} | {cat_total} | {cat_satisfied} "
                f"| {cat_partial} | {cat_not} | {cov} |"
            )
            total_all += cat_total
            satisfied_all += cat_satisfied

        # Handle uncategorized
        uncategorized = by_category.get("uncategorized", [])
        if uncategorized:
            uc_total = len(uncategorized)
            uc_sat = sum(
                1 for i in uncategorized if i.get("status") == "satisfied"
            )
            uc_partial = sum(
                1 for i in uncategorized
                if i.get("status") == "partially_satisfied"
            )
            uc_not = sum(
                1 for i in uncategorized
                if i.get("status") == "not_satisfied"
            )
            uc_cov = (
                f"{100.0 * (uc_sat + uc_partial * 0.5) / uc_total:.0f}%"
                if uc_total > 0 else "N/A"
            )
            lines.append(
                f"| Other | {uc_total} | {uc_sat} "
                f"| {uc_partial} | {uc_not} | {uc_cov} |"
            )
            total_all += uc_total
            satisfied_all += uc_sat

        # Detail table per mitigation
        lines.append("")
        lines.append("### Detailed Mitigation Status")
        lines.append("")
        lines.append(
            "| Mitigation ID | Name | Status | Category |"
        )
        lines.append(
            "|---------------|------|--------|----------|"
        )

        for a in sorted(assessments, key=lambda x: x.get("requirement_id", "")):
            req_id = a.get("requirement_id", "N/A")
            catalog_entry = catalog_by_id.get(req_id, {})
            name = catalog_entry.get("name", a.get("requirement_title", ""))
            if len(name) > 50:
                name = name[:47] + "..."
            status = a.get("status", "not_assessed")
            category = CATEGORY_NAMES.get(
                catalog_entry.get("category", ""), "Other"
            )
            lines.append(f"| {req_id} | {name} | {status} | {category} |")

        return "\n".join(lines)

    def _build_technique_exposure(self, assessments: List[Dict]) -> str:
        """Build technique exposure analysis.

        Determines which ATLAS techniques are mitigated vs exposed
        based on the mitigation status from assessments.
        """
        mitigations_catalog = self._load_catalog("atlas_mitigations.json")
        catalog_by_id = {
            m["id"]: m for m in mitigations_catalog if "id" in m
        }

        # Build map: technique -> list of mitigations that address it
        technique_mitigations: Dict[str, List[str]] = {}
        for m in mitigations_catalog:
            for tech_id in m.get("techniques_addressed", []):
                if tech_id not in technique_mitigations:
                    technique_mitigations[tech_id] = []
                technique_mitigations[tech_id].append(m["id"])

        # Build map: mitigation_id -> status
        status_by_mitigation = {}
        for a in assessments:
            req_id = a.get("requirement_id", "")
            status_by_mitigation[req_id] = a.get("status", "not_assessed")

        if not technique_mitigations:
            return "*No technique data available. Ensure atlas_mitigations.json is present.*"

        # Classify each technique
        mitigated = []
        partially_mitigated = []
        exposed = []

        for tech_id in sorted(technique_mitigations.keys()):
            mit_ids = technique_mitigations[tech_id]
            statuses = [
                status_by_mitigation.get(mid, "not_assessed")
                for mid in mit_ids
            ]
            satisfied_count = sum(1 for s in statuses if s == "satisfied")
            partial_count = sum(
                1 for s in statuses if s == "partially_satisfied"
            )

            if satisfied_count == len(mit_ids):
                mitigated.append(tech_id)
            elif satisfied_count > 0 or partial_count > 0:
                partially_mitigated.append(tech_id)
            else:
                exposed.append(tech_id)

        total = len(technique_mitigations)
        lines = [
            f"**Total Techniques Tracked:** {total}",
            f"**Fully Mitigated:** {len(mitigated)}",
            f"**Partially Mitigated:** {len(partially_mitigated)}",
            f"**Exposed (Unmitigated):** {len(exposed)}",
            "",
        ]

        if exposed:
            lines.append("### Exposed Techniques (Unmitigated)")
            lines.append("")
            lines.append("| Technique ID | Addressing Mitigations | Mitigation Status |")
            lines.append("|--------------|----------------------|-------------------|")
            for tech_id in exposed:
                mit_ids = technique_mitigations[tech_id]
                mit_str = ", ".join(mit_ids)
                status_str = ", ".join(
                    f"{mid}={status_by_mitigation.get(mid, 'not_assessed')}"
                    for mid in mit_ids
                )
                lines.append(f"| {tech_id} | {mit_str} | {status_str} |")
            lines.append("")

        if partially_mitigated:
            lines.append("### Partially Mitigated Techniques")
            lines.append("")
            lines.append("| Technique ID | Satisfied Mitigations | Total Mitigations |")
            lines.append("|--------------|----------------------|-------------------|")
            for tech_id in partially_mitigated:
                mit_ids = technique_mitigations[tech_id]
                sat_count = sum(
                    1 for mid in mit_ids
                    if status_by_mitigation.get(mid) == "satisfied"
                )
                lines.append(
                    f"| {tech_id} | {sat_count} | {len(mit_ids)} |"
                )

        return "\n".join(lines)

    def _build_owasp_crossref(self, assessments: List[Dict]) -> str:
        """Build OWASP LLM Top 10 cross-reference section.

        Maps ATLAS mitigations to OWASP LLM risk categories and shows
        coverage status per OWASP risk.
        """
        owasp_catalog = _load_owasp_catalog()

        # Build mitigation status lookup
        status_by_mitigation = {}
        for a in assessments:
            req_id = a.get("requirement_id", "")
            status_by_mitigation[req_id] = a.get("status", "not_assessed")

        lines = [
            "| # | OWASP LLM Risk | ATLAS Mitigations | Coverage |",
            "|---|---------------|-------------------|----------|",
        ]

        for idx, (owasp_code, atlas_mits) in enumerate(
            OWASP_TO_ATLAS_MAP.items(), 1
        ):
            # Find OWASP name from catalog
            owasp_name = owasp_code.replace("_", " ").title()
            for cat in owasp_catalog:
                if cat.get("code") == owasp_code:
                    owasp_name = cat.get("name", owasp_name)
                    break

            # Check coverage
            satisfied = sum(
                1 for mid in atlas_mits
                if status_by_mitigation.get(mid) == "satisfied"
            )
            partial = sum(
                1 for mid in atlas_mits
                if status_by_mitigation.get(mid) == "partially_satisfied"
            )
            total = len(atlas_mits)

            if satisfied == total:
                coverage = "Covered"
            elif satisfied > 0 or partial > 0:
                coverage = "Partial"
            else:
                coverage = "Exposed"

            mit_str = ", ".join(atlas_mits)
            lines.append(
                f"| {idx} | {owasp_name} | {mit_str} | {coverage} |"
            )

        # Summary
        covered_count = sum(
            1 for _, mits in OWASP_TO_ATLAS_MAP.items()
            if all(
                status_by_mitigation.get(mid) == "satisfied"
                for mid in mits
            )
        )
        lines.append("")
        lines.append(
            f"**OWASP LLM Coverage:** {covered_count} / "
            f"{len(OWASP_TO_ATLAS_MAP)} risks fully covered"
        )

        return "\n".join(lines)

    def _build_gap_analysis(self, assessments: List[Dict]) -> str:
        """Build gap analysis for unmitigated or not-satisfied items."""
        mitigations_catalog = self._load_catalog("atlas_mitigations.json")
        catalog_by_id = {
            m["id"]: m for m in mitigations_catalog if "id" in m
        }

        gaps = [
            a for a in assessments
            if a.get("status") in ("not_satisfied", "not_assessed")
        ]

        if not gaps:
            return (
                "*No gaps identified. All assessed mitigations are "
                "satisfied or not applicable.*"
            )

        lines = [
            f"**Total Gaps:** {len(gaps)}",
            "",
            "| Mitigation ID | Name | Status | Category | "
            "Techniques at Risk |",
            "|---------------|------|--------|----------"
            "|-------------------|",
        ]

        for g in sorted(gaps, key=lambda x: x.get("requirement_id", "")):
            req_id = g.get("requirement_id", "N/A")
            catalog_entry = catalog_by_id.get(req_id, {})
            name = catalog_entry.get("name", g.get("requirement_title", ""))
            if len(name) > 40:
                name = name[:37] + "..."
            status = g.get("status", "not_assessed")
            category = CATEGORY_NAMES.get(
                catalog_entry.get("category", ""), "Other"
            )
            techniques = catalog_entry.get("techniques_addressed", [])
            tech_str = ", ".join(techniques[:3])
            if len(techniques) > 3:
                tech_str += f" (+{len(techniques) - 3})"

            lines.append(
                f"| {req_id} | {name} | {status} | {category} | {tech_str} |"
            )

        return "\n".join(lines)

    def _build_remediation_recommendations(
        self, assessments: List[Dict],
    ) -> str:
        """Build prioritized remediation recommendations."""
        mitigations_catalog = self._load_catalog("atlas_mitigations.json")
        catalog_by_id = {
            m["id"]: m for m in mitigations_catalog if "id" in m
        }

        not_satisfied = [
            a for a in assessments
            if a.get("status") == "not_satisfied"
        ]
        partial = [
            a for a in assessments
            if a.get("status") == "partially_satisfied"
        ]
        not_assessed = [
            a for a in assessments
            if a.get("status") == "not_assessed"
        ]

        lines = []

        # Priority 1: Not satisfied mitigations with most techniques
        if not_satisfied:
            # Sort by number of techniques addressed (most impactful first)
            prioritized = sorted(
                not_satisfied,
                key=lambda a: len(
                    catalog_by_id.get(
                        a.get("requirement_id", ""), {}
                    ).get("techniques_addressed", [])
                ),
                reverse=True,
            )
            lines.append("### Priority 1: Critical Gaps (Not Satisfied)")
            lines.append("")
            lines.append(
                "These mitigations are not satisfied and leave the system "
                "exposed to adversarial ML attacks:"
            )
            lines.append("")
            for a in prioritized:
                req_id = a.get("requirement_id", "N/A")
                catalog_entry = catalog_by_id.get(req_id, {})
                name = catalog_entry.get("name", "")
                desc = catalog_entry.get("description", "")
                if len(desc) > 120:
                    desc = desc[:117] + "..."
                tech_count = len(
                    catalog_entry.get("techniques_addressed", [])
                )
                nist = ", ".join(
                    catalog_entry.get("nist_controls", [])[:4]
                )
                lines.append(
                    f"- **{req_id} ({name})**: {desc}"
                )
                lines.append(
                    f"  - Techniques addressed: {tech_count} | "
                    f"NIST controls: {nist or 'N/A'}"
                )
            lines.append("")

        # Priority 2: Partially satisfied
        if partial:
            lines.append("### Priority 2: Complete Partial Implementations")
            lines.append("")
            lines.append(
                f"{len(partial)} mitigation(s) are partially satisfied. "
                "Complete implementation to achieve full ATLAS coverage."
            )
            lines.append("")
            for a in partial:
                req_id = a.get("requirement_id", "N/A")
                catalog_entry = catalog_by_id.get(req_id, {})
                name = catalog_entry.get("name", "")
                lines.append(f"- **{req_id}**: {name}")
            lines.append("")

        # Priority 3: Not assessed
        if not_assessed:
            lines.append("### Priority 3: Complete Assessment")
            lines.append("")
            lines.append(
                f"{len(not_assessed)} mitigation(s) have not been assessed. "
                "All ATLAS mitigations must be evaluated for AI security "
                "posture certification."
            )
            lines.append("")

        if not lines:
            return (
                "*No recommendations at this time. All mitigations are "
                "satisfied or not applicable.*"
            )

        return "\n".join(lines)

    def _build_nist_mapping(self, assessments: List[Dict]) -> str:
        """Build NIST 800-53 control mapping section.

        Maps each ATLAS mitigation to its corresponding NIST 800-53
        controls from the catalog.
        """
        mitigations_catalog = self._load_catalog("atlas_mitigations.json")
        catalog_by_id = {
            m["id"]: m for m in mitigations_catalog if "id" in m
        }

        if not catalog_by_id:
            return (
                "*NIST 800-53 mapping unavailable "
                "(mitigations catalog not loaded).*"
            )

        # Build mapping table
        status_by_id = {}
        for a in assessments:
            req_id = a.get("requirement_id", "")
            status_by_id[req_id] = a.get("status", "not_assessed")

        lines = [
            "| Mitigation ID | Name | NIST 800-53 Controls | Status |",
            "|---------------|------|---------------------|--------|",
        ]

        # Collect all unique NIST controls
        all_nist = set()
        for m in mitigations_catalog:
            nist_controls = m.get("nist_controls", [])
            all_nist.update(nist_controls)

            mid = m.get("id", "")
            name = m.get("name", "")
            if len(name) > 35:
                name = name[:32] + "..."
            nist_str = ", ".join(nist_controls) if nist_controls else "N/A"
            status = status_by_id.get(mid, "not_assessed")
            lines.append(f"| {mid} | {name} | {nist_str} | {status} |")

        lines.append("")
        lines.append(
            f"**Total Unique NIST 800-53 Controls Referenced:** "
            f"{len(all_nist)}"
        )
        if all_nist:
            sorted_nist = sorted(all_nist)
            lines.append(
                f"**Controls:** {', '.join(sorted_nist)}"
            )

        return "\n".join(lines)

    def _apply_markings(
        self, report_text: str, impact_level: str,
    ) -> str:
        """Apply CUI markings to the report text."""
        cui_config = _load_cui_config()
        header = cui_config.get("document_header", "").strip()
        footer = cui_config.get("document_footer", "").strip()
        banner_top = cui_config.get("banner_top", "CUI // SP-CTI")

        if banner_top in report_text:
            return report_text

        return f"{header}\n\n{report_text.strip()}\n\n{footer}\n"

    def _log_audit_event(
        self, conn: sqlite3.Connection, project_id: str,
        action: str, details: Dict,
    ) -> None:
        """Log an audit trail event for ATLAS report generation."""
        try:
            conn.execute(
                """INSERT INTO audit_trail
                   (project_id, event_type, actor, action, details,
                    affected_files, classification)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    project_id,
                    "atlas_assessed",
                    "icdev-compliance-engine",
                    action,
                    json.dumps(details),
                    json.dumps([]),
                    "CUI",
                ),
            )
            conn.commit()
        except Exception as exc:
            print(
                f"Warning: Could not log audit event: {exc}",
                file=sys.stderr,
            )

    # -----------------------------------------------------------------
    # Main report generation
    # -----------------------------------------------------------------

    def generate_report(
        self,
        project_id: str,
        output_path: Optional[str] = None,
    ) -> Dict:
        """Generate an ATLAS compliance report for a project.

        Args:
            project_id: The project identifier.
            output_path: Override output directory or file path.

        Returns:
            Dict with keys: status, output_file, summary.
        """
        conn = self._get_connection()
        try:
            # 1. Load project data
            project = self._get_project(project_id)
            project_name = project.get("name", project_id)

            # 2. Load assessment data
            assessments = self._load_assessments(project_id)

            # 3. Calculate overall metrics
            total = len(assessments)
            satisfied = sum(
                1 for a in assessments
                if a.get("status") == "satisfied"
            )
            not_satisfied = sum(
                1 for a in assessments
                if a.get("status") == "not_satisfied"
            )
            partial = sum(
                1 for a in assessments
                if a.get("status") == "partially_satisfied"
            )
            na_count = sum(
                1 for a in assessments
                if a.get("status") == "not_applicable"
            )
            not_assessed = sum(
                1 for a in assessments
                if a.get("status") == "not_assessed"
            )

            assessable = total - na_count
            coverage = (
                round(
                    100.0 * (satisfied + partial * 0.5) / assessable, 1
                )
                if assessable > 0 else 0.0
            )

            gate_result = (
                "PASS"
                if not_satisfied == 0 and coverage >= 80.0
                else "FAIL"
            )

            if coverage >= 90:
                posture = "Strong"
            elif coverage >= 70:
                posture = "Moderate"
            elif coverage >= 50:
                posture = "Developing"
            else:
                posture = "Weak"

            # 4. Build all report sections
            executive_summary = self._build_executive_summary(
                assessments, project
            )
            mitigation_coverage = self._build_mitigation_coverage(assessments)
            technique_exposure = self._build_technique_exposure(assessments)
            owasp_crossref = self._build_owasp_crossref(assessments)
            gap_analysis = self._build_gap_analysis(assessments)
            remediation = self._build_remediation_recommendations(assessments)
            nist_mapping = self._build_nist_mapping(assessments)

            # 5. Load CUI config
            cui_config = _load_cui_config()

            # 6. Determine version
            report_count_row = conn.execute(
                """SELECT COUNT(*) as cnt FROM audit_trail
                   WHERE project_id = ? AND event_type = 'atlas_assessed'
                   AND action LIKE '%report%'""",
                (project_id,),
            ).fetchone()
            report_count = (
                report_count_row["cnt"] if report_count_row else 0
            )
            new_version = f"{report_count + 1}.0"

            now = datetime.now(timezone.utc)

            # 7. Build substitution variables
            variables = {
                "system_name": project_name,
                "project_id": project_id,
                "impact_level": project.get("impact_level", "CUI"),
                "classification": project.get("classification", "CUI"),
                "assessment_date": now.strftime("%Y-%m-%d"),
                "version": new_version,
                "assessor": "icdev-compliance-engine",
                "executive_summary": executive_summary,
                "mitigation_coverage": mitigation_coverage,
                "technique_exposure": technique_exposure,
                "owasp_crossref": owasp_crossref,
                "gap_analysis": gap_analysis,
                "remediation_recommendations": remediation,
                "nist_mapping": nist_mapping,
                "cui_banner_top": cui_config.get(
                    "document_header",
                    cui_config.get("banner_top", "CUI // SP-CTI"),
                ),
                "cui_banner_bottom": cui_config.get(
                    "document_footer",
                    cui_config.get("banner_bottom", "CUI // SP-CTI"),
                ),
            }

            # 8. Apply template substitution
            report_content = _substitute_variables(
                REPORT_TEMPLATE, variables
            )

            # 9. Apply CUI markings
            report_content = self._apply_markings(
                report_content,
                project.get("impact_level", "CUI"),
            )

            # 10. Determine output path
            if output_path:
                out_path = Path(output_path)
                if out_path.is_dir() or str(output_path).endswith(
                    ("/", "\\")
                ):
                    out_dir = out_path
                    out_file = (
                        out_dir / f"atlas-report-v{new_version}.md"
                    )
                else:
                    out_file = out_path
            else:
                dir_path = project.get("directory_path", "")
                if dir_path:
                    out_dir = Path(dir_path) / "compliance"
                else:
                    out_dir = (
                        BASE_DIR / "projects" / project_name / "compliance"
                    )
                out_file = out_dir / f"atlas-report-v{new_version}.md"

            out_file.parent.mkdir(parents=True, exist_ok=True)

            # 11. Write file
            with open(out_file, "w", encoding="utf-8") as f:
                f.write(report_content)

            # 12. Log audit event
            audit_details = {
                "version": new_version,
                "coverage_pct": coverage,
                "posture": posture,
                "gate_result": gate_result,
                "total_mitigations": total,
                "mitigations_satisfied": satisfied,
                "mitigations_not_satisfied": not_satisfied,
                "mitigations_partial": partial,
                "mitigations_not_assessed": not_assessed,
                "output_file": str(out_file),
            }
            self._log_audit_event(
                conn, project_id,
                f"ATLAS report v{new_version} generated",
                audit_details,
            )

            # 13. Print summary
            print("ATLAS compliance report generated successfully:")
            print(f"  File:              {out_file}")
            print(f"  Version:           {new_version}")
            print(f"  Project:           {project_name}")
            print(f"  Coverage:          {coverage:.1f}%")
            print(f"  Posture:           {posture}")
            print(f"  Gate Result:       {gate_result}")
            print(f"  Mitigations:       {total}")
            print(f"  Satisfied:         {satisfied}")
            print(f"  Not Satisfied:     {not_satisfied}")
            print(f"  Partial:           {partial}")
            print(f"  Not Assessed:      {not_assessed}")

            # 14. Build summary dict
            summary = {
                "version": new_version,
                "project_id": project_id,
                "project_name": project_name,
                "coverage_pct": coverage,
                "posture": posture,
                "gate_result": gate_result,
                "total_mitigations": total,
                "mitigations_satisfied": satisfied,
                "mitigations_not_satisfied": not_satisfied,
                "mitigations_partial": partial,
                "mitigations_not_assessed": not_assessed,
                "mitigations_na": na_count,
                "generated_at": now.isoformat(),
            }

            return {
                "status": "success",
                "output_file": str(out_file),
                "summary": summary,
                "gate_result": {
                    "gate": "atlas_ai",
                    "result": gate_result,
                    "coverage_pct": coverage,
                    "mitigations_not_satisfied": not_satisfied,
                    "posture": posture,
                },
            }

        finally:
            conn.close()

    # -----------------------------------------------------------------
    # CLI
    # -----------------------------------------------------------------

    def run_cli(self) -> None:
        """Standard CLI entry point."""
        parser = argparse.ArgumentParser(
            description="Generate MITRE ATLAS compliance report"
        )
        parser.add_argument(
            "--project-id", required=True,
            help="Project ID",
        )
        parser.add_argument(
            "--output-path",
            help="Output directory or file path",
        )
        parser.add_argument(
            "--json", action="store_true",
            help="JSON output",
        )
        parser.add_argument(
            "--human", action="store_true",
            help="Human-readable colored output",
        )
        parser.add_argument(
            "--db-path", type=Path, default=None,
            help="Database path override",
        )
        args = parser.parse_args()

        if args.db_path:
            self.db_path = args.db_path

        try:
            result = self.generate_report(
                args.project_id,
                output_path=args.output_path,
            )
            if args.json:
                print(json.dumps(result, indent=2))
            else:
                print(f"\nATLAS report generated: {result['output_file']}")
        except (FileNotFoundError, ValueError) as e:
            print(f"ERROR: {e}", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    ATLASReportGenerator().run_cli()
