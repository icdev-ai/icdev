#!/usr/bin/env python3
# CUI // SP-CTI
# CONTROLLED UNCLASSIFIED INFORMATION (CUI)
# Distribution: Distribution D -- Authorized DoD Personnel Only
# CUI // SP-CTI
"""Compliance status report generator.

Reads a baseline file (JSON or YAML) and an environment-state file (JSON or
YAML), calculates per-control compliance flags, and outputs a structured
compliance status report to stdout or a file.

Baseline format (JSON/YAML):
  controls: list of {id, family, nist_control_id, title, priority, ...}

Environment state format (JSON/YAML):
  system_name: str
  assessed_date: str            # ISO date, optional
  controls: list of {
      id: str                   # matches baseline control id
      status: implemented|partial|not_implemented|not_applicable
      evidence: str             # optional
      notes: str                # optional
  }

Usage:
  python tools/generate_compliance_report.py \\
      --baseline context/compliance/fedramp_moderate_baseline.json \\
      --env-state args/env_state.yaml \\
      --output reports/compliance_status.md
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    import yaml
    _YAML_AVAILABLE = True
except ImportError:
    _YAML_AVAILABLE = False

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent.parent

STATUS_COMPLIANT = "COMPLIANT"
STATUS_PARTIAL = "PARTIAL"
STATUS_NON_COMPLIANT = "NON-COMPLIANT"
STATUS_NOT_APPLICABLE = "N/A"
STATUS_MISSING = "NOT ASSESSED"

_STATUS_MAP = {
    "implemented": STATUS_COMPLIANT,
    "partial": STATUS_PARTIAL,
    "not_implemented": STATUS_NON_COMPLIANT,
    "not_applicable": STATUS_NOT_APPLICABLE,
}

PRIORITY_WEIGHT = {"P1": 3, "P2": 2, "P3": 1, "P4": 0}

CONTROL_FAMILIES = {
    "AC": "Access Control",
    "AU": "Audit and Accountability",
    "AT": "Awareness and Training",
    "CM": "Configuration Management",
    "CP": "Contingency Planning",
    "IA": "Identification and Authentication",
    "IR": "Incident Response",
    "MA": "Maintenance",
    "MP": "Media Protection",
    "PE": "Physical and Environmental Protection",
    "PL": "Planning",
    "PM": "Program Management",
    "PS": "Personnel Security",
    "PT": "PII Processing and Transparency",
    "RA": "Risk Assessment",
    "CA": "Assessment, Authorization, and Monitoring",
    "SC": "System and Communications Protection",
    "SI": "System and Information Integrity",
    "SA": "System and Services Acquisition",
    "SR": "Supply Chain Risk Management",
}

CRITICAL_CONTROLS = {"AC-2", "IA-2", "SC-7", "AU-2", "CM-6"}

CUI_BANNER = "CUI // SP-CTI"

# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------


def _load_file(path: Path) -> dict:
    """Load JSON or YAML file; raise ValueError for unsupported extensions."""
    suffix = path.suffix.lower()
    with open(path, "r", encoding="utf-8") as fh:
        raw = fh.read()

    if suffix == ".json":
        return json.loads(raw)

    if suffix in (".yaml", ".yml"):
        if not _YAML_AVAILABLE:
            raise ImportError("PyYAML is required to load YAML files: pip install pyyaml")
        return yaml.safe_load(raw)

    raise ValueError(f"Unsupported file extension '{suffix}' — expected .json, .yaml, or .yml")


def _parse_baseline(data: dict) -> list[dict]:
    """Extract control list from baseline data."""
    if "controls" in data:
        return data["controls"]
    # Flat list at root
    if isinstance(data, list):
        return data
    raise ValueError("Baseline must contain a 'controls' list")


def _parse_env_state(data: dict) -> tuple[dict, dict]:
    """Return (metadata, {control_id: state_record}) from env-state data."""
    meta = {
        "system_name": data.get("system_name", "Unknown System"),
        "assessed_date": data.get("assessed_date", ""),
        "assessor": data.get("assessor", ""),
        "baseline_ref": data.get("baseline_ref", ""),
    }
    state_map: dict[str, dict] = {}
    for record in data.get("controls", []):
        ctrl_id = record.get("id", "")
        if ctrl_id:
            state_map[ctrl_id] = record
    return meta, state_map


# ---------------------------------------------------------------------------
# Compliance calculation
# ---------------------------------------------------------------------------


def _compliance_flag(baseline_ctrl: dict, state_map: dict) -> dict:
    """Return a compliance result dict for a single baseline control."""
    ctrl_id = baseline_ctrl.get("id", "")
    nist_id = baseline_ctrl.get("nist_control_id", ctrl_id)
    family_code = baseline_ctrl.get("family", nist_id.split("-")[0] if "-" in nist_id else "")
    family_name = CONTROL_FAMILIES.get(family_code, family_code)
    title = baseline_ctrl.get("title", "")
    priority = baseline_ctrl.get("priority", "P3")

    # Look up by exact id, then by nist_control_id as fallback
    state = state_map.get(ctrl_id) or state_map.get(nist_id)

    if state is None:
        compliance = STATUS_MISSING
        evidence = ""
        notes = "No assessment record found"
    else:
        raw_status = str(state.get("status", "")).lower().strip()
        compliance = _STATUS_MAP.get(raw_status, STATUS_MISSING)
        evidence = state.get("evidence", "")
        notes = state.get("notes", "")

    is_critical = nist_id in CRITICAL_CONTROLS
    blocks_gate = is_critical and compliance == STATUS_NON_COMPLIANT

    return {
        "id": ctrl_id,
        "nist_id": nist_id,
        "family": family_code,
        "family_name": family_name,
        "title": title,
        "priority": priority,
        "compliance": compliance,
        "is_critical": is_critical,
        "blocks_gate": blocks_gate,
        "evidence": evidence,
        "notes": notes,
    }


def _calculate_summary(results: list[dict]) -> dict:
    """Aggregate compliance results into summary statistics."""
    total = len(results)
    counts = {
        STATUS_COMPLIANT: 0,
        STATUS_PARTIAL: 0,
        STATUS_NON_COMPLIANT: 0,
        STATUS_NOT_APPLICABLE: 0,
        STATUS_MISSING: 0,
    }
    for r in results:
        counts[r["compliance"]] = counts.get(r["compliance"], 0) + 1

    # Applicable = all minus N/A
    applicable = total - counts[STATUS_NOT_APPLICABLE]
    compliant = counts[STATUS_COMPLIANT]
    score_pct = round(compliant / applicable * 100, 1) if applicable else 0.0

    gate_blockers = [r for r in results if r["blocks_gate"]]
    gate_pass = len(gate_blockers) == 0

    by_family: dict[str, dict] = {}
    for r in results:
        fam = r["family"]
        if fam not in by_family:
            by_family[fam] = {"name": r["family_name"], "total": 0, "compliant": 0, "non_compliant": 0, "partial": 0}
        by_family[fam]["total"] += 1
        if r["compliance"] == STATUS_COMPLIANT:
            by_family[fam]["compliant"] += 1
        elif r["compliance"] == STATUS_NON_COMPLIANT:
            by_family[fam]["non_compliant"] += 1
        elif r["compliance"] == STATUS_PARTIAL:
            by_family[fam]["partial"] += 1

    return {
        "total": total,
        "applicable": applicable,
        "counts": counts,
        "score_pct": score_pct,
        "gate_pass": gate_pass,
        "gate_blockers": gate_blockers,
        "by_family": by_family,
    }


# ---------------------------------------------------------------------------
# Report rendering
# ---------------------------------------------------------------------------


def _gate_badge(gate_pass: bool) -> str:
    return "PASS" if gate_pass else "FAIL"


def _render_report(meta: dict, results: list[dict], summary: dict, baseline_path: Path) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    assessed = meta.get("assessed_date") or now[:10]

    lines: list[str] = []

    # CUI banner
    lines += [CUI_BANNER, "", "---", ""]

    # Header
    lines += [
        "# Compliance Status Report",
        "",
        f"**System:** {meta['system_name']}",
        f"**Baseline:** {baseline_path.name}",
        f"**Assessed Date:** {assessed}",
    ]
    if meta.get("assessor"):
        lines.append(f"**Assessor:** {meta['assessor']}")
    if meta.get("baseline_ref"):
        lines.append(f"**Baseline Ref:** {meta['baseline_ref']}")
    lines += [f"**Report Generated:** {now}", ""]

    # Executive summary
    lines += ["## Executive Summary", ""]
    gate_label = _gate_badge(summary["gate_pass"])
    lines += [
        "| Metric | Value |",
        "|--------|-------|",
        f"| Overall Score | {summary['score_pct']}% ({summary['counts'][STATUS_COMPLIANT]}/{summary['applicable']} applicable controls) |",
        f"| Gate Status | {gate_label} |",
        f"| Total Controls | {summary['total']} |",
        f"| Compliant | {summary['counts'][STATUS_COMPLIANT]} |",
        f"| Partial | {summary['counts'][STATUS_PARTIAL]} |",
        f"| Non-Compliant | {summary['counts'][STATUS_NON_COMPLIANT]} |",
        f"| Not Applicable | {summary['counts'][STATUS_NOT_APPLICABLE]} |",
        f"| Not Assessed | {summary['counts'][STATUS_MISSING]} |",
        "",
    ]

    if not summary["gate_pass"]:
        lines += ["### Gate Blockers (Critical Controls FAILED)", ""]
        for r in summary["gate_blockers"]:
            lines.append(f"- **{r['nist_id']}** {r['title']} — {r['compliance']}")
        lines.append("")

    # Family summary table
    lines += ["## Compliance by Control Family", ""]
    lines += ["| Family | Name | Total | Compliant | Partial | Non-Compliant |"]
    lines += ["|--------|------|-------|-----------|---------|---------------|"]
    for fam_code in sorted(summary["by_family"]):
        fam = summary["by_family"][fam_code]
        lines.append(
            f"| {fam_code} | {fam['name']} | {fam['total']} "
            f"| {fam['compliant']} | {fam['partial']} | {fam['non_compliant']} |"
        )
    lines.append("")

    # Detail table
    lines += ["## Control-Level Compliance Status", ""]
    lines += ["| Control ID | NIST ID | Priority | Status | Critical | Title |"]
    lines += ["|------------|---------|----------|--------|----------|-------|"]

    for r in results:
        critical_flag = "YES" if r["is_critical"] else ""
        lines.append(
            f"| {r['id']} | {r['nist_id']} | {r['priority']} "
            f"| {r['compliance']} | {critical_flag} | {r['title']} |"
        )
    lines.append("")

    # Non-compliant detail section
    non_compliant = [r for r in results if r["compliance"] in (STATUS_NON_COMPLIANT, STATUS_PARTIAL, STATUS_MISSING)]
    if non_compliant:
        lines += ["## Findings Requiring Remediation", ""]
        for r in non_compliant:
            lines += [
                f"### {r['nist_id']} — {r['title']}",
                "",
                f"- **Status:** {r['compliance']}",
                f"- **Priority:** {r['priority']}",
                f"- **Critical Control:** {'Yes' if r['is_critical'] else 'No'}",
            ]
            if r["notes"]:
                lines.append(f"- **Notes:** {r['notes']}")
            if r["evidence"]:
                lines.append(f"- **Evidence:** {r['evidence']}")
            lines.append("")

    lines += ["---", "", CUI_BANNER, ""]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def run(baseline_path: Path, env_state_path: Path, output_path: Path | None, fmt: str) -> int:
    """Load inputs, calculate compliance, render and write report. Returns exit code."""
    # Load and parse
    try:
        baseline_data = _load_file(baseline_path)
        env_data = _load_file(env_state_path)
    except (FileNotFoundError, ValueError, ImportError) as exc:
        print(f"ERROR loading input files: {exc}", file=sys.stderr)
        return 1

    try:
        baseline_controls = _parse_baseline(baseline_data)
        meta, state_map = _parse_env_state(env_data)
    except (ValueError, KeyError) as exc:
        print(f"ERROR parsing inputs: {exc}", file=sys.stderr)
        return 1

    if not baseline_controls:
        print("ERROR: Baseline contains no controls.", file=sys.stderr)
        return 1

    # Calculate compliance for each control
    results = [_compliance_flag(ctrl, state_map) for ctrl in baseline_controls]
    summary = _calculate_summary(results)

    # Render report
    if fmt == "json":
        report_text = json.dumps(
            {
                "metadata": meta,
                "summary": {
                    "total": summary["total"],
                    "applicable": summary["applicable"],
                    "score_pct": summary["score_pct"],
                    "gate_pass": summary["gate_pass"],
                    "counts": summary["counts"],
                    "gate_blockers": [r["nist_id"] for r in summary["gate_blockers"]],
                },
                "controls": results,
            },
            indent=2,
        )
    else:
        report_text = _render_report(meta, results, summary, baseline_path)

    # Output
    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as fh:
            fh.write(report_text)
        print(f"Report written to: {output_path}")
    else:
        print(report_text)

    # Signal gate status via exit code
    return 0 if summary["gate_pass"] else 2


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate a compliance status report from a baseline and environment state."
    )
    parser.add_argument(
        "--baseline",
        required=True,
        type=Path,
        help="Path to baseline JSON/YAML (e.g. context/compliance/fedramp_moderate_baseline.json)",
    )
    parser.add_argument(
        "--env-state",
        required=True,
        type=Path,
        dest="env_state",
        help="Path to environment state JSON/YAML listing control implementation status",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Write report to this file instead of stdout",
    )
    parser.add_argument(
        "--format",
        choices=["markdown", "json"],
        default="markdown",
        help="Output format (default: markdown)",
    )
    args = parser.parse_args()

    exit_code = run(args.baseline, args.env_state, args.output, args.format)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
