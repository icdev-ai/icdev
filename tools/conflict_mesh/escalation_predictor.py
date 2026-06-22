# CUI // SP-CTI
"""Escalation Predictor — compute and store conflict escalation risk scores.

Combines MLPatternEngine signal extraction with a weighted linear formula
to produce a 0–1 risk score per conflict event.  Scores are persisted to
the `conflict_predictions` table for audit and trend analysis.

Scoring formula:
    risk = 0.40 * violence_score
          + 0.20 * min(actor_count / 5, 1.0)
          + 0.20 * geographic_spread
          + 0.20 * min(abs(tone) / 10.0, 1.0)

Usage (library):
    from tools.conflict_mesh.ml_pattern_engine import MLPatternEngine
    from tools.conflict_mesh.escalation_predictor import EscalationPredictor

    engine = MLPatternEngine()
    predictor = EscalationPredictor(engine)
    score = predictor.predict(event_dict)
    row = predictor.predict_and_store(event_dict)

Usage (CLI):
    python tools/conflict_mesh/escalation_predictor.py --batch-since 2024-01-01 --threshold 0.7 --json
    python tools/conflict_mesh/escalation_predictor.py --event-id acled-12345 --json
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.conflict_mesh.ml_pattern_engine import EscalationSignals, MLPatternEngine  # noqa: E402

from tools.logging.icdev_logger import get_logger
log = get_logger("conflict_mesh.predictor")

_MODEL_VERSION_DEFAULT = "v1.0"


def _get_conn(db_path: Optional[str] = None):
    if db_path:
        import sqlite3
        return sqlite3.connect(db_path)
    from tools.db.storage import get_connection
    return get_connection()


class EscalationPredictor:
    """Computes and stores conflict escalation risk scores."""

    def __init__(
        self,
        pattern_engine: Optional[MLPatternEngine],
        db_path: Optional[str] = None,
    ) -> None:
        self._engine = pattern_engine
        self._db_path = db_path

    def _compute_score(self, signals: EscalationSignals) -> float:
        score = (
            0.40 * signals.violence_score
            + 0.20 * min(signals.actor_count / 5.0, 1.0)
            + 0.20 * signals.geographic_spread
            + 0.20 * min(abs(signals.tone) / 10.0, 1.0)
        )
        return max(0.0, min(1.0, score))

    def predict(self, event: Dict[str, Any]) -> float:
        """Return escalation risk score (0–1) for a single event."""
        if self._engine is None:
            return 0.0
        text = event.get("headline") or ""
        metadata = event.get("metadata") or {}
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except (json.JSONDecodeError, ValueError):
                metadata = {}
        signals = self._engine.extract_signals(text, metadata)
        return self._compute_score(signals)

    def predict_and_store(
        self,
        event: Dict[str, Any],
        model_version: str = _MODEL_VERSION_DEFAULT,
    ) -> Optional[Dict[str, Any]]:
        """Compute risk score and persist to conflict_predictions table."""
        if self._engine is None:
            return None
        text = event.get("headline") or ""
        metadata = event.get("metadata") or {}
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except (json.JSONDecodeError, ValueError):
                metadata = {}

        signals = self._engine.extract_signals(text, metadata)
        score = self._compute_score(signals)
        now = datetime.now(timezone.utc).isoformat()
        prediction_id = uuid.uuid4().hex

        row = {
            "id": prediction_id,
            "event_id": event.get("id", ""),
            "source": event.get("source", ""),
            "prediction_date": now[:10],
            "escalation_risk": score,
            "signals": json.dumps(asdict(signals)),
            "model_version": model_version,
            "confidence": min(signals.violence_score + 0.3, 1.0),
            "created_at": now,
        }
        try:
            conn = _get_conn(self._db_path)
            conn.execute(
                """
                INSERT INTO conflict_predictions
                    (id, event_id, source, prediction_date, escalation_risk,
                     signals, model_version, confidence, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["id"], row["event_id"], row["source"],
                    row["prediction_date"], row["escalation_risk"],
                    row["signals"], row["model_version"],
                    row["confidence"], row["created_at"],
                ),
            )
            conn.commit()
            conn.close()
        except Exception as exc:
            log.warning("Failed to store prediction for event %s: %s", event.get("id"), exc)
        return row

    def predict_batch(
        self,
        events: List[Dict[str, Any]],
        model_version: str = _MODEL_VERSION_DEFAULT,
    ) -> List[Dict[str, Any]]:
        """Compute and store predictions for a list of events."""
        results = []
        for evt in events:
            row = self.predict_and_store(evt, model_version=model_version)
            if row:
                results.append(row)
        return results

    def get_high_risk(
        self, threshold: float = 0.7, limit: int = 20
    ) -> List[Dict[str, Any]]:
        """Return stored predictions with escalation_risk >= threshold."""
        try:
            conn = _get_conn(self._db_path)
            conn.row_factory = lambda c, r: dict(zip([col[0] for col in c.description], r))
            rows = conn.execute(
                """
                SELECT * FROM conflict_predictions
                WHERE escalation_risk >= ?
                ORDER BY escalation_risk DESC, created_at DESC
                LIMIT ?
                """,
                (threshold, limit),
            ).fetchall()
            conn.close()
            return rows
        except Exception as exc:
            log.warning("get_high_risk query failed: %s", exc)
            return []


def main() -> None:
    parser = argparse.ArgumentParser(description="Conflict Escalation Predictor")
    parser.add_argument("--event-id", help="Score a single event from DB by id")
    parser.add_argument("--batch-since", help="Score all events since date (YYYY-MM-DD)")
    parser.add_argument("--threshold", type=float, default=0.7,
                        help="High-risk threshold (default: 0.7)")
    parser.add_argument("--limit", type=int, default=20, help="Max results")
    parser.add_argument("--json", action="store_true", dest="output_json")
    args = parser.parse_args()

    engine = MLPatternEngine()
    predictor = EscalationPredictor(engine)

    if args.batch_since:
        # Fetch events from DB and score them
        try:
            conn = _get_conn()
            conn.row_factory = lambda c, r: dict(zip([col[0] for col in c.description], r))
            events = conn.execute(
                "SELECT * FROM sg_conflict_events WHERE event_date >= ? LIMIT ?",
                (args.batch_since, args.limit),
            ).fetchall()
            conn.close()
        except Exception as exc:
            log.error("Failed to fetch events: %s", exc)
            events = []

        results = predictor.predict_batch(events)
        if args.output_json:
            print(json.dumps(results, indent=2))
        else:
            print(f"Scored {len(results)} events")

    elif args.event_id:
        try:
            conn = _get_conn()
            conn.row_factory = lambda c, r: dict(zip([col[0] for col in c.description], r))
            event = conn.execute(
                "SELECT * FROM sg_conflict_events WHERE id = ?", (args.event_id,)
            ).fetchone()
            conn.close()
        except Exception as exc:
            log.error("Failed to fetch event %s: %s", args.event_id, exc)
            event = None

        if event:
            row = predictor.predict_and_store(event)
            if args.output_json:
                print(json.dumps(row, indent=2))
            else:
                print(f"Score: {row['escalation_risk']:.3f}")
        else:
            print(f"Event {args.event_id} not found")

    else:
        high_risk = predictor.get_high_risk(threshold=args.threshold, limit=args.limit)
        if args.output_json:
            print(json.dumps(high_risk, indent=2))
        else:
            print(f"High-risk predictions (>= {args.threshold}): {len(high_risk)}")
            for r in high_risk:
                print(f"  {r['event_id']} — risk={r['escalation_risk']:.3f}")


if __name__ == "__main__":
    main()
