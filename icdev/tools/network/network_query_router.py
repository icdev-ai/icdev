# [CUI // SP-CTI]
"""ICDEV™ Network Infrastructure Intelligence — Query Router.

Classifies natural language network questions into one of 13 analysis
intents and dispatches to the appropriate intelligence engine function.

Usage:
    python tools/network/network_query_router.py --topology-id T1 --query "Do I need redundant link in NYC?" --json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.logging.icdev_logger import get_logger  # noqa: E402

logger = get_logger("icdev.network.query_router")

# ── Intent classification patterns ──────────────────────────────────────────

_INTENT_PATTERNS: list[tuple[str, list[str]]] = [
    ("redundancy", [
        r"\b(redundan|resilien|failover|single\s*point|SPOF|backup\s*link|HA\b|high\s*avail)",
    ]),
    ("eol", [
        r"\b(end\s*of\s*life|EOL\b|EOS\b|end\s*of\s*support|aging|lifecycle|obsolet|deprecat)",
        r"\b(replace\w*)\b.*\b(device|router|switch|firewall|hardware)\b",
    ]),
    ("blast_radius", [
        r"\b(blast\s*radius)",
        r"(what\s*(if|happens)\s*.*\b(down|fail|crash|die|offline))",
        r"\b(affected|impact)\b.*\b(fail|down)\b",
    ]),
    ("impact", [
        r"\b(add|remove|change|modify|upgrade|migrate|decommission)\b.*\b(link|device|router|switch|circuit)\b",
        r"\b(what\s*if\s*(I|we)\s*(add|remove|change))\b",
    ]),
    ("capacity", [
        r"\b(add\w*)\b.*\b(user|vdi|workstation|endpoint|office|site)\b",
        r"\b(capacity|onboard|growth|scale)\b",
        r"\b(\d+)\s*(more|new|additional)\s*(user|vdi|seat)\b",
    ]),
    ("config", [
        r"\b(config\w*|drift|golden|provision|push\s*config|ansible|running\s*config|startup\s*config)\b",
        r"\b(generate\s*config|self[-\s]provis)\b",
    ]),
    ("compliance", [
        r"\b(STIG|complian|IDS|IPS|segmentat|firewall\s*rule|NIST\s*SC|zone|enclave)\b",
        r"\b(security\s*posture|audit)\b.*\b(network)\b",
    ]),
    ("sla", [
        r"\b(SLA|uptime|availab|nine|99\.\d|MTBF|MTTR|downtime)\b",
    ]),
    ("latency", [
        r"\b(latency|delay|hop|RTT|round\s*trip|jitter|QoS|perform)\b.*\b(network|path|link)\b",
        r"\b(usable|response\s*time)\b.*\b(vdi|voice|video)\b",
    ]),
    ("vendor_risk", [
        r"\b(vendor|concentrat|single\s*vendor|supply\s*chain|diversif)\b",
        r"\b(cisco|juniper|arista|fortinet|palo\s*alto)\b.*\b(CVE|vulnerab|risk)\b",
    ]),
    ("circuit", [
        r"\b(circuit)\b.*\b(contract|expir|renew|renegotiat|cost|manag|review|assess)\b",
        r"\bcircuit\s+contract",
        r"\b(contract)\b.*\b(circuit|carrier|ISP)\b",
        r"\b(carrier|ISP)\b.*\b(expir|renew|renegotiat|contract)\b",
    ]),
    ("cost", [
        r"\b(cost|TCO|budget|expense|spend|price|investment)\b",
        r"\b(\d+)[-\s]year\b.*\b(cost|project|forecast)\b",
    ]),
]


def classify_query(question: str) -> str:
    """Classify a natural language question into an analysis intent."""
    for intent, patterns in _INTENT_PATTERNS:
        for pattern in patterns:
            if re.search(pattern, question, re.IGNORECASE):
                return intent
    return "general"


# ── Parameter extraction ────────────────────────────────────────────────────

def _extract_number(text: str, keywords: list[str]) -> int | None:
    """Extract a number near specified keywords."""
    for kw in keywords:
        match = re.search(rf"(\d+)\s*(?:{kw})", text, re.IGNORECASE)
        if match:
            return int(match.group(1))
        match = re.search(rf"(?:{kw})\s*(\d+)", text, re.IGNORECASE)
        if match:
            return int(match.group(1))
    return None


def _extract_region(text: str) -> str:
    """Extract a region/site name from the question."""
    region_patterns = [
        r"\b(?:in|at|for|near)\s+(?:the\s+)?([A-Z][a-zA-Z\s]+(?:DC|office|site|region|campus|building))",
        r"\b(?:in|at|for)\s+(?:the\s+)?([A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)?)",
        r"\b(Northeast|Southeast|Northwest|Southwest|East\s*Coast|West\s*Coast|Midwest)\b",
        r"\b(NYC|LAX|DFW|ORD|SFO|ATL|BOS|SEA|DEN|MIA|PHX|IAD|IAH)\b",
    ]
    for pattern in region_patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1).strip()
    return ""


def _extract_device_name(text: str) -> str:
    """Extract a device name from the question."""
    patterns = [
        r"(?:device|router|switch|firewall)\s+([A-Za-z0-9][-A-Za-z0-9_.]+)",
        r"([A-Za-z0-9][-A-Za-z0-9_.]+)\s+(?:fails|goes\s+down|crashes|is\s+down)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1)
    return ""


def extract_params(question: str, intent: str) -> dict:
    """Extract parameters from a question based on detected intent."""
    params: dict[str, Any] = {}

    if intent == "capacity":
        params["user_count"] = _extract_number(question, ["user", "vdi", "seat", "workstation", "endpoint"]) or 100
        params["region"] = _extract_region(question)
        services = []
        if re.search(r"\bvdi\b", question, re.IGNORECASE):
            services.append("vdi")
        if re.search(r"\b(teams|zoom|webex)\b", question, re.IGNORECASE):
            services.append("teams")
        if re.search(r"\b(o365|office\s*365|microsoft\s*365)\b", question, re.IGNORECASE):
            services.append("o365")
        params["services"] = services or ["general"]
        params["description"] = question

    elif intent == "eol":
        params["horizon_years"] = _extract_number(question, ["year"]) or 5

    elif intent == "blast_radius":
        params["device_name"] = _extract_device_name(question)

    elif intent == "cost":
        params["years"] = _extract_number(question, ["year"]) or 5

    elif intent in ("latency", "sla"):
        params["region"] = _extract_region(question)

    elif intent == "redundancy":
        params["target_site"] = _extract_region(question)

    elif intent == "circuit":
        params["horizon_months"] = _extract_number(question, ["month"]) or 12

    return params


# ── Query dispatch ───────────────────────────────────────────────────────────

def route_query(topology_id: str, question: str) -> dict:
    """Classify question, extract params, dispatch to intelligence engine."""
    from tools.network import network_intelligence as ni

    intent = classify_query(question)
    params = extract_params(question, intent)

    result: dict[str, Any] = {"intent": intent, "query": question, "params": params}

    if intent == "redundancy":
        result["analysis"] = ni.analyze_redundancy(topology_id, params.get("target_site"))
    elif intent == "eol":
        result["analysis"] = ni.analyze_eol(topology_id, params.get("horizon_years", 5))
    elif intent == "blast_radius":
        device_name = params.get("device_name", "")
        if device_name:
            # Resolve device name to ID
            conn = ni._get_nc_conn()
            row = conn.execute(
                "SELECT node_id FROM ni_devices WHERE topology_id = %s AND label LIKE %s",
                (topology_id, f"%{device_name}%"),
            ).fetchone()
            conn.close()
            if row:
                device_id = row[0] if isinstance(row, tuple) else row["node_id"]
                result["analysis"] = ni.analyze_blast_radius(topology_id, device_id)
            else:
                result["analysis"] = {"error": f"Device '{device_name}' not found"}
        else:
            result["analysis"] = {"error": "Could not identify target device from question"}
    elif intent == "cost":
        result["analysis"] = ni.project_costs(topology_id, params.get("years", 5))
    elif intent == "capacity":
        result["analysis"] = ni.analyze_capacity(topology_id, params)
    elif intent == "config":
        result["analysis"] = ni.manage_configs(topology_id, "diff")
    elif intent == "compliance":
        result["analysis"] = ni.assess_compliance(topology_id)
    elif intent == "sla":
        result["analysis"] = ni.assess_availability(topology_id)
    elif intent == "latency":
        result["analysis"] = ni.assess_latency(topology_id)
    elif intent == "vendor_risk":
        result["analysis"] = ni.assess_vendor_risk(topology_id)
    elif intent == "circuit":
        result["analysis"] = ni.assess_circuits(topology_id, params.get("horizon_months", 12))
    elif intent == "impact":
        result["analysis"] = {"note": "Impact analysis requires structured change list. Use the API endpoint."}
    else:
        # Fall back to existing nl_query engine
        try:
            from tools.network.nl_query import answer_query
            result["analysis"] = answer_query(topology_id, question)
        except (ImportError, Exception) as e:
            result["analysis"] = {"error": f"General query failed: {e}"}

    return result


# ── CLI ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="ICDEV™ Network Intelligence Query Router")
    parser.add_argument("--topology-id", required=True)
    parser.add_argument("--query", required=True, help="Natural language question")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    result = route_query(args.topology_id, args.query)

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        print(f"Intent: {result['intent']}")
        print(f"Params: {result.get('params', {})}")
        print(json.dumps(result.get("analysis", {}), indent=2, default=str))


if __name__ == "__main__":
    main()
