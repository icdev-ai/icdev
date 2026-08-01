# CUI // SP-CTI
"""Oracle Trajectory Lens — Architectural trajectory forecasting.

Analyses code_quality_metrics time-series (weekly snapshots) to predict
when files will breach complexity or maintainability thresholds, detect
converging hotspots, and score predictions by urgency and blast radius.

Three-phase pipeline: analyze → score → propose
Scanner-tier with optional LLM threshold calibration (oracle_anomaly_detection)
when fleet data is too sparse for percentile-based adaptation.
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import json
import subprocess
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from tools.db.storage import get_connection
from tools.oracle.base_lens import BaseLens, OraclePrediction

logger = get_logger(__name__)

_ICDEV_ROOT = Path(__file__).resolve().parents[2]
_TRAJECTORY_CONFIG_PATH = _ICDEV_ROOT / "args" / "oracle_trajectory_config.yaml"


def _load_trajectory_config() -> dict:
    """Load threshold config from args/oracle_trajectory_config.yaml; return {} on failure."""
    try:
        with open(_TRAJECTORY_CONFIG_PATH, encoding="utf-8") as fh:
            raw = yaml.safe_load(fh)
        return (raw or {}).get("trajectory_forecast", {})
    except Exception as exc:
        logger.warning("Trajectory: failed to load config, using built-in defaults: %s", exc)
        return {}


_CFG = _load_trajectory_config()
_FB = _CFG.get("fallbacks", {})
_AN = _CFG.get("anomaly", {})
_VEL = _CFG.get("velocity", {})
_HS = _CFG.get("hotspot", {})
_URG = _CFG.get("urgency", {})
_CON = _CFG.get("confidence", {})
_LLM = _CFG.get("llm_calibration", {})
_LLM_VAL = _LLM.get("validation", {})

# Fallback thresholds — used when insufficient data to derive adaptive values
CC_THRESHOLD: int = int(_FB.get("cc_threshold", 15))
MAINTAINABILITY_FLOOR: float = float(_FB.get("maintainability_floor", 0.5))
MIN_SNAPSHOTS: int = int(_FB.get("min_snapshots", 3))
FORECAST_HORIZON_DAYS: int = int(_FB.get("forecast_horizon_days", 365))
HOTSPOT_MIN_FILES: int = int(_HS.get("min_files", 2))
HOTSPOT_CRITICAL_FALLBACK: int = int(_HS.get("critical_fallback", 4))
DAYS_URGENT: int = int(_URG.get("days_critical", 30))
DAYS_WARNING: int = int(_URG.get("days_warning", 90))

# Anomaly detection parameters
ADAPTIVE_MIN_FILES: int = int(_AN.get("adaptive_min_files", 10))
CC_ANOMALY_PERCENTILE: float = float(_AN.get("cc_percentile", 90.0))
MAINT_ANOMALY_PERCENTILE: float = float(_AN.get("maintainability_percentile", 10.0))
VELOCITY_ANOMALY_PERCENTILE: float = float(_AN.get("velocity_percentile", 75.0))
VELOCITY_FALLBACK_SLOPE: float = float(_VEL.get("fallback_slope_cutoff", 1.0))
VELOCITY_FALLBACK_RECENT_AVG: float = float(_VEL.get("fallback_recent_avg_cutoff", 5.0))

# LLM calibration parameters — used when fleet data is too sparse for adaptive thresholds
LLM_CALIBRATION_FUNCTION_NAME: str = str(_LLM.get("function_name", "oracle_anomaly_detection"))
LLM_CALIBRATION_MAX_TOKENS: int = int(_LLM.get("max_tokens", 200))
LLM_CALIBRATION_TEMPERATURE: float = float(_LLM.get("temperature", 0.0))

# LLM output validation lower bounds — reject suggestions below these floors
LLM_VAL_CC_THRESHOLD_MIN: float = float(_LLM_VAL.get("cc_threshold_min", 1.0))
LLM_VAL_MAINTAINABILITY_FLOOR_MIN: float = float(_LLM_VAL.get("maintainability_floor_min", 0.0))
LLM_VAL_SLOPE_CUTOFF_MIN: float = float(_LLM_VAL.get("slope_cutoff_min", 0.01))
LLM_VAL_RECENT_AVG_CUTOFF_MIN: float = float(_LLM_VAL.get("recent_avg_cutoff_min", 0.01))

# Confidence scoring parameters — loaded from args/oracle_trajectory_config.yaml
CC_BREACH_CONF_MAX: float = float(_CON.get("cc_breach_max", 0.92))
CC_BREACH_CONF_BASE: float = float(_CON.get("cc_breach_base", 0.55))
CC_BREACH_CONF_SCALE: float = float(_CON.get("cc_breach_scale", 0.40))
MAINT_DECLINE_CONF_MAX: float = float(_CON.get("maint_decline_max", 0.90))
MAINT_DECLINE_CONF_BASE: float = float(_CON.get("maint_decline_base", 0.55))
MAINT_DECLINE_CONF_SCALE: float = float(_CON.get("maint_decline_scale", 0.38))
HOTSPOT_CONF_MAX: float = float(_CON.get("hotspot_max", 0.88))
HOTSPOT_CONF_BASE: float = float(_CON.get("hotspot_base", 0.55))
HOTSPOT_CONF_PER_FILE: float = float(_CON.get("hotspot_per_file", 0.06))
VELOCITY_CONF_FIXED: float = float(_CON.get("velocity_fixed", 0.70))


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _linear_regression(xs: list[float], ys: list[float]) -> tuple[float, float]:
    """Return (slope, intercept) via ordinary least squares."""
    n = len(xs)
    if n < 2:
        return 0.0, ys[0] if ys else 0.0
    sum_x = sum(xs)
    sum_y = sum(ys)
    sum_xy = sum(x * y for x, y in zip(xs, ys))
    sum_x2 = sum(x * x for x in xs)
    denom = n * sum_x2 - sum_x**2
    if denom == 0:
        return 0.0, sum_y / n
    slope = (n * sum_xy - sum_x * sum_y) / denom
    intercept = (sum_y - slope * sum_x) / n
    return slope, intercept


def _days_to_threshold(
    current: float, slope_per_week: float, threshold: float
) -> float | None:
    """Return days until value crosses threshold, or None if not trending that way."""
    if slope_per_week == 0:
        return None
    weeks = (threshold - current) / slope_per_week
    if weeks <= 0:
        return None
    return weeks * 7.0


def _percentile(values: list[float], p: float) -> float:
    """Return the p-th percentile (0–100) via linear interpolation. No external deps."""
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    idx = (p / 100.0) * (n - 1)
    lo = int(idx)
    hi = min(lo + 1, n - 1)
    return sorted_vals[lo] * (1 - (idx - lo)) + sorted_vals[hi] * (idx - lo)


def _compute_adaptive_thresholds(
    file_trends: dict,
) -> dict[str, float]:
    """Derive CC and maintainability thresholds from the fleet's own distribution.

    Uses the most-recent snapshot per file so thresholds reflect the current
    codebase state, not historical averages.  Falls back to module constants
    when the fleet is too small (<ADAPTIVE_MIN_FILES files with valid data).
    """
    cc_current: list[float] = []
    maint_current: list[float] = []

    for snapshots in file_trends.values():
        if not snapshots:
            continue
        latest = sorted(snapshots, key=lambda s: s["week"])[-1]
        if latest["cc"] > 0:
            cc_current.append(latest["cc"])
        if latest["maintainability"] > 0:
            maint_current.append(latest["maintainability"])

    cc_threshold = float(CC_THRESHOLD)
    maint_floor = MAINTAINABILITY_FLOOR
    adaptive = False

    if len(cc_current) >= ADAPTIVE_MIN_FILES:
        cc_threshold = _percentile(cc_current, CC_ANOMALY_PERCENTILE)
        adaptive = True

    if len(maint_current) >= ADAPTIVE_MIN_FILES:
        maint_floor = _percentile(maint_current, MAINT_ANOMALY_PERCENTILE)
        adaptive = True

    return {
        "cc_threshold": cc_threshold,
        "maintainability_floor": maint_floor,
        "adaptive": adaptive,
    }


def _compute_velocity_thresholds(dir_activity: dict) -> dict[str, float]:
    """Derive commit-velocity thresholds from the fleet of directories.

    Returns slope and recent-average cutoffs at VELOCITY_ANOMALY_PERCENTILE.
    Falls back to hardcoded values when fewer than ADAPTIVE_MIN_FILES directories
    have enough history.
    """
    slopes: list[float] = []
    recent_avgs: list[float] = []

    for week_counts in dir_activity.values():
        if len(week_counts) < MIN_SNAPSHOTS:
            continue
        sorted_weeks = sorted(week_counts.items())
        xs = list(range(len(sorted_weeks)))
        ys = [count for _, count in sorted_weeks]
        slope, _ = _linear_regression(xs, ys)
        if slope > 0:
            slopes.append(slope)
        recent_avg = sum(ys[-MIN_SNAPSHOTS:]) / MIN_SNAPSHOTS
        recent_avgs.append(recent_avg)

    if len(slopes) >= ADAPTIVE_MIN_FILES:
        return {
            "slope_cutoff": _percentile(slopes, VELOCITY_ANOMALY_PERCENTILE),
            "recent_avg_cutoff": _percentile(recent_avgs, VELOCITY_ANOMALY_PERCENTILE),
            "adaptive": True,
        }
    return {"slope_cutoff": VELOCITY_FALLBACK_SLOPE, "recent_avg_cutoff": VELOCITY_FALLBACK_RECENT_AVG, "adaptive": False}


def _llm_calibrate_thresholds(
    file_trends: dict,
    dir_activity: dict,
) -> dict[str, float]:
    """Use LLM to calibrate anomaly thresholds when fleet data is sparse.

    Builds a compact summary of the observed distribution and asks the model
    to suggest data-driven thresholds in place of the module-level defaults.
    Returns a dict with any subset of the four threshold keys; empty dict when
    the LLM is unavailable or returns unparseable output.

    Graceful: never raises — always returns {} on failure so callers can fall
    through to the hardcoded constants without disruption.
    """
    # Collect per-file quality stats
    cc_samples: list[float] = []
    maint_samples: list[float] = []
    for snapshots in file_trends.values():
        if not snapshots:
            continue
        latest = sorted(snapshots, key=lambda s: s["week"])[-1]
        if latest["cc"] > 0:
            cc_samples.append(latest["cc"])
        if latest["maintainability"] > 0:
            maint_samples.append(latest["maintainability"])

    # Collect per-directory velocity stats
    commit_slopes: list[float] = []
    commit_avgs: list[float] = []
    for week_counts in dir_activity.values():
        if len(week_counts) < 2:
            continue
        sorted_weeks = sorted(week_counts.items())
        ys = [count for _, count in sorted_weeks]
        slope, _ = _linear_regression(list(range(len(ys))), ys)
        if slope > 0:
            commit_slopes.append(slope)
        commit_avgs.append(sum(ys) / len(ys))

    stats = {
        "file_count": len(file_trends),
        "cc_p50": round(_percentile(cc_samples, 50.0), 2) if cc_samples else None,
        "cc_p90": round(_percentile(cc_samples, 90.0), 2) if cc_samples else None,
        "cc_max": round(max(cc_samples), 2) if cc_samples else None,
        "maint_p10": round(_percentile(maint_samples, 10.0), 4) if maint_samples else None,
        "maint_p50": round(_percentile(maint_samples, 50.0), 4) if maint_samples else None,
        "dir_count": len(dir_activity),
        "commit_slope_p75": round(_percentile(commit_slopes, 75.0), 2) if commit_slopes else None,
        "commit_avg_p75": round(_percentile(commit_avgs, 75.0), 2) if commit_avgs else None,
    }

    try:
        from tools.llm.router import LLMRouter
        from tools.llm.provider import LLMRequest

        router = LLMRouter()
        request = LLMRequest(
            messages=[
                {
                    "role": "user",
                    "content": (
                        "Given these sparse fleet statistics from a Python codebase, "
                        "calibrate anomaly detection thresholds as a JSON object.\n\n"
                        f"Fleet stats:\n{json.dumps(stats, indent=2)}\n\n"
                        "Return a JSON object with exactly these keys:\n"
                        '- "cc_threshold" (float > 0): cyclomatic complexity ceiling for anomaly flagging\n'
                        '- "maintainability_floor" (float 0.0–1.0): minimum maintainability score before flagging\n'
                        '- "slope_cutoff" (float > 0): minimum commit slope per week to flag a high-velocity directory\n'
                        '- "recent_avg_cutoff" (float > 0): minimum recent weekly commit average to flag\n'
                        f"Baseline defaults if data is absent: cc={CC_THRESHOLD}, "
                        f"maint={MAINTAINABILITY_FLOOR}, slope={VELOCITY_FALLBACK_SLOPE}, "
                        f"avg={VELOCITY_FALLBACK_RECENT_AVG}.\n"
                        "Return ONLY the JSON object, no markdown fences, no explanation."
                    ),
                }
            ],
            system_prompt=(
                "You are a code quality expert specialising in anomaly detection. "
                "Use the observed statistics to recommend data-driven thresholds that "
                "minimise false positives on small fleets. "
                "Return a single valid JSON object with the four required keys."
            ),
            max_tokens=LLM_CALIBRATION_MAX_TOKENS,
            temperature=LLM_CALIBRATION_TEMPERATURE,
            skip_injection_scan=True,
        )
        response = router.invoke(LLM_CALIBRATION_FUNCTION_NAME, request)
        raw = (response.content or "").strip()
        if raw.startswith("```"):
            parts = raw.split("```")
            raw = parts[1].lstrip("json").strip() if len(parts) > 1 else raw
        parsed = json.loads(raw)
        result: dict[str, float] = {}
        for key, lo in [
            ("cc_threshold", LLM_VAL_CC_THRESHOLD_MIN),
            ("maintainability_floor", LLM_VAL_MAINTAINABILITY_FLOOR_MIN),
            ("slope_cutoff", LLM_VAL_SLOPE_CUTOFF_MIN),
            ("recent_avg_cutoff", LLM_VAL_RECENT_AVG_CUTOFF_MIN),
        ]:
            val = parsed.get(key)
            if isinstance(val, (int, float)) and float(val) >= lo:
                result[key] = float(val)
        if result:
            logger.debug("LLM trajectory threshold calibration: %s", result)
        return result
    except Exception as exc:
        logger.debug("LLM trajectory threshold calibration skipped: %s", exc)
        return {}


class TrajectoryLens(BaseLens):
    """Forecast architectural trajectory: complexity growth and threshold breaches."""

    name = "trajectory"
    description = (
        "Forecasts complexity growth, threshold breaches, and directory hotspots "
        "from code_quality_metrics time-series"
    )

    # ── Phase 1: Analyze ──────────────────────────────────────────────────────

    def analyze(self) -> dict[str, Any]:
        """Gather per-file quality time-series, git directory activity, and blast radius."""
        data: dict[str, Any] = {
            "file_trends": {},   # file_path -> list[dict] weekly snapshots
            "dir_activity": {},  # dir_path -> {week_tag: commit_count}
            "dependents": {},    # file_path -> int
        }

        self._load_quality_trends(data)
        self._load_git_activity(data)
        self._load_blast_radius(data)
        return data

    def _load_quality_trends(self, data: dict[str, Any]) -> None:
        """Query code_quality_metrics weekly aggregates per file."""
        try:
            conn = get_connection()
            rows = conn.execute(
                "SELECT file_path, "
                "  strftime('%Y-%W', created_at) AS week_tag, "
                "  AVG(cyclomatic_complexity)  AS avg_cc, "
                "  AVG(cognitive_complexity)   AS avg_cog, "
                "  AVG(maintainability_score)  AS avg_maint "
                "FROM code_quality_metrics "
                "WHERE file_path IS NOT NULL AND created_at IS NOT NULL "
                "GROUP BY file_path, week_tag "
                "ORDER BY file_path, week_tag"
            ).fetchall()
            conn.close()

            for row in rows:
                fp = row["file_path"]
                if fp not in data["file_trends"]:
                    data["file_trends"][fp] = []
                data["file_trends"][fp].append(
                    {
                        "week": row["week_tag"],
                        "cc": float(row["avg_cc"] or 0),
                        "cognitive": float(row["avg_cog"] or 0),
                        "maintainability": float(row["avg_maint"] or 0),
                    }
                )
        except Exception as exc:
            logger.warning("Trajectory: failed to read code_quality_metrics: %s", exc)

    def _load_git_activity(self, data: dict[str, Any]) -> None:
        """Tally file-change counts per directory per week from git log (last 8 weeks)."""
        try:
            result = subprocess.run(
                [
                    "git", "-C", str(_ICDEV_ROOT), "log",
                    "--name-only", "--pretty=format:%aI",
                    "--since=8 weeks ago",
                ],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode != 0:
                return

            dir_week: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
            current_week = ""
            for line in result.stdout.splitlines():
                line = line.strip()
                if not line:
                    continue
                # ISO timestamp lines start with a digit and contain "T"
                if line[0].isdigit() and "T" in line:
                    try:
                        dt = datetime.fromisoformat(line[:19].replace("Z", ""))
                        year, week, _ = dt.isocalendar()
                        current_week = f"{year}-{week:02d}"
                    except ValueError:
                        pass
                elif current_week and ("/" in line or line.endswith(".py")):
                    dir_path = str(Path(line).parent)
                    dir_week[dir_path][current_week] += 1

            data["dir_activity"] = {d: dict(w) for d, w in dir_week.items()}
        except Exception as exc:
            logger.warning("Trajectory: git log failed: %s", exc)

    def _load_blast_radius(self, data: dict[str, Any]) -> None:
        """Estimate blast radius as count of Python files that reference each tracked file."""
        tracked = set(data["file_trends"].keys())
        dependents: dict[str, int] = {}
        for fp in tracked:
            stem = Path(fp).stem
            if not stem or stem.startswith("_"):
                dependents[fp] = 0
                continue
            try:
                result = subprocess.run(
                    [
                        "git", "-C", str(_ICDEV_ROOT), "grep",
                        "-rl", "--include=*.py", stem,
                    ],
                    capture_output=True, text=True, timeout=10,
                )
                hits = len(result.stdout.strip().splitlines()) if result.returncode == 0 else 0
                dependents[fp] = max(0, hits - 1)  # subtract the file itself
            except Exception:
                dependents[fp] = 0
        data["dependents"] = dependents

    # ── Phase 2: Score ────────────────────────────────────────────────────────

    def score(self, analysis: dict[str, Any]) -> list[OraclePrediction]:
        """Compute regression slopes and emit trajectory predictions."""
        predictions: list[OraclePrediction] = []
        file_trends: dict[str, list[dict]] = analysis.get("file_trends", {})
        dir_activity: dict[str, dict] = analysis.get("dir_activity", {})
        dependents: dict[str, int] = analysis.get("dependents", {})

        # Derive anomaly thresholds from the fleet before iterating files
        adaptive = _compute_adaptive_thresholds(file_trends)
        cc_threshold = adaptive["cc_threshold"]
        maint_floor = adaptive["maintainability_floor"]

        vel = _compute_velocity_thresholds(dir_activity)
        slope_cutoff = vel["slope_cutoff"]
        recent_avg_cutoff = vel["recent_avg_cutoff"]

        # When fleet data is too sparse for percentile-based adaptation, ask the
        # LLM to calibrate thresholds from the observed distribution summary.
        threshold_source: str
        if not adaptive["adaptive"] or not vel["adaptive"]:
            llm_cal = _llm_calibrate_thresholds(file_trends, dir_activity)
            if llm_cal:
                if not adaptive["adaptive"]:
                    cc_threshold = llm_cal.get("cc_threshold", cc_threshold)
                    maint_floor = llm_cal.get("maintainability_floor", maint_floor)
                if not vel["adaptive"]:
                    slope_cutoff = llm_cal.get("slope_cutoff", slope_cutoff)
                    recent_avg_cutoff = llm_cal.get("recent_avg_cutoff", recent_avg_cutoff)
                threshold_source = "llm_calibrated"
            else:
                threshold_source = "fallback"
        else:
            threshold_source = "adaptive"

        # dir_path -> [(file_path, cc_slope)]  — used for hotspot detection
        dir_cc_slopes: dict[str, list[tuple[str, float]]] = defaultdict(list)

        for fp, snapshots in file_trends.items():
            if len(snapshots) < MIN_SNAPSHOTS:
                continue

            snaps = sorted(snapshots, key=lambda s: s["week"])
            xs = list(range(len(snaps)))
            cc_vals = [s["cc"] for s in snaps]
            maint_vals = [s["maintainability"] for s in snaps]

            cc_slope, _ = _linear_regression(xs, cc_vals)
            maint_slope, _ = _linear_regression(xs, maint_vals)

            current_cc = cc_vals[-1]
            current_maint = maint_vals[-1]
            blast = dependents.get(fp, 0)
            dir_path = str(Path(fp).parent)

            dir_cc_slopes[dir_path].append((fp, cc_slope))

            # CC threshold breach forecast
            if cc_slope > 0 and current_cc < cc_threshold:
                days = _days_to_threshold(current_cc, cc_slope, cc_threshold)
                if days is not None and days < FORECAST_HORIZON_DAYS:
                    severity = (
                        "critical" if days < DAYS_URGENT
                        else "warning" if days < DAYS_WARNING
                        else "info"
                    )
                    confidence = min(CC_BREACH_CONF_MAX, CC_BREACH_CONF_BASE + (1.0 - days / FORECAST_HORIZON_DAYS) * CC_BREACH_CONF_SCALE)
                    predictions.append(
                        OraclePrediction(
                            lens=self.name,
                            title=f"CC Threshold Breach in ~{int(days)}d: {Path(fp).name}",
                            description=(
                                f"{fp} cyclomatic complexity is rising. "
                                f"Current: {current_cc:.1f}, slope: +{cc_slope:.2f}/week. "
                                f"Will exceed {cc_threshold:.1f} (fleet {CC_ANOMALY_PERCENTILE:.0f}th pct) "
                                f"in ~{int(days)} days."
                            ),
                            confidence=confidence,
                            severity=severity,
                            category="trajectory_forecast",
                            data={
                                "file_path": fp,
                                "metric": "cyclomatic_complexity",
                                "current_value": round(current_cc, 2),
                                "slope_per_week": round(cc_slope, 4),
                                "threshold": round(cc_threshold, 2),
                                "threshold_source": threshold_source,
                                "days_to_threshold": round(days, 1),
                                "blast_radius": blast,
                            },
                        )
                    )

            # Maintainability floor breach forecast
            if maint_slope < 0 and current_maint > maint_floor:
                days = _days_to_threshold(current_maint, maint_slope, maint_floor)
                if days is not None and days < FORECAST_HORIZON_DAYS:
                    severity = (
                        "critical" if days < DAYS_URGENT
                        else "warning" if days < DAYS_WARNING
                        else "info"
                    )
                    confidence = min(MAINT_DECLINE_CONF_MAX, MAINT_DECLINE_CONF_BASE + (1.0 - days / FORECAST_HORIZON_DAYS) * MAINT_DECLINE_CONF_SCALE)
                    predictions.append(
                        OraclePrediction(
                            lens=self.name,
                            title=f"Maintainability Decline in ~{int(days)}d: {Path(fp).name}",
                            description=(
                                f"{fp} maintainability score is declining. "
                                f"Current: {current_maint:.3f}, slope: {maint_slope:.4f}/week. "
                                f"Will breach floor ({maint_floor:.3f}, fleet {MAINT_ANOMALY_PERCENTILE:.0f}th pct) "
                                f"in ~{int(days)} days."
                            ),
                            confidence=confidence,
                            severity=severity,
                            category="trajectory_forecast",
                            data={
                                "file_path": fp,
                                "metric": "maintainability_score",
                                "current_value": round(current_maint, 4),
                                "slope_per_week": round(maint_slope, 4),
                                "threshold": round(maint_floor, 4),
                                "threshold_source": threshold_source,
                                "days_to_threshold": round(days, 1),
                                "blast_radius": blast,
                            },
                        )
                    )

        # Converging complexity hotspot detection
        # Critical cutoff: top quartile of trending-file counts across directories
        trending_counts = [
            len([s for _, s in slopes if s > 0])
            for slopes in dir_cc_slopes.values()
        ]
        hotspot_critical_cutoff = (
            _percentile(trending_counts, VELOCITY_ANOMALY_PERCENTILE)
            if len(trending_counts) >= ADAPTIVE_MIN_FILES
            else HOTSPOT_CRITICAL_FALLBACK
        )

        for dir_path, file_slopes in dir_cc_slopes.items():
            trending_up = [(fp, slope) for fp, slope in file_slopes if slope > 0]
            if len(trending_up) < HOTSPOT_MIN_FILES:
                continue

            avg_slope = sum(s for _, s in trending_up) / len(trending_up)
            total_blast = sum(dependents.get(fp, 0) for fp, _ in trending_up)
            top_files = sorted(trending_up, key=lambda x: -x[1])[:5]

            confidence = min(HOTSPOT_CONF_MAX, HOTSPOT_CONF_BASE + len(trending_up) * HOTSPOT_CONF_PER_FILE)
            severity = "critical" if len(trending_up) >= hotspot_critical_cutoff else "warning"

            predictions.append(
                OraclePrediction(
                    lens=self.name,
                    title=f"Complexity Hotspot: {dir_path} ({len(trending_up)} files rising)",
                    description=(
                        f"Directory '{dir_path}' has {len(trending_up)} files with increasing "
                        f"cyclomatic complexity (avg slope: +{avg_slope:.2f}/week). "
                        f"Combined blast radius: {total_blast} dependents."
                    ),
                    confidence=confidence,
                    severity=severity,
                    category="trajectory_forecast",
                    data={
                        "directory": dir_path,
                        "trending_file_count": len(trending_up),
                        "avg_cc_slope_per_week": round(avg_slope, 4),
                        "top_files": [
                            {"file": fp, "slope": round(s, 4)} for fp, s in top_files
                        ],
                        "combined_blast_radius": total_blast,
                    },
                )
            )

        # High commit-velocity directories — anomalous relative to fleet
        for dir_path, week_counts in dir_activity.items():
            if len(week_counts) < MIN_SNAPSHOTS:
                continue
            sorted_weeks = sorted(week_counts.items())
            xs = list(range(len(sorted_weeks)))
            ys = [count for _, count in sorted_weeks]
            slope, _ = _linear_regression(xs, ys)
            recent_avg = sum(ys[-MIN_SNAPSHOTS:]) / MIN_SNAPSHOTS
            if slope > slope_cutoff and recent_avg > recent_avg_cutoff:
                predictions.append(
                    OraclePrediction(
                        lens=self.name,
                        title=f"High Commit Velocity: {dir_path}",
                        description=(
                            f"Directory '{dir_path}' shows accelerating commit activity "
                            f"(slope: +{slope:.1f} commits/week, recent avg: {recent_avg:.1f}/week). "
                            "High-churn areas are prone to integration conflicts and quality drift."
                        ),
                        confidence=VELOCITY_CONF_FIXED,
                        severity="info",
                        category="trajectory_forecast",
                        data={
                            "directory": dir_path,
                            "commit_slope_per_week": round(slope, 2),
                            "recent_weekly_avg": round(recent_avg, 1),
                            "threshold_source": "adaptive" if vel["adaptive"] else "fallback",
                        },
                    )
                )

        # Sort: critical first, then by days_to_threshold ascending
        def _urgency_key(p: OraclePrediction) -> tuple[int, float]:
            sev_order = {"critical": 0, "warning": 1, "info": 2}
            days = p.data.get("days_to_threshold", 999.0)
            return (sev_order.get(p.severity, 3), float(days))

        predictions.sort(key=_urgency_key)
        return predictions

    # ── Phase 3: Propose ──────────────────────────────────────────────────────

    def propose(self, predictions: list[OraclePrediction]) -> list[OraclePrediction]:
        """Enrich predictions with actionable recommendations."""
        for pred in predictions:
            metric = pred.data.get("metric", "")

            if metric == "cyclomatic_complexity":
                fp = pred.data.get("file_path", "")
                blast = pred.data.get("blast_radius", 0)
                pred.recommendations = [
                    f"Run complexity check: python -m ruff check --select C90 {fp}",
                    "Extract complex functions into smaller single-responsibility units",
                    "Replace deep conditional chains with polymorphism or strategy patterns",
                    "Add this file to icdev-review complexity gate watchlist",
                    f"Prioritize refactor — blast radius: {blast} dependent files",
                ]

            elif metric == "maintainability_score":
                fp = pred.data.get("file_path", "")
                pred.recommendations = [
                    f"Run lint scan: python -m ruff check {fp}",
                    "Improve docstring coverage (missing docs reduce maintainability score)",
                    "Reduce function length and nesting depth to improve score",
                    "Run: python tools/qdc_canvas/gate_executor.py --gate maintainability --json",
                    "Add to Genesis auto-remediation queue if score drops below 0.4",
                ]

            elif "trending_file_count" in pred.data:
                # hotspot prediction
                dp = pred.data.get("directory", "")
                pred.recommendations = [
                    f"Audit all rising files in '{dp}': ruff check {dp}/",
                    "Identify which recent changes increased cyclomatic complexity",
                    "Consider splitting the directory into focused sub-modules",
                    "Add per-directory complexity gate to CI pipeline",
                    "Schedule a refactoring sprint before hotspot propagates to dependents",
                ]

            elif "commit_slope_per_week" in pred.data:
                # high velocity prediction
                dp = pred.data.get("directory", "")
                pred.recommendations = [
                    f"Enable mandatory code review for all PRs touching '{dp}'",
                    "Add automated complexity check to pre-commit hooks for this directory",
                    "Consider feature flags to reduce integration risk during high-churn periods",
                ]

        return predictions


# ── CLI ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    lens = TrajectoryLens()
    predictions = lens.run()
    print(json.dumps([p.to_dict() for p in predictions], indent=2, default=str))
