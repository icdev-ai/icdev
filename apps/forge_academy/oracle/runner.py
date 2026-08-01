# CUI // SP-CTI
"""FORGE Academy Oracle Runner — orchestrates all 3 lenses and detects convergence."""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

from .base_lens import OraclePrediction

_log = logging.getLogger(__name__)
from .db import insert_convergence, insert_prediction, list_convergence_events, list_predictions
from .lens_content_quality import LensContentQuality
from .lens_learner_risk import LensLearnerRisk
from .lens_skill_gap import LensSkillGap
from .lens_aadc_readiness import LensAADCReadiness
from .lens_staleness_detector import LensStalenesssDetector
from .lens_ace_skill_gap import ACESkillGapLens
from .lens_agent_readiness import AgentReadinessLens

_LENSES = [
    LensLearnerRisk,
    LensContentQuality,
    LensSkillGap,
    LensAADCReadiness,
    LensStalenesssDetector,
    ACESkillGapLens,
    AgentReadinessLens,
]
_CONVERGENCE_MIN_LENSES = 2


class AcademyOracleRunner:
    """Run all Academy Oracle lenses and persist results."""

    def run(self) -> dict[str, Any]:
        """Execute all lenses, persist predictions, detect convergence. Returns summary dict."""
        all_predictions: list[OraclePrediction] = []

        for LensClass in _LENSES:
            # Instantiation is INSIDE the try: a lens that fails to construct
            # (e.g. an incomplete abstract subclass) must be skipped, not allowed
            # to abort the whole sweep — that would 500 the on-demand run route
            # and the scheduled academy_oracle_reflex. (penta-aca-06)
            try:
                preds = LensClass().run()
                all_predictions.extend(preds)
            except Exception:
                # Fail loud but keep sweeping: a broken lens (e.g. SQL column
                # drift) must be visible in the logs, not silently dropped —
                # that masking is how 3 of 7 lenses went dead unnoticed
                # (penta-fix-02). The sweep still continues so one bad lens
                # cannot 500 the on-demand route or the scheduled reflex.
                _log.exception(
                    "Academy Oracle lens %s failed — skipping this lens for the sweep",
                    getattr(LensClass, "__name__", str(LensClass)),
                )

        persisted = self._persist(all_predictions)
        convergence = self._detect_convergence(all_predictions)

        return {
            "predictions": [p.to_dict() for p in all_predictions],
            "persisted_count": persisted,
            "convergence": convergence,
        }

    def _persist(self, predictions: list[OraclePrediction]) -> int:
        count = 0
        for p in predictions:
            d = p.data
            row_id = insert_prediction(
                lens_id=p.lens,
                lens_name=p.lens.replace("_", " ").title(),
                subject_type=d.get("subject_type", "unknown"),
                subject_id=d.get("subject_id", "0"),
                prediction_type=d.get("prediction_type", p.category),
                prediction_text=p.description,
                confidence=p.confidence,
                severity=p.severity,
                horizon_days=d.get("horizon_days", 7),
                evidence={k: v for k, v in d.items()
                          if k not in ("subject_type", "subject_id", "prediction_type", "horizon_days")},
            )
            if row_id:
                count += 1
        return count

    def _detect_convergence(self, predictions: list[OraclePrediction]) -> list[dict]:
        # Group by (subject_type, subject_id) across lenses
        groups: dict[tuple, list[OraclePrediction]] = defaultdict(list)
        for p in predictions:
            key = (p.data.get("subject_type", ""), p.data.get("subject_id", ""))
            groups[key].append(p)

        events: list[dict] = []
        for (subject_type, subject_id), preds in groups.items():
            unique_lenses = {p.lens for p in preds}
            if len(unique_lenses) < _CONVERGENCE_MIN_LENSES:
                continue
            consensus_score = sum(p.confidence for p in preds) / len(preds)
            severity_rank = {"critical": 3, "warning": 2, "info": 1}
            severity = max((p.severity for p in preds), key=lambda s: severity_rank.get(s, 0))
            lens_names = ", ".join(sorted(unique_lenses))
            summary = (
                f"Subject {subject_type}:{subject_id} flagged by {len(unique_lenses)} lenses "
                f"({lens_names}) with consensus confidence {consensus_score:.2f}."
            )
            recommended = preds[0].recommendations[0] if preds[0].recommendations else None
            event_id = insert_convergence(
                subject_type=subject_type,
                subject_id=subject_id,
                lens_count=len(unique_lenses),
                consensus_score=round(consensus_score, 3),
                severity=severity,
                summary=summary,
                recommended_action=recommended,
            )
            events.append({
                "id": event_id,
                "subject_type": subject_type,
                "subject_id": subject_id,
                "lens_count": len(unique_lenses),
                "consensus_score": round(consensus_score, 3),
                "severity": severity,
                "summary": summary,
            })
        return events

    # ------------------------------------------------------------------
    # Read helpers for the dashboard
    # ------------------------------------------------------------------

    @staticmethod
    def get_predictions(**kwargs) -> list[dict]:
        return list_predictions(**kwargs)

    @staticmethod
    def get_convergence_events(**kwargs) -> list[dict]:
        return list_convergence_events(**kwargs)
