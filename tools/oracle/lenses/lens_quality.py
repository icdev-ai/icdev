# CUI // SP-CTI
"""Oracle Quality Lens — Predicts quality regressions and improvement opportunities.

Analyzes QDC gate execution history, UQS trends, and Genesis quality snapshots
to anticipate quality issues before they manifest as CI failures or security incidents.

Predictions feed into:
  - Genesis Quality Reflex (auto-remediation)
  - QDC Dashboard (trend alerts)
  - Innovation Engine (quality improvement signals)

Scanner-tier with optional LLM threshold calibration (oracle_quality_anomaly_detection,
claude-haiku) when quality distributions are available. Falls back to statistical
methods on any LLM error; zero LLM cost when calibration is disabled.
Threshold configuration: args/oracle_quality_config.yaml
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import json
import statistics as _stats
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from tools.db.storage import get_connection

from tools.oracle.base_lens import BaseLens, OraclePrediction

logger = get_logger(__name__)

ICDEV_ROOT = Path(__file__).resolve().parents[3]
QDC_DB = ICDEV_ROOT / "data" / "qdc_canvas.db"
GENESIS_QDB = ICDEV_ROOT / "data" / "genesis_quality.db"
_CONFIG_PATH = ICDEV_ROOT / "args" / "oracle_quality_config.yaml"


def _load_config() -> dict[str, Any]:
    try:
        with open(_CONFIG_PATH, encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    except Exception as exc:
        logger.warning("oracle_quality_config.yaml unreadable, using defaults: %s", exc)
        return {}


_CFG = _load_config()
_AB = _CFG.get("anomaly_bounds", {})
_UQS = _CFG.get("uqs", {})
_GF = _CFG.get("gate_failure", {})
_FS = _CFG.get("findings_spike", {})
_CT = _CFG.get("critical_trends", {})
_EWMA_CFG = _CFG.get("ewma", {})
_MAD_CFG = _CFG.get("mad", {})

# Resolved constants (config value → hardcoded fallback)
_Z_WARN: float = float(_AB.get("z_warn", 1.5))
_Z_CRIT: float = float(_AB.get("z_crit", 2.5))
_MIN_SAMPLES: int = int(_AB.get("min_samples", 3))

# Adaptive z-score range: thresholds scale linearly with coefficient of variation.
# CV≈0 (stable series) → tight bounds; CV≥1 (volatile series) → loose bounds.
_Z_WARN_LOW: float = float(_AB.get("z_warn_low", 1.2))
_Z_WARN_HIGH: float = float(_AB.get("z_warn_high", 2.0))
_Z_CRIT_LOW: float = float(_AB.get("z_crit_low", 2.0))
_Z_CRIT_HIGH: float = float(_AB.get("z_crit_high", 3.0))

# IQR (Tukey fence) anomaly detection parameters
_IQR_WARN_MUL: float = float(_AB.get("iqr_warn_multiplier", 1.5))
_IQR_CRIT_MUL: float = float(_AB.get("iqr_crit_multiplier", 3.0))
_IQR_PSEUDO_CV: float = float(_AB.get("iqr_pseudo_cv", 0.1))
_IQR_TO_SIGMA: float = float(_AB.get("iqr_to_sigma", 1.35))

_UQS_Z_WARN_MUL: float = float(_UQS.get("z_warn_multiplier", 1.0))
_UQS_Z_CRIT_MUL: float = float(_UQS.get("z_crit_multiplier", 2.0))
_UQS_WARN_FLOOR: float = float(_UQS.get("warn_delta_floor", 2.0))
_UQS_CRIT_FLOOR: float = float(_UQS.get("crit_delta_floor", 5.0))

_RATE_WARN_FB: float = float(_GF.get("rate_warn_fallback", 0.45))
_RATE_CRIT_FB: float = float(_GF.get("rate_crit_fallback", 0.75))
_RATE_FLOOR: float = float(_GF.get("rate_floor", 0.35))
_RATE_MIN_FAIL: int = int(_GF.get("min_failure_count", 2))

_UQS_CONF_BASE: float = float(_UQS.get("conf_base", 0.5))
_UQS_CONF_SIGMA_DENOM: float = float(_UQS.get("conf_sigma_denom", 4.0))
_UQS_CONF_CAP: float = float(_UQS.get("conf_cap", 0.95))
_UQS_SCORE_WINDOW: int = int(_UQS.get("score_window", 5))
_UQS_RECENT_WINDOW: int = int(_UQS.get("recent_window", 3))

_GATE_CONF_BASE: float = float(_GF.get("conf_base", 0.4))
_GATE_CONF_RATE_SCALE: float = float(_GF.get("conf_rate_scale", 0.5))
_GATE_CONF_CAP: float = float(_GF.get("conf_cap", 0.90))

_SPIKE_FB_MUL: float = float(_FS.get("spike_fallback_multiplier", 1.5))
_SPIKE_ABS_FLOOR: float = float(_FS.get("min_abs_floor", 5.0))
_SPIKE_MEAN_RATIO: float = float(_FS.get("min_abs_mean_ratio", 0.5))
_SPIKE_MEAN_FALLBACK: float = float(_FS.get("mean_fallback", 10.0))
_SPIKE_CONF_BASE: float = float(_FS.get("conf_base", 0.5))
_SPIKE_CONF_SLOPE: float = float(_FS.get("confidence_z_slope", 0.08))
_SPIKE_CONF_CAP: float = float(_FS.get("conf_cap", 0.95))

_CT_BASE_CONF: float = float(_CT.get("base_confidence", 0.70))
_CT_CONF_PER: float = float(_CT.get("confidence_per_critical", 0.05))
_CT_CONF_CAP: float = float(_CT.get("conf_cap", 0.95))

# EWMA smoothing for UQS trend detection
_USE_EWMA: bool = bool(_EWMA_CFG.get("enabled", True))
_EWMA_ALPHA: float = float(_EWMA_CFG.get("alpha", 0.3))

# MAD robust std estimator (fallback replaces hardcoded 5.0)
_MAD_ENABLED: bool = bool(_MAD_CFG.get("enabled", True))
_MAD_FALLBACK_STD: float = float(_MAD_CFG.get("fallback_std", 5.0))

# LLM anomaly threshold calibration (optional claude-haiku call)
_LLM_CAL_CFG = _CFG.get("llm_calibration", {})
_LLM_CAL_ENABLED: bool = bool(_LLM_CAL_CFG.get("enabled", True))
_LLM_CAL_FN: str = str(_LLM_CAL_CFG.get("function_name", "oracle_quality_anomaly_detection"))
_LLM_CAL_MAX_TOKENS: int = int(_LLM_CAL_CFG.get("max_tokens", 256))
_LLM_CAL_TEMP: float = float(_LLM_CAL_CFG.get("temperature", 0.0))
_LLM_CAL_VAL = _LLM_CAL_CFG.get("validation", {})
_LLM_VAL_UQS_WARN_MIN: float = float(_LLM_CAL_VAL.get("uqs_warn_delta_min", 0.5))
_LLM_VAL_UQS_CRIT_MIN: float = float(_LLM_CAL_VAL.get("uqs_crit_delta_min", 1.0))
_LLM_VAL_RATE_WARN_MIN: float = float(_LLM_CAL_VAL.get("gate_rate_warn_min", 0.10))
_LLM_VAL_RATE_CRIT_MIN: float = float(_LLM_CAL_VAL.get("gate_rate_crit_min", 0.20))
_LLM_VAL_SPIKE_MUL_MIN: float = float(_LLM_CAL_VAL.get("spike_multiplier_min", 1.1))
_LLM_VAL_SPIKE_MUL_MAX: float = float(_LLM_CAL_VAL.get("spike_multiplier_max", 10.0))


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _compute_stats(values: list[float]) -> dict[str, float]:
    """Return mean, std, min, max, n for a series using only built-ins."""
    n = len(values)
    if n == 0:
        return {"mean": 0.0, "std": 0.0, "min": 0.0, "max": 0.0, "n": 0}
    mean = sum(values) / n
    variance = sum((v - mean) ** 2 for v in values) / n
    return {
        "mean": mean,
        "std": variance ** 0.5,
        "min": min(values),
        "max": max(values),
        "n": float(n),
    }


def _zscore(value: float, stats: dict[str, float]) -> float:
    """Z-score of value; returns 0.0 when std is negligible."""
    if stats["std"] < 1e-9:
        return 0.0
    return (value - stats["mean"]) / stats["std"]


# Externally visible defaults — keys mirror oracle_quality_config.yaml [gate_failure] fields
_QUALITY_CONFIG_DEFAULTS: dict[str, Any] = {
    "gate_rate_warn": _RATE_FLOOR,
    "gate_min_count": _RATE_MIN_FAIL,
    "gate_rate_crit": _RATE_CRIT_FB,
}


def _load_quality_config() -> dict[str, Any]:
    """Re-read quality lens gate thresholds from oracle_quality_config.yaml; fall back to defaults."""
    cfg = dict(_QUALITY_CONFIG_DEFAULTS)
    config_path = ICDEV_ROOT / "args" / "oracle_quality_config.yaml"
    if config_path.exists():
        try:
            with open(config_path, encoding="utf-8") as fh:
                loaded = yaml.safe_load(fh) or {}
            gf = loaded.get("gate_failure", {})
            if "rate_floor" in gf:
                cfg["gate_rate_warn"] = float(gf["rate_floor"])
            if "min_failure_count" in gf:
                cfg["gate_min_count"] = int(gf["min_failure_count"])
            if "rate_crit_fallback" in gf:
                cfg["gate_rate_crit"] = float(gf["rate_crit_fallback"])
        except Exception as exc:
            logger.warning("Failed to reload oracle_quality_config.yaml: %s", exc)
    return cfg


def _adaptive_z_thresholds(values: list[float]) -> tuple[float, float]:
    """Derive z-score thresholds scaled by series volatility (coefficient of variation).

    Stable series (CV near 0) get tighter thresholds; volatile series (CV ≥ 1) get
    looser thresholds.  Clamps CV to [0, 1] and interpolates linearly between the
    configured low/high ends.  Falls back to (_Z_WARN, _Z_CRIT) when insufficient
    data or mean is zero.
    """
    if len(values) < _MIN_SAMPLES:
        return _Z_WARN, _Z_CRIT
    mean = _stats.mean(values)
    if mean == 0.0:
        return _Z_WARN, _Z_CRIT
    cv = min(1.0, _stats.pstdev(values) / abs(mean))
    z_warn = _Z_WARN_LOW + (_Z_WARN_HIGH - _Z_WARN_LOW) * cv
    z_crit = _Z_CRIT_LOW + (_Z_CRIT_HIGH - _Z_CRIT_LOW) * cv
    return z_warn, z_crit


def _iqr_bounds(values: list[float]) -> dict[str, float] | None:
    """Return IQR-based anomaly thresholds (Tukey fences).

    Uses Q1 − 1.5·IQR (lower) and Q3 + 1.5·IQR (upper) for warn, and
    Q1 − 3·IQR / Q3 + 3·IQR for critical.  More robust than z-score for
    skewed or non-normal distributions; complementary to _anomaly_bounds.

    Returns None when fewer than _MIN_SAMPLES samples.
    """
    n = len(values)
    if n < _MIN_SAMPLES:
        return None
    sorted_v = sorted(values)
    mid = n // 2
    q1_data = sorted_v[:mid]
    q3_data = sorted_v[mid:] if n % 2 == 0 else sorted_v[mid + 1:]
    q1 = _stats.median(q1_data) if q1_data else sorted_v[0]
    q3 = _stats.median(q3_data) if q3_data else sorted_v[-1]
    iqr = q3 - q1
    if iqr < 1e-9:
        # Near-zero IQR: fall back to mean-based pseudo-IQR to avoid degenerate bounds
        mean = _stats.mean(values)
        iqr = max(1e-6, abs(mean) * _IQR_PSEUDO_CV)
    return {
        "q1": q1,
        "q3": q3,
        "iqr": iqr,
        "upper_warn": q3 + _IQR_WARN_MUL * iqr,
        "upper_crit": q3 + _IQR_CRIT_MUL * iqr,
        "lower_warn": q1 - _IQR_WARN_MUL * iqr,
        "lower_crit": q1 - _IQR_CRIT_MUL * iqr,
    }


def _anomaly_bounds(
    values: list[float],
    z_warn: float | None = None,
    z_crit: float | None = None,
) -> dict[str, float] | None:
    """Return dynamic anomaly thresholds blending z-score and IQR methods.

    Primary method: mean ± z*σ with adaptive z-scores (coefficient of variation).
    Fallback when std ≈ 0: IQR-derived spread replaces σ so bounds remain meaningful.
    Pass explicit z_warn/z_crit to override the adaptive z-score logic.

    Returns None when fewer than _MIN_SAMPLES samples — callers must supply fallback defaults.
    """
    if len(values) < _MIN_SAMPLES:
        return None
    adaptive_warn, adaptive_crit = _adaptive_z_thresholds(values)
    z_warn = z_warn if z_warn is not None else adaptive_warn
    z_crit = z_crit if z_crit is not None else adaptive_crit
    mean = _stats.mean(values)
    std = _stats.pstdev(values)

    # When std is negligible, use IQR/_IQR_TO_SIGMA as a robust σ estimate (normal-equivalent)
    if std < 1e-9:
        iqr_info = _iqr_bounds(values)
        if iqr_info:
            std = iqr_info["iqr"] / _IQR_TO_SIGMA
        else:
            std = 1e-6

    return {
        "mean": mean,
        "std": std,
        "upper_warn": mean + z_warn * std,
        "upper_crit": mean + z_crit * std,
        "lower_warn": mean - z_warn * std,
        "lower_crit": mean - z_crit * std,
    }


def _mad_std_estimate(values: list[float]) -> float:
    """Robust σ estimate via Median Absolute Deviation × 1.4826.

    MAD is resistant to outliers; the 1.4826 factor makes it consistent with σ
    for normally-distributed data. Returns _MAD_FALLBACK_STD when the series is
    empty or the MAD itself is near zero (constant series).
    """
    if not values:
        return _MAD_FALLBACK_STD
    median = _stats.median(values)
    mad = _stats.median([abs(v - median) for v in values])
    robust_std = mad * 1.4826
    return robust_std if robust_std > 1e-9 else _MAD_FALLBACK_STD


def _ewma(values: list[float], alpha: float | None = None) -> list[float]:
    """Exponentially weighted moving average (input: most-recent-first).

    alpha=1 → no smoothing (identity); alpha→0 → heavy smoothing.
    Processes oldest-first so accumulation is correct, then reverses output
    to preserve the most-recent-first ordering expected by callers.
    """
    if not values:
        return []
    a = alpha if alpha is not None else _EWMA_ALPHA
    rev = list(reversed(values))
    smoothed = [rev[0]]
    for v in rev[1:]:
        smoothed.append(a * v + (1 - a) * smoothed[-1])
    return list(reversed(smoothed))


def _calibrate_quality_anomaly_thresholds(
    uqs_scores: list[float],
    gate_rates: list[float],
    findings_history: list[float],
) -> dict[str, float]:
    """Calibrate quality anomaly thresholds via LLM from observed distributions.

    Calls oracle_quality_anomaly_detection (claude-haiku) with data statistics.
    Returns overrides for uqs_warn_delta, uqs_crit_delta, gate_rate_warn,
    gate_rate_crit, and spike_multiplier. Returns {} on any failure so callers
    transparently fall back to module-level constants.
    """
    if not _LLM_CAL_ENABLED:
        return {}
    if not uqs_scores and not gate_rates and not findings_history:
        return {}

    def _dist_stats(vals: list[float]) -> dict:
        if not vals:
            return {}
        sv = sorted(vals)
        n = len(sv)
        mean = sum(sv) / n
        variance = sum((v - mean) ** 2 for v in sv) / n
        std = variance ** 0.5
        return {
            "count": n,
            "mean": round(mean, 3),
            "stdev": round(std, 3),
            "min": round(sv[0], 3),
            "p75": round(sv[min(int(n * 0.75), n - 1)], 3),
            "p90": round(sv[min(int(n * 0.90), n - 1)], 3),
            "max": round(sv[-1], 3),
        }

    summary: dict[str, dict] = {}
    if uqs_scores:
        summary["uqs_scores"] = _dist_stats(uqs_scores)
    if gate_rates:
        summary["gate_failure_rates"] = _dist_stats(gate_rates)
    if findings_history:
        summary["findings_counts"] = _dist_stats(findings_history)

    required_keys: list[tuple[str, float, float | None]] = [
        ("uqs_warn_delta", _LLM_VAL_UQS_WARN_MIN, None),
        ("uqs_crit_delta", _LLM_VAL_UQS_CRIT_MIN, None),
        ("gate_rate_warn", _LLM_VAL_RATE_WARN_MIN, 1.0),
        ("gate_rate_crit", _LLM_VAL_RATE_CRIT_MIN, 1.0),
        ("spike_multiplier", _LLM_VAL_SPIKE_MUL_MIN, _LLM_VAL_SPIKE_MUL_MAX),
    ]

    try:
        from tools.llm.router import LLMRouter
        from tools.llm.provider import LLMRequest

        router = LLMRouter()
        request = LLMRequest(
            messages=[
                {
                    "role": "user",
                    "content": (
                        "Analyse these quality-metric statistical distributions and "
                        "return calibrated anomaly detection thresholds as a JSON object.\n\n"
                        f"Distributions:\n{json.dumps(summary, indent=2)}\n\n"
                        "Required keys:\n"
                        '- "uqs_warn_delta" (float ≥ 0.5): minimum UQS point drop to flag '
                        "as a warning-level declining trend\n"
                        '- "uqs_crit_delta" (float ≥ 1.0): minimum UQS point drop to flag '
                        "as a critical regression\n"
                        '- "gate_rate_warn" (float 0.1–1.0): gate failure rate floor for '
                        "warning-level alert\n"
                        '- "gate_rate_crit" (float 0.2–1.0): gate failure rate floor for '
                        "critical alert\n"
                        '- "spike_multiplier" (float 1.1–10.0): multiplier applied to '
                        "previous findings count to define the spike threshold\n"
                        "\nReturn ONLY valid JSON with these keys, no markdown fences."
                    ),
                }
            ],
            system_prompt=(
                "You are a quality-metrics anomaly-detection expert. "
                "Use the observed distributions to recommend data-driven thresholds that "
                "minimise false positives while catching real regressions. "
                "Return a single valid JSON object with the five required keys."
            ),
            max_tokens=_LLM_CAL_MAX_TOKENS,
            temperature=_LLM_CAL_TEMP,
            skip_injection_scan=True,
        )
        response = router.invoke(_LLM_CAL_FN, request)
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
        if result:
            logger.debug("LLM quality anomaly threshold calibration: %s", result)
        return result
    except Exception as exc:
        logger.debug("LLM quality anomaly threshold calibration skipped: %s", exc)
        return {}


class QualityLens(BaseLens):
    """Predicts quality trends, regressions, and improvement opportunities."""

    name = "quality"
    description = "Anticipates quality regressions from QDC gate history and UQS trends"

    def analyze(self) -> dict[str, Any]:
        """Gather quality data from QDC and Genesis quality DBs."""
        data: dict[str, Any] = {
            "uqs_history": [],
            "gate_history": [],
            "quality_snapshots": [],
            "recent_trends": [],
            "llm_calibrated_thresholds": {},
        }

        # UQS history from QDC canvas DB
        if QDC_DB.exists():
            try:
                conn = get_connection(str(QDC_DB))
                rows = conn.execute(
                    "SELECT uqs_score, dimension_scores, computed_at "
                    "FROM qdc_uqs_history ORDER BY computed_at DESC LIMIT 20"
                ).fetchall()
                data["uqs_history"] = [dict(r) for r in rows]

                gate_rows = conn.execute(
                    "SELECT gate_id, sa11_control, status, executed_at "
                    "FROM qdc_gate_results ORDER BY executed_at DESC LIMIT 50"
                ).fetchall()
                data["gate_history"] = [dict(r) for r in gate_rows]
                conn.close()
            except Exception as e:
                logger.warning("Failed to read QDC DB: %s", e)

        # Genesis quality snapshots
        if GENESIS_QDB.exists():
            try:
                conn = get_connection(str(GENESIS_QDB))
                rows = conn.execute(
                    "SELECT uqs_score, grade, total_findings, auto_fixed, snapshot_at "
                    "FROM quality_snapshots ORDER BY snapshot_at DESC LIMIT 10"
                ).fetchall()
                data["quality_snapshots"] = [dict(r) for r in rows]

                trends = conn.execute(
                    "SELECT trend_type, dimension, direction, severity, detail, detected_at "
                    "FROM quality_trends WHERE resolved = 0 ORDER BY detected_at DESC LIMIT 10"
                ).fetchall()
                data["recent_trends"] = [dict(r) for r in trends]
                conn.close()
            except Exception as e:
                logger.warning("Failed to read Genesis quality DB: %s", e)

        # LLM anomaly threshold calibration — runs after all data is loaded
        uqs_scores = [float(h.get("uqs_score", 0)) for h in data["uqs_history"]]
        gate_ids: set[str] = {g.get("gate_id") for g in data["gate_history"] if g.get("gate_id")}
        gate_rates_for_cal: list[float] = []
        for gid in gate_ids:
            total = sum(1 for g in data["gate_history"] if g.get("gate_id") == gid)
            fails = sum(1 for g in data["gate_history"] if g.get("gate_id") == gid and g.get("status") == "fail")
            if total:
                gate_rates_for_cal.append(fails / total)
        findings_vals = [float(s.get("total_findings", 0)) for s in data["quality_snapshots"]]
        data["llm_calibrated_thresholds"] = _calibrate_quality_anomaly_thresholds(
            uqs_scores, gate_rates_for_cal, findings_vals
        )

        return data

    def score(self, analysis: dict[str, Any]) -> list[OraclePrediction]:
        """Score quality data into predictions using statistical anomaly detection."""
        predictions: list[OraclePrediction] = []
        llm_cal = analysis.get("llm_calibrated_thresholds", {})

        # ── UQS trajectory ───────────────────────────────────────────────────
        uqs_history = analysis.get("uqs_history", [])
        if len(uqs_history) >= _MIN_SAMPLES:
            all_scores = [float(h.get("uqs_score", 0)) for h in uqs_history]
            # EWMA smoothing reduces false positives from noisy single-point readings
            smoothed_scores = _ewma(all_scores) if _USE_EWMA else all_scores
            scores = smoothed_scores[:_UQS_SCORE_WINDOW]
            avg_recent = sum(scores[:_UQS_RECENT_WINDOW]) / _UQS_RECENT_WINDOW
            avg_older = sum(scores[_UQS_RECENT_WINDOW:]) / len(scores[_UQS_RECENT_WINDOW:]) if len(scores) > _UQS_RECENT_WINDOW else avg_recent

            bounds = _anomaly_bounds(all_scores) or {}
            fallback_std = _mad_std_estimate(all_scores) if _MAD_ENABLED else _MAD_FALLBACK_STD
            std = bounds.get("std") or fallback_std
            _warn_delta_default = max(_UQS_WARN_FLOOR, std * _UQS_Z_WARN_MUL)
            _crit_delta_default = max(_UQS_CRIT_FLOOR, std * _UQS_Z_CRIT_MUL)
            warn_delta = llm_cal.get("uqs_warn_delta", _warn_delta_default)
            crit_delta = llm_cal.get("uqs_crit_delta", _crit_delta_default)
            lower_crit = bounds.get("lower_crit", avg_recent - crit_delta)

            delta_decline = avg_older - avg_recent
            delta_rise = avg_recent - avg_older

            if delta_decline > warn_delta:
                severity = "critical" if (delta_decline > crit_delta or avg_recent <= lower_crit) else "warning"
                predictions.append(
                    OraclePrediction(
                        lens=self.name,
                        title="UQS Declining Trend",
                        description=(
                            f"UQS has dropped from {avg_older:.1f} to {avg_recent:.1f} "
                            f"(−{delta_decline:.1f} pts, {delta_decline / std:.1f}σ below mean)"
                        ),
                        confidence=min(_UQS_CONF_CAP, _UQS_CONF_BASE + delta_decline / (std * _UQS_CONF_SIGMA_DENOM)),
                        severity=severity,
                        category="quality_regression",
                        data={
                            "avg_recent": avg_recent,
                            "avg_older": avg_older,
                            "delta": round(avg_recent - avg_older, 1),
                            "z_score": round(delta_decline / std, 2),
                            "dynamic_warn_delta": round(warn_delta, 2),
                        },
                    )
                )
            elif delta_rise > warn_delta:
                predictions.append(
                    OraclePrediction(
                        lens=self.name,
                        title="UQS Improving Trend",
                        description=(
                            f"UQS has improved from {avg_older:.1f} to {avg_recent:.1f} "
                            f"(+{delta_rise:.1f} pts, {delta_rise / std:.1f}σ above mean)"
                        ),
                        confidence=min(_UQS_CONF_CAP, _UQS_CONF_BASE + delta_rise / (std * _UQS_CONF_SIGMA_DENOM)),
                        severity="info",
                        category="quality_improvement",
                        data={
                            "avg_recent": avg_recent,
                            "avg_older": avg_older,
                            "delta": round(avg_recent - avg_older, 1),
                            "z_score": round(delta_rise / std, 2),
                        },
                    )
                )

        # ── Gate failure patterns ─────────────────────────────────────────────
        gate_history = analysis.get("gate_history", [])
        gate_totals: dict[str, int] = {}
        gate_failures: dict[str, int] = {}
        for g in gate_history:
            gid = g.get("gate_id", "unknown")
            gate_totals[gid] = gate_totals.get(gid, 0) + 1
            if g.get("status") == "fail":
                gate_failures[gid] = gate_failures.get(gid, 0) + 1

        # Compute failure rates; derive anomaly thresholds from their distribution
        failure_rates = {
            gid: gate_failures.get(gid, 0) / gate_totals[gid]
            for gid in gate_totals
        }
        all_rates = list(failure_rates.values())
        rate_stats = _compute_stats(all_rates)
        rate_bounds = _anomaly_bounds(all_rates) or {}
        rate_warn = llm_cal.get("gate_rate_warn", rate_bounds.get("upper_warn", _RATE_WARN_FB))
        rate_crit = llm_cal.get("gate_rate_crit", rate_bounds.get("upper_crit", _RATE_CRIT_FB))

        for gate_id, count in gate_failures.items():
            rate = failure_rates[gate_id]
            total = gate_totals[gate_id]
            rate_z = _zscore(rate, rate_stats)
            if rate >= max(_RATE_FLOOR, rate_warn) and count >= _RATE_MIN_FAIL:
                severity = "critical" if rate >= rate_crit else "warning"
                predictions.append(
                    OraclePrediction(
                        lens=self.name,
                        title=f"Recurring Gate Failure: {gate_id}",
                        description=(
                            f"Gate '{gate_id}' failed {count}/{total} times "
                            f"({rate:.0%} failure rate, threshold {rate_warn:.0%})"
                        ),
                        confidence=min(_GATE_CONF_CAP, _GATE_CONF_BASE + rate * _GATE_CONF_RATE_SCALE),
                        severity=severity,
                        category="gate_failure_pattern",
                        data={
                            "gate_id": gate_id,
                            "failure_count": count,
                            "total_runs": total,
                            "failure_rate": round(rate, 3),
                            "rate_zscore": round(rate_z, 2),
                            "dynamic_warn_rate": round(rate_warn, 3),
                        },
                    )
                )

        # ── Findings spike ────────────────────────────────────────────────────
        snapshots = analysis.get("quality_snapshots", [])
        if len(snapshots) >= 2:
            all_findings = [float(s.get("total_findings", 0)) for s in snapshots]
            current_findings = all_findings[0]
            prev_findings = all_findings[1]

            f_bounds = _anomaly_bounds(all_findings) or {}
            # Upper warn threshold: historical mean + z_warn*σ (or multiplier × prev)
            _spike_mul = llm_cal.get("spike_multiplier", _SPIKE_FB_MUL)
            spike_threshold = f_bounds.get("upper_warn", prev_findings * _spike_mul)
            # Minimum absolute floor: fraction of historical mean or abs floor, whichever is larger
            min_abs = max(_SPIKE_ABS_FLOOR, f_bounds.get("mean", _SPIKE_MEAN_FALLBACK) * _SPIKE_MEAN_RATIO)

            if current_findings > spike_threshold and current_findings > min_abs:
                f_std = f_bounds.get("std", 1.0) or 1.0
                f_mean = f_bounds.get("mean", prev_findings)
                z_score = (current_findings - f_mean) / f_std
                growth_ratio = current_findings / max(prev_findings, 1.0)
                predictions.append(
                    OraclePrediction(
                        lens=self.name,
                        title="Findings Growth Spike",
                        description=(
                            f"Quality findings jumped from {int(prev_findings)} to {int(current_findings)} "
                            f"(+{int(current_findings - prev_findings)}, {z_score:.1f}σ above baseline)"
                        ),
                        confidence=min(_SPIKE_CONF_CAP, _SPIKE_CONF_BASE + z_score * _SPIKE_CONF_SLOPE),
                        severity="warning",
                        category="findings_spike",
                        data={
                            "current": current_findings,
                            "previous": prev_findings,
                            "growth_ratio": round(growth_ratio, 2),
                            "z_score": round(z_score, 2),
                            "dynamic_spike_threshold": round(spike_threshold, 1),
                        },
                    )
                )

        # ── Unresolved critical trends ────────────────────────────────────────
        critical_trends = [t for t in analysis.get("recent_trends", []) if t.get("severity") == "critical"]
        n_critical = len(critical_trends)
        for trend in critical_trends:
            # Confidence rises with the count of unresolved critical items
            base_confidence = min(_CT_CONF_CAP, _CT_BASE_CONF + n_critical * _CT_CONF_PER)
            predictions.append(
                OraclePrediction(
                    lens=self.name,
                    title=f"Unresolved Critical Trend: {trend.get('trend_type')}",
                    description=trend.get("detail", ""),
                    confidence=base_confidence,
                    severity="critical",
                    category="unresolved_trend",
                    data=trend,
                )
            )

        return predictions

    def propose(self, predictions: list[OraclePrediction]) -> list[OraclePrediction]:
        """Enrich predictions with actionable recommendations."""
        for pred in predictions:
            if pred.category == "quality_regression":
                pred.recommendations = [
                    "Run full gate scan: python tools/qdc_canvas/gate_executor.py --all --json",
                    "Review worst-scoring gates and prioritize fixes",
                    "Check if recent code changes introduced regressions",
                    "Consider tightening gate thresholds after remediation",
                ]
            elif pred.category == "gate_failure_pattern":
                gate_id = pred.data.get("gate_id", "")
                pred.recommendations = [
                    f"Execute gate: python tools/qdc_canvas/gate_executor.py --gate {gate_id} --json",
                    f"Review {gate_id} findings and apply auto-fix if available",
                    "Check if the underlying tool needs configuration updates",
                    "Consider adding this gate to the Genesis auto-remediation list",
                ]
            elif pred.category == "findings_spike":
                pred.recommendations = [
                    "Run auto-remediation: python tools/genesis/reflexes/quality.py --remediate --json",
                    "Review recent commits for quality-impacting changes",
                    "Consider reverting problematic changes if spike is severe",
                ]
            elif pred.category == "quality_improvement":
                pred.recommendations = [
                    "Document what drove the improvement for knowledge base",
                    "Consider raising gate thresholds to lock in gains",
                    "Share improvement patterns with child apps via genome",
                ]

        return predictions


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    lens = QualityLens()
    predictions = lens.run()
    print(json.dumps([p.to_dict() for p in predictions], indent=2, default=str))
