# CUI // SP-CTI
"""Oracle Workflow Pattern Lens — Mine frequent multi-step tool sequences.

Analyzes audit_trail and kanban_tasks to surface:
  - Frequent 3–5 step sequential event patterns (sliding window, hash counting)
  - Tool pairs with >80% co-occurrence rate (composition candidates)
  - Tasks that fail then succeed (backlog→in_progress→backlog→done) as
    self-healing candidates

Pattern mining is deterministic. Anomaly thresholds are calibrated at
score-time by an optional LLM step (oracle_anomaly_detection routing,
claude-haiku). Falls back to args/lens_workflow_patterns.yaml / code
defaults when the LLM is unavailable.
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import json
import logging
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from tools.db.storage import get_connection
from tools.oracle.base_lens import BaseLens, OraclePrediction

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Config loader — externalises thresholds to args/lens_workflow_patterns.yaml
# ---------------------------------------------------------------------------

_ARGS_PATH = Path(__file__).parents[3] / "args" / "lens_workflow_patterns.yaml"

_CODE_DEFAULTS: dict[str, object] = {
    "min_pattern_freq": 3,
    "cooccurrence_threshold": 0.80,
    "window_sizes": [3, 4, 5],
    "lookback_days": 30,
    "fallback_freq_denominator": 20.0,
    "fallback_session_denominator": 10.0,
    "min_self_heal_fails": 2.0,
    "self_heal_z_score": 0.0,
    "heal_reflex_min_rate": 0.7,
    "max_ngrams_reported": 50,
    "max_pairs_reported": 100,
    "max_task_types_reported": 10,
    # Kanban history depth cap
    "max_kanban_done": 500,
    # Fraction of automated events in a pattern to classify automation potential as "high"
    "automation_high_ratio": 0.5,
    # Event type classification for automation potential
    "automated_event_types": [
        "code_generated", "test_executed", "test_passed",
        "security_scan", "compliance_check", "deployment_initiated",
        "agent_task_completed",
    ],
    "manual_event_types": [
        "approval_granted", "approval_denied", "decision_made",
    ],
    # Max tokens for the LLM anomaly-threshold calibration call
    "llm_max_tokens": 384,
    # Anomaly-detection population minimums
    "min_population_percentile": 5,
    "min_population_adaptive": 5,
    "min_population_zscore": 3,
    # Minimum population size for _percentile_rank to return a meaningful result
    "min_percentile_rank_population": 2,
    # Adaptive Z multipliers (mean + z*σ threshold derivation)
    "adaptive_z_ngram": 1.0,
    "adaptive_z_cooccurrence": 1.0,
    "adaptive_z_kanban": 1.0,
    "adaptive_z_heal": 0.0,
    # Z-score severity boundaries
    "z_critical": 3.0,
    "z_warning": 2.0,
    # Confidence formula weights
    "pattern_conf_freq_weight": 0.6,
    "pattern_conf_consistency_weight": 0.4,
    "cooccurrence_conf_cap": 0.95,
    "cooccurrence_conf_base": 0.50,
    "cooccurrence_conf_scale": 0.45,
    "task_conf_cap": 0.90,
    "task_conf_base": 0.40,
    "task_conf_scale": 0.60,
    "heal_conf_cap": 0.90,
    "heal_conf_base": 0.40,
    "heal_conf_scale": 0.50,
}


def _load_lens_config() -> dict:
    try:
        import yaml  # optional dep — present in all ICDEV environments
        raw = _ARGS_PATH.read_text(encoding="utf-8")
        return yaml.safe_load(raw) or {}
    except Exception:
        return {}


_cfg = _load_lens_config()

# Minimum number of times a pattern must appear to be reported
_MIN_PATTERN_FREQ: int = int(_cfg.get("min_pattern_freq", _CODE_DEFAULTS["min_pattern_freq"]))
# Co-occurrence threshold above which a pair is flagged as a composition candidate
_COOCCURRENCE_THRESHOLD: float = float(_cfg.get("cooccurrence_threshold", _CODE_DEFAULTS["cooccurrence_threshold"]))
# Window sizes for N-gram mining
_WINDOW_SIZES: tuple[int, ...] = tuple(int(x) for x in _cfg.get("window_sizes", _CODE_DEFAULTS["window_sizes"]))
# How many days of audit history to consider
_LOOKBACK_DAYS: int = int(_cfg.get("lookback_days", _CODE_DEFAULTS["lookback_days"]))
# Fallback denominators used in _score_pattern when no population distribution exists
_FALLBACK_FREQ_DENOMINATOR: float = float(_cfg.get("fallback_freq_denominator", _CODE_DEFAULTS["fallback_freq_denominator"]))
_FALLBACK_SESSION_DENOMINATOR: float = float(_cfg.get("fallback_session_denominator", _CODE_DEFAULTS["fallback_session_denominator"]))
# Self-healing anomaly detection parameters
_MIN_SELF_HEAL_FAILS: float = float(_cfg.get("min_self_heal_fails", _CODE_DEFAULTS["min_self_heal_fails"]))
_SELF_HEAL_Z_SCORE: float = float(_cfg.get("self_heal_z_score", _CODE_DEFAULTS["self_heal_z_score"]))
_HEAL_REFLEX_MIN_RATE: float = float(_cfg.get("heal_reflex_min_rate", _CODE_DEFAULTS["heal_reflex_min_rate"]))
# Reporting caps (top-N) — anomaly candidates beyond these ranks are noise
_MAX_NGRAMS_REPORTED: int = int(_cfg.get("max_ngrams_reported", _CODE_DEFAULTS["max_ngrams_reported"]))
_MAX_PAIRS_REPORTED: int = int(_cfg.get("max_pairs_reported", _CODE_DEFAULTS["max_pairs_reported"]))
_MAX_TASK_TYPES_REPORTED: int = int(_cfg.get("max_task_types_reported", _CODE_DEFAULTS["max_task_types_reported"]))
# Anomaly-detection population minimums
_MIN_POPULATION_PERCENTILE: int = int(_cfg.get("min_population_percentile", _CODE_DEFAULTS["min_population_percentile"]))
_MIN_POPULATION_ADAPTIVE: int = int(_cfg.get("min_population_adaptive", _CODE_DEFAULTS["min_population_adaptive"]))
_MIN_POPULATION_ZSCORE: int = int(_cfg.get("min_population_zscore", _CODE_DEFAULTS["min_population_zscore"]))
# Z-score severity boundaries
_Z_CRITICAL: float = float(_cfg.get("z_critical", _CODE_DEFAULTS["z_critical"]))
_Z_WARNING: float = float(_cfg.get("z_warning", _CODE_DEFAULTS["z_warning"]))
# Confidence formula weights
_PATTERN_CONF_FREQ_WEIGHT: float = float(_cfg.get("pattern_conf_freq_weight", _CODE_DEFAULTS["pattern_conf_freq_weight"]))
_PATTERN_CONF_CONSISTENCY_WEIGHT: float = float(_cfg.get("pattern_conf_consistency_weight", _CODE_DEFAULTS["pattern_conf_consistency_weight"]))
_COOCCURRENCE_CONF_CAP: float = float(_cfg.get("cooccurrence_conf_cap", _CODE_DEFAULTS["cooccurrence_conf_cap"]))
_COOCCURRENCE_CONF_BASE: float = float(_cfg.get("cooccurrence_conf_base", _CODE_DEFAULTS["cooccurrence_conf_base"]))
_COOCCURRENCE_CONF_SCALE: float = float(_cfg.get("cooccurrence_conf_scale", _CODE_DEFAULTS["cooccurrence_conf_scale"]))
_TASK_CONF_CAP: float = float(_cfg.get("task_conf_cap", _CODE_DEFAULTS["task_conf_cap"]))
_TASK_CONF_BASE: float = float(_cfg.get("task_conf_base", _CODE_DEFAULTS["task_conf_base"]))
_TASK_CONF_SCALE: float = float(_cfg.get("task_conf_scale", _CODE_DEFAULTS["task_conf_scale"]))
_HEAL_CONF_CAP: float = float(_cfg.get("heal_conf_cap", _CODE_DEFAULTS["heal_conf_cap"]))
_HEAL_CONF_BASE: float = float(_cfg.get("heal_conf_base", _CODE_DEFAULTS["heal_conf_base"]))
_HEAL_CONF_SCALE: float = float(_cfg.get("heal_conf_scale", _CODE_DEFAULTS["heal_conf_scale"]))
# Adaptive Z multipliers for each scoring path
_ADAPTIVE_Z_NGRAM: float = float(_cfg.get("adaptive_z_ngram", _CODE_DEFAULTS["adaptive_z_ngram"]))
_ADAPTIVE_Z_COOCCURRENCE: float = float(_cfg.get("adaptive_z_cooccurrence", _CODE_DEFAULTS["adaptive_z_cooccurrence"]))
_ADAPTIVE_Z_KANBAN: float = float(_cfg.get("adaptive_z_kanban", _CODE_DEFAULTS["adaptive_z_kanban"]))
_ADAPTIVE_Z_HEAL: float = float(_cfg.get("adaptive_z_heal", _CODE_DEFAULTS["adaptive_z_heal"]))
# Kanban query cap and automation-potential ratio
_MAX_KANBAN_DONE: int = int(_cfg.get("max_kanban_done", _CODE_DEFAULTS["max_kanban_done"]))
_AUTOMATION_HIGH_RATIO: float = float(_cfg.get("automation_high_ratio", _CODE_DEFAULTS["automation_high_ratio"]))
_AUTOMATED_EVENT_TYPES: frozenset[str] = frozenset(
    _cfg.get("automated_event_types", _CODE_DEFAULTS["automated_event_types"])  # type: ignore[arg-type]
)
_MANUAL_EVENT_TYPES: frozenset[str] = frozenset(
    _cfg.get("manual_event_types", _CODE_DEFAULTS["manual_event_types"])  # type: ignore[arg-type]
)
_LLM_MAX_TOKENS: int = int(_cfg.get("llm_max_tokens", _CODE_DEFAULTS["llm_max_tokens"]))
_MIN_PERCENTILE_RANK_POPULATION: int = int(
    _cfg.get("min_percentile_rank_population", _CODE_DEFAULTS["min_percentile_rank_population"])
)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _lookback_ts() -> str:
    cutoff = datetime.now(timezone.utc) - timedelta(days=_LOOKBACK_DAYS)
    return cutoff.isoformat()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ngrams(seq: list[str], n: int) -> list[tuple[str, ...]]:
    """Return all N-grams of length n from seq."""
    return [tuple(seq[i : i + n]) for i in range(len(seq) - n + 1)]


def _percentile_rank(value: float, population: list[float]) -> float:
    """Return [0, 1] fraction of population values ≤ value (anomaly detection)."""
    if len(population) < _MIN_PERCENTILE_RANK_POPULATION:
        return 1.0
    return sum(1 for v in population if v <= value) / len(population)


def _score_pattern(
    freq: int,
    session_count: int,
    freq_population: list[float] | None = None,
    session_population: list[float] | None = None,
) -> float:
    """Compute a [0, 1] confidence score for a pattern.

    Uses percentile rank within observed distributions when ≥5 samples are
    available; normalizes by observed max for sparse (1–4 sample) populations;
    falls back to fixed-denominator normalization only when no population exists.
    """
    if freq_population and len(freq_population) >= _MIN_POPULATION_PERCENTILE:
        freq_score = _percentile_rank(float(freq), freq_population)
    elif freq_population:
        # sparse but non-empty: stay data-driven by normalizing against observed max
        freq_score = min(1.0, float(freq) / max(freq_population))
    else:
        freq_score = min(1.0, freq / _FALLBACK_FREQ_DENOMINATOR)

    if session_population and len(session_population) >= _MIN_POPULATION_PERCENTILE:
        consistency = _percentile_rank(float(session_count), session_population)
    elif session_population:
        # sparse but non-empty: normalize against observed max
        consistency = min(1.0, float(session_count) / max(session_population))
    else:
        consistency = min(1.0, session_count / _FALLBACK_SESSION_DENOMINATOR)

    return round(_PATTERN_CONF_FREQ_WEIGHT * freq_score + _PATTERN_CONF_CONSISTENCY_WEIGHT * consistency, 3)


def _adaptive_threshold(values: list[float], fallback: float, z: float = 1.0) -> float:
    """Return mean + z * population-σ; falls back to `fallback` with fewer than _MIN_POPULATION_ADAPTIVE values."""
    if len(values) < _MIN_POPULATION_ADAPTIVE:
        return fallback
    mu = statistics.mean(values)
    sigma = statistics.pstdev(values)
    return mu + z * sigma


def _z_score_severity(
    value: float,
    population: list[float],
    z_critical: float | None = None,
    z_warning: float | None = None,
) -> str:
    """Severity based on Z-score position above the population mean.

    Accepts calibrated z_critical / z_warning overrides; falls back to
    module-level constants when not provided.
    """
    if len(population) < _MIN_POPULATION_ZSCORE:
        return "info"
    mu = statistics.mean(population)
    sigma = statistics.pstdev(population)
    if sigma == 0:
        return "warning" if value > mu else "info"
    z = (value - mu) / sigma
    crit = z_critical if z_critical is not None else _Z_CRITICAL
    warn = z_warning if z_warning is not None else _Z_WARNING
    if z >= crit:
        return "critical"
    if z >= warn:
        return "warning"
    return "info"


def _automation_potential(pattern: tuple[str, ...]) -> str:
    """Heuristic: rate automation potential based on event types present."""
    hits_auto = sum(1 for e in pattern if e in _AUTOMATED_EVENT_TYPES)
    hits_manual = sum(1 for e in pattern if e in _MANUAL_EVENT_TYPES)
    if hits_manual:
        return "low"
    if len(pattern) > 0 and (hits_auto / len(pattern)) >= _AUTOMATION_HIGH_RATIO:
        return "high"
    return "medium"


# ---------------------------------------------------------------------------
# LLM-Driven Anomaly Threshold Calibration
# ---------------------------------------------------------------------------

def _llm_anomaly_thresholds(
    freq_distribution: list[float],
    cooccurrence_distribution: list[float],
    heal_distribution: list[float] | None = None,
) -> dict[str, float]:
    """Calibrate anomaly thresholds via LLM from observed run-time distributions.

    Calls oracle_anomaly_detection (claude-haiku) with distribution statistics.
    Returns overrides for min_pattern_freq, cooccurrence_threshold, the two
    sparse-population denominators, and (when heal_distribution is provided)
    min_self_heal_fails. Returns {} on any failure so callers fall back to
    module-level constants transparently.
    """
    if not freq_distribution and not cooccurrence_distribution and not heal_distribution:
        return {}

    def _dist_stats(vals: list[float]) -> dict:
        if not vals:
            return {}
        sv = sorted(vals)
        n = len(sv)
        return {
            "count": n,
            "mean": round(statistics.mean(sv), 3),
            "stdev": round(statistics.pstdev(sv), 3),
            "min": round(sv[0], 3),
            "p75": round(sv[min(int(n * 0.75), n - 1)], 3),
            "p90": round(sv[min(int(n * 0.90), n - 1)], 3),
            "max": round(sv[-1], 3),
        }

    summary: dict[str, dict] = {}
    if freq_distribution:
        summary["pattern_frequency"] = _dist_stats(freq_distribution)
    if cooccurrence_distribution:
        summary["cooccurrence_rate"] = _dist_stats(cooccurrence_distribution)
    if heal_distribution:
        summary["heal_fail_counts"] = _dist_stats(heal_distribution)

    # Core keys always requested; heal and Z-score keys added when data is available
    required_keys = [
        ("min_pattern_freq", 1.0, None),
        ("cooccurrence_threshold", 0.0, 1.0),
        ("fallback_freq_denominator", 1.0, None),
        ("fallback_session_denominator", 1.0, None),
        ("z_critical", 1.0, None),
        ("z_warning", 0.5, None),
        ("adaptive_z_ngram", 0.0, None),
        ("adaptive_z_cooccurrence", 0.0, None),
        ("adaptive_z_kanban", 0.0, None),
    ]
    heal_key_desc = ""
    if heal_distribution:
        required_keys.append(("min_self_heal_fails", 1.0, None))
        required_keys.append(("adaptive_z_heal", 0.0, None))
        required_keys.append(("heal_reflex_min_rate", 0.0, 1.0))
        heal_key_desc = (
            '- "min_self_heal_fails" (number ≥ 1): minimum fail-count for a '
            "self-healing candidate to surface (~1 sigma above mean of heal_fail_counts)\n"
            '- "adaptive_z_heal" (float ≥ 0.0): sigma multiplier for self-healing '
            "adaptive fail-count threshold (higher = stricter filter)\n"
            '- "heal_reflex_min_rate" (float 0–1): heal-rate floor above which the '
            "Genesis Heal reflex should be recommended\n"
        )

    try:
        from tools.llm.router import LLMRouter
        from tools.llm.provider import LLMRequest

        router = LLMRouter()
        request = LLMRequest(
            messages=[
                {
                    "role": "user",
                    "content": (
                        "Analyse these workflow-pattern statistical distributions and "
                        "return calibrated anomaly thresholds as a JSON object.\n\n"
                        f"Distributions:\n{json.dumps(summary, indent=2)}\n\n"
                        "Required keys (all positive numbers):\n"
                        '- "min_pattern_freq" (integer ≥ 1): minimum occurrence count '
                        "for a pattern to be anomalously significant (~1 sigma above mean)\n"
                        '- "cooccurrence_threshold" (float 0–1): minimum co-occurrence '
                        "rate to flag a pair as a composition candidate\n"
                        '- "fallback_freq_denominator" (number > 0): sparse-population '
                        "frequency normalisation denominator\n"
                        '- "fallback_session_denominator" (number > 0): sparse-population '
                        "session-count normalisation denominator\n"
                        '- "z_critical" (float ≥ 1.0): Z-score boundary above population '
                        "mean to classify severity as critical\n"
                        '- "z_warning" (float ≥ 0.5): Z-score boundary above population '
                        "mean to classify severity as warning\n"
                        '- "adaptive_z_ngram" (float ≥ 0.0): sigma multiplier for ngram '
                        "adaptive frequency threshold (higher = stricter filter)\n"
                        '- "adaptive_z_cooccurrence" (float ≥ 0.0): sigma multiplier for '
                        "co-occurrence adaptive threshold\n"
                        '- "adaptive_z_kanban" (float ≥ 0.0): sigma multiplier for '
                        "kanban task-type adaptive threshold\n"
                        + heal_key_desc
                        + "\nReturn ONLY valid JSON with these keys, no markdown fences."
                    ),
                }
            ],
            system_prompt=(
                "You are a statistical anomaly-detection expert. "
                "Return a single valid JSON object with the required threshold keys. "
                "All values must be positive numbers within their stated ranges."
            ),
            max_tokens=_LLM_MAX_TOKENS,
            temperature=0.0,
            skip_injection_scan=True,
        )
        response = router.invoke("oracle_anomaly_detection", request)
        raw = (response.content or "").strip()
        if raw.startswith("```"):
            parts = raw.split("```")
            raw = parts[1].lstrip("json").strip() if len(parts) > 1 else raw
        parsed = json.loads(raw)
        result: dict[str, float] = {}
        for key, lo, hi in required_keys:
            val = parsed.get(key)
            if isinstance(val, (int, float)):
                clamped = max(lo, float(val))
                if hi is not None:
                    clamped = min(hi, clamped)
                result[key] = clamped
        return result
    except Exception as exc:
        logger.debug("LLM anomaly threshold calibration skipped: %s", exc)
        return {}


# ---------------------------------------------------------------------------
# Lens
# ---------------------------------------------------------------------------

class WorkflowPatternLens(BaseLens):
    """Mine frequent workflow patterns from audit_trail and kanban_tasks."""

    name = "workflow_pattern"
    description = (
        "Surfaces frequent sequential tool patterns, composition candidates, "
        "and self-healing opportunities from audit and kanban history"
    )

    # ── Phase 1: Analyze ──────────────────────────────────────────────────

    def analyze(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "sessions": {},          # session_id → ordered list of event_types
            "kanban_done": [],       # task rows with status=done
            "failed_then_done": [],  # (actor, action) pairs that failed then completed
        }

        try:
            conn = get_connection()
            self._load_audit_sequences(conn, data)
            self._load_kanban_done(conn, data)
            self._load_self_healing_candidates(conn, data)
            conn.close()
        except Exception as exc:
            logger.warning("WorkflowPatternLens.analyze error: %s", exc)

        return data

    def _load_audit_sequences(self, conn, data: dict) -> None:
        """Load audit_trail rows for last 30 days, grouped into sessions."""
        cutoff = _lookback_ts()
        rows = conn.execute(
            "SELECT event_type, actor, action, session_id, created_at "
            "FROM audit_trail "
            "WHERE created_at >= ? "
            "ORDER BY session_id NULLS LAST, created_at ASC",
            (cutoff,),
        ).fetchall()

        sessions: dict[str, list[str]] = defaultdict(list)
        for row in rows:
            # Fall back to actor as session key when session_id is absent
            key = row["session_id"] or f"actor:{row['actor']}"
            sessions[key].append(row["event_type"])

        # Keep only sessions with enough events for N-gram mining
        data["sessions"] = {
            k: v for k, v in sessions.items() if len(v) >= min(_WINDOW_SIZES)
        }

    def _load_kanban_done(self, conn, data: dict) -> None:
        """Load completed kanban tasks."""
        rows = conn.execute(
            "SELECT id, title, task_type, priority, created_at, completed_at "
            "FROM kanban_tasks "
            "WHERE status = 'done' "
            "ORDER BY created_at DESC "
            "LIMIT ?",
            (_MAX_KANBAN_DONE,),
        ).fetchall()
        data["kanban_done"] = [dict(r) for r in rows]

    def _load_self_healing_candidates(self, conn, data: dict) -> None:
        """Detect actor+action combos that appear under both failed and completed."""
        cutoff = _lookback_ts()

        failed = conn.execute(
            "SELECT actor, action FROM audit_trail "
            "WHERE event_type = 'agent_task_failed' AND created_at >= ?",
            (cutoff,),
        ).fetchall()
        failed_set = {(r["actor"], r["action"]) for r in failed}

        if not failed_set:
            return

        completed = conn.execute(
            "SELECT actor, action FROM audit_trail "
            "WHERE event_type = 'agent_task_completed' AND created_at >= ?",
            (cutoff,),
        ).fetchall()
        completed_set = {(r["actor"], r["action"]) for r in completed}

        # Pairs that appear in both failed and completed → self-healing candidates
        heal_candidates = []
        for actor, action in failed_set & completed_set:
            fail_count = sum(
                1 for r in failed if r["actor"] == actor and r["action"] == action
            )
            success_count = sum(
                1 for r in completed if r["actor"] == actor and r["action"] == action
            )
            heal_candidates.append(
                {
                    "actor": actor,
                    "action": action,
                    "fail_count": fail_count,
                    "success_count": success_count,
                    "heal_rate": round(
                        success_count / max(1, fail_count + success_count), 3
                    ),
                }
            )

        data["failed_then_done"] = sorted(
            heal_candidates, key=lambda x: x["fail_count"], reverse=True
        )

    # ── Phase 2: Score ────────────────────────────────────────────────────

    def _calibrate_anomaly_thresholds(self, analysis: dict[str, Any]) -> dict[str, float]:
        """Pre-scan analysis data to build distributions, then calibrate via LLM.

        Returns a dict of threshold overrides (same keys as _CODE_DEFAULTS).
        Empty dict means fall back to module-level constants.
        """
        sessions = analysis.get("sessions", {})
        heal_candidates = analysis.get("failed_then_done", [])

        if not sessions and not heal_candidates:
            return {}

        freq_dist: list[float] = []
        cooc_dist: list[float] = []

        if sessions:
            # Quick n-gram frequency pre-scan
            raw_freqs: Counter[tuple[str, ...]] = Counter()
            for events in sessions.values():
                for n in _WINDOW_SIZES:
                    for gram in _ngrams(events, n):
                        raw_freqs[gram] += 1
            freq_dist = [float(c) for c in raw_freqs.values()]

            # Quick co-occurrence rate pre-scan
            ev_sess: dict[str, set[str]] = defaultdict(set)
            for sid, events in sessions.items():
                for evt in set(events):
                    ev_sess[evt].add(sid)
            pair_cnts: Counter[tuple[str, str]] = Counter()
            for sid, events in sessions.items():
                uniq = sorted(set(events))
                for i in range(len(uniq)):
                    for j in range(i + 1, len(uniq)):
                        pair_cnts[(uniq[i], uniq[j])] += 1
            cooc_dist = [
                cnt / len(ev_sess[a] | ev_sess[b])
                for (a, b), cnt in pair_cnts.items()
                if ev_sess[a] | ev_sess[b]
            ]

        # Heal-candidate fail-count distribution for min_self_heal_fails calibration
        heal_dist = [float(c["fail_count"]) for c in heal_candidates] if heal_candidates else None

        return _llm_anomaly_thresholds(freq_dist, cooc_dist, heal_dist)

    def score(self, analysis: dict[str, Any]) -> list[OraclePrediction]:
        # Calibrate anomaly thresholds from observed data; falls back to module constants
        calibrated = self._calibrate_anomaly_thresholds(analysis)
        self._calibrated = calibrated  # retained for propose()

        predictions: list[OraclePrediction] = []
        predictions.extend(self._score_ngram_patterns(analysis, calibrated))
        predictions.extend(self._score_cooccurrence_pairs(analysis, calibrated))
        predictions.extend(self._score_kanban_task_patterns(analysis, calibrated))
        predictions.extend(self._score_self_healing(analysis, calibrated))

        # Sort by confidence descending
        predictions.sort(key=lambda p: p.confidence, reverse=True)
        return predictions

    def _score_ngram_patterns(
        self, analysis: dict[str, Any], calibrated: dict[str, float] | None = None
    ) -> list[OraclePrediction]:
        """Mine frequent 3–5 step sequential event patterns."""
        sessions = analysis.get("sessions", {})
        if not sessions:
            return []

        cal = calibrated or {}
        min_freq = int(cal.get("min_pattern_freq", _MIN_PATTERN_FREQ))

        # Count: ngram_tuple → {count, sessions}
        ngram_counts: Counter[tuple[str, ...]] = Counter()
        ngram_session_sets: dict[tuple[str, ...], set[str]] = defaultdict(set)

        for session_id, events in sessions.items():
            seen_in_session: set[tuple[str, ...]] = set()
            for n in _WINDOW_SIZES:
                for gram in _ngrams(events, n):
                    ngram_counts[gram] += 1
                    if gram not in seen_in_session:
                        ngram_session_sets[gram].add(session_id)
                        seen_in_session.add(gram)

        # Adaptive frequency threshold (uses LLM-calibrated values when available)
        all_freqs = [float(c) for c in ngram_counts.values()]
        all_spreads = [float(len(ngram_session_sets[g])) for g in ngram_counts]
        adaptive_z_ngram = cal.get("adaptive_z_ngram", _ADAPTIVE_Z_NGRAM)
        adaptive_min_freq = max(
            float(min_freq),
            _adaptive_threshold(all_freqs, float(min_freq), z=adaptive_z_ngram),
        )

        predictions: list[OraclePrediction] = []
        for gram, freq in ngram_counts.most_common(_MAX_NGRAMS_REPORTED):
            if freq < adaptive_min_freq:
                break
            session_spread = len(ngram_session_sets[gram])
            conf = _score_pattern(freq, session_spread, all_freqs, all_spreads)
            auto_potential = _automation_potential(gram)
            predictions.append(
                OraclePrediction(
                    lens=self.name,
                    title=f"Frequent {len(gram)}-Step Pattern",
                    description=(
                        f"Sequence '{' → '.join(gram)}' appears {freq}× "
                        f"across {session_spread} session(s)"
                    ),
                    confidence=conf,
                    severity="info",
                    category="workflow_pattern",
                    data={
                        "pattern": list(gram),
                        "frequency": freq,
                        "session_spread": session_spread,
                        "automation_potential": auto_potential,
                    },
                )
            )

        return predictions

    def _score_cooccurrence_pairs(
        self, analysis: dict[str, Any], calibrated: dict[str, float] | None = None
    ) -> list[OraclePrediction]:
        """Detect tool pairs with anomalously high co-occurrence as composition candidates."""
        sessions = analysis.get("sessions", {})
        if not sessions:
            return []

        cal = calibrated or {}
        cooc_threshold = cal.get("cooccurrence_threshold", _COOCCURRENCE_THRESHOLD)

        # session sets per event_type
        event_sessions: dict[str, set[str]] = defaultdict(set)
        for session_id, events in sessions.items():
            for evt in set(events):
                event_sessions[evt].add(session_id)

        # Count pair co-occurrences
        pair_counts: Counter[tuple[str, str]] = Counter()
        for session_id, events in sessions.items():
            unique_evts = sorted(set(events))
            for i in range(len(unique_evts)):
                for j in range(i + 1, len(unique_evts)):
                    pair_counts[(unique_evts[i], unique_evts[j])] += 1

        # Adaptive co-occurrence threshold (uses LLM-calibrated values when available)
        _all_rates: list[float] = []
        for (a, b), cnt in pair_counts.items():
            u = len(event_sessions[a] | event_sessions[b])
            if u > 0:
                _all_rates.append(cnt / u)
        adaptive_z_cooccurrence = cal.get("adaptive_z_cooccurrence", _ADAPTIVE_Z_COOCCURRENCE)
        adaptive_cooccurrence = _adaptive_threshold(_all_rates, cooc_threshold, z=adaptive_z_cooccurrence)

        predictions: list[OraclePrediction] = []
        for (evt_a, evt_b), pair_freq in pair_counts.most_common(_MAX_PAIRS_REPORTED):
            union = len(event_sessions[evt_a] | event_sessions[evt_b])
            if union == 0:
                continue
            cooccurrence_rate = pair_freq / union
            if cooccurrence_rate < adaptive_cooccurrence:
                continue
            # Percentile rank within observed distribution → anomaly-detection confidence
            conf = min(_COOCCURRENCE_CONF_CAP, _COOCCURRENCE_CONF_BASE + _percentile_rank(cooccurrence_rate, _all_rates) * _COOCCURRENCE_CONF_SCALE)
            predictions.append(
                OraclePrediction(
                    lens=self.name,
                    title=f"Tool Composition Candidate: {evt_a} + {evt_b}",
                    description=(
                        f"'{evt_a}' and '{evt_b}' co-occur in "
                        f"{cooccurrence_rate:.0%} of sessions ({pair_freq}/{union})"
                    ),
                    confidence=round(conf, 3),
                    severity="info",
                    category="tool_composition_candidate",
                    data={
                        "tool_a": evt_a,
                        "tool_b": evt_b,
                        "cooccurrence_rate": round(cooccurrence_rate, 3),
                        "pair_frequency": pair_freq,
                        "union_sessions": union,
                    },
                )
            )

        return predictions

    def _score_kanban_task_patterns(
        self, analysis: dict[str, Any], calibrated: dict[str, float] | None = None
    ) -> list[OraclePrediction]:
        """Detect recurring task_type patterns among completed kanban tasks."""
        tasks = analysis.get("kanban_done", [])
        cal = calibrated or {}
        min_freq = int(cal.get("min_pattern_freq", _MIN_PATTERN_FREQ))
        if len(tasks) < min_freq:
            return []

        type_counts: Counter[str] = Counter(
            t.get("task_type", "unknown") for t in tasks
        )

        # Adaptive task-type frequency threshold (uses LLM-calibrated values when available)
        all_type_freqs = [float(c) for c in type_counts.values()]
        adaptive_z_kanban = cal.get("adaptive_z_kanban", _ADAPTIVE_Z_KANBAN)
        adaptive_task_min = max(
            float(min_freq),
            _adaptive_threshold(all_type_freqs, float(min_freq), z=adaptive_z_kanban),
        )

        predictions: list[OraclePrediction] = []
        total = len(tasks)
        all_rates = [c / total for c in type_counts.values()]
        for task_type, count in type_counts.most_common(_MAX_TASK_TYPES_REPORTED):
            if count < adaptive_task_min:
                continue
            rate = count / total
            # Percentile rank within task-type rate distribution
            conf = min(_TASK_CONF_CAP, _TASK_CONF_BASE + _percentile_rank(rate, all_rates) * _TASK_CONF_SCALE)
            predictions.append(
                OraclePrediction(
                    lens=self.name,
                    title=f"Recurring Task Type: {task_type}",
                    description=(
                        f"Task type '{task_type}' accounts for {count}/{total} "
                        f"({rate:.0%}) of completed kanban tasks"
                    ),
                    confidence=round(conf, 3),
                    severity="info",
                    category="recurring_task_type",
                    data={
                        "task_type": task_type,
                        "count": count,
                        "total_done": total,
                        "rate": round(rate, 3),
                    },
                )
            )

        return predictions

    def _score_self_healing(
        self, analysis: dict[str, Any], calibrated: dict[str, float] | None = None
    ) -> list[OraclePrediction]:
        """Surface actor+action combos that fail then eventually succeed."""
        candidates = analysis.get("failed_then_done", [])
        predictions: list[OraclePrediction] = []

        cal = calibrated or {}
        min_self_heal_fails = float(cal.get("min_self_heal_fails", _MIN_SELF_HEAL_FAILS))

        # Adaptive fail-count floor (LLM-calibrated baseline when available) + Z-score severity
        all_fail_counts = [float(c["fail_count"]) for c in candidates]
        all_heal_rates = [c["heal_rate"] for c in candidates]
        adaptive_z_heal = cal.get("adaptive_z_heal", _ADAPTIVE_Z_HEAL)
        adaptive_min_fails = max(
            min_self_heal_fails,
            _adaptive_threshold(all_fail_counts, min_self_heal_fails, z=adaptive_z_heal),
        )

        z_crit = cal.get("z_critical") or None
        z_warn = cal.get("z_warning") or None
        for c in candidates:
            if c["fail_count"] < adaptive_min_fails:
                continue
            # Percentile rank of heal rate within observed distribution
            conf = min(_HEAL_CONF_CAP, _HEAL_CONF_BASE + _percentile_rank(c["heal_rate"], all_heal_rates) * _HEAL_CONF_SCALE)
            severity = _z_score_severity(float(c["fail_count"]), all_fail_counts, z_critical=z_crit, z_warning=z_warn)
            predictions.append(
                OraclePrediction(
                    lens=self.name,
                    title=f"Self-Healing Candidate: {c['actor']} / {c['action']!r}",
                    description=(
                        f"'{c['actor']}' failed {c['fail_count']}× then succeeded "
                        f"{c['success_count']}× for action '{c['action']}' "
                        f"(heal rate {c['heal_rate']:.0%})"
                    ),
                    confidence=round(conf, 3),
                    severity=severity,
                    category="self_healing_candidate",
                    data=c,
                )
            )

        return predictions

    # ── Phase 3: Propose ──────────────────────────────────────────────────

    def propose(self, predictions: list[OraclePrediction]) -> list[OraclePrediction]:
        """Enrich each prediction with actionable recommendations."""
        cal = getattr(self, "_calibrated", {})
        heal_reflex_min_rate = float(cal.get("heal_reflex_min_rate", _HEAL_REFLEX_MIN_RATE))
        for pred in predictions:
            if pred.category == "workflow_pattern":
                pattern_str = " → ".join(pred.data.get("pattern", []))
                auto = pred.data.get("automation_potential", "medium")
                pred.recommendations = [
                    f"Template this workflow: '{pattern_str}'",
                    "Add as a reusable goal in goals/manifest.md",
                    f"Automation potential: {auto} — "
                    + (
                        "consider adding to Genesis reflexes"
                        if auto == "high"
                        else "review for semi-automation"
                    ),
                    "Cross-reference with tools/manifest.md to identify missing helpers",
                ]

            elif pred.category == "tool_composition_candidate":
                a = pred.data.get("tool_a", "")
                b = pred.data.get("tool_b", "")
                pred.recommendations = [
                    f"Create a composite tool that runs '{a}' then '{b}' atomically",
                    "Check tools/manifest.md for an existing wrapper before building",
                    "Add the composite call as a goal step to reduce manual chaining",
                    f"Rate: {pred.data.get('cooccurrence_rate', 0):.0%} co-occurrence — "
                    "strong signal for a dedicated pipeline step",
                ]

            elif pred.category == "recurring_task_type":
                task_type = pred.data.get("task_type", "")
                pred.recommendations = [
                    f"Create a standard goal template for '{task_type}' tasks",
                    "Add acceptance criteria checklist to kanban card template",
                    "Consider scheduling recurring tasks in kanban_scheduler.py",
                    "Review common failure modes for this task type in audit_trail",
                ]

            elif pred.category == "self_healing_candidate":
                actor = pred.data.get("actor", "")
                action = pred.data.get("action", "")
                heal_rate = pred.data.get("heal_rate", 0)
                pred.recommendations = [
                    f"Add retry logic for actor='{actor}', action='{action}'",
                    f"Register in Genesis Heal reflex if heal rate ≥ {heal_reflex_min_rate:.0%}"
                    if heal_rate >= heal_reflex_min_rate
                    else f"Monitor: heal rate below {heal_reflex_min_rate:.0%}, manual review recommended",
                    "Investigate root cause of initial failures in audit_trail",
                    "python tools/genesis/reflexes/heal.py --actor "
                    + actor
                    + " --dry-run",
                ]

        return predictions


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.WARNING)
    lens = WorkflowPatternLens()
    preds = lens.run()
    print(json.dumps([p.to_dict() for p in preds], indent=2, default=str))
    print(f"\n# {len(preds)} prediction(s) generated", file=sys.stderr)
