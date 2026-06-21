# CUI // SP-CTI
"""Shared types for the agent-readiness pillar system."""
from __future__ import annotations

import math
import pathlib
import re
from dataclasses import dataclass, field
from typing import Callable, Optional

# ---------------------------------------------------------------------------
# Configuration loading
# ---------------------------------------------------------------------------

_CONFIG_PATH = pathlib.Path(__file__).parents[4] / "args" / "agent_readiness.yaml"
_config_cache: Optional[dict] = None


def _load_config() -> dict:
    global _config_cache
    if _config_cache is not None:
        return _config_cache
    try:
        import yaml  # type: ignore[import]
        with open(_CONFIG_PATH, encoding="utf-8") as fh:
            _config_cache = yaml.safe_load(fh) or {}
    except Exception:
        _config_cache = {}
    return _config_cache


def get_threshold(key: str, default):
    """Return the configured numeric threshold for *key*, falling back to *default*."""
    return _load_config().get("thresholds", {}).get(key, default)


def get_pillar_weights() -> dict[str, float]:
    """Return the configured pillar weight mapping (falls back to empty dict)."""
    return _load_config().get("pillar_weights", {})


# ---------------------------------------------------------------------------
# Anomaly detection — adaptive threshold engine
# ---------------------------------------------------------------------------

class AnomalyDetector:
    """Statistical anomaly detector for pillar score histories.

    Flags score values that deviate significantly from recent history using
    z-score or IQR fencing.  Falls back to the static threshold when the
    history is too short to be meaningful.
    """

    def __init__(self, static_threshold: float = 0.5) -> None:
        cfg = _load_config().get("anomaly_detection", {})
        self.enabled: bool = cfg.get("enabled", True)
        self.method: str = cfg.get("method", "zscore")
        self.zscore_threshold: float = float(cfg.get("zscore_threshold", 2.5))
        self.iqr_multiplier: float = float(cfg.get("iqr_multiplier", 1.5))
        self.min_samples: int = int(cfg.get("min_samples", 5))
        self.window_size: int = int(cfg.get("window_size", 20))
        self.static_threshold: float = static_threshold
        self._history: list[float] = []

    # ------------------------------------------------------------------
    def record(self, score: float) -> None:
        """Append *score* to the rolling history window."""
        self._history.append(score)
        if len(self._history) > self.window_size:
            self._history.pop(0)

    # ------------------------------------------------------------------
    def is_anomalous(self, score: float) -> bool:
        """Return True when *score* is a statistical outlier relative to history."""
        if not self.enabled or len(self._history) < self.min_samples:
            return False
        if self.method == "iqr":
            return self._iqr_flag(score)
        return self._zscore_flag(score)

    def adaptive_threshold(self) -> float:
        """Return an adaptive lower-bound threshold based on history, or the static fallback."""
        if not self.enabled or len(self._history) < self.min_samples:
            return self.static_threshold
        if self.method == "iqr":
            q1, _ = self._quartiles()
            iqr = self._iqr()
            return max(0.0, q1 - self.iqr_multiplier * iqr)
        # zscore: mean minus N*std gives a lower fence
        mean = sum(self._history) / len(self._history)
        std = self._std()
        return max(0.0, mean - self.zscore_threshold * std)

    # ------------------------------------------------------------------
    def _std(self) -> float:
        n = len(self._history)
        if n < 2:
            return 0.0
        mean = sum(self._history) / n
        variance = sum((x - mean) ** 2 for x in self._history) / (n - 1)
        return math.sqrt(variance)

    def _zscore_flag(self, score: float) -> bool:
        mean = sum(self._history) / len(self._history)
        std = self._std()
        if std == 0:
            return False
        return abs((score - mean) / std) > self.zscore_threshold

    def _quartiles(self) -> tuple[float, float]:
        s = sorted(self._history)
        n = len(s)
        mid = n // 2
        q1 = sorted(s[:mid])[len(s[:mid]) // 2]
        q3 = sorted(s[mid + (n % 2):])[len(s[mid + (n % 2):]) // 2]
        return float(q1), float(q3)

    def _iqr(self) -> float:
        q1, q3 = self._quartiles()
        return q3 - q1

    def _iqr_flag(self, score: float) -> bool:
        q1, _ = self._quartiles()
        iqr = self._iqr()
        lower_fence = q1 - self.iqr_multiplier * iqr
        return score < lower_fence


# ---------------------------------------------------------------------------
# Core pillar data types
# ---------------------------------------------------------------------------

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

    def score(self, results: list[CriterionResult]) -> dict:
        evaluated = [r for r in results if not r.skipped]
        passed = sum(1 for r in evaluated if r.passed)
        total = len(evaluated)
        return {
            "pillar_id": self.id,
            "passed": passed,
            "total": total,
            "percentage": round(passed / total, 4) if total > 0 else 0.0,
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
