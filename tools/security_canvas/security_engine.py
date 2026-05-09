# [CUI // SP-CTI]
"""ICDEV™ Security Design Canvas — Security Analysis Engine.

Pure functions for STRIDE threat modeling, security assessment, risk scoring,
gap detection, and control-to-threat mapping against security design graphs.

No Flask dependency — takes graph data and returns results.
No LLM dependency — all checks are deterministic.
"""

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from tools.security_canvas.constants import (
    STRIDE_CATEGORIES,
    RISK_MATRIX,
    LIKELIHOOD_LEVELS,
    IMPACT_LEVELS,
    NIST_CONTROL_FAMILIES,
    SECURITY_ASSESSMENT_RULES,
    COMPLIANCE_CROSSWALK,
)


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


def _is_control(ntype):
    """Check if a node type is a security control."""
    return ntype.startswith("ctrl-")


def _is_asset(ntype):
    """Check if a node type is an asset."""
    return ntype.startswith("asset-")


def _is_boundary(ntype):
    """Check if a node type is a trust boundary."""
    return ntype.startswith("boundary-")


def _edge_crosses_boundary(edge, boundaries):
    """Check if an edge crosses a trust boundary (source and target in different boundaries)."""
    src = edge["source"]
    tgt = edge["target"]
    src_boundaries = set()
    tgt_boundaries = set()
    for b in boundaries:
        contained = b.get("contained_assets", [])
        if src in contained:
            src_boundaries.add(b["id"])
        if tgt in contained:
            tgt_boundaries.add(b["id"])
    # Crosses if they're in different boundary sets (or one is outside all boundaries)
    if not src_boundaries and not tgt_boundaries:
        return False  # both outside all boundaries
    return src_boundaries != tgt_boundaries


def _risk_score_from_levels(likelihood, impact):
    """Look up risk score from likelihood and impact level strings."""
    l_idx = LIKELIHOOD_LEVELS.index(likelihood) if likelihood in LIKELIHOOD_LEVELS else 2
    i_idx = IMPACT_LEVELS.index(impact) if impact in IMPACT_LEVELS else 2
    return RISK_MATRIX.get((l_idx, i_idx), 9)


# ── STRIDE Analysis ─────────────────────────────────────────────────────────


def run_stride_analysis(graph_data: dict) -> dict:
    """Analyze nodes and edges for STRIDE threats.

    For each data flow crossing a trust boundary, enumerates applicable STRIDE
    threats based on node types and flow properties.

    Args:
        graph_data: Dict with "nodes", "edges", and optionally "boundaries".

    Returns:
        Dict with threats list, total count, and by_category breakdown.
    """
    nodes = graph_data.get("nodes", [])
    edges = graph_data.get("edges", [])
    boundaries = graph_data.get("boundaries", [])
    ntypes = _node_types(nodes)
    labels = _label_map(nodes)

    threats = []
    by_category = {k: 0 for k in STRIDE_CATEGORIES}

    for edge in edges:
        src_type = ntypes.get(edge["source"], "")
        tgt_type = ntypes.get(edge["target"], "")
        src_label = labels.get(edge["source"], edge["source"])
        tgt_label = labels.get(edge["target"], edge["target"])
        is_encrypted = edge.get("encrypted", False)
        is_authenticated = edge.get("authenticated", False)
        crosses = _edge_crosses_boundary(edge, boundaries)

        for cat_key, cat_info in STRIDE_CATEGORIES.items():
            applicable = False
            desc = ""

            if cat_key == "S":
                # Spoofing: unauthenticated flows or flows involving user-facing assets
                if not is_authenticated:
                    applicable = True
                    desc = f"Unauthenticated flow from {src_label} to {tgt_label} — identity could be spoofed."
                elif src_type in cat_info["typical_targets"]:
                    applicable = True
                    desc = f"{src_label} ({src_type}) is a typical spoofing target."

            elif cat_key == "T":
                # Tampering: unencrypted flows or flows to data stores
                if not is_encrypted and crosses:
                    applicable = True
                    desc = f"Unencrypted boundary-crossing flow {src_label}→{tgt_label} is vulnerable to tampering."
                elif tgt_type in ("asset-database", "asset-storage"):
                    applicable = True
                    desc = f"Data flow to {tgt_label} ({tgt_type}) could allow tampering if input validation is weak."

            elif cat_key == "R":
                # Repudiation: any flow without logging
                if not is_authenticated:
                    applicable = True
                    desc = f"Flow from {src_label} to {tgt_label} has no authentication — actions are deniable."

            elif cat_key == "I":
                # Info disclosure: unencrypted boundary crossings
                if not is_encrypted and crosses:
                    applicable = True
                    desc = f"Unencrypted boundary-crossing flow {src_label}→{tgt_label} exposes data to eavesdropping."
                elif tgt_type in ("asset-database", "asset-storage") and not is_encrypted:
                    applicable = True
                    desc = f"Unencrypted flow to {tgt_label} may expose sensitive data."

            elif cat_key == "D":
                # DoS: any internet-facing or server asset
                if src_type == "boundary-internet" or tgt_type == "boundary-internet":
                    applicable = True
                    desc = f"Internet-facing flow {src_label}→{tgt_label} is exposed to DoS attacks."

            elif cat_key == "E":
                # EoP: flows to privileged services without PAM
                if tgt_type in ("asset-server", "asset-container", "ctrl-pam"):
                    protocol = (edge.get("protocol") or "").lower()
                    if protocol in ("ssh", "rdp", "admin", ""):
                        applicable = True
                        desc = f"Flow to {tgt_label} may allow privilege escalation without proper access controls."

            if applicable:
                threat_id = f"STRIDE-{cat_key}-{edge['source'][:6]}-{edge['target'][:6]}"
                threats.append(
                    {
                        "id": threat_id,
                        "category": cat_key,
                        "category_name": cat_info["name"],
                        "title": f"{cat_info['name']}: {src_label} → {tgt_label}",
                        "description": desc,
                        "affected": f"{src_label}, {tgt_label}",
                        "affected_assets": [edge["source"], edge["target"]],
                        "edge_id": edge.get("id", ""),
                        "crosses_boundary": crosses,
                        "nist_controls": cat_info["nist_controls"],
                        "likelihood": "high" if crosses else "medium",
                        "impact": "high" if tgt_type in ("asset-database", "asset-storage") else "medium",
                    }
                )
                by_category[cat_key] += 1

    return {
        "threats": threats,
        "total": len(threats),
        "by_category": by_category,
    }


# ── Security Assessment — check helpers ──────────────────────────────────────


def _sec_build_ctx(graph_data: dict) -> dict:
    nodes = graph_data.get("nodes", [])
    edges = graph_data.get("edges", [])
    ntypes = _node_types(nodes)
    return {
        "nodes": nodes, "edges": edges,
        "boundaries": graph_data.get("boundaries", []),
        "threats": graph_data.get("threats", []),
        "ntypes": ntypes, "labels": _label_map(nodes), "adj": _build_adjacency(edges),
        "asset_nodes": [n for n in nodes if _is_asset(ntypes.get(n["id"], ""))],
        "db_nodes": [n for n in nodes if ntypes.get(n["id"]) in ("asset-database", "asset-storage")],
        "siem_nodes": [n for n in nodes if ntypes.get(n["id"]) == "ctrl-siem"],
        "kms_nodes": [n for n in nodes if ntypes.get(n["id"]) == "ctrl-kms"],
        "idp_nodes": [n for n in nodes if ntypes.get(n["id"]) == "ctrl-idp"],
        "ids_nodes": [n for n in nodes if ntypes.get(n["id"]) == "ctrl-ids"],
        "scanner_nodes": [n for n in nodes if ntypes.get(n["id"]) == "ctrl-scanner"],
        "pam_nodes": [n for n in nodes if ntypes.get(n["id"]) == "ctrl-pam"],
    }


def _sec_make_finding(rule, affected="", affected_type="design") -> dict:
    return {"rule_id": rule["id"], "title": rule["title"], "severity": rule["severity"],
            "category": rule["category"], "affected_entity": affected, "affected_type": affected_type}


def _sec_edge_label(e, labels):
    return f"{labels.get(e['source'], '')}→{labels.get(e['target'], '')}"


def _sec_run_flow_checks(check, rule, ctx):
    nodes, edges, ntypes, labels, adj = ctx["nodes"], ctx["edges"], ctx["ntypes"], ctx["labels"], ctx["adj"]
    db_nodes, siem_nodes, kms_nodes = ctx["db_nodes"], ctx["siem_nodes"], ctx["kms_nodes"]
    asset_nodes, idp_nodes, pam_nodes = ctx["asset_nodes"], ctx["idp_nodes"], ctx["pam_nodes"]
    boundaries, threats = ctx["boundaries"], ctx["threats"]
    ids_nodes, scanner_nodes = ctx["ids_nodes"], ctx["scanner_nodes"]

    if check == "all_flows_authenticated":
        return [_sec_make_finding(rule, _sec_edge_label(e, labels), "edge") for e in edges
                if not e.get("authenticated") and ntypes.get(e["target"], "") != "ctrl-siem"]
    if check == "idp_for_user_assets":
        return [_sec_make_finding(rule)] if not idp_nodes and any(ntypes.get(n["id"]) == "asset-client" for n in nodes) else []
    if check == "pam_for_privileged":
        return [_sec_make_finding(rule)] if not pam_nodes and any((e.get("protocol") or "").lower() in ("ssh", "rdp", "admin") for e in edges) else []
    if check == "boundary_flows_encrypted":
        return [_sec_make_finding(rule, _sec_edge_label(e, labels), "edge") for e in edges
                if _edge_crosses_boundary(e, boundaries) and not e.get("encrypted", False)]
    if check == "kms_present":
        return [] if kms_nodes else [_sec_make_finding(rule)]
    if check == "db_encryption_at_rest":
        return [_sec_make_finding(rule, labels.get(db["id"], db["id"]), "node") for db in db_nodes
                if not kms_nodes and not any(ntypes.get(nb) == "ctrl-kms" for nb in adj.get(db["id"], set()))]
    if check in ("boundaries_defined", "has_boundaries"):
        return [] if boundaries else [_sec_make_finding(rule)]
    if check == "firewall_at_boundary":
        return [_sec_make_finding(rule, labels.get(n["id"], n["id"]), "node")
                for n in nodes if ntypes.get(n["id"]) == "boundary-internet"
                and not any(ntypes.get(nb) == "ctrl-firewall" for nb in adj.get(n["id"], set()))]
    if check == "no_direct_inet_db":
        inet_ids = {n["id"] for n in nodes if ntypes.get(n["id"]) == "boundary-internet"}
        db_ids = {n["id"] for n in db_nodes}
        return [_sec_make_finding(rule, _sec_edge_label(e, labels), "edge") for e in edges
                if (e["source"] in inet_ids and e["target"] in db_ids) or (e["target"] in inet_ids and e["source"] in db_ids)]
    if check == "siem_present":
        return [] if siem_nodes else [_sec_make_finding(rule)]
    if check == "all_assets_logged":
        if not siem_nodes:
            return []
        siem_ids = {n["id"] for n in siem_nodes}
        return [_sec_make_finding(rule, labels.get(a["id"], a["id"]), "node") for a in asset_nodes
                if not (adj.get(a["id"], set()) & siem_ids)]
    if check == "db_audit_logging":
        if not siem_nodes:
            return []
        siem_ids = {n["id"] for n in siem_nodes}
        return [_sec_make_finding(rule, labels.get(db["id"], db["id"]), "node") for db in db_nodes
                if not (adj.get(db["id"], set()) & siem_ids)]
    if check == "ids_present":
        return [] if ids_nodes else [_sec_make_finding(rule)]
    if check == "scanner_present":
        return [] if scanner_nodes else [_sec_make_finding(rule)]
    if check == "s2s_auth":
        return [_sec_make_finding(rule, _sec_edge_label(e, labels), "edge") for e in edges
                if _is_asset(ntypes.get(e["source"], "")) and _is_asset(ntypes.get(e["target"], ""))
                and not e.get("authenticated", False)]
    if check == "data_classification":
        results = []
        for db in db_nodes:
            cfg = db.get("config_json", "{}")
            if isinstance(cfg, str):
                try:
                    cfg = json.loads(cfg)
                except (json.JSONDecodeError, TypeError):
                    cfg = {}
            if not cfg.get("data_classification"):
                results.append(_sec_make_finding(rule, labels.get(db["id"], db["id"]), "node"))
        return results
    if check == "dlp_present":
        return [_sec_make_finding(rule)] if db_nodes and not any(ntypes.get(n["id"]) == "ctrl-dlp" for n in nodes) else []
    if check == "siem_alerting":
        return [_sec_make_finding(rule, labels.get(s["id"], s["id"]), "node")
                for s in siem_nodes if not adj.get(s["id"], set())]
    if check in ("ir_runbook", "no_shared_admin", "sbom_present"):
        return []
    if check == "registry_admission":
        return [_sec_make_finding(rule)] if (any(ntypes.get(n["id"]) == "asset-registry" for n in nodes)
                and not any(ntypes.get(n["id"]) == "ctrl-admission" for n in nodes)) else []
    if check == "assets_labeled":
        return [_sec_make_finding(rule, n["id"], "node") for n in nodes
                if not n.get("label") or n["label"] == n["id"]]
    if check == "threats_documented":
        return [] if threats else [_sec_make_finding(rule)]
    if check == "edr_present":
        return [_sec_make_finding(rule)] if asset_nodes and not any(ntypes.get(n["id"]) == "ctrl-edr" for n in nodes) else []
    if check == "cspm_present":
        has_cloud = any(_is_boundary(ntypes.get(n["id"], "")) and "cloud" in ntypes.get(n["id"], "") for n in nodes)
        return [_sec_make_finding(rule)] if has_cloud and not any(ntypes.get(n["id"]) == "ctrl-cspm" for n in nodes) else []
    if check == "backup_present":
        storage = [n for n in nodes if ntypes.get(n["id"]) in ("asset-database", "asset-storage")]
        if storage and not any(kw in (n.get("label", "") or "").lower() for n in nodes for kw in ("backup", " dr", "replica")):
            return [_sec_make_finding(rule)]
        return []
    if check == "secret_mgmt_present":
        return [_sec_make_finding(rule)] if asset_nodes and not kms_nodes else []
    if check == "mtls_s2s":
        return [_sec_make_finding(rule, _sec_edge_label(e, labels), "edge") for e in edges
                if _is_asset(ntypes.get(e["source"], "")) and _is_asset(ntypes.get(e["target"], ""))
                and not (e.get("encrypted") and e.get("authenticated"))]
    if check == "api_gateway_protected":
        inet_ids = {n["id"] for n in nodes if ntypes.get(n["id"]) == "boundary-internet"}
        return [_sec_make_finding(rule, labels.get(api["id"], api["id"]), "node")
                for api in nodes if ntypes.get(api["id"]) in ("asset-server", "asset-api")
                and (adj.get(api["id"], set()) & inet_ids)
                and not any(ntypes.get(nb) == "ctrl-firewall" for nb in adj.get(api["id"], set()))]
    if check == "zero_trust_posture":
        return [_sec_make_finding(rule, _sec_edge_label(e, labels), "edge") for e in edges
                if ntypes.get(e["target"], "") not in ("ctrl-siem", "ctrl-ids")
                and not (e.get("encrypted") and e.get("authenticated"))]
    if check == "admission_control_present":
        has_containers = any(ntypes.get(n["id"]) in ("asset-container", "asset-registry") for n in nodes)
        has_admission = any(
            ("admission" in (n.get("label", "") or "").lower() and ntypes.get(n["id"]) == "ctrl-scanner")
            or any(kw in (n.get("label", "") or "").lower() for kw in ("kyverno", "opa", "gatekeeper"))
            for n in nodes
        )
        return [_sec_make_finding(rule)] if has_containers and not has_admission else []
    if check == "fips_crypto_validated":
        has_crypto = bool(kms_nodes) or any(ntypes.get(n["id"]) == "ctrl-encryption" for n in nodes)
        return [_sec_make_finding(rule)] if not has_crypto and any(e.get("encrypted") for e in edges) else []
    if check == "hardening_baseline":
        results = []
        for n in nodes:
            if ntypes.get(n["id"]) == "asset-server":
                cfg = n.get("config_json", n.get("config", {}))
                if isinstance(cfg, str):
                    try:
                        cfg = json.loads(cfg)
                    except (json.JSONDecodeError, TypeError):
                        cfg = {}
                if not cfg.get("hardening_baseline"):
                    results.append(_sec_make_finding(rule, labels.get(n["id"], n["id"]), "node"))
        return results
    return None  # unknown check


def _sec_run_vdi_checks(check, rule, ctx):
    nodes, ntypes, labels, adj, boundaries = ctx["nodes"], ctx["ntypes"], ctx["labels"], ctx["adj"], ctx["boundaries"]
    vdi_hosts = [n for n in nodes if ntypes.get(n["id"]) == "asset-vdi-host"]
    if not vdi_hosts:
        return []
    if check == "vdi_session_policy":
        return [_sec_make_finding(rule, labels.get(h["id"], h["id"]), "node") for h in vdi_hosts
                if not any(ntypes.get(nb) == "ctrl-session-policy" for nb in adj.get(h["id"], set()))]
    if check == "vdi_gateway_required":
        inet_ids = {n["id"] for n in nodes if ntypes.get(n["id"]) == "boundary-internet"}
        gw_ids = {n["id"] for n in nodes if ntypes.get(n["id"]) == "ctrl-vdi-gateway"}
        findings = [_sec_make_finding(rule, labels.get(h["id"], h["id"]), "node") for h in vdi_hosts
                    if (adj.get(h["id"], set()) & inet_ids) and not (adj.get(h["id"], set()) & gw_ids)]
        if not gw_ids:
            findings.append(_sec_make_finding(rule))
        return findings
    if check == "vdi_boundary_isolation":
        vdi_bounds = [b for b in boundaries if b.get("type") == "boundary-vdi-session"]
        if not vdi_bounds:
            return [_sec_make_finding(rule)]
        contained = {cid for b in vdi_bounds for cid in b.get("contained_assets", [])}
        return [_sec_make_finding(rule, labels.get(h["id"], h["id"]), "node") for h in vdi_hosts if h["id"] not in contained]
    if check == "vdi_profile_encrypted":
        return [_sec_make_finding(rule, labels.get(ps["id"], ps["id"]), "node")
                for ps in nodes if ntypes.get(ps["id"]) == "asset-profile-store"
                and not any(ntypes.get(nb) in ("ctrl-kms", "ctrl-encryption") for nb in adj.get(ps["id"], set()))]
    if check == "vdi_endpoint_authenticated":
        return [_sec_make_finding(rule, labels.get(tc["id"], tc["id"]), "node")
                for tc in nodes if ntypes.get(tc["id"]) == "asset-thin-client"
                and not any(ntypes.get(nb) == "ctrl-idp" for nb in adj.get(tc["id"], set()))]
    if check == "vdi_image_integrity":
        hardening_ids = {n["id"] for n in nodes if ntypes.get(n["id"]) == "ctrl-image-hardening"}
        if not hardening_ids:
            return [_sec_make_finding(rule)]
        return [_sec_make_finding(rule, labels.get(h["id"], h["id"]), "node") for h in vdi_hosts
                if not (adj.get(h["id"], set()) & hardening_ids)]
    return []


def _sec_compute_scores(findings, rules):
    severity_weights = {"CAT1": 10, "CAT2": 5, "CAT3": 2}
    scores = {}
    for cat in {r["category"] for r in rules}:
        cat_rules = [r for r in rules if r["category"] == cat]
        cat_findings = [f for f in findings if f["category"] == cat]
        scores[cat] = {"total": len(cat_rules), "passed": len(cat_rules) - len(cat_findings), "failed": len(cat_findings)}
    penalty = sum(severity_weights.get(f["severity"], 2) for f in findings)
    return scores, max(0.0, min(100.0, 100.0 - penalty))


# ── Security Assessment ─────────────────────────────────────────────────────


def run_security_assessment(design_id: str, graph_data: dict, rules: list = None) -> dict:
    """Evaluate security assessment rules against a design."""
    if rules is None:
        rules = SECURITY_ASSESSMENT_RULES
    ctx = _sec_build_ctx(graph_data)
    findings = []
    for rule in rules:
        check = rule["check"]
        result = _sec_run_flow_checks(check, rule, ctx)
        if result is None:
            result = _sec_run_vdi_checks(check, rule, ctx)
        findings.extend(result or [])
    scores, risk_score = _sec_compute_scores(findings, rules)
    return {
        "design_id": design_id, "findings": findings,
        "total_rules": len(rules), "passed": len(rules) - len(findings), "failed": len(findings),
        "scores": scores, "risk_score": round(risk_score, 1),
        "posture_grade": compute_posture_grade(risk_score),
        "assessed_at": datetime.now(timezone.utc).isoformat(),
        "_rules": rules,
    }


# ── Risk Scoring ─────────────────────────────────────────────────────────────


def compute_risk_score(threats: list, controls: list) -> dict:
    """Compute risk scores for threats with/without mitigating controls.

    Args:
        threats: List of threat dicts with id, likelihood, impact, affected_assets.
        controls: List of control dicts with mitigates_threats (list of threat IDs).

    Returns:
        Dict with overall_risk, per_threat scores, mitigated, and unmitigated lists.
    """
    # Build control → threat mapping
    control_coverage = set()
    for ctrl in controls:
        mitigates = ctrl.get("mitigates_threats", [])
        if isinstance(mitigates, str):
            try:
                mitigates = json.loads(mitigates)
            except (json.JSONDecodeError, TypeError):
                mitigates = []
        for t_id in mitigates:
            control_coverage.add(t_id)

    per_threat = []
    mitigated = []
    unmitigated = []
    total_risk = 0.0

    for threat in threats:
        t_id = threat.get("id", "")
        likelihood = threat.get("likelihood", "medium")
        impact = threat.get("impact", "medium")
        raw_score = _risk_score_from_levels(likelihood, impact)
        is_mitigated = t_id in control_coverage

        # Mitigated threats have reduced risk (25% of raw)
        effective_score = raw_score * 0.25 if is_mitigated else float(raw_score)

        entry = {
            "threat_id": t_id,
            "title": threat.get("title", ""),
            "raw_risk_score": raw_score,
            "effective_risk_score": round(effective_score, 2),
            "mitigated": is_mitigated,
            "likelihood": likelihood,
            "impact": impact,
        }
        per_threat.append(entry)
        total_risk += effective_score

        if is_mitigated:
            mitigated.append(entry)
        else:
            unmitigated.append(entry)

    # Overall risk: average effective score normalized to 0-25 scale
    overall_risk = round(total_risk / len(threats), 2) if threats else 0.0

    return {
        "overall_risk": overall_risk,
        "per_threat": per_threat,
        "mitigated": mitigated,
        "unmitigated": unmitigated,
        "total_threats": len(threats),
        "mitigated_count": len(mitigated),
        "unmitigated_count": len(unmitigated),
    }


# ── Posture Grade ────────────────────────────────────────────────────────────


def compute_posture_grade(risk_score: float) -> str:
    """Convert a risk score (0-100) to a letter grade.

    A (>=90), B (>=75), C (>=60), D (>=40), F (<40).
    """
    if risk_score >= 90:
        return "A"
    elif risk_score >= 75:
        return "B"
    elif risk_score >= 60:
        return "C"
    elif risk_score >= 40:
        return "D"
    else:
        return "F"


# ── Gap Detection ────────────────────────────────────────────────────────────


def detect_security_gaps(graph_data: dict) -> list:
    """Find missing controls, unprotected assets, and unauthenticated flows.

    Args:
        graph_data: Dict with "nodes", "edges", and "boundaries".

    Returns:
        List of gap dicts with gap_type, severity, description, affected.
    """
    nodes = graph_data.get("nodes", [])
    edges = graph_data.get("edges", [])
    boundaries = graph_data.get("boundaries", [])
    ntypes = _node_types(nodes)
    labels = _label_map(nodes)
    adj = _build_adjacency(edges)

    type_set = set(ntypes.values())
    gaps = []

    # 1. Missing SIEM
    if "ctrl-siem" not in type_set:
        gaps.append(
            {
                "gap_type": "missing_control",
                "severity": "CAT2",
                "description": "No SIEM/SOC node in design — audit logging and monitoring will be incomplete (NIST AU-6, SI-4).",
                "affected": "design",
                "recommended_control": "ctrl-siem",
            }
        )

    # 2. Missing KMS
    if "ctrl-kms" not in type_set:
        has_encrypted = any(e.get("encrypted") for e in edges)
        if has_encrypted or any(ntypes.get(n["id"]) in ("asset-database", "asset-storage") for n in nodes):
            gaps.append(
                {
                    "gap_type": "missing_control",
                    "severity": "CAT1",
                    "description": "No KMS/HSM for key management — encrypted flows and data stores need centralized key management (NIST SC-12).",
                    "affected": "design",
                    "recommended_control": "ctrl-kms",
                }
            )

    # 3. Missing firewall
    if "ctrl-firewall" not in type_set:
        gaps.append(
            {
                "gap_type": "missing_control",
                "severity": "CAT1",
                "description": "No firewall/WAF — perimeter and internal network filtering required (NIST SC-7).",
                "affected": "design",
                "recommended_control": "ctrl-firewall",
            }
        )

    # 4. Missing IdP
    if "ctrl-idp" not in type_set:
        has_users = any(ntypes.get(n["id"]) == "asset-client" for n in nodes)
        if has_users:
            gaps.append(
                {
                    "gap_type": "missing_control",
                    "severity": "CAT1",
                    "description": "No IdP/MFA — user authentication requires centralized identity provider (NIST IA-2, IA-8).",
                    "affected": "design",
                    "recommended_control": "ctrl-idp",
                }
            )

    # 5. Unprotected assets (no control neighbor)
    for n in nodes:
        ntype = ntypes.get(n["id"], "")
        if not _is_asset(ntype):
            continue
        neighbors = adj.get(n["id"], set())
        has_control_neighbor = any(_is_control(ntypes.get(nb, "")) for nb in neighbors)
        if not has_control_neighbor:
            gaps.append(
                {
                    "gap_type": "unprotected_asset",
                    "severity": "CAT2",
                    "description": f"Asset '{labels.get(n['id'], n['id'])}' has no direct connection to any security control.",
                    "affected": n["id"],
                    "recommended_control": "ctrl-firewall",
                }
            )

    # 6. Unauthenticated flows
    for e in edges:
        if not e.get("authenticated", False):
            tgt_t = ntypes.get(e["target"], "")
            # Skip log/monitoring flows
            if tgt_t in ("ctrl-siem", "ctrl-ids"):
                continue
            src_t = ntypes.get(e["source"], "")
            if _is_asset(src_t) and _is_asset(tgt_t):
                gaps.append(
                    {
                        "gap_type": "unauthenticated_flow",
                        "severity": "CAT2",
                        "description": f"Unauthenticated service-to-service flow: {labels.get(e['source'], '')} → {labels.get(e['target'], '')} (NIST IA-3).",
                        "affected": e.get("id", ""),
                        "recommended_control": "ctrl-idp",
                    }
                )

    # 7. Unencrypted boundary crossings
    for e in edges:
        if _edge_crosses_boundary(e, boundaries) and not e.get("encrypted", False):
            gaps.append(
                {
                    "gap_type": "unencrypted_crossing",
                    "severity": "CAT1",
                    "description": f"Unencrypted flow crossing trust boundary: {labels.get(e['source'], '')} → {labels.get(e['target'], '')} (NIST SC-8).",
                    "affected": e.get("id", ""),
                    "recommended_control": "ctrl-encryption",
                }
            )

    # 8. No boundaries at all
    if not boundaries and len(nodes) > 3:
        gaps.append(
            {
                "gap_type": "no_segmentation",
                "severity": "CAT2",
                "description": "Design has no trust boundaries — network segmentation required for defense in depth (NIST SC-7).",
                "affected": "design",
                "recommended_control": "boundary-network",
            }
        )

    return gaps


# ── NIST 800-53 Control Coverage ────────────────────────────────────────────


def compute_nist_coverage(graph_data: dict) -> dict:
    """Compute NIST 800-53 control family coverage based on graph nodes/edges.

    Analyzes which control node types are present and maps them to the 20
    NIST 800-53 control families, returning per-family and overall coverage.

    Args:
        graph_data: Dict with "nodes", "edges", and "boundaries".

    Returns:
        Dict with families, overall_coverage_pct, total_families, covered_families.
    """
    nodes = graph_data.get("nodes", [])
    edges = graph_data.get("edges", [])
    boundaries = graph_data.get("boundaries", [])
    ntypes = _node_types(nodes)
    type_set = set(ntypes.values())

    has_boundaries = len(boundaries) > 0
    has_authenticated = any(e.get("authenticated", False) for e in edges)

    # Map control types to NIST families they cover
    # Each entry: (family_code, coverage_pct_contribution)
    _CTRL_FAMILY_MAP = {
        "ctrl-firewall": [("SC", 40), ("AC", 15)],
        "ctrl-idp": [("IA", 50), ("AC", 15)],
        "ctrl-siem": [("AU", 50), ("SI", 15), ("IR", 15)],
        "ctrl-kms": [("SC", 30)],
        "ctrl-ids": [("SI", 30), ("RA", 15)],
        "ctrl-pam": [("AC", 40)],
        "ctrl-scanner": [("RA", 40), ("CA", 15)],
        "ctrl-encryption": [("SC", 30)],
        "ctrl-dlp": [("MP", 40), ("SI", 15)],
        "ctrl-edr": [("SI", 30), ("IR", 15)],
        "ctrl-cspm": [("CA", 30), ("CM", 15)],
    }

    # Accumulate coverage per family
    family_coverage = {}  # family_code -> {"pct": int, "controls": [str]}
    for fam_code in NIST_CONTROL_FAMILIES:
        family_coverage[fam_code] = {"pct": 0, "controls": []}

    for ctrl_type, mappings in _CTRL_FAMILY_MAP.items():
        if ctrl_type in type_set:
            for fam_code, pct in mappings:
                if fam_code in family_coverage:
                    family_coverage[fam_code]["pct"] += pct
                    family_coverage[fam_code]["controls"].append(ctrl_type)

    # Bonus: boundaries partially cover SC-7
    if has_boundaries:
        family_coverage["SC"]["pct"] += 10
        if "boundary" not in family_coverage["SC"]["controls"]:
            family_coverage["SC"]["controls"].append("boundary")

    # Bonus: authenticated edges partially cover IA-3
    if has_authenticated:
        family_coverage["IA"]["pct"] += 10
        if "authenticated-edge" not in family_coverage["IA"]["controls"]:
            family_coverage["IA"]["controls"].append("authenticated-edge")

    # Cap each family at 100%
    for fam_code in family_coverage:
        if family_coverage[fam_code]["pct"] > 100:
            family_coverage[fam_code]["pct"] = 100

    # Build output
    families = {}
    for fam_code, fam_info in NIST_CONTROL_FAMILIES.items():
        pct = family_coverage[fam_code]["pct"]
        ctrls = family_coverage[fam_code]["controls"]
        if pct >= 70:
            status = "full"
        elif pct > 0:
            status = "partial"
        else:
            status = "none"
        families[fam_code] = {
            "name": fam_info["name"],
            "coverage_pct": pct,
            "controls_present": ctrls,
            "status": status,
        }

    total_families = len(NIST_CONTROL_FAMILIES)
    covered_families = sum(1 for f in families.values() if f["coverage_pct"] > 0)
    overall_coverage_pct = (
        round(sum(f["coverage_pct"] for f in families.values()) / total_families) if total_families else 0
    )

    return {
        "families": families,
        "overall_coverage_pct": overall_coverage_pct,
        "total_families": total_families,
        "covered_families": covered_families,
    }


# ── Attack Path Finder ──────────────────────────────────────────────────────


def find_attack_paths(graph_data: dict) -> dict:
    """Find and score attack paths from entry points to high-value targets.

    Uses BFS to enumerate simple paths through the graph, then scores each
    path based on encryption, authentication, control traversal, and
    boundary crossings.

    Args:
        graph_data: Dict with "nodes", "edges", and "boundaries".

    Returns:
        Dict with attack_paths list, total_paths, critical_paths.
    """
    nodes = graph_data.get("nodes", [])
    edges = graph_data.get("edges", [])
    boundaries = graph_data.get("boundaries", [])
    ntypes = _node_types(nodes)
    labels = _label_map(nodes)

    # Build directed adjacency with edge properties
    dir_adj = {}  # {src: [(tgt, edge_props), ...]}
    for e in edges:
        src, tgt = e["source"], e["target"]
        props = {
            "encrypted": bool(e.get("encrypted", False)),
            "authenticated": bool(e.get("authenticated", False)),
        }
        dir_adj.setdefault(src, []).append((tgt, props))
        # Also add reverse direction (undirected graph)
        dir_adj.setdefault(tgt, []).append((src, props))

    # Identify entry points: connected to boundary-internet or asset-client
    inet_ids = {n["id"] for n in nodes if ntypes.get(n["id"]) == "boundary-internet"}
    entry_points = set()
    # Nodes connected to internet boundary
    for e in edges:
        if e["source"] in inet_ids:
            entry_points.add(e["target"])
        if e["target"] in inet_ids:
            entry_points.add(e["source"])
    # asset-client nodes are also entry points
    for n in nodes:
        if ntypes.get(n["id"]) == "asset-client":
            entry_points.add(n["id"])

    # Identify high-value targets
    targets = {n["id"] for n in nodes if ntypes.get(n["id"]) in ("asset-database", "asset-storage")}

    MAX_DEPTH = 8
    attack_paths = []

    # BFS to find all simple paths from each entry to each target
    for entry_id in entry_points:
        for target_id in targets:
            if entry_id == target_id:
                continue
            # BFS with path tracking
            queue = [(entry_id, [entry_id])]
            while queue:
                current, path = queue.pop(0)
                if current == target_id:
                    # Score this path
                    risk_score = _score_attack_path(path, dir_adj, ntypes, boundaries, edges)
                    risk_level = (
                        "critical"
                        if risk_score >= 80
                        else "high"
                        if risk_score >= 60
                        else "medium"
                        if risk_score >= 40
                        else "low"
                    )
                    mitigations = _suggest_mitigations(path, ntypes)
                    path_id = f"AP-{entry_id[:6]}-{target_id[:6]}-{len(attack_paths)}"
                    attack_paths.append(
                        {
                            "id": path_id,
                            "entry": {
                                "id": entry_id,
                                "label": labels.get(entry_id, entry_id),
                                "type": ntypes.get(entry_id, ""),
                            },
                            "target": {
                                "id": target_id,
                                "label": labels.get(target_id, target_id),
                                "type": ntypes.get(target_id, ""),
                            },
                            "hops": [
                                {
                                    "node_id": nid,
                                    "node_label": labels.get(nid, nid),
                                    "node_type": ntypes.get(nid, ""),
                                }
                                for nid in path
                            ],
                            "risk_score": risk_score,
                            "risk_level": risk_level,
                            "mitigations": mitigations,
                        }
                    )
                    continue
                if len(path) >= MAX_DEPTH:
                    continue
                for neighbor, _props in dir_adj.get(current, []):
                    if neighbor not in path:
                        queue.append((neighbor, path + [neighbor]))

    critical_paths = sum(1 for p in attack_paths if p["risk_level"] == "critical")

    return {
        "attack_paths": attack_paths,
        "total_paths": len(attack_paths),
        "critical_paths": critical_paths,
    }


def _score_attack_path(path, dir_adj, ntypes, boundaries, edges):
    """Score an attack path. Higher = more dangerous."""
    score = 100

    # Build quick edge lookup: (src, tgt) -> edge props
    edge_lookup = {}
    for e in edges:
        edge_lookup[(e["source"], e["target"])] = e
        edge_lookup[(e["target"], e["source"])] = e

    for i in range(len(path) - 1):
        hop_src = path[i]
        hop_tgt = path[i + 1]
        edge = edge_lookup.get((hop_src, hop_tgt), {})
        is_encrypted = bool(edge.get("encrypted", False))
        is_authenticated = bool(edge.get("authenticated", False))

        if is_encrypted:
            score -= 10
        if is_authenticated:
            score -= 15

        # Control node traversed
        if _is_control(ntypes.get(hop_tgt, "")):
            score -= 20

        # Boundary crossing without encryption
        if _edge_crosses_boundary(edge, boundaries) and not is_encrypted:
            score += 15

    return max(0, min(100, score))


def _suggest_mitigations(path, ntypes):
    """Suggest mitigations for an attack path."""
    mitigations = []
    has_encryption = False
    has_auth = False
    has_firewall = False
    has_ids = False

    for nid in path:
        ntype = ntypes.get(nid, "")
        if ntype == "ctrl-encryption":
            has_encryption = True
        if ntype == "ctrl-idp":
            has_auth = True
        if ntype == "ctrl-firewall":
            has_firewall = True
        if ntype == "ctrl-ids":
            has_ids = True

    if not has_firewall:
        mitigations.append("Add firewall/WAF between entry and internal assets")
    if not has_auth:
        mitigations.append("Add authentication (IdP/MFA) on path")
    if not has_encryption:
        mitigations.append("Enable encryption on data flows")
    if not has_ids:
        mitigations.append("Add IDS/IPS for intrusion detection")

    return mitigations


# ── FedRAMP Authorization Boundary Auto-Drawing ──────────────────────────────


def generate_fedramp_boundary(graph_data: dict, impact_level: str = "moderate") -> dict:
    """Generate FedRAMP authorization boundary and missing controls.

    Analyzes existing nodes to determine what boundaries and controls are
    missing for a FedRAMP authorization boundary at the given impact level,
    then generates boundary rectangles and control nodes.

    Args:
        graph_data: Dict with "nodes", "edges", and "boundaries".
        impact_level: One of "low", "moderate", "high".

    Returns:
        Dict with impact_level, boundaries_added, controls_added,
        edges_added, total_additions, fedramp_ready, missing_for_ready.
    """
    impact_level = impact_level.lower()
    if impact_level not in ("low", "moderate", "high"):
        impact_level = "moderate"

    nodes = graph_data.get("nodes", [])
    edges = graph_data.get("edges", [])
    boundaries = graph_data.get("boundaries", [])
    ntypes = _node_types(nodes)
    type_set = set(ntypes.values())
    boundary_types = {b.get("type", "") for b in boundaries}

    # ── Compute bounding box of all existing nodes ──
    all_x = [n.get("x", 0) for n in nodes]
    all_y = [n.get("y", 0) for n in nodes]
    if all_x and all_y:
        min_x = min(all_x) - 50
        min_y = min(all_y) - 50
        max_x = max(all_x) + 50
        max_y = max(all_y) + 50
    else:
        min_x, min_y, max_x, max_y = 0, 0, 600, 400

    bbox_w = max(max_x - min_x, 400)
    bbox_h = max(max_y - min_y, 300)

    # ── Required boundaries and controls per impact level ──
    _REQUIRED_BOUNDARIES = {
        "low": [
            ("boundary-authorization", "Authorization Boundary"),
            ("boundary-internet", "Internet Boundary"),
        ],
        "moderate": [
            ("boundary-authorization", "Authorization Boundary"),
            ("boundary-internet", "Internet Boundary"),
            ("boundary-dmz", "DMZ"),
        ],
        "high": [
            ("boundary-authorization", "Authorization Boundary"),
            ("boundary-internet", "Internet Boundary"),
            ("boundary-dmz", "DMZ"),
        ],
    }

    _REQUIRED_CONTROLS = {
        "low": [],
        "moderate": [
            ("ctrl-firewall", "Firewall / WAF"),
            ("ctrl-idp", "Identity Provider (MFA)"),
            ("ctrl-siem", "SIEM"),
            ("ctrl-kms", "Key Management Service"),
            ("ctrl-ids", "IDS/IPS"),
            ("ctrl-scanner", "Vulnerability Scanner"),
        ],
        "high": [
            ("ctrl-firewall", "Firewall / WAF"),
            ("ctrl-idp", "Identity Provider (MFA)"),
            ("ctrl-siem", "SIEM"),
            ("ctrl-kms", "Key Management Service"),
            ("ctrl-ids", "IDS/IPS"),
            ("ctrl-scanner", "Vulnerability Scanner"),
            ("ctrl-pam", "Privileged Access Management"),
            ("ctrl-dlp", "Data Loss Prevention"),
            ("ctrl-edr", "Endpoint Detection & Response"),
            ("ctrl-encryption", "Encryption Gateway"),
        ],
    }

    boundaries_added = []
    controls_added = []
    edges_added = []

    # ── Generate missing boundaries ──
    for btype, blabel in _REQUIRED_BOUNDARIES[impact_level]:
        if btype in boundary_types:
            continue
        bid = f"fedramp-{btype}-{str(uuid.uuid4())[:8]}"
        if btype == "boundary-authorization":
            bx, by, bw, bh = min_x - 20, min_y - 20, bbox_w + 40, bbox_h + 40
        elif btype == "boundary-internet":
            bx, by, bw, bh = min_x - 60, min_y - 60, bbox_w + 120, 80
        elif btype == "boundary-dmz":
            bx, by, bw, bh = min_x - 10, min_y - 10, bbox_w + 20, 100
        else:
            bx, by, bw, bh = min_x, min_y, bbox_w, bbox_h
        boundary_obj = {
            "id": bid,
            "type": btype,
            "label": blabel,
            "x": bx,
            "y": by,
            "width": bw,
            "height": bh,
            "config": {"fedramp_generated": True, "impact_level": impact_level},
        }
        boundaries_added.append(boundary_obj)

    # ── Generate missing control nodes along bottom edge ──
    ctrl_x_start = min_x
    ctrl_y = max_y + 30  # Below existing nodes
    ctrl_spacing = 120
    ctrl_index = 0

    # Collect all asset node IDs for edge generation
    asset_ids = [n["id"] for n in nodes if _is_asset(ntypes.get(n["id"], ""))]

    for ctype, clabel in _REQUIRED_CONTROLS[impact_level]:
        if ctype in type_set:
            continue
        cid = f"fedramp-{ctype}-{str(uuid.uuid4())[:8]}"
        ctrl_node = {
            "id": cid,
            "type": ctype,
            "label": clabel,
            "x": ctrl_x_start + (ctrl_index * ctrl_spacing),
            "y": ctrl_y,
            "config": {"fedramp_generated": True, "impact_level": impact_level},
        }
        controls_added.append(ctrl_node)

        # Generate edges from this control to assets it protects
        for aid in asset_ids:
            edge_id = f"fedramp-edge-{str(uuid.uuid4())[:8]}"
            edges_added.append(
                {
                    "id": edge_id,
                    "source": cid,
                    "target": aid,
                    "label": "protects",
                    "config": {"fedramp_generated": True},
                }
            )

        ctrl_index += 1

    # ── Determine FedRAMP readiness ──
    missing_for_ready = []

    # Check boundaries after additions
    final_boundary_types = boundary_types | {b["type"] for b in boundaries_added}
    for btype, blabel in _REQUIRED_BOUNDARIES[impact_level]:
        if btype not in final_boundary_types:
            missing_for_ready.append(f"Missing boundary: {blabel}")

    # Check controls after additions
    final_type_set = type_set | {c["type"] for c in controls_added}
    for ctype, clabel in _REQUIRED_CONTROLS[impact_level]:
        if ctype not in final_type_set:
            missing_for_ready.append(f"Missing control: {clabel}")

    # Additional readiness checks
    if not asset_ids:
        missing_for_ready.append("No assets defined in design")
    if not edges and not edges_added:
        missing_for_ready.append("No data flows defined")
    if impact_level == "high" and "ctrl-encryption" not in final_type_set:
        missing_for_ready.append("High impact requires encryption at all boundaries")

    fedramp_ready = len(missing_for_ready) == 0

    total_additions = len(boundaries_added) + len(controls_added) + len(edges_added)

    return {
        "impact_level": impact_level,
        "boundaries_added": boundaries_added,
        "controls_added": controls_added,
        "edges_added": edges_added,
        "total_additions": total_additions,
        "fedramp_ready": fedramp_ready,
        "missing_for_ready": missing_for_ready,
    }


# ── MITRE ATT&CK Coverage ──────────────────────────────────────────────────


def compute_mitre_coverage(graph_data: dict) -> dict:
    """Compute MITRE ATT&CK technique coverage based on controls in the graph.

    For each MITRE tactic and technique, checks whether the design graph
    contains any of the controls that can detect that technique. Returns
    per-tactic coverage percentages and a list of critical undetected
    techniques.

    Args:
        graph_data: Security design graph with nodes, edges, boundaries.

    Returns:
        Dict with tactics breakdown, overall coverage, and critical gaps.
    """
    from tools.security_canvas.constants import MITRE_ATTACK_TECHNIQUES

    nodes = graph_data.get("nodes", [])
    # Collect all control types present in the graph
    present_controls = set()
    for n in nodes:
        ntype = n.get("type", "")
        if ntype.startswith("ctrl-"):
            present_controls.add(ntype)

    tactics = {}
    total_techniques = 0
    total_detected = 0
    critical_undetected = []

    for tactic_key, tactic_data in MITRE_ATTACK_TECHNIQUES.items():
        techniques_out = []
        detected_count = 0

        for tech in tactic_data["techniques"]:
            detectable_by = tech.get("detectable_by", [])
            matched_controls = [c for c in detectable_by if c in present_controls]
            detected = len(matched_controls) > 0
            if detected:
                detected_count += 1
            elif tech.get("severity") == "critical":
                critical_undetected.append(tech["name"])

            techniques_out.append(
                {
                    "id": tech["id"],
                    "name": tech["name"],
                    "detected": detected,
                    "detected_by": matched_controls,
                    "severity": tech.get("severity", "medium"),
                }
            )

        tech_total = len(tactic_data["techniques"])
        coverage_pct = round(detected_count * 100 / tech_total) if tech_total else 0
        total_techniques += tech_total
        total_detected += detected_count

        tactics[tactic_key] = {
            "tactic_id": tactic_data["tactic_id"],
            "name": tactic_data["name"],
            "total_techniques": tech_total,
            "detected_techniques": detected_count,
            "coverage_pct": coverage_pct,
            "techniques": techniques_out,
        }

    overall_pct = round(total_detected * 100 / total_techniques) if total_techniques else 0

    return {
        "tactics": tactics,
        "overall_coverage_pct": overall_pct,
        "total_techniques": total_techniques,
        "detected_techniques": total_detected,
        "critical_undetected": critical_undetected,
    }


# ── Compliance Crosswalk (NIST / FedRAMP / CMMC) ──────────────────────────────


def compute_compliance_crosswalk(graph_data: dict) -> dict:
    """Compute multi-framework compliance crosswalk for a security design.

    Maps NIST 800-53 controls to FedRAMP and CMMC L2 equivalents, computes
    per-framework coverage based on which NIST control families are present.

    Args:
        graph_data: Dict with "nodes", "edges", and "boundaries".

    Returns:
        Dict with frameworks, crosswalk_matrix, and overall_summary.
    """
    # Get NIST family coverage from existing engine
    nist_result = compute_nist_coverage(graph_data)
    nist_families = nist_result.get("families", {})

    # Determine which NIST families have coverage > 0
    covered_families = {fam_code for fam_code, fam_data in nist_families.items() if fam_data.get("coverage_pct", 0) > 0}

    # Build per-framework results
    nist_controls = []
    fedramp_controls = []
    cmmc_controls = []
    crosswalk_matrix = []

    for nist_id, mapping in COMPLIANCE_CROSSWALK.items():
        # Extract family prefix (e.g., "AC-2" -> "AC")
        family = nist_id.split("-")[0]
        is_covered = family in covered_families

        description = mapping["description"]

        # NIST
        nist_controls.append(
            {
                "id": nist_id,
                "description": description,
                "covered": is_covered,
            }
        )

        # FedRAMP
        fedramp_id = mapping.get("fedramp")
        if fedramp_id:
            fedramp_controls.append(
                {
                    "id": fedramp_id,
                    "nist_source": nist_id,
                    "description": description,
                    "covered": is_covered,
                }
            )

        # CMMC
        cmmc_id = mapping.get("cmmc")
        if cmmc_id:
            cmmc_controls.append(
                {
                    "id": cmmc_id,
                    "nist_source": nist_id,
                    "description": description,
                    "covered": is_covered,
                }
            )

        # Crosswalk row
        crosswalk_matrix.append(
            {
                "nist": nist_id,
                "fedramp": fedramp_id or "N/A",
                "cmmc": cmmc_id or "N/A",
                "description": description,
                "covered": is_covered,
            }
        )

    # Compute per-framework stats
    def _fw_stats(controls):
        total = len(controls)
        covered = sum(1 for c in controls if c["covered"])
        pct = round(covered * 100 / total) if total else 0
        return {"total_controls": total, "covered": covered, "coverage_pct": pct, "controls": controls}

    frameworks = {
        "nist_800_53": _fw_stats(nist_controls),
        "fedramp": _fw_stats(fedramp_controls),
        "cmmc": _fw_stats(cmmc_controls),
    }

    # Overall summary
    total_all = sum(f["total_controls"] for f in frameworks.values())
    covered_all = sum(f["covered"] for f in frameworks.values())
    overall_pct = round(covered_all * 100 / total_all) if total_all else 0

    if overall_pct >= 70:
        summary = f"Strong compliance posture ({overall_pct}% overall). Most frameworks well-covered."
    elif overall_pct >= 40:
        summary = f"Moderate compliance posture ({overall_pct}% overall). Gaps remain in some frameworks."
    else:
        summary = f"Weak compliance posture ({overall_pct}% overall). Significant gaps across frameworks."

    return {
        "frameworks": frameworks,
        "crosswalk_matrix": crosswalk_matrix,
        "overall_summary": summary,
    }


# ── Control-to-Threat Mapping ────────────────────────────────────────────────


def map_controls_to_threats(threats: list) -> list:
    """For each identified threat, suggest NIST 800-53 controls.

    Uses the STRIDE_CATEGORIES crosswalk to map threat categories to
    recommended NIST control families and specific controls.

    Args:
        threats: List of threat dicts with category (STRIDE letter).

    Returns:
        List of recommendation dicts with threat_id, controls, rationale.
    """
    recommendations = []

    for threat in threats:
        cat = threat.get("category", "")
        cat_info = STRIDE_CATEGORIES.get(cat, {})
        nist_controls = cat_info.get("nist_controls", [])

        # Expand controls with family context
        enriched_controls = []
        for ctrl_id in nist_controls:
            family = ctrl_id.split("-")[0]
            family_info = NIST_CONTROL_FAMILIES.get(family, {})
            enriched_controls.append(
                {
                    "control_id": ctrl_id,
                    "family": family,
                    "family_name": family_info.get("name", family),
                }
            )

        recommendations.append(
            {
                "threat_id": threat.get("id", ""),
                "threat_title": threat.get("title", ""),
                "threat_category": cat,
                "threat_category_name": cat_info.get("name", cat),
                "recommended_controls": enriched_controls,
                "rationale": f"{cat_info.get('name', cat)} threats are mitigated by "
                f"{', '.join(nist_controls)} controls.",
            }
        )

    return recommendations


# ── Auto-Fix Generator ──────────────────────────────────────────────────

# Human-readable labels for control/boundary node types
_AUTOFIX_LABELS = {
    "ctrl-siem": "SIEM/SOC",
    "ctrl-kms": "KMS/HSM",
    "ctrl-firewall": "Firewall/WAF",
    "ctrl-idp": "IdP/MFA",
    "ctrl-ids": "IDS/IPS",
    "ctrl-encryption": "Encryptor",
    "boundary-network": "Network Zone",
}


def generate_auto_fix(design_id: str, graph_data: dict) -> dict:
    """Analyze security gaps and generate missing control nodes + edges.

    Runs gap detection and for each gap with a recommended_control, creates
    new canvas nodes and edges to remediate the gap automatically.

    Args:
        design_id: UUID of the security design.
        graph_data: Dict with "nodes", "edges", and "boundaries".

    Returns:
        Dict with nodes_added, edges_added, edges_modified, total_fixes.
    """
    gaps = detect_security_gaps(graph_data)
    nodes = graph_data.get("nodes", [])
    edges = graph_data.get("edges", [])
    boundaries = graph_data.get("boundaries", [])
    ntypes = _node_types(nodes)

    # Find max x among existing nodes for placement offset
    max_x = 0
    for n in nodes:
        nx = n.get("x", 0)
        if nx > max_x:
            max_x = nx
    for b in boundaries:
        bx = b.get("x", 0) + b.get("width", 300)
        if bx > max_x:
            max_x = bx
    base_x = max_x + 150

    nodes_added = []
    edges_added = []
    edges_modified = []

    # Track which control types we've already created to avoid duplicates
    created_controls = {}  # recommended_control -> node_id
    y_offset = 50

    # Collect asset node IDs for "design"-level gaps
    asset_ids = [n["id"] for n in nodes if _is_asset(ntypes.get(n["id"], ""))]

    for gap in gaps:
        rec = gap.get("recommended_control")
        if not rec:
            continue

        gap_type = gap.get("gap_type", "")

        # Handle unencrypted_crossing by modifying existing edges
        if gap_type == "unencrypted_crossing":
            affected_edge_id = gap.get("affected", "")
            if affected_edge_id:
                # Find the matching edge by id
                for e in edges:
                    if e.get("id", "") == affected_edge_id:
                        edges_modified.append(
                            {
                                "source": e["source"],
                                "target": e["target"],
                                "encrypted": 1,
                                "authenticated": 1,
                            }
                        )
                        break
            continue

        # Create or reuse a control node for this recommended_control
        if rec not in created_controls:
            node_id = "autofix-" + str(uuid.uuid4())[:8]
            label = _AUTOFIX_LABELS.get(rec, rec)
            new_node = {
                "id": node_id,
                "type": rec,
                "label": label,
                "x": base_x,
                "y": y_offset,
            }
            nodes_added.append(new_node)
            created_controls[rec] = node_id
            y_offset += 80

        ctrl_node_id = created_controls[rec]

        # Generate edges based on gap type
        if gap_type == "unprotected_asset":
            # Connect control to the specific unprotected asset
            affected_asset = gap.get("affected", "")
            if affected_asset and affected_asset != "design":
                edges_added.append(
                    {
                        "source": ctrl_node_id,
                        "target": affected_asset,
                        "encrypted": 0,
                        "authenticated": 0,
                    }
                )

        elif gap_type == "missing_control":
            # Connect control to ALL asset nodes
            for aid in asset_ids:
                edges_added.append(
                    {
                        "source": ctrl_node_id,
                        "target": aid,
                        "encrypted": 0,
                        "authenticated": 0,
                    }
                )

        elif gap_type == "unauthenticated_flow":
            # Connect control between the flow's source and target
            affected_edge_id = gap.get("affected", "")
            if affected_edge_id:
                for e in edges:
                    if e.get("id", "") == affected_edge_id:
                        edges_added.append(
                            {
                                "source": ctrl_node_id,
                                "target": e["source"],
                                "encrypted": 0,
                                "authenticated": 1,
                            }
                        )
                        edges_added.append(
                            {
                                "source": ctrl_node_id,
                                "target": e["target"],
                                "encrypted": 0,
                                "authenticated": 1,
                            }
                        )
                        break

        elif gap_type == "no_segmentation":
            # Boundary node — no edges needed, it's a zone container
            pass

    total_fixes = len(nodes_added) + len(edges_added) + len(edges_modified)

    return {
        "design_id": design_id,
        "nodes_added": nodes_added,
        "edges_added": edges_added,
        "edges_modified": edges_modified,
        "total_fixes": total_fixes,
    }


# ── Multi-Design Comparison ────────────────────────────────────────────────


def compare_designs(graph_a: dict, graph_b: dict, name_a: str = "Design A", name_b: str = "Design B") -> dict:
    """Compare two security design graphs and return a comprehensive diff.

    Computes set differences for nodes, edges, and boundaries, then runs
    security assessment and NIST coverage on both to surface posture deltas.

    Args:
        graph_a: First design graph dict (nodes, edges, boundaries).
        graph_b: Second design graph dict (nodes, edges, boundaries).
        name_a: Human-readable label for design A.
        name_b: Human-readable label for design B.

    Returns:
        Dict with node/edge/boundary diffs, risk deltas, finding diffs,
        NIST coverage deltas, and a summary string.
    """

    # ── Extract node sets (type+label as identity key) ────────────────────
    def _node_key(n):
        return (n.get("type", ""), n.get("label", n.get("id", "")))

    nodes_a = graph_a.get("nodes", [])
    nodes_b = graph_b.get("nodes", [])
    keys_a = {_node_key(n) for n in nodes_a}
    keys_b = {_node_key(n) for n in nodes_b}

    nodes_only_in_a = [{"type": k[0], "label": k[1]} for k in keys_a - keys_b]
    nodes_only_in_b = [{"type": k[0], "label": k[1]} for k in keys_b - keys_a]
    nodes_in_both = [{"type": k[0], "label": k[1]} for k in keys_a & keys_b]

    # ── Extract edge sets (source_label→target_label) ─────────────────────
    labels_a = _label_map(nodes_a)
    labels_b = _label_map(nodes_b)

    def _edge_key(e, lmap):
        src = lmap.get(e.get("source", ""), e.get("source", ""))
        tgt = lmap.get(e.get("target", ""), e.get("target", ""))
        return (src, tgt)

    edges_a = graph_a.get("edges", [])
    edges_b = graph_b.get("edges", [])
    ekeys_a = {_edge_key(e, labels_a) for e in edges_a}
    ekeys_b = {_edge_key(e, labels_b) for e in edges_b}

    edges_only_in_a = [{"source": k[0], "target": k[1]} for k in ekeys_a - ekeys_b]
    edges_only_in_b = [{"source": k[0], "target": k[1]} for k in ekeys_b - ekeys_a]
    edges_in_both = [{"source": k[0], "target": k[1]} for k in ekeys_a & ekeys_b]

    # ── Extract boundary sets ─────────────────────────────────────────────
    def _boundary_key(b):
        return (b.get("type", ""), b.get("label", b.get("id", "")))

    bounds_a = graph_a.get("boundaries", [])
    bounds_b = graph_b.get("boundaries", [])
    bkeys_a = {_boundary_key(b) for b in bounds_a}
    bkeys_b = {_boundary_key(b) for b in bounds_b}

    boundaries_only_in_a = [{"type": k[0], "label": k[1]} for k in bkeys_a - bkeys_b]
    boundaries_only_in_b = [{"type": k[0], "label": k[1]} for k in bkeys_b - bkeys_a]
    boundaries_in_both = [{"type": k[0], "label": k[1]} for k in bkeys_a & bkeys_b]

    # ── Security assessment comparison ────────────────────────────────────
    assess_a = run_security_assessment("compare-a", graph_a)
    assess_b = run_security_assessment("compare-b", graph_b)

    risk_score_a = assess_a.get("risk_score", 0.0)
    risk_score_b = assess_b.get("risk_score", 0.0)
    risk_delta = round(risk_score_b - risk_score_a, 1)
    grade_a = assess_a.get("posture_grade", "F")
    grade_b = assess_b.get("posture_grade", "F")

    findings_a_ids = {f["rule_id"] for f in assess_a.get("findings", [])}
    findings_b_ids = {f["rule_id"] for f in assess_b.get("findings", [])}
    findings_only_in_a = sorted(findings_a_ids - findings_b_ids)
    findings_only_in_b = sorted(findings_b_ids - findings_a_ids)

    # ── NIST coverage comparison ──────────────────────────────────────────
    nist_a = compute_nist_coverage(graph_a)
    nist_b = compute_nist_coverage(graph_b)

    nist_deltas = {}
    fam_a = nist_a.get("families", {})
    fam_b = nist_b.get("families", {})
    all_fam_codes = set(fam_a.keys()) | set(fam_b.keys())
    for fam in sorted(all_fam_codes):
        pct_a = fam_a.get(fam, {}).get("coverage_pct", 0)
        pct_b = fam_b.get(fam, {}).get("coverage_pct", 0)
        if pct_a != pct_b:
            nist_deltas[fam] = {
                "name": fam_a.get(fam, fam_b.get(fam, {})).get("name", fam),
                "coverage_a": pct_a,
                "coverage_b": pct_b,
                "delta": pct_b - pct_a,
            }

    # ── Build summary ─────────────────────────────────────────────────────
    parts = []
    if risk_delta > 0:
        parts.append(f"{name_b} scores {risk_delta} points higher than {name_a}")
    elif risk_delta < 0:
        parts.append(f"{name_a} scores {abs(risk_delta)} points higher than {name_b}")
    else:
        parts.append(f"Both designs have equal risk scores ({risk_score_a})")

    n_diff = len(nodes_only_in_a) + len(nodes_only_in_b)
    if n_diff:
        parts.append(f"{n_diff} node differences")
    e_diff = len(edges_only_in_a) + len(edges_only_in_b)
    if e_diff:
        parts.append(f"{e_diff} edge differences")
    f_diff = len(findings_only_in_a) + len(findings_only_in_b)
    if f_diff:
        parts.append(f"{f_diff} finding differences")

    summary = ". ".join(parts) + "."

    return {
        "name_a": name_a,
        "name_b": name_b,
        # Nodes
        "nodes_only_in_a": nodes_only_in_a,
        "nodes_only_in_b": nodes_only_in_b,
        "nodes_in_both": nodes_in_both,
        # Edges
        "edges_only_in_a": edges_only_in_a,
        "edges_only_in_b": edges_only_in_b,
        "edges_in_both": edges_in_both,
        # Boundaries
        "boundaries_only_in_a": boundaries_only_in_a,
        "boundaries_only_in_b": boundaries_only_in_b,
        "boundaries_in_both": boundaries_in_both,
        # Risk
        "risk_score_a": risk_score_a,
        "risk_score_b": risk_score_b,
        "risk_delta": risk_delta,
        "grade_a": grade_a,
        "grade_b": grade_b,
        # Findings
        "findings_only_in_a": findings_only_in_a,
        "findings_only_in_b": findings_only_in_b,
        # NIST coverage
        "nist_coverage_a": nist_a.get("overall_coverage_pct", 0),
        "nist_coverage_b": nist_b.get("overall_coverage_pct", 0),
        "nist_deltas": nist_deltas,
        # Summary
        "summary": summary,
    }


def diff_graph_versions(graph_old: dict, graph_new: dict) -> dict:
    """Lightweight diff between two graph versions.

    Compares node/edge/boundary counts and identifies additions/removals
    by id.  Much faster than :func:`compare_designs` — suitable for
    automatic version snapshots on every save.

    Args:
        graph_old: Previous graph dict (nodes, edges, boundaries).
        graph_new: Updated graph dict (nodes, edges, boundaries).

    Returns:
        Dict with added/removed counts, added/removed node labels,
        and a human-readable summary string.
    """
    old_nodes = {n.get("id"): n for n in graph_old.get("nodes", [])}
    new_nodes = {n.get("id"): n for n in graph_new.get("nodes", [])}
    old_node_ids = set(old_nodes.keys())
    new_node_ids = set(new_nodes.keys())

    added_node_ids = new_node_ids - old_node_ids
    removed_node_ids = old_node_ids - new_node_ids
    added_node_labels = [new_nodes[nid].get("label", nid) for nid in added_node_ids]
    removed_node_labels = [old_nodes[nid].get("label", nid) for nid in removed_node_ids]

    def _edge_key(e):
        return (e.get("source", ""), e.get("target", ""))

    old_edge_keys = {_edge_key(e) for e in graph_old.get("edges", [])}
    new_edge_keys = {_edge_key(e) for e in graph_new.get("edges", [])}
    edges_added = len(new_edge_keys - old_edge_keys)
    edges_removed = len(old_edge_keys - new_edge_keys)

    old_bounds = {b.get("id", b.get("label", "")) for b in graph_old.get("boundaries", [])}
    new_bounds = {b.get("id", b.get("label", "")) for b in graph_new.get("boundaries", [])}
    boundaries_added = len(new_bounds - old_bounds)
    boundaries_removed = len(old_bounds - new_bounds)

    # Build summary
    parts = []
    if added_node_labels:
        parts.append(f"+{len(added_node_labels)} node(s)")
    if removed_node_labels:
        parts.append(f"-{len(removed_node_labels)} node(s)")
    if edges_added:
        parts.append(f"+{edges_added} edge(s)")
    if edges_removed:
        parts.append(f"-{edges_removed} edge(s)")
    if boundaries_added:
        parts.append(f"+{boundaries_added} boundary(ies)")
    if boundaries_removed:
        parts.append(f"-{boundaries_removed} boundary(ies)")
    summary = ", ".join(parts) if parts else "No structural changes"

    return {
        "nodes_added": len(added_node_ids),
        "nodes_removed": len(removed_node_ids),
        "edges_added": edges_added,
        "edges_removed": edges_removed,
        "boundaries_added": boundaries_added,
        "boundaries_removed": boundaries_removed,
        "added_node_labels": sorted(added_node_labels),
        "removed_node_labels": sorted(removed_node_labels),
        "summary": summary,
    }


# ── CLI ──────────────────────────────────────────────────────────────────────


def main():
    """CLI entry point for security analysis."""
    import argparse

    parser = argparse.ArgumentParser(description="Security Design Canvas — Analysis Engine")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--design-file", type=str, help="Path to design JSON file")
    parser.add_argument(
        "--analysis",
        choices=["stride", "assessment", "risk", "gaps", "controls"],
        default="assessment",
        help="Analysis type to run",
    )
    args = parser.parse_args()

    # Load design data
    if args.design_file:
        design_path = Path(args.design_file)
        with open(design_path, "r", encoding="utf-8") as f:
            graph_data = json.load(f)
    else:
        # Demo with empty graph
        graph_data = {"nodes": [], "edges": [], "boundaries": [], "threats": []}

    if args.analysis == "stride":
        result = run_stride_analysis(graph_data)
    elif args.analysis == "assessment":
        result = run_security_assessment("demo-design", graph_data)
    elif args.analysis == "risk":
        threats = graph_data.get("threats", [])
        controls = graph_data.get("controls", [])
        result = compute_risk_score(threats, controls)
    elif args.analysis == "gaps":
        result = detect_security_gaps(graph_data)
    elif args.analysis == "controls":
        threats = graph_data.get("threats", [])
        result = map_controls_to_threats(threats)
    else:
        result = {"error": f"Unknown analysis type: {args.analysis}"}

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        if isinstance(result, dict):
            for k, v in result.items():
                if isinstance(v, list):
                    print(f"\n{k} ({len(v)} items):")
                    for item in v[:10]:
                        print(f"  - {item.get('title', item.get('description', str(item)[:80]))}")
                else:
                    print(f"{k}: {v}")
        elif isinstance(result, list):
            print(f"\nResults ({len(result)} items):")
            for item in result:
                print(f"  - {item.get('title', item.get('description', str(item)[:80]))}")


if __name__ == "__main__":
    main()
