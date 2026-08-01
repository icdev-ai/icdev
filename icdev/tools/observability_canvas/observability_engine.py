# CUI // SP-CTI
"""ICDEV Observability Design Canvas — Deterministic Assessment Engine.

Pure functions for observability stack assessment, coverage scoring,
gap detection, and MITRE ATT&CK detection coverage analysis.

No Flask dependency — takes graph data and returns results.
No LLM dependency — all checks are deterministic.
"""

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from tools.observability_canvas.constants import (
    OBSERVABILITY_COMPLIANCE_RULES,
    RECOMMENDED_SOURCE_TYPES,
    SIEM_PLATFORM_TYPES,
    SEVERITY_WEIGHTS,
)

from tools.logging.icdev_logger import get_logger
_LOGGER = get_logger("icdev.observability_canvas.observability_engine")

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


def _coerce_graph_data(graph_data):
    """Normalize graph input into (graph_dict, nodes, edges).

    Robustness (obx-fix-04): accepts a dict OR a JSON string. An invalid JSON
    string (or any non-dict) degrades to an empty design with a logged warning
    rather than raising ``AttributeError``. Malformed nodes (missing ``id``) and
    edges (missing ``source``/``target``) are dropped so downstream pure
    functions never ``KeyError``.

    Behavior for a valid dict input is byte-for-byte identical: every node and
    edge is well-formed, so the returned lists preserve original order/content.
    """
    if isinstance(graph_data, str):
        try:
            graph_data = json.loads(graph_data)
        except (ValueError, TypeError):
            _LOGGER.warning(
                "graph_data is not valid JSON; treating as empty design"
            )
            graph_data = {}
    if not isinstance(graph_data, dict):
        graph_data = {}
    raw_nodes = graph_data.get("nodes", []) or []
    raw_edges = graph_data.get("edges", []) or []
    nodes = [n for n in raw_nodes if isinstance(n, dict) and n.get("id") is not None]
    edges = [
        e
        for e in raw_edges
        if isinstance(e, dict) and e.get("source") is not None and e.get("target") is not None
    ]
    return graph_data, nodes, edges


def _node_types(nodes):
    """Return {node_id: node_type} dict.

    Nodes without an ``id`` are skipped rather than raising ``KeyError``.
    """
    result = {}
    for n in nodes:
        nid = n.get("id") if isinstance(n, dict) else None
        if nid is None:
            continue
        result[nid] = n.get("type", "")
    return result


def _label_map(nodes):
    """Return {node_id: label} dict. Nodes without an ``id`` are skipped."""
    result = {}
    for n in nodes:
        nid = n.get("id") if isinstance(n, dict) else None
        if nid is None:
            continue
        result[nid] = n.get("label", nid)
    return result


def _build_adjacency(edges):
    """Build undirected adjacency: {node_id: {neighbor_id, ...}}.

    Edges missing ``source`` or ``target`` are skipped rather than raising.
    """
    adj = {}
    for e in edges:
        if not isinstance(e, dict):
            continue
        src = e.get("source")
        tgt = e.get("target")
        if src is None or tgt is None:
            continue
        adj.setdefault(src, set()).add(tgt)
        adj.setdefault(tgt, set()).add(src)
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


def assess_observability_design(
    graph_data,
    rules: list = None,
    canvas_project_id: str = None,
    odc_design_id: str = None,
) -> dict:
    """Run all observability compliance rules against a design graph.

    Args:
        graph_data: Dict (or JSON string) with "nodes" and "edges" lists.
        rules: Optional list of rules (defaults to OBSERVABILITY_COMPLIANCE_RULES).
        canvas_project_id: Optional canvas-project id, threaded to cross-canvas
            ``check_function`` rules (e.g. ODC-NDC-001) that need it.
        odc_design_id: Optional ODC design id, threaded to cross-canvas checks.

    Returns:
        Dict with findings, score, grade, and per-category breakdown. Rules that
        declare a ``check_function`` (rather than a node-presence check) are
        dispatched generically and reported under ``cross_canvas_checks``.

    Scoring decision (obx-fix-04):
        The node-presence scoring path is unchanged. ``max_penalty`` is still the
        sum over *all* rules (a ``check_function`` rule already contributed a
        fixed weight to this denominator before this change, so scores for
        node-presence rules are byte-for-byte preserved). A ``check_function``
        result affects the score only when it returns status ``"fail"`` — its
        violations are added as findings and deducted like any other finding. A
        ``"pass"`` adds nothing; an ``"unknown"`` result (data unavailable) is
        surfaced under ``cross_canvas_checks`` but adds NO finding and NO penalty
        — so an unknown counts as neither pass nor fail in the score, and is
        never silently treated as a fabricated pass.
    """
    if rules is None:
        rules = OBSERVABILITY_COMPLIANCE_RULES

    graph_data, nodes, edges = _coerce_graph_data(graph_data)
    ntypes = _node_types(nodes)
    adj = _build_adjacency(edges)

    check_context = {
        "nodes": nodes,
        "edges": edges,
        "ntypes": ntypes,
        "adj": adj,
        "graph_data": graph_data,
        "canvas_project_id": canvas_project_id,
        "odc_design_id": odc_design_id,
    }

    findings = []
    by_category = {}
    cross_canvas_checks = []

    def _record_finding(rule, affected_entity, affected_type, detail):
        findings.append(
            {
                "id": str(uuid.uuid4())[:8],
                "rule_id": rule["id"],
                "title": rule["title"],
                "severity": rule["severity"],
                "category": rule["category"],
                "description": rule["description"],
                "affected_entity": affected_entity,
                "affected_type": affected_type,
                "detail": detail,
            }
        )
        cat = rule["category"]
        by_category.setdefault(cat, {"total": 0, "cat1": 0, "cat2": 0})
        by_category[cat]["total"] += 1
        if rule["severity"] == "CAT1":
            by_category[cat]["cat1"] += 1
        else:
            by_category[cat]["cat2"] += 1

    for rule in rules:
        rule_id = rule["id"]
        check_fn = _RULE_CHECKS.get(rule_id)
        if check_fn:
            # Node-presence path — unchanged (byte-for-byte identical behavior).
            rule_findings = check_fn(nodes, edges, ntypes, adj)
            for rf in rule_findings:
                _record_finding(
                    rule,
                    rf.get("affected_entity", ""),
                    rf.get("affected_type", "design"),
                    rf.get("detail", ""),
                )
        elif rule.get("check_function"):
            # Generic cross-canvas / check_function dispatch.
            disp = _dispatch_check_function(rule, check_context)
            cross_canvas_checks.append(
                {
                    "rule_id": rule_id,
                    "title": rule.get("title", rule_id),
                    "status": disp["status"],
                    "reason": disp.get("reason", ""),
                    "violation_count": len(disp.get("violations", [])),
                    "score": disp.get("score"),
                }
            )
            if disp["status"] == "fail":
                for v in disp.get("violations", []):
                    _record_finding(
                        rule,
                        v.get("topology_name") or v.get("topology_id") or "topology",
                        "topology",
                        v.get("reason", ""),
                    )
        # else: rule has neither a node check nor a check_function — skip.

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
        "cross_canvas_checks": cross_canvas_checks,
        "assessed_at": datetime.now(timezone.utc).isoformat(),
    }


def _dispatch_check_function(rule: dict, context: dict) -> dict:
    """Resolve and invoke a rule's named ``check_function`` generically.

    The rule's ``check_function`` string is resolved via ``getattr`` on THIS
    engine module. Missing/uncallable function or any raised exception ->
    ``status="unknown"`` (logged), never a fabricated pass. The resolved
    function is called with the subset of ``context`` matching its parameter
    names, so both node-graph checks and cross-canvas (project/design id)
    checks are supported.

    Returns a normalized dict: ``{rule_id, status, violations, reason, score}``
    where ``status`` is one of ``"pass" | "fail" | "unknown"``.
    """
    import inspect
    import sys

    fn_name = rule.get("check_function")
    fn = getattr(sys.modules[__name__], fn_name, None) if fn_name else None
    if fn is None or not callable(fn):
        _LOGGER.warning(
            "ODC assess: check_function '%s' for rule %s not resolvable — status=unknown",
            fn_name,
            rule.get("id"),
        )
        return {
            "rule_id": rule.get("id"),
            "status": "unknown",
            "violations": [],
            "reason": f"check_function '{fn_name}' not resolvable",
        }
    try:
        sig = inspect.signature(fn)
        kwargs = {p: context.get(p) for p in sig.parameters if p in context}
        result = fn(**kwargs)
    except Exception as exc:  # noqa: BLE001 — any check failure degrades to unknown
        _LOGGER.warning(
            "ODC assess: check_function '%s' raised %s — status=unknown",
            fn_name,
            exc,
        )
        return {
            "rule_id": rule.get("id"),
            "status": "unknown",
            "violations": [],
            "reason": f"error: {exc}",
        }
    if not isinstance(result, dict):
        return {
            "rule_id": rule.get("id"),
            "status": "unknown",
            "violations": [],
            "reason": "check_function returned a non-dict result",
        }
    return {
        "rule_id": rule.get("id"),
        "status": result.get("status", "unknown"),
        "violations": result.get("violations", []),
        "reason": result.get("reason", ""),
        "score": result.get("score"),
    }


def compute_coverage_score(graph_data) -> dict:
    """Compute percentage of recommended source types present vs. recommended.

    Args:
        graph_data: Dict with "nodes" list.

    Returns:
        Dict with coverage_pct, present, missing, total_recommended.
    """
    _, nodes, _ = _coerce_graph_data(graph_data)
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


def compute_mitre_detection_coverage(graph_data) -> dict:
    """Score MITRE ATT&CK detection coverage from baseline node metadata.

    If a detection baseline node (cmp-baseline) exists and has a 'techniques'
    field in its config_json, compute coverage. Otherwise return zero coverage.

    Args:
        graph_data: Dict with "nodes" list.

    Returns:
        Dict with coverage info: total_techniques, covered, pct, gaps.
    """
    _, nodes, _ = _coerce_graph_data(graph_data)
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


# SIEM / audit-forwarder node types and label keywords used to detect whether a
# topology already forwards its audit events to a SIEM within its own graph.
_SIEM_NODE_TYPES = {
    "siem",
    "siem_forwarder",
    "nc_audit",
    "log_forwarder",
    "splunk",
    "elastic",
    "sentinel",
    "plt-splunk",
    "plt-elastic",
    "plt-sentinel",
}
_SIEM_KEYWORDS = ("siem", "splunk", "forwarder", "elastic", "sentinel", "audit")


def _rget(row, key, idx):
    """Backend-agnostic row accessor.

    Works for sqlite3.Row (int + str indexing), psycopg2 RealDictRow (str only),
    and plain tuples (int only). Returns None if neither access path resolves.
    """
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        try:
            return row[idx]
        except (KeyError, IndexError, TypeError):
            return None


def _unknown_ndc_result(reason: str) -> dict:
    """Fail-closed ODC-NDC-001 result: 'unknown' with NO score (never fabricates a pass)."""
    _LOGGER.warning("ODC-NDC-001: unable to assess — %s (status=unknown)", reason)
    return {
        "rule_id": "ODC-NDC-001",
        "status": "unknown",
        "violations": [],
        "reason": reason,
    }


def _load_ndc_topologies(canvas_project_id: str):
    """Load NDC topologies linked to a canvas project.

    Returns a list of ``{"id", "name", "graph"}`` dicts. Raises on store
    unavailability (missing table / connection error) so the caller degrades to
    an ``unknown`` result rather than a fabricated pass. Patchable in tests.
    """
    from tools.db.storage import get_canvas_connection

    conn = get_canvas_connection()
    # ndc_topologies (migration 125) has no classification/tenant_id columns, so
    # the canvas connection (RLS disabled) is required. '?' placeholders are
    # translated per-backend by StorageConnection.
    rows = conn.execute(
        "SELECT id, name, design_json FROM ndc_topologies WHERE project_id = %s",
        (canvas_project_id,),
    ).fetchall()
    topologies = []
    for r in rows:
        raw = _rget(r, "design_json", 2) or "{}"
        try:
            graph = json.loads(raw)
        except (ValueError, TypeError):
            graph = {}
        topologies.append(
            {"id": _rget(r, "id", 0), "name": _rget(r, "name", 1), "graph": graph}
        )
    return topologies


def _load_forwarded_topology_ids(odc_design_id: str):
    """Return the set of topology ids marked audit-forwarded for an ODC design.

    Reads the additive ``topology_id``/``forward_status`` columns on
    ``odc_sdc_verifications`` (see db/init_db.py). Returns ``None`` on store
    unavailability (missing table/column, connection error) so the caller
    degrades to ``unknown``; a successful query with zero matching rows returns
    an empty set (a valid "nothing forwarded yet" state).
    """
    from tools.observability_canvas.db.init_db import get_connection

    conn = get_connection()
    rows = conn.execute(
        "SELECT topology_id, forward_status FROM odc_sdc_verifications "
        "WHERE design_id = %s AND forward_status IN ('forwarded', 'verified')",
        (odc_design_id,),
    ).fetchall()
    covered = set()
    for r in rows:
        tid = _rget(r, "topology_id", 0)
        if tid:
            covered.add(tid)
    return covered


def _topology_has_siem_forwarder(graph) -> bool:
    """True if the topology graph itself contains a SIEM/audit-forwarder node."""
    nodes = graph.get("nodes", []) if isinstance(graph, dict) else []
    for n in nodes:
        if not isinstance(n, dict):
            continue
        ntype = str(n.get("type", "")).lower()
        label = str(n.get("label", "")).lower()
        if (
            ntype in _SIEM_NODE_TYPES
            or any(kw in ntype for kw in _SIEM_KEYWORDS)
            or any(kw in label for kw in _SIEM_KEYWORDS)
        ):
            return True
    return False


def check_nc_audit_to_siem_forwarder(canvas_project_id: str, odc_design_id: str) -> dict:
    """Check ODC-NDC-001: every NDC topology has an nc_audit -> SIEM forwarder path.

    For each NDC topology in the canvas project, verify it either (a) has a
    matching audit-forwarding record in this ODC design's ``odc_sdc_verifications``
    (``forward_status`` forwarded/verified), or (b) already carries a
    SIEM/audit-forwarder node in its own topology graph. Uncovered topologies are
    violations.

    Fail-closed contract (obx-fix-04): when the underlying data cannot be read
    (missing identifiers, NDC/ODC store unavailable, missing table/column, no
    topologies to evaluate), this returns ``{"status": "unknown"}`` WITHOUT a
    score and logs the reason — it NEVER returns a fabricated pass.

    Returns:
        On assessable data: ``{rule_id, status: 'pass'|'fail', violations, score}``.
        Otherwise: ``{rule_id, status: 'unknown', violations: [], reason}`` (no score).
    """
    if not canvas_project_id or not odc_design_id:
        return _unknown_ndc_result("missing canvas_project_id or odc_design_id")

    # 1. Load the set of NDC topologies to evaluate.
    try:
        topologies = _load_ndc_topologies(canvas_project_id)
    except Exception as exc:  # noqa: BLE001 — store unavailable -> unknown
        return _unknown_ndc_result(f"NDC topology store unavailable: {exc}")
    if topologies is None:
        return _unknown_ndc_result("NDC topology store returned no result")
    if not topologies:
        return _unknown_ndc_result("no NDC topologies found for canvas project")

    # 2. Load audit-forwarding coverage recorded on the ODC design.
    try:
        covered_topo_ids = _load_forwarded_topology_ids(odc_design_id)
    except Exception as exc:  # noqa: BLE001 — store unavailable -> unknown
        return _unknown_ndc_result(f"ODC verification store unavailable: {exc}")
    if covered_topo_ids is None:
        return _unknown_ndc_result("ODC verification store returned no result")

    # 3. A topology is compliant if covered by an ODC verification OR it already
    #    forwards audit to a SIEM within its own graph.
    violations = []
    for topo in topologies:
        topo_id = topo.get("id")
        if topo_id in covered_topo_ids:
            continue
        if _topology_has_siem_forwarder(topo.get("graph", {})):
            continue
        violations.append(
            {
                "topology_id": topo_id,
                "topology_name": topo.get("name") or topo_id,
                "reason": (
                    "No audit->SIEM forwarder path: topology has no SIEM/audit "
                    "forwarder node and no ODC audit-forwarding verification."
                ),
            }
        )

    status = "pass" if not violations else "fail"
    score = 100 if not violations else max(0, 100 - (len(violations) * 20))
    return {
        "rule_id": "ODC-NDC-001",
        "status": status,
        "violations": violations,
        "score": score,
        "topologies_checked": len(topologies),
        "covered_count": len(topologies) - len(violations),
    }
