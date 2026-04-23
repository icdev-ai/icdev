# CUI // SP-CTI
"""Options intent parser — extract structured intent from natural-language trading phrases.

parse_intent(text, *, underlying=None) -> dict with fields:
  direction, horizon, iv_view, risk_cap, source, raw_text,
  underlying (optional), greek_targets (optional).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

try:
    import yaml as _yaml
    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False

_SCHEMA_PATH = Path(__file__).parents[3] / "args" / "options_intent_schema.yaml"

_DEFAULTS: dict = {
    "direction": "neutral",
    "horizon": "short",
    "iv_view": "neutral",
    "risk_cap": "defined",
}

# Uppercase words that look like tickers but are option/trading abbreviations
_NON_TICKER = frozenset({"IV", "DTE", "OTM", "ITM", "ATM", "LEAPS", "ER", "VIX"})
_TICKER_RE = re.compile(r"\b[A-Z]{1,5}\b")

# (compiled pattern, greek_targets dict to merge)
_GREEK_PATTERNS: list[tuple[re.Pattern, dict]] = [
    # Delta: reduce / hedge
    (re.compile(r"\b(reduce|lower|decrease|cut)\b.{0,30}\bdelta\b", re.I), {"delta_direction": "reduce"}),
    (re.compile(r"\bhedge\b.{0,30}\bdelta\b", re.I), {"delta_direction": "reduce"}),
    (re.compile(r"\bdelta.{0,30}\b(reduce|lower|decrease|cut|hedge)\b", re.I), {"delta_direction": "reduce"}),
    # Theta: add / collect premium
    (re.compile(r"\b(add|increase|boost|collect)\b.{0,30}\btheta\b", re.I), {"theta": "increase"}),
    (re.compile(r"\bcollect\b.{0,30}\bpremium\b", re.I), {"theta": "increase"}),
    (re.compile(r"\bpremium\b.{0,20}\bcollect\b", re.I), {"theta": "increase"}),
    # Vega: hedge / reduce iv exposure
    (re.compile(r"\bhedge\b.{0,30}\bvega\b", re.I), {"vega_direction": "reduce"}),
    (re.compile(r"\b(reduce|lower|decrease|cut)\b.{0,30}\bvega\b", re.I), {"vega_direction": "reduce"}),
    (re.compile(r"\b(reduce|lower|decrease|cut|hedge)\b.{0,40}\biv\b.{0,20}\bexposure\b", re.I), {"vega_direction": "reduce"}),
    (re.compile(r"\b(reduce|lower|decrease|cut|hedge)\b.{0,40}\bimplied.{0,10}vol", re.I), {"vega_direction": "reduce"}),
    # Delta: neutralize
    (re.compile(r"\bneutralize\b.{0,30}\bdelta\b", re.I), {"delta_direction": "neutral"}),
    (re.compile(r"\bdelta.{0,30}\bneutralize\b", re.I), {"delta_direction": "neutral"}),
    # Delta: protect against
    (re.compile(r"\bprotect.{0,30}\bdelta\b", re.I), {"delta_direction": "reduce"}),
    (re.compile(r"\bdelta.{0,30}\bprotect\b", re.I), {"delta_direction": "reduce"}),
    # Vega: manage / add
    (re.compile(r"\bmanage\b.{0,30}\bvega\b", re.I), {"vega_direction": "reduce"}),
    (re.compile(r"\bvega.{0,30}\bmanage\b", re.I), {"vega_direction": "reduce"}),
    # Theta: protect against / reduce
    (re.compile(r"\bprotect.{0,30}\btheta\b", re.I), {"theta": "reduce"}),
    (re.compile(r"\btheta.{0,30}\bprotect\b", re.I), {"theta": "reduce"}),
    (re.compile(r"\btheta.{0,20}decay\b", re.I), {"theta": "reduce"}),
    # Gamma: neutralize / hedge / reduce
    (re.compile(r"\bneutralize\b.{0,30}\bgamma\b", re.I), {"gamma": "neutral"}),
    (re.compile(r"\bgamma.{0,30}\bneutralize\b", re.I), {"gamma": "neutral"}),
    (re.compile(r"\b(flatten|flat)\b.{0,20}\bgamma\b", re.I), {"gamma": "neutral"}),
    (re.compile(r"\bhedge\b.{0,30}\bgamma\b", re.I), {"gamma": "neutral"}),
    (re.compile(r"\bgamma.{0,30}\bhedge\b", re.I), {"gamma": "neutral"}),
    (re.compile(r"\b(reduce|lower|decrease|cut)\b.{0,30}\bgamma\b", re.I), {"gamma": "reduce"}),
    (re.compile(r"\bgamma.{0,30}\b(reduce|lower|decrease|cut)\b", re.I), {"gamma": "reduce"}),
]


def _load_hints() -> dict:
    """Load keyword_hints from the schema YAML; fall back to empty dict on failure."""
    if not _HAS_YAML or not _SCHEMA_PATH.exists():
        return {}
    try:
        with open(_SCHEMA_PATH, encoding="utf-8") as fh:
            data = _yaml.safe_load(fh)
        return data.get("keyword_hints", {})
    except Exception:
        return {}


_HINTS: dict = _load_hints()


def _match_field(text_lower: str, field: str) -> Optional[str]:
    """Return the first matching enum value for *field*, or None.

    Uses word-boundary regex so 'defined risk' does not match 'undefined risk'.
    """
    for value, phrases in _HINTS.get(field, {}).items():
        for phrase in phrases:
            pattern = r"\b" + re.escape(phrase.lower()) + r"\b"
            if re.search(pattern, text_lower):
                return value
    return None


def _extract_ticker(text: str) -> Optional[str]:
    """Return the first plausible ticker (1-5 uppercase letters) found in text."""
    for m in _TICKER_RE.finditer(text):
        word = m.group()
        if word not in _NON_TICKER:
            return word
    return None


def parse_intent(text: str, *, underlying: Optional[str] = None) -> dict:
    """Parse natural-language trading intent into a structured dict.

    Args:
        text: Natural-language trading thesis.
        underlying: Explicit ticker override; takes precedence over text extraction.

    Returns:
        Dict always containing direction, horizon, iv_view, risk_cap, source,
        raw_text, and optionally underlying and greek_targets.
    """
    safe_text = text or ""
    text_lower = safe_text.lower()

    result: dict = {
        "raw_text": safe_text,
        "direction": _match_field(text_lower, "direction") or _DEFAULTS["direction"],
        "horizon": _match_field(text_lower, "horizon") or _DEFAULTS["horizon"],
        "iv_view": _match_field(text_lower, "iv_view") or _DEFAULTS["iv_view"],
        "risk_cap": _match_field(text_lower, "risk_cap") or _DEFAULTS["risk_cap"],
        "source": "rule",
    }

    # Underlying: explicit kwarg wins over text extraction
    if underlying is not None:
        result["underlying"] = underlying.strip().upper()
    else:
        ticker = _extract_ticker(safe_text)
        if ticker:
            result["underlying"] = ticker

    # Greek targets: merge all matching pattern dicts
    greek_targets: dict = {}
    for pattern, targets in _GREEK_PATTERNS:
        if pattern.search(safe_text):
            greek_targets.update(targets)
    if greek_targets:
        result["greek_targets"] = greek_targets

    return result
