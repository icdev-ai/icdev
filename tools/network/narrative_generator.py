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

import hashlib
import json
import sys
import uuid
from pathlib import Path
from typing import Any


# LLM stack — imported at module level so tests can monkeypatch these names on
# tools.network.narrative_generator directly (see tests/test_ndc_narrative_egress.py),
# and so the fail-closed egress gate can reuse the canonical locality primitive.
# Guarded so the module still loads (deterministic-only) when the LLM stack is
# unavailable; _invoke_llm then degrades to NARRATIVE_TEMPLATES.
try:
    from tools.llm.router import LLMRouter, _provider_is_local_only
    from tools.llm.provider import LLMRequest
except Exception:  # pragma: no cover - LLM stack optional
    LLMRouter = None  # type: ignore[assignment]
    LLMRequest = None  # type: ignore[assignment]
    _provider_is_local_only = None  # type: ignore[assignment]

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.logging.icdev_logger import get_logger  # noqa: E402
logger = get_logger(__name__)

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


# ── Public API ────────────────────────────────────────────────────────────────

def load_personas() -> list[dict]:
    """Return the list of all persona dicts from tfw_personas.yaml."""
    return _load_personas_config().get("personas", [])


def load_classification_context(level: str) -> dict:
    """Return the classification overlay dict for a given level key (NIPR, IL4, IL5, IL6, SIPR)."""
    return _get_classification_ctx(level)


def load_cross_cloud_context(node: dict) -> dict | None:
    """Return cross-cloud context dict for a CSP node, or None if not a CSP node."""
    return _get_csp_ctx(node)


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


# ── Deterministic narrative templates (per action × persona) ─────────────────
# Used as rich fallback when LLM is unavailable.  Template variables: {node_label},
# {action_type}, {classification}.  Add entries for new action types as needed.

NARRATIVE_TEMPLATES: dict[str, dict[str, str]] = {
    "authenticate": {
        "seceng": (
            "[SecEng] Authentication at {node_label}: CAC/PIV or FIDO2 MFA required. "
            "DoD PKI chain validated. TLS 1.3 with AES-256-GCM enforced."
        ),
        "neteng": (
            "[NetEng] Auth hop at {node_label}. Route selected for {action_type}. "
            "BGP-advertised path, latency ~5 ms."
        ),
        "cloudarch": (
            "[CloudArch] Authentication at {node_label} via CSP identity federation "
            "(SAML 2.0 / OIDC)."
        ),
        "compofficer": (
            "[CompOfficer] NIST IA-2(1) satisfied at {node_label}. "
            "MFA mandatory per FedRAMP-High baseline. DoD PKI required."
        ),
        "appdev": (
            "[AppDev] App authenticates at {node_label}. "
            "Check HTTP 401 on token expiry; refresh via /token endpoint."
        ),
        "missionowner": (
            "[Mission] Users authenticate at {node_label} before accessing the mission system."
        ),
        "ciso": (
            "[CISO] Authentication control at {node_label}. MEDIUM risk. "
            "IA-2 satisfied; MFA in place."
        ),
    },
    "encrypt_vpn": {
        "seceng": (
            "[SecEng] VPN encryption at {node_label}: IKEv2/AES-256-GCM, SHA-384, "
            "DH Group 20 (ECDH P-384), PFS enabled."
        ),
        "neteng": (
            "[NetEng] IPSec tunnel at {node_label}. IKEv2, AES-256-GCM, PFS Group 20, "
            "SA lifetime 1 h / 4 GB."
        ),
        "cloudarch": (
            "[CloudArch] Cloud VPN gateway at {node_label}. FIPS 140-2 Level 1+ compliant."
        ),
        "compofficer": (
            "[CompOfficer] SC-8 Transmission Confidentiality at {node_label}. "
            "SC-8(1) cryptographic protection required. FIPS 140-2 Level 1+."
        ),
        "appdev": (
            "[AppDev] Traffic encrypted at {node_label}. "
            "App observes TLS 1.3 to backend; no cleartext at this hop."
        ),
        "missionowner": (
            "[Mission] Mission traffic encrypted at {node_label}. "
            "No capability impact from this hop."
        ),
        "ciso": (
            "[CISO] Encryption control at {node_label}. SC-8 satisfied. LOW risk."
        ),
    },
    "security_inspect": {
        "seceng": (
            "[SecEng] Security inspection at {node_label}: IDPS signatures, CDM posture, WAF, DLP."
        ),
        "neteng": (
            "[NetEng] Inspect hop at {node_label}. Traffic mirrored to IDPS sensor."
        ),
        "cloudarch": (
            "[CloudArch] Cloud-native WAF/IDPS at {node_label}. CSP firewall premium tier."
        ),
        "compofficer": (
            "[CompOfficer] SI-4 monitoring at {node_label}. CDM sensor active; logs to SIEM."
        ),
        "appdev": (
            "[AppDev] Traffic inspected at {node_label}. WAF may return HTTP 403 for malformed requests."
        ),
        "missionowner": (
            "[Mission] Security inspection at {node_label} protects mission data from threats."
        ),
        "ciso": (
            "[CISO] IDPS/WAF at {node_label}. SC-7 satisfied. Active monitoring in place."
        ),
    },
    "originate": {
        "seceng": (
            "[SecEng] Traffic originates at {node_label}. Source address validation required."
        ),
        "neteng": (
            "[NetEng] Packet originates at {node_label}. Initial routing decision; source IP verified."
        ),
        "cloudarch": (
            "[CloudArch] Traffic originates at {node_label}. Source endpoint configured in landing zone."
        ),
        "compofficer": (
            "[CompOfficer] Traffic originates at {node_label}. Source boundary documented in SSP §8."
        ),
        "appdev": (
            "[AppDev] Request initiates at {node_label}. App sends initial HTTP request to backend."
        ),
        "missionowner": "[Mission] Mission flow begins at {node_label}.",
        "ciso": "[CISO] Flow origination at {node_label}. Source boundary verified.",
    },
    "deliver": {
        "seceng": (
            "[SecEng] Traffic delivered at {node_label}. Final ACL check and logging complete."
        ),
        "neteng": "[NetEng] Packet delivered at {node_label}. Destination reached; route confirmed.",
        "cloudarch": (
            "[CloudArch] Traffic delivered at {node_label}. Destination service reached via private endpoint."
        ),
        "compofficer": (
            "[CompOfficer] Traffic delivered at {node_label}. Destination boundary documented in SSP."
        ),
        "appdev": "[AppDev] Response received at {node_label}. Expect HTTP 200.",
        "missionowner": "[Mission] Mission flow completes at {node_label}. Capability delivered.",
        "ciso": "[CISO] Flow delivered at {node_label}. Destination boundary verified. LOW risk.",
    },
}


# ── Classification overlay builder ────────────────────────────────────────────

def build_classification_overlay(flow: dict) -> str:
    """Return a classification context string for injection into a system prompt.

    For IL5 flows the string includes "FIPS 140-2 Level 2".
    """
    classification = flow.get("classification", "NIPR")
    ctx = _get_classification_ctx(classification)
    return (
        f"Classification: {ctx.get('label', classification)}. "
        f"Required encryption: {ctx.get('encryption', 'FIPS 140-2, AES-256')}. "
        f"Key management: {ctx.get('key_mgmt', 'DoD PKI')}. "
        f"MFA: {ctx.get('mfa', 'CAC/PIV')}. "
        f"Audit retention: {ctx.get('audit_retention', '3 years')}."
    )


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


# ── Classification egress gate (ndc-gov-02) ───────────────────────────────────
# Gov/DoD fail-closed posture: TFW narrative content (node/flow details) may only
# egress to an LLM provider when the flow classification is UNRESTRICTED
# (NIPR/unclassified), or when the resolved provider is LOCAL/air-gapped. The
# restricted set below is the classification-side decision; the local/cloud
# decision reuses the canonical locality primitive (see _tfw_provider_is_local).
RESTRICTED_CLASSIFICATIONS: frozenset[str] = frozenset(
    {"CUI", "IL4", "IL5", "IL6", "SIPR"}
)


def _is_restricted_classification(classification: str) -> bool:
    """True when *classification* requires a local-only LLM provider for egress."""
    return (classification or "").strip().upper() in RESTRICTED_CLASSIFICATIONS


def _tfw_provider_is_local(router: Any) -> tuple[bool, str]:
    """Resolve the ``tfw_narrative`` provider and whether it is local-only.

    Reuses the ONE definition of "local" for the CUI egress boundary — the router's
    ``_provider_is_local_only`` (a fail-closed wrapper over
    ``cli_bridge.activate._is_local_only_provider``): a provider of ``type: ollama``
    carrying no ``api_key_env``. Inventing a second locality definition here is
    precisely how CUI leaks, so we delegate.

    Fail-closed: any resolution error (or an unavailable LLM stack) is treated as
    NOT local. Returns ``(is_local, provider_name)``.
    """
    if _provider_is_local_only is None:  # LLM stack unavailable at import
        return False, ""
    try:
        _provider, _model_id, model_cfg = router.get_provider_for_function("tfw_narrative")
        provider_name = (model_cfg or {}).get("provider", "")
        providers = getattr(router, "_config", {}).get("providers", {}) or {}
        return bool(_provider_is_local_only(provider_name, providers)), provider_name
    except Exception as exc:
        logger.warning("tfw narrative provider locality resolution failed: %s", exc)
        return False, ""


# ── LLM invocation ────────────────────────────────────────────────────────────

def _invoke_llm(
    system_prompt: str,
    user_content: str,
    classification: str = "NIPR",
    persona_id: str = "",
) -> str | None:
    if LLMRouter is None or LLMRequest is None:  # LLM stack unavailable
        return None
    try:
        router = LLMRouter()
        if not router.has_any_llm():
            return None

        # Fail-closed classification egress gate (ndc-gov-02): a restricted
        # classification may ONLY use a local/air-gapped provider. If the resolved
        # provider for tfw_narrative is cloud-hosted, do NOT egress the
        # (potentially classified) node/flow details — return None so the caller
        # falls back to the deterministic NARRATIVE_TEMPLATES path.
        if _is_restricted_classification(classification):
            is_local, provider_name = _tfw_provider_is_local(router)
            if not is_local:
                logger.warning(
                    "tfw narrative LLM egress BLOCKED (fail-closed): classification=%s "
                    "persona=%s resolved_provider=%s is not local — falling back to "
                    "deterministic templates.",
                    classification,
                    persona_id or "?",
                    provider_name or "?",
                )
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
    except Exception as exc:
        logger.warning("tfw narrative LLM call failed: %s", exc)
    return None


# ── Read-through narrative cache (ndc-perf-01) ────────────────────────────────
# Persisted persona narratives in nc_step_persona_responses are re-usable across
# walkthrough runs when nothing that feeds the prompt has changed. We key the
# cache on (step_id, persona_id, classification, flow_hash), where flow_hash is a
# sha256 (repo rule: NEVER md5) over the canonicalized step + node + prev_node +
# flow fields that actually feed the system/user prompt. The step_id + persona_id
# select the row (they are the table's UNIQUE key); classification + flow_hash are
# stored inside the existing detail_json column under reserved meta keys, so NO
# schema migration is required. Rows written before this change carry no meta and
# are treated as cache MISSES.
_CACHE_HASH_KEY = "_cache_flow_hash"
_CACHE_CLS_KEY = "_cache_classification"


def _canonical_flow_hash(
    step: dict,
    node: dict,
    flow: dict,
    prev_node: dict | None,
) -> str:
    """Return a sha256 hex digest over the prompt-relevant step/node/flow content.

    The payload includes exactly the fields that flow into the persona prompts
    (see generate_for_persona): the step's action/details, the node (type/label/
    config drives CSP context), the previous node (inter-CSP detection) and the
    flow's app_type. Persona and classification are NOT hashed here — they are
    independent components of the cache key.
    """
    payload = {
        "step": {
            "step_number": step.get("step_number"),
            "node_id": step.get("node_id", ""),
            "node_label": step.get("node_label", ""),
            "action_type": step.get("action_type", ""),
            "security_detail": step.get("security_detail", {}),
            "network_detail": step.get("network_detail", {}),
        },
        "node": {
            "id": node.get("id", ""),
            "type": node.get("type", ""),
            "label": node.get("label", ""),
            "config": node.get("config", {}),
        },
        "prev_node": (
            {
                "id": prev_node.get("id", ""),
                "type": prev_node.get("type", ""),
                "label": prev_node.get("label", ""),
            }
            if prev_node
            else None
        ),
        "flow": {
            "app_type": flow.get("app_type", ""),
        },
    }
    canon = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def _read_cached_response(
    conn: Any,
    step_id: str | None,
    persona_id: str,
    classification: str,
    flow_hash: str,
) -> dict | None:
    """Return a cached {narrative, detail_json} on a hit, else None (miss).

    Graceful degradation: any error (missing table, unusable connection, bad JSON)
    is logged at debug and treated as a cache miss — cache reads must NEVER fail
    narrative generation. A row whose stored meta does not match the current
    flow_hash + classification (including legacy rows with no meta) is a miss.
    """
    if conn is None or not step_id:
        return None
    try:
        row = conn.execute(
            "SELECT narrative, detail_json FROM nc_step_persona_responses"
            " WHERE step_id = %s AND persona_id = %s",
            (step_id, persona_id),
        ).fetchone()
        if not row:
            return None
        row = dict(row)
        stored = json.loads(row.get("detail_json") or "{}")
        if not isinstance(stored, dict):
            return None
        if stored.get(_CACHE_HASH_KEY) != flow_hash:
            return None
        if (stored.get(_CACHE_CLS_KEY) or "") != (classification or ""):
            return None
        # Strip cache meta so the returned detail_json matches a fresh generation.
        clean = {
            k: v for k, v in stored.items()
            if k not in (_CACHE_HASH_KEY, _CACHE_CLS_KEY)
        }
        return {"narrative": row.get("narrative") or "", "detail_json": clean}
    except Exception as exc:  # pragma: no cover - defensive, logged as miss
        logger.debug("narrative cache read miss (error): %s", exc)
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
    use_llm: bool = True,
    conn: Any = None,
    step_id: str | None = None,
    flow_hash: str | None = None,
    force_regenerate: bool = False,
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
    conn        : optional canvas DB connection for the read-through cache
    step_id     : optional nc_flow_walkthrough_steps.id (cache row selector)
    flow_hash   : optional precomputed canonical hash; computed if not supplied
    force_regenerate : if True, bypass the cache and always (re)generate

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

    # ── Read-through cache lookup (ndc-perf-01) ───────────────────────────────
    # Compute the canonical flow hash (shared with generate_all's persist step)
    # and, unless force_regenerate is set, return any matching persisted response
    # WITHOUT invoking the LLM. Cache-read failures degrade to a miss.
    if flow_hash is None:
        flow_hash = _canonical_flow_hash(step, node, flow, prev_node)
    if not force_regenerate:
        cached = _read_cached_response(
            conn, step_id, persona_id, classification, flow_hash
        )
        if cached is not None:
            return cached

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

    narrative = (
        _invoke_llm(
            system_prompt,
            user_content,
            classification=classification,
            persona_id=persona_id,
        )
        if use_llm
        else None
    )

    if not narrative:
        # Try NARRATIVE_TEMPLATES for a richer deterministic fallback
        tmpl = NARRATIVE_TEMPLATES.get(action_type, {}).get(persona_id, "")
        if tmpl:
            try:
                narrative = tmpl.format(
                    node_label=node_label,
                    action_type=action_type,
                    classification=classification,
                )
            except KeyError:
                narrative = tmpl
        else:
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
    force_regenerate: bool = False,
) -> dict:
    """Generate narrative + detail_json for every step × persona combination.

    Parameters
    ----------
    flow_id        : UUID of the traffic flow
    conn           : DB connection (network canvas DB)
    personas       : list of persona ids to generate; None = all defined
    classification : classification level (NIPR, IL4, IL5, IL6, SIPR)
    use_llm        : if False, skip LLM and return deterministic output only
    force_regenerate : if True, bypass the read-through cache and re-generate
                       every step × persona (see ndc-perf-01)

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
        "SELECT * FROM nc_traffic_flows WHERE id = %s", (flow_id,)
    ).fetchone()
    if not flow_row:
        return {"steps": [], "summary": {}}

    flow = dict(flow_row)
    steps = engine.get_walkthrough(flow_id, conn)
    if not steps:
        steps = engine.generate_walkthrough(flow_id, conn)

    # Load full node dicts from topology graph
    topo_row = conn.execute(
        "SELECT graph_json FROM topologies WHERE id = %s", (flow["topology_id"],)
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

    # ── Read-through cache wiring (ndc-perf-01) ───────────────────────────────
    # Resolve step DB ids up front so generate_for_persona can look up (and, on a
    # miss, we can persist under) the canonical flow hash. Best-effort: a lookup
    # failure just yields an empty map, degrading every step to a cache miss.
    step_id_by_num: dict[Any, str] = {}
    try:
        _sid_rows = conn.execute(
            "SELECT id, step_number FROM nc_flow_walkthrough_steps WHERE flow_id = %s",
            (flow_id,),
        ).fetchall()
        step_id_by_num = {r["step_number"]: r["id"] for r in _sid_rows}
    except Exception as _sid_exc:  # pragma: no cover - defensive
        logger.debug("narrative cache: step-id lookup failed: %s", _sid_exc)
    flow_hash_by_num: dict[Any, str] = {}

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

        step_num = step.get("step_number")
        s_id = step_id_by_num.get(step_num)
        flow_hash = _canonical_flow_hash(step, node, flow, prev_node if prev_node else None)
        flow_hash_by_num[step_num] = flow_hash

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
                use_llm=use_llm,
                conn=conn,
                step_id=s_id,
                flow_hash=flow_hash,
                force_regenerate=force_regenerate,
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

    # Persist persona responses to nc_step_persona_responses (best-effort).
    # Embed the cache meta (flow_hash + classification) into detail_json so a later
    # generate_all with an unchanged flow reads it back instead of re-invoking the
    # LLM (ndc-perf-01). step_id_by_num was resolved above.
    try:
        for out_step in output_steps:
            s_num = out_step["step_number"]
            s_id = step_id_by_num.get(s_num)
            if not s_id:
                continue
            f_hash = flow_hash_by_num.get(s_num, "")
            for pid, pr in out_step["personas"].items():
                detail_with_meta = dict(pr.get("detail_json", {}))
                detail_with_meta[_CACHE_HASH_KEY] = f_hash
                detail_with_meta[_CACHE_CLS_KEY] = classification
                conn.execute(
                    "INSERT OR REPLACE INTO nc_step_persona_responses"
                    " (id, step_id, persona_id, narrative, detail_json)"
                    " VALUES (%s, %s, %s, %s, %s)",
                    (
                        str(uuid.uuid4()),
                        s_id,
                        pid,
                        pr.get("narrative", ""),
                        json.dumps(detail_with_meta),
                    ),
                )
        conn.commit()
    except Exception as _persist_exc:
        logger.warning("narrative_generator: nc_step_persona_responses persist failed: %s", _persist_exc)

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
    parser.add_argument("--force-regenerate", action="store_true",
                        help="Bypass the read-through narrative cache and regenerate")
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
        force_regenerate=args.force_regenerate,
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
