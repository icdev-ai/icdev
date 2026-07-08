"""Deterministic grounding utilities for the RFI Response Workbench.

Anti-hallucination layer, FORGE-style: instead of asking the LLM not to
hallucinate, remove the opportunity deterministically.

  - find_placeholders(text)                      -> unresolved [BRACKETED] tokens
  - substitute_profile_facts(text, profile, parsed) -> replace identity-fact
    placeholders (UEI, CAGE, entity name, NAICS, RFI number, contacts) with
    real values from the company profile / parsed RFI
  - validate_references(text, parsed)            -> flag citations of sections,
    parts, items, or objectives that do not exist in the parsed RFI
  - build_ground_truth_block(parsed)             -> prompt block listing the only
    citable structure, injected into every generation prompt
  - check_numeric_claims(sections)               -> cross-section numeric
    conflicts (ROM totals, prototype timelines)

All functions are pure/deterministic (regex + dict lookups, no LLM, no DB).
"""

from __future__ import annotations

import re

# ── Placeholders ──────────────────────────────────────────────────────────────

# [UPPERCASE_TOKEN] style placeholders: starts with an uppercase letter, then
# uppercase letters/digits/space/underscore/dash/slash/&/#. Excludes markdown
# links ("[Title](url)" — mixed case and followed by "(") and checkboxes.
_PLACEHOLDER_RE = re.compile(r"\[([A-Z][A-Z0-9 _/&#.-]{1,40})\](?!\()")


def find_placeholders(text: str) -> list[str]:
    """Return sorted unique unresolved placeholder tokens like [UEI_NUMBER]."""
    if not text:
        return []
    return sorted({f"[{m.group(1)}]" for m in _PLACEHOLDER_RE.finditer(text)})


# ── Identity-fact substitution ────────────────────────────────────────────────

def _fact_patterns(profile: dict, parsed: dict) -> list[tuple[re.Pattern, str]]:
    """Build (pattern, replacement) pairs for facts we know deterministically."""
    contact_name = profile.get("contact_name") or ""
    pairs = [
        (r"\[(?:SAM[\s_.-]*)?(?:GOV[\s_.-]*)?UEI(?:[\s_-]*(?:NUMBER|NO|#))?\]",
         profile.get("sam_uei")),
        (r"\[CAGE(?:[\s_-]*CODE)?\]", profile.get("cage_code")),
        (r"\[(?:COMPANY|ENTITY)(?:[\s_-]*NAME)?\]", profile.get("entity_name")),
        (r"\[NAICS(?:[\s_-]*CODE)?\]",
         parsed.get("naics") or profile.get("primary_naics")),
        (r"\[RFI[\s_-]*(?:NUMBER|NO|#)?\]", parsed.get("rfi_number")),
        (r"\[ADDRESS\]", profile.get("address")),
        (r"\[(?:PHONE|TELEPHONE)(?:[\s_-]*(?:NUMBER|NO))?\]",
         profile.get("contact_phone")),
        (r"\[EMAIL(?:[\s_-]*ADDRESS)?\]", profile.get("contact_email")),
        (r"\[(?:POC|CONTACT)(?:[\s_-]*NAME)?\]", contact_name),
    ]
    compiled = [
        (re.compile(pat, re.IGNORECASE), str(val))
        for pat, val in pairs
        if val
    ]
    # Mangled RFI numbers like "RFI-26-[TASK_ORDER]" — replace the whole run
    # with the real RFI number when we know it.
    rfi_number = parsed.get("rfi_number")
    if rfi_number:
        compiled.append((
            re.compile(r"RFI[-\s]?\d{2}[-\s]?\[[A-Z0-9 _-]{1,30}\]"),
            str(rfi_number),
        ))
    return compiled


def substitute_profile_facts(text: str, profile: dict, parsed: dict) -> tuple[str, list[dict]]:
    """Replace identity-fact placeholders with known values.

    Returns (new_text, substitutions) where substitutions is a list of
    {token, value, count}. Tokens without a known value are left untouched
    for find_placeholders() / the export gate to flag.
    """
    if not text:
        return text, []
    substitutions = []
    for pattern, value in _fact_patterns(profile or {}, parsed or {}):
        new_text, count = pattern.subn(value, text)
        if count:
            substitutions.append({
                "pattern": pattern.pattern, "value": value, "count": count,
            })
            text = new_text
    return text, substitutions


# ── Citation validation ───────────────────────────────────────────────────────

_SECTION_REF_RE = re.compile(r"\bSection\s+([0-9IVXLCivxlc]+(?:\.[0-9A-Za-z]+)*)\b")
_OBJECTIVE_REF_RE = re.compile(r"\bObjectives?\s+([A-Z])\b")
# Negative lookbehinds: "FAR Part 12" / "DFARS Part 215" are regulatory
# citations, not RFI questionnaire parts.
_PART_REF_RE = re.compile(r"(?<!FAR\s)(?<!DFARS\s)\bParts?\s+(\d{1,2})\b")
_ITEM_REF_RE = re.compile(r"\bItems?\s+(\d{1,2}\.\d{1,2})\b")

_ROMAN_RE = re.compile(r"^[IVXLCivxlc]+$")


def _valid_sets(parsed: dict) -> dict:
    parsed = parsed or {}
    doc_sections = {str(s.get("number")) for s in parsed.get("document_sections", [])}
    items = {p.get("item_number") for p in parsed.get("questionnaire_parts", [])}
    # Workbench-native structure is always citable.
    items |= {"6.1", "6.2", "6.3", "6.4"}
    parts = {p.get("part", "").replace("Part", "").strip()
             for p in parsed.get("questionnaire_parts", [])}
    parts = {p for p in parts if p.isdigit()} | {"6"}
    objectives = {o.get("letter") for o in parsed.get("objectives", [])}
    return {
        "sections": {s for s in doc_sections if s},
        "items": {i for i in items if i},
        "parts": parts,
        "objectives": {o for o in objectives if o},
    }


def validate_references(text: str, parsed: dict) -> dict:
    """Validate every 'Section X' / 'Part N' / 'Item N.M' / 'Objective X'
    reference in text against the parsed RFI structure.

    A reference class is only checked when the parse produced that class
    (no false positives on unparsed documents). Roman-numeral section
    references (e.g. 'Section IV.B') are always invalid — hallmark of a
    hallucinated citation.
    Returns {"valid": bool, "invalid_refs": [{ref, reason}], "checked": int}.
    """
    if not text:
        return {"valid": True, "invalid_refs": [], "checked": 0}
    valid = _valid_sets(parsed)
    invalid, checked = [], 0

    for m in _SECTION_REF_RE.finditer(text):
        token = m.group(1)
        checked += 1
        head = token.split(".")[0]
        if _ROMAN_RE.match(head):
            invalid.append({"ref": f"Section {token}",
                            "reason": "roman-numeral section — the RFI uses numeric sections"})
            continue
        if valid["sections"]:
            # "Section 3.0" → section 3; "Section 2.6" may cite an item.
            normalized = head
            if normalized not in valid["sections"] and token not in valid["items"]:
                invalid.append({"ref": f"Section {token}",
                                "reason": f"not in RFI sections {sorted(valid['sections'])}"})

    if valid["objectives"]:
        for m in _OBJECTIVE_REF_RE.finditer(text):
            checked += 1
            if m.group(1) not in valid["objectives"]:
                invalid.append({"ref": f"Objective {m.group(1)}",
                                "reason": f"RFI objectives are {sorted(valid['objectives'])}"})

    if valid["parts"]:
        for m in _PART_REF_RE.finditer(text):
            checked += 1
            if m.group(1) not in valid["parts"]:
                invalid.append({"ref": f"Part {m.group(1)}",
                                "reason": f"RFI questionnaire parts are {sorted(valid['parts'])}"})

    if valid["items"]:
        for m in _ITEM_REF_RE.finditer(text):
            checked += 1
            if m.group(1) not in valid["items"]:
                invalid.append({"ref": f"Item {m.group(1)}",
                                "reason": "no such questionnaire item in the RFI"})

    return {"valid": not invalid, "invalid_refs": invalid, "checked": checked}


# ── Prompt ground truth ───────────────────────────────────────────────────────

def build_ground_truth_block(parsed: dict) -> str:
    """Render the only citable RFI structure as a prompt constraint block.

    Injected into every generation prompt so the model sees the real section
    numbering instead of inventing one.
    """
    parsed = parsed or {}
    lines = ["[RFI GROUND TRUTH — cite ONLY what is listed here]"]
    if parsed.get("rfi_number") or parsed.get("title"):
        lines.append(f"RFI: {parsed.get('rfi_number', '')} — {parsed.get('title', '')}".strip(" —"))

    doc_sections = parsed.get("document_sections") or []
    if doc_sections:
        lines.append("Document sections: " + "; ".join(
            f"{s['number']} {s['title']}" for s in doc_sections))

    objectives = parsed.get("objectives") or []
    if objectives:
        lines.append("Objectives: " + "; ".join(
            f"{o.get('letter')} {o.get('title')}" for o in objectives))

    qparts = parsed.get("questionnaire_parts") or []
    if qparts:
        lines.append("Questionnaire items: " + "; ".join(
            f"{p.get('item_number')} {p.get('topic')}" for p in qparts))

    sub = parsed.get("submission_requirements") or {}
    facts = []
    if sub.get("max_pages"):
        facts.append(f"{sub['max_pages']}-page response limit")
    if sub.get("max_appendix_pages"):
        facts.append(f"{sub['max_appendix_pages']}-page appendix allowed")
    if parsed.get("due_date") or sub.get("due_date"):
        facts.append(f"response due {parsed.get('due_date') or sub.get('due_date')}")
    if parsed.get("questions_due_date") or sub.get("questions_due_date"):
        facts.append(f"questions due {parsed.get('questions_due_date') or sub.get('questions_due_date')}")
    if facts:
        lines.append("Submission facts: " + "; ".join(facts))

    lines.append(
        "Rules: Reference only the sections, items, parts, and objectives "
        "listed above (e.g. \"Section 3\", \"Objective D\", \"Item 2.7\"). "
        "Never invent section numbers and never use roman-numeral sections. "
        "Quote the RFI's own language when answering its questions. Do not "
        "assert specific past-performance metrics, customer names, or "
        "contract values unless they appear in the provided source context "
        "or company profile."
    )
    return "\n".join(lines)


# ── Cross-section numeric consistency ─────────────────────────────────────────

_MONEY_RE = re.compile(
    r"\$\s?([\d][\d,]*(?:\.\d+)?)\s*(M\b|K\b|million|thousand)?", re.IGNORECASE
)
_MONTHS_RE = re.compile(r"\b(\d{1,2})\s*(?:-|to\s+)?\s*months?\b", re.IGNORECASE)


def _normalize_money(num: str, suffix: str | None) -> float:
    val = float(num.replace(",", ""))
    sfx = (suffix or "").lower()
    if sfx in ("m", "million"):
        val *= 1_000_000
    elif sfx in ("k", "thousand"):
        val *= 1_000
    return val


def check_numeric_claims(sections: list[dict]) -> list[dict]:
    """Detect cross-section numeric conflicts in ROM totals and prototype
    timeline months. Returns conflict dicts in the same shape as
    check_cross_section_consistency()."""
    rom_totals: dict[str, set[float]] = {}
    proto_months: dict[str, set[int]] = {}

    for s in sections:
        text = s.get("content") or s.get("ai_draft") or ""
        item = s.get("item_number", "?")
        if not text:
            continue
        for m in _MONEY_RE.finditer(text):
            ctx = text[max(0, m.start() - 60):m.start()].lower()
            if "rom total" in ctx or "total rom" in ctx or ("total" in ctx and "rom" in ctx):
                rom_totals.setdefault(item, set()).add(
                    _normalize_money(m.group(1), m.group(2)))
        for m in _MONTHS_RE.finditer(text):
            ctx = text[max(0, m.start() - 80):min(len(text), m.end() + 40)].lower()
            if "prototype" in ctx and ("award" in ctx or "deliver" in ctx or "working" in ctx):
                proto_months.setdefault(item, set()).add(int(m.group(1)))

    conflicts = []
    all_roms = {v for vals in rom_totals.values() for v in vals}
    if len(all_roms) > 1:
        conflicts.append({
            "type": "rom_total_mismatch",
            "sections": sorted(rom_totals.keys()),
            "message": "ROM total differs across sections: "
                       + ", ".join(f"${v:,.0f}" for v in sorted(all_roms)),
            "severity": "error",
        })
    all_months = {v for vals in proto_months.values() for v in vals}
    if len(all_months) > 1:
        conflicts.append({
            "type": "prototype_timeline_mismatch",
            "sections": sorted(proto_months.keys()),
            "message": "Prototype timeline differs across sections: "
                       + ", ".join(f"{v} months" for v in sorted(all_months)),
            "severity": "warning",
        })
    return conflicts
