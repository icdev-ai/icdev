#!/usr/bin/env python3
# CUI // SP-CTI
"""Information Signal Scorer — information warfare pressure index.

Detects information operations signals via NLP-based analysis of news text
and cyber-reconnaissance telemetry:

  rhetoric_score          — inflammatory language intensity
  dehumanization_index    — group-dehumanization pattern density
  cyber_recon_score       — SCADA/ICS probing rate (from sg-cyber-01)
  disinformation_surge    — topic velocity on IW trigger trigrams

Uses HuggingFace transformers when available (distilbert-sst-2 for
sentiment, dslim/bert-base-NER for entity extraction); falls back to
keyword/heuristic mode in air-gap deployments.

─────────────────────────────────────────────────────────────
ALGORITHMS
─────────────────────────────────────────────────────────────
1. Rhetoric Score (0–10)
   ▸ Keyword lexicon: high-intensity escalatory and enemy-vilification terms
   ▸ Sentiment polarity via distilbert-sst-2 (NEGATIVE confidence → 0–10)
   ▸ CAMEO/Goldstein score contribution when supplied
   ▸ rhetoric_score = clamp(
         0.50 × keyword_score
       + 0.35 × sentiment_score
       + 0.15 × goldstein_score,
       0, 10
     )

2. Dehumanization Index (0–10)
   ▸ Animal/vermin metaphors: cockroaches, rats, parasites, vermin, pests
   ▸ Disease/contamination: plague, cancer, infection, filth, purge, cleanse
   ▸ Genocide rhetoric: exterminate, annihilate, wipe out, eliminate the
   ▸ NER group co-occurrence: named group within window of dehumanization term
     (optional; requires transformers)
   ▸ dehumanization_index = clamp(hit_weight_sum / DEHUM_SATURATION × 10, 0, 10)

3. Cyber Recon Score (0–10) — SCADA probing rate (sg-cyber-01)
   ▸ ICS/SCADA protocol probe counts: Modbus (502), DNP3 (20000/19999),
     S7comm (102), OPC-UA (4840), EtherNet/IP (44818), BACnet (47808)
   ▸ One-sided CUSUM on probe_rate vs 30-day neutral baseline:
       S⁺[t] = max(0, S⁺[t-1] + z[t] − k)  k=0.5, H=4.0
   ▸ Honeypot hit rate contributes +20% bonus when > 0
   ▸ cyber_recon_score = clamp(max(S⁺) / H × 10 × (1 + 0.2×bool(honeypot_hits)), 0, 10)

4. Disinformation Surge (0–10)
   ▸ Topic velocity detector for three IW trigger trigram clusters:
       enemy_atrocities:  "commit atrocities", "war crimes committed", "mass murder",
                          "killed civilians", "deliberate targeting", "systematic killing"
       national_defense:  "defending the homeland", "protecting our people",
                          "sacred duty", "fight for survival", "exist as a nation"
       historical_claims: "historically our territory", "ancient homeland",
                          "always been ours", "historical right", "reclaim our land"
   ▸ Window velocity: trigram_hits in current window / window_size
   ▸ Z-score: (v − μ_baseline) / σ_baseline  (30-day baseline, min σ=0.01)
   ▸ disinformation_surge = clamp(max(z_score across topics) / SURGE_Z_MAX × 10, 0, 10)
     where SURGE_Z_MAX = 5.0 (z=5 maps to score=10)

─────────────────────────────────────────────────────────────
COMPOSITE
─────────────────────────────────────────────────────────────
    information_score = (
        0.30 × rhetoric_score
      + 0.30 × dehumanization_index
      + 0.20 × cyber_recon_score
      + 0.20 × disinformation_surge
    )

Score is 0–10, rounded to 2 decimal places.

─────────────────────────────────────────────────────────────
DB INTEGRATION
─────────────────────────────────────────────────────────────
Reads from / writes to `sg_information_signals` and
`sg_information_scores` tables (migration 055).
Falls back gracefully when DB is unavailable.

─────────────────────────────────────────────────────────────
CLI
─────────────────────────────────────────────────────────────
    python tools/intelligence/war_readiness/information_scorer.py --demo --json
    python tools/intelligence/war_readiness/information_scorer.py \\
        --scenario-id <uuid> --json
    python tools/intelligence/war_readiness/information_scorer.py \\
        --params-file signals.json --json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import statistics
import sys
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence


BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.logging.icdev_logger import get_logger  # noqa: E402
logger = get_logger("icdev.intelligence.war_readiness.information_scorer")

# ---------------------------------------------------------------------------
# Optional transformers (air-gap safe)
# ---------------------------------------------------------------------------

_HAS_TRANSFORMERS = False
_sentiment_pipe = None
_ner_pipe = None


def _load_transformers() -> bool:
    global _HAS_TRANSFORMERS, _sentiment_pipe, _ner_pipe
    if _HAS_TRANSFORMERS:
        return True
    try:
        from transformers import pipeline  # type: ignore

        _sentiment_pipe = pipeline(
            "sentiment-analysis",
            model="distilbert-base-uncased-finetuned-sst-2-english",
            truncation=True,
            max_length=512,
        )
        _ner_pipe = pipeline(
            "ner",
            model="dslim/bert-base-NER",
            aggregation_strategy="simple",
            truncation=True,
            max_length=512,
        )
        _HAS_TRANSFORMERS = True
    except Exception:
        pass
    return _HAS_TRANSFORMERS


# ---------------------------------------------------------------------------
# Composite weights
# ---------------------------------------------------------------------------

COMPOSITE_WEIGHTS = {
    "rhetoric":        0.30,
    "dehumanization":  0.30,
    "cyber_recon":     0.20,
    "disinfo_surge":   0.20,
}

# Rhetoric sub-weights
RHETORIC_SUBWEIGHTS = {
    "keyword":    0.50,
    "sentiment":  0.35,
    "goldstein":  0.15,
}

# CUSUM parameters (cyber recon)
CUSUM_K = 0.5   # reference / slack parameter
CUSUM_H = 4.0   # alert threshold

# Dehumanization saturation: raw weight sum → score 10
DEHUM_SATURATION = 15.0

# Disinformation surge: z-score that maps to score 10
SURGE_Z_MAX = 5.0

# ---------------------------------------------------------------------------
# Lexicons
# ---------------------------------------------------------------------------

# Rhetoric: escalatory / vilification language
RHETORIC_KEYWORDS: list[tuple[str, float]] = [
    # Military escalation
    (r"\b(declare[sd]?\s+war|declaration\s+of\s+war)\b", 4.0),
    (r"\b(full[- ]scale\s+(invasion|attack|assault|offensive))\b", 3.5),
    (r"\b(obliterate|annihilate|exterminate|wipe\s+out)\b", 3.0),
    (r"\b(crush(ed|ing)?|destroy(ed|ing)?|devastate[sd]?)\b", 2.0),
    (r"\b(bomb(ing|ed|s)?|shell(ing|ed|s)?|strike[sd]?|barrage[sd]?)\b", 1.5),
    # Enemy vilification
    (r"\b(terrorist[s]?|war[- ]criminal[s]?|butcher[s]?|murderer[s]?)\b", 2.5),
    (r"\b(aggressor[s]?|invader[s]?|occupier[s]?|oppressor[s]?)\b", 2.0),
    (r"\b(enemy\s+(forces|troops|army|nation|state|people))\b", 1.5),
    (r"\b(regime[s]?|puppet[s]?|lackey|stooge[s]?|traitor[s]?)\b", 1.5),
    # Threat / ultimatum language
    (r"\b(ultimatum|last\s+warning|final\s+warning|point\s+of\s+no\s+return)\b", 2.0),
    (r"\b(consequences\s+will\s+be\s+(severe|catastrophic|grave))\b", 1.5),
    (r"\b(red\s+line[s]?\s+(crossed|violated))\b", 2.5),
    # Dehumanizing rhetoric (bonus — also scored in dehumanization)
    (r"\b(vermin|parasite[s]?|cockroach(es)?|rat[s]?\b)", 2.0),
]

# Dehumanization term classes and weights
DEHUM_PATTERNS: list[tuple[str, float, str]] = [
    # Animal / vermin metaphors
    (r"\b(cockroach(es)?|vermin|rats?\b|parasite[s]?|pest[s]?|lice|locust[s]?)\b", 4.0, "animal_vermin"),
    (r"\b(savage[s]?|barbarian[s]?|beast[s]?|animal[s]?\s+(like|in\s+human))\b", 3.0, "animalization"),
    (r"\b(subhuman|non[-\s]?human|less\s+than\s+human|not\s+really\s+human)\b", 5.0, "subhuman"),
    # Disease / contamination metaphors
    (r"\b(plague[sd]?|infection|infestation|cancer\s+(of|on)\s+(the|our))\b", 3.5, "disease"),
    (r"\b(filth(y)?|contaminate[sd]?|pollute[sd]?|taint(ed)?)\b", 2.5, "contamination"),
    (r"\b(cleanse[sd]?|purge[sd]?|purify|purification|cleansing)\b", 4.0, "ethnic_cleansing"),
    # Genocide / elimination rhetoric
    (r"\b(exterminate[sd]?|eradicate[sd]?|eliminate\s+(all|every|the)\s+\w+)\b", 5.0, "elimination"),
    (r"\b(wipe\s+(them|the\s+\w+)\s+out|sweep\s+away\s+(the|these))\b", 4.0, "elimination"),
    (r"\b(final\s+solution|total\s+destruction\s+of\s+(the|their))\b", 5.0, "genocide_rhetoric"),
    # Collective dehumanization
    (r"\b(they\s+(are\s+)?(all|not)\s+(human|people)|them\s+vs\s+us\s+(is\s+)?real)\b", 2.0, "collective"),
    (r"\b(those\s+people\s+(don't|do\s+not)\s+(deserve|count|matter))\b", 3.0, "collective"),
]

# IW topic trigram clusters for disinformation surge detection
DISINFO_TOPICS: dict[str, list[str]] = {
    "enemy_atrocities": [
        r"commit(?:ted|ting)?\s+atrocities",
        r"war\s+crimes?\s+(?:committed|perpetrated|documented)",
        r"mass\s+murder(?:ed|ing)?",
        r"kill(?:ed|ing)\s+civilians",
        r"deliberate\s+targeting\s+of\s+civilians",
        r"systematic\s+killing",
        r"execut(?:ed|ing)\s+(?:prisoners|civilians|hostages)",
        r"ethnic\s+cleansing\s+(?:ongoing|documented|confirmed)",
        r"torture\s+of\s+(?:prisoners|civilians|detainees)",
        r"genocide\s+(?:underway|documented|confirmed|occurring)",
    ],
    "national_defense": [
        r"defending\s+(?:the\s+)?homeland",
        r"protecting\s+our\s+(?:people|nation|country|citizens)",
        r"sacred\s+(?:duty|obligation)\s+to\s+defend",
        r"fight(?:ing)?\s+for\s+(?:our\s+)?survival",
        r"exist(?:ence)?\s+(?:as\s+a\s+nation|of\s+our\s+people)",
        r"no\s+choice\s+but\s+to\s+(?:fight|defend|respond)",
        r"forced\s+to\s+(?:act|respond|defend\s+ourselves)",
        r"special\s+(?:military\s+)?operation\s+to\s+(?:protect|liberate|denazify)",
        r"denazification\s+(?:of|operation)",
        r"liberation\s+of\s+(?:our\s+)?(?:people|brothers|kin)",
    ],
    "historical_claims": [
        r"historically\s+(?:our|Russian|Chinese|Serbian)\s+territory",
        r"ancient\s+homeland",
        r"always\s+been\s+(?:ours|part\s+of)",
        r"historical\s+right\s+to",
        r"reclaim(?:ing)?\s+our\s+(?:land|territory|heritage)",
        r"artificial\s+(?:country|state|border)",
        r"invented\s+(?:nation|people|state)",
        r"never\s+(?:truly\s+)?(?:existed|been\s+independent)",
        r"one\s+people\s+(?:artificially\s+)?divided",
        r"rightful\s+(?:territory|claim|ownership)",
    ],
}

# ICS/SCADA protocol probe signatures
SCADA_PROTOCOLS: list[tuple[str, float]] = [
    ("modbus",         3.5),   # TCP/502 — most common ICS protocol
    ("dnp3",           3.5),   # TCP/20000, 19999 — utilities
    ("s7comm",         4.0),   # TCP/102 — Siemens PLCs (Stuxnet vector)
    ("opcua",          3.0),   # TCP/4840 — unified architecture
    ("ethernetip",     3.0),   # TCP/44818 — Allen Bradley
    ("bacnet",         2.5),   # UDP/47808 — building automation
    ("iec104",         3.5),   # TCP/2404 — power grid
    ("profinet",       3.0),   # UDP/34964 — manufacturing
    ("fins",           2.5),   # UDP/9600 — Omron
]

# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------


def _clamp(v: float, lo: float = 0.0, hi: float = 10.0) -> float:
    return max(lo, min(hi, v))


def _item_hash(text: str, tag: str) -> str:
    return hashlib.sha1(f"{tag}:{text[:200]}".encode(), usedforsecurity=False).hexdigest()  # nosec: B324


def _snippet(text: str, match: re.Match, window: int = 100) -> str:
    start = max(0, match.start() - window)
    end = min(len(text), match.end() + window)
    return text[start:end]


# ---------------------------------------------------------------------------
# 1. Rhetoric Score
# ---------------------------------------------------------------------------


def score_rhetoric(news_items: Sequence[dict]) -> dict:
    """Score inflammatory rhetoric intensity in news items.

    Returns dict with rhetoric_score (0–10) and sub-scores.
    """
    _load_transformers()

    keyword_hits: list[dict] = []
    seen_hashes: set[str] = set()
    raw_keyword_weight = 0.0

    for item in news_items:
        text = item.get("text", "")
        if not text:
            continue
        lower = text.lower()
        for pattern, weight in RHETORIC_KEYWORDS:
            for m in re.finditer(pattern, lower, re.IGNORECASE):
                h = _item_hash(text, f"rhetoric:{pattern}")
                if h in seen_hashes:
                    continue
                seen_hashes.add(h)
                raw_keyword_weight += weight
                keyword_hits.append({
                    "phrase": m.group(0),
                    "weight": weight,
                    "snippet": _snippet(text, m),
                    "source": item.get("source", ""),
                })

    # Keyword score: saturate at weight sum = 20.0
    KEYWORD_SATURATION = 20.0
    keyword_score = _clamp(raw_keyword_weight / KEYWORD_SATURATION * 10.0)

    # Sentiment score via transformers (optional)
    sentiment_score = 0.0
    transformer_used = False
    if _sentiment_pipe is not None and news_items:
        texts = [item.get("text", "")[:512] for item in news_items if item.get("text")]
        if texts:
            try:
                results = _sentiment_pipe(texts)
                neg_confidences = [
                    r["score"] if r["label"] == "NEGATIVE" else (1.0 - r["score"])
                    for r in results
                ]
                mean_neg = statistics.mean(neg_confidences)
                sentiment_score = _clamp(mean_neg * 10.0)
                transformer_used = True
            except Exception:
                pass

    # Goldstein / CAMEO contribution
    goldstein_scores = [
        item.get("goldstein_scale", 0.0)
        for item in news_items
        if item.get("goldstein_scale") is not None
    ]
    goldstein_score = 0.0
    if goldstein_scores:
        mean_g = statistics.mean(goldstein_scores)
        # Goldstein: −10 (conflict) to +10 (cooperation); map negative → high rhetoric
        goldstein_score = _clamp((-mean_g + 10.0) / 20.0 * 10.0)

    # Weighted composite
    raw = (
        RHETORIC_SUBWEIGHTS["keyword"]   * keyword_score
        + RHETORIC_SUBWEIGHTS["sentiment"] * sentiment_score
        + RHETORIC_SUBWEIGHTS["goldstein"] * goldstein_score
    )
    rhetoric_score = _clamp(raw)

    return {
        "rhetoric_score": round(rhetoric_score, 2),
        "keyword_score": round(keyword_score, 2),
        "sentiment_score": round(sentiment_score, 2),
        "goldstein_score": round(goldstein_score, 2),
        "transformer_used": transformer_used,
        "keyword_hit_count": len(keyword_hits),
        "raw_keyword_weight": round(raw_keyword_weight, 2),
        "top_hits": keyword_hits[:10],
    }


# ---------------------------------------------------------------------------
# 2. Dehumanization Index
# ---------------------------------------------------------------------------


def score_dehumanization(news_items: Sequence[dict]) -> dict:
    """Score group-dehumanization pattern density in news items.

    Returns dict with dehumanization_index (0–10) and hit breakdown.
    """
    _load_transformers()

    hits_by_class: dict[str, int] = {}
    hit_details: list[dict] = []
    seen_hashes: set[str] = set()
    raw_weight_sum = 0.0

    for item in news_items:
        text = item.get("text", "")
        if not text:
            continue
        lower = text.lower()

        for pattern, weight, label in DEHUM_PATTERNS:
            for m in re.finditer(pattern, lower, re.IGNORECASE):
                h = _item_hash(text, f"dehum:{pattern}")
                if h in seen_hashes:
                    continue
                seen_hashes.add(h)
                raw_weight_sum += weight
                hits_by_class[label] = hits_by_class.get(label, 0) + 1
                hit_details.append({
                    "class": label,
                    "phrase": m.group(0),
                    "weight": weight,
                    "snippet": _snippet(text, m),
                    "source": item.get("source", ""),
                })

        # NER group co-occurrence bonus (optional transformers)
        if _ner_pipe is not None and text:
            try:
                entities = _ner_pipe(text[:512])
                group_entities = [
                    e for e in entities
                    if e.get("entity_group") in ("ORG", "MISC", "PER", "LOC")
                ]
                for ent in group_entities:
                    # Check if a dehumanizing pattern is near this entity
                    for pattern, weight, label in DEHUM_PATTERNS:
                        if re.search(pattern, lower[max(0, ent["start"] - 150):ent["end"] + 150],
                                     re.IGNORECASE):
                            bonus_h = _item_hash(text, f"ner_bonus:{ent['word']}:{pattern}")
                            if bonus_h not in seen_hashes:
                                seen_hashes.add(bonus_h)
                                raw_weight_sum += weight * 0.5  # 50% bonus for NER co-occurrence
                                hits_by_class[f"ner_{label}"] = hits_by_class.get(f"ner_{label}", 0) + 1
            except Exception:
                pass

    dehumanization_index = _clamp(raw_weight_sum / DEHUM_SATURATION * 10.0)

    return {
        "dehumanization_index": round(dehumanization_index, 2),
        "raw_weight_sum": round(raw_weight_sum, 2),
        "hits_by_class": hits_by_class,
        "total_hits": len(hit_details),
        "transformer_ner_used": _HAS_TRANSFORMERS,
        "top_hits": hit_details[:10],
    }


# ---------------------------------------------------------------------------
# 3. Cyber Recon Score (SCADA probing rate)
# ---------------------------------------------------------------------------


def _cusum_score(series: list[float], k: float = CUSUM_K, h: float = CUSUM_H) -> float:
    """One-sided CUSUM on a z-normalised series. Returns max cumulative sum.

    Uses the first 60% of the series as the stable training window for
    μ/σ estimation, then runs CUSUM over the full series. This prevents
    late-arriving surge values from inflating the baseline and masking alerts.
    """
    if len(series) < 2:
        return 0.0
    train_end = max(2, int(len(series) * 0.6))
    training = series[:train_end]
    mu = statistics.mean(training)
    sigma = statistics.stdev(training) if len(training) > 1 else 1.0
    sigma = max(sigma, 1e-9)

    s_pos = 0.0
    max_s = 0.0
    for v in series:
        z = (v - mu) / sigma
        s_pos = max(0.0, s_pos + z - k)
        max_s = max(max_s, s_pos)
    return max_s


def score_cyber_recon(params: dict) -> dict:
    """Score SCADA/ICS probing rate for cyber reconnaissance signals.

    params keys:
        probe_series        (dict[str, list[float]]): per-protocol probe counts over time
        honeypot_hits       (int):   honeypot trigger count (current window)
        honeypot_baseline   (float): expected honeypot hits per window (baseline)
        scada_probe_rate    (float): aggregate probes/hour (simple scalar, alt to series)
        ics_scan_count      (int):   total ICS scans detected

    Returns dict with cyber_recon_score (0–10) and sub-scores.
    """
    probe_series: dict[str, list[float]] = params.get("probe_series", {})
    honeypot_hits: int = params.get("honeypot_hits", 0)
    honeypot_baseline: float = params.get("honeypot_baseline", 0.0)
    scada_probe_rate: float = params.get("scada_probe_rate", 0.0)
    ics_scan_count: int = params.get("ics_scan_count", 0)

    protocol_scores: dict[str, float] = {}
    cusum_values: dict[str, float] = {}
    alerts: list[str] = []

    # CUSUM per protocol series
    for proto_name, weight in SCADA_PROTOCOLS:
        series = probe_series.get(proto_name, [])
        if series:
            cusum_val = _cusum_score(series)
            cusum_values[proto_name] = round(cusum_val, 3)
            proto_score = _clamp(cusum_val / CUSUM_H * 10.0) * (weight / 4.0)
            protocol_scores[proto_name] = round(proto_score, 3)
            if cusum_val >= CUSUM_H:
                alerts.append(proto_name)

    # Scalar fallback: scada_probe_rate or ics_scan_count
    scalar_score = 0.0
    if not probe_series:
        if scada_probe_rate > 0:
            # Normalise: assume baseline 10 probes/hr → z=0; 100/hr → high
            z = max(0.0, (scada_probe_rate - 10.0) / max(scada_probe_rate * 0.1, 1.0))
            scalar_score = _clamp(z / CUSUM_H * 10.0)
        elif ics_scan_count > 0:
            scalar_score = _clamp(math.log1p(ics_scan_count) / math.log1p(1000) * 10.0)

    # Aggregate: max of protocol CUSUM scores or scalar
    if protocol_scores:
        base_score = max(protocol_scores.values())
    else:
        base_score = scalar_score

    # Honeypot hit bonus (+20% when hits exceed baseline)
    honeypot_bonus = 0.0
    if honeypot_hits > honeypot_baseline:
        honeypot_bonus = min(0.20 * base_score, 2.0)

    cyber_recon_score = _clamp(base_score + honeypot_bonus)

    return {
        "cyber_recon_score": round(cyber_recon_score, 2),
        "base_score": round(base_score, 2),
        "honeypot_bonus": round(honeypot_bonus, 2),
        "protocol_scores": protocol_scores,
        "cusum_values": cusum_values,
        "alerts": alerts,
        "honeypot_hits": honeypot_hits,
        "honeypot_baseline": honeypot_baseline,
    }


# ---------------------------------------------------------------------------
# 4. Disinformation Surge Detector
# ---------------------------------------------------------------------------


def score_disinformation_surge(
    news_items: Sequence[dict],
    baseline_stats: dict | None = None,
) -> dict:
    """Detect topic-velocity surges on IW trigger trigram clusters.

    Compares current-window hit count against a 30-day baseline using
    z-score normalisation.

    params:
        news_items     — current-window articles
        baseline_stats — dict[topic_name, {"mean": float, "std": float}]
                         If None, uses conservative defaults (mean=0.1, std=0.05)

    Returns dict with disinformation_surge (0–10) and per-topic breakdown.
    """
    topic_counts: dict[str, int] = {t: 0 for t in DISINFO_TOPICS}
    topic_snippets: dict[str, list[str]] = {t: [] for t in DISINFO_TOPICS}
    seen_hashes: set[str] = set()

    for item in news_items:
        text = item.get("text", "")
        if not text:
            continue
        lower = text.lower()
        for topic, patterns in DISINFO_TOPICS.items():
            for pattern in patterns:
                for m in re.finditer(pattern, lower, re.IGNORECASE):
                    h = _item_hash(text, f"disinfo:{topic}:{pattern}")
                    if h in seen_hashes:
                        continue
                    seen_hashes.add(h)
                    topic_counts[topic] += 1
                    if len(topic_snippets[topic]) < 3:
                        topic_snippets[topic].append(_snippet(text, m, window=80))

    # Z-score each topic
    window_size = max(len(news_items), 1)
    topic_z: dict[str, float] = {}
    topic_velocities: dict[str, float] = {}

    _defaults = {"mean": 0.1, "std": 0.05}

    for topic, count in topic_counts.items():
        velocity = count / window_size
        topic_velocities[topic] = round(velocity, 4)
        stats = (baseline_stats or {}).get(topic, _defaults)
        mu = stats.get("mean", _defaults["mean"])
        sigma = max(stats.get("std", _defaults["std"]), 0.01)
        z = (velocity - mu) / sigma
        topic_z[topic] = round(z, 3)

    max_z = max(topic_z.values()) if topic_z else 0.0
    disinformation_surge = _clamp(max_z / SURGE_Z_MAX * 10.0)

    # Identify the dominant topic
    dominant_topic = max(topic_z, key=lambda t: topic_z[t]) if topic_z else None

    return {
        "disinformation_surge": round(disinformation_surge, 2),
        "max_z_score": round(max_z, 3),
        "dominant_topic": dominant_topic,
        "topic_counts": topic_counts,
        "topic_velocities": topic_velocities,
        "topic_z_scores": topic_z,
        "topic_snippets": topic_snippets,
    }


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class InformationScoreResult:
    scenario_id: str
    information_score: float
    rhetoric_score: float
    dehumanization_index: float
    cyber_recon_score: float
    disinformation_surge: float
    rhetoric_detail: dict = field(default_factory=dict)
    dehumanization_detail: dict = field(default_factory=dict)
    cyber_recon_detail: dict = field(default_factory=dict)
    disinfo_detail: dict = field(default_factory=dict)
    computed_at: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# DB persistence
# ---------------------------------------------------------------------------


def _persist_score(result: dict) -> None:
    try:
        from tools.db.storage import get_connection  # noqa: PLC0415

        conn = get_connection()
        now = result.get("computed_at", datetime.now(timezone.utc).isoformat())
        conn.execute(
            """
            INSERT INTO sg_information_scores
                (scenario_id, information_score, rhetoric_score,
                 dehumanization_index, cyber_recon_score, disinformation_surge,
                 detail_json, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                result["scenario_id"],
                result["information_score"],
                result["rhetoric_score"],
                result["dehumanization_index"],
                result["cyber_recon_score"],
                result["disinformation_surge"],
                json.dumps({
                    "rhetoric": result.get("rhetoric_detail"),
                    "dehumanization": result.get("dehumanization_detail"),
                    "cyber_recon": result.get("cyber_recon_detail"),
                    "disinfo": result.get("disinfo_detail"),
                }),
                now,
            ),
        )
        conn.commit()
        conn.close()
    except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
        logger.warning("_persist_score: best-effort INSERT into sg_information_scores failed (non-blocking): %s", exc)


def _load_signals_from_db(scenario_id: str) -> dict:
    """Load news items and cyber params from DB for a scenario."""
    try:
        from tools.db.storage import get_connection  # noqa: PLC0415

        conn = get_connection()
        rows = conn.execute(
            """
            SELECT signal_type, payload_json
            FROM sg_information_signals
            WHERE scenario_id = %s
            ORDER BY signal_ts ASC
            """,
            (scenario_id,),
        ).fetchall()
        conn.close()

        news_items = []
        cyber_params: dict = {}
        baseline_stats: dict = {}

        for row in rows:
            sig_type = row[0]
            payload = json.loads(row[1] or "{}")
            if sig_type == "news_item":
                news_items.append(payload)
            elif sig_type == "cyber_recon":
                cyber_params.update(payload)
            elif sig_type == "disinfo_baseline":
                baseline_stats.update(payload)

        return {
            "news_items": news_items,
            "cyber_params": cyber_params,
            "baseline_stats": baseline_stats,
        }
    except Exception:
        return {"news_items": [], "cyber_params": {}, "baseline_stats": {}}


# ---------------------------------------------------------------------------
# Main scoring entry point
# ---------------------------------------------------------------------------


def compute_information_score(params: dict) -> InformationScoreResult:
    """Compute the full information warfare pressure index.

    params keys:
        scenario_id     (str):  UUID for DB audit trail.
        news_items      (list): [{text, date, source, goldstein_scale?}, ...]
        cyber_params    (dict): cyber recon parameters (see score_cyber_recon)
        baseline_stats  (dict): per-topic baseline for disinformation surge
        persist         (bool): write to sg_information_scores (default True)

    Returns InformationScoreResult with information_score (0–10).
    """
    scenario_id = params.get("scenario_id") or str(uuid.uuid4())
    news_items = params.get("news_items") or []
    cyber_params = params.get("cyber_params") or {}
    baseline_stats = params.get("baseline_stats") or {}

    # Load from DB if no direct input
    if not news_items and not cyber_params and scenario_id:
        db_data = _load_signals_from_db(scenario_id)
        news_items = db_data["news_items"]
        cyber_params = db_data.get("cyber_params", {})
        if not baseline_stats:
            baseline_stats = db_data.get("baseline_stats", {})

    rhetoric_result = score_rhetoric(news_items)
    dehum_result = score_dehumanization(news_items)
    cyber_result = score_cyber_recon(cyber_params)
    disinfo_result = score_disinformation_surge(news_items, baseline_stats or None)

    r = rhetoric_result["rhetoric_score"]
    d = dehum_result["dehumanization_index"]
    c = cyber_result["cyber_recon_score"]
    s = disinfo_result["disinformation_surge"]

    information_score = round(
        COMPOSITE_WEIGHTS["rhetoric"]       * r
        + COMPOSITE_WEIGHTS["dehumanization"] * d
        + COMPOSITE_WEIGHTS["cyber_recon"]    * c
        + COMPOSITE_WEIGHTS["disinfo_surge"]  * s,
        2,
    )
    information_score = _clamp(information_score)

    result = InformationScoreResult(
        scenario_id=scenario_id,
        information_score=information_score,
        rhetoric_score=r,
        dehumanization_index=d,
        cyber_recon_score=c,
        disinformation_surge=s,
        rhetoric_detail=rhetoric_result,
        dehumanization_detail=dehum_result,
        cyber_recon_detail=cyber_result,
        disinfo_detail=disinfo_result,
        computed_at=datetime.now(timezone.utc).isoformat(),
    )

    if params.get("persist", True):
        _persist_score(result.to_dict())

    return result


# ---------------------------------------------------------------------------
# Demo scenarios
# ---------------------------------------------------------------------------

_HOSTILE_INVASION_NEWS = [
    {
        "date": "2022-02-24",
        "source": "Reuters",
        "goldstein_scale": -9.0,
        "text": (
            "Russian forces launched a full-scale invasion, declaring they would "
            "obliterate Ukrainian resistance. State media described Ukrainians as "
            "Nazis and cockroaches that must be exterminated to purify the region. "
            "Analysts warn of systematic killing of civilians and ethnic cleansing. "
            "The final warning was ignored and red lines crossed."
        ),
    },
    {
        "date": "2022-02-25",
        "source": "AP",
        "goldstein_scale": -8.5,
        "text": (
            "Troops advancing toward Kyiv shelled residential areas. "
            "State television described Ukrainians as parasites infesting "
            "Russian territory. War crimes committed by invading forces are "
            "being documented by human rights organizations. Genocide underway "
            "according to UN observers. Civilians deliberately targeted in mass murder."
        ),
    },
    {
        "date": "2022-02-26",
        "source": "BBC",
        "goldstein_scale": -7.0,
        "text": (
            "Defending the homeland against an existential threat, Ukrainian forces "
            "repelled attacks. 'We fight for our survival as a nation,' declared the "
            "president. Our sacred duty to defend against these terrorists and war criminals "
            "who seek to exterminate our people and reclaim our historically Ukrainian land. "
            "This is our ancient homeland — always been ours, never theirs."
        ),
    },
]

_HOSTILE_CYBER_PARAMS: dict = {
    "probe_series": {
        "modbus": [12, 10, 11, 13, 12, 14, 11, 45, 87, 134, 201, 185],
        "s7comm": [3, 2, 4, 3, 3, 4, 2, 18, 42, 76, 115, 98],
        "dnp3": [5, 6, 4, 7, 5, 6, 4, 22, 51, 88, 132, 110],
    },
    "honeypot_hits": 47,
    "honeypot_baseline": 3.0,
}

DEMO_PARAMS: dict = {
    "scenario_id": "demo-russia-ukraine-2022-information",
    "persist": False,
    "news_items": _HOSTILE_INVASION_NEWS,
    "cyber_params": _HOSTILE_CYBER_PARAMS,
    "baseline_stats": {
        "enemy_atrocities": {"mean": 0.05, "std": 0.02},
        "national_defense":  {"mean": 0.03, "std": 0.015},
        "historical_claims": {"mean": 0.02, "std": 0.01},
    },
}

DEMO_PEACEFUL_PARAMS: dict = {
    "scenario_id": "demo-nato-solidarity-2024",
    "persist": False,
    "news_items": [
        {
            "date": "2024-07-11",
            "source": "NATO HQ",
            "goldstein_scale": 6.5,
            "text": (
                "Allied leaders reaffirmed the collective defense commitment at the NATO "
                "summit. Diplomatic exchanges were warm and constructive. Member states "
                "pledged cooperation on shared security challenges through dialogue."
            ),
        }
    ],
    "cyber_params": {
        "probe_series": {
            "modbus": [10, 11, 9, 12, 10, 11, 10, 9, 11, 10],
        },
        "honeypot_hits": 1,
        "honeypot_baseline": 2.0,
    },
    "baseline_stats": {},
}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Information Signal Scorer (rhetoric + dehumanization + cyber recon + disinfo surge)"
    )
    p.add_argument("--scenario-id", help="Scenario UUID (loads signals from DB)")
    p.add_argument("--params-file", help="JSON file with full params dict")
    p.add_argument("--demo", action="store_true", help="Run hostile-invasion demo scenario")
    p.add_argument("--demo-peaceful", action="store_true", help="Run peaceful/NATO demo scenario")
    p.add_argument("--no-persist", action="store_true", help="Skip DB write")
    p.add_argument("--json", action="store_true", help="Output JSON")
    return p


def main() -> None:
    args = _build_parser().parse_args()

    if args.demo:
        params = dict(DEMO_PARAMS)
    elif args.demo_peaceful:
        params = dict(DEMO_PEACEFUL_PARAMS)
    elif args.params_file:
        with open(args.params_file, encoding="utf-8") as f:
            params = json.load(f)
    else:
        params = {"scenario_id": args.scenario_id or str(uuid.uuid4())}

    if args.no_persist:
        params["persist"] = False

    result = compute_information_score(params)
    d = result.to_dict()

    if args.json:
        print(json.dumps(d, indent=2))
    else:
        print("\n=== Information Signal Scorer ===")
        print(f"Scenario   : {d['scenario_id']}")
        print(f"Computed   : {d['computed_at']}")
        print(f"\nComposite information_score: {d['information_score']:.2f} / 10")
        print(f"  Rhetoric Score       (×0.30): {d['rhetoric_score']:.2f}")
        print(f"  Dehumanization Index (×0.30): {d['dehumanization_index']:.2f}")
        print(f"  Cyber Recon Score    (×0.20): {d['cyber_recon_score']:.2f}")
        print(f"  Disinformation Surge (×0.20): {d['disinformation_surge']:.2f}")
        rhet = d.get("rhetoric_detail", {})
        print(f"\n  Rhetoric sub-scores: keyword={rhet.get('keyword_score', 0):.2f} "
              f"sentiment={rhet.get('sentiment_score', 0):.2f} "
              f"goldstein={rhet.get('goldstein_score', 0):.2f} "
              f"transformer={'yes' if rhet.get('transformer_used') else 'no'}")
        dehum = d.get("dehumanization_detail", {})
        print(f"  Dehumanization hits: {dehum.get('total_hits', 0)} "
              f"(raw weight sum: {dehum.get('raw_weight_sum', 0):.2f})")
        cyber = d.get("cyber_recon_detail", {})
        if cyber.get("alerts"):
            print(f"  SCADA/ICS alerts: {', '.join(cyber['alerts'])}")
        disinfo = d.get("disinfo_detail", {})
        print(f"  Disinformation dominant topic: {disinfo.get('dominant_topic', 'none')} "
              f"(max z={disinfo.get('max_z_score', 0):.2f})")


if __name__ == "__main__":
    main()
