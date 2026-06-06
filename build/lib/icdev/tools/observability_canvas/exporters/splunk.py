# CUI // SP-CTI
"""Splunk SPL export adapter for ODC Digital Twin Sigma rules.

Converts a single Sigma rule YAML string to one Splunk SPL search stanza.
Field modifiers (contains, gt, lt, cidr) are translated deterministically — no LLM.

Public API:
    sigma_to_spl(rule_yaml: str) -> str    — one rule → one SPL stanza
    batch_to_spl(rules: list[str]) -> str  — many rules → one SPL script
"""

from __future__ import annotations

import yaml

# ---------------------------------------------------------------------------
# Sigma field name → Splunk CIM field name
# ---------------------------------------------------------------------------
_FIELD_MAP: dict[str, str] = {
    "CommandLine":      "process.command_line",
    "EventID":          "EventCode",
    "LogonType":        "Logon_Type",
    "src_ip":           "src",
    "dst_ip":           "dest",
    "dst_port":         "dest_port",
    "bytes_in":         "bytes_in",
    "bytes_out":        "bytes_out",
    "Message":          "message",
    "eventName":        "eventName",
    "responseElements": "responseElements",
    "errorCode":        "errorCode",
    "Keywords":         "Keywords",
    "Status":           "Status",
}


# ---------------------------------------------------------------------------
# Sigma modifier → SPL translation
# ---------------------------------------------------------------------------

def _translate_term(key: str, value: object) -> str:
    """Translate one Sigma detection key-value pair to a SPL search term.

    Handles Sigma field modifiers:
      - |contains        → wildcard *value*
      - |contains|any    → OR of wildcard terms
      - |gt              → field > value
      - |lt              → field < value
      - |cidr            → cidrmatch("cidr", field)
      - (plain)          → field=value or OR for lists
    """
    parts = key.split("|")
    raw_field = parts[0]
    modifiers = {m.lower() for m in parts[1:]}
    field = _FIELD_MAP.get(raw_field, raw_field)

    values = value if isinstance(value, list) else [value]

    if "cidr" in modifiers:
        return f'cidrmatch("{values[0]}", {field})'

    if "gt" in modifiers:
        return f"{field} > {values[0]}"

    if "lt" in modifiers:
        return f"{field} < {values[0]}"

    if "contains" in modifiers:
        clauses = [f'{field}="*{v}*"' for v in values]
        return f"({' OR '.join(clauses)})" if len(clauses) > 1 else clauses[0]

    # plain equality — list becomes OR
    clauses = [f"{field}={v!r}" for v in values]
    return f"({' OR '.join(clauses)})" if len(clauses) > 1 else clauses[0]


def _selection_to_spl(selection: dict) -> str:
    """Join all Sigma selection key-value pairs into SPL search terms."""
    terms = [_translate_term(k, v) for k, v in selection.items()]
    return " ".join(terms) if terms else "*"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def sigma_to_spl(rule_yaml: str) -> str:
    """Convert one Sigma rule YAML string to one Splunk SPL search stanza.

    Args:
        rule_yaml: Well-formed Sigma YAML string.

    Returns:
        SPL string: comment header followed by a ``| search <terms>`` line.

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

    title = rule.get("title", "unnamed")
    tid_tag = next(
        (t for t in rule.get("tags", []) if t.startswith("attack.t")), ""
    )
    comment = f"/* {title}" + (f" [{tid_tag}]" if tid_tag else "") + " */"
    search_terms = _selection_to_spl(selection)
    return f"{comment}\n| search {search_terms}"


def batch_to_spl(rules: list[str]) -> str:
    """Convert multiple Sigma rule YAMLs to one SPL script (one stanza per rule).

    Args:
        rules: List of Sigma YAML strings.

    Returns:
        SPL script with stanzas separated by blank lines.
    """
    if not rules:
        return '| comment "ICDEV ODC Twin — no rules generated"'

    stanzas: list[str] = []
    for raw in rules:
        try:
            stanzas.append(sigma_to_spl(raw))
        except (ValueError, yaml.YAMLError):
            continue

    return "\n\n".join(stanzas) if stanzas else '| comment "ICDEV ODC Twin — no valid rules"'
