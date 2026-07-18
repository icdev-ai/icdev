#!/usr/bin/env python3
# CUI // SP-CTI
"""STRATEGOS — Bayesian I&W Updater + Exercise vs Pre-War GBM Classifier.

Two components:
  1. BayesianIWUpdater  — updates P(war) posterior as new evidence arrives.
     Uses Bayes' rule: P(war|evidence) ∝ P(evidence|war) × P(war)
     Stores posteriors in sg_bayesian_war_posteriors.

  2. ExerciseVsPreWarClassifier  — GBM-style gradient-boosted decision tree
     that classifies current signal patterns as either:
       - "exercise"  (military activity is routine training)
       - "pre_war"   (military activity indicates intent to attack)
     Uses 23 historical cases from historical_baselines.py as training data.
     Implemented as a pure-Python gradient boosting tree (no sklearn required).

Usage:
  from tools.strategos.iw_bayesian import BayesianIWUpdater, ExerciseVsPreWarClassifier

  updater = BayesianIWUpdater()
  result = updater.update("ukraine", evidence_type="military_buildup", strength=0.85)
  print(result["posterior"])  # P(war) after this evidence

  clf = ExerciseVsPreWarClassifier()
  clf.fit()  # loads from historical_baselines
  pred = clf.predict({"military_buildup": 0.9, "logistics_surge": 0.8, ...})
  print(pred["label"], pred["confidence"])
"""
from __future__ import annotations

import json
import math
import uuid
from datetime import datetime, timezone
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_conn():
    # Always route through the shared storage layer. Its translate layer
    # handles placeholder/dialect differences on both SQLite and PostgreSQL.
    # A raw sqlite3 fallback would reject the %s placeholders these queries
    # use, so it was dead weight that masked genuine import failures — let
    # an import error raise loudly instead.
    from tools.db.storage import get_connection
    return get_connection()


# ── Likelihood ratios for each evidence type ─────────────────────────────────

LIKELIHOOD_RATIOS: dict[str, dict[str, float]] = {
    # evidence_type: {P(E|war), P(E|no_war)}
    "military_buildup":      {"war": 0.92, "no_war": 0.25},
    "logistics_surge":       {"war": 0.88, "no_war": 0.20},
    "diplomatic_failure":    {"war": 0.80, "no_war": 0.30},
    "economic_strain":       {"war": 0.70, "no_war": 0.35},
    "info_warfare":          {"war": 0.75, "no_war": 0.40},
    "cyber_attack":          {"war": 0.65, "no_war": 0.15},
    "nuclear_signaling":     {"war": 0.55, "no_war": 0.05},
    "force_dispersal":       {"war": 0.78, "no_war": 0.40},
    "border_closure":        {"war": 0.82, "no_war": 0.15},
    "embassy_evacuation":    {"war": 0.90, "no_war": 0.05},
    "ais_dark_period":       {"war": 0.60, "no_war": 0.20},
    "troop_concentration":   {"war": 0.88, "no_war": 0.30},
    "air_defense_activation": {"war": 0.75, "no_war": 0.20},
    "exercise_declared":     {"war": 0.40, "no_war": 0.60},  # exercises are also normal
    "social_unrest":         {"war": 0.55, "no_war": 0.45},
}


class BayesianIWUpdater:
    """Sequential Bayesian updater for P(conflict onset | observed signals)."""

    DEFAULT_PRIOR = 0.05  # 5% baseline P(war in next 90 days)

    def get_latest_posterior(self, theater_id: str) -> float:
        """Retrieve the most recent posterior for this theater, or prior if none."""
        conn = _get_conn()
        try:
            row = conn.execute(
                "SELECT posterior FROM sg_bayesian_war_posteriors "
                "WHERE metadata_json::jsonb->>'theater_id' = %s "
                "ORDER BY created_at DESC LIMIT 1",
                (theater_id,),
            ).fetchone()
            return float(row[0]) if row else self.DEFAULT_PRIOR
        except Exception:
            try:
                row = conn.execute(
                    "SELECT posterior FROM sg_bayesian_war_posteriors "
                    "ORDER BY created_at DESC LIMIT 1",
                ).fetchone()
                return float(row[0]) if row else self.DEFAULT_PRIOR
            except Exception:
                return self.DEFAULT_PRIOR
        finally:
            conn.close()

    def update(
        self,
        theater_id: str,
        evidence_type: str,
        strength: float = 1.0,
        prior: float | None = None,
    ) -> dict[str, Any]:
        """Apply one evidence update via Bayes' rule. Returns updated posterior."""
        if evidence_type not in LIKELIHOOD_RATIOS:
            return {"error": f"Unknown evidence type: {evidence_type}"}

        p_prior = prior if prior is not None else self.get_latest_posterior(theater_id)
        lr = LIKELIHOOD_RATIOS[evidence_type]

        # Interpolate likelihoods by strength
        p_e_war = lr["war"] * strength + (1 - strength) * 0.5
        p_e_no_war = lr["no_war"] * strength + (1 - strength) * 0.5

        # Bayes: P(war|E) = P(E|war)*P(war) / P(E)
        p_e = p_e_war * p_prior + p_e_no_war * (1 - p_prior)
        if p_e == 0:
            p_posterior = p_prior
        else:
            p_posterior = (p_e_war * p_prior) / p_e

        # Clamp to [0.001, 0.999]
        p_posterior = max(0.001, min(0.999, p_posterior))
        likelihood_ratio = p_e_war / p_e_no_war if p_e_no_war > 0 else 99.0

        result: dict[str, Any] = {
            "theater_id": theater_id,
            "evidence_type": evidence_type,
            "strength": strength,
            "prior": round(p_prior, 6),
            "posterior": round(p_posterior, 6),
            "likelihood_ratio": round(likelihood_ratio, 4),
            "delta": round(p_posterior - p_prior, 6),
            "created_at": _now(),
        }
        self._persist(result)
        return result

    def update_batch(
        self,
        theater_id: str,
        signals: dict[str, float],
    ) -> dict[str, Any]:
        """Apply multiple signal updates sequentially. Returns final posterior."""
        prior = self.get_latest_posterior(theater_id)
        history = []
        for evidence_type, strength in signals.items():
            r = self.update(theater_id, evidence_type, strength, prior=prior)
            if "error" not in r:
                prior = r["posterior"]
                history.append(r)
        return {
            "theater_id": theater_id,
            "final_posterior": prior,
            "updates": len(history),
            "history": history,
        }

    def _persist(self, result: dict) -> None:
        conn = _get_conn()
        try:
            rid = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO sg_bayesian_war_posteriors "
                "(id, prior, evidence_type, strength, likelihood_ratio, posterior, metadata_json, created_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                (
                    rid, result["prior"], result["evidence_type"], result["strength"],
                    result["likelihood_ratio"], result["posterior"],
                    json.dumps({"theater_id": result["theater_id"], "delta": result["delta"]}),
                    result["created_at"],
                ),
            )
            conn.commit()
        except Exception:
            try:
                rid = str(uuid.uuid4())
                conn.execute(
                    "INSERT INTO sg_bayesian_war_posteriors "
                    "(id, prior, evidence_type, strength, likelihood_ratio, posterior, metadata_json, created_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        rid, result["prior"], result["evidence_type"], result["strength"],
                        result["likelihood_ratio"], result["posterior"],
                        json.dumps({"theater_id": result["theater_id"]}),
                        result["created_at"],
                    ),
                )
                conn.commit()
            except Exception:
                pass
        finally:
            conn.close()


# ── Exercise vs Pre-War GBM Classifier ───────────────────────────────────────

_FEATURES = [
    "military_buildup",
    "logistics_surge",
    "diplomatic_failure",
    "economic_strain",
    "info_warfare",
    "cyber",
    "nuclear_signaling",
]

# Gradient Boosting — pure Python decision stumps ensemble
# Each stump: (feature_idx, threshold, left_pred, right_pred, weight)
_Stump = tuple[int, float, float, float, float]


class _DecisionStump:
    def __init__(self, feat_idx: int, threshold: float, left: float, right: float):
        self.feat_idx = feat_idx
        self.threshold = threshold
        self.left = left
        self.right = right

    def predict(self, x: list[float]) -> float:
        return self.left if x[self.feat_idx] <= self.threshold else self.right


class ExerciseVsPreWarClassifier:
    """Pure-Python gradient boosting classifier (no sklearn).

    Trained on the 23 historical cases from historical_baselines.py.
    Labels: 0.0 = exercise/no_war, 1.0 = pre_war

    Exercise patterns have: high military_buildup + high logistics_surge
    but LOW diplomatic_failure and LOW nuclear_signaling.
    """

    EXERCISE_PRIOR = 0.6   # P(exercise) — exercises are more common than wars
    N_ESTIMATORS = 20
    LEARNING_RATE = 0.3

    def __init__(self) -> None:
        self._stumps: list[_DecisionStump] = []
        self._base_pred: float = 0.5
        self._fitted = False

    def fit(self, X: list[list[float]] | None = None, y: list[float] | None = None) -> None:
        """Train on historical cases (default) or provided data."""
        if X is None or y is None:
            X, y = self._load_historical_data()
        if not X:
            return

        self._base_pred = sum(y) / len(y)
        residuals = [yi - self._base_pred for yi in y]

        for _ in range(self.N_ESTIMATORS):
            best_stump, best_err = None, float("inf")
            for feat_idx in range(len(_FEATURES)):
                vals = sorted(set(x[feat_idx] for x in X))
                thresholds = [(vals[i] + vals[i + 1]) / 2 for i in range(len(vals) - 1)]
                for thr in thresholds:
                    left = [residuals[i] for i, x in enumerate(X) if x[feat_idx] <= thr]
                    right = [residuals[i] for i, x in enumerate(X) if x[feat_idx] > thr]
                    l_pred = sum(left) / len(left) if left else 0.0
                    r_pred = sum(right) / len(right) if right else 0.0
                    err = sum(
                        (residuals[i] - (l_pred if X[i][feat_idx] <= thr else r_pred)) ** 2
                        for i in range(len(X))
                    )
                    if err < best_err:
                        best_err = err
                        best_stump = _DecisionStump(feat_idx, thr, l_pred, r_pred)

            if best_stump is None:
                break
            self._stumps.append(best_stump)
            preds = [best_stump.predict(x) for x in X]
            residuals = [
                residuals[i] - self.LEARNING_RATE * preds[i]
                for i in range(len(residuals))
            ]

        self._fitted = True

    def _load_historical_data(self) -> tuple[list[list[float]], list[float]]:
        from tools.strategos.historical_baselines import get_pattern_matrix
        matrix = get_pattern_matrix()
        X, y = [], []
        for case in matrix:
            X.append(case["vector"])
            # Label: readiness_level >= 7 and composite >= 0.7 → pre_war
            label = 1.0 if (case["readiness_level"] >= 7 and case["composite"] >= 0.70) else 0.0
            y.append(label)
        # Add synthetic exercise patterns (high mil/logistics, low dipl/nuclear)
        exercises = [
            [0.85, 0.80, 0.15, 0.30, 0.20, 0.10, 0.05],  # clear exercise
            [0.75, 0.70, 0.20, 0.25, 0.25, 0.05, 0.02],
            [0.90, 0.85, 0.10, 0.20, 0.15, 0.08, 0.01],
            [0.70, 0.65, 0.18, 0.30, 0.20, 0.12, 0.03],
        ]
        for ex in exercises:
            X.append(ex)
            y.append(0.0)
        return X, y

    def _raw_score(self, x: list[float]) -> float:
        if not self._fitted:
            self.fit()
        score = self._base_pred
        for stump in self._stumps:
            score += self.LEARNING_RATE * stump.predict(x)
        return score

    def _sigmoid(self, x: float) -> float:
        return 1.0 / (1.0 + math.exp(-x))

    def predict(self, signals: dict[str, float]) -> dict[str, Any]:
        """Classify a signal dict. Returns label + confidence."""
        if not self._fitted:
            self.fit()
        x = [signals.get(f, 0.0) for f in _FEATURES]
        raw = self._raw_score(x)
        prob_pre_war = self._sigmoid(raw * 2 - 1)
        label = "pre_war" if prob_pre_war >= 0.5 else "exercise"
        confidence = prob_pre_war if label == "pre_war" else 1 - prob_pre_war
        return {
            "label": label,
            "confidence": round(confidence, 4),
            "prob_pre_war": round(prob_pre_war, 4),
            "prob_exercise": round(1 - prob_pre_war, 4),
            "features": dict(zip(_FEATURES, x)),
        }

    def predict_from_theater(self, theater_id: str) -> dict[str, Any]:
        """Load current signal scores from iw_scorers and classify."""
        from tools.strategos.iw_scorers import score_all_domains
        all_scores = score_all_domains(theater_id)
        dims = all_scores["domains"]
        signals = {
            "military_buildup": dims.get("military", 0.0),
            "logistics_surge": dims.get("military", 0.0) * 0.9,
            "diplomatic_failure": dims.get("political", 0.0),
            "economic_strain": dims.get("economic", 0.0),
            "info_warfare": dims.get("information", 0.0),
            "cyber": dims.get("information", 0.0) * 0.7,
            "nuclear_signaling": 0.0,
        }
        result = self.predict(signals)
        result["theater_id"] = theater_id
        result["domain_scores"] = dims
        return result
