# CUI // SP-CTI
"""ICDEV™ Network Canvas — Configuration Review Assistant.

Deterministic engine that prepares role-based guided prompts, builds LLM
prompts, parses the LLM response, and generates deterministic fallbacks
(sample templates and topology graphs) when no LLM is available.

No Flask dependency. Designed to be called from tools.network.blueprint and
from CLI/headless workflows.
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from typing import Any

from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.network.config_review")


# Import constants lazily to avoid circular imports at module load time.
def _roles():
    from tools.network.constants import CONFIG_REVIEW_ROLES

    return CONFIG_REVIEW_ROLES


def _questions():
    from tools.network.constants import CONFIG_REVIEW_QUESTIONS

    return CONFIG_REVIEW_QUESTIONS


def _detect_vendor(text: str) -> str:
    from tools.network.config_parser import detect_vendor

    return detect_vendor(text)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_roles() -> dict[str, dict[str, str]]:
    """Return all supported review roles."""
    return dict(_roles())


def get_questions(role_key: str) -> list[dict[str, str]]:
    """Return yes/no question bank for a role."""
    return list(_questions().get(role_key, []))


def generate_guided_prompts(role_key: str, vendor: str, hostname: str = "") -> list[dict[str, str]]:
    """Return 5–10 guided prompt cards for the selected role + vendor.

    Each card contains a short title, preview text, and the full prompt that
    would be sent to the LLM if the user selects it.
    """
    roles = _roles()
    role = roles.get(role_key)
    if not role:
        return []

    host_label = f" ({hostname})" if hostname else ""
    focus = role["focus"]
    prompt_focus = role["prompt_focus"]

    templates = [
        {
            "title": f"Review this {vendor} config{host_label} for {role['label']} concerns",
            "preview": f"Run a full {role['label'].lower()} review covering {focus.lower()[:60]}...",
            "prompt": (
                f"As a {role['label']}, perform a comprehensive review of the attached "
                f"{vendor} device configuration{host_label}. {focus} {prompt_focus} "
                f"Return findings in the requested JSON schema."
            ),
        },
        {
            "title": f"Generate a standardized {vendor} template from this config",
            "preview": "Extract the reusable pattern and produce a clean, commented baseline.",
            "prompt": (
                f"As a {role['label']}, convert the attached {vendor} config into a "
                f"standardized, reusable template. Remove site-specific values, add comments "
                f"explaining each section, and explain the standardization rationale."
            ),
        },
        {
            "title": "Identify security/compliance gaps",
            "preview": "Find hardening gaps, missing STIG controls, and protocol weaknesses.",
            "prompt": (
                f"As a {role['label']}, identify security and compliance gaps in the attached "
                f"{vendor} config. Map each finding to DISA STIG/NIST where applicable, rate severity, "
                f"and provide a remediation snippet."
            ),
        },
        {
            "title": "Suggest optimization opportunities",
            "preview": "Performance, manageability, and cost improvements.",
            "prompt": (
                f"As a {role['label']}, suggest optimizations for the attached {vendor} config. "
                f"Consider performance, operational efficiency, and simplicity. Provide before/after "
                f"snippets where relevant."
            ),
        },
        {
            "title": "Build a remediation plan",
            "preview": "Prioritized steps to bring the config in line with best practices.",
            "prompt": (
                f"As a {role['label']}, create a prioritized remediation plan for the attached "
                f"{vendor} config. Include steps, expected verification commands, rollback hints, "
                f"and priority order."
            ),
        },
        {
            "title": "Explain this config in plain language",
            "preview": "A paragraph-by-paragraph explanation suitable for stakeholders.",
            "prompt": (
                f"As a {role['label']} acting as a technical writer, explain the attached "
                f"{vendor} config in plain language. Break it down by section, call out risks, "
                f"and produce documentation suitable for a runbook."
            ),
        },
        {
            "title": "Extract interfaces and neighbors for topology",
            "preview": "Build a node/edge graph that can be opened in the NDC canvas.",
            "prompt": (
                f"As a {role['label']}, extract devices, interfaces, VLANs, and Layer-3/Layer-2 "
                f"relationships from the attached {vendor} config and return a topology graph "
                f"as JSON nodes/edges so it can be imported into the ICDEV Network Design Canvas."
            ),
        },
    ]

    # Trim prompts when role is very specific.
    if role_key == "technical_writer":
        # Move documentation/explanation prompt first.
        templates = [templates[5], templates[0], templates[1], templates[4]]
    elif role_key == "security_auditor":
        templates = [templates[0], templates[2], templates[4], templates[3], templates[6]]
    elif role_key == "network_architect":
        templates = [templates[0], templates[1], templates[3], templates[6], templates[4]]

    return templates[:7]


def build_llm_prompt(
    role_key: str,
    config_text: str,
    vendor: str,
    answers: dict[str, str],
    hostname: str = "",
    selected_prompt_title: str = "",
) -> str:
    """Build the final LLM prompt for a configuration review.

    Args:
        role_key: key in CONFIG_REVIEW_ROLES.
        config_text: raw device configuration text.
        vendor: detected or provided vendor key.
        answers: dict mapping question_id to "yes"|"no"|"unknown".
        hostname: optional device hostname from parser.
        selected_prompt_title: optional guided-prompt title that narrows focus.
    """
    roles = _roles()
    role = roles.get(role_key, {})
    role_label = role.get("label", role_key)
    focus = role.get("focus", "")
    prompt_focus = role.get("prompt_focus", "")
    questions = _questions().get(role_key, [])

    # Build a compact answer narrative for the LLM.
    answer_lines = []
    for q in questions:
        qid = q["id"]
        ans = answers.get(qid, "unknown").lower()
        answer_lines.append(f"- {q['question']} → {ans}")
        if ans == "no" and q.get("prompt_hook"):
            answer_lines.append(f"  (focus area: {q['prompt_hook']})")
    answer_block = "\n".join(answer_lines) if answer_lines else "- No contextual questions answered."

    # If a guided prompt title is supplied, bias the system instruction.
    extra_focus = ""
    if selected_prompt_title:
        extra_focus = f"\nThe user selected this guided focus: {selected_prompt_title}. Orient the response accordingly."

    prompt = f"""You are a senior {role_label} reviewing a network device configuration for an ICDEV™ Network Design Canvas user.

ROLE FOCUS: {focus}
REVIEW STYLE: {prompt_focus}{extra_focus}

CONTEXT QUESTIONS (user answers):
{answer_block}

DEVICE CONFIGURATION ({vendor}) {f"— hostname: {hostname}" if hostname else ""}:
```
{config_text}
```

INSTRUCTIONS:
1. Analyze the configuration for security/compliance gaps, optimization opportunities, and remediation needs from the perspective of a {role_label}.
2. Return a single JSON object (no markdown outside the JSON) with this exact schema:

{{
  "security_compliance": [
    {{
      "title": "string",
      "severity": "CAT1|CAT2|CAT3|info",
      "detail": "string",
      "remediation": "string",
      "sample_config_snippet": "string (CLI snippet if applicable, else \"\")",
      "references": ["STIG-XXX", "NIST-AC-XX"]
    }}
  ],
  "optimization": [
    {{
      "title": "string",
      "detail": "string",
      "recommendation": "string",
      "sample_config_snippet": "string (CLI snippet if applicable, else \"\")"
    }}
  ],
  "remediation": [
    {{
      "title": "string",
      "priority": "high|medium|low",
      "steps": ["step 1", "step 2"],
      "sample_config_snippet": "string (CLI snippet if applicable, else \"\")",
      "verification": "string"
    }}
  ],
  "sample_template": "string — a complete, sanitized, vendor-appropriate baseline config template derived from this device, with comments explaining each section",
  "explanation": "string — a concise markdown explanation of the review methodology and key takeaways",
  "topology_graph": {{
    "nodes": [{{"id": "string", "label": "string", "type": "router|switch|firewall|host|unknown", "x": 0, "y": 0, "properties": {{}}}}],
    "edges": [{{"id": "string", "source": "string", "target": "string", "label": "string", "properties": {{}}}}]
  }}
}}

3. For "sample_template", produce a vendor-appropriate (Cisco IOS/NX-OS style for cisco_ios/cisco_nxos, Juniper JunOS style for juniper) baseline template that could be reused across similar devices. Strip site-specific secrets and IP addresses; use placeholders like <HOSTNAME>, <MGMT_IP>, <VLAN_ID>.
4. Severity guidance: CAT1 = immediate exploit/complete exposure; CAT2 = significant weakness; CAT3 = minor finding; info = observation.
5. Keep all snippets syntactically valid for the detected vendor when possible.
"""
    return prompt


def parse_review_response(raw_text: str, vendor: str) -> dict[str, Any]:
    """Parse LLM response into the canonical review result structure.

    If the response is not valid JSON or is missing required keys, fill in
    deterministic fallbacks so the UI never crashes.
    """
    content = raw_text or ""
    # Try to extract JSON from markdown fences or raw text.
    json_match = re.search(r"\{.*\}", content, re.DOTALL)
    if not json_match:
        return _fallback_review("Could not parse LLM response as JSON.", vendor)

    try:
        data = json.loads(json_match.group())
    except json.JSONDecodeError as exc:
        logger.warning("config_review parse JSON error: %s", exc)
        return _fallback_review(f"JSON parse error: {exc}", vendor)

    # Normalize each section to a list.
    result = {
        "security_compliance": _as_list(data.get("security_compliance")),
        "optimization": _as_list(data.get("optimization")),
        "remediation": _as_list(data.get("remediation")),
        "sample_template": str(data.get("sample_template") or _generate_sample_template(vendor)),
        "explanation": str(data.get("explanation") or ""),
        "topology_graph": _normalize_topology(data.get("topology_graph"), vendor),
    }
    return result


def _as_list(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [dict(item) if isinstance(item, dict) else {"title": str(item)} for item in value]
    if isinstance(value, dict):
        return [dict(value)]
    return []


def _normalize_topology(value: Any, vendor: str) -> dict[str, list[dict[str, Any]]]:
    if isinstance(value, dict):
        nodes = _as_list(value.get("nodes"))
        edges = _as_list(value.get("edges"))
        return {"nodes": nodes, "edges": edges}
    return _fallback_topology(vendor)


def _fallback_review(reason: str, vendor: str) -> dict[str, Any]:
    return {
        "security_compliance": [
            {
                "title": "Review generated with deterministic fallback",
                "severity": "info",
                "detail": reason,
                "remediation": "Re-run the review when an LLM provider is available, or use the sample template below.",
                "sample_config_snippet": "",
                "references": [],
            }
        ],
        "optimization": [],
        "remediation": [],
        "sample_template": _generate_sample_template(vendor),
        "explanation": "The AI review was unavailable, so a deterministic baseline template was returned instead.",
        "topology_graph": _fallback_topology(vendor),
    }


def _generate_sample_template(vendor: str) -> str:
    """Return a deterministic vendor-appropriate baseline template."""
    if vendor == "juniper":
        return """## Juniper JunOS baseline template
system {
    host-name <HOSTNAME>;
    domain-name <DOMAIN>;
    name-server { <DNS1>; <DNS2>; }
    services {
        ssh { root-login deny; protocol-version v2; }
        netconf { ssh; }
    }
    syslog { user * { any emergency; } file messages { any notice; } }
    ntp { server <NTP_SERVER> key <KEY>; }
}
interfaces {
    lo0 { unit 0 { family inet { address <LOOPBACK_IP>; } } }
    ge-0/0/0 { unit 0 { description "<PEER_DESC>"; family inet { address <P2P_IP>; } } }
}
routing-options { router-id <ROUTER_ID>; autonomous-system <ASN>; }
protocols { ospf { area 0.0.0.0 { interface lo0.0 { passive; } interface ge-0/0/0.0; } } }
security { forward-policy { default-action permit; } }
"""
    if vendor in ("cisco_nxos", "nxos"):
        return """! Cisco NX-OS baseline template
hostname <HOSTNAME>
no ip domain-lookup
ip domain-name <DOMAIN>
nv overlay evpn
feature ospf
feature bgp
feature interface-vlan
feature hsrp
feature ssh
ssh version 2
no feature telnet
no feature http-server

ntp server <NTP_SERVER> use-vrf management
snmp-server group <GROUP> v3 auth
!
vlan <VLAN_ID>
  name <VLAN_NAME>
!
interface loopback0
  description RID
  ip address <LOOPBACK_IP>/<MASK>
!
interface Ethernet1/1
  description <PEER_DESC>
  no switchport
  ip address <P2P_IP>/<MASK>
  ip router ospf 1 area 0.0.0.0
"""
    # Default to Cisco IOS.
    return """! Cisco IOS baseline template
hostname <HOSTNAME>
!
no ip domain-lookup
ip domain-name <DOMAIN>
!
! Management plane hardening
enable secret <ENABLE_SECRET>
username <ADMIN> privilege 15 secret <SECRET>
aaa new-model
aaa authentication login default local
!
! Services
no service pad
service timestamps debug datetime msec
service timestamps log datetime msec
service password-encryption
!
! SSH only
ip ssh version 2
no ip http server
no ip http secure-server
!
ntp server <NTP_SERVER>
logging host <SYSLOG_SERVER>
!
snmp-server group <GROUP> v3 auth
snmp-server host <TRAP_HOST> version 3 priv <USER>
!
interface Loopback0
 description RID
 ip address <LOOPBACK_IP> <LOOPBACK_MASK>
!
interface GigabitEthernet0/0
 description <PEER_DESC>
 ip address <P2P_IP> <P2P_MASK>
!
router ospf 1
 router-id <ROUTER_ID>
 network <LOOPBACK_IP> 0.0.0.0 area 0
 network <P2P_NET> <P2P_WC> area 0
"""


def _fallback_topology(vendor: str) -> dict[str, list[dict[str, Any]]]:
    return {
        "nodes": [
            {
                "id": f"node-{uuid.uuid4().hex[:8]}",
                "label": "Device",
                "type": "router" if vendor in ("cisco_ios", "cisco_nxos", "ios_xr", "juniper") else "switch",
                "x": 100,
                "y": 100,
                "properties": {"vendor": vendor, "note": "Deterministic fallback node"},
            }
        ],
        "edges": [],
    }


def generate_export_config(vendor: str, findings: list[dict[str, Any]]) -> str:
    """Generate a starter config from the remediation snippets in findings.

    This is the deterministic path used when the LLM is unavailable or when the
    user clicks 'Export starter config'.
    """
    lines = [f"! Starter config generated from ICDEV Config Review ({vendor})", "!"]
    for f in findings:
        snippet = f.get("sample_config_snippet") or f.get("sample_config") or ""
        if snippet.strip():
            lines.append(f"! {f.get('title', 'finding')}")
            lines.append(snippet.strip())
            lines.append("")
    if len(lines) == 2:
        lines.append(_generate_sample_template(vendor))
    return "\n".join(lines)


def generate_export_topology(findings: list[dict[str, Any]], vendor: str) -> dict[str, list[dict[str, Any]]]:
    """Return a topology graph synthesized from findings or fallback."""
    # Prefer an embedded topology_graph from a finding if present.
    for f in findings:
        topo = f.get("topology_graph")
        if isinstance(topo, dict) and (topo.get("nodes") or topo.get("edges")):
            return _normalize_topology(topo, vendor)
    return _fallback_topology(vendor)


def compute_config_hash(config_text: str) -> str:
    """Stable hash for deduplication and audit references."""
    return hashlib.sha256(config_text.encode("utf-8")).hexdigest()[:16]
