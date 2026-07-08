"""Surface-agnostic anti-hallucination utilities for LLM-drafted content.

Extracted from tools/govcon/rfi_grounding.py so any drafting surface
(RFI workbench, proposals, DIC document generator, Tech Writer) can reuse
the deterministic pieces. Everything here is pure regex/dict — no LLM,
no DB, no Flask.

  - find_placeholders(text)         -> unresolved [BRACKETED] tokens
  - substitute_facts(text, pairs)   -> replace tokens with known values
  - placeholder_findings(sections)  -> per-section unresolved-token report,
                                       ready for an export/publish gate
  - check_numeric_claims(sections)  -> cross-section numeric conflicts
                                       (ROM totals, prototype timelines)

Surface-specific logic (which facts are substitutable, which citation
structures are valid) stays in the caller — see rfi_grounding.py for the
RFI-structure validator built on top of this module.
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


def substitute_facts(text: str, pairs: list[tuple[str, str]]) -> tuple[str, list[dict]]:
    """Replace placeholder tokens with deterministically-known values.

    Args:
        text: draft content.
        pairs: (regex_pattern, replacement) tuples; patterns are matched
            case-insensitively. Pairs with empty replacements are skipped.

    Returns (new_text, substitutions) where substitutions is a list of
    {pattern, value, count}. Tokens without a known value are left for
    find_placeholders() / the gate to flag.
    """
    if not text:
        return text, []
    substitutions = []
    for pattern, value in pairs:
        if not value:
            continue
        compiled = re.compile(pattern, re.IGNORECASE)
        new_text, count = compiled.subn(str(value), text)
        if count:
            substitutions.append({"pattern": pattern, "value": str(value), "count": count})
            text = new_text
    return text, substitutions


def placeholder_findings(sections: list[dict], content_keys: tuple[str, ...] = ("content", "ai_draft")) -> list[dict]:
    """Scan sections for unresolved placeholder tokens.

    Each section dict needs an identifying field (item_number / title / id —
    first one present is used) and content under one of content_keys.
    Returns [{item_number, placeholders[]}] — empty list means gate passes.
    """
    findings = []
    for sec in sections:
        content = ""
        for key in content_keys:
            if sec.get(key):
                content = sec[key]
                break
        tokens = find_placeholders(content)
        if tokens:
            label = sec.get("item_number") or sec.get("title") or sec.get("id") or "?"
            findings.append({"item_number": label, "placeholders": tokens})
    return findings


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
    timeline months. Returns conflict dicts:
    {type, sections[], message, severity}."""
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
