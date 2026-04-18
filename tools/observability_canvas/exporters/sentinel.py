# CUI // SP-CTI
"""Microsoft Sentinel KQL export adapter for ODC Digital Twin Sigma rules.

Converts a single Sigma rule YAML string to one Azure Sentinel KQL query string.
Field modifiers (contains, gt, lt, cidr) are translated deterministically — no LLM.

Public API:
    sigma_to_kql(rule_yaml: str) -> str     — one rule → one KQL query string
    batch_to_kql(rules: list[str]) -> str   — many rules → KQL queries separated by blank lines
"""

from __future__ import annotations

import yaml

# ---------------------------------------------------------------------------
# Sigma field name → Microsoft Sentinel / Azure Monitor schema field name
# ---------------------------------------------------------------------------
_FIELD_MAP: dict[str, str] = {
    "CommandLine":      "CommandLine",
    "EventID":          "EventID",
    "LogonType":        "LogonType",
    "src_ip":           "SrcIpAddr",
    "dst_ip":           "DstIpAddr",
    "dst_port":         "DstPortNumber",
    "bytes_in":         "SrcBytes",
    "bytes_out":        "DstBytes",
    "Message":          "EventMessage",
    "eventName":        "OperationName",
    "responseElements": "ResponseElements",
    "errorCode":        "ResultType",
    "Keywords":         "Keywords",
    "Status":           "EventResult",
}

# Sentinel table by Sigma logsource category
_TABLE_MAP: dict[str, str] = {
    "authentication":    "SecurityEvent",
    "process_creation":  "DeviceProcessEvents",
    "network_connection": "DeviceNetworkEvents",
    "dns":               "DnsEvents",
    "file_event":        "DeviceFileEvents",
}
_DEFAULT_TABLE = "CommonSecurityLog"


# ---------------------------------------------------------------------------
# Sigma modifier → KQL expression
# ---------------------------------------------------------------------------

def _translate_term(key: str, value: object) -> str:
    """Translate one Sigma detection key-value pair to a KQL filter expression."""
    parts = key.split("|")
    raw_field = parts[0]
    modifiers = {m.lower() for m in parts[1:]}
    field = _FIELD_MAP.get(raw_field, raw_field)

    values = value if isinstance(value, list) else [value]

    if "cidr" in modifiers:
        return f"ipv4_is_in_range({field}, '{values[0]}')"

    if "gt" in modifiers:
        return f"{field} > {values[0]}"

    if "lt" in modifiers:
        return f"{field} < {values[0]}"

    if "contains" in modifiers:
        clauses = [f'{field} contains "{v}"' for v in values]
        return f"({' or '.join(clauses)})" if len(clauses) > 1 else clauses[0]

    # plain equality — list becomes or
    clauses = [f'{field} == "{v}"' if isinstance(v, str) else f"{field} == {v}" for v in values]
    return f"({' or '.join(clauses)})" if len(clauses) > 1 else clauses[0]


def _selection_to_kql(selection: dict) -> str:
    """Join all Sigma selection key-value pairs into one KQL where clause body."""
    terms = [_translate_term(k, v) for k, v in selection.items()]
    return "\n    and ".join(terms) if terms else "true"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def sigma_to_kql(rule_yaml: str) -> str:
    """Convert one Sigma rule YAML string to one Azure Sentinel KQL query string.

    Args:
        rule_yaml: Well-formed Sigma YAML string.

    Returns:
        KQL string: comment header, table, where clause, project, and extend.

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

    logsource = rule.get("logsource") or {}
    category = logsource.get("category", "")
    table = _TABLE_MAP.get(category, _DEFAULT_TABLE)

    title = rule.get("title", "unnamed")
    tid_tag = next(
        (t for t in rule.get("tags", []) if t.startswith("attack.t")), ""
    )
    level = rule.get("level", "unknown")

    comment_parts = [f"// {title}"]
    if tid_tag:
        comment_parts.append(f"// MITRE ATT&CK: {tid_tag.upper()}")
    comment_parts.append(f"// Severity: {level}")
    comment = "\n".join(comment_parts)

    where_clause = _selection_to_kql(selection)
    return (
        f"{comment}\n"
        f"{table}\n"
        f"| where {where_clause}"
    )


def batch_to_kql(rules: list[str]) -> str:
    """Convert multiple Sigma rule YAMLs to KQL queries separated by blank lines.

    Args:
        rules: List of Sigma YAML strings.

    Returns:
        KQL queries joined by double newlines, one per valid rule.
    """
    if not rules:
        return '// ICDEV ODC Twin — no rules generated\nCommonSecurityLog\n| where true'

    results: list[str] = []
    for raw in rules:
        try:
            results.append(sigma_to_kql(raw))
        except (ValueError, yaml.YAMLError):
            continue

    return "\n\n".join(results) if results else '// ICDEV ODC Twin — no valid rules\nCommonSecurityLog\n| where true'
