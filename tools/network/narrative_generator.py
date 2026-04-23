# CUI // SP-CTI
"""Traffic Flow Walkthrough — Narrative Generator.

Wraps TrafficFlowEngine walkthrough steps with per-persona LLM narratives and
deterministic detail_json enrichment:

  1. CSP detection + cross-cloud context injection into system prompt
  2. Multi-CSP inter-hop detection with dual-CSP context
  3. Classification overlay injected into every system prompt
  4. Compliance Officer (compofficer) NIST 800-53 / FedRAMP control pre-population
  5. App Developer (appdev) endpoint / token-endpoint inference from node config
  6. generate_all() summary: hop count, CSPs traversed, encryption chain, risk, latency

Public API
----------
  generate_for_persona(step, node, persona_id, flow, classification,
                       prev_node=None, llm_client=None) -> dict
      Returns {"narrative": str, "detail_json": dict}

  generate_all(flow_id, conn, personas=None, classification="NIPR",
               use_llm=True) -> dict
      Returns {"steps": [...], "summary": {...}}

Usage
-----
  python tools/network/narrative_generator.py --flow-id <id> --json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

# ── YAML config loader ────────────────────────────────────────────────────────

_PERSONAS_CACHE: dict | None = None


def _load_personas_config() -> dict:
    global _PERSONAS_CACHE
    if _PERSONAS_CACHE is not None:
        return _PERSONAS_CACHE
    try:
        import yaml
        cfg_path = BASE_DIR / "args" / "tfw_personas.yaml"
        with open(cfg_path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        _PERSONAS_CACHE = data or {}
    except Exception:
        _PERSONAS_CACHE = {}
    return _PERSONAS_CACHE


def _personas_by_id() -> dict[str, dict]:
    cfg = _load_personas_config()
    return {p["id"]: p for p in cfg.get("personas", [])}


def _cross_cloud_contexts() -> dict[str, dict]:
    return _load_personas_config().get("cross_cloud_contexts", {})


def _classification_levels() -> dict[str, dict]:
    return _load_personas_config().get("classification_levels", {})


# ── CSP detection ─────────────────────────────────────────────────────────────

_CSP_LABEL_HINTS: list[tuple[str, str]] = [
    ("aws", "aws_govcloud"),
    ("azure", "azure_gov"),
    ("gcp", "gcp_gov"),
    ("google", "gcp_gov"),
    ("oci", "oci_gov"),
]

_CSP_TYPE_PREFIXES: list[tuple[str, str]] = [
    ("aws-", "aws_govcloud"),
    ("azure-", "azure_gov"),
    ("az-", "azure_gov"),
    ("gcp-", "gcp_gov"),
    ("google-", "gcp_gov"),
    ("oci-", "oci_gov"),
]


def detect_csp(node: dict) -> str | None:
    """Return the cross_cloud_contexts key for a node, or None if not CSP."""
    ntype = (node.get("type") or "").lower()
    label = (node.get("label") or "").lower()

    for prefix, key in _CSP_TYPE_PREFIXES:
        if ntype.startswith(prefix):
            return key

    for hint, key in _CSP_LABEL_HINTS:
        if hint in ntype or hint in label:
            return key

    return None


def _get_csp_ctx(node: dict) -> dict | None:
    key = detect_csp(node)
    if not key:
        return None
    return _cross_cloud_contexts().get(key)


def _get_classification_ctx(classification: str) -> dict:
    levels = _classification_levels()
    cls_upper = (classification or "NIPR").upper()
    # exact match first, then case-insensitive scan
    ctx = levels.get(cls_upper)
    if not ctx:
        for k, v in levels.items():
            if k.upper() == cls_upper:
                ctx = v
                break
    return ctx or {"label": cls_upper, "encryption": "FIPS 140-2 (TLS 1.2+, AES-256)",
                   "key_mgmt": "DoD PKI", "mfa": "CAC/PIV", "audit_retention": "3 years"}


# ── NIST / FedRAMP mapping for compofficer ────────────────────────────────────

_ACTION_NIST_MAP: dict[str, dict[str, list[str]]] = {
    "authenticate":     {"nist_controls": ["IA-2", "IA-2(1)", "IA-2(2)", "IA-5", "IA-8"],
                         "fedramp_controls": ["IA-2(1)", "IA-2(2)"]},
    "security_inspect": {"nist_controls": ["SC-7", "SC-8", "SC-8(1)", "SI-3", "SI-4"],
                         "fedramp_controls": ["SC-8(1)"]},
    "encrypt_vpn":      {"nist_controls": ["SC-8", "SC-8(1)", "SC-28", "SC-12", "SC-13"],
                         "fedramp_controls": ["SC-8(1)", "SC-28"]},
    "tls_inspect":      {"nist_controls": ["SC-8(1)", "SC-7(8)", "SI-4"],
                         "fedramp_controls": ["SC-8(1)"]},
    "route_lookup":     {"nist_controls": ["SC-7", "AC-17", "SC-20", "SC-21"],
                         "fedramp_controls": ["SC-7"]},
    "authorize":        {"nist_controls": ["AC-3", "AC-4", "AC-17", "AC-20"],
                         "fedramp_controls": ["AC-3", "AC-4"]},
}

# Map raw action_type values (from DOMAIN_ACTION_MAP) to NIST mapping keys
_ACTION_NORMALIZE: dict[str, str] = {
    "authenticate":      "authenticate",
    "mfa-verify":        "authenticate",
    "mfa_verify":        "authenticate",
    "pki-validate":      "authenticate",
    "pki_validate":      "authenticate",
    "authorize":         "authorize",
    "inspect":           "security_inspect",
    "idps-scan":         "security_inspect",
    "cdm-check":         "security_inspect",
    "waf-filter":        "security_inspect",
    "dns-inspect":       "security_inspect",
    "rpz-filter":        "security_inspect",
    "security_inspect":  "security_inspect",
    "tls-inspect":       "tls_inspect",
    "tls_inspect":       "tls_inspect",
    "encrypt":           "encrypt_vpn",
    "encrypt_vpn":       "encrypt_vpn",
    "route":             "route_lookup",
    "route-advertisement": "route_lookup",
    "route-reflect":     "route_lookup",
    "route-import":      "route_lookup",
    "route-filter":      "route_lookup",
    "route_lookup":      "route_lookup",
}


def _nist_for_action(action_type: str) -> dict[str, list[str]]:
    norm = _ACTION_NORMALIZE.get(action_type, _ACTION_NORMALIZE.get(
        action_type.replace("-", "_"), ""))
    return dict(_ACTION_NIST_MAP.get(norm, {}))


# ── Per-hop latency estimates (ms) ───────────────────────────────────────────

_DOMAIN_LATENCY_MS: dict[str, int] = {
    "on_prem":   2,
    "nipr":      5,
    "bcap_vdms": 8,
    "bcap_vdss": 12,
    "csp_il4":   15,
    "csp_il5":   18,
}


# ── System prompt builders ────────────────────────────────────────────────────

def _csp_prompt_block(csp: dict, label: str = "") -> str:
    prefix = f"{label}: " if label else ""
    return (
        f"{prefix}CSP: {csp['name']} ({csp['regions'][0]}). "
        f"IL levels: {', '.join(csp['il_levels'])}. "
        f"Network constructs: {', '.join(csp['network_constructs'])}. "
        f"Identity: {csp['identity']}. "
        f"BCAP connectivity: {csp['connectivity_to_bcap']}."
    )


def _build_system_prompt(
    persona: dict,
    csp_ctx: dict | None,
    prev_csp_ctx: dict | None,
    cls_ctx: dict,
) -> str:
    base = persona.get("system_prompt", "").strip()

    # Classification overlay — always injected
    cls_block = (
        f"\n\nClassification: {cls_ctx.get('label', 'NIPRNet (Unclassified / CUI)')}. "
        f"Required encryption: {cls_ctx.get('encryption', 'FIPS 140-2, TLS 1.2+, AES-256')}. "
        f"Key management: {cls_ctx.get('key_mgmt', 'DoD PKI')}. "
        f"MFA requirement: {cls_ctx.get('mfa', 'CAC/PIV')}. "
        f"Audit retention: {cls_ctx.get('audit_retention', '3 years')}."
    )

    # CSP context — injected when hop is on a CSP node
    csp_block = ""
    if csp_ctx and prev_csp_ctx and csp_ctx.get("name") != prev_csp_ctx.get("name"):
        # Inter-CSP transit
        csp_block = (
            f"\n\nThis hop is an inter-CSP transit. "
            f"Origin: {prev_csp_ctx['name']}. Destination: {csp_ctx['name']}. "
            "Connectivity options: ExpressRoute Global Reach + Direct Connect via DISA DISN BCAP, "
            "or direct MPLS peering arranged through DISA."
        )
    elif csp_ctx:
        csp_block = f"\n\nCSP at this hop: {_csp_prompt_block(csp_ctx)}"

    return base + cls_block + csp_block


# ── Compofficer detail enrichment ─────────────────────────────────────────────

def _build_compofficer_detail(step: dict) -> dict:
    action_type = step.get("action_type", "")
    nist = _nist_for_action(action_type)
    return {
        "nist_controls":   nist.get("nist_controls", []),
        "fedramp_controls": nist.get("fedramp_controls", []),
        "action_type":     action_type,
    }


# ── Appdev detail enrichment ──────────────────────────────────────────────────

def _build_appdev_detail(
    step: dict,
    node: dict,
    flow: dict,
    csp_key: str | None,
) -> dict:
    action_type = step.get("action_type", "")
    app_type = flow.get("app_type", "")
    config: dict = node.get("config") or {}
    detail: dict[str, Any] = {}

    # Endpoint URL from node config
    for field in ("endpoint", "url", "fqdn"):
        if field in config and config[field]:
            detail["endpoint_url"] = config[field]
            break

    # DNS name inference for CSP IL4 nodes
    if "endpoint_url" not in detail:
        domain_type = step.get("security_detail", {}).get("domain_type", "")
        if domain_type in ("csp_il4", "csp_il5"):
            if csp_key == "azure_gov":
                detail["dns_name"] = "*.privatelink.database.windows.net"
            elif csp_key == "aws_govcloud":
                detail["dns_name"] = "*.execute-api.us-gov-east-1.amazonaws.com"
            elif csp_key == "gcp_gov":
                detail["dns_name"] = "*.p.googleapis.com"
            elif csp_key == "oci_gov":
                detail["dns_name"] = "*.oraclecloud.com"

    # Token endpoint for SSO SAML authentication hops
    if action_type in ("authenticate", "mfa-verify", "mfa_verify") and app_type == "sso_saml":
        if csp_key == "azure_gov":
            detail["token_endpoint"] = "https://login.microsoftonline.us/<tenant>/saml2"
        elif csp_key == "aws_govcloud":
            detail["token_endpoint"] = "https://sts.amazonaws.com/saml"
        elif csp_key == "gcp_gov":
            detail["token_endpoint"] = "https://accounts.google.com/o/saml2"
        elif csp_key == "oci_gov":
            detail["token_endpoint"] = "https://idcs-<tenant>.identity.oraclecloud.com/fed/v1/idp/saml"

    return detail


# ── LLM invocation ────────────────────────────────────────────────────────────

def _invoke_llm(system_prompt: str, user_content: str) -> str | None:
    try:
        from tools.llm.router import LLMRouter
        from tools.llm.provider import LLMRequest

        router = LLMRouter()
        if not router.has_any_llm():
            return None

        req = LLMRequest(
            messages=[{"role": "user", "content": user_content}],
            system_prompt=system_prompt,
            max_tokens=1024,
            temperature=0.3,
        )
        resp = router.invoke("tfw_narrative", req)
        if resp and resp.content:
            return resp.content.strip()
    except Exception:
        pass
    return None


# ── Core: generate for one persona at one step ────────────────────────────────

def generate_for_persona(
    step: dict,
    node: dict,
    persona_id: str,
    flow: dict,
    classification: str = "NIPR",
    prev_node: dict | None = None,
    llm_client: Any = None,
) -> dict:
    """Generate narrative + detail_json for one walkthrough step + persona.

    Parameters
    ----------
    step        : walkthrough step dict from TrafficFlowEngine.get_walkthrough()
    node        : full topology node dict (with type, label, config)
    persona_id  : one of the persona ids defined in tfw_personas.yaml
    flow        : traffic flow dict (app_type, classification, etc.)
    classification : classification level key (NIPR, IL4, IL5, IL6, SIPR)
    prev_node   : previous hop's node dict (for inter-CSP detection)
    llm_client  : ignored; LLMRouter is loaded internally when available

    Returns
    -------
    dict with keys:
        narrative   : str — LLM-generated prose or deterministic fallback
        detail_json : dict — merged deterministic + persona-specific fields
    """
    personas = _personas_by_id()
    persona = personas.get(persona_id, {})
    if not persona:
        return {"narrative": "", "detail_json": {}}

    cls_ctx = _get_classification_ctx(classification)
    csp_ctx = _get_csp_ctx(node)
    prev_csp_ctx = _get_csp_ctx(prev_node) if prev_node else None
    csp_key = detect_csp(node)

    system_prompt = _build_system_prompt(persona, csp_ctx, prev_csp_ctx, cls_ctx)

    # ── Deterministic detail_json base ────────────────────────────────────────
    detail_json: dict[str, Any] = {
        "persona":          persona_id,
        "action_type":      step.get("action_type", ""),
        "node_label":       step.get("node_label", ""),
        "classification":   cls_ctx.get("label", classification),
        "encryption":       cls_ctx.get("encryption", ""),
    }
    if csp_ctx:
        detail_json["csp"] = csp_ctx.get("name", "")
        detail_json["csp_region"] = csp_ctx.get("regions", [""])[0]
        detail_json["il_levels"] = csp_ctx.get("il_levels", [])

    # ── Persona-specific enrichment ───────────────────────────────────────────
    if persona_id == "compofficer":
        detail_json.update(_build_compofficer_detail(step))

    elif persona_id == "appdev":
        appdev_extra = _build_appdev_detail(step, node, flow, csp_key)
        detail_json.update(appdev_extra)

    elif persona_id == "neteng":
        domain_type = step.get("security_detail", {}).get("domain_type", "")
        detail_json["expected_latency_ms"] = _DOMAIN_LATENCY_MS.get(domain_type, 5)

    # ── LLM narrative generation ──────────────────────────────────────────────
    action_type = step.get("action_type", "forward")
    node_label = step.get("node_label", step.get("node_id", ""))
    app_type = flow.get("app_type", "")
    csp_name = csp_ctx.get("name", "") if csp_ctx else ""
    inter_csp = (
        prev_csp_ctx is not None
        and csp_ctx is not None
        and csp_ctx.get("name") != prev_csp_ctx.get("name")
    )

    user_content = (
        f"Hop: {node_label} | Action: {action_type} | App: {app_type}"
        + (f" | CSP: {csp_name}" if csp_name else "")
        + (f" | Inter-CSP transit from {prev_csp_ctx['name']}" if inter_csp else "")
        + f" | Classification: {classification}"
        + f"\n\nSecurity detail: {json.dumps(step.get('security_detail', {}))}"
        + f"\nNetwork detail: {json.dumps(step.get('network_detail', {}))}"
    )

    narrative = _invoke_llm(system_prompt, user_content)

    if not narrative:
        # Deterministic fallback narrative
        csp_suffix = f" via {csp_name}" if csp_name else ""
        narrative = (
            f"[{persona.get('short', persona_id)}] "
            f"Hop {step.get('step_number', '?')}: {node_label}{csp_suffix}. "
            f"Action: {action_type}. "
            f"Classification: {cls_ctx.get('label', classification)}. "
            f"Encryption: {cls_ctx.get('encryption', 'FIPS 140-2, AES-256')}."
        )

    return {"narrative": narrative, "detail_json": detail_json}


# ── Summary helpers ───────────────────────────────────────────────────────────

def _highest_risk(all_persona_results: list[dict]) -> str:
    """Return the highest risk_level or risk_rating found in any persona detail_json."""
    severity_order = ["CRITICAL", "HIGH", "MEDIUM", "LOW"]
    found: list[str] = []
    for result in all_persona_results:
        for step_personas in result.values() if isinstance(result, dict) else []:
            if not isinstance(step_personas, dict):
                continue
            for pr in step_personas.values() if isinstance(step_personas, dict) else []:
                if not isinstance(pr, dict):
                    continue
                dj = pr.get("detail_json", {})
                for field in ("risk_level", "risk_rating"):
                    val = dj.get(field, "")
                    if val:
                        found.append(str(val).upper())
    for sev in severity_order:
        if sev in found:
            return sev
    return "UNKNOWN"


# ── Core: generate all personas for all steps ─────────────────────────────────

def generate_all(
    flow_id: str,
    conn: Any,
    personas: list[str] | None = None,
    classification: str = "NIPR",
    use_llm: bool = True,
) -> dict:
    """Generate narrative + detail_json for every step × persona combination.

    Parameters
    ----------
    flow_id        : UUID of the traffic flow
    conn           : DB connection (network canvas DB)
    personas       : list of persona ids to generate; None = all defined
    classification : classification level (NIPR, IL4, IL5, IL6, SIPR)
    use_llm        : if False, skip LLM and return deterministic output only

    Returns
    -------
    dict:
        steps   : list of {step_number, node_id, node_label, action_type,
                           personas: {persona_id: {narrative, detail_json}}}
        summary : {hop_count, csps_traversed, classification, encryption,
                   inter_csp_hops, key_risk, total_latency_ms, description}
    """
    from tools.network.traffic_flow import TrafficFlowEngine

    engine = TrafficFlowEngine()
    engine._ensure_tables(conn)

    flow_row = conn.execute(
        "SELECT * FROM nc_traffic_flows WHERE id = ?", (flow_id,)
    ).fetchone()
    if not flow_row:
        return {"steps": [], "summary": {}}

    flow = dict(flow_row)
    steps = engine.get_walkthrough(flow_id, conn)
    if not steps:
        steps = engine.generate_walkthrough(flow_id, conn)

    # Load full node dicts from topology graph
    topo_row = conn.execute(
        "SELECT graph_json FROM topologies WHERE id = ?", (flow["topology_id"],)
    ).fetchone()
    nodes_dict: dict[str, dict] = {}
    if topo_row and topo_row["graph_json"]:
        raw = json.loads(topo_row["graph_json"])
        nodes_dict = {n["id"]: n for n in raw.get("nodes", [])}

    all_persona_ids = list(_personas_by_id().keys())
    active_personas = personas if personas is not None else all_persona_ids

    cls_ctx = _get_classification_ctx(classification)
    output_steps: list[dict] = []
    csps_seen: list[str] = []
    inter_csp_hops: list[str] = []
    total_latency_ms: int = 0
    all_results: list[dict] = []

    prev_node: dict | None = None
    prev_csp_key: str | None = None

    for step in steps:
        node_id = step.get("node_id", "")
        node = nodes_dict.get(node_id, {"id": node_id, "label": step.get("node_label", node_id), "type": ""})
        csp_key = detect_csp(node)

        # Track CSPs traversed
        if csp_key:
            csp_ctx = _cross_cloud_contexts().get(csp_key, {})
            csp_name = csp_ctx.get("name", csp_key)
            if csp_name not in csps_seen:
                csps_seen.append(csp_name)
            if prev_csp_key and prev_csp_key != csp_key:
                prev_csp_name = _cross_cloud_contexts().get(prev_csp_key, {}).get("name", prev_csp_key)
                inter_csp_hops.append(f"{prev_csp_name} → {csp_name}")

        # Accumulate latency from neteng detail or domain estimate
        domain_type = step.get("security_detail", {}).get("domain_type", "")
        total_latency_ms += _DOMAIN_LATENCY_MS.get(domain_type, 5)

        step_result: dict = {
            "step_number": step.get("step_number"),
            "node_id":     node_id,
            "node_label":  step.get("node_label", node_id),
            "action_type": step.get("action_type", ""),
            "personas":    {},
        }

        per_step_results: dict = {}
        for pid in active_personas:
            result = generate_for_persona(
                step=step,
                node=node,
                persona_id=pid,
                flow=flow,
                classification=classification,
                prev_node=prev_node if prev_node else None,
                llm_client=None,
            )
            step_result["personas"][pid] = result
            per_step_results[pid] = result

        output_steps.append(step_result)
        all_results.append({step.get("step_number"): per_step_results})

        prev_node = node
        prev_csp_key = csp_key

    # ── Domain labels for summary description ─────────────────────────────────
    domain_labels: list[str] = []
    seen_domains: set = set()
    for step in steps:
        dt = step.get("security_detail", {}).get("domain_type", "")
        if dt and dt not in seen_domains:
            seen_domains.add(dt)
            domain_labels.append(dt.replace("_", " ").upper())

    hop_count = len(steps)
    csp_str = ", ".join(csps_seen) if csps_seen else "on-prem only"
    cls_label = cls_ctx.get("label", classification)
    encryption = cls_ctx.get("encryption", "FIPS 140-2, AES-256")

    # Detect highest risk across all persona detail_json
    key_risk = _highest_risk(all_results)

    # Build a human-readable description
    domain_part = ", ".join(domain_labels) if domain_labels else "mixed domains"
    description = (
        f"{hop_count}-hop flow traversing {domain_part}"
        + (f", {csp_str}" if csps_seen else "")
        + f". Classification: {cls_label}. "
        + f"Encryption chain: {encryption}."
        + (f" Inter-CSP transits: {'; '.join(inter_csp_hops)}." if inter_csp_hops else "")
        + (f" Estimated end-to-end latency: {total_latency_ms} ms." if total_latency_ms else "")
    )

    summary = {
        "hop_count":        hop_count,
        "csps_traversed":   csps_seen,
        "inter_csp_hops":   inter_csp_hops,
        "classification":   cls_label,
        "encryption":       encryption,
        "key_risk":         key_risk,
        "total_latency_ms": total_latency_ms,
        "description":      description,
    }

    return {"steps": output_steps, "summary": summary}


# ── CLI ───────────────────────────────────────────────────────────────────────

def _cli() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="TFW Narrative Generator — generate persona narratives for a traffic flow"
    )
    parser.add_argument("--flow-id", required=True, help="Traffic flow UUID")
    parser.add_argument("--classification", default="NIPR",
                        help="Classification level (NIPR, IL4, IL5, IL6, SIPR)")
    parser.add_argument("--personas", nargs="+",
                        help="Persona IDs to include (default: all)")
    parser.add_argument("--no-llm", action="store_true",
                        help="Disable LLM narrative generation")
    parser.add_argument("--json", action="store_true", dest="as_json",
                        help="Output JSON")
    args = parser.parse_args()

    from tools.network.db.init_db import get_connection
    conn = get_connection()

    result = generate_all(
        flow_id=args.flow_id,
        conn=conn,
        personas=args.personas,
        classification=args.classification,
        use_llm=not args.no_llm,
    )

    if args.as_json:
        print(json.dumps(result, indent=2))
    else:
        summary = result.get("summary", {})
        print(f"Summary: {summary.get('description', '')}")
        for step in result.get("steps", []):
            print(f"\nStep {step['step_number']}: {step['node_label']} [{step['action_type']}]")
            for pid, pr in step.get("personas", {}).items():
                print(f"  [{pid}] {pr.get('narrative', '')[:120]}")


if __name__ == "__main__":
    _cli()
