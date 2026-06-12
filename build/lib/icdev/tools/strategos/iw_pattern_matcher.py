#!/usr/bin/env python3
# CUI // SP-CTI
"""STRATEGOS — DTW Pattern Matcher + Weibull Time-to-Event Estimator.

Two analysis components:
  1. DTWPatternMatcher  — Dynamic Time Warping similarity search against the
     23 historical pre-war case signatures from historical_baselines.py.
     Identifies the top-N most similar historical conflicts to the current
     theater signal pattern.

  2. WeibullTTEEstimator  — Fits a Weibull survival model to historical
     time-to-event data (days from first I&W signal to conflict onset) and
     estimates the probability of conflict onset within a given window.

DTW is implemented in pure Python — no scipy/tslearn required.

Usage:
  from tools.strategos.iw_pattern_matcher import DTWPatternMatcher, WeibullTTEEstimator

  matcher = DTWPatternMatcher()
  matches = matcher.find_similar(current_signals, top_n=5)
  for m in matches:
      print(m['case_id'], m['distance'], m['conflict_name'])

  tte = WeibullTTEEstimator()
  tte.fit()
  est = tte.estimate_days_to_onset(current_posterior=0.65)
  print(est['median_days'], est['p90_days'])
"""
from __future__ import annotations

import math
from typing import Any


_FEATURES = [
    "military_buildup",
    "logistics_surge",
    "diplomatic_failure",
    "economic_strain",
    "info_warfare",
    "cyber",
    "nuclear_signaling",
]


# ── Pure-Python DTW ───────────────────────────────────────────────────────────

def _dtw_distance(a: list[float], b: list[float]) -> float:
    """Dynamic Time Warping distance between two equal-length vectors.

    For single-snapshot comparison (not time series), this reduces to
    Euclidean distance — but the DTW framework generalizes to time-window
    comparisons when multi-day signal windows are available.
    """
    n, m = len(a), len(b)
    dtw = [[float("inf")] * (m + 1) for _ in range(n + 1)]
    dtw[0][0] = 0.0
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            cost = abs(a[i - 1] - b[j - 1])
            dtw[i][j] = cost + min(dtw[i - 1][j], dtw[i][j - 1], dtw[i - 1][j - 1])
    return dtw[n][m]


class DTWPatternMatcher:
    """Find historically similar pre-war signal patterns via DTW."""

    def __init__(self) -> None:
        self._matrix: list[dict] = []

    def _load(self) -> None:
        if self._matrix:
            return
        from tools.strategos.historical_baselines import get_pattern_matrix
        self._matrix = get_pattern_matrix()

    def _signals_to_vector(self, signals: dict[str, float]) -> list[float]:
        return [signals.get(f, 0.0) for f in _FEATURES]

    def find_similar(
        self,
        signals: dict[str, float],
        top_n: int = 5,
        exclude_labels: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Return top_n most similar historical cases ranked by DTW distance.

        Args:
            signals: dict mapping feature names to [0,1] scores
            top_n: number of results to return
            exclude_labels: skip cases with these case_ids
        """
        self._load()
        query_vec = self._signals_to_vector(signals)
        exclude = set(exclude_labels or [])
        results = []

        for case in self._matrix:
            if case["case_id"] in exclude:
                continue
            dist = _dtw_distance(query_vec, case["vector"])
            # Similarity score: 1 / (1 + dist)
            similarity = 1.0 / (1.0 + dist)
            results.append({
                "case_id": case["case_id"],
                "conflict_name": case["conflict_name"],
                "year": case["year"],
                "readiness_level": case["readiness_level"],
                "composite": case["composite"],
                "dtw_distance": round(dist, 6),
                "similarity": round(similarity, 4),
            })

        results.sort(key=lambda x: x["dtw_distance"])
        return results[:top_n]

    def analyze_theater(
        self, theater_id: str, top_n: int = 5
    ) -> dict[str, Any]:
        """Load current theater signal scores and run pattern match."""
        from tools.strategos.iw_scorers import score_all_domains
        all_scores = score_all_domains(theater_id)
        dims = all_scores["domains"]
        signals = {
            "military_buildup": dims.get("military", 0.0),
            "logistics_surge": dims.get("military", 0.0) * 0.85,
            "diplomatic_failure": dims.get("political", 0.0),
            "economic_strain": dims.get("economic", 0.0),
            "info_warfare": dims.get("information", 0.0),
            "cyber": dims.get("information", 0.0) * 0.65,
            "nuclear_signaling": 0.0,
        }
        matches = self.find_similar(signals, top_n=top_n)
        avg_readiness = sum(m["readiness_level"] for m in matches) / len(matches) if matches else 0
        return {
            "theater_id": theater_id,
            "current_signals": signals,
            "top_matches": matches,
            "avg_historical_readiness": round(avg_readiness, 2),
            "best_match": matches[0] if matches else None,
        }


# ── Weibull TTE Estimator ─────────────────────────────────────────────────────
#
# Weibull survival model: S(t) = exp(-(t/lambda)^k)
# CDF: F(t) = 1 - exp(-(t/lambda)^k)
# Median: t_50 = lambda * (ln(2))^(1/k)
#
# Fit uses Method of Moments on historical T-minus data:
# How many days before onset did I&W signals reach readiness_level >= 6?
# We use readiness_level as a proxy for signal density — higher level
# = shorter lead time to onset.

_HISTORICAL_LEAD_TIMES: list[tuple[int, float]] = [
    # (days_from_first_signal_to_onset, composite_score)
    (180, 0.65),  # Suez 1956 — 6 months of political maneuvering
    (90, 0.84),   # Six Day War 1967 — 3-month crisis
    (21, 0.75),   # Yom Kippur 1973 — surprise despite indicators
    (60, 0.68),   # Falklands 1982 — 2 months domestic pressure
    (180, 0.88),  # Cuba 1962 — 6 months deployment
    (210, 0.86),  # Gulf War 1991 — Desert Shield
    (30, 0.72),   # Kosovo 1999 — Rambouillet ultimatum
    (365, 0.82),  # Iraq 2003 — UNSCR diplomacy
    (14, 0.79),   # Georgia 2008 — rapid escalation
    (21, 0.81),   # Ukraine 2014 — Maidan → annexation
    (90, 0.93),   # Ukraine 2022 — buildup + exercises
    (45, 0.69),   # Syria 2011 — protest → war
    (30, 0.71),   # Ethiopia 2020 — internal escalation
    (210, 0.79),  # Taiwan Strait 1995 — crisis window
    (14, 0.77),   # Kargil 1999 — infiltration surprise
    (60, 0.63),   # Tanker War 1984 — gradual escalation
    (90, 0.71),   # Strait of Hormuz 2019 — sanctions escalation
    (44, 0.78),   # Nagorno-Karabakh 2020 — rapid conventional
    (7, 0.85),    # NK Crisis 2017 — test window
    (180, 0.91),  # D-Day 1944 — deliberate preparation
]


class WeibullTTEEstimator:
    """Weibull survival model for time-to-conflict-onset estimation."""

    def __init__(self) -> None:
        self._k: float = 1.5     # shape parameter
        self._lam: float = 90.0  # scale parameter (days)
        self._fitted = False

    def fit(self, data: list[tuple[int, float]] | None = None) -> None:
        """Fit Weibull parameters from historical lead-time data."""
        if data is None:
            data = _HISTORICAL_LEAD_TIMES
        if not data:
            return

        times = [float(d[0]) for d in data]
        n = len(times)
        mean = sum(times) / n
        var = sum((t - mean) ** 2 for t in times) / (n - 1) if n > 1 else 1.0
        cv = math.sqrt(var) / mean if mean > 0 else 1.0

        # MOM estimate: k ≈ (1/CV)^1.086 (Menon 1963 approximation)
        self._k = max(0.5, (1.0 / cv) ** 1.086)
        # lambda = mean / Gamma(1 + 1/k)
        self._lam = mean / self._gamma_approx(1 + 1 / self._k)
        self._fitted = True

    def _gamma_approx(self, x: float) -> float:
        """Stirling approximation of Gamma function for x > 0.5."""
        if x < 0.5:
            return math.pi / (math.sin(math.pi * x) * self._gamma_approx(1 - x))
        x -= 1
        a = [
            1.000000000190015,
            76.18009172947146,
            -86.50532032941677,
            24.01409824083091,
            -1.231739572450155,
            1.208650973866179e-3,
            -5.395239384953e-6,
        ]
        t = x + 5.5
        ser = a[0] + sum(a[i + 1] / (x + i + 1) for i in range(6))
        return math.sqrt(2 * math.pi) * (t ** (x + 0.5)) * math.exp(-t) * ser

    def _weibull_cdf(self, t: float) -> float:
        """P(T <= t) under fitted Weibull."""
        if not self._fitted:
            self.fit()
        return 1 - math.exp(-((t / self._lam) ** self._k))

    def _quantile(self, p: float) -> float:
        """Inverse CDF — days such that P(onset within t) = p."""
        if not self._fitted:
            self.fit()
        return self._lam * ((-math.log(1 - p)) ** (1 / self._k))

    def estimate_days_to_onset(
        self,
        current_posterior: float = 0.5,
        windows: list[int] | None = None,
    ) -> dict[str, Any]:
        """Estimate time-to-onset distribution.

        Args:
            current_posterior: P(war) from Bayesian updater (0-1)
            windows: list of day windows to compute P(onset within N days)
        """
        if not self._fitted:
            self.fit()

        if windows is None:
            windows = [7, 14, 30, 60, 90, 180]

        # Scale lambda by posterior: higher P(war) → shorter expected window
        scale_factor = max(0.3, 1.0 - current_posterior * 0.7)
        adjusted_lam = self._lam * scale_factor

        median_days = round(adjusted_lam * (math.log(2) ** (1 / self._k)))
        p90_days = round(adjusted_lam * ((-math.log(0.10)) ** (1 / self._k)))

        window_probs = {}
        for w in windows:
            prob = 1 - math.exp(-((w / adjusted_lam) ** self._k))
            window_probs[f"p_within_{w}d"] = round(prob, 4)

        return {
            "posterior": current_posterior,
            "weibull_k": round(self._k, 4),
            "weibull_lambda_days": round(self._lam, 1),
            "adjusted_lambda_days": round(adjusted_lam, 1),
            "median_days": median_days,
            "p90_days": p90_days,
            "window_probs": window_probs,
            "interpretation": (
                f"At P(war)={current_posterior:.0%}, median onset in ~{median_days}d "
                f"(90th pct: ~{p90_days}d)"
            ),
        }
