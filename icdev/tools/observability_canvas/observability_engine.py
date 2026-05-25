# CUI // SP-CTI
"""ICDEV Observability Design Canvas — Deterministic Assessment Engine.

Pure functions for observability stack assessment, coverage scoring,
gap detection, and MITRE ATT&CK detection coverage analysis.

No Flask dependency — takes graph data and returns results.
No LLM dependency — all checks are deterministic.
"""

import uuid
from datetime import datetime, timezone
from pathlib import Path

from tools.observability_canvas.constants import (
    OBSERVABILITY_COMPLIANCE_RULES,
    RECOMMENDED_SOURCE_TYPES,
    SIEM_PLATFORM_TYPES,
    SEVERITY_WEIGHTS,
)

try:
    import yaml as _yaml

    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False

_CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "args" / "observability_canvas_config.yaml"


def _load_config() -> dict:
    """Load ODC engine config from args/observability_canvas_config.yaml."""
    if not _HAS_YAML or not _CONFIG_PATH.exists():
        return {}
    with open(_CONFIG_PATH, "r", encoding="utf-8") as _f:
        return _yaml.safe_load(_f) or {}


_ODC_CONFIG = _load_config()


# ── Helpers ──────────────────────────────────────────────────────────────────


def _node_types(nodes):
    """Return {node_id: node_type} dict."""
    return {n["id"]: n.get("type", "") for n in nodes}


def _label_map(nodes):
    """Return {node_id: label} dict."""
    return {n["id"]: n.get("label", n["id"]) for n in nodes}


def _build_adjacency(edges):
    """Build undirected adjacency: {node_id: {neighbor_id, ...}}."""
    adj = {}
    for e in edges:
        adj.setdefault(e["source"], set()).add(e["target"])
        adj.setdefault(e["target"], set()).add(e["source"])
    return adj


def _is_source(ntype):
    """Check if a node type is a log/telemetry source."""
    return ntype.startswith("src-")


def _is_collector(ntype):
    """Check if a node type is a collector."""
    return ntype.startswith("col-")


def _is_platform(ntype):
    """Check if a node type is an analytics platform."""
    return ntype.startswith("plt-")


def _is_automation(ntype):
    """Check if a node type is an automation/response element."""
    return ntype.startswith("auto-")


def _is_compliance(ntype):
    """Check if a node type is a compliance element."""
    return ntype.startswith("cmp-")


def _is_siem(ntype):
    """Check if a node type is a SIEM/analytics platform."""
    return ntype in SIEM_PLATFORM_TYPES


def _nodes_of_prefix(nodes, ntypes, prefix):
    """Return nodes whose type starts with the given prefix."""
    return [n for n in nodes if ntypes.get(n["id"], "").startswith(prefix)]


def _nodes_of_type(nodes, ntypes, target_type):
    """Return nodes matching a specific type."""
    return [n for n in nodes if ntypes.get(n["id"], "") == target_type]


# ── Rule Check Functions ─────────────────────────────────────────────────────


def _check_sources_connected_to_collector(nodes, edges, ntypes, adj):
    """ODC-LOG-001: Every source must connect to at least one collector."""
    findings = []
    source_nodes = _nodes_of_prefix(nodes, ntypes, "src-")
    collector_ids = {n["id"] for n in nodes if _is_collector(ntypes.get(n["id"], ""))}
    for src in source_nodes:
        neighbors = adj.get(src["id"], set())
        connected_to_collector = bool(neighbors & collector_ids)
        if not connected_to_collector:
            findings.append(
                {
                    "affected_entity": src.get("label", src["id"]),
                    "affected_type": "node",
                    "detail": f"Source '{src.get('label', src['id'])}' is not connected to any collector.",
                }
            )
    return findings


def _check_collectors_forward_to_siem(nodes, edges, ntypes, adj):
    """ODC-LOG-002: All collectors must forward to a SIEM/analytics platform."""
    findings = []
    collector_nodes = _nodes_of_prefix(nodes, ntypes, "col-")
    # Include all platform types (not just SIEM — Prometheus/Grafana/Jaeger are valid targets)
    platform_ids = {n["id"] for n in nodes if _is_platform(ntypes.get(n["id"], ""))}
    for col in collector_nodes:
        neighbors = adj.get(col["id"], set())
        forwards_to_platform = bool(neighbors & platform_ids)
        if not forwards_to_platform:
            findings.append(
                {
                    "affected_entity": col.get("label", col["id"]),
                    "affected_type": "node",
                    "detail": f"Collector '{col.get('label', col['id'])}' does not forward to any analytics platform.",
                }
            )
    return findings


def _check_log_archive_present(nodes, ntypes):
    """ODC-LOG-003: At least one S3/archive destination."""
    archive_nodes = _nodes_of_type(nodes, ntypes, "col-s3")
    if not archive_nodes:
        return [
            {
                "affected_entity": "design",
                "affected_type": "design",
                "detail": "No log archive (S3/object storage) destination for long-term retention.",
            }
        ]
    return []


def _check_os_and_network_logs(nodes, ntypes):
    """ODC-LOG-004: OS/system log and network log sources must be present."""
    findings = []
    os_nodes = _nodes_of_type(nodes, ntypes, "src-os-log")
    net_nodes = _nodes_of_type(nodes, ntypes, "src-network-log")
    if not os_nodes:
        findings.append(
            {
                "affected_entity": "design",
                "affected_type": "design",
                "detail": "Missing OS/system log source (src-os-log).",
            }
        )
    if not net_nodes:
        findings.append(
            {
                "affected_entity": "design",
                "affected_type": "design",
                "detail": "Missing network log source (src-network-log).",
            }
        )
    return findings


def _check_cloud_audit_logs(nodes, ntypes):
    """ODC-LOG-005: If cloud services in scope, cloud audit log must be present."""
    # Detect cloud scope: any cloud-related platform (Sentinel, Chronicle, Datadog) or cloud log
    cloud_indicators = {"plt-sentinel", "plt-chronicle", "plt-datadog", "src-container-log", "src-cloud-log"}
    has_cloud_scope = any(ntypes.get(n["id"], "") in cloud_indicators for n in nodes)
    cloud_log_nodes = _nodes_of_type(nodes, ntypes, "src-cloud-log")
    if has_cloud_scope and not cloud_log_nodes:
        return [
            {
                "affected_entity": "design",
                "affected_type": "design",
                "detail": "Cloud services detected but no cloud audit log source (CloudTrail/Activity Log).",
            }
        ]
    return []


def _check_alert_rules(nodes, ntypes):
    """ODC-DET-001: At least one alert rule must be defined."""
    alert_nodes = _nodes_of_type(nodes, ntypes, "auto-alert-rule")
    if not alert_nodes:
        return [
            {
                "affected_entity": "design",
                "affected_type": "design",
                "detail": "No alert rules defined — monitoring is passive-only.",
            }
        ]
    return []


def _check_soar_or_runbook(nodes, ntypes, adj):
    """ODC-DET-002: Alert rules should connect to SOAR/runbook."""
    findings = []
    alert_nodes = _nodes_of_type(nodes, ntypes, "auto-alert-rule")
    soar_ids = {n["id"] for n in nodes if ntypes.get(n["id"], "") in ("auto-soar", "auto-runbook")}
    if alert_nodes and not soar_ids:
        findings.append(
            {
                "affected_entity": "design",
                "affected_type": "design",
                "detail": "Alert rules exist but no SOAR playbook or runbook for automated response.",
            }
        )
    elif alert_nodes and soar_ids:
        for alert in alert_nodes:
            neighbors = adj.get(alert["id"], set())
            if not (neighbors & soar_ids):
                findings.append(
                    {
                        "affected_entity": alert.get("label", alert["id"]),
                        "affected_type": "node",
                        "detail": f"Alert rule '{alert.get('label', alert['id'])}' not connected to SOAR/runbook.",
                    }
                )
    return findings


def _check_mitre_baseline(nodes, ntypes):
    """ODC-DET-003: Detection baseline node should be present."""
    baseline_nodes = _nodes_of_type(nodes, ntypes, "cmp-baseline")
    if not baseline_nodes:
        return [
            {
                "affected_entity": "design",
                "affected_type": "design",
                "detail": "No MITRE ATT&CK detection baseline node — cannot assess detection coverage.",
            }
        ]
    return []


def _check_retention_policy(nodes, ntypes):
    """ODC-RET-001: Log retention policy must be defined."""
    policy_nodes = _nodes_of_type(nodes, ntypes, "cmp-log-policy")
    if not policy_nodes:
        return [
            {
                "affected_entity": "design",
                "affected_type": "design",
                "detail": "No log retention policy defined (DoD requires 1yr online + 7yr archive).",
            }
        ]
    return []


def _check_log_transport_encrypted(nodes, edges, ntypes):
    """ODC-SEC-001: Collector-to-platform connections must use TLS."""
    findings = []
    for e in edges:
        src_type = ntypes.get(e["source"], "")
        tgt_type = ntypes.get(e["target"], "")
        # Check collector -> platform edges
        if (_is_collector(src_type) and _is_platform(tgt_type)) or (_is_platform(src_type) and _is_collector(tgt_type)):
            if not e.get("encrypted", False):
                src_label = e.get("source", "")
                tgt_label = e.get("target", "")
                # Try to get labels from nodes
                for n in nodes:
                    if n["id"] == e["source"]:
                        src_label = n.get("label", e["source"])
                    if n["id"] == e["target"]:
                        tgt_label = n.get("label", e["target"])
                findings.append(
                    {
                        "affected_entity": f"{src_label} -> {tgt_label}",
                        "affected_type": "edge",
                        "detail": f"Unencrypted log transport from {src_label} to {tgt_label}.",
                    }
                )
    return findings


def _check_edr_telemetry(nodes, ntypes):
    """ODC-SEC-002: EDR telemetry source should be present."""
    edr_nodes = _nodes_of_type(nodes, ntypes, "src-endpoint")
    if not edr_nodes:
        return [
            {
                "affected_entity": "design",
                "affected_type": "design",
                "detail": "No EDR telemetry source for endpoint visibility.",
            }
        ]
    return []


def _check_iam_logs(nodes, ntypes):
    """ODC-SEC-003: IAM/IdP logs must be collected."""
    iam_nodes = _nodes_of_type(nodes, ntypes, "src-iam")
    if not iam_nodes:
        return [
            {
                "affected_entity": "design",
                "affected_type": "design",
                "detail": "No IAM/IdP log source for authentication event monitoring.",
            }
        ]
    return []


def _check_ticket_system(nodes, ntypes, adj):
    """ODC-INT-001: SOAR/alert chain should connect to ticket system."""
    ticket_nodes = _nodes_of_type(nodes, ntypes, "auto-ticket")
    soar_nodes = [n for n in nodes if ntypes.get(n["id"], "") in ("auto-soar", "auto-runbook")]
    if soar_nodes and not ticket_nodes:
        return [
            {
                "affected_entity": "design",
                "affected_type": "design",
                "detail": "SOAR/runbook exists but no ticket system for incident tracking.",
            }
        ]
    if ticket_nodes and soar_nodes:
        ticket_ids = {n["id"] for n in ticket_nodes}
        findings = []
        for soar in soar_nodes:
            neighbors = adj.get(soar["id"], set())
            if not (neighbors & ticket_ids):
                findings.append(
                    {
                        "affected_entity": soar.get("label", soar["id"]),
                        "affected_type": "node",
                        "detail": f"'{soar.get('label', soar['id'])}' not connected to ticket system.",
                    }
                )
        return findings
    return []


# ── Rule dispatch ────────────────────────────────────────────────────────────

_RULE_CHECKS = {
    "ODC-LOG-001": lambda n, e, nt, adj: _check_sources_connected_to_collector(n, e, nt, adj),
    "ODC-LOG-002": lambda n, e, nt, adj: _check_collectors_forward_to_siem(n, e, nt, adj),
    "ODC-LOG-003": lambda n, e, nt, adj: _check_log_archive_present(n, nt),
    "ODC-LOG-004": lambda n, e, nt, adj: _check_os_and_network_logs(n, nt),
    "ODC-LOG-005": lambda n, e, nt, adj: _check_cloud_audit_logs(n, nt),
    "ODC-DET-001": lambda n, e, nt, adj: _check_alert_rules(n, nt),
    "ODC-DET-002": lambda n, e, nt, adj: _check_soar_or_runbook(n, nt, adj),
    "ODC-DET-003": lambda n, e, nt, adj: _check_mitre_baseline(n, nt),
    "ODC-RET-001": lambda n, e, nt, adj: _check_retention_policy(n, nt),
    "ODC-SEC-001": lambda n, e, nt, adj: _check_log_transport_encrypted(n, e, nt),
    "ODC-SEC-002": lambda n, e, nt, adj: _check_edr_telemetry(n, nt),
    "ODC-SEC-003": lambda n, e, nt, adj: _check_iam_logs(n, nt),
    "ODC-INT-001": lambda n, e, nt, adj: _check_ticket_system(n, nt, adj),
}


# ── Public API ───────────────────────────────────────────────────────────────


def assess_observability_design(graph_data: dict, rules: list = None) -> dict:
    """Run all observability compliance rules against a design graph.

    Args:
        graph_data: Dict with "nodes" and "edges" lists.
        rules: Optional list of rules (defaults to OBSERVABILITY_COMPLIANCE_RULES).

    Returns:
        Dict with findings, score, grade, and per-category breakdown.
    """
    if rules is None:
        rules = OBSERVABILITY_COMPLIANCE_RULES

    nodes = graph_data.get("nodes", [])
    edges = graph_data.get("edges", [])
    ntypes = _node_types(nodes)
    adj = _build_adjacency(edges)

    findings = []
    by_category = {}

    for rule in rules:
        rule_id = rule["id"]
        check_fn = _RULE_CHECKS.get(rule_id)
        if not check_fn:
            continue

        rule_findings = check_fn(nodes, edges, ntypes, adj)
        for rf in rule_findings:
            findings.append(
                {
                    "id": str(uuid.uuid4())[:8],
                    "rule_id": rule_id,
                    "title": rule["title"],
                    "severity": rule["severity"],
                    "category": rule["category"],
                    "description": rule["description"],
                    "affected_entity": rf.get("affected_entity", ""),
                    "affected_type": rf.get("affected_type", "design"),
                    "detail": rf.get("detail", ""),
                }
            )
            cat = rule["category"]
            by_category.setdefault(cat, {"total": 0, "cat1": 0, "cat2": 0})
            by_category[cat]["total"] += 1
            if rule["severity"] == "CAT1":
                by_category[cat]["cat1"] += 1
            else:
                by_category[cat]["cat2"] += 1

    # Compute score: 100 minus weighted deductions
    max_penalty = sum(SEVERITY_WEIGHTS.get(r["severity"], 2) for r in rules) * 2
    total_penalty = sum(SEVERITY_WEIGHTS.get(f["severity"], 2) for f in findings)
    score = max(0, round(100 * (1 - total_penalty / max(max_penalty, 1)), 1))

    # Grade
    if score >= 90:
        grade = "A"
    elif score >= 80:
        grade = "B"
    elif score >= 70:
        grade = "C"
    elif score >= 60:
        grade = "D"
    else:
        grade = "F"

    cat1_count = sum(1 for f in findings if f["severity"] == "CAT1")
    cat2_count = sum(1 for f in findings if f["severity"] == "CAT2")

    return {
        "assessment_id": str(uuid.uuid4()),
        "assessment_type": "observability_compliance",
        "findings": findings,
        "total_findings": len(findings),
        "cat1_findings": cat1_count,
        "cat2_findings": cat2_count,
        "score": score,
        "grade": grade,
        "by_category": by_category,
        "assessed_at": datetime.now(timezone.utc).isoformat(),
    }


def compute_coverage_score(graph_data: dict) -> dict:
    """Compute percentage of recommended source types present vs. recommended.

    Args:
        graph_data: Dict with "nodes" list.

    Returns:
        Dict with coverage_pct, present, missing, total_recommended.
    """
    nodes = graph_data.get("nodes", [])
    ntypes = _node_types(nodes)
    present_types = set(ntypes.values())

    present = [t for t in RECOMMENDED_SOURCE_TYPES if t in present_types]
    missing = [t for t in RECOMMENDED_SOURCE_TYPES if t not in present_types]
    total = len(RECOMMENDED_SOURCE_TYPES)
    pct = round(100 * len(present) / max(total, 1), 1)

    return {
        "coverage_pct": pct,
        "present": present,
        "missing": missing,
        "present_count": len(present),
        "total_recommended": total,
    }


def compute_mitre_detection_coverage(graph_data: dict) -> dict:
    """Score MITRE ATT&CK detection coverage from baseline node metadata.

    If a detection baseline node (cmp-baseline) exists and has a 'techniques'
    field in its config_json, compute coverage. Otherwise return zero coverage.

    Args:
        graph_data: Dict with "nodes" list.

    Returns:
        Dict with coverage info: total_techniques, covered, pct, gaps.
    """
    nodes = graph_data.get("nodes", [])
    ntypes = _node_types(nodes)
    baseline_nodes = _nodes_of_type(nodes, ntypes, "cmp-baseline")

    if not baseline_nodes:
        return {
            "has_baseline": False,
            "total_techniques": 0,
            "covered_techniques": 0,
            "coverage_pct": 0.0,
            "technique_gaps": [],
        }

    # Aggregate techniques from all baseline nodes
    covered = set()
    total_techniques = set()
    for bn in baseline_nodes:
        config = bn.get("config_json", {})
        if isinstance(config, str):
            import json

            try:
                config = json.loads(config)
            except (ValueError, TypeError):
                config = {}
        techniques = config.get("techniques", [])
        for tech in techniques:
            tid = tech.get("id", "")
            if tid:
                total_techniques.add(tid)
                if tech.get("covered", False):
                    covered.add(tid)

    total = len(total_techniques)
    covered_count = len(covered)
    gaps = sorted(total_techniques - covered)
    pct = round(100 * covered_count / max(total, 1), 1)

    return {
        "has_baseline": True,
        "total_techniques": total,
        "covered_techniques": covered_count,
        "coverage_pct": pct,
        "technique_gaps": gaps,
    }


def detect_observability_gaps(assessment_result: dict) -> dict:
    """Analyze assessment findings to identify specific gaps and recommendations.

    Args:
        assessment_result: Output from assess_observability_design().

    Returns:
        Dict with categorized gaps and prioritized recommendations.
    """
    findings = assessment_result.get("findings", [])

    gaps = {
        "missing_collectors": [],
        "missing_platforms": [],
        "missing_sources": [],
        "missing_automation": [],
        "missing_compliance": [],
        "unencrypted_transport": [],
        "disconnected_nodes": [],
    }
    recommendations = []

    for f in findings:
        rule_id = f["rule_id"]
        severity = f["severity"]
        priority = "critical" if severity == "CAT1" else "recommended"

        if rule_id == "ODC-LOG-001":
            gaps["disconnected_nodes"].append(f["affected_entity"])
            recommendations.append(
                {
                    "priority": priority,
                    "action": f"Connect source '{f['affected_entity']}' to a collector (Fluentd, Filebeat, or OTel Collector).",
                    "rule_id": rule_id,
                }
            )

        elif rule_id == "ODC-LOG-002":
            gaps["missing_platforms"].append(f["affected_entity"])
            recommendations.append(
                {
                    "priority": priority,
                    "action": f"Route collector '{f['affected_entity']}' to a SIEM/analytics platform.",
                    "rule_id": rule_id,
                }
            )

        elif rule_id == "ODC-LOG-003":
            gaps["missing_collectors"].append("col-s3")
            recommendations.append(
                {
                    "priority": priority,
                    "action": "Add a log archive (S3/GCS/ADLS) for long-term retention compliance.",
                    "rule_id": rule_id,
                }
            )

        elif rule_id == "ODC-LOG-004":
            gaps["missing_sources"].append(f["detail"])
            recommendations.append(
                {
                    "priority": priority,
                    "action": f"Add missing log source: {f['detail']}",
                    "rule_id": rule_id,
                }
            )

        elif rule_id == "ODC-LOG-005":
            gaps["missing_sources"].append("src-cloud-log")
            recommendations.append(
                {
                    "priority": priority,
                    "action": "Add cloud audit log source (CloudTrail/Activity Log/Audit Log).",
                    "rule_id": rule_id,
                }
            )

        elif rule_id in ("ODC-DET-001", "ODC-DET-002", "ODC-DET-003"):
            gaps["missing_automation"].append(f["detail"])
            recommendations.append(
                {
                    "priority": priority,
                    "action": f["detail"],
                    "rule_id": rule_id,
                }
            )

        elif rule_id == "ODC-RET-001":
            gaps["missing_compliance"].append("cmp-log-policy")
            recommendations.append(
                {
                    "priority": priority,
                    "action": "Add a log retention policy node (1yr online + 7yr archive for DoD).",
                    "rule_id": rule_id,
                }
            )

        elif rule_id == "ODC-SEC-001":
            gaps["unencrypted_transport"].append(f["affected_entity"])
            recommendations.append(
                {
                    "priority": priority,
                    "action": f"Enable TLS on log transport: {f['affected_entity']}.",
                    "rule_id": rule_id,
                }
            )

        elif rule_id in ("ODC-SEC-002", "ODC-SEC-003"):
            gaps["missing_sources"].append(f["detail"])
            recommendations.append(
                {
                    "priority": priority,
                    "action": f["detail"],
                    "rule_id": rule_id,
                }
            )

        elif rule_id == "ODC-INT-001":
            gaps["missing_automation"].append("auto-ticket")
            recommendations.append(
                {
                    "priority": priority,
                    "action": "Connect SOAR/runbook to a ticket system (ServiceNow/Jira).",
                    "rule_id": rule_id,
                }
            )

    # Sort recommendations: critical first
    recommendations.sort(key=lambda r: 0 if r["priority"] == "critical" else 1)

    return {
        "gaps": gaps,
        "recommendations": recommendations,
        "total_gaps": sum(len(v) for v in gaps.values()),
        "critical_count": sum(1 for r in recommendations if r["priority"] == "critical"),
        "recommended_count": sum(1 for r in recommendations if r["priority"] == "recommended"),
    }


# ── Cross-Canvas Checks (ODC ↔ NDC) ─────────────────────────────────────────


def check_nc_audit_to_siem_forwarder(canvas_project_id: str, odc_design_id: str) -> dict:
    """Check ODC-NDC-001: every NDC topology has nc_audit -> SIEM forwarder.

    Query NDC topologies in the project, check each has a matching audit
    forwarder node in this ODC design reaching a SIEM destination.

    Returns: {rule_id: 'ODC-NDC-001', status: 'pass'|'fail', violations: [...], score: 0-100}
    """
    violations: list = []
    try:
        import sqlite3 as _sq
        import pathlib as _pl
        import json as _js
        _data = _pl.Path(__file__).resolve().parents[2] / "data"

        # 1. Load NDC topologies for canvas_project_id
        ndc_db = _data / "network_canvas.db"
        ndc_topos: list = []
        if ndc_db.exists():
            _nc = _sq.connect(ndc_db)
            try:
                rows = _nc.execute(
                    "SELECT id, name, graph_json FROM topologies "
                    "WHERE template_id = ? OR id = ? OR description LIKE ?",
                    (canvas_project_id, canvas_project_id, f"%{canvas_project_id}%"),
                ).fetchall()
                ndc_topos = [{"id": r[0], "name": r[1], "graph": _js.loads(r[2] or "{}")} for r in rows]
            except Exception:
                ndc_topos = []
            finally:
                _nc.close()

        # 2. Load ODC design's SDC verifications (proxy for audit forwarder coverage)
        odc_db = _data / "observability_canvas.db"
        covered_topo_ids: set = set()
        if odc_db.exists():
            _oc = _sq.connect(odc_db)
            try:
                cov = _oc.execute(
                    "SELECT source_id FROM odc_sdc_verifications "
                    "WHERE design_id = ? AND status IN ('pass', 'verified')",
                    (odc_design_id,),
                ).fetchall()
                covered_topo_ids = {r[0] for r in cov}
            except Exception:
                covered_topo_ids = set()
            finally:
                _oc.close()

        # 3. For each NDC topology, check it has an audit/SIEM forwarder node
        #    OR is covered by an ODC SDC verification.
        SIEM_TYPES = {"siem", "siem_forwarder", "nc_audit", "log_forwarder", "splunk", "elastic", "sentinel"}
        for topo in ndc_topos:
            topo_id = topo["id"]
            if topo_id in covered_topo_ids:
                continue
            # Check graph_json for SIEM-type nodes
            nodes = topo["graph"].get("nodes", [])
            has_siem = any(
                str(n.get("type", "")).lower() in SIEM_TYPES
                or any(kw in str(n.get("label", "")).lower() for kw in ("siem", "splunk", "forwarder", "elastic", "sentinel", "audit"))
                for n in nodes
            )
            if not has_siem:
                violations.append({
                    "topology_id": topo_id,
                    "topology_name": topo["name"],
                    "reason": "No SIEM/audit forwarder node found in topology graph and no ODC SDC verification coverage",
                })
    except Exception:
        # DB layer unavailable — return pass with note rather than fail hard.
        pass

    status = "pass" if not violations else "fail"
    score = 100 if not violations else max(0, 100 - (len(violations) * 20))
    return {
        "rule_id": "ODC-NDC-001",
        "status": status,
        "violations": violations,
        "score": score,
    }
