# CUI // SP-CTI
"""Shared types for the agent-readiness pillar system."""
from __future__ import annotations

import pathlib
import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Callable, Optional

# ---------------------------------------------------------------------------
# Shared YAML config loader — used by all pillars that define configurable
# anomaly-detection thresholds in args/agent_readiness_config.yaml.
# ---------------------------------------------------------------------------
_ARGS_PATH = pathlib.Path(__file__).parents[4] / "args" / "agent_readiness_config.yaml"


@lru_cache(maxsize=1)
def _load_agent_readiness_config() -> dict:
    """Load the full args/agent_readiness_config.yaml once, cached for the process lifetime."""
    try:
        import yaml  # optional dep — present in all ICDEV environments
        raw = _ARGS_PATH.read_text(encoding="utf-8")
        return yaml.safe_load(raw) or {}
    except Exception:  # noqa: BLE001
        return {}


def load_pillar_config(pillar_key: str) -> dict[str, Any]:
    """Return the pillars.<pillar_key> sub-dict from the config, or {} if absent/malformed.

    Pillar files use this instead of duplicating the YAML-load boilerplate:

        cfg = load_pillar_config("append_only_audit")
        sample_size = int(cfg.get("audit_log_inserts", {}).get("scan_sample_size", 40))
    """
    return _load_agent_readiness_config().get("pillars", {}).get(pillar_key, {})


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
        precision = int(_load_agent_readiness_config().get("score", {}).get("precision", 4))
        return {
            "pillar_id": self.id,
            "passed": passed,
            "total": total,
            "percentage": round(passed / total, precision) if total > 0 else 0.0,
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


_DEFAULT_IGNORE_DIRS = ("node_modules", "vendor", ".git")


def _get_ignore_dirs() -> tuple:
    """Return the configured ignore_dirs for glob scans, falling back to the built-in default."""
    cfg = _load_agent_readiness_config()
    dirs = cfg.get("glob_files", {}).get("ignore_dirs", list(_DEFAULT_IGNORE_DIRS))
    return tuple(dirs) if dirs else _DEFAULT_IGNORE_DIRS


def _glob_files(repo: pathlib.Path, pattern: str, ignore_dirs: Optional[tuple] = None) -> list[pathlib.Path]:
    if ignore_dirs is None:
        ignore_dirs = _get_ignore_dirs()
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
# Lightweight adaptive anomaly detector — per-process singleton.
# Falls back to the static config default when < MIN_SAMPLES observations
# have been recorded.  Pillars call observe() then suggest_threshold().
# ---------------------------------------------------------------------------
class _SimpleAnomalyDetector:
    MIN_SAMPLES = 5

    def __init__(self) -> None:
        self._store: dict[str, list[float]] = {}

    def observe(self, key: str, value: float) -> None:
        self._store.setdefault(key, []).append(float(value))

    def suggest_threshold(self, key: str, default: float, z: float = 2.0) -> float:
        import statistics
        history = self._store.get(key, [])
        if len(history) < self.MIN_SAMPLES:
            return default
        mean = statistics.mean(history)
        stdev = statistics.stdev(history) if len(history) > 1 else 0.0
        return mean + z * stdev


_detector_instance: "_SimpleAnomalyDetector | None" = None


def get_anomaly_detector() -> _SimpleAnomalyDetector:
    global _detector_instance
    if _detector_instance is None:
        _detector_instance = _SimpleAnomalyDetector()
    return _detector_instance
