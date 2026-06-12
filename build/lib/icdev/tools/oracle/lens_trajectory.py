# CUI // SP-CTI
"""Oracle Trajectory Lens — Architectural trajectory forecasting.

Analyses code_quality_metrics time-series (weekly snapshots) to predict
when files will breach complexity or maintainability thresholds, detect
converging hotspots, and score predictions by urgency and blast radius.

Three-phase pipeline: analyze → score → propose
Scanner-tier only (zero Claude tokens).
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import subprocess
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tools.db.storage import get_connection
from tools.oracle.base_lens import BaseLens, OraclePrediction

logger = get_logger(__name__)

_ICDEV_ROOT = Path(__file__).resolve().parents[2]

# Thresholds
CC_THRESHOLD: int = 15          # cyclomatic complexity ceiling
MAINTAINABILITY_FLOOR: float = 0.5  # maintainability score floor
MIN_SNAPSHOTS: int = 3          # minimum data points for regression
HOTSPOT_MIN_FILES: int = 2      # files in same dir trending up = hotspot
DAYS_URGENT: int = 30           # < 30 days → critical
DAYS_WARNING: int = 90          # 30–90 days → warning


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
            if cc_slope > 0 and current_cc < CC_THRESHOLD:
                days = _days_to_threshold(current_cc, cc_slope, CC_THRESHOLD)
                if days is not None and days < 365:
                    severity = (
                        "critical" if days < DAYS_URGENT
                        else "warning" if days < DAYS_WARNING
                        else "info"
                    )
                    confidence = min(0.92, 0.55 + (1.0 - days / 365.0) * 0.40)
                    predictions.append(
                        OraclePrediction(
                            lens=self.name,
                            title=f"CC Threshold Breach in ~{int(days)}d: {Path(fp).name}",
                            description=(
                                f"{fp} cyclomatic complexity is rising. "
                                f"Current: {current_cc:.1f}, slope: +{cc_slope:.2f}/week. "
                                f"Will exceed {CC_THRESHOLD} in ~{int(days)} days."
                            ),
                            confidence=confidence,
                            severity=severity,
                            category="trajectory_forecast",
                            data={
                                "file_path": fp,
                                "metric": "cyclomatic_complexity",
                                "current_value": round(current_cc, 2),
                                "slope_per_week": round(cc_slope, 4),
                                "threshold": CC_THRESHOLD,
                                "days_to_threshold": round(days, 1),
                                "blast_radius": blast,
                            },
                        )
                    )

            # Maintainability floor breach forecast
            if maint_slope < 0 and current_maint > MAINTAINABILITY_FLOOR:
                days = _days_to_threshold(current_maint, maint_slope, MAINTAINABILITY_FLOOR)
                if days is not None and days < 365:
                    severity = (
                        "critical" if days < DAYS_URGENT
                        else "warning" if days < DAYS_WARNING
                        else "info"
                    )
                    confidence = min(0.90, 0.55 + (1.0 - days / 365.0) * 0.38)
                    predictions.append(
                        OraclePrediction(
                            lens=self.name,
                            title=f"Maintainability Decline in ~{int(days)}d: {Path(fp).name}",
                            description=(
                                f"{fp} maintainability score is declining. "
                                f"Current: {current_maint:.3f}, slope: {maint_slope:.4f}/week. "
                                f"Will breach floor ({MAINTAINABILITY_FLOOR}) in ~{int(days)} days."
                            ),
                            confidence=confidence,
                            severity=severity,
                            category="trajectory_forecast",
                            data={
                                "file_path": fp,
                                "metric": "maintainability_score",
                                "current_value": round(current_maint, 4),
                                "slope_per_week": round(maint_slope, 4),
                                "threshold": MAINTAINABILITY_FLOOR,
                                "days_to_threshold": round(days, 1),
                                "blast_radius": blast,
                            },
                        )
                    )

        # Converging complexity hotspot detection
        for dir_path, file_slopes in dir_cc_slopes.items():
            trending_up = [(fp, slope) for fp, slope in file_slopes if slope > 0]
            if len(trending_up) < HOTSPOT_MIN_FILES:
                continue

            avg_slope = sum(s for _, s in trending_up) / len(trending_up)
            total_blast = sum(dependents.get(fp, 0) for fp, _ in trending_up)
            top_files = sorted(trending_up, key=lambda x: -x[1])[:5]

            confidence = min(0.88, 0.55 + len(trending_up) * 0.06)
            severity = "critical" if len(trending_up) >= 4 else "warning"

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

        # High commit-velocity directories
        for dir_path, week_counts in dir_activity.items():
            if len(week_counts) < 3:
                continue
            sorted_weeks = sorted(week_counts.items())
            xs = list(range(len(sorted_weeks)))
            ys = [count for _, count in sorted_weeks]
            slope, _ = _linear_regression(xs, ys)
            recent_avg = sum(ys[-3:]) / 3
            if slope > 1.0 and recent_avg > 5:
                predictions.append(
                    OraclePrediction(
                        lens=self.name,
                        title=f"High Commit Velocity: {dir_path}",
                        description=(
                            f"Directory '{dir_path}' shows accelerating commit activity "
                            f"(slope: +{slope:.1f} commits/week, recent avg: {recent_avg:.1f}/week). "
                            "High-churn areas are prone to integration conflicts and quality drift."
                        ),
                        confidence=0.70,
                        severity="info",
                        category="trajectory_forecast",
                        data={
                            "directory": dir_path,
                            "commit_slope_per_week": round(slope, 2),
                            "recent_weekly_avg": round(recent_avg, 1),
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
    import json

    lens = TrajectoryLens()
    predictions = lens.run()
    print(json.dumps([p.to_dict() for p in predictions], indent=2, default=str))
