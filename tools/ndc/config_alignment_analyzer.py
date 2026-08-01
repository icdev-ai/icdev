#!/usr/bin/env python3
# CUI // SP-CTI
"""NDC Config Alignment Analyzer.

Parses a device running config, extracts sections, retrieves relevant SOPs
via RAG, and scores alignment against best practices. Outputs PASS/WARN/FAIL
per section with rationale and optimization recommendations.

Usage:
    python tools/ndc/config_alignment_analyzer.py --device-id <id> --json
    python tools/ndc/config_alignment_analyzer.py --config-file path.conf --vendor cisco_ios --json
    python tools/ndc/config_alignment_analyzer.py --config-text "..." --vendor juniper --json
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent
_NC_DB = BASE_DIR / "data" / "network_canvas.db"


# ---------------------------------------------------------------------------
# Deterministic rule-based checks (air-gap safe, zero LLM)
# ---------------------------------------------------------------------------

_ALIGNMENT_RULES = {
    "bgp": {
        "checks": [
            {
                "name": "BGP MD5 Authentication",
                "pattern": re.compile(r"password|md5|auth-key", re.I),
                "weight": 15,
                "rationale": "BGP sessions without MD5/TCP-AO authentication are vulnerable to session hijacking.",
            },
            {
                "name": "BGP Prefix Filtering",
                "pattern": re.compile(r"prefix-list|route-map.*(filter|permit|deny)|import|export", re.I),
                "weight": 15,
                "rationale": "Unfiltered BGP advertisements can leak routes or accept invalid prefixes.",
            },
            {
                "name": "BGP BFD",
                "pattern": re.compile(r"bfd|minimum-interval|multiplier", re.I),
                "weight": 10,
                "rationale": "BFD provides sub-second failure detection for BGP peering sessions.",
            },
            {
                "name": "BGP Route Dampening",
                "pattern": re.compile(r"dampen|damping|flap", re.I),
                "weight": 5,
                "rationale": "Route dampening suppresses unstable routes from propagating.",
            },
        ],
    },
    "ospf": {
        "checks": [
            {
                "name": "OSPF Authentication",
                "pattern": re.compile(r"authentication|message-digest|ip ospf auth", re.I),
                "weight": 15,
                "rationale": "OSPF without authentication is susceptible to rogue router injection.",
            },
            {
                "name": "OSPF Stub Area",
                "pattern": re.compile(r"stub|nssa|no-summary", re.I),
                "weight": 5,
                "rationale": "Stub/NSSA areas reduce LSA flooding and improve convergence.",
            },
        ],
    },
    "security": {
        "checks": [
            {
                "name": "ACL / Firewall Filters",
                "pattern": re.compile(r"access-list|ip access-group|filter|firewall", re.I),
                "weight": 15,
                "rationale": "Missing ACLs expose management and data planes to unauthorized access.",
            },
            {
                "name": "SNMPv3",
                "pattern": re.compile(r"snmp-server group.*v3|snmp-server host.*version 3|snmpv3", re.I),
                "weight": 15,
                "rationale": "SNMPv1/v2c send community strings in cleartext; SNMPv3 provides encryption and auth.",
            },
            {
                "name": "AAA / TACACS+",
                "pattern": re.compile(r"tacacs|aaa|radius", re.I),
                "weight": 15,
                "rationale": "Centralized AAA ensures consistent authentication and audit logging.",
            },
        ],
    },
    "management": {
        "checks": [
            {
                "name": "NTP Configuration",
                "pattern": re.compile(r"ntp server|ntp peer", re.I),
                "weight": 10,
                "rationale": "Clock drift breaks certificate validation, logging correlation, and event sequencing.",
            },
            {
                "name": "Syslog",
                "pattern": re.compile(r"logging|syslog", re.I),
                "weight": 10,
                "rationale": "Remote syslog is required for centralized audit and SIEM ingestion.",
            },
            {
                "name": "SSH Only (No Telnet)",
                "pattern": re.compile(r"ssh|ip ssh version 2", re.I),
                "weight": 10,
                "rationale": "Telnet transmits credentials in plaintext.",
            },
            {
                "name": "No Telnet Enabled",
                "negative_pattern": re.compile(r"telnet server|service telnet", re.I),
                "weight": 10,
                "rationale": "Telnet must be explicitly disabled.",
            },
        ],
    },
    "qos": {
        "checks": [
            {
                "name": "QoS Class-Map / Policy-Map",
                "pattern": re.compile(r"class-map|policy-map|service-policy", re.I),
                "weight": 15,
                "rationale": "QoS policies protect voice/video traffic and prevent bufferbloat.",
            },
            {
                "name": "DSCP Trust Boundary",
                "pattern": re.compile(r"dscp|trust|cos|precedence", re.I),
                "weight": 10,
                "rationale": "DSCP trust boundaries ensure end-to-end QoS marking is preserved.",
            },
        ],
    },
    "interfaces": {
        "checks": [
            {
                "name": "Interface Descriptions",
                "pattern": re.compile(r"description", re.I),
                "weight": 10,
                "rationale": "Descriptions improve operational troubleshooting and circuit tracing.",
            },
            {
                "name": "Unused Ports Shutdown",
                "pattern": re.compile(r"shutdown", re.I),
                "weight": 10,
                "rationale": "Unused interfaces should be administratively shutdown to prevent unauthorized access.",
            },
        ],
    },
}


def _rag_sops_for_section(section_name: str, section_text: str, top_k: int = 3) -> List[Dict[str, Any]]:
    """Retrieve relevant SOP chunks from RAG for a config section."""
    try:
        from tools.llm import get_embedding_provider
        from tools.rag.vector_store_factory import VectorStoreFactory

        provider = get_embedding_provider()
        store = VectorStoreFactory.create()
        query = f"{section_name} best practices network configuration {section_text[:200]}"
        emb = provider.embed(query)
        results = store.search(emb, top_k=top_k, filters={"source_type": "ndc_sops"})
        return [
            {"chunk_id": r.chunk_id, "content": r.content[:400], "score": round(r.score, 4)}
            for r in results
        ]
    except Exception:
        return []


def _llm_analyze_section(section_name: str, section_text: str, sops: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Use LLM to compare config section against best practices. Returns None if LLM unavailable."""
    try:
        from tools.llm.router import LLMRouter
        from tools.llm.provider import LLMRequest

        sop_text = "\n\n".join(f"SOP {i+1}:\n{s['content']}" for i, s in enumerate(sops[:2]))
        prompt = (
            f"You are a senior network engineer auditing a device configuration section.\n\n"
            f"Section: {section_name}\n"
            f"Config:\n```\n{section_text[:800]}\n```\n\n"
            f"Relevant SOPs:\n{sop_text}\n\n"
            "Evaluate this section against industry best practices and the SOPs above. "
            "Respond with ONLY a JSON object in this exact format:\n"
            '{"status": "PASS|WARN|FAIL", "score": 0-100, "rationale": "brief explanation", '
            '"recommendations": ["one", "two"]}'
        )
        router = LLMRouter()
        req = LLMRequest(
            messages=[{"role": "user", "content": prompt}],
            system_prompt="You are a network configuration auditor. Respond with valid JSON only.",
            max_tokens=512,
        )
        resp = router.invoke("code_generation", req)
        result = json.loads(resp.content)
        return {
            "status": result.get("status", "WARN"),
            "score": int(result.get("score", 50)),
            "rationale": result.get("rationale", ""),
            "recommendations": result.get("recommendations", []),
            "model": getattr(resp, "model_id", ""),
        }
    except Exception:
        return None


def _deterministic_score_section(section_name: str, section_text: str) -> Dict[str, Any]:
    """Score a config section using deterministic keyword rules."""
    ruleset = _ALIGNMENT_RULES.get(section_name.lower(), {"checks": []})
    checks = ruleset["checks"]
    total_weight = sum(c["weight"] for c in checks)
    score = 0
    findings: List[str] = []
    recommendations: List[str] = []

    for check in checks:
        matched = False
        if "pattern" in check:
            matched = bool(check["pattern"].search(section_text))
        if "negative_pattern" in check:
            matched = not bool(check["negative_pattern"].search(section_text))

        if matched:
            score += check["weight"]
        else:
            findings.append(f"Missing: {check['name']}")
            recommendations.append(f"Add {check['name']}: {check['rationale']}")

    pct = int((score / max(total_weight, 1)) * 100)
    if pct >= 90:
        status = "PASS"
    elif pct >= 60:
        status = "WARN"
    else:
        status = "FAIL"

    return {
        "status": status,
        "score": pct,
        "rationale": "; ".join(findings) if findings else "All checked best practices present.",
        "recommendations": recommendations,
        "model": "deterministic",
    }


def _extract_sections_from_config(config_text: str, vendor: str = "") -> Dict[str, str]:
    """Split raw config into logical sections for analysis."""
    sections: Dict[str, str] = {}

    # BGP
    bgp_match = re.search(
        r"router bgp.*?(?=\nrouter|\n\!|\n\Z|set protocols bgp.*?$)"
        if vendor != "juniper"
        else r"set protocols bgp.*?(?=set protocols [^b]|$)",
        config_text,
        re.DOTALL | re.I,
    )
    if bgp_match:
        sections["bgp"] = bgp_match.group(0)

    # OSPF
    ospf_match = re.search(
        r"router ospf.*?(?=\nrouter|\n\!|\n\Z)" if vendor != "juniper" else r"set protocols ospf.*?(?=set protocols [^o]|$)",
        config_text,
        re.DOTALL | re.I,
    )
    if ospf_match:
        sections["ospf"] = ospf_match.group(0)

    # Security: ACLs / firewall filters
    acl_lines = [
        line for line in config_text.splitlines()
        if re.search(r"access-list|ip access-group|firewall filter|filter", line, re.I)
    ]
    if acl_lines:
        sections["security"] = "\n".join(acl_lines)

    # Management: NTP, SNMP, Syslog, SSH
    mgmt_lines = [
        line for line in config_text.splitlines()
        if re.search(r"ntp|snmp|syslog|logging|ssh|telnet|aaa|tacacs|radius", line, re.I)
    ]
    if mgmt_lines:
        sections["management"] = "\n".join(mgmt_lines)

    # QoS
    qos_lines = [
        line for line in config_text.splitlines()
        if re.search(r"class-map|policy-map|service-policy|dscp|qos|trust|precedence", line, re.I)
    ]
    if qos_lines:
        sections["qos"] = qos_lines[0] if len(qos_lines) == 1 else "\n".join(qos_lines)

    # Interfaces
    iface_blocks = re.findall(
        r"interface \S+.*?!(?=\ninterface|\n\Z)" if vendor != "juniper" else r"set interfaces \S+.*?$",
        config_text,
        re.DOTALL | re.I | re.M,
    )
    if iface_blocks:
        sections["interfaces"] = "\n".join(iface_blocks[:20])  # limit to first 20

    return sections


def analyze_config(
    config_text: str,
    vendor: str = "",
    use_llm: bool = True,
    top_k: int = 3,
) -> Dict[str, Any]:
    """Analyze a device config and return alignment scores per section."""
    sections = _extract_sections_from_config(config_text, vendor)

    if not sections:
        return {
            "classification": "CUI // SP-CTI",
            "overall_status": "FAIL",
            "overall_score": 0,
            "sections": [],
            "recommendations": ["Config text could not be parsed into recognizable sections."],
        }

    section_results: List[Dict[str, Any]] = []
    total_score = 0
    total_weight = 0

    for sec_name, sec_text in sections.items():
        sops = _rag_sops_for_section(sec_name, sec_text, top_k)
        llm_result = None
        if use_llm:
            llm_result = _llm_analyze_section(sec_name, sec_text, sops)

        if llm_result:
            result = llm_result
            result["rag_sops"] = sops
        else:
            det = _deterministic_score_section(sec_name, sec_text)
            det["rag_sops"] = sops
            result = det

        weight = sum(c["weight"] for c in _ALIGNMENT_RULES.get(sec_name.lower(), {}).get("checks", []))
        if weight == 0:
            weight = 10
        total_score += result["score"] * weight
        total_weight += weight
        section_results.append({"section": sec_name, **result})

    overall = int(total_score / max(total_weight, 1))
    if overall >= 90:
        overall_status = "PASS"
    elif overall >= 70:
        overall_status = "WARN"
    else:
        overall_status = "FAIL"

    # Aggregate recommendations
    all_recs: List[str] = []
    for sr in section_results:
        for rec in sr.get("recommendations", []):
            if rec not in all_recs:
                all_recs.append(rec)

    return {
        "classification": "CUI // SP-CTI",
        "overall_status": overall_status,
        "overall_score": overall,
        "sections": section_results,
        "recommendations": all_recs,
    }


def analyze_device(device_id: str, use_llm: bool = True) -> Dict[str, Any]:
    """Fetch config from ni_device_configs and analyze."""
    from tools.network.db.init_db import get_connection

    conn = get_connection()
    row = conn.execute(
        """SELECT config_text, config_type FROM ni_device_configs
           WHERE device_id = %s
           ORDER BY CASE config_type
             WHEN 'running' THEN 1
             WHEN 'startup' THEN 2
             ELSE 3
           END, created_at DESC LIMIT 1""",
        (device_id,),
    ).fetchone()
    conn.close()

    if not row:
        return {"error": f"No config found for device {device_id}"}

    config_text = row["config_text"]
    return analyze_config(config_text, vendor="", use_llm=use_llm)


def main() -> None:
    parser = argparse.ArgumentParser(description="NDC Config Alignment Analyzer")
    parser.add_argument("--device-id", type=str, help="Device ID in ni_device_configs")
    parser.add_argument("--config-file", type=str, help="Path to raw config file")
    parser.add_argument("--config-text", type=str, help="Raw config text (inline)")
    parser.add_argument("--vendor", type=str, default="", help="Vendor slug (cisco_ios, juniper, etc.)")
    parser.add_argument("--no-llm", action="store_true", help="Force deterministic rules (skip LLM)")
    parser.add_argument("--top-k", type=int, default=3, help="RAG SOP chunks per section")
    parser.add_argument("--json", action="store_true", dest="json_output", help="JSON output")
    args = parser.parse_args()

    if args.device_id:
        result = analyze_device(args.device_id, use_llm=not args.no_llm)
    elif args.config_file:
        text = Path(args.config_file).read_text(encoding="utf-8", errors="replace")
        result = analyze_config(text, vendor=args.vendor, use_llm=not args.no_llm, top_k=args.top_k)
    elif args.config_text:
        result = analyze_config(args.config_text, vendor=args.vendor, use_llm=not args.no_llm, top_k=args.top_k)
    else:
        parser.error("Provide --device-id, --config-file, or --config-text")

    if args.json_output:
        print(json.dumps(result, indent=2))
    else:
        if "error" in result:
            print(f"Error: {result['error']}")
        else:
            print(f"Overall: {result['overall_status']} ({result['overall_score']}%)")
            for sec in result["sections"]:
                print(f"  {sec['section']}: {sec['status']} ({sec['score']}%) — {sec['rationale'][:80]}")
            if result["recommendations"]:
                print("Recommendations:")
                for rec in result["recommendations"][:10]:
                    print(f"  - {rec}")


if __name__ == "__main__":
    main()
