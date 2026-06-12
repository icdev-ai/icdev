# CUI // SP-CTI
"""Signal Prioritization Engine — standalone scoring engine.

Reused by the signal_scout Genesis reflex and the /strategos/signals dashboard.

Usage:
    from tools.intelligence.signal_pricer import SignalPricer, SignalScore

    pricer = SignalPricer()
    score = pricer.score(signal_dict, pir_list, current_pmesii)
    print(score.total, score.rationale)
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple


# ── PMESII-PT keyword map ────────────────────────────────────────────────────

_PMESII_PT: Dict[str, List[str]] = {
    "political": [
        "election", "government", "parliament", "minister", "president", "coup",
        "sanctions", "diplomatic", "treaty", "bilateral", "nato", "un ", "g7", "g20",
        "regime", "opposition", "protest", "legislation", "policy",
    ],
    "military": [
        "troops", "military", "army", "navy", "air force", "missile", "drone",
        "airstrike", "offensive", "defense", "weapon", "combat", "battalion",
        "artillery", "soldier", "brigadier", "general", "tank", "naval",
        "war", "attack", "invasion", "cease-fire", "ceasefire",
    ],
    "economic": [
        "gdp", "inflation", "trade", "sanction", "tariff", "oil price", "gas price",
        "currency", "bank", "financial", "market", "economy", "recession",
        "export", "import", "investment", "supply chain", "commodity", "debt",
    ],
    "social": [
        "civilian", "population", "refugee", "displacement", "humanitarian",
        "protest", "riot", "demonstration", "social", "community", "religion",
        "ethnic", "tribal", "poverty", "healthcare", "education",
    ],
    "information": [
        "cyber", "disinformation", "propaganda", "hack", "breach", "leak",
        "information", "media", "social media", "fake news", "psyop", "signal",
        "intelligence", "surveillance", "wiretap", "intercept", "darkweb",
    ],
    "infrastructure": [
        "pipeline", "power grid", "electricity", "bridge", "road", "railway",
        "airport", "port", "water", "dam", "infrastructure", "factory",
        "hospital", "communication", "internet outage", "blackout",
    ],
    "physical_environment": [
        "terrain", "flood", "earthquake", "storm", "weather", "geography",
        "climate", "drought", "volcano", "wildfire", "river", "mountain",
        "desert", "coast", "forest", "environment",
    ],
    "time": [
        "deadline", "timeline", "schedule", "imminent", "24 hours", "48 hours",
        "hours", "days", "weeks", "months", "anniversary", "window", "phase",
    ],
}

# STANAG 2022 reliability: letter → normalised score (1-6 scale → 0-1)
_STANAG_SCORE: Dict[str, float] = {
    "A": 6 / 6,
    "B": 5 / 6,
    "C": 4 / 6,
    "D": 3 / 6,
    "E": 2 / 6,
    "F": 1 / 6,
}

_SOURCE_GRADE: Dict[str, str] = {
    "reuters world news": "A",
    "reuters": "A",
    "bbc world news": "A",
    "bbc": "A",
    "ap news": "A",
    "associated press": "A",
    "defense one": "B",
    "bellingcat": "B",
    "al jazeera": "B",
    "cisa alerts": "B",
    "cisa": "B",
    "twitter": "E",
    "telegram": "D",
}

# Scoring weights (must sum to 1.0)
_W_POSTERIOR = 0.35
_W_SOURCE = 0.25
_W_RECENCY = 0.20
_W_COVERAGE = 0.20


# ── Output type ──────────────────────────────────────────────────────────────

@dataclass
class SignalScore:
    """Composite signal priority score."""
    total: float                   # 0-10 (composite scaled)
    posterior_shift: float         # 0-1 KL-proxy dimension rarity
    source_discriminability: float # 0-1 STANAG reliability
    temporal_recency: float        # 0-1 exp(-hours/24)
    domain_coverage: float         # 0-1 PIR keyword match
    rationale: str                 # human-readable summary
    stanag_grade: str              # A-F
    pmesii_dims: List[str]         # covered PMESII-PT dimensions
    pir_matches: List[str]         # matched PIR topics


# ── Scoring helpers ──────────────────────────────────────────────────────────

def _classify_pmesii(text: str) -> Dict[str, int]:
    lower = text.lower()
    return {dim: sum(1 for kw in keywords if kw in lower)
            for dim, keywords in _PMESII_PT.items()}


def _posterior_shift(
    text: str, prior_counts: Dict[str, int]
) -> Tuple[float, List[str]]:
    hits = _classify_pmesii(text)
    covered = [dim for dim, h in hits.items() if h > 0]
    if not covered:
        return 0.0, []

    total_prior = sum(prior_counts.values()) + len(_PMESII_PT)
    scores = []
    for dim in covered:
        p = (prior_counts.get(dim, 0) + 1) / total_prior
        scores.append(-math.log(p))

    raw = sum(scores) / len(scores)
    return min(raw / 2.5, 1.0), covered


def _source_reliability(source: str) -> Tuple[float, str]:
    if not source:
        return _STANAG_SCORE["F"], "F"
    lower = source.lower().strip()
    for name, grade in _SOURCE_GRADE.items():
        if lower.startswith(name) or name in lower:
            return _STANAG_SCORE[grade], grade
    for gov_token in ("gov", "mil", ".un.", "nato", "interpol"):
        if gov_token in lower:
            return _STANAG_SCORE["B"], "B"
    return _STANAG_SCORE["F"], "F"


def _temporal_recency(
    signal_date: Optional[str], created_at: Optional[str]
) -> float:
    def _parse(s: str) -> Optional[datetime]:
        if not s:
            return None
        for fmt in (
            "%Y-%m-%dT%H:%M:%SZ",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d %H:%M:%S",
            "%a, %d %b %Y %H:%M:%S %z",
            "%a, %d %b %Y %H:%M:%S GMT",
            "%Y-%m-%d",
        ):
            try:
                dt = datetime.strptime(s, fmt)
                return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
            except ValueError:
                continue
        return None

    dt = _parse(signal_date or "") or _parse(created_at or "")
    if dt is None:
        return 0.5
    hours_old = max((datetime.now(timezone.utc) - dt).total_seconds() / 3600, 0.0)
    return math.exp(-hours_old / 24.0)


def _domain_coverage(
    text: str, pir_list: List[Dict[str, Any]]
) -> Tuple[float, List[str]]:
    if not pir_list:
        return 0.0, []
    lower = text.lower()
    matched: List[str] = []
    for pir in pir_list:
        topic = (pir.get("topic") or "").lower()
        words = [w for w in topic.split() if len(w) >= 4]
        if words and any(w in lower for w in words):
            matched.append((pir.get("topic") or "")[:60])
    return (1.0 if matched else 0.0), matched


# ── Public API ───────────────────────────────────────────────────────────────

class SignalPricer:
    """Score a single signal against active PIRs and a PMESII prior."""

    def score(
        self,
        signal_dict: Dict[str, Any],
        pir_list: List[Dict[str, Any]],
        current_pmesii: Dict[str, int],
    ) -> SignalScore:
        """Compute a SignalScore for *signal_dict*.

        Parameters
        ----------
        signal_dict:
            Must contain: title, body, source, signal_date, created_at.
        pir_list:
            Active PIR/CCIR/EEI dicts; each must have a 'topic' key.
        current_pmesii:
            Prior hit counts per PMESII-PT dimension (from recently processed signals).
            Pass an empty dict to disable posterior-shift weighting.
        """
        text = (signal_dict.get("title") or "") + " " + (signal_dict.get("body") or "")

        ps, dims = _posterior_shift(text, current_pmesii)
        sd, grade = _source_reliability(signal_dict.get("source") or "")
        tr = _temporal_recency(signal_dict.get("signal_date"), signal_dict.get("created_at"))
        dc, pir_matches = _domain_coverage(text, pir_list)

        composite = _W_POSTERIOR * ps + _W_SOURCE * sd + _W_RECENCY * tr + _W_COVERAGE * dc
        total = round(composite * 10, 2)

        parts = [
            f"STANAG-{grade} source ({sd:.2f})",
            f"temporal={tr:.2f}",
        ]
        if dims:
            parts.append(f"PMESII={','.join(dims[:3])}")
        if pir_matches:
            parts.append(f"PIR=✓{pir_matches[0][:30]!r}")
        else:
            parts.append("PIR=no_match")
        rationale = "; ".join(parts)

        return SignalScore(
            total=total,
            posterior_shift=round(ps, 4),
            source_discriminability=round(sd, 4),
            temporal_recency=round(tr, 4),
            domain_coverage=round(dc, 4),
            rationale=rationale,
            stanag_grade=grade,
            pmesii_dims=dims,
            pir_matches=pir_matches,
        )


# ── CLI ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import json  # noqa: E401

    sample = {
        "title": "Ukraine reports Russian missile strike on Kyiv power grid",
        "body": "Ukrainian officials confirmed a missile attack on infrastructure near Kyiv, disrupting electricity supply.",
        "source": "reuters",
        "signal_date": None,
        "created_at": "2026-04-27T10:00:00Z",
    }
    pirs = [
        {"topic": "Enemy missile capabilities"},
        {"topic": "Infrastructure damage in Ukraine"},
    ]
    pricer = SignalPricer()
    result = pricer.score(sample, pirs, {})
    print(json.dumps({
        "total": result.total,
        "posterior_shift": result.posterior_shift,
        "source_discriminability": result.source_discriminability,
        "temporal_recency": result.temporal_recency,
        "domain_coverage": result.domain_coverage,
        "rationale": result.rationale,
        "stanag_grade": result.stanag_grade,
        "pmesii_dims": result.pmesii_dims,
        "pir_matches": result.pir_matches,
    }, indent=2))
