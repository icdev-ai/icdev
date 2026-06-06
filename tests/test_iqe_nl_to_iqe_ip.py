# CUI // SP-CTI
"""Unit tests for IQE NL-to-IQE IPv4 / CIDR extraction: tools/iqe/nl_to_iqe.py

Covers the deterministic ``_extract_ip`` nlp-extractor path (the IQE analog of a
reputation-list / IP parser), its precedence inside ``_pattern_translate``, the
CIDR octet-alignment rules, the address-field fallback, IPv4 octet validation,
and the guarantee that every emitted IQE string parses under the IQE grammar.
"""
from __future__ import annotations

import pytest

from tools.iqe.nl_to_iqe import (
    _cidr_to_predicate,
    _extract_ip,
    _ip_field,
    _pattern_translate,
    nl_to_iqe,
)
from tools.iqe.parser import parse

COLLECTIONS = ["events", "nodes"]


# ---------------------------------------------------------------------------
# _extract_ip — exact IPv4 equality
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "question",
    [
        "ip is 192.168.1.1",
        "ip equals 192.168.1.1",
        "ip equal to 192.168.1.1",
        "address matches 192.168.1.1",
        "src_ip == 192.168.1.1",
        "source_ip listed as 192.168.1.1",
    ],
)
def test_equality_forms(question):
    out = _extract_ip(question, COLLECTIONS)
    assert out is not None
    assert '== "192.168.1.1"' in out["iqe"]
    assert out["iqe"].startswith("foreach n in events where n.")


def test_equality_resolves_known_field():
    out = _extract_ip("source_ip is 10.20.30.40", COLLECTIONS)
    assert out["iqe"] == 'foreach n in events where n.source_ip == "10.20.30.40" select *'


def test_equality_unknown_field_falls_back_to_ip():
    out = _extract_ip("which entry is 10.20.30.40", COLLECTIONS)
    assert out["iqe"] == 'foreach n in events where n.ip == "10.20.30.40" select *'


# ---------------------------------------------------------------------------
# _extract_ip — CIDR / subnet membership
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "question, op, lit",
    [
        ("source_ip in 10.0.0.0/8", "startswith", '"10."'),
        ("ip within 192.168.0.0/16", "startswith", '"192.168."'),
        ("address in subnet 172.16.5.0/24", "startswith", '"172.16.5."'),
        ("ip belongs to 8.8.8.8/32", "==", '"8.8.8.8"'),
        ("ip part of 203.0.113.0/24", "startswith", '"203.0.113."'),
        ("ip in cidr 10.1.0.0/16", "startswith", '"10.1."'),
    ],
)
def test_cidr_membership_forms(question, op, lit):
    out = _extract_ip(question, COLLECTIONS)
    assert out is not None
    assert f"{op} {lit}" in out["iqe"]


def test_cidr_membership_field_fallback():
    out = _extract_ip("traffic in 10.0.0.0/8", COLLECTIONS)
    assert out["iqe"] == 'foreach n in events where n.ip startswith "10." select *'


def test_non_aligned_cidr_defers_to_llm():
    # /20 is not byte-aligned → no deterministic string predicate → None.
    assert _extract_ip("ip in 10.0.0.0/20", COLLECTIONS) is None


def test_cidr_base_ip_not_read_as_equality():
    # The trailing "/8" must prevent the bare-IP equality branch from firing on
    # the CIDR's base address.
    out = _extract_ip("source_ip in 10.0.0.0/8", COLLECTIONS)
    assert "/8" not in out["iqe"]
    assert 'startswith "10."' in out["iqe"]


# ---------------------------------------------------------------------------
# _cidr_to_predicate — direct unit coverage
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "cidr, expected",
    [
        ("10.0.0.0/8", ("startswith", '"10."')),
        ("192.168.0.0/16", ("startswith", '"192.168."')),
        ("172.16.5.0/24", ("startswith", '"172.16.5."')),
        ("8.8.8.8/32", ("==", '"8.8.8.8"')),
        ("10.0.0.0/20", None),
        ("10.0.0.0/0", None),
    ],
)
def test_cidr_to_predicate(cidr, expected):
    assert _cidr_to_predicate(cidr) == expected


# ---------------------------------------------------------------------------
# _ip_field — field resolution
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("token", ["ip", "src_ip", "destination_ip", "address", "host"])
def test_ip_field_known(token):
    assert _ip_field(token) == token


@pytest.mark.parametrize("token", ["logs", "foo", None, ""])
def test_ip_field_fallback(token):
    assert _ip_field(token) == "ip"


# ---------------------------------------------------------------------------
# IPv4 octet validation — no out-of-range address parses
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "question",
    [
        "ip is 999.1.1.1",
        "ip is 256.0.0.1",
        "ip is 10.0.0",
        "ip is 1.2.3.4.5",
    ],
)
def test_invalid_ipv4_not_extracted(question):
    assert _extract_ip(question, COLLECTIONS) is None


def test_valid_boundary_octets():
    out = _extract_ip("ip is 255.0.255.0", COLLECTIONS)
    assert out is not None
    assert '== "255.0.255.0"' in out["iqe"]


# ---------------------------------------------------------------------------
# Precedence + public API
# ---------------------------------------------------------------------------


def test_pattern_translate_prefers_ip_over_select_all():
    # Without the extractor this would widen to "select *".
    out = _pattern_translate("show all events where ip is 192.168.1.1", COLLECTIONS)
    assert '== "192.168.1.1"' in out["iqe"]


def test_nl_to_iqe_end_to_end_equality():
    out = nl_to_iqe("source_ip is 192.168.1.1", COLLECTIONS)
    assert out["iqe"] == 'foreach n in events where n.source_ip == "192.168.1.1" select *'


def test_no_ip_returns_none():
    assert _extract_ip("show all the routers", COLLECTIONS) is None


# ---------------------------------------------------------------------------
# Grammar validity — every emitted IQE string must parse
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "question",
    [
        "ip is 192.168.1.1",
        "source_ip in 10.0.0.0/8",
        "address within 172.16.0.0/16",
        "ip belongs to 8.8.8.8/32",
        "destination_ip == 203.0.113.7",
    ],
)
def test_emitted_iqe_parses(question):
    out = _extract_ip(question, COLLECTIONS)
    assert out is not None
    parse(out["iqe"])  # raises on malformed query
