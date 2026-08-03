# CUI // SP-CTI
"""Semantic loop detection for the agent-loop primitive (ars-loop-01).

The agent loop already stops on *no* progress (``stall_threshold``: no novel
successful tool call for N turns) and on exact-duplicate tool calls (control 3
blocks a call repeated ≥5 times with byte-identical inputs). Neither catches the
common failure this module targets: an agent that keeps *moving* — every turn
produces a call that is novel by exact match — while every call is semantically
the same action:

    read_file {"path": "tools/foo.py"}
    read_file {"path": "./tools/foo.py"}
    read_file {"path": "tools\\foo.py"}

    run {"cmd": "pytest tests/test_a.py -v"}
    run {"cmd": "pytest -v tests/test_a.py"}
    run {"cmd": "python -m pytest tests/test_a.py -v"}

Left alone that burns the token budget to its ceiling and reports
``error_max_budget_tokens``, which reads as "task too big" rather than "agent was
stuck". This module returns a distinct signal so the two are separable in
telemetry.

Design notes
------------
* **Arguments AND results must both repeat.** Argument similarity alone is far
  too loose at this string length — ``{"path": "file1.txt"}`` and
  ``{"path": "file2.txt"}`` differ by one character. Requiring the *results* to
  also be near-identical is what keeps legitimate iterative work unflagged: an
  agent fixing a failing test re-runs the same command, but the output changes
  every time (``5 failed`` → ``2 failed`` → ``passed``).
* **Two similarity metrics, averaged.** ``difflib.SequenceMatcher`` alone scores
  ``file1.txt`` vs ``file2.txt`` at ~0.98; token-set Jaccard alone scores the
  cosmetic ``pytest`` variations above at ~0.6. Averaging the two separates the
  cases (0.49 vs 0.75) far better than either does alone.
* **Digits are never normalised away.** Scrubbing numbers would make
  ``5 failed`` and ``2 failed`` identical — i.e. it would erase exactly the
  signal that distinguishes progress from a loop.
* **A cluster, not a pair.** Flagging needs several equivalent calls, spread
  over several distinct turns, covering most of the recent window. One repeated
  ``list_dir`` between real edits is not a loop; a window that is *mostly* one
  repeated action is.

Pure functions, stdlib only — no DB, no LLM, no I/O beyond reading
``args/llm_config.yaml`` for defaults.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any, Iterable, Sequence

__all__ = [
    "DEFAULT_CONFIG",
    "LoopDetection",
    "ToolCallRecord",
    "call_similarity",
    "detect_semantic_loop",
    "load_detector_config",
    "normalize_text",
    "similarity",
]


# ---------------------------------------------------------------------------
# Defaults (overridable via args/llm_config.yaml → agent_loop.loop_detection)
# ---------------------------------------------------------------------------

DEFAULT_CONFIG: dict[str, Any] = {
    # Master switch. Tuned against 302 real Claude Code transcripts from this
    # repo (see tools/llm/loop_detector_tune.py) before being turned on.
    "enabled": True,
    # How many of the most recent tool calls are considered.
    "window": 8,
    # Both argument and result similarity must reach this to call two tool calls
    # equivalent.
    "similarity_threshold": 0.85,
    # Minimum equivalent calls in the window before anything can be flagged.
    "min_cluster_size": 3,
    # Those calls must be spread over at least this many distinct turns, so a
    # single turn that fans out several similar read-only calls is not a loop.
    "min_distinct_turns": 3,
    # ...and must include at least this many *byte-distinct* argument payloads.
    # Byte-identical repeats are already handled by the agent loop's duplicate
    # guard (warn at 3, block at 5) and by stall_threshold; this control exists
    # for the case those two miss — calls that vary just enough to look novel.
    "min_distinct_variants": 2,
    # Fraction of the window the cluster must cover. Below this the agent is
    # doing other things too, which is progress.
    "coverage_ratio": 0.6,
    # Head + tail characters sampled from each tool result. Head alone would
    # compare banner text (e.g. the pytest platform header) and miss the summary
    # line at the end, which is where progress actually shows up.
    "result_sample_chars": 400,
}

# Config keys that carry integer values (everything else is float/bool).
_INT_KEYS = (
    "window",
    "min_cluster_size",
    "min_distinct_turns",
    "min_distinct_variants",
    "result_sample_chars",
)
_FLOAT_KEYS = ("similarity_threshold", "coverage_ratio")


def load_detector_config() -> dict[str, Any]:
    """Return :data:`DEFAULT_CONFIG` overlaid with ``agent_loop.loop_detection``.

    Reads the single canonical LLM config resolved by
    :func:`icdev.tools.llm.config_path.resolve_llm_config_path`. Any failure
    (missing file, bad YAML, missing section) degrades to the built-in defaults —
    a config problem must not disable a safety control silently *or* crash a
    running agent.
    """
    cfg = dict(DEFAULT_CONFIG)
    try:
        import yaml

        from icdev.tools.llm.config_path import resolve_llm_config_path

        path = resolve_llm_config_path()
        if not path.is_file():
            return cfg
        with open(path, encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
        section = (raw.get("agent_loop") or {}).get("loop_detection") or {}
        if "enabled" in section:
            cfg["enabled"] = bool(section["enabled"])
        for key in _INT_KEYS:
            if key in section:
                cfg[key] = int(section[key])
        for key in _FLOAT_KEYS:
            if key in section:
                cfg[key] = float(section[key])
    except Exception:  # noqa: BLE001 — config read must never break the loop
        return dict(DEFAULT_CONFIG)
    return cfg


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------


@dataclass
class ToolCallRecord:
    """One executed tool call, as the agent loop saw it."""

    turn: int
    name: str
    arguments: Any = field(default_factory=dict)
    result: str = ""
    is_error: bool = False


@dataclass
class LoopDetection:
    """Outcome of :func:`detect_semantic_loop`."""

    detected: bool = False
    tool_name: str = ""
    cluster_size: int = 0
    window_size: int = 0
    distinct_turns: int = 0
    distinct_variants: int = 0
    mean_similarity: float = 0.0
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "detected": self.detected,
            "tool_name": self.tool_name,
            "cluster_size": self.cluster_size,
            "window_size": self.window_size,
            "distinct_turns": self.distinct_turns,
            "distinct_variants": self.distinct_variants,
            "mean_similarity": round(self.mean_similarity, 4),
            "reason": self.reason,
        }


# ---------------------------------------------------------------------------
# Normalisation + similarity
# ---------------------------------------------------------------------------

_WS_RE = re.compile(r"\s+")
_SLASHES_RE = re.compile(r"/{2,}")
_DOTSLASH_RE = re.compile(r"(?:\./)+")
_QUOTES = "\"'`"


def normalize_text(text: str, *, sort_tokens: bool = True) -> str:
    """Normalise away spellings that do not change what an action *does*.

    Case, surrounding quotes, Windows vs POSIX separators, redundant ``./``
    prefixes, whitespace runs, and — when ``sort_tokens`` is set — token order,
    so ``pytest x -v`` and ``pytest -v x`` normalise alike.

    Digits and words are left untouched on purpose: ``5 failed`` must not
    normalise to ``2 failed``.
    """
    if not text:
        return ""
    lowered = text.replace("\\", "/").lower()
    # Path spellings, wherever they appear in the token (``path=./tools/foo.py``
    # as much as a bare ``./tools/foo.py``): collapse repeated separators, then
    # drop redundant ``./`` segments.
    lowered = _SLASHES_RE.sub("/", lowered)
    lowered = _DOTSLASH_RE.sub("", lowered)
    tokens = [t.strip(_QUOTES).strip(",;") for t in _WS_RE.split(lowered) if t.strip()]
    cleaned = [tok for tok in tokens if tok]
    if sort_tokens:
        cleaned.sort()
    return " ".join(cleaned)


def _arguments_to_text(arguments: Any) -> str:
    """Flatten a tool-call argument payload to comparable text."""
    if arguments is None:
        return ""
    if isinstance(arguments, str):
        return arguments
    if isinstance(arguments, dict):
        parts = []
        for key in sorted(arguments):
            parts.append(f"{key}={_arguments_to_text(arguments[key])}")
        return " ".join(parts)
    if isinstance(arguments, (list, tuple)):
        return " ".join(_arguments_to_text(item) for item in arguments)
    try:
        return json.dumps(arguments, sort_keys=True, default=str)
    except Exception:  # noqa: BLE001
        return str(arguments)


def _sample_result(text: str, sample_chars: int) -> str:
    """Head + tail sample of a tool result.

    Head-only sampling compares banners (``platform win32 -- Python 3.12 ...``)
    and would call two very different pytest runs identical. The tail carries
    the summary line, which is where progress appears.
    """
    if not text:
        return ""
    if sample_chars <= 0 or len(text) <= sample_chars:
        return text
    half = max(1, sample_chars // 2)
    return text[:half] + " " + text[-half:]


def _jaccard(a_tokens: set[str], b_tokens: set[str]) -> float:
    if not a_tokens and not b_tokens:
        return 1.0
    if not a_tokens or not b_tokens:
        return 0.0
    return len(a_tokens & b_tokens) / len(a_tokens | b_tokens)


def _variant_key(record: ToolCallRecord) -> str:
    """Byte-level identity of a call, matching the loop's own duplicate key.

    Deliberately *not* normalised: two spellings of the same path are two
    variants here, which is what separates "the agent is varying its wording"
    from "the agent is repeating itself verbatim".
    """
    try:
        payload = json.dumps(record.arguments, sort_keys=True, default=str)
    except Exception:  # noqa: BLE001
        payload = str(record.arguments)
    return f"{record.name}:{payload}"


def similarity(left: str, right: str) -> float:
    """Similarity in ``[0, 1]``: mean of character ratio and token-set Jaccard.

    Neither metric works alone at these string lengths. ``SequenceMatcher``
    scores ``file1.txt`` vs ``file2.txt`` at ~0.98 (one character apart);
    Jaccard scores cosmetically-varied commands too low to ever match. Their
    mean separates the two cases.
    """
    a = normalize_text(left)
    b = normalize_text(right)
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    ratio = SequenceMatcher(None, a, b).ratio()
    jac = _jaccard(set(a.split()), set(b.split()))
    return (ratio + jac) / 2.0


def call_similarity(
    left: ToolCallRecord,
    right: ToolCallRecord,
    *,
    result_sample_chars: int = 400,
) -> tuple[float, float]:
    """Return ``(argument_similarity, result_similarity)`` for two tool calls.

    Different tool names are never equivalent — ``(0.0, 0.0)``.
    """
    if left.name != right.name:
        return (0.0, 0.0)
    arg_sim = similarity(_arguments_to_text(left.arguments), _arguments_to_text(right.arguments))
    res_sim = similarity(
        _sample_result(left.result or "", result_sample_chars),
        _sample_result(right.result or "", result_sample_chars),
    )
    return (arg_sim, res_sim)


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


def detect_semantic_loop(
    records: Iterable[ToolCallRecord],
    *,
    config: dict[str, Any] | None = None,
) -> LoopDetection:
    """Detect a run of semantically equivalent tool calls in recent history.

    A detection requires *all* of:

    1. ``min_cluster_size`` calls that are mutually equivalent — same tool, and
       both argument and result similarity at or above ``similarity_threshold``;
    2. those calls spread over at least ``min_distinct_turns`` distinct turns;
    3. at least ``min_distinct_variants`` byte-distinct argument payloads among
       them;
    4. the cluster covering at least ``coverage_ratio`` of the window.

    Conditions 2–4 are the false-positive guards. A single turn that reads four
    similar files in parallel fails (2); byte-identical repeats fail (3) and are
    left to the loop's own duplicate guard, which blocks them and demands a new
    approach; an agent that re-lists a directory between genuine edits fails (4).

    Args:
        records: Tool calls in execution order. Only the last ``window`` are
            considered; callers may pass the full history.
        config:  Overrides for :data:`DEFAULT_CONFIG`. ``None`` loads from
            ``args/llm_config.yaml``.

    Returns:
        :class:`LoopDetection` — ``detected=False`` when nothing qualifies.
    """
    cfg = dict(DEFAULT_CONFIG)
    cfg.update(config or {})

    window_size = max(1, int(cfg["window"]))
    min_cluster = max(2, int(cfg["min_cluster_size"]))
    min_turns = max(1, int(cfg["min_distinct_turns"]))
    min_variants = max(1, int(cfg["min_distinct_variants"]))
    threshold = float(cfg["similarity_threshold"])
    coverage = float(cfg["coverage_ratio"])
    sample_chars = int(cfg["result_sample_chars"])

    window: Sequence[ToolCallRecord] = list(records)[-window_size:]
    if len(window) < min_cluster:
        return LoopDetection(window_size=len(window))

    # Greedy clustering against each cluster's first member. Windows are small
    # (single digits), so the O(n²) comparison is cheap and a smarter algorithm
    # would only obscure what is being compared.
    clusters: list[list[tuple[ToolCallRecord, float]]] = []
    for record in window:
        for cluster in clusters:
            arg_sim, res_sim = call_similarity(
                cluster[0][0], record, result_sample_chars=sample_chars
            )
            if arg_sim >= threshold and res_sim >= threshold:
                cluster.append((record, (arg_sim + res_sim) / 2.0))
                break
        else:
            clusters.append([(record, 1.0)])

    if not clusters:
        return LoopDetection(window_size=len(window))

    best = max(clusters, key=len)
    members = [rec for rec, _ in best]
    distinct_turns = len({rec.turn for rec in members})
    distinct_variants = len({_variant_key(rec) for rec in members})
    mean_sim = sum(score for _, score in best) / len(best)

    detection = LoopDetection(
        tool_name=members[0].name,
        cluster_size=len(best),
        window_size=len(window),
        distinct_turns=distinct_turns,
        distinct_variants=distinct_variants,
        mean_similarity=mean_sim,
    )

    if (
        len(best) >= min_cluster
        and distinct_turns >= min_turns
        and distinct_variants >= min_variants
        and (len(best) / len(window)) >= coverage
    ):
        detection.detected = True
        detection.reason = (
            f"{len(best)}/{len(window)} recent calls to {members[0].name!r} across "
            f"{distinct_turns} turns are semantically equivalent under "
            f"{distinct_variants} different argument spellings "
            f"(mean similarity {mean_sim:.2f} ≥ {threshold})"
        )
    return detection
