# CUI // SP-CTI
"""Canvas Auto-Remediation Engine — confidence-tiered fix/suggest/escalate.

Provides deterministic (no LLM) auto-remediation for canvas assessment
findings using the ICDEV self-healing confidence tiers:
  >= 0.7  auto-fix   (add missing node/edge to graph)
  0.3-0.7 suggest    (return recommendation, no mutation)
  < 0.3   escalate   (return escalation notice)

CAT1 findings always escalate (0.3), CAT2 suggest (0.5), CAT3 auto-fix (0.8).
Max 5 auto-remediations per hour (module-level counter).
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import json
import sqlite3
import uuid
from tools.db.storage import get_connection
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = get_logger("icdev.canvas.auto_remediate")

# ---------------------------------------------------------------------------
# Rate limiter — max 5 auto-fix actions per hour (module-level)
# ---------------------------------------------------------------------------
_auto_fix_timestamps: list[datetime] = []
_MAX_AUTO_FIXES_PER_HOUR = 5


def _check_rate_limit() -> bool:
    """Return True if another auto-fix is allowed, False if rate-limited."""
    now = datetime.now(timezone.utc)
    cutoff = now.timestamp() - 3600
    # Prune old entries
    _auto_fix_timestamps[:] = [t for t in _auto_fix_timestamps if t.timestamp() > cutoff]
    return len(_auto_fix_timestamps) < _MAX_AUTO_FIXES_PER_HOUR


def _record_auto_fix() -> None:
    _auto_fix_timestamps.append(datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
# Canvas DB mapping
# ---------------------------------------------------------------------------
_CANVAS_DB_MAP: dict[str, tuple[str, str]] = {
    "idc": ("infra_canvas.db", "idc_assessments"),
    "odc": ("observability_canvas.db", "odc_assessments"),
}


def _data_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "data"


def _load_findings_from_db(canvas_key: str, design_id: str) -> list[dict[str, Any]]:
    """Load findings from the canvas assessment DB table."""
    mapping = _CANVAS_DB_MAP.get(canvas_key)
    if not mapping:
        logger.warning("No DB mapping for canvas_key=%s", canvas_key)
        return []

    db_name, table_name = mapping
    db_path = _data_dir() / db_name
    if not db_path.exists():
        logger.warning("Database not found: %s", db_path)
        return []

    try:
        conn = get_connection(str(db_path))
        row = conn.execute(
            f"SELECT findings_json FROM {table_name} WHERE design_id = ? ORDER BY created_at DESC LIMIT 1",  # noqa: S608
            (design_id,),
        ).fetchone()
        conn.close()
        if row and row["findings_json"]:
            data = json.loads(row["findings_json"])
            return data if isinstance(data, list) else []
    except (sqlite3.OperationalError, json.JSONDecodeError, KeyError) as exc:
        logger.warning("Failed to load findings from %s: %s", db_path, exc)
    return []


# ---------------------------------------------------------------------------
# Severity → confidence mapping
# ---------------------------------------------------------------------------
_SEVERITY_CONFIDENCE: dict[str, float] = {
    "CAT1": 0.3,   # always escalate
    "CAT2": 0.5,   # suggest
    "CAT3": 0.8,   # auto-fix
}


def _confidence_for_finding(finding: dict[str, Any]) -> float:
    severity = finding.get("severity", "CAT2").upper()
    return _SEVERITY_CONFIDENCE.get(severity, 0.5)


# ---------------------------------------------------------------------------
# IDC remediation rules (rule_id pattern → action description + node type)
# ---------------------------------------------------------------------------
_IDC_RULES: dict[str, dict[str, str]] = {
    "encrypt": {
        "description": "Add KMS encryption node for data-at-rest/in-transit",
        "node_type": "kms",
        "node_label": "KMS Encryption",
    },
    "lb": {
        "description": "Add load balancer node for high-availability",
        "node_type": "load_balancer",
        "node_label": "Load Balancer",
    },
    "waf": {
        "description": "Add WAF node for web application firewall protection",
        "node_type": "waf",
        "node_label": "WAF",
    },
    "backup": {
        "description": "Add backup/DR node for disaster recovery",
        "node_type": "backup",
        "node_label": "Backup / DR",
    },
    "logging": {
        "description": "Add centralized logging node",
        "node_type": "logging",
        "node_label": "Centralized Logging",
    },
    "vpc": {
        "description": "Add VPC/network segmentation node",
        "node_type": "vpc",
        "node_label": "VPC Segmentation",
    },
    "mfa": {
        "description": "Add MFA/IAM identity node",
        "node_type": "iam",
        "node_label": "MFA / IAM",
    },
    "patch": {
        "description": "Add patch management node",
        "node_type": "patch_mgmt",
        "node_label": "Patch Management",
    },
}

# ---------------------------------------------------------------------------
# ODC remediation rules
# ---------------------------------------------------------------------------
_ODC_RULES: dict[str, dict[str, str]] = {
    "siem": {
        "description": "Add SIEM node for security event monitoring",
        "node_type": "siem",
        "node_label": "SIEM",
    },
    "audit": {
        "description": "Add audit trail node for immutable event logging",
        "node_type": "audit_trail",
        "node_label": "Audit Trail",
    },
    "alert": {
        "description": "Add alerting node for threshold-based notifications",
        "node_type": "alerting",
        "node_label": "Alerting",
    },
    "metric": {
        "description": "Add metrics collection node",
        "node_type": "metrics",
        "node_label": "Metrics Collector",
    },
    "trace": {
        "description": "Add distributed tracing node",
        "node_type": "tracing",
        "node_label": "Distributed Tracing",
    },
    "dashboard": {
        "description": "Add observability dashboard node",
        "node_type": "obs_dashboard",
        "node_label": "Observability Dashboard",
    },
    "log": {
        "description": "Add log aggregation node",
        "node_type": "log_aggregation",
        "node_label": "Log Aggregation",
    },
    "health": {
        "description": "Add health-check / heartbeat node",
        "node_type": "health_check",
        "node_label": "Health Check",
    },
}


def _match_rule(rule_id: str, rules: dict[str, dict[str, str]]) -> dict[str, str] | None:
    """Match a rule_id against known remediation patterns (substring match)."""
    rule_lower = rule_id.lower()
    for pattern, action in rules.items():
        if pattern in rule_lower:
            return action
    return None


# ---------------------------------------------------------------------------
# Graph mutation helpers
# ---------------------------------------------------------------------------

def _add_node_to_graph(graph: dict[str, Any], node_type: str, label: str) -> str:
    """Add a node to the graph dict (in-place). Returns new node id."""
    nodes = graph.setdefault("nodes", [])
    node_id = f"auto-{node_type}-{uuid.uuid4().hex[:8]}"
    nodes.append({
        "id": node_id,
        "type": node_type,
        "label": label,
        "auto_remediated": True,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    return node_id


def _add_edge_to_graph(graph: dict[str, Any], source: str, target: str, label: str = "auto-fix") -> str:
    """Add an edge to the graph dict (in-place). Returns edge id."""
    edges = graph.setdefault("edges", [])
    edge_id = f"auto-edge-{uuid.uuid4().hex[:8]}"
    edges.append({
        "id": edge_id,
        "source": source,
        "target": target,
        "label": label,
        "auto_remediated": True,
    })
    return edge_id


# ---------------------------------------------------------------------------
# Core remediation engine
# ---------------------------------------------------------------------------

def auto_remediate(
    canvas_key: str,
    design_id: str,
    graph: dict[str, Any],
    findings: list[dict[str, Any]],
    rules: dict[str, dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Generic canvas auto-remediation engine.

    Processes each finding against the rule set, applying the confidence-tiered
    approach.  Mutates *graph* in-place for auto-fix actions.

    Parameters
    ----------
    canvas_key : str
        Canvas identifier (e.g., ``"idc"``, ``"odc"``).
    design_id : str
        The design being remediated.
    graph : dict
        Mutable graph dict (``{"nodes": [...], "edges": [...]}``).
    findings : list[dict]
        Assessment findings, each with ``rule_id``, ``title``, ``severity``.
    rules : dict | None
        Remediation rule map.  Defaults to IDC rules if *canvas_key* is
        ``"idc"``, ODC rules for ``"odc"``.

    Returns
    -------
    dict
        Result with ``status``, counts, ``actions``, ``nodes_added``,
        ``edges_added``, and ``rate_limited`` flag.
    """
    if rules is None:
        if canvas_key == "odc":
            rules = _ODC_RULES
        else:
            rules = _IDC_RULES

    actions: list[dict[str, Any]] = []
    nodes_added: list[str] = []
    edges_added: list[str] = []
    auto_fixed = 0
    suggested = 0
    escalated = 0
    rate_limited = False

    for finding in findings:
        rule_id = finding.get("rule_id", "")
        title = finding.get("title", rule_id)
        confidence = _confidence_for_finding(finding)
        matched_rule = _match_rule(rule_id, rules)

        if confidence >= 0.7:
            # Auto-fix tier
            if not _check_rate_limit():
                rate_limited = True
                actions.append({
                    "finding_rule_id": rule_id,
                    "action": "escalate",
                    "confidence": confidence,
                    "description": f"Rate limited — cannot auto-fix: {title}",
                })
                escalated += 1
                continue

            if matched_rule:
                node_id = _add_node_to_graph(graph, matched_rule["node_type"], matched_rule["node_label"])
                nodes_added.append(node_id)
                _record_auto_fix()

                # Connect to first existing node if any
                existing_nodes = graph.get("nodes", [])
                if len(existing_nodes) > 1:
                    target = existing_nodes[0]["id"]
                    if target != node_id:
                        eid = _add_edge_to_graph(graph, node_id, target, f"auto-fix-{matched_rule['node_type']}")
                        edges_added.append(eid)

                actions.append({
                    "finding_rule_id": rule_id,
                    "action": "auto_fix",
                    "confidence": confidence,
                    "description": f"Auto-fixed: {matched_rule['description']}",
                })
                auto_fixed += 1
            else:
                # No matching rule — suggest instead
                actions.append({
                    "finding_rule_id": rule_id,
                    "action": "suggest",
                    "confidence": confidence,
                    "description": f"No auto-fix rule for '{rule_id}' — manual review recommended: {title}",
                })
                suggested += 1

        elif confidence >= 0.3:
            # Suggest tier
            desc = matched_rule["description"] if matched_rule else f"Review finding: {title}"
            actions.append({
                "finding_rule_id": rule_id,
                "action": "suggest",
                "confidence": confidence,
                "description": f"Suggested: {desc}",
            })
            suggested += 1

        else:
            # Escalate tier
            actions.append({
                "finding_rule_id": rule_id,
                "action": "escalate",
                "confidence": confidence,
                "description": f"Escalated (confidence {confidence:.2f}): {title} — requires human review",
            })
            escalated += 1

    result: dict[str, Any] = {
        "status": "ok",
        "canvas": canvas_key,
        "design_id": design_id,
        "total_findings": len(findings),
        "auto_fixed": auto_fixed,
        "suggested": suggested,
        "escalated": escalated,
        "actions": actions,
        "fixes_applied": auto_fixed,
        "nodes_added": nodes_added,
        "edges_added": edges_added,
        "rate_limited": rate_limited,
    }
    logger.info(
        "auto_remediate canvas=%s design=%s total=%d fixed=%d suggested=%d escalated=%d",
        canvas_key, design_id, len(findings), auto_fixed, suggested, escalated,
    )
    return result


# ---------------------------------------------------------------------------
# Canvas-specific wrappers
# ---------------------------------------------------------------------------

def auto_remediate_idc(
    design_id: str,
    graph: dict[str, Any],
    findings: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """IDC (Infrastructure Design Canvas) auto-remediation wrapper.

    Parameters
    ----------
    design_id : str
        Infrastructure design identifier.
    graph : dict
        Mutable graph dict — nodes/edges are added in-place for auto-fixes.
    findings : list[dict] | None
        Assessment findings.  If ``None``, loaded from ``idc_assessments`` table.

    Returns
    -------
    dict
        Remediation result (see :func:`auto_remediate`).
    """
    if findings is None:
        findings = _load_findings_from_db("idc", design_id)
    return auto_remediate("idc", design_id, graph, findings, rules=_IDC_RULES)


def auto_remediate_odc(
    design_id: str,
    graph: dict[str, Any],
    findings: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """ODC (Observability Design Canvas) auto-remediation wrapper.

    Parameters
    ----------
    design_id : str
        Observability design identifier.
    graph : dict
        Mutable graph dict — nodes/edges are added in-place for auto-fixes.
    findings : list[dict] | None
        Assessment findings.  If ``None``, loaded from ``odc_assessments`` table.

    Returns
    -------
    dict
        Remediation result (see :func:`auto_remediate`).
    """
    if findings is None:
        findings = _load_findings_from_db("odc", design_id)
    return auto_remediate("odc", design_id, graph, findings, rules=_ODC_RULES)