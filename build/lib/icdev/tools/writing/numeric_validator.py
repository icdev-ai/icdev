# CUI // SP-CTI
"""WriteGuard Numeric Validator (WG 16).

Deterministic numeric/symbol consistency checker for technical documents.

Validates:
    - IPv4/IPv6 CIDR syntax + overlap detection
    - RFC 1918 vs public address mixing
    - VLAN ID range (1-4094)
    - ASN public/private distinction
    - Percent-sum totals (~100%)
    - Year-subtotal vs total reconciliation
    - Currency symbol consistency
    - K/M/B vs spelled-out magnitude consistency
    - Date and time format mixing

Usage:
    from tools.writing.numeric_validator import validate_numerics

    result = validate_numerics(text)
    # result = {issues: [...], score: 0-100, summary: {...}}

Scanner-tier only (zero LLM). Stdlib only.

CUI // SP-CTI
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple


# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

_IPV4_CIDR_RE = re.compile(r"\b(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})/(\d{1,2})\b")
_IPV6_CIDR_RE = re.compile(r"\b([0-9a-fA-F:]+:+[0-9a-fA-F:]*)/(\d{1,3})\b")
_VLAN_RE = re.compile(r"\bVLAN\s+(\d+)\b", re.IGNORECASE)
_ASN_RE = re.compile(r"\b(?:AS|ASN\s+)(\d+)\b")
_PERCENT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%")
_YEAR_TOTAL_RE = re.compile(
    r"Year\s*1[^$]*\$(\d[\d,\.]*)[^Y]*Year\s*2[^$]*\$(\d[\d,\.]*)[^Y]*Year\s*3[^$]*\$(\d[\d,\.]*)[^T]*Total[^$]*\$(\d[\d,\.]*)",
    re.IGNORECASE | re.DOTALL,
)
_ISO_DATE_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_SLASH_DATE_RE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{2,4})\b")
_TIME_12H_RE = re.compile(r"\b(\d{1,2})(?::\d{2})?\s*(?:am|pm|AM|PM)\b")
_TIME_24H_RE = re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)\b")
_CURRENCY_SYMS = {"$": "USD", "€": "EUR", "£": "GBP", "¥": "JPY"}


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------


def _check_ipv4_cidrs(text: str) -> Tuple[List[Dict[str, Any]], List[ipaddress.IPv4Network]]:
    issues: List[Dict[str, Any]] = []
    nets: List[ipaddress.IPv4Network] = []
    for m in _IPV4_CIDR_RE.finditer(text):
        addr, prefix = m.group(1), int(m.group(2))
        octets = [int(o) for o in addr.split(".")]
        bad_octet = any(o > 255 for o in octets)
        bad_prefix = prefix > 32
        if bad_octet or bad_prefix:
            issues.append({
                "category": "cidr_invalid",
                "match": m.group(0),
                "reason": "octet>255" if bad_octet else "prefix>32",
            })
            continue
        try:
            net = ipaddress.ip_network(f"{addr}/{prefix}", strict=False)
            nets.append(net)
        except ValueError as e:
            issues.append({"category": "cidr_invalid", "match": m.group(0), "reason": str(e)})
    return issues, nets


def _check_cidr_overlaps(nets: List[ipaddress.IPv4Network]) -> List[Dict[str, Any]]:
    issues: List[Dict[str, Any]] = []
    for i, a in enumerate(nets):
        for b in nets[i + 1:]:
            if a == b:
                continue
            if a.overlaps(b):
                issues.append({
                    "category": "cidr_overlap",
                    "match": f"{a} overlaps {b}",
                })
    return issues


def _check_ipv6_cidrs(text: str) -> List[Dict[str, Any]]:
    issues: List[Dict[str, Any]] = []
    for m in _IPV6_CIDR_RE.finditer(text):
        addr, prefix = m.group(1), int(m.group(2))
        if prefix > 128:
            issues.append({
                "category": "ipv6_cidr_invalid",
                "match": m.group(0),
                "reason": "prefix>128",
            })
            continue
        try:
            ipaddress.IPv6Network(f"{addr}/{prefix}", strict=False)
        except ValueError:
            # May be an IPv4 false-positive — ignore silently
            pass
    return issues


def _check_rfc1918(text: str, nets: List[ipaddress.IPv4Network]) -> List[Dict[str, Any]]:
    if not nets:
        return []
    private = [n for n in nets if n.is_private]
    public = [n for n in nets if not n.is_private and not n.is_loopback and not n.is_link_local]
    if private and public:
        return [{
            "category": "rfc1918_mix",
            "match": f"Document mixes private ({len(private)}) and public ({len(public)}) address space",
        }]
    return []


def _check_vlans(text: str) -> List[Dict[str, Any]]:
    issues: List[Dict[str, Any]] = []
    for m in _VLAN_RE.finditer(text):
        vid = int(m.group(1))
        if vid < 1 or vid > 4094:
            issues.append({
                "category": "vlan_out_of_range",
                "match": m.group(0),
                "reason": f"VLAN {vid} not in 1-4094",
            })
    return issues


def _check_asns(text: str) -> List[Dict[str, Any]]:
    issues: List[Dict[str, Any]] = []
    public_keywords = ("public internet", "global bgp", "internet peering", "public asn")
    lower = text.lower()
    is_public_context = any(k in lower for k in public_keywords)
    for m in _ASN_RE.finditer(text):
        asn = int(m.group(1))
        if asn > 4_294_967_295:
            issues.append({
                "category": "asn_invalid",
                "match": m.group(0),
                "reason": "ASN exceeds 32-bit max",
            })
            continue
        is_private = 64512 <= asn <= 65534 or 4_200_000_000 <= asn <= 4_294_967_294
        if is_private and is_public_context:
            issues.append({
                "category": "asn_private_in_public_context",
                "match": m.group(0),
                "reason": f"Private ASN {asn} referenced in public-internet context",
            })
    return issues


def _check_percent_sums(text: str) -> List[Dict[str, Any]]:
    """Find groups of 3+ percent values within a small window and flag sums close to but not 100."""
    issues: List[Dict[str, Any]] = []
    lines = text.splitlines()
    window: List[float] = []
    for line in lines:
        vals = [float(m.group(1)) for m in _PERCENT_RE.finditer(line)]
        if vals:
            window.extend(vals)
        else:
            if len(window) >= 3:
                s = sum(window)
                if abs(s - 100) < 5 and s != 100:
                    issues.append({
                        "category": "percent_sum_off",
                        "match": f"values={window} sum={s}",
                        "reason": f"Sum {s}% is close to but not 100%",
                    })
            window = []
    if len(window) >= 3:
        s = sum(window)
        if abs(s - 100) < 5 and s != 100:
            issues.append({
                "category": "percent_sum_off",
                "match": f"values={window} sum={s}",
                "reason": f"Sum {s}% is close to but not 100%",
            })
    return issues


def _check_year_totals(text: str) -> List[Dict[str, Any]]:
    issues: List[Dict[str, Any]] = []
    for m in _YEAR_TOTAL_RE.finditer(text):
        try:
            parts = [float(p.replace(",", "")) for p in m.groups()]
        except ValueError:
            continue
        y1, y2, y3, total = parts
        expected = y1 + y2 + y3
        if abs(expected - total) > 0.01:
            issues.append({
                "category": "year_total_mismatch",
                "match": m.group(0)[:120],
                "reason": f"Year sum {expected} != stated total {total}",
            })
    return issues


def _check_currency(text: str) -> List[Dict[str, Any]]:
    found = {sym: text.count(sym) for sym in _CURRENCY_SYMS}
    present = [s for s, n in found.items() if n > 0]
    if len(present) > 1:
        return [{
            "category": "currency_mixed",
            "match": ",".join(present),
            "reason": "Multiple currency symbols used without explicit notation",
        }]
    return []


def _check_magnitude_consistency(text: str) -> List[Dict[str, Any]]:
    suffix_count = len(re.findall(r"\$?\d+(?:\.\d+)?\s*[KMB]\b", text))
    spelled_million = len(re.findall(r"\bmillion\b", text, re.IGNORECASE))
    spelled_billion = len(re.findall(r"\bbillion\b", text, re.IGNORECASE))
    spelled_total = spelled_million + spelled_billion
    issues: List[Dict[str, Any]] = []
    if suffix_count >= 3 and spelled_total >= 1:
        issues.append({
            "category": "magnitude_style_mixed",
            "match": f"suffix={suffix_count} spelled={spelled_total}",
            "reason": "Document mixes M/B suffixes with spelled-out 'million'/'billion'",
        })
    elif spelled_total >= 3 and suffix_count >= 1:
        issues.append({
            "category": "magnitude_style_mixed",
            "match": f"suffix={suffix_count} spelled={spelled_total}",
            "reason": "Document mixes spelled-out 'million'/'billion' with M/B suffixes",
        })
    return issues


def _check_date_formats(text: str) -> List[Dict[str, Any]]:
    iso = len(_ISO_DATE_RE.findall(text))
    slash_matches = _SLASH_DATE_RE.findall(text)
    us = eu = 0
    for a, b, _y in slash_matches:
        ai, bi = int(a), int(b)
        if ai > 12 and bi <= 12:
            eu += 1
        elif bi > 12 and ai <= 12:
            us += 1
        else:
            us += 1  # ambiguous → default US convention
    styles = [("ISO", iso), ("US", us), ("EU", eu)]
    nonzero = [s for s in styles if s[1] > 0]
    if len(nonzero) > 1:
        return [{
            "category": "date_format_mixed",
            "match": ",".join(f"{name}={n}" for name, n in nonzero),
            "reason": "Multiple date formats used",
        }]
    return []


def _check_time_formats(text: str) -> List[Dict[str, Any]]:
    t12 = len(_TIME_12H_RE.findall(text))
    t24 = len(_TIME_24H_RE.findall(text))
    if t12 > 0 and t24 > 0:
        return [{
            "category": "time_format_mixed",
            "match": f"12h={t12} 24h={t24}",
            "reason": "Document mixes 12h and 24h time formats",
        }]
    return []


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def validate_numerics(text: str) -> Dict[str, Any]:
    """Validate numeric/symbol consistency in ``text``.

    Returns dict with keys: issues (list), score (0-100), summary (counts per category).
    """
    issues: List[Dict[str, Any]] = []

    v4_issues, v4_nets = _check_ipv4_cidrs(text)
    issues.extend(v4_issues)
    issues.extend(_check_cidr_overlaps(v4_nets))
    issues.extend(_check_ipv6_cidrs(text))
    issues.extend(_check_rfc1918(text, v4_nets))
    issues.extend(_check_vlans(text))
    issues.extend(_check_asns(text))
    issues.extend(_check_percent_sums(text))
    issues.extend(_check_year_totals(text))
    issues.extend(_check_currency(text))
    issues.extend(_check_magnitude_consistency(text))
    issues.extend(_check_date_formats(text))
    issues.extend(_check_time_formats(text))

    summary: Dict[str, int] = {}
    for i in issues:
        summary[i["category"]] = summary.get(i["category"], 0) + 1

    penalty = min(100, len(issues) * 5)
    score = max(0, 100 - penalty)
    return {"issues": issues, "score": score, "summary": summary}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="WriteGuard Numeric Validator (WG 16)")
    parser.add_argument("--text", type=str, default=None)
    parser.add_argument("--file", type=str, default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if args.file:
        text = Path(args.file).read_text(encoding="utf-8")
    elif args.text:
        text = args.text
    else:
        text = sys.stdin.read()

    result = validate_numerics(text)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"Score: {result['score']}/100")
        print(f"Summary: {result['summary']}")
        for i in result["issues"]:
            print(f"  [{i['category']}] {i.get('match', '')}: {i.get('reason', '')}")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
