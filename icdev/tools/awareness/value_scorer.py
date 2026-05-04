#!/usr/bin/env python3
# CUI // SP-CTI
"""Value scoring for suggested kanban cards.

Confidence answers "how sure am I this is a real gap?" — value answers
"how much does fixing it matter?". This module maps the former to the
latter using a simple, transparent, tunable formula:

    value = confidence × rule_weight × (1 + dedup_boost × min(dup_count-1, dedup_cap))

Where:
  * ``rule_weight`` comes from ``args/awareness_config.yaml`` — runtime-
    breakage rules weight higher than discoverability/hygiene rules, and
    noisy rules under refinement weight lower.
  * ``dup_count`` is the number of suggested cards sharing the same
    ``subject`` (the thing that needs to be fixed). One fix that
    remediates N findings ranks higher than N distinct fixes of size 1.
  * ``dedup_boost`` and ``dedup_cap`` are also YAML-tunable.

The config reloads on every call, so operators can adjust the weights
in ``args/awareness_config.yaml`` without restarting the dashboard.
Cost is negligible — a single small YAML parse per suggested-lane page
fetch, not per-row.

Invoked from ``tools/dashboard/api/kanban.py::list_tasks`` when the
client requests ``?sort=value`` or when the kanban board is rendering
the Suggested column.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Dict, Iterable, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent
_CONFIG_PATH = BASE_DIR / "args" / "awareness_config.yaml"

# Safe defaults if the YAML is missing or malformed — the scorer must
# never raise out of the list_tasks hot path.
_FALLBACK_WEIGHTS: Dict[str, float] = {
    "broken_test_reference": 1.50,
    "module_import_broken": 1.50,
    "orphan_db_table": 1.40,
    "route_regression": 1.40,
    "empty_mcp_server": 1.30,
    "tool_not_in_manifest": 1.20,
    "skill_references_missing_goal": 1.20,
    "coherence_new_fail": 1.10,
    "route_no_e2e": 0.90,
    "route_not_listed": 0.60,
    "_default": 1.00,
}
_FALLBACK_DEDUP_BOOST = 0.10
_FALLBACK_DEDUP_CAP = 10


def _read_config_mtime() -> float:
    """Return the config file's mtime, or 0.0 if missing. Used as an
    lru_cache key so edits invalidate the cache automatically."""
    try:
        return _CONFIG_PATH.stat().st_mtime
    except OSError:
        return 0.0


@lru_cache(maxsize=4)
def _load_config(_mtime_key: float) -> Dict:
    """Parse args/awareness_config.yaml with safe fallbacks. The
    ``_mtime_key`` argument is part of the cache key — when the file
    changes on disk, the mtime shifts and a fresh parse runs."""
    try:
        import yaml  # type: ignore
    except ImportError:
        return {
            "weights": _FALLBACK_WEIGHTS,
            "dedup_boost": _FALLBACK_DEDUP_BOOST,
            "dedup_cap": _FALLBACK_DEDUP_CAP,
        }

    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError):
        return {
            "weights": _FALLBACK_WEIGHTS,
            "dedup_boost": _FALLBACK_DEDUP_BOOST,
            "dedup_cap": _FALLBACK_DEDUP_CAP,
        }

    weights = raw.get("value_weights")
    if not isinstance(weights, dict):
        weights = _FALLBACK_WEIGHTS
    # Ensure _default is always present
    if "_default" not in weights:
        weights["_default"] = _FALLBACK_WEIGHTS["_default"]

    boost = raw.get("value_dedup_boost", _FALLBACK_DEDUP_BOOST)
    cap = raw.get("value_dedup_cap", _FALLBACK_DEDUP_CAP)
    try:
        boost = float(boost)
    except (TypeError, ValueError):
        boost = _FALLBACK_DEDUP_BOOST
    try:
        cap = int(cap)
    except (TypeError, ValueError):
        cap = _FALLBACK_DEDUP_CAP

    return {"weights": weights, "dedup_boost": boost, "dedup_cap": cap}


def _current_config() -> Dict:
    return _load_config(_read_config_mtime())


def compute_value(
    confidence: Optional[float],
    rule: Optional[str],
    dup_count: int = 1,
) -> float:
    """Return the value score for a single suggested card.

    Args:
        confidence: Oracle confidence from the originating prediction,
            in [0.0, 1.0]. ``None`` is treated as 0.0.
        rule: The gap or drift rule name (e.g. ``broken_test_reference``).
            Unknown rules fall back to the ``_default`` weight.
        dup_count: Number of suggested cards sharing the same subject.
            Must be >= 1. Out-of-range values are clamped.

    Returns:
        A non-negative float. Higher = more valuable. Typical range is
        0.0 to ~3.0; the theoretical ceiling is
        ``1.0 × max_weight × (1 + dedup_boost × dedup_cap)``.
    """
    cfg = _current_config()
    weights: Dict[str, float] = cfg["weights"]
    boost: float = cfg["dedup_boost"]
    cap: int = cfg["dedup_cap"]

    try:
        conf = float(confidence) if confidence is not None else 0.0
    except (TypeError, ValueError):
        conf = 0.0
    if conf < 0.0:
        conf = 0.0

    weight = float(weights.get(rule or "", weights.get("_default", 1.0)))

    dup = max(1, int(dup_count) if dup_count else 1)
    dedup_multiplier = 1.0 + boost * min(dup - 1, cap)

    return round(conf * weight * dedup_multiplier, 4)


def compute_dup_counts(subjects: Iterable[str]) -> Dict[str, int]:
    """Count how many times each subject appears. The ``subjects`` iterable
    yields a stable subject string per suggested card (typically the
    prediction's ``subject_id``, or a title-extracted fallback). Returns a
    dict mapping subject → count.
    """
    counts: Dict[str, int] = {}
    for s in subjects:
        key = s or ""
        counts[key] = counts.get(key, 0) + 1
    return counts


def extract_subject(title: str, description: Optional[str] = None) -> str:
    """Extract a stable subject string from a gap-detector card title.

    Gap detector titles follow the shape ``[Gap] <rule-label>: <subject>``
    (see ``suggested_card_writer._gap_title``). The substring after the
    first colon is the stable identifier we want for dedup grouping.

    Falls back to the full title if no colon is present, and to empty
    string on null input. Never raises.
    """
    if not title:
        return ""
    if ":" in title:
        return title.split(":", 1)[1].strip()
    return title.strip()


def annotate_tasks_with_value(tasks: List[Dict]) -> None:
    """Mutate a list of suggested-task dicts in place, adding two fields:

      * ``oracle_dup_count`` — number of suggested cards sharing subject
      * ``oracle_value`` — computed value score (float)

    Only cards with a non-None ``oracle_confidence`` get a value score
    (cards that are not Oracle-backed fall through to 0.0 and dup=1).
    """
    # Build subject → count map in one pass
    subjects = [extract_subject(t.get("title") or "") for t in tasks]
    counts = compute_dup_counts(subjects)

    for t, subj in zip(tasks, subjects):
        dup = counts.get(subj, 1)
        conf = t.get("oracle_confidence")
        rule = t.get("oracle_lens")
        t["oracle_dup_count"] = dup
        t["oracle_value"] = compute_value(conf, rule, dup)
