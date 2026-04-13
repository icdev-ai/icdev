#!/usr/bin/env python3
# CUI // SP-CTI
"""Standards Validator (WG 22) — deterministic standards citation validator.

Scans prose for standards references (NIST SP 800-53, TOGAF, OWASP, CIS,
ISO 27001, PCI DSS, SABSA) and validates them against
``args/standards_registry.yaml``.

Flags:
  - valid           : citation matches a registry entry
  - wrong_title     : citation asserts a title that disagrees with registry
  - not_found       : standard known, but section/control not in registry
  - missing_section : bare citation (e.g. "NIST SP 800-53" with no control)

Entry point:
    validate_citations(text: str, registry_path: str | Path | None = None) -> dict
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import yaml  # type: ignore
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore


DEFAULT_REGISTRY = (
    Path(__file__).resolve().parents[2] / "args" / "standards_registry.yaml"
)


# ---------------------------------------------------------------------------
# Citation detection patterns
# ---------------------------------------------------------------------------

# NIST SP 800-53/171 with a control identifier (AC-1, 3.1.1, etc.)
PAT_NIST_SP = re.compile(
    r"\bNIST\s+SP\s+800-?(?P<num>\d+)\s+"
    r"(?P<control>(?:[A-Z]{2}-\d+(?:\(\d+\))?|\d+(?:\.\d+)+))\b"
)
# Bare NIST SP reference (no control)
PAT_NIST_SP_BARE = re.compile(r"\bNIST\s+SP\s+800-?(?P<num>\d+)\b")

PAT_TOGAF_PHASE = re.compile(r"\bTOGAF\s+Phase\s+(?P<phase>[A-H])\b")
PAT_TOGAF_BARE = re.compile(r"\bTOGAF\b(?!\s+Phase)")

PAT_OWASP = re.compile(r"\bOWASP\s+(?:Top\s+10\s+)?A(?P<num>\d+)\b", re.IGNORECASE)
PAT_OWASP_BARE = re.compile(r"\bOWASP(?:\s+Top\s+10)?\b(?!\s+A\d)", re.IGNORECASE)

PAT_CIS = re.compile(r"\bCIS\s+Control\s+(?P<num>\d+)\b")
PAT_CIS_BARE = re.compile(r"\bCIS\s+Controls?\b(?!\s+\d)")

PAT_ISO = re.compile(r"\bISO\s+27001\s+(?P<clause>\d+(?:\.\d+)*)\b")
PAT_ISO_BARE = re.compile(r"\bISO\s+27001\b(?!\s+\d)")

PAT_PCI = re.compile(
    r"\bPCI\s+DSS\s+Req(?:uirement)?\s+(?P<req>\d+(?:\.\d+)*)\b", re.IGNORECASE
)
PAT_PCI_BARE = re.compile(r"\bPCI\s+DSS\b(?!\s+Req)", re.IGNORECASE)

PAT_SABSA = re.compile(
    r"\bSABSA\s+(?P<layer>Contextual|Conceptual|Logical|Physical|Component|Operational)\b"
)
PAT_SABSA_BARE = re.compile(r"\bSABSA\b(?!\s+(?:Contextual|Conceptual|Logical|Physical|Component|Operational))")


# ---------------------------------------------------------------------------
# Registry loading
# ---------------------------------------------------------------------------


def _load_registry(registry_path: Optional[Path] = None) -> Dict[str, Any]:
    path = Path(registry_path) if registry_path else DEFAULT_REGISTRY
    if not path.exists():
        return {"standards": {}}
    text = path.read_text(encoding="utf-8")
    if yaml is not None:
        return yaml.safe_load(text) or {"standards": {}}
    # Minimal fallback parser — only needed if PyYAML missing.
    return {"standards": {}}


# ---------------------------------------------------------------------------
# Per-standard validators
# ---------------------------------------------------------------------------


def _norm_nist_family(num: str) -> Optional[str]:
    num = num.lstrip("0") or "0"
    mapping = {"53": "NIST_SP_800-53", "171": "NIST_SP_800-171"}
    return mapping.get(num)


def _validate_nist(text: str, reg: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen_spans: List[tuple[int, int]] = []
    for m in PAT_NIST_SP.finditer(text):
        seen_spans.append(m.span())
        family = _norm_nist_family(m.group("num"))
        control = m.group("control")
        entry = reg.get(family or "", {})
        controls = entry.get("controls", {}) if entry else {}
        if not entry:
            status = "not_found"
        elif control in controls:
            status = "valid"
        else:
            status = "not_found"
        out.append(
            {
                "standard": family or f"NIST_SP_800-{m.group('num')}",
                "reference": m.group(0),
                "section": control,
                "status": status,
                "line": text.count("\n", 0, m.start()) + 1,
            }
        )
    for m in PAT_NIST_SP_BARE.finditer(text):
        if any(s <= m.start() < e for s, e in seen_spans):
            continue
        family = _norm_nist_family(m.group("num"))
        out.append(
            {
                "standard": family or f"NIST_SP_800-{m.group('num')}",
                "reference": m.group(0),
                "section": None,
                "status": "missing_section",
                "line": text.count("\n", 0, m.start()) + 1,
            }
        )
    return out


def _validate_togaf(text: str, reg: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    entry = reg.get("TOGAF", {})
    phases = entry.get("phases", {}) if entry else {}
    seen: List[tuple[int, int]] = []
    for m in PAT_TOGAF_PHASE.finditer(text):
        seen.append(m.span())
        phase = m.group("phase")
        status = "valid" if phase in phases else "not_found"
        out.append(
            {
                "standard": "TOGAF",
                "reference": m.group(0),
                "section": phase,
                "status": status,
                "line": text.count("\n", 0, m.start()) + 1,
            }
        )
    for m in PAT_TOGAF_BARE.finditer(text):
        if any(s <= m.start() < e for s, e in seen):
            continue
        out.append(
            {
                "standard": "TOGAF",
                "reference": m.group(0),
                "section": None,
                "status": "missing_section",
                "line": text.count("\n", 0, m.start()) + 1,
            }
        )
    return out


def _validate_owasp(text: str, reg: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    entry = reg.get("OWASP_TOP_10", {})
    items = entry.get("items", {}) if entry else {}
    seen: List[tuple[int, int]] = []
    for m in PAT_OWASP.finditer(text):
        seen.append(m.span())
        key = f"A{int(m.group('num')):02d}"
        status = "valid" if key in items else "not_found"
        out.append(
            {
                "standard": "OWASP_TOP_10",
                "reference": m.group(0),
                "section": key,
                "status": status,
                "line": text.count("\n", 0, m.start()) + 1,
            }
        )
    for m in PAT_OWASP_BARE.finditer(text):
        if any(s <= m.start() < e for s, e in seen):
            continue
        out.append(
            {
                "standard": "OWASP_TOP_10",
                "reference": m.group(0),
                "section": None,
                "status": "missing_section",
                "line": text.count("\n", 0, m.start()) + 1,
            }
        )
    return out


def _validate_cis(text: str, reg: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    entry = reg.get("CIS_CONTROLS", {})
    items = entry.get("items", {}) if entry else {}
    seen: List[tuple[int, int]] = []
    for m in PAT_CIS.finditer(text):
        seen.append(m.span())
        key = m.group("num")
        status = "valid" if key in items else "not_found"
        out.append(
            {
                "standard": "CIS_CONTROLS",
                "reference": m.group(0),
                "section": key,
                "status": status,
                "line": text.count("\n", 0, m.start()) + 1,
            }
        )
    for m in PAT_CIS_BARE.finditer(text):
        if any(s <= m.start() < e for s, e in seen):
            continue
        out.append(
            {
                "standard": "CIS_CONTROLS",
                "reference": m.group(0),
                "section": None,
                "status": "missing_section",
                "line": text.count("\n", 0, m.start()) + 1,
            }
        )
    return out


def _validate_iso(text: str, reg: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    entry = reg.get("ISO_27001", {})
    clauses = entry.get("clauses", {}) if entry else {}
    seen: List[tuple[int, int]] = []
    for m in PAT_ISO.finditer(text):
        seen.append(m.span())
        key = m.group("clause")
        status = "valid" if key in clauses else "not_found"
        out.append(
            {
                "standard": "ISO_27001",
                "reference": m.group(0),
                "section": key,
                "status": status,
                "line": text.count("\n", 0, m.start()) + 1,
            }
        )
    for m in PAT_ISO_BARE.finditer(text):
        if any(s <= m.start() < e for s, e in seen):
            continue
        out.append(
            {
                "standard": "ISO_27001",
                "reference": m.group(0),
                "section": None,
                "status": "missing_section",
                "line": text.count("\n", 0, m.start()) + 1,
            }
        )
    return out


def _validate_pci(text: str, reg: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    entry = reg.get("PCI_DSS", {})
    reqs = entry.get("requirements", {}) if entry else {}
    seen: List[tuple[int, int]] = []
    for m in PAT_PCI.finditer(text):
        seen.append(m.span())
        key = m.group("req")
        top = key.split(".")[0]
        status = "valid" if key in reqs or top in reqs else "not_found"
        out.append(
            {
                "standard": "PCI_DSS",
                "reference": m.group(0),
                "section": key,
                "status": status,
                "line": text.count("\n", 0, m.start()) + 1,
            }
        )
    for m in PAT_PCI_BARE.finditer(text):
        if any(s <= m.start() < e for s, e in seen):
            continue
        out.append(
            {
                "standard": "PCI_DSS",
                "reference": m.group(0),
                "section": None,
                "status": "missing_section",
                "line": text.count("\n", 0, m.start()) + 1,
            }
        )
    return out


def _validate_sabsa(text: str, reg: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    entry = reg.get("SABSA", {})
    layers = entry.get("layers", []) if entry else []
    seen: List[tuple[int, int]] = []
    for m in PAT_SABSA.finditer(text):
        seen.append(m.span())
        layer = m.group("layer")
        status = "valid" if layer in layers else "not_found"
        out.append(
            {
                "standard": "SABSA",
                "reference": m.group(0),
                "section": layer,
                "status": status,
                "line": text.count("\n", 0, m.start()) + 1,
            }
        )
    for m in PAT_SABSA_BARE.finditer(text):
        if any(s <= m.start() < e for s, e in seen):
            continue
        out.append(
            {
                "standard": "SABSA",
                "reference": m.group(0),
                "section": None,
                "status": "missing_section",
                "line": text.count("\n", 0, m.start()) + 1,
            }
        )
    return out


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def validate_citations(
    text: str,
    registry_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Scan text, validate citations, return aggregate result."""
    reg = _load_registry(registry_path).get("standards", {})

    citations: List[Dict[str, Any]] = []
    citations.extend(_validate_nist(text, reg))
    citations.extend(_validate_togaf(text, reg))
    citations.extend(_validate_owasp(text, reg))
    citations.extend(_validate_cis(text, reg))
    citations.extend(_validate_iso(text, reg))
    citations.extend(_validate_pci(text, reg))
    citations.extend(_validate_sabsa(text, reg))

    citations.sort(key=lambda c: (c["line"], c["standard"]))

    valid = sum(1 for c in citations if c["status"] == "valid")
    wrong_title = sum(1 for c in citations if c["status"] == "wrong_title")
    not_found = sum(1 for c in citations if c["status"] == "not_found")
    missing_section = sum(1 for c in citations if c["status"] == "missing_section")
    total = len(citations)

    if total == 0:
        score = 100.0
    else:
        penalty = (
            wrong_title * 1.0
            + not_found * 0.8
            + missing_section * 0.5
        )
        score = max(0.0, 100.0 * (1.0 - penalty / total))

    return {
        "citations": citations,
        "valid": valid,
        "wrong_title": wrong_title,
        "not_found": not_found,
        "missing_section": missing_section,
        "total": total,
        "score": round(score, 1),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    import argparse
    import json
    import sys

    parser = argparse.ArgumentParser(
        description="WG 22 Standards Validator — validate standards citations"
    )
    parser.add_argument("--file", "-f", help="Path to text file")
    parser.add_argument("--text", "-t", help="Text string")
    parser.add_argument("--registry", help="Custom registry YAML path")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    args = parser.parse_args()

    if args.file:
        content = Path(args.file).read_text(encoding="utf-8")
    elif args.text:
        content = args.text
    else:
        content = sys.stdin.read()

    reg_path = Path(args.registry) if args.registry else None
    result = validate_citations(content, reg_path)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"Score            : {result['score']}/100")
        print(f"Total citations  : {result['total']}")
        print(f"  valid          : {result['valid']}")
        print(f"  wrong_title    : {result['wrong_title']}")
        print(f"  not_found      : {result['not_found']}")
        print(f"  missing_section: {result['missing_section']}")
        for c in result["citations"]:
            print(
                f"  line {c['line']:4d} [{c['status']:15s}] "
                f"{c['standard']:16s} {c['reference']!r}"
            )
