# CUI // SP-CTI
"""WriteGuard Glossary Engine — deterministic acronym and terminology validator.

Loads a central glossary from ``context/writeguard/glossary.yaml`` and checks
written content for:

1. **Undefined acronyms** — all-caps tokens not in the glossary and not defined
   inline via ``Full Name (ACRONYM)`` pattern.
2. **Forbidden terms** — words/phrases the style guide bans (e.g. "cyber"
   should be "cybersecurity").
3. **Corporate term misuse** — known product/framework names used incorrectly
   (case-insensitive match against glossary).

Follows the same return contract as every other ``_*_check`` in
``analysis_engine.py``: ``{"score": float, "issues": list, ...}``.

Cross-platform: pathlib.Path, encoding='utf-8'.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Glossary loader
# ---------------------------------------------------------------------------

_DEFAULT_GLOSSARY_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "context"
    / "writeguard"
    / "glossary.yaml"
)


def _load_glossary(glossary_path: Optional[str] = None) -> Dict[str, Any]:
    """Load glossary YAML.  Falls back gracefully if file is missing."""
    path = Path(glossary_path) if glossary_path else _DEFAULT_GLOSSARY_PATH
    if not path.exists():
        return {"acronyms": {}, "corporate_terms": {}, "forbidden_terms": []}

    try:
        import yaml  # pyyaml is a project dependency
    except ImportError:  # pragma: no cover — pyyaml always available in ICDEV
        return {"acronyms": {}, "corporate_terms": {}, "forbidden_terms": []}

    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}

    return {
        "acronyms": data.get("acronyms") or {},
        "corporate_terms": data.get("corporate_terms") or {},
        "forbidden_terms": data.get("forbidden_terms") or [],
    }


# ---------------------------------------------------------------------------
# Common all-caps tokens to skip (not real acronyms)
# ---------------------------------------------------------------------------

_COMMON_CAPS: set[str] = {
    "US", "UK", "EU", "UN", "AI", "IT", "OR", "AND", "THE", "FOR",
    "NOT", "BUT", "NOR", "YET", "SO", "IF", "AS", "AT", "BY", "IN",
    "OF", "ON", "TO", "UP", "IS", "AM", "AN", "DO", "NO", "OK", "VS",
    "ID", "RE", "CC", "PM", "HR",
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def check_glossary(
    text: str,
    glossary_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Run glossary/acronym/terminology checks on *text*.

    Parameters
    ----------
    text : str
        Content to validate.
    glossary_path : str | None
        Override path to glossary YAML.  Defaults to
        ``context/writeguard/glossary.yaml``.

    Returns
    -------
    dict
        ``score``              — float 0-1 (1 = no issues)
        ``undefined_acronyms`` — list of acronyms not in glossary & not
                                  defined inline
        ``forbidden_hits``     — list of forbidden terms found
        ``misused_terms``      — list of corporate terms used with wrong case
        ``suggestions``        — list of human-readable suggestions
        ``issues``             — list of short issue strings (same contract
                                  as other WriteGuard checks)
    """
    if not text or not text.strip():
        return {
            "score": 1.0,
            "undefined_acronyms": [],
            "forbidden_hits": [],
            "misused_terms": [],
            "suggestions": [],
            "issues": [],
        }

    glossary = _load_glossary(glossary_path)
    known_acronyms: Dict[str, str] = glossary["acronyms"]
    corporate_terms: Dict[str, str] = glossary["corporate_terms"]
    forbidden_terms: List[str] = glossary["forbidden_terms"]

    issues: List[str] = []
    suggestions: List[str] = []

    # ------------------------------------------------------------------
    # 1. Undefined acronyms
    # ------------------------------------------------------------------
    # Find all-caps tokens (2-6 chars) in the text
    found_acronyms = set(re.findall(r"\b([A-Z]{2,6})\b", text))
    found_acronyms -= _COMMON_CAPS

    # Also include mixed-case known acronyms like cATO, eMASS, SAFe, DoD
    # Corporate terms (FORGE, ANVIL, etc.) also count as known
    all_known = {**known_acronyms, **corporate_terms}
    known_keys_upper = {k.upper(): k for k in all_known}

    # Check if each acronym is either in the glossary or defined inline
    undefined: List[str] = []
    for acr in sorted(found_acronyms):
        # Known in glossary or corporate terms (case-insensitive)?
        if acr in all_known or acr in known_keys_upper:
            continue
        # Defined inline: "Full Name (ACRONYM)"?
        define_re = re.compile(
            r"[a-zA-Z\s]+\(" + re.escape(acr) + r"\)",
            re.IGNORECASE,
        )
        if define_re.search(text):
            continue
        undefined.append(acr)

    if undefined:
        preview = ", ".join(undefined[:5])
        issues.append(f"undefined acronyms: {preview}")
        for acr in undefined:
            suggestions.append(
                f"Define '{acr}' on first use: Full Name ({acr}), "
                f"or add it to glossary.yaml"
            )

    # ------------------------------------------------------------------
    # 2. Forbidden terms
    # ------------------------------------------------------------------
    forbidden_hits: List[str] = []
    _FORBIDDEN_REPLACEMENTS: Dict[str, str] = {
        "cyber": "cybersecurity",
        "govt": "government",
    }
    for term in forbidden_terms:
        # Match as whole word (word boundary)
        pattern = re.compile(r"\b" + re.escape(term) + r"\b", re.IGNORECASE)
        matches = pattern.findall(text)
        if matches:
            forbidden_hits.append(term)
            replacement = _FORBIDDEN_REPLACEMENTS.get(term.lower(), "")
            hint = f" — use '{replacement}'" if replacement else ""
            issues.append(f"forbidden term '{term}'{hint}")
            if replacement:
                suggestions.append(f"Replace '{term}' with '{replacement}'")

    # ------------------------------------------------------------------
    # 3. Corporate term misuse (wrong casing)
    # ------------------------------------------------------------------
    misused: List[str] = []
    for canonical, definition in corporate_terms.items():
        # Find occurrences that match case-insensitively but not exactly
        pattern = re.compile(r"\b" + re.escape(canonical) + r"\b", re.IGNORECASE)
        for match in pattern.finditer(text):
            found_text = match.group(0)
            if found_text != canonical:
                misused.append(f"'{found_text}' should be '{canonical}'")
    # Deduplicate
    misused = list(dict.fromkeys(misused))

    if misused:
        preview = "; ".join(misused[:3])
        issues.append(f"corporate term casing: {preview}")
        for m in misused:
            suggestions.append(f"Fix casing: {m}")

    # ------------------------------------------------------------------
    # Score: penalise per issue
    # ------------------------------------------------------------------
    penalty = (
        len(undefined) * 0.06
        + len(forbidden_hits) * 0.10
        + len(misused) * 0.04
    )
    score = max(0.0, round(1.0 - penalty, 2))

    return {
        "score": score,
        "undefined_acronyms": undefined,
        "forbidden_hits": forbidden_hits,
        "misused_terms": misused,
        "suggestions": suggestions,
        "issues": issues,
    }
