# CUI // SP-CTI
"""Agentic AI Design Canvas — Findings Inbox (Phase 9).

Aggregates findings from all AADC analysis sources into a unified, sortable feed:
  - NIST AI RMF / OWASP assessment findings
  - Design lint issues (LNT-XX)
  - Red team unmitigated scenarios
  - ATO failed items
  - Regulatory gaps
  - Open risk register items (CRITICAL/HIGH)

Each finding: source, design_id, design_name, severity, title, detail, created_at.
"""

from __future__ import annotations

import json


_SEVERITY_ORDER = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "INFO": 4}


def aggregate_findings(
    designs: list[dict],
    assessments: list[dict],
    lint_reports: list[dict],
    red_team_reports: list[dict],
    ato_reports: list[dict],
    regulatory_reports: list[dict],
    risk_items: list[dict],
    severity_filter: str | None = None,
    source_filter: str | None = None,
    design_filter: str | None = None,
) -> dict:
    """
    Aggregate findings from all sources into a unified feed.

    Returns:
        {
          findings: list[dict],
          summary: {total, by_severity, by_source},
          filters: {severity, source, design_id}
        }
    """
    design_names = {d["id"]: d.get("name", d["id"][:8]) for d in designs}

    findings: list[dict] = []

    # Assessment findings
    for a in assessments:
        did = a.get("design_id", "")
        findings.extend(_from_assessment(a, did, design_names.get(did, did[:8])))

    # Lint findings
    for r in lint_reports:
        did = r.get("design_id", "")
        findings.extend(_from_lint(r, did, design_names.get(did, did[:8])))

    # Red team findings
    for r in red_team_reports:
        did = r.get("design_id", "")
        findings.extend(_from_red_team(r, did, design_names.get(did, did[:8])))

    # ATO findings
    for r in ato_reports:
        did = r.get("design_id", "")
        findings.extend(_from_ato(r, did, design_names.get(did, did[:8])))

    # Regulatory findings
    for r in regulatory_reports:
        did = r.get("design_id", "")
        findings.extend(_from_regulatory(r, did, design_names.get(did, did[:8])))

    # Risk items
    for r in risk_items:
        if r.get("severity") in ("CRITICAL", "HIGH") and r.get("status") == "open":
            did = r.get("design_id", "")
            findings.append({
                "source": "risk_register",
                "design_id": did,
                "design_name": design_names.get(did, did[:8]),
                "severity": r.get("severity", "HIGH"),
                "title": r.get("title", "Open risk item"),
                "detail": r.get("description", ""),
                "created_at": r.get("created_at", ""),
            })

    # Apply filters
    if severity_filter:
        findings = [f for f in findings if f["severity"] == severity_filter]
    if source_filter:
        findings = [f for f in findings if f["source"] == source_filter]
    if design_filter:
        findings = [f for f in findings if f["design_id"] == design_filter]

    # Sort: severity then source
    findings.sort(key=lambda x: (_SEVERITY_ORDER.get(x["severity"], 99), x.get("source", "")))

    # Summary
    by_sev: dict[str, int] = {}
    by_src: dict[str, int] = {}
    for f in findings:
        by_sev[f["severity"]] = by_sev.get(f["severity"], 0) + 1
        by_src[f["source"]] = by_src.get(f["source"], 0) + 1

    return {
        "findings": findings,
        "summary": {
            "total": len(findings),
            "by_severity": by_sev,
            "by_source": by_src,
        },
        "filters": {
            "severity": severity_filter,
            "source": source_filter,
            "design_id": design_filter,
        },
    }


def _from_assessment(report: dict, design_id: str, design_name: str) -> list[dict]:
    out = []
    raw = report.get("findings_json") or report.get("findings") or "[]"
    if isinstance(raw, str):
        try:
            findings = json.loads(raw)
        except (ValueError, TypeError):
            findings = []
    else:
        findings = raw if isinstance(raw, list) else []

    ts = report.get("created_at", "")
    for f in findings:
        if isinstance(f, str):
            continue
        sev = f.get("severity", "MEDIUM").upper()
        if sev in ("CRITICAL", "HIGH", "MEDIUM"):
            out.append({
                "source": "assessment",
                "design_id": design_id,
                "design_name": design_name,
                "severity": sev,
                "title": f.get("title", f.get("rule", "Assessment finding")),
                "detail": f.get("recommendation", f.get("message", "")),
                "created_at": ts,
            })
    return out


def _from_lint(report: dict, design_id: str, design_name: str) -> list[dict]:
    out = []
    raw = report.get("report_json") or "{}"
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            data = {}
    else:
        data = raw if isinstance(raw, dict) else {}

    recs = data.get("recommendations", [])
    ts = report.get("created_at", "")
    for r in recs:
        sev = r.get("severity", "MEDIUM").upper()
        out.append({
            "source": "lint",
            "design_id": design_id,
            "design_name": design_name,
            "severity": sev,
            "title": f"[{r.get('rule_id','LNT')}] {r.get('message', 'Lint issue')}",
            "detail": f"Add node: {r.get('add_node', '—')}",
            "created_at": ts,
        })
    return out


def _from_red_team(report: dict, design_id: str, design_name: str) -> list[dict]:
    out = []
    raw = report.get("report_json") or "{}"
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            data = {}
    else:
        data = raw if isinstance(raw, dict) else {}

    scenarios = data.get("scenarios", [])
    ts = report.get("created_at", "")
    for s in scenarios:
        if s.get("applicable") and not s.get("mitigated"):
            score = s.get("exploitability", 5)
            sev = "CRITICAL" if score >= 9 else "HIGH" if score >= 7 else "MEDIUM"
            out.append({
                "source": "red_team",
                "design_id": design_id,
                "design_name": design_name,
                "severity": sev,
                "title": f"[{s.get('id','RT')}] {s.get('name','Red team scenario')} (exploitability {score})",
                "detail": s.get("atlas_technique", ""),
                "created_at": ts,
            })
    return out


def _from_ato(report: dict, design_id: str, design_name: str) -> list[dict]:
    out = []
    raw = report.get("report_json") or "{}"
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            data = {}
    else:
        data = raw if isinstance(raw, dict) else {}

    items = data.get("items", [])
    ts = report.get("created_at", "")
    for item in items:
        if not item.get("passed"):
            sev = item.get("severity", "HIGH").upper()
            out.append({
                "source": "ato",
                "design_id": design_id,
                "design_name": design_name,
                "severity": sev,
                "title": f"[{item.get('id','ATO')}] {item.get('name','ATO check failed')}",
                "detail": item.get("remediation", ""),
                "created_at": ts,
            })
    return out


def _from_regulatory(report: dict, design_id: str, design_name: str) -> list[dict]:
    out = []
    raw = report.get("report_json") or "{}"
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            data = {}
    else:
        data = raw if isinstance(raw, dict) else {}

    gaps = data.get("gaps", [])
    ts = report.get("created_at", "")
    for g in gaps:
        if g.get("status") == "gap":
            sev = g.get("severity", "MEDIUM").upper()
            out.append({
                "source": "regulatory",
                "design_id": design_id,
                "design_name": design_name,
                "severity": sev,
                "title": f"[{g.get('id','REG')}] {g.get('name','Regulatory gap')}",
                "detail": g.get("requirement", ""),
                "created_at": ts,
            })
    return out
