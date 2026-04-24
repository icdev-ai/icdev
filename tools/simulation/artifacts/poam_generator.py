#!/usr/bin/env python3
# CUI // SP-CTI
"""POAM Generator — Plan of Action and Milestones generator.

Derives POA&M findings from a TFW session topology and/or AUDIT mode
walkthrough analysis. Each finding captures:
  - weakness / gap identified
  - risk level (Critical / High / Moderate / Low)
  - scheduled completion date
  - responsible party
  - remediation action

AUDIT mode calls this module to surface walkthrough findings as POA&M
candidates, which are persisted to session metadata.

Canvas handling mirrors ppsm_extractor:
  ndc  -> network / boundary findings
  sdc  -> API / software security findings
  eda  -> data / event stream findings

Public surface:
  generate_poam(session_id, canvas_type) -> dict
  add_poam_finding(session_id, finding: dict) -> dict
  get_poam_findings(session_id) -> list[dict]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parents[3]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.canvas.canvas_registry import get_display_name
from tools.db.storage import get_connection

# ---------------------------------------------------------------------------
# Canvas aliases
# ---------------------------------------------------------------------------

_CANVAS_ALIASES: dict[str, str] = {
    "bdc": "sdc",
    "idc": "ndc",
    "odc": "ndc",
    "ddc": "eda",
    "pdc": "eda",
    "qdc": "ndc",
    "mdc": "ndc",
}


def _resolve(canvas_type: str) -> str:
    ct = canvas_type.lower()
    return _CANVAS_ALIASES.get(ct, ct)


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _scheduled_completion(days: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Risk heuristics from node/edge labels
# ---------------------------------------------------------------------------

_CRITICAL_RE = re.compile(
    r"\broot\b|\bprivileged\b|\bsecret\b|\badmin\b|\bbypass\b|\bexploit\b"
    r"|\bunauthenticated\b|\bpublic.?rce\b",
    re.I,
)
_HIGH_RE = re.compile(
    r"\bno.?auth\b|\bopen\b.*\bport\b|\btelnet\b|\bftp\b|\bhttp\b(?!s)"
    r"|\bplaintext\b|\bcleartext\b|\bunencrypted\b|\bself.?signed\b",
    re.I,
)
_MODERATE_RE = re.compile(
    r"\bmissing\b|\bgap\b|\bweak\b|\bdeprecated\b|\blegacy\b|\bold.?version\b"
    r"|\bno.?mfa\b|\bpassword\b",
    re.I,
)


def _infer_risk(text: str) -> str:
    if _CRITICAL_RE.search(text):
        return "Critical"
    if _HIGH_RE.search(text):
        return "High"
    if _MODERATE_RE.search(text):
        return "Moderate"
    return "Low"


# ---------------------------------------------------------------------------
# Baseline POAM templates per canvas type
# ---------------------------------------------------------------------------

_NDC_FINDINGS = [
    {
        "weakness": "Unverified firewall rule coverage for all inter-zone traffic",
        "risk_level": "High",
        "remediation": "Audit all ACL/firewall rules; remove any 'permit any any' entries.",
        "days": 30,
    },
    {
        "weakness": "Missing network segmentation for PII/CUI data flows",
        "risk_level": "Moderate",
        "remediation": "Implement VLAN/subnet isolation for sensitive data paths.",
        "days": 60,
    },
    {
        "weakness": "No network monitoring / SIEM coverage detected",
        "risk_level": "High",
        "remediation": "Deploy SIEM agent on all perimeter nodes; enable flow logging.",
        "days": 45,
    },
]

_SDC_FINDINGS = [
    {
        "weakness": "API endpoints lacking mTLS or strong authentication",
        "risk_level": "High",
        "remediation": "Enforce mTLS on all service-to-service calls; add OAuth2 for external.",
        "days": 30,
    },
    {
        "weakness": "Missing input validation on inbound API payloads",
        "risk_level": "Moderate",
        "remediation": "Integrate schema validation (OpenAPI) at API gateway layer.",
        "days": 45,
    },
    {
        "weakness": "No rate limiting configured on public-facing endpoints",
        "risk_level": "Moderate",
        "remediation": "Configure rate limits and WAF rules at API gateway.",
        "days": 30,
    },
]

_EDA_FINDINGS = [
    {
        "weakness": "Topic ACLs not enforced; any consumer can read all topics",
        "risk_level": "High",
        "remediation": "Configure topic-level ACL for each producer/consumer pair.",
        "days": 30,
    },
    {
        "weakness": "Events containing PII published without field-level encryption",
        "risk_level": "High",
        "remediation": "Apply field-level encryption for PII fields before publish.",
        "days": 45,
    },
    {
        "weakness": "No dead-letter queue (DLQ) monitoring for failed message processing",
        "risk_level": "Low",
        "remediation": "Create DLQ alerts; add retry/poison-pill logic to consumers.",
        "days": 60,
    },
]

_BASELINE_FINDINGS: dict[str, list[dict]] = {
    "ndc": _NDC_FINDINGS,
    "sdc": _SDC_FINDINGS,
    "eda": _EDA_FINDINGS,
}


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------


def _load_session(conn, session_id: str) -> dict[str, Any]:
    row = conn.execute(
        "SELECT id, canvas_type, topology_id, mode, metadata "
        "FROM nc_simulation_sessions WHERE id = ?",
        (session_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"nc_simulation_sessions: session not found: {session_id}")
    return dict(row)


def _load_graph_json(conn, session: dict[str, Any]) -> dict[str, Any]:
    meta_raw = session.get("metadata") or "{}"
    try:
        meta = json.loads(meta_raw) if isinstance(meta_raw, str) else meta_raw
    except json.JSONDecodeError:
        meta = {}
    if "refined_graph_json" in meta:
        return meta["refined_graph_json"]
    topology_id = session.get("topology_id")
    if topology_id:
        row = conn.execute(
            "SELECT graph_json FROM topologies WHERE id = ?", (topology_id,)
        ).fetchone()
        if row and row[0]:
            try:
                return json.loads(row[0]) if isinstance(row[0], str) else row[0]
            except json.JSONDecodeError:
                pass
    return {"nodes": [], "edges": []}


def _persist_poam_findings(conn, session_id: str, findings: list[dict]) -> None:
    """Persist POA&M findings to session metadata."""
    row = conn.execute(
        "SELECT metadata FROM nc_simulation_sessions WHERE id = ?", (session_id,)
    ).fetchone()
    if not row:
        return
    meta_raw = row[0] or "{}"
    try:
        meta = json.loads(meta_raw) if isinstance(meta_raw, str) else meta_raw
    except json.JSONDecodeError:
        meta = {}
    existing = meta.get("poam_findings", [])
    existing_ids = {f.get("finding_id") for f in existing}
    for f in findings:
        if f.get("finding_id") not in existing_ids:
            existing.append(f)
    meta["poam_findings"] = existing
    conn.execute(
        "UPDATE nc_simulation_sessions SET metadata = ? WHERE id = ?",
        (json.dumps(meta), session_id),
    )
    conn.commit()


def _load_persisted_findings(conn, session_id: str) -> list[dict]:
    row = conn.execute(
        "SELECT metadata FROM nc_simulation_sessions WHERE id = ?", (session_id,)
    ).fetchone()
    if not row:
        return []
    meta_raw = row[0] or "{}"
    try:
        meta = json.loads(meta_raw) if isinstance(meta_raw, str) else meta_raw
    except json.JSONDecodeError:
        meta = {}
    return meta.get("poam_findings", [])


# ---------------------------------------------------------------------------
# Finding builders
# ---------------------------------------------------------------------------


def _build_topology_findings(nodes: list[dict], edges: list[dict], resolved: str) -> list[dict]:
    """Derive additional findings from topology labels."""
    findings: list[dict] = []
    all_labels = [n.get("label", "") for n in nodes] + [e.get("label", "") for e in edges]
    for label in all_labels:
        risk = _infer_risk(label)
        if risk in ("Critical", "High"):
            findings.append(
                {
                    "finding_id": f"F-{uuid.uuid4().hex[:8].upper()}",
                    "weakness": f"Risk indicator detected in topology label: '{label}'",
                    "risk_level": risk,
                    "remediation": "Review and remediate the identified topology element.",
                    "scheduled_completion": _scheduled_completion(30 if risk == "Critical" else 45),
                    "responsible_party": "System Owner",
                    "status": "Open",
                    "source": "topology-scan",
                }
            )
    return findings


def _build_baseline_findings(resolved: str) -> list[dict]:
    templates = _BASELINE_FINDINGS.get(resolved, _NDC_FINDINGS)
    findings: list[dict] = []
    for tmpl in templates:
        findings.append(
            {
                "finding_id": f"F-{uuid.uuid4().hex[:8].upper()}",
                "weakness": tmpl["weakness"],
                "risk_level": tmpl["risk_level"],
                "remediation": tmpl["remediation"],
                "scheduled_completion": _scheduled_completion(tmpl["days"]),
                "responsible_party": "ISSM",
                "status": "Open",
                "source": "baseline",
            }
        )
    return findings


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_poam(session_id: str, canvas_type: str) -> dict:
    """Generate POA&M for a TFW session.

    Returns:
        dict with keys: poam_id, session_id, canvas_type, canvas_display,
        findings (list), total, critical, high, moderate, low, markdown
    """
    resolved = _resolve(canvas_type)
    conn = get_connection()
    try:
        session = _load_session(conn, session_id)
        graph = _load_graph_json(conn, session)
        persisted = _load_persisted_findings(conn, session_id)
    finally:
        conn.close()

    nodes: list[dict] = graph.get("nodes", [])
    edges: list[dict] = graph.get("edges", [])

    # Combine persisted + new baseline + topology-derived
    persisted_ids = {f.get("finding_id") for f in persisted}
    new_findings: list[dict] = []
    for f in _build_baseline_findings(resolved):
        if f["finding_id"] not in persisted_ids:
            new_findings.append(f)
    for f in _build_topology_findings(nodes, edges, resolved):
        if f["finding_id"] not in persisted_ids:
            new_findings.append(f)

    all_findings = persisted + new_findings

    # Persist new findings back to session
    if new_findings:
        conn = get_connection()
        try:
            _persist_poam_findings(conn, session_id, all_findings)
        except Exception:
            pass
        finally:
            conn.close()

    risk_counts: dict[str, int] = {"Critical": 0, "High": 0, "Moderate": 0, "Low": 0}
    for f in all_findings:
        risk_counts[f.get("risk_level", "Low")] = risk_counts.get(f.get("risk_level", "Low"), 0) + 1

    canvas_display = get_display_name(canvas_type)
    poam_id = f"POAM-{session_id[:8].upper()}"

    # Markdown
    def _risk_badge(r: str) -> str:
        icons = {"Critical": "🔴", "High": "🟠", "Moderate": "🟡", "Low": "🟢"}
        return icons.get(r, "⚪")

    rows = []
    for i, f in enumerate(all_findings, 1):
        rows.append(
            f"**{i}. {_risk_badge(f['risk_level'])} [{f['risk_level']}]** "
            f"`{f['finding_id']}`\n"
            f"  - **Weakness:** {f['weakness']}\n"
            f"  - **Remediation:** {f['remediation']}\n"
            f"  - **Due:** {f['scheduled_completion']} | "
            f"**Owner:** {f['responsible_party']} | "
            f"**Status:** {f['status']}"
        )

    markdown = (
        f"## POA&M — {poam_id}\n\n"
        f"**Canvas:** {canvas_display} | "
        f"**Total:** {len(all_findings)} finding(s) | "
        f"Critical: {risk_counts['Critical']} | "
        f"High: {risk_counts['High']} | "
        f"Moderate: {risk_counts['Moderate']} | "
        f"Low: {risk_counts['Low']}\n\n"
        + "\n\n".join(rows)
    )

    return {
        "poam_id": poam_id,
        "session_id": session_id,
        "canvas_type": canvas_type,
        "canvas_display": canvas_display,
        "findings": all_findings,
        "total": len(all_findings),
        "critical": risk_counts["Critical"],
        "high": risk_counts["High"],
        "moderate": risk_counts["Moderate"],
        "low": risk_counts["Low"],
        "markdown": markdown,
    }


def add_poam_finding(session_id: str, finding: dict) -> dict:
    """Add a single POA&M finding to a session (used by AUDIT mode).

    The finding dict should include: weakness, risk_level, remediation,
    scheduled_completion, responsible_party, source.
    """
    finding.setdefault("finding_id", f"F-{uuid.uuid4().hex[:8].upper()}")
    finding.setdefault("status", "Open")
    finding.setdefault("responsible_party", "ISSM")
    finding.setdefault("scheduled_completion", _scheduled_completion(45))
    finding.setdefault("source", "audit")
    conn = get_connection()
    try:
        _persist_poam_findings(conn, session_id, [finding])
    finally:
        conn.close()
    return finding


def get_poam_findings(session_id: str) -> list[dict]:
    """Return all persisted POA&M findings for a session."""
    conn = get_connection()
    try:
        return _load_persisted_findings(conn, session_id)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Generate POA&M for a TFW session.")
    p.add_argument("--session-id", required=True)
    p.add_argument("--canvas-type", required=True)
    p.add_argument("--json", action="store_true")
    return p


def main(argv: list[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)
    result = generate_poam(args.session_id, args.canvas_type)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(result["markdown"])


if __name__ == "__main__":
    main()
