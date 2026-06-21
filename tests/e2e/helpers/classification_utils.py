# CUI // SP-CTI
"""
Classification utility functions for the Derivative Classifier E2E tests.

These are deterministic (no LLM) implementations of the classification rules
that the Derivative Classifier coworker profile applies:

  - CAPCO portion marking format  (EO 13526 / ICD 710)
  - Aggregate classification      (highest-portion wins)
  - Compilation rule              (sensitive combination detector)
  - Marking inference             (keyword-based heuristics matching SCG patterns)

In production the coworker profile calls the LLM with the full SOUL.md
identity injected. These helpers let the E2E test suite assert correctness
without requiring a live LLM.
"""
from __future__ import annotations

import re
from typing import Any

# ---------------------------------------------------------------------------
# Classification level ordering  (lowest → highest)
# ---------------------------------------------------------------------------

_LEVEL_ORDER = ["U", "CUI", "C", "S", "TS", "TS//SCI"]

_LEVEL_LABELS = {
    "U":      "UNCLASSIFIED",
    "CUI":    "CONTROLLED UNCLASSIFIED INFORMATION",
    "C":      "CONFIDENTIAL",
    "S":      "SECRET",
    "TS":     "TOP SECRET",
    "TS//SCI": "TOP SECRET//SCI",
}


def _level_index(lvl: str) -> int:
    normalized = lvl.upper().strip()
    if normalized in _LEVEL_ORDER:
        return _LEVEL_ORDER.index(normalized)
    if normalized.startswith("TS"):
        return _LEVEL_ORDER.index("TS")
    return 0


# ---------------------------------------------------------------------------
# Aggregate — highest-portion wins
# ---------------------------------------------------------------------------

def aggregate_markings(markings: list[str]) -> str:
    """Return the highest classification level from a list of portion markings.

    EO 13526 §1.6(b): Overall classification = highest of all portions.

    Args:
        markings: List of level strings, e.g. ["U", "C", "S", "TS"].

    Returns:
        Highest level string, e.g. "TS".
    """
    if not markings:
        return "U"
    return max(markings, key=_level_index)


# ---------------------------------------------------------------------------
# Compilation rule — per EO 13526 §1.7
# ---------------------------------------------------------------------------

# Keyword sets that signal sensitive operational data when COMBINED
_COMPILATION_GROUPS: list[tuple[str, list[str], str, float]] = [
    # (group_name, keywords, suggested_marking, confidence)
    (
        "unit_location_schedule",
        ["personnel", "unit", "company", "battalion", "grid", "coordinate",
         "schedule", "time", "exercise", "operation"],
        "S",
        0.85,
    ),
    (
        "comms_keys",
        ["frequency", "call sign", "authentication", "crypto", "key", "net"],
        "S",
        0.90,
    ),
    (
        "mission_intent",
        ["objective", "target", "route", "phase", "mission", "strike"],
        "S",
        0.88,
    ),
    (
        "identity_location",
        ["name", "identity", "personnel", "grid", "address", "location"],
        "C",
        0.70,
    ),
]

_COMPILATION_WINDOW_THRESHOLD = 2  # min items with group keywords before elevating


def check_compilation(
    items: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Detect classification-by-compilation: multiple lower-classified items
    that together reveal something at a higher level.

    EO 13526 §1.7: Information classified top secret, secret, or confidential
    may be considered classified by compilation even if individual items are not.

    Args:
        items: List of {"text": str, "marking": str} dicts.

    Returns:
        {
          "elevated": bool,
          "suggested_marking": str,
          "confidence": float,
          "reason": str,
          "triggered_group": str | None,
        }
    """
    combined_text = " ".join(i.get("text", "") for i in items).lower()
    all_markings = [i.get("marking", "U") for i in items]
    highest_existing = aggregate_markings(all_markings)

    for group_name, keywords, suggested, confidence in _COMPILATION_GROUPS:
        matched = [kw for kw in keywords if kw.lower() in combined_text]
        if len(matched) >= _COMPILATION_WINDOW_THRESHOLD:
            # Only flag if the combination is higher than existing highest
            if _level_index(suggested) > _level_index(highest_existing):
                return {
                    "elevated": True,
                    "suggested_marking": suggested,
                    "confidence": confidence,
                    "triggered_group": group_name,
                    "matched_keywords": matched,
                    "reason": (
                        f"Compilation: {len(matched)} indicators from group "
                        f"'{group_name}' ({', '.join(matched[:4])}) elevate "
                        f"aggregate from ({highest_existing}) to ({suggested}) "
                        f"per EO 13526 §1.7."
                    ),
                }

    return {
        "elevated": False,
        "suggested_marking": highest_existing,
        "confidence": 0.0,
        "triggered_group": None,
        "matched_keywords": [],
        "reason": "No compilation elevation triggered.",
    }


# ---------------------------------------------------------------------------
# Marking inference — deterministic SCG-style keyword heuristics
# ---------------------------------------------------------------------------

# Pattern: (regex, marking, rationale_template)
_MARKING_PATTERNS: list[tuple[re.Pattern, str, str]] = [
    # TS — named collection sources, codewords, compartments
    (re.compile(r"\bsigint\s+collection\s+(asset|program|source)\b", re.I),
     "TS", "Named SIGINT collection asset reveals sources and methods (ICD 710 / EO 13526 §1.4(c))"),
    (re.compile(r"\bhumint\s+source\b|\bcontrolled\s+source\b|\bagent\s+report\b", re.I),
     "TS//SCI", "HUMINT controlled source reference — TS//SCI per DODM 5200.01"),
    (re.compile(r"\bcomsec\s+key\b|\bcryptographic\s+(key|material|system)\b", re.I),
     "TS", "COMSEC key material — TS per NSS Policy 12 FAM 540"),

    # Secret — operational details, comms, ROE
    (re.compile(r"\bsincgars\b", re.I),
     "S", "SINCGARS tactical radio system reference reveals COMSEC posture (SCG: COMMS-S-001)"),
    (re.compile(r"\bauthentication\s+code\b|\bnet\s+control\s+station\b", re.I),
     "S", "Authentication codes are Secret per COMSEC marking guidance"),
    (re.compile(r"\broe\s+(chapter|section|appendix)\b|\brules\s+of\s+engagement.*chapter", re.I),
     "C", "Specific ROE chapter/section reference — Confidential per CJCSM 3121 series"),
    (re.compile(r"\bindirect\s+fire.*commander.*approv\b|\bfire\s+support.*authority\b", re.I),
     "S", "Fire support authorization chain reveals command structure (S)"),

    # Confidential — threat assessments, general force disposition
    (re.compile(r"\bthreat\s+assessment\b.*\bmoderate\b|\bprobability.*adversary\b", re.I),
     "C", "General threat assessment without specific intelligence sources — Confidential"),
    (re.compile(r"\badversary\s+radio\s+traffic\b|\bpre-operational\s+activit\b", re.I),
     "S", "Adversary signals activity indicator reveals collection (S)"),

    # Unclassified — explicitly open-source, published data
    (re.compile(r"\bopen.source\b|\bpublicly\s+available\b|\bpublished\s+in\b", re.I),
     "U", "Source material is explicitly open-source — Unclassified"),
    (re.compile(r"\bpublic\s+affairs\b|\bpress\s+release\b", re.I),
     "U", "Public affairs content — Unclassified"),
]

_DEFAULT_MARKING = ("U", "No sensitive keywords detected — defaulting to Unclassified (verify against SCG)")


def infer_marking(text: str) -> dict[str, Any]:
    """
    Infer CAPCO portion marking from paragraph text using SCG-style patterns.

    Args:
        text: Paragraph text to classify.

    Returns:
        {"marking": str, "rationale": str, "confidence": float}
    """
    best_marking = "U"
    best_rationale = _DEFAULT_MARKING[1]
    best_confidence = 0.4  # baseline for no-match

    for pattern, marking, rationale in _MARKING_PATTERNS:
        if pattern.search(text):
            if _level_index(marking) > _level_index(best_marking):
                best_marking = marking
                best_rationale = rationale
                best_confidence = 0.85

    return {
        "marking": best_marking,
        "rationale": best_rationale,
        "confidence": best_confidence,
    }


def classify_document(
    paragraphs: dict[str, str],
) -> dict[str, Any]:
    """
    Full document classification: portion markings + compilation check + aggregate.

    Args:
        paragraphs: {label: text} dict, e.g. {"PARAGRAPH 1": "The unit is..."}

    Returns:
        {
          "portions": {label: {"marking", "rationale", "confidence"}},
          "compilation": {elevated, reason, ...},
          "aggregate": str,
          "banner": str,
          "summary": str,
        }
    """
    portions: dict[str, dict[str, Any]] = {}
    for label, text in paragraphs.items():
        portions[label] = infer_marking(text)

    # Compilation check on all portions together
    compilation_items = [
        {"text": text, "marking": portions[label]["marking"]}
        for label, text in paragraphs.items()
    ]
    compilation = check_compilation(compilation_items)

    # Aggregate = max of individual portions + any compilation elevation
    individual_markings = [p["marking"] for p in portions.values()]
    aggregate = aggregate_markings(individual_markings)
    if compilation["elevated"] and _level_index(compilation["suggested_marking"]) > _level_index(aggregate):
        aggregate = compilation["suggested_marking"]

    banner = format_banner(aggregate)

    summary_lines = [f"{banner}\n"]
    for label, info in portions.items():
        summary_lines.append(
            f"({info['marking']}) {label}: {info['rationale']}"
        )
    if compilation["elevated"]:
        summary_lines.append(
            f"\n⚠ COMPILATION WARNING: {compilation['reason']}"
        )
    summary_lines.append(f"\nOverall Classification: {banner}")

    return {
        "portions": portions,
        "compilation": compilation,
        "aggregate": aggregate,
        "banner": banner,
        "summary": "\n".join(summary_lines),
    }


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def format_portion_mark(level: str, caveats: list[str] | None = None) -> str:
    """Return CAPCO-compliant portion mark string.

    Args:
        level:   Classification level, e.g. "S", "TS".
        caveats: Optional list, e.g. ["NF"], ["SCI"], ["REL TO USA, FVEY"].

    Returns:
        "(S)", "(TS//SCI)", "(S//NF//REL TO USA, FVEY)", etc.
    """
    parts = [level.upper()]
    if caveats:
        parts.extend(c.upper() for c in caveats)
    return "(" + "//".join(parts) + ")"


def format_banner(level: str, caveats: list[str] | None = None) -> str:
    """Return document banner line (no parens, centred label).

    Args:
        level:   "U", "C", "S", "TS".
        caveats: e.g. ["SCI"] → "TOP SECRET//SCI".

    Returns:
        "SECRET", "TOP SECRET//SCI", "UNCLASSIFIED", etc.
    """
    label = _LEVEL_LABELS.get(level.upper(), level.upper())
    if caveats:
        label = label + "//" + "//".join(c.upper() for c in caveats)
    return label
