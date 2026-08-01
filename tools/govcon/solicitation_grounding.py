"""Deterministic grounding utilities for /proposals solicitation drafting.

Solicitation-structure counterpart of tools/govcon/rfi_grounding.py
(ground-prop-02): instead of asking the LLM not to hallucinate citations,
remove the opportunity deterministically.

  - build_ground_truth_block(parsed) -> prompt block listing the only citable
    solicitation structure (UCF sections, L.x instructions, volumes, Section M
    factors/subfactors, CLINs, submission facts), injected into drafting prompts
  - validate_references(text, parsed) -> flag citations of UCF sections,
    Section L instructions, evaluation factors, subfactors, volumes, or CLINs
    that do not exist in the parsed solicitation

`parsed` is the dict produced by tools/govcon/solicitation_parser.py.
All functions are pure/deterministic (regex + dict lookups, no LLM, no DB).
Generic placeholder/numeric checks live in tools/quality/content_grounding.py.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# Re-exported so proposal call sites can import the generic pieces from here,
# mirroring rfi_grounding.py.
from tools.quality.content_grounding import (  # noqa: F401
    check_numeric_claims,
    find_placeholders,
    placeholder_findings,
    substitute_facts,
)

# ── Citation patterns ─────────────────────────────────────────────────────────

# "Section L", "Section L.4.2", "Section M-2" — UCF letter sections. Negative
# lookahead keeps multi-letter tokens ("Section II") in group scope for the
# roman/invalid check below.
_UCF_SECTION_REF_RE = re.compile(
    r"\bSections?\s+([A-M])((?:[.\-]\d+)+)?(?![A-Za-z0-9])"
)
# Roman-numeral section refs that are NOT a single UCF letter (II, IV, VII…) —
# hallmark of a hallucinated citation; solicitations use lettered sections.
_ROMAN_SECTION_REF_RE = re.compile(r"\bSections?\s+([IVXLC]{2,})\b")
_FACTOR_REF_RE = re.compile(r"\b(?:Evaluation\s+)?Factor\s+(\d{1,2})\b", re.IGNORECASE)
_SUBFACTOR_REF_RE = re.compile(r"\bSub-?factor\s+(\d{1,2}(?:\.\d{1,2})?)\b", re.IGNORECASE)
_VOLUME_REF_RE = re.compile(r"\bVolume\s+([IVX]{1,4}|\d{1,2})\b", re.IGNORECASE)
_CLIN_REF_RE = re.compile(r"\bCLIN\s*#?\s*(\d{4}[A-Z]{0,2})\b", re.IGNORECASE)

_ROMAN_VALUES = {"I": 1, "V": 5, "X": 10}


def _roman_to_int(token: str) -> int | None:
    token = token.upper()
    if not token or any(c not in _ROMAN_VALUES for c in token):
        return None
    total = 0
    for i, c in enumerate(token):
        v = _ROMAN_VALUES[c]
        if i + 1 < len(token) and v < _ROMAN_VALUES[token[i + 1]]:
            total -= v
        else:
            total += v
    return total


def _volume_keys(raw: str) -> set[str]:
    """Normalize a volume identifier to the set of equivalent citable keys
    ("II" and "2" cite the same volume)."""
    raw = (raw or "").strip().upper()
    keys = {raw}
    if raw.isdigit():
        n = int(raw)
        romans = {1: "I", 2: "II", 3: "III", 4: "IV", 5: "V", 6: "VI",
                  7: "VII", 8: "VIII", 9: "IX", 10: "X"}
        if n in romans:
            keys.add(romans[n])
    else:
        n = _roman_to_int(raw)
        if n is not None:
            keys.add(str(n))
    return keys


def _valid_sets(parsed: dict) -> dict:
    parsed = parsed or {}
    section_letters = {
        (s.get("letter") or "").upper()
        for s in parsed.get("document_sections", [])
    }
    l_numbers = {
        (i.get("number") or "").upper()
        for i in parsed.get("section_l_instructions", [])
    }
    factors = {str(f.get("factor")) for f in parsed.get("section_m_factors", [])}
    subfactors = {
        str(sf.get("number"))
        for f in parsed.get("section_m_factors", [])
        for sf in f.get("subfactors", [])
    }
    volumes: set[str] = set()
    for v in parsed.get("volume_structure", []):
        volumes |= _volume_keys(str(v.get("volume", "")))
    clins = {(c.get("clin") or "").upper() for c in parsed.get("clins", [])}
    return {
        "sections": {s for s in section_letters if s},
        "l_numbers": {n for n in l_numbers if n},
        "factors": {f for f in factors if f and f != "None"},
        "subfactors": {s for s in subfactors if s and s != "None"},
        "volumes": {v for v in volumes if v},
        "clins": {c for c in clins if c},
    }


def validate_references(text: str, parsed: dict) -> dict:
    """Validate every 'Section X'/'Factor N'/'Subfactor N.M'/'Volume N'/'CLIN
    NNNN' reference in text against the parsed solicitation structure.

    A reference class is only checked when the parse produced that class (no
    false positives on unparsed or partially-parsed documents). Multi-letter
    roman-numeral section references (e.g. 'Section IV') are always invalid —
    solicitations use UCF lettered sections.
    Returns {"valid": bool, "invalid_refs": [{ref, reason}], "checked": int}.
    """
    if not text:
        return {"valid": True, "invalid_refs": [], "checked": 0}
    valid = _valid_sets(parsed)
    invalid, checked = [], 0

    for m in _ROMAN_SECTION_REF_RE.finditer(text):
        checked += 1
        invalid.append({
            "ref": f"Section {m.group(1)}",
            "reason": "roman-numeral section — solicitations use UCF lettered sections (A–M)",
        })

    if valid["sections"]:
        for m in _UCF_SECTION_REF_RE.finditer(text):
            letter = m.group(1).upper()
            sub = (m.group(2) or "").replace("-", ".")
            checked += 1
            if letter not in valid["sections"]:
                invalid.append({
                    "ref": f"Section {letter}{sub}",
                    "reason": f"not in solicitation sections {sorted(valid['sections'])}",
                })
                continue
            # Section L sub-references are checked against the parsed L.x
            # instruction numbers; other letters' sub-structure is unparsed.
            if letter == "L" and sub and valid["l_numbers"]:
                l_ref = f"L{sub}"
                if l_ref not in valid["l_numbers"]:
                    invalid.append({
                        "ref": f"Section {l_ref}",
                        "reason": "no such Section L instruction in the solicitation",
                    })

    if valid["factors"]:
        for m in _FACTOR_REF_RE.finditer(text):
            checked += 1
            if m.group(1) not in valid["factors"]:
                invalid.append({
                    "ref": f"Factor {m.group(1)}",
                    "reason": f"evaluation factors are {sorted(valid['factors'])}",
                })

    if valid["subfactors"]:
        for m in _SUBFACTOR_REF_RE.finditer(text):
            checked += 1
            if m.group(1) not in valid["subfactors"]:
                invalid.append({
                    "ref": f"Subfactor {m.group(1)}",
                    "reason": f"subfactors are {sorted(valid['subfactors'])}",
                })

    if valid["volumes"]:
        for m in _VOLUME_REF_RE.finditer(text):
            checked += 1
            if not (_volume_keys(m.group(1)) & valid["volumes"]):
                invalid.append({
                    "ref": f"Volume {m.group(1).upper()}",
                    "reason": "no such proposal volume in Section L",
                })

    if valid["clins"]:
        for m in _CLIN_REF_RE.finditer(text):
            checked += 1
            if m.group(1).upper() not in valid["clins"]:
                invalid.append({
                    "ref": f"CLIN {m.group(1).upper()}",
                    "reason": f"solicitation CLINs are {sorted(valid['clins'])}",
                })

    return {"valid": not invalid, "invalid_refs": invalid, "checked": checked}


# ── Prompt ground truth ───────────────────────────────────────────────────────

def build_ground_truth_block(parsed: dict) -> str:
    """Render the only citable solicitation structure as a prompt constraint
    block, injected into every proposal-drafting prompt so the model sees the
    real section/factor/CLIN structure instead of inventing one."""
    parsed = parsed or {}
    lines = ["[SOLICITATION GROUND TRUTH — cite ONLY what is listed here]"]
    header = f"{parsed.get('solicitation_number', '')} — {parsed.get('title', '')}".strip(" —")
    if header:
        lines.append(f"Solicitation: {header}")

    sections = parsed.get("document_sections") or []
    if sections:
        lines.append("Document sections: " + "; ".join(
            f"{s.get('letter')} {s.get('title')}" for s in sections))

    l_items = parsed.get("section_l_instructions") or []
    if l_items:
        lines.append("Section L instructions: " + "; ".join(
            f"{i.get('number')} {i.get('title')}" for i in l_items))

    volumes = parsed.get("volume_structure") or []
    if volumes:
        lines.append("Proposal volumes: " + "; ".join(
            f"Volume {v.get('volume')} {v.get('title')}" for v in volumes))

    factors = parsed.get("section_m_factors") or []
    if factors:
        rendered = []
        for f in factors:
            part = f"Factor {f.get('factor')} {f.get('name')}"
            if f.get("weight_pct"):
                part += f" (weight {f['weight_pct']}%)"
            subs = f.get("subfactors") or []
            if subs:
                part += " [subfactors: " + ", ".join(
                    f"{sf.get('number')} {sf.get('name')}" for sf in subs) + "]"
            rendered.append(part)
        lines.append("Evaluation factors: " + "; ".join(rendered))
    if parsed.get("basis_of_award"):
        basis = {"lpta": "Lowest Price Technically Acceptable",
                 "best_value_tradeoff": "Best Value Tradeoff"}.get(
                     parsed["basis_of_award"], parsed["basis_of_award"])
        lines.append(f"Basis of award: {basis}")
    if parsed.get("relative_importance"):
        lines.append(f"Relative importance: {parsed['relative_importance']}")

    clins = parsed.get("clins") or []
    if clins:
        lines.append("CLINs: " + "; ".join(
            f"{c.get('clin')} {c.get('description', '')[:60]}".strip() for c in clins))

    sub = parsed.get("submission_requirements") or {}
    facts = []
    if sub.get("max_pages"):
        facts.append(f"{sub['max_pages']}-page limit")
    if parsed.get("due_date") or sub.get("due_date"):
        facts.append(f"proposals due {parsed.get('due_date') or sub.get('due_date')}")
    if sub.get("submission_portal"):
        facts.append(f"submit via {sub['submission_portal']}")
    if facts:
        lines.append("Submission facts: " + "; ".join(facts))

    lines.append(
        "Rules: Reference only the sections, instructions, volumes, factors, "
        "and CLINs listed above (e.g. \"Section L.4\", \"Factor 2\", "
        "\"Volume I\", \"CLIN 0001\"). Never invent section numbers, "
        "evaluation factors, or CLINs, and never use roman-numeral document "
        "sections. Quote the solicitation's own language when responding to "
        "its requirements. Do not assert specific past-performance metrics, "
        "customer names, or contract values unless they appear in the "
        "provided source context or company profile."
    )
    return "\n".join(lines)
