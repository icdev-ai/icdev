# CUI // SP-CTI
"""Regulatory Foresight — deterministic impact scorer (no LLM, no DB)."""

import json
import re
from pathlib import Path

_CATALOG_PATH = (
    Path(__file__).parent.parent.parent
    / "context"
    / "govcon"
    / "icdev_capability_catalog.json"
)
_CONFIG_PATH = (
    Path(__file__).parent.parent.parent
    / "args"
    / "regulatory_foresight_config.yaml"
)

_DEFAULT_WEIGHTS = {
    "time_to_mandate": 0.40,
    "icdev_impact": 0.35,
    "blast_radius": 0.25,
}


def _load_weights() -> dict:
    try:
        import yaml  # type: ignore

        with open(_CONFIG_PATH, encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        weights = cfg.get("score_weights", {})
        if weights:
            return {k: float(v) for k, v in weights.items()}
    except Exception:
        pass
    return _DEFAULT_WEIGHTS.copy()


def _load_catalog() -> list:
    with open(_CATALOG_PATH, encoding="utf-8") as f:
        cat = json.load(f)
    return cat.get("capabilities", []) + cat.get("products", [])


def _parse_json_field(value) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, ValueError):
            return [value] if value.strip() else []
    return []


def _tokenize(text: str) -> set:
    return {w for w in re.split(r"[\s\-_/,;:.()]+", text.lower()) if len(w) > 2}


class ImpactScorer:
    """Computes three deterministic sub-scores and a weighted composite."""

    def __init__(self):
        self._weights = _load_weights()
        entries = _load_catalog()
        # Per-entry lowercase keyword sets for blast_radius
        self._cap_kw_sets = [
            {kw.lower() for kw in e.get("keywords", [])} for e in entries
        ]
        # Flat union for icdev_impact matching
        self._all_catalog_kws: set = set()
        for kw_set in self._cap_kw_sets:
            self._all_catalog_kws.update(kw_set)

    # ------------------------------------------------------------------
    # Sub-scorers
    # ------------------------------------------------------------------

    def _score_time_to_mandate(self, signal: dict) -> float:
        days = signal.get("time_to_mandate_days")
        try:
            days = int(days)
        except (TypeError, ValueError):
            return 0.1
        if days <= 0:
            return 0.1
        if days < 180:
            return 1.0
        if days < 365:
            return 0.7
        if days < 730:
            return 0.4
        return 0.1

    def _score_icdev_impact(self, signal: dict) -> float:
        frameworks = _parse_json_field(signal.get("affected_frameworks"))
        if not frameworks:
            return 0.0
        # Tokenize each framework name for multi-word overlap (e.g. "NIST 800-53" → {"nist","800"})
        signal_tokens: set = set()
        for fw in frameworks:
            signal_tokens.add(fw.lower())
            signal_tokens.update(_tokenize(fw))
        signal_tokens = {t for t in signal_tokens if len(t) > 2}
        if not signal_tokens:
            return 0.0
        overlap = signal_tokens & self._all_catalog_kws
        return min(1.0, len(overlap) / len(signal_tokens))

    def _score_blast_radius(self, signal: dict) -> float:
        reg_tokens: set = set()
        title = signal.get("title") or ""
        reg_tokens.update(_tokenize(title))
        for fw in _parse_json_field(signal.get("affected_frameworks")):
            reg_tokens.update(_tokenize(fw))
        for area in _parse_json_field(signal.get("icdev_impact_areas")):
            reg_tokens.update(_tokenize(area))
        if not reg_tokens or not self._cap_kw_sets:
            return 0.0
        touched = sum(1 for kw_set in self._cap_kw_sets if kw_set & reg_tokens)
        return min(1.0, touched / len(self._cap_kw_sets))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def score(self, signal: dict) -> dict:
        """Return signal dict with time_to_mandate_score, icdev_impact_score,
        blast_radius_score, and composite_score appended."""
        t = self._score_time_to_mandate(signal)
        i = self._score_icdev_impact(signal)
        b = self._score_blast_radius(signal)
        w = self._weights
        composite = (
            t * w.get("time_to_mandate", 0.40)
            + i * w.get("icdev_impact", 0.35)
            + b * w.get("blast_radius", 0.25)
        )
        result = dict(signal)
        result["time_to_mandate_score"] = round(t, 4)
        result["icdev_impact_score"] = round(i, 4)
        result["blast_radius_score"] = round(b, 4)
        result["composite_score"] = round(min(1.0, composite), 4)
        return result
