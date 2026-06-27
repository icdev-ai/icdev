"""Win/loss analysis engine — orchestrates pattern analysis, DB writes, and cross-registration."""
from __future__ import annotations

import argparse
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from tools.win_loss.pattern_analyzer import FeatureImpact, PatternAnalyzer

_CONFIG_PATH = Path(__file__).parent.parent.parent / "args" / "win_loss_config.yaml"


def _load_config() -> dict[str, Any]:
    try:
        with open(_CONFIG_PATH, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        return {}


class WinLossEngine:
    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self._cfg = config if config is not None else _load_config()
        analysis = self._cfg.get("analysis", {})
        self._auto_signal_threshold: float = float(
            analysis.get("auto_signal_threshold", 0.70)
        )
        self._top_k: int = int(self._cfg.get("output", {}).get("top_k_features", 10))

    def run(self) -> dict:
        from tools.db.storage import get_connection

        patterns: list[FeatureImpact] = PatternAnalyzer(config=self._cfg).analyze()
        top_win = [p.feature_tag for p in patterns if p.win_rate >= 0.5][: self._top_k]
        top_loss = [p.feature_tag for p in patterns if p.win_rate < 0.5][: self._top_k]
        outcomes_analyzed = 0

        conn = get_connection()
        try:
            try:
                row = conn.execute(
                    "SELECT COUNT(*) FROM pg_win_loss_records"
                    " WHERE outcome IN ('won', 'lost')"
                ).fetchone()
                outcomes_analyzed = int(row[0]) if row else 0
            except Exception:
                pass

            run_id = str(uuid.uuid4())
            run_at = datetime.now(timezone.utc).isoformat()

            try:
                conn.execute(
                    "INSERT INTO win_loss_analysis_runs"
                    " (id, run_at, outcomes_analyzed, patterns_found,"
                    "  top_win_features, top_loss_features, result_json)"
                    " VALUES (%s, %s, %s, %s, %s, %s, %s)",
                    (
                        run_id,
                        run_at,
                        outcomes_analyzed,
                        len(patterns),
                        json.dumps(top_win),
                        json.dumps(top_loss),
                        json.dumps({"run_id": run_id, "patterns_found": len(patterns)}),
                    ),
                )
                for p in patterns:
                    conn.execute(
                        "INSERT INTO win_loss_feature_impacts"
                        " (id, run_id, feature_tag, win_count, loss_count,"
                        "  win_rate, impact_score, analyzed_at)"
                        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                        (
                            str(uuid.uuid4()),
                            run_id,
                            p.feature_tag,
                            p.win_count,
                            p.loss_count,
                            p.win_rate,
                            p.impact_score,
                            run_at,
                        ),
                    )
                    if p.impact_score >= self._auto_signal_threshold:
                        conn.execute(
                            "INSERT OR IGNORE INTO creative_feature_gaps"
                            " (id, feature_name, description, gap_score,"
                            "  signal_ids, discovered_at)"
                            " VALUES (%s, %s, %s, %s, %s, %s)",
                            (
                                str(uuid.uuid4()),
                                p.feature_tag,
                                f"Win/loss signal: {p.feature_tag}",
                                p.impact_score,
                                "[]",
                                run_at,
                            ),
                        )
                conn.commit()
            except Exception:
                try:
                    conn.rollback()
                except Exception:
                    pass
        finally:
            conn.close()

        return {
            "outcomes_analyzed": outcomes_analyzed,
            "patterns_found": len(patterns),
            "top_win_features": top_win,
        }


def _main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Win/loss analysis engine")
    parser.add_argument("--run", action="store_true", help="Run pattern analysis")
    parser.add_argument("--status", action="store_true", help="Show last run status")
    parser.add_argument("--top-features", action="store_true", help="Show top features")
    parser.add_argument("--limit", type=int, default=10, help="Limit output rows")
    parser.add_argument("--daemon", action="store_true", help="Run once (daemon compat)")
    parser.add_argument("--json", action="store_true", dest="json_out", help="JSON output")
    args = parser.parse_args(argv)

    if args.run or args.daemon:
        result = WinLossEngine().run()
        if args.json_out:
            print(json.dumps(result))
        else:
            print(f"Patterns found: {result['patterns_found']}")
            print(f"Outcomes analyzed: {result['outcomes_analyzed']}")
    elif args.status:
        if args.json_out:
            print(json.dumps({"status": "ok"}))
        else:
            print("Win/loss engine ready")
    elif args.top_features:
        patterns = PatternAnalyzer().analyze()
        top = patterns[: args.limit]
        if args.json_out:
            print(
                json.dumps(
                    [{"feature_tag": p.feature_tag, "win_rate": p.win_rate} for p in top]
                )
            )
        else:
            for p in top:
                print(f"{p.feature_tag}: win_rate={p.win_rate:.2f} impact={p.impact_score:.2f}")
    else:
        parser.print_help()


if __name__ == "__main__":
    _main()
