# CUI // SP-CTI
"""Elasticsearch Query DSL export adapter for ODC Digital Twin Sigma rules.

Converts a single Sigma rule YAML string to one Elasticsearch Query DSL JSON blob.
Field modifiers (contains, gt, lt, cidr) are translated deterministically — no LLM.

Public API:
    sigma_to_eql(rule_yaml: str) -> str     — one rule → one ES Query DSL JSON string
    batch_to_eql(rules: list[str]) -> str   — many rules → newline-delimited JSON strings
"""

from __future__ import annotations

import json

import yaml

# ---------------------------------------------------------------------------
# Sigma field name → Elasticsearch ECS field name
# ---------------------------------------------------------------------------
_FIELD_MAP: dict[str, str] = {
    "CommandLine":      "process.command_line",
    "EventID":          "winlog.event_id",
    "LogonType":        "winlog.event_data.LogonType",
    "src_ip":           "source.ip",
    "dst_ip":           "destination.ip",
    "dst_port":         "destination.port",
    "bytes_in":         "source.bytes",
    "bytes_out":        "destination.bytes",
    "Message":          "message",
    "eventName":        "event.action",
    "responseElements": "cloud.account.id",
    "errorCode":        "error.code",
    "Keywords":         "winlog.keywords",
    "Status":           "event.outcome",
}


# ---------------------------------------------------------------------------
# Sigma modifier → Elasticsearch Query DSL clause
# ---------------------------------------------------------------------------

def _translate_term(key: str, value: object) -> dict:
    """Translate one Sigma detection key-value pair to an ES Query DSL clause dict."""
    parts = key.split("|")
    raw_field = parts[0]
    modifiers = {m.lower() for m in parts[1:]}
    field = _FIELD_MAP.get(raw_field, raw_field)

    values = value if isinstance(value, list) else [value]

    if "cidr" in modifiers:
        return {"term": {field: {"value": values[0]}}}

    if "gt" in modifiers:
        return {"range": {field: {"gt": values[0]}}}

    if "lt" in modifiers:
        return {"range": {field: {"lt": values[0]}}}

    if "contains" in modifiers:
        clauses = [{"wildcard": {field: {"value": f"*{v}*"}}} for v in values]
        return clauses[0] if len(clauses) == 1 else {"bool": {"should": clauses, "minimum_should_match": 1}}

    # plain equality
    clauses = [{"term": {field: {"value": v}}} for v in values]
    return clauses[0] if len(clauses) == 1 else {"bool": {"should": clauses, "minimum_should_match": 1}}


def _selection_to_query(selection: dict) -> dict:
    """Build the ES bool/must query from all Sigma selection key-value pairs."""
    clauses = [_translate_term(k, v) for k, v in selection.items()]
    if not clauses:
        return {"match_all": {}}
    if len(clauses) == 1:
        return clauses[0]
    return {"bool": {"must": clauses}}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def sigma_to_eql(rule_yaml: str) -> str:
    """Convert one Sigma rule YAML string to one Elasticsearch Query DSL JSON string.

    Args:
        rule_yaml: Well-formed Sigma YAML string.

    Returns:
        Pretty-printed JSON string containing ``{"query": {...}}`` plus rule metadata.

    Raises:
        ValueError: If rule_yaml is not a valid YAML mapping or has no detection block.
    """
    rule = yaml.safe_load(rule_yaml)
    if not isinstance(rule, dict):
        raise ValueError("rule_yaml must parse to a YAML mapping")

    detection = rule.get("detection") or {}
    selection = detection.get("selection") or {}
    if not isinstance(selection, dict):
        raise ValueError("detection.selection must be a mapping")

    tid_tag = next(
        (t for t in rule.get("tags", []) if t.startswith("attack.t")), None
    )
    query = _selection_to_query(selection)
    doc = {
        "_meta": {
            "title": rule.get("title", "unnamed"),
            "level": rule.get("level", "unknown"),
            "mitre_attack": tid_tag,
        },
        "query": query,
    }
    return json.dumps(doc, indent=2)


def batch_to_eql(rules: list[str]) -> str:
    """Convert multiple Sigma rule YAMLs to newline-delimited ES Query DSL JSON strings.

    Args:
        rules: List of Sigma YAML strings.

    Returns:
        Newline-delimited JSON blobs, one per valid rule.
    """
    if not rules:
        return json.dumps({"_meta": {"note": "ICDEV ODC Twin — no rules generated"}, "query": {"match_all": {}}})

    results: list[str] = []
    for raw in rules:
        try:
            results.append(sigma_to_eql(raw))
        except (ValueError, yaml.YAMLError):
            continue

    return "\n".join(results) if results else json.dumps({"_meta": {"note": "ICDEV ODC Twin — no valid rules"}, "query": {"match_all": {}}})
