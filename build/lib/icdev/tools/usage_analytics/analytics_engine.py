"""Usage analytics: aggregation, feature adoption scoring, and signal surfacing."""

import argparse
import hashlib
import json
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

import yaml

from tools.db.storage import get_connection

_CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "args" / "usage_analytics_config.yaml"

_DEFAULT_LOW_ADOPTION = 0.05
_DEFAULT_HIGH_ERROR = 0.10
_DEFAULT_SIGNAL_THRESHOLD = 0.65
_DEFAULT_ROLLUP_HOURS = 24


def _load_config() -> dict:
    if _CONFIG_PATH.exists():
        with open(_CONFIG_PATH, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}


@dataclass
class FeatureAdoptionResult:
    feature_tag: str
    adoption_score: float
    error_rate: float
    avg_duration_ms: float
    trend: Literal["rising", "flat", "declining"]
    hit_count: int
    unique_sessions: int


class AnalyticsEngine:
    def __init__(self) -> None:
        cfg = _load_config()
        scoring = cfg.get("scoring", {})
        agg = cfg.get("aggregation", {})
        self.low_adoption_threshold: float = scoring.get("low_adoption_threshold", _DEFAULT_LOW_ADOPTION)
        self.high_error_threshold: float = scoring.get("high_error_threshold", _DEFAULT_HIGH_ERROR)
        self.signal_threshold: float = scoring.get("signal_threshold", _DEFAULT_SIGNAL_THRESHOLD)
        self.rollup_interval_hours: int = agg.get("rollup_interval_hours", _DEFAULT_ROLLUP_HOURS)

    def aggregate(self, days: int = 1) -> int:
        """Roll up usage_events into usage_aggregates for each feature_tag × date."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        conn = get_connection()
        try:
            # total distinct sessions per date for the adoption_score denominator
            session_rows = conn.execute(
                """
                SELECT substr(occurred_at, 1, 10) AS day,
                       COUNT(DISTINCT user_session) AS cnt
                FROM usage_events
                WHERE occurred_at >= ?
                  AND feature_tag IS NOT NULL
                GROUP BY day
                """,
                (cutoff,),
            ).fetchall()
            total_sessions: dict[str, int] = {}
            for row in session_rows:
                total_sessions[row["day"]] = row["cnt"]

            detail_rows = conn.execute(
                """
                SELECT
                    feature_tag,
                    substr(occurred_at, 1, 10) AS day,
                    COUNT(*) AS hit_count,
                    COUNT(DISTINCT user_session) AS unique_sessions,
                    SUM(CASE WHEN status_code >= 400 THEN 1 ELSE 0 END) AS error_count,
                    AVG(duration_ms) AS avg_duration_ms
                FROM usage_events
                WHERE occurred_at >= ?
                  AND feature_tag IS NOT NULL
                GROUP BY feature_tag, day
                """,
                (cutoff,),
            ).fetchall()

            upserted = 0
            now_iso = datetime.now(timezone.utc).isoformat()
            for row in detail_rows:
                feature_tag = row["feature_tag"]
                day = row["day"]
                hit_count = row["hit_count"] or 0
                unique_sessions = row["unique_sessions"] or 0
                error_count = row["error_count"] or 0
                avg_duration = float(row["avg_duration_ms"] or 0.0)
                total = total_sessions.get(day, 1) or 1
                adoption_score = round(hit_count / total, 6)
                agg_id = f"{feature_tag}::{day}"

                existing = conn.execute(
                    "SELECT id FROM usage_aggregates WHERE id = ? LIMIT 1", (agg_id,)
                ).fetchone()
                if existing:
                    conn.execute(
                        """UPDATE usage_aggregates
                           SET hit_count=?, unique_sessions=?, error_count=?,
                               avg_duration_ms=?, adoption_score=?, updated_at=?
                           WHERE id=?""",
                        (hit_count, unique_sessions, error_count,
                         avg_duration, adoption_score, now_iso, agg_id),
                    )
                else:
                    conn.execute(
                        """INSERT INTO usage_aggregates
                           (id, feature_tag, date, hit_count, unique_sessions,
                            error_count, avg_duration_ms, adoption_score, updated_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (agg_id, feature_tag, day, hit_count, unique_sessions,
                         error_count, avg_duration, adoption_score, now_iso),
                    )
                upserted += 1

            conn.commit()
            return upserted
        finally:
            conn.close()

    def score(self) -> list[FeatureAdoptionResult]:
        """Return a FeatureAdoptionResult per feature, with trend vs prior window."""
        cutoff_7d = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")
        cutoff_3d = (datetime.now(timezone.utc) - timedelta(days=3)).strftime("%Y-%m-%d")

        conn = get_connection()
        try:
            rows = conn.execute(
                """
                SELECT feature_tag, date, hit_count, unique_sessions,
                       error_count, avg_duration_ms, adoption_score
                FROM usage_aggregates
                WHERE date >= ?
                ORDER BY feature_tag, date DESC
                """,
                (cutoff_7d,),
            ).fetchall()
        finally:
            conn.close()

        by_feature: dict[str, list] = {}
        for row in rows:
            by_feature.setdefault(row["feature_tag"], []).append(row)

        results: list[FeatureAdoptionResult] = []
        for feature_tag, feature_rows in by_feature.items():
            latest = feature_rows[0]
            hit_count = latest["hit_count"] or 0
            unique_sessions = latest["unique_sessions"] or 0
            error_count = latest["error_count"] or 0
            avg_dur = float(latest["avg_duration_ms"] or 0.0)
            adoption = float(latest["adoption_score"] or 0.0)
            error_rate = round(error_count / hit_count, 6) if hit_count else 0.0

            recent = [r for r in feature_rows if r["date"] >= cutoff_3d]
            older = [r for r in feature_rows if r["date"] < cutoff_3d]
            recent_avg = (
                sum(float(r["adoption_score"] or 0.0) for r in recent) / len(recent)
                if recent else 0.0
            )
            older_avg = (
                sum(float(r["adoption_score"] or 0.0) for r in older) / len(older)
                if older else recent_avg
            )
            delta = recent_avg - older_avg
            if delta > 0.01:
                trend: Literal["rising", "flat", "declining"] = "rising"
            elif delta < -0.01:
                trend = "declining"
            else:
                trend = "flat"

            results.append(
                FeatureAdoptionResult(
                    feature_tag=feature_tag,
                    adoption_score=round(adoption, 6),
                    error_rate=error_rate,
                    avg_duration_ms=round(avg_dur, 2),
                    trend=trend,
                    hit_count=hit_count,
                    unique_sessions=unique_sessions,
                )
            )

        results.sort(key=lambda r: r.adoption_score, reverse=True)
        return results

    def surface_signals(self) -> int:
        """Cross-register low-adoption or high-error features to innovation_signals."""
        scored = self.score()
        now_iso = datetime.now(timezone.utc).isoformat()
        today = now_iso[:10]
        inserted = 0

        conn = get_connection()
        try:
            for result in scored:
                low_adopt = result.adoption_score < self.low_adoption_threshold
                high_err = result.error_rate > self.high_error_threshold
                if not (low_adopt or high_err):
                    continue

                if low_adopt and high_err:
                    category = "low_adoption_high_error"
                elif low_adopt:
                    category = "low_adoption"
                else:
                    category = "high_error_rate"

                # day-scoped hash prevents duplicate signals within the same UTC day
                content_hash = hashlib.sha256(
                    f"{result.feature_tag}:{category}:{today}".encode()
                ).hexdigest()

                existing = conn.execute(
                    "SELECT id FROM innovation_signals WHERE content_hash = ? LIMIT 1",
                    (content_hash,),
                ).fetchone()
                if existing:
                    continue

                title = f"Usage signal: {result.feature_tag} ({category})"
                description = (
                    f"Feature '{result.feature_tag}' flagged — "
                    f"adoption={result.adoption_score:.4f}, "
                    f"error_rate={result.error_rate:.4f}, "
                    f"trend={result.trend}, "
                    f"hit_count={result.hit_count}"
                )
                metadata = json.dumps(
                    {
                        "feature_tag": result.feature_tag,
                        "adoption_score": result.adoption_score,
                        "error_rate": result.error_rate,
                        "trend": result.trend,
                        "hit_count": result.hit_count,
                        "unique_sessions": result.unique_sessions,
                    }
                )

                conn.execute(
                    """INSERT INTO innovation_signals
                       (id, source, source_type, title, description, url,
                        metadata, community_score, content_hash, discovered_at,
                        status, category)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'new', ?)""",
                    (
                        str(uuid.uuid4()),
                        "usage_analytics",
                        "feature_adoption",
                        title,
                        description,
                        "",
                        metadata,
                        result.adoption_score,
                        content_hash,
                        now_iso,
                        category,
                    ),
                )
                inserted += 1

            conn.commit()
            return inserted
        finally:
            conn.close()


def _print_report(engine: AnalyticsEngine, as_json: bool) -> None:
    results = engine.score()
    if as_json:
        print(json.dumps([asdict(r) for r in results], indent=2))
    else:
        print(f"{'Feature':<40} {'Score':>7} {'ErrRate':>8} {'AvgMs':>8} {'Trend':<12} {'Hits':>6}")
        print("-" * 90)
        for r in results:
            print(
                f"{r.feature_tag:<40} {r.adoption_score:>7.4f} {r.error_rate:>8.4f} "
                f"{r.avg_duration_ms:>8.1f} {r.trend:<12} {r.hit_count:>6}"
            )


def _run_once(engine: AnalyticsEngine, args: argparse.Namespace) -> None:
    if args.aggregate:
        n = engine.aggregate(days=getattr(args, "days", 1))
        if args.json:
            print(json.dumps({"aggregated": n}))
        else:
            print(f"Aggregated {n} feature-day rows.")

    if args.score:
        results = engine.score()
        if args.json:
            print(json.dumps([asdict(r) for r in results], indent=2))
        else:
            _print_report(engine, as_json=False)

    if args.surface_signals:
        n = engine.surface_signals()
        if args.json:
            print(json.dumps({"signals_inserted": n}))
        else:
            print(f"Surfaced {n} new innovation signal(s).")

    if args.report:
        _print_report(engine, as_json=args.json)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Usage analytics: aggregate, score, and surface adoption signals."
    )
    parser.add_argument("--aggregate", action="store_true", help="Roll up usage_events into usage_aggregates.")
    parser.add_argument("--days", type=int, default=1, help="Number of past days to aggregate (default: 1).")
    parser.add_argument("--score", action="store_true", help="Print feature adoption scores.")
    parser.add_argument("--surface-signals", dest="surface_signals", action="store_true",
                        help="Cross-register flagged features to innovation_signals.")
    parser.add_argument("--report", action="store_true", help="Print full adoption report.")
    parser.add_argument("--daemon", action="store_true",
                        help="Run aggregate+score+surface on rollup_interval_hours loop.")
    parser.add_argument("--json", dest="json", action="store_true", help="Output as JSON.")
    args = parser.parse_args()

    engine = AnalyticsEngine()

    if args.daemon:
        interval_s = engine.rollup_interval_hours * 3600
        while True:
            daemon_args = argparse.Namespace(
                aggregate=True, days=1, score=True, surface_signals=True,
                report=False, json=args.json
            )
            _run_once(engine, daemon_args)
            time.sleep(interval_s)
    else:
        _run_once(engine, args)


if __name__ == "__main__":
    main()
