# CUI // SP-CTI
"""Shared types for the agent-readiness pillar system."""
from __future__ import annotations

import json
import pathlib
import re
import threading
from dataclasses import dataclass, field
from typing import Callable, Optional


@dataclass
class CriterionResult:
    criterion_id: str
    passed: bool
    message: str
    details: str = ""
    skipped: bool = False


@dataclass
class Criterion:
    id: str
    name: str
    description: str
    pillar_id: str
    level: int  # 1–5 maturity
    check: Callable[[pathlib.Path], CriterionResult] = field(repr=False)


@dataclass
class Pillar:
    id: str
    name: str
    description: str
    criteria: list[Criterion]

    def run(self, repo_path: pathlib.Path) -> list[CriterionResult]:
        results = []
        for c in self.criteria:
            try:
                results.append(c.check(repo_path))
            except Exception as exc:  # noqa: BLE001
                results.append(
                    CriterionResult(
                        criterion_id=c.id,
                        passed=False,
                        message=f"Check raised an exception: {exc}",
                        skipped=True,
                    )
                )
        return results

    def score(self, results: list[CriterionResult], anomaly_threshold: float = 0.5) -> dict:
        evaluated = [r for r in results if not r.skipped]
        passed = sum(1 for r in evaluated if r.passed)
        total = len(evaluated)
        percentage = round(passed / total, 4) if total > 0 else 0.0
        # Anomaly detection: flag pillars scoring below the configured threshold.
        # Only meaningful when at least one criterion was evaluated (total > 0).
        anomalous = total > 0 and percentage < anomaly_threshold
        return {
            "pillar_id": self.id,
            "passed": passed,
            "total": total,
            "percentage": percentage,
            "anomalous": anomalous,
            "anomaly_threshold": anomaly_threshold,
        }


# ---------------------------------------------------------------------------
# File-system helpers (sync, pure Python — no external deps)
# ---------------------------------------------------------------------------

def _read(repo: pathlib.Path, *rel_parts: str) -> Optional[str]:
    p = repo.joinpath(*rel_parts)
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _exists(repo: pathlib.Path, *globs: str) -> Optional[str]:
    for g in globs:
        if "*" in g or "?" in g:
            hits = list(repo.glob(g))
            if hits:
                return str(hits[0].relative_to(repo))
        else:
            p = repo / g
            if p.exists():
                return g
    return None


def _glob_files(repo: pathlib.Path, pattern: str, ignore_dirs: tuple = ("node_modules", "vendor", ".git")) -> list[pathlib.Path]:
    results = []
    for p in repo.glob(pattern):
        parts = p.parts
        if any(d in parts for d in ignore_dirs):
            continue
        results.append(p)
    return results


def _search(text: str, pattern: str, flags: int = re.IGNORECASE) -> bool:
    return bool(re.search(pattern, text, flags))


# ---------------------------------------------------------------------------
# Anomaly-detection infrastructure
# ---------------------------------------------------------------------------

def _mean_std(values: list[float]) -> tuple[float, float]:
    """Return (mean, population_std) for a non-empty list of floats."""
    n = len(values)
    mean = sum(values) / n
    variance = sum((x - mean) ** 2 for x in values) / n
    return mean, variance ** 0.5


class ThresholdAnomalyDetector:
    """Learns adaptive pass/fail thresholds from observed metric values over time.

    For each named metric, maintains a rolling window of observed values and
    computes an adaptive lower-bound threshold using z-score statistics:

        adaptive_threshold = mean - sigma * std

    A value below the adaptive threshold is treated as statistically anomalous
    relative to the observed distribution. Falls back to the supplied static
    default when fewer than MIN_SAMPLES observations exist.

    State is optionally persisted to a JSON file so learning survives across
    process restarts. All public methods are thread-safe.
    """

    MIN_SAMPLES: int = 5
    WINDOW_SIZE: int = 200
    DEFAULT_SIGMA: float = 1.5

    def __init__(self, store_path: Optional[pathlib.Path] = None) -> None:
        self._store = store_path
        self._lock = threading.Lock()
        self._data: dict[str, list[float]] = self._load()
        self._dirty = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def observe(self, metric: str, value: float) -> None:
        """Record an observed metric value; trims to WINDOW_SIZE."""
        with self._lock:
            window = self._data.setdefault(metric, [])
            window.append(float(value))
            if len(window) > self.WINDOW_SIZE:
                self._data[metric] = window[-self.WINDOW_SIZE :]
            self._dirty = True

    def suggest_threshold(
        self, metric: str, default: float, sigma: float = DEFAULT_SIGMA
    ) -> float:
        """Return an adaptive lower-bound threshold derived from observed history.

        When fewer than MIN_SAMPLES observations are available the static default
        is returned unchanged.  The adaptive value is clamped to
        [default * 0.5, default * 2.0] to prevent unreasonable drift from the
        intended baseline.
        """
        with self._lock:
            window = self._data.get(metric, [])
        if len(window) < self.MIN_SAMPLES:
            return default
        mean, std = _mean_std(window)
        adaptive = mean - sigma * std
        return float(max(default * 0.5, min(adaptive, default * 2.0)))

    def is_anomaly(
        self, metric: str, value: float, default: float, sigma: float = DEFAULT_SIGMA
    ) -> bool:
        """Return True when *value* is below the adaptive (or default) threshold."""
        return value < self.suggest_threshold(metric, default, sigma)

    def stats(self, metric: str) -> dict:
        """Return descriptive statistics for a tracked metric (read-only)."""
        with self._lock:
            window = list(self._data.get(metric, []))
        if not window:
            return {"n": 0, "mean": None, "std": None, "min": None, "max": None}
        mean, std = _mean_std(window)
        return {
            "n": len(window),
            "mean": round(mean, 6),
            "std": round(std, 6),
            "min": round(min(window), 6),
            "max": round(max(window), 6),
        }

    def flush(self) -> None:
        """Persist accumulated observations to disk (no-op when no store_path)."""
        with self._lock:
            if self._dirty and self._store:
                self._save()
                self._dirty = False

    # ------------------------------------------------------------------
    # Persistence helpers
    # ------------------------------------------------------------------

    def _load(self) -> dict[str, list[float]]:
        if self._store and self._store.exists():
            try:
                raw = self._store.read_text(encoding="utf-8")
                data = json.loads(raw)
                if isinstance(data, dict):
                    return {
                        k: [float(v) for v in lst]
                        for k, lst in data.items()
                        if isinstance(lst, list)
                    }
            except Exception:  # noqa: BLE001
                pass
        return {}

    def _save(self) -> None:
        if not self._store:
            return
        try:
            self._store.parent.mkdir(parents=True, exist_ok=True)
            self._store.write_text(
                json.dumps(self._data, indent=2), encoding="utf-8"
            )
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_DETECTOR_LOCK = threading.Lock()
_DETECTOR: Optional[ThresholdAnomalyDetector] = None
_DETECTOR_STORE = (
    pathlib.Path(__file__).parents[5] / "data" / "agent_readiness_thresholds.json"
)


def get_anomaly_detector() -> ThresholdAnomalyDetector:
    """Return the shared per-process ThresholdAnomalyDetector instance (lazy init)."""
    global _DETECTOR
    if _DETECTOR is None:
        with _DETECTOR_LOCK:
            if _DETECTOR is None:
                _DETECTOR = ThresholdAnomalyDetector(store_path=_DETECTOR_STORE)
    return _DETECTOR
