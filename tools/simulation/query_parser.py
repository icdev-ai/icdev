#!/usr/bin/env python3
# CUI // SP-CTI
"""Natural Language Simulation Query Parser.

Parses free-text queries into structured simulation modifications.
Air-gap safe — deterministic regex patterns, no LLM required.

Supports:
  - What-if queries: "what if we add 3 microservices?"
  - Vendor queries: "what happens if we add AWS as a vendor?"
  - Classification changes: "move to IL5"
  - Requirement changes: "add 15 new requirements"
  - Architecture changes: "remove 2 components and add 4 interfaces"
  - Schedule queries: "extend timeline by 3 months"
  - Compound queries: "add AWS, 5 microservices, and move to IL5"

Usage:
    python tools/simulation/query_parser.py --query "add 3 microservices" --json
    python tools/simulation/query_parser.py --query "what if we move to IL5?" --json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

# ---------------------------------------------------------------------------
# Intent classification patterns
# ---------------------------------------------------------------------------

INTENT_PATTERNS = {
    "what_if": re.compile(
        r"\b(what\s+(if|happens?|would happen)|"
        r"suppose|imagine|consider|simulate|"
        r"how would|what('s| is) the impact|"
        r"show me what|run.*(scenario|simulation)|"
        r"if we|if I|let'?s (say|assume))\b",
        re.IGNORECASE,
    ),
    "cascade": re.compile(
        r"\b(cascade|downstream|upstream|depend|"
        r"ripple|propagat|chain.*(effect|reaction)|"
        r"what (systems?|components?|controls?) (are |would be )?(affected|impacted)|"
        r"trace.*(impact|dependency|effect))\b",
        re.IGNORECASE,
    ),
    "compare": re.compile(
        r"\b(compare|versus|vs\.?|side.by.side|"
        r"which is (better|faster|cheaper)|"
        r"difference between|contrast|trade.?off)\b",
        re.IGNORECASE,
    ),
    "readiness": re.compile(
        r"\b(ready|readiness|prepared|capable|"
        r"how (close|far)|can we (handle|support)|"
        r"assessment|gap.analysis)\b",
        re.IGNORECASE,
    ),
}

# ---------------------------------------------------------------------------
# Modification extraction patterns
# ---------------------------------------------------------------------------

# Numbers — handle digits and word forms
_NUM_WORDS = {
    "a": 1,
    "an": 1,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "fifteen": 15,
    "twenty": 20,
    "thirty": 30,
    "fifty": 50,
    "hundred": 100,
    "several": 3,
    "few": 3,
    "couple": 2,
    "many": 10,
    "some": 5,
}


def _extract_number(text: str) -> int:
    """Extract a number from text — digits or word forms."""
    text = text.strip().lower()
    if text.isdigit():
        return int(text)
    return _NUM_WORDS.get(text, 1)


# Component patterns
_COMPONENT_PATTERN = re.compile(
    r"(?:add|introduce|create|deploy|spin up|provision|include)\s+"
    r"(\d+|a|an|one|two|three|four|five|six|seven|eight|nine|ten|several|few|couple)\s+"
    r"(?:new\s+)?"
    r"(microservices?|services?|components?|modules?|containers?|pods?|"
    r"applications?|apps?|apis?|endpoints?|functions?|lambdas?|servers?|"
    r"nodes?|instances?|databases?|caches?|queues?|workers?)",
    re.IGNORECASE,
)

_REMOVE_COMPONENT_PATTERN = re.compile(
    r"(?:remove|delete|decommission|retire|sunset|drop|eliminate)\s+"
    r"(\d+|a|an|one|two|three|four|five|six|seven|eight|nine|ten|several|few|couple)\s+"
    r"(?:existing\s+)?"
    r"(microservices?|services?|components?|modules?|containers?|pods?|"
    r"applications?|apps?|apis?|endpoints?|functions?|lambdas?|servers?|"
    r"nodes?|instances?|databases?|caches?|queues?|workers?)",
    re.IGNORECASE,
)

_INTERFACE_PATTERN = re.compile(
    r"(?:add|create|expose|publish)\s+"
    r"(\d+|a|an|one|two|three|four|five|six|seven|eight|nine|ten|several|few|couple)\s+"
    r"(?:new\s+)?"
    r"(interfaces?|apis?|endpoints?|rest\s*apis?|graphql\s*endpoints?|grpc\s*services?)",
    re.IGNORECASE,
)

# Requirement patterns
_ADD_REQ_PATTERN = re.compile(
    r"(?:add|include|introduce|scope in)\s+"
    r"(\d+|a|an|one|two|three|four|five|six|seven|eight|nine|ten|several|few|many|some)\s+"
    r"(?:new\s+)?"
    r"(requirements?|user\s*stories?|features?|epics?|capabilities?|use\s*cases?)",
    re.IGNORECASE,
)

_REMOVE_REQ_PATTERN = re.compile(
    r"(?:remove|drop|descope|cut|eliminate)\s+"
    r"(\d+|a|an|one|two|three|four|five|six|seven|eight|nine|ten|several|few|many|some)\s+"
    r"(requirements?|user\s*stories?|features?|epics?|capabilities?|use\s*cases?)",
    re.IGNORECASE,
)

# Vendor / supply chain patterns
_ADD_VENDOR_PATTERN = re.compile(
    r"(?:add|onboard|integrate|bring in|include|switch to|adopt|use)\s+"
    r"([\w\s.&-]+?)\s+"
    r"(?:as\s+(?:a\s+)?(?:vendor|supplier|provider|partner)|"
    r"to\s+(?:the\s+)?(?:supply\s*chain|vendor\s*list|tech\s*stack))",
    re.IGNORECASE,
)

_VENDOR_DIRECT_PATTERN = re.compile(
    r"(?:add|integrate|adopt|onboard|switch to|use)\s+"
    r"(AWS|Azure|GCP|Oracle|IBM|Palantir|Splunk|CrowdStrike|Palo Alto|"
    r"ServiceNow|Snowflake|Databricks|MongoDB|Redis|Kafka|Elastic|"
    r"Docker|Kubernetes|Terraform|Ansible|Jenkins|GitLab|GitHub|"
    r"Okta|Auth0|Twilio|SendGrid|Stripe|Datadog|New Relic|"
    r"Google Cloud|Amazon Web Services|Microsoft Azure)",
    re.IGNORECASE,
)

# Classification / impact level changes
_CLASSIFICATION_PATTERN = re.compile(
    r"(?:move|change|upgrade|migrate|transition|reclassify|elevate|shift)\s+"
    r"(?:to|the\s+classification\s+to|the\s+impact\s+level\s+to|"
    r"the\s+system\s+to)\s+"
    r"(IL[2-6]|SECRET|CUI|FOUO|UNCLASSIFIED|public|"
    r"impact\s*level\s*[2-6]|FedRAMP\s*(?:Low|Moderate|High))",
    re.IGNORECASE,
)

# Schedule patterns
_SCHEDULE_EXTEND_PATTERN = re.compile(
    r"(?:extend|delay|push|add|increase)\s+(?:the\s+)?"
    r"(?:timeline|schedule|deadline|delivery|sprint|PI)\s+"
    r"(?:by\s+)?"
    r"(\d+|a|an|one|two|three|four|five|six|seven|eight|nine|ten|several|few)\s+"
    r"(weeks?|months?|sprints?|PIs?|quarters?|days?)",
    re.IGNORECASE,
)

_SCHEDULE_COMPRESS_PATTERN = re.compile(
    r"(?:compress|shorten|reduce|cut|accelerate)\s+(?:the\s+)?"
    r"(?:timeline|schedule|deadline|delivery)\s+"
    r"(?:by\s+)?"
    r"(\d+|a|an|one|two|three|four|five|six|seven|eight|nine|ten|several|few)\s+"
    r"(weeks?|months?|sprints?|PIs?|quarters?|days?)",
    re.IGNORECASE,
)

# Team / staffing patterns
_ADD_STAFF_PATTERN = re.compile(
    r"(?:add|hire|onboard|bring in|staff up|include)\s+"
    r"(\d+|a|an|one|two|three|four|five|six|seven|eight|nine|ten|several|few)\s+"
    r"(?:new\s+)?(?:more\s+)?"
    r"(developers?|engineers?|staff|people|FTEs?|team\s*members?|"
    r"contractors?|resources?|analysts?|architects?|testers?)",
    re.IGNORECASE,
)

# Scope increase/decrease (generic)
_SCOPE_INCREASE_PATTERN = re.compile(
    r"\b(scope\s*creep|scope\s*increase|expand\s*(?:the\s*)?scope|"
    r"grow\s*(?:the\s*)?scope|double\s*(?:the\s*)?scope|"
    r"triple\s*(?:the\s*)?scope)\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Core parser
# ---------------------------------------------------------------------------


def classify_intent(query: str) -> Tuple[str, float]:
    """Classify query intent using heuristic patterns.

    Returns (intent_label, confidence).
    """
    scores = {}
    for intent, pattern in INTENT_PATTERNS.items():
        matches = pattern.findall(query)
        scores[intent] = len(matches)

    if not any(scores.values()):
        # Default to what_if for any modification-like query
        if re.search(r"\b(add|remove|change|move|switch|integrate|upgrade)\b", query, re.IGNORECASE):
            return "what_if", 0.6
        return "what_if", 0.4

    best = max(scores, key=scores.get)
    total = sum(scores.values())
    confidence = scores[best] / total if total > 0 else 0.5
    return best, round(min(confidence, 0.95), 2)


def parse_modifications(query: str) -> Dict[str, Any]:
    """Extract structured modifications from natural language query.

    Returns a dict compatible with simulation_engine.create_scenario(modifications=...).
    """
    mods: Dict[str, Any] = {}
    change_arch: Dict[str, Any] = {}

    # Components
    m = _COMPONENT_PATTERN.search(query)
    if m:
        n = _extract_number(m.group(1))
        change_arch["add_components"] = n

    m = _REMOVE_COMPONENT_PATTERN.search(query)
    if m:
        n = _extract_number(m.group(1))
        change_arch["remove_components"] = n

    # Interfaces
    m = _INTERFACE_PATTERN.search(query)
    if m:
        n = _extract_number(m.group(1))
        change_arch["add_interfaces"] = n

    if change_arch:
        mods["change_architecture"] = change_arch

    # Requirements
    m = _ADD_REQ_PATTERN.search(query)
    if m:
        mods["add_requirements"] = _extract_number(m.group(1))

    m = _REMOVE_REQ_PATTERN.search(query)
    if m:
        mods["remove_requirements"] = _extract_number(m.group(1))

    # Vendors
    vendor_name = None
    m = _ADD_VENDOR_PATTERN.search(query)
    if m:
        vendor_name = m.group(1).strip()
    else:
        m = _VENDOR_DIRECT_PATTERN.search(query)
        if m:
            vendor_name = m.group(1).strip()

    if vendor_name:
        # Determine risk tier based on known vendors
        critical_vendors = {
            "aws",
            "azure",
            "gcp",
            "google cloud",
            "amazon web services",
            "microsoft azure",
            "oracle",
            "palantir",
        }
        risk_tier = "critical" if vendor_name.lower() in critical_vendors else "moderate"
        mods["add_vendor"] = {"name": vendor_name, "scrm_risk_tier": risk_tier}

    # Classification
    m = _CLASSIFICATION_PATTERN.search(query)
    if m:
        raw = m.group(1).strip().upper()
        # Normalize
        il_map = {
            "IMPACT LEVEL 2": "IL2",
            "IMPACT LEVEL 3": "IL3",
            "IMPACT LEVEL 4": "IL4",
            "IMPACT LEVEL 5": "IL5",
            "IMPACT LEVEL 6": "IL6",
            "FEDRAMP LOW": "IL2",
            "FEDRAMP MODERATE": "IL4",
            "FEDRAMP HIGH": "IL5",
        }
        mods["change_classification"] = il_map.get(raw, raw)

    # Schedule
    m = _SCHEDULE_EXTEND_PATTERN.search(query)
    if m:
        n = _extract_number(m.group(1))
        unit = m.group(2).lower().rstrip("s")
        weeks = {
            "week": n,
            "month": n * 4,
            "sprint": n * 2,
            "pi": n * 10,
            "quarter": n * 13,
            "day": max(1, n // 5),
        }.get(unit, n)
        mods["schedule_extension_weeks"] = weeks

    m = _SCHEDULE_COMPRESS_PATTERN.search(query)
    if m:
        n = _extract_number(m.group(1))
        unit = m.group(2).lower().rstrip("s")
        weeks = {
            "week": n,
            "month": n * 4,
            "sprint": n * 2,
            "pi": n * 10,
            "quarter": n * 13,
            "day": max(1, n // 5),
        }.get(unit, n)
        mods["schedule_compression_weeks"] = weeks

    # Staff
    m = _ADD_STAFF_PATTERN.search(query)
    if m:
        mods["add_staff"] = _extract_number(m.group(1))

    # Scope creep
    if _SCOPE_INCREASE_PATTERN.search(query):
        if "add_requirements" not in mods:
            mods["add_requirements"] = 15  # default scope creep estimate
        if "change_architecture" not in mods:
            mods["change_architecture"] = {"add_components": 5}

    return mods


def extract_entities(query: str) -> List[Dict[str, str]]:
    """Extract named entities relevant to simulation from the query."""
    entities = []

    # Vendor names
    m = _VENDOR_DIRECT_PATTERN.search(query)
    if m:
        entities.append({"type": "vendor", "name": m.group(1)})

    # IL levels
    il_matches = re.findall(r"\bIL[2-6]\b", query, re.IGNORECASE)
    for il in il_matches:
        entities.append({"type": "classification", "name": il.upper()})

    # NIST control IDs
    ctrl_matches = re.findall(r"\b([A-Z]{2}-\d+(?:\(\d+\))?)\b", query)
    for ctrl in ctrl_matches:
        entities.append({"type": "control", "name": ctrl})

    # Requirement IDs
    req_matches = re.findall(r"\b(REQ-\d+)\b", query, re.IGNORECASE)
    for req in req_matches:
        entities.append({"type": "requirement", "name": req.upper()})

    return entities


def parse_query(query: str) -> Dict[str, Any]:
    """Full pipeline: classify intent, extract modifications, extract entities.

    Returns:
        {
            "query": str,
            "intent": str,
            "intent_confidence": float,
            "modifications": dict,
            "entities": list,
            "scenario_name": str,
            "scenario_type": str,
        }
    """
    intent, confidence = classify_intent(query)
    modifications = parse_modifications(query)
    entities = extract_entities(query)

    # Auto-generate scenario name from key modifications
    name_parts = []
    arch = modifications.get("change_architecture", {})
    if arch.get("add_components"):
        name_parts.append(f"+{arch['add_components']} components")
    if arch.get("remove_components"):
        name_parts.append(f"-{arch['remove_components']} components")
    if modifications.get("add_requirements"):
        name_parts.append(f"+{modifications['add_requirements']} requirements")
    if modifications.get("add_vendor"):
        name_parts.append(f"+vendor:{modifications['add_vendor']['name']}")
    if modifications.get("change_classification"):
        name_parts.append(f"to {modifications['change_classification']}")
    if modifications.get("schedule_extension_weeks"):
        name_parts.append(f"+{modifications['schedule_extension_weeks']}w schedule")
    if modifications.get("schedule_compression_weeks"):
        name_parts.append(f"-{modifications['schedule_compression_weeks']}w schedule")
    if modifications.get("add_staff"):
        name_parts.append(f"+{modifications['add_staff']} staff")

    scenario_name = ", ".join(name_parts) if name_parts else query[:60]

    # Map intent to scenario_type
    type_map = {
        "what_if": "what_if",
        "cascade": "what_if",
        "compare": "coa_comparison",
        "readiness": "compliance_impact",
    }

    return {
        "query": query,
        "intent": intent,
        "intent_confidence": confidence,
        "modifications": modifications,
        "entities": entities,
        "scenario_name": f"NLQ: {scenario_name}",
        "scenario_type": type_map.get(intent, "what_if"),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description="Natural Language Simulation Query Parser")
    parser.add_argument("--query", required=True, help="Natural language query to parse")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    result = parse_query(args.query)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"Intent:         {result['intent']} ({result['intent_confidence']:.0%})")
        print(f"Scenario Name:  {result['scenario_name']}")
        print(f"Scenario Type:  {result['scenario_type']}")
        print(f"Modifications:  {json.dumps(result['modifications'], indent=2)}")
        print(f"Entities:       {result['entities']}")


if __name__ == "__main__":
    main()
