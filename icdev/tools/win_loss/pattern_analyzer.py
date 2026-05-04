"""Win/loss pattern analyzer — correlates feature tags with proposal outcomes."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

_CONFIG_PATH = Path(__file__).parent.parent.parent / "args" / "win_loss_config.yaml"


def _load_config() -> dict[str, Any]:
    try:
        with open(_CONFIG_PATH, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        return {}


@dataclass
class FeatureImpact:
    feature_tag: str
    win_rate: float
    impact_score: float
    win_count: int
    loss_count: int


class PatternAnalyzer:
    def __init__(self, config: dict[str, Any] | None = None) -> None:
        cfg = config or _load_config()
        self._min_outcomes: int = int(
            cfg.get("analysis", {}).get("min_outcomes_for_pattern", 5)
        )

    def analyze(self) -> list[FeatureImpact]:
        from tools.db.storage import get_connection

        conn = get_connection()
        try:
            return self._run(conn)
        finally:
            conn.close()

    def _run(self, conn: Any) -> list[FeatureImpact]:
        rows = self._fetch_rows(conn)
        if not rows:
            return []

        feature_wins: dict[str, int] = {}
        feature_losses: dict[str, int] = {}
        total_won = 0
        total_lost = 0

        for outcome, _, raw_features in rows:
            tags = _parse_features(raw_features)
            if outcome == "won":
                total_won += 1
                bucket = feature_wins
            else:
                total_lost += 1
                bucket = feature_losses
            for tag in tags:
                bucket[tag] = bucket.get(tag, 0) + 1

        total_outcomes = total_won + total_lost
        if total_outcomes == 0:
            return []

        baseline = total_won / total_outcomes
        results: list[FeatureImpact] = []

        for tag in set(feature_wins) | set(feature_losses):
            wins = feature_wins.get(tag, 0)
            losses = feature_losses.get(tag, 0)
            total_with = wins + losses
            if total_with < self._min_outcomes:
                continue
            win_rate = wins / total_with
            # Normalize (win_rate - baseline) from [-1, 1] to [0, 1]
            impact_score = (win_rate - baseline + 1.0) / 2.0
            results.append(
                FeatureImpact(
                    feature_tag=tag,
                    win_rate=win_rate,
                    impact_score=impact_score,
                    win_count=wins,
                    loss_count=losses,
                )
            )

        results.sort(key=lambda x: x.impact_score, reverse=True)
        return results

    def _fetch_rows(self, conn: Any) -> list[tuple[str, str, str | None]]:
        """Return (outcome, opportunity_id, features_col) rows, degrading gracefully."""
        base_where = "WHERE outcome IN ('won', 'lost')"
        for col in ("features_demonstrated", "our_strengths"):
            try:
                return conn.execute(
                    f"SELECT outcome, opportunity_id, {col} "
                    f"FROM pg_win_loss_records {base_where}"
                ).fetchall()
            except Exception:
                continue
        return []


def _parse_features(raw: str | None) -> list[str]:
    """Parse a JSON array, JSON object, or comma-separated feature tag string."""
    if not raw:
        return []
    stripped = raw.strip()
    if stripped.startswith("["):
        try:
            items = json.loads(stripped)
            if isinstance(items, list):
                return [str(i).strip() for i in items if i]
        except (json.JSONDecodeError, ValueError):
            pass
    if stripped.startswith("{"):
        try:
            obj = json.loads(stripped)
            for key in ("features", "tags", "features_demonstrated"):
                if key in obj and isinstance(obj[key], list):
                    return [str(i).strip() for i in obj[key] if i]
        except (json.JSONDecodeError, ValueError):
            pass
    return [t.strip() for t in stripped.split(",") if t.strip()]
