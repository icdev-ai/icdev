"""IQE NL-to-IQE translator — converts natural language questions to IQE query strings."""
from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import os
import re
from typing import Any

logger = get_logger(__name__)

_OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
_OLLAMA_MODEL = os.environ.get("OLLAMA_IQE_MODEL", os.environ.get("OLLAMA_TOPO_MODEL", "qwen3.5:latest"))
_LLM_TIMEOUT = int(os.environ.get("OLLAMA_IQE_TIMEOUT", "30"))

_IQE_SYSTEM_PROMPT = """You are an IQE (ICDEV Query Engine) query generator.
IQE syntax:
  foreach <var> in <collection> [where <predicate>] [select <fields>]

Predicates: ==, !=, >, <, >=, <=, contains, startswith, and, or, not
Literals: strings in "quotes", numbers, true, false, null

Examples:
  foreach n in nodes select *
  foreach n in nodes where n.type == "router" select n.label, n.type
  foreach n in nodes where n.label contains "core" select n.label, n.id
  foreach e in edges where e.protocol == "BGP" select *

Given the user question and available collections, output ONLY the IQE query — no explanation, no markdown.
If you cannot generate a valid query, output: foreach n in nodes select *"""


# Comparison-phrase → IQE operator map. Ordered most-specific-first so that
# ">= / <=" phrases are matched before their "> / <" prefixes (e.g. "at least"
# before "less than", "greater than or equal to" before "greater than").
_COMPARISON_PHRASES: list[tuple[str, str]] = [
    (r"greater than or equal to|at least|no less than|>=", ">="),
    (r"less than or equal to|at most|no more than|<=", "<="),
    (r"greater than|more than|larger than|over|above|exceeds|exceeding", ">"),
    (r"less than|fewer than|smaller than|under|below", "<"),
    (r"not equal to|!=", "!="),
    (r"equal to|equals|exactly|is equal to", "=="),
]

# Filler tokens that may sit directly before a comparison phrase — never a field.
_STOPWORD_FIELDS = frozenset(
    {"is", "are", "of", "than", "and", "or", "the", "a", "an", "with",
     "value", "values", "no", "that", "which", "has", "have", "having"}
)

# Date-comparison phrase → IQE operator map for the temporal extractor. Ordered
# most-specific-first so multi-word phrases ("on or after") win over their
# single-word prefixes ("after"/"on"), mirroring the comparison-phrase ordering.
_TEMPORAL_PHRASES: list[tuple[str, str]] = [
    (r"on or after|since|starting from|starting|as of|from", ">="),
    (r"on or before|up to|through|until|by", "<="),
    (r"after|newer than|more recent than", ">"),
    (r"before|older than|prior to|earlier than", "<"),
    (r"on|dated", "=="),
]

# Identifiers accepted as the date field when one sits before a temporal phrase.
# Anything else (e.g. a collection noun like "logs") falls back to the default.
_TEMPORAL_FIELDS = frozenset(
    {"created", "created_at", "modified", "modified_at", "updated",
     "updated_at", "timestamp", "date", "time", "datetime", "logged",
     "occurred", "happened", "last_seen", "first_seen", "start", "end",
     "expires", "expiry", "due", "published"}
)

# Bare ISO-8601 calendar date (YYYY-MM-DD). Date-only is deterministic and
# matches date-partitioned log semantics (the S3 yyyy/mm/dd prefix); time-of-day
# is deliberately left to the LLM path.
_ISO_DATE_RX = r"\d{4}-\d{2}-\d{2}"

# Default field when a temporal phrase has no recognizable date field before it.
_DEFAULT_TEMPORAL_FIELD = "timestamp"


# --- IP / CIDR extraction -------------------------------------------------
# Analog of reputation-list parsers (regex_user_input -> nlp_extractor): pull a
# concrete IPv4 / CIDR predicate out of NL query input instead of widening to a
# bare "select *"/LLM call. Mirrors the comparison/temporal extractors.

# A single, validated IPv4 octet (0-255) — rejects 256+ so "999.1.1.1" never
# parses as an address.
_OCTET = r"(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)"
# Dotted-quad IPv4 literal.
_IPV4_RX = rf"{_OCTET}(?:\.{_OCTET}){{3}}"
# CIDR block: IPv4 plus a /0-32 prefix length.
_CIDR_RX = rf"{_IPV4_RX}/(?:3[0-2]|[12]?\d)"

# Phrases asserting an exact IP match (field <phrase> <ip>).
_IP_EQ_PHRASES = r"is in the list|listed as|equal to|equals|matches|matching|is|are|=="
# Phrases asserting subnet/CIDR membership (field <phrase> <cidr>).
_IP_MEMBERSHIP_PHRASES = (
    r"belongs to|part of|in the range|in subnet|in cidr|within|inside|under|in"
)

# Identifiers accepted as the address field when one sits before an IP phrase.
_IP_FIELDS = frozenset(
    {"ip", "ip_address", "ipaddress", "address", "addr", "host", "hostname",
     "src", "src_ip", "source", "source_ip", "dst", "dst_ip", "dest",
     "destination", "destination_ip", "client_ip", "remote_ip", "peer_ip",
     "peer", "gateway", "subnet", "network", "cidr"}
)

# Default field when an IP phrase has no recognizable address field before it.
_DEFAULT_IP_FIELD = "ip"


def _ip_field(token: str | None) -> str:
    """Resolve the address field for an IP predicate from an optional token."""
    if token and token in _IP_FIELDS:
        return token
    return _DEFAULT_IP_FIELD


def _cidr_to_predicate(cidr: str) -> tuple[str, str] | None:
    """Translate an octet-aligned CIDR block into an IQE (op, literal) pair.

    Only byte-boundary prefixes (/8, /16, /24) and a host route (/32) map to a
    deterministic predicate: the aligned ones become a ``startswith`` on the
    network octets (the trailing dot prevents 10.x matching 100.x), and /32
    becomes an exact ``==``. Non-aligned prefixes (e.g. /20) have no simple
    string form and are returned as ``None`` so the caller defers to the LLM.
    """
    ip, _, prefix_s = cidr.partition("/")
    prefix = int(prefix_s)
    if prefix == 32:
        return ("==", f'"{ip}"')
    if prefix in (8, 16, 24):
        keep = prefix // 8
        network = ".".join(ip.split(".")[:keep]) + "."
        return ("startswith", f'"{network}"')
    return None


def _temporal_field(token: str | None) -> str:
    """Resolve the date field for a temporal predicate from an optional token."""
    if token and token in _TEMPORAL_FIELDS:
        return token
    return _DEFAULT_TEMPORAL_FIELD


def _match_collection(q: str, collections: list[str], default: str) -> str:
    """Pick the collection whose (singularized) name appears in the question."""
    for c in collections:
        root = c.lower().rstrip("s")
        if root and root in q:
            return c
    return default


def _extract_comparison(question: str, collections: list[str]) -> dict[str, Any] | None:
    """Extract a numeric comparison predicate from natural language.

    Handles the unambiguous ``<field> <comparison-phrase> <number>`` word order
    (e.g. "weight greater than 10", "degree at least 3", "score under 5"). The
    inverted form ("more than 5 connections") is ambiguous about which token is
    the field, so it is deliberately left to the LLM translation path.

    Returns an IQE/explanation dict, or ``None`` when no predicate is found.
    """
    q = question.lower().strip()
    default_coll = collections[0] if collections else "nodes"

    for phrase_rx, op in _COMPARISON_PHRASES:
        m = re.search(
            rf"\b([a-z_][a-z0-9_]*)\s+(?:is\s+|are\s+|of\s+)?(?:{phrase_rx})\s+"
            r"(\d+(?:\.\d+)?)\b",
            q,
        )
        if not m:
            continue
        field = m.group(1)
        if field in _STOPWORD_FIELDS:
            continue
        raw = m.group(2)
        val = raw if "." in raw else str(int(raw))
        coll = _match_collection(q, collections, default_coll)
        iqe = f"foreach n in {coll} where n.{field} {op} {val} select *"
        return {"iqe": iqe, "explanation": f"Filter {coll} where {field} {op} {val}"}

    return None


def _extract_temporal(question: str, collections: list[str]) -> dict[str, Any] | None:
    """Extract a date-range predicate from natural language.

    Handles two unambiguous, deterministic forms anchored on an ISO calendar
    date (``YYYY-MM-DD``):

    * ``[<field>] between <date> and <date>`` → ``>=``/``<=`` range
    * ``[<field>] <temporal-phrase> <date>``  → single comparison
      (e.g. "created after 2026-01-01", "logs since 2026-03-01",
      "events before 2026-02-15", "records on 2026-01-10").

    The optional leading token names the date field when it is a recognized
    field (see ``_TEMPORAL_FIELDS``); otherwise the predicate falls back to
    ``timestamp``. Relative dates ("last 7 days") and time-of-day are left to
    the LLM path. Returns an IQE/explanation dict, or ``None``.
    """
    q = question.lower().strip()
    default_coll = collections[0] if collections else "nodes"
    coll = _match_collection(q, collections, default_coll)

    # Range form first (most specific) — "between D1 and D2".
    m = re.search(
        rf"\b(?:([a-z_][a-z0-9_]*)\s+)?between\s+({_ISO_DATE_RX})\s+and\s+({_ISO_DATE_RX})\b",
        q,
    )
    if m:
        field = _temporal_field(m.group(1))
        d1, d2 = m.group(2), m.group(3)
        iqe = (
            f'foreach n in {coll} where n.{field} >= "{d1}" '
            f'and n.{field} <= "{d2}" select *'
        )
        return {
            "iqe": iqe,
            "explanation": f"Filter {coll} where {field} between {d1} and {d2}",
        }

    for phrase_rx, op in _TEMPORAL_PHRASES:
        m = re.search(
            rf"\b(?:([a-z_][a-z0-9_]*)\s+)?(?:{phrase_rx})\s+({_ISO_DATE_RX})\b",
            q,
        )
        if not m:
            continue
        field = _temporal_field(m.group(1))
        d = m.group(2)
        iqe = f'foreach n in {coll} where n.{field} {op} "{d}" select *'
        return {"iqe": iqe, "explanation": f"Filter {coll} where {field} {op} {d}"}

    return None


def _extract_ip(question: str, collections: list[str]) -> dict[str, Any] | None:
    """Extract an IPv4 address / CIDR-subnet predicate from natural language.

    This is the IQE analog of a reputation-list parser: rather than collapse an
    address-filtered question to ``select *``, recognize two unambiguous forms
    anchored on a validated IPv4 literal:

    * ``[<field>] <membership-phrase> <CIDR>`` → octet-aligned subnet match
      (e.g. "source_ip in 10.0.0.0/8" → ``startswith "10."``); /32 collapses to
      an exact match. Non-byte-aligned prefixes (/20) are left to the LLM.
    * ``[<field>] <equality-phrase> <IPv4>`` → exact match
      (e.g. "ip is 192.168.1.1" → ``== "192.168.1.1"``).

    The optional leading token names the address field when recognized (see
    ``_IP_FIELDS``); otherwise the predicate falls back to ``ip``. Returns an
    IQE/explanation dict, or ``None`` when no predicate is found.
    """
    q = question.lower().strip()
    default_coll = collections[0] if collections else "nodes"
    coll = _match_collection(q, collections, default_coll)

    # CIDR membership first (most specific — carries a prefix length).
    m = re.search(
        rf"\b(?:([a-z_][a-z0-9_]*)\s+)?(?:{_IP_MEMBERSHIP_PHRASES})\s+({_CIDR_RX})\b",
        q,
    )
    if m:
        pred = _cidr_to_predicate(m.group(2))
        if pred:  # None → non-aligned prefix, defer to LLM
            field = _ip_field(m.group(1))
            op, lit = pred
            iqe = f"foreach n in {coll} where n.{field} {op} {lit} select *"
            return {
                "iqe": iqe,
                "explanation": f"Filter {coll} where {field} {op} {lit} (subnet {m.group(2)})",
            }

    # Exact IPv4 equality — the negative lookahead skips a CIDR's base address
    # so "in 10.0.0.0/8" is not also read as "== 10.0.0.0".
    m = re.search(
        rf"\b(?:([a-z_][a-z0-9_]*)\s+)?(?:{_IP_EQ_PHRASES})\s+({_IPV4_RX})(?![\d./])",
        q,
    )
    if m:
        field = _ip_field(m.group(1))
        ip = m.group(2)
        iqe = f'foreach n in {coll} where n.{field} == "{ip}" select *'
        return {"iqe": iqe, "explanation": f'Filter {coll} where {field} == "{ip}"'}

    return None


def _pattern_translate(question: str, collections: list[str]) -> dict[str, Any] | None:
    """Try simple pattern-based translation before calling LLM."""
    q = question.lower().strip()

    default_coll = collections[0] if collections else "nodes"

    # IP / CIDR predicates are the most specific (anchored on a validated IPv4
    # literal), so they run first — otherwise an address-filtered question would
    # be silently widened to "select *".
    ip_pred = _extract_ip(question, collections)
    if ip_pred:
        return ip_pred

    # Temporal/date-range predicates take precedence (anchored on a full ISO
    # date, so they are the most specific) — otherwise a dated question would be
    # silently widened to "select *".
    temporal = _extract_temporal(question, collections)
    if temporal:
        return temporal

    # Numeric comparison predicates take precedence over "show all" so that a
    # filtered question is not silently widened to "select *".
    comparison = _extract_comparison(question, collections)
    if comparison:
        return comparison

    # "show all X" / "list all X" / "get all X"
    m = re.search(r"\b(?:show|list|get|display)\s+all\s+(\w+)", q)
    if m:
        ctype = m.group(1).rstrip("s")  # rough singularize
        # find matching collection
        coll = next((c for c in collections if ctype in c.lower()), default_coll)
        iqe = f"foreach n in {coll} select *"
        return {"iqe": iqe, "explanation": f"Select all records from {coll}"}

    # "how many X" / "count X"
    m = re.search(r"\b(?:how many|count)\s+(\w+)", q)
    if m:
        ctype = m.group(1).rstrip("s")
        coll = next((c for c in collections if ctype in c.lower()), default_coll)
        iqe = f"foreach n in {coll} select *"
        return {"iqe": iqe, "explanation": f"Count all records in {coll} (row_count in response)"}

    # "X of type Y" / "X where type is Y"
    m = re.search(r"\b(\w+)\s+(?:of\s+)?type\s+['\"]?(\w+)['\"]?", q)
    if m:
        coll = next((c for c in collections if m.group(1).rstrip("s") in c.lower()), default_coll)
        dtype = m.group(2)
        iqe = f'foreach n in {coll} where n.type == "{dtype}" select *'
        return {"iqe": iqe, "explanation": f'Filter {coll} by type == "{dtype}"'}

    # "X named/labeled/called Y"
    m = re.search(r'\b(?:named|labeled|called)\s+["\']?([A-Za-z0-9_\-]+)["\']?', q)
    if m:
        label = m.group(1)
        iqe = f'foreach n in {default_coll} where n.label contains "{label}" select *'
        return {"iqe": iqe, "explanation": f'Filter {default_coll} where label contains "{label}"'}

    return None


def _llm_translate(question: str, collections: list[str]) -> dict[str, Any]:
    """Use Ollama to translate question → IQE query."""
    try:
        from tools.http.client import request as _http_request
    except ImportError:
        return _fallback(collections)

    coll_hint = ", ".join(collections) if collections else "nodes"
    user_msg = f"Available collections: {coll_hint}\n\nQuestion: {question}"
    payload = {
        "model": _OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": _IQE_SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        "stream": False,
        "options": {"temperature": 0.0, "num_predict": 128, "think": False},
    }
    try:
        resp = _http_request("POST", f"{_OLLAMA_HOST}/api/chat", json=payload, timeout=_LLM_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        raw = (data.get("message") or {}).get("content", "").strip()
        raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
        # Strip markdown code fences if present
        raw = re.sub(r"^```[a-z]*\n?", "", raw).rstrip("`").strip()
        if raw.lower().startswith("foreach"):
            return {"iqe": raw, "explanation": f"LLM-generated IQE for: {question}"}
    except Exception as exc:
        logger.debug("IQE LLM translation failed: %s", exc)

    return _fallback(collections)


def _fallback(collections: list[str]) -> dict[str, Any]:
    coll = collections[0] if collections else "nodes"
    return {"iqe": f"foreach n in {coll} select *", "explanation": "Default: select all from primary collection"}


def nl_to_iqe(question: str, collections: list[str]) -> dict[str, Any]:
    """Translate a natural language *question* to an IQE query string.

    Args:
        question:    Free-form natural language question.
        collections: Available collection names (e.g. ["nodes", "edges"]).

    Returns:
        dict with keys:
            iqe (str)         — the IQE query string
            explanation (str) — human-readable description of what it does
    """
    question = (question or "").strip()
    if not question:
        return _fallback(collections)

    result = _pattern_translate(question, collections)
    if result:
        return result

    return _llm_translate(question, collections)
