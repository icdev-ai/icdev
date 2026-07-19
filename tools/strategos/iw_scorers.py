#!/usr/bin/env python3
# CUI // SP-CTI
"""STRATEGOS — I&W Domain Signal Scorers.

Five PMESII-PT domain scorers that each produce a normalized [0, 1] score
consumed by the PMESIIPTCompositor (iw_engine.py).

Scorers:
  EconomicSignalScorer    — reads sg_economic_signals + CUSUM alerts
  MilitarySignalScorer    — reads sg_military_signal_scores + sg_ghost_signals
  DiplomaticSignalScorer  — reads sg_iw_indicators (diplomatic category)
  InfrastructureScorer    — reads sg_infrastructure_signals
  InformationScorer       — reads sg_information_scores + sg_iw_events (info)

Each scorer exposes:
  .score(theater_id, window_days=30) -> float in [0, 1]
  .score_detail(theater_id, window_days=30) -> dict with breakdown

Usage:
  from tools.strategos.iw_scorers import EconomicSignalScorer, MilitarySignalScorer
  econ = EconomicSignalScorer()
  score = econ.score("ukraine")
  detail = econ.score_detail("ukraine", window_days=60)
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

_NOW = lambda: datetime.now(timezone.utc)  # noqa: E731


def _get_conn():
    # Always route through the shared storage layer. Its translate layer
    # handles placeholder/dialect differences on both SQLite and PostgreSQL.
    # A raw sqlite3 fallback would reject the %s placeholders these queries
    # use, so it was dead weight that masked genuine import failures — let
    # an import error raise loudly instead.
    from tools.db.storage import get_connection
    return get_connection()


def _cutoff(window_days: int) -> str:
    return (_NOW() - timedelta(days=window_days)).isoformat()


def _clamp(v: float) -> float:
    return max(0.0, min(1.0, v))


# ── Economic Signal Scorer ───────────────────────────────────────────────────

class EconomicSignalScorer:
    """Score economic stress from sg_economic_signals CUSUM alerts."""

    def score(self, theater_id: str, window_days: int = 90) -> float:
        return self.score_detail(theater_id, window_days)["score"]

    def score_detail(self, theater_id: str, window_days: int = 90) -> dict[str, Any]:
        cutoff = _cutoff(window_days)
        conn = _get_conn()
        try:
            rows = conn.execute(
                "SELECT signal_type, value, metadata_json FROM sg_economic_signals "
                "WHERE theater_id = %s AND created_at >= %s "
                "ORDER BY signal_ts DESC",
                (theater_id, cutoff),
            ).fetchall()
        except Exception:
            try:
                rows = conn.execute(
                    "SELECT signal_type, value, metadata_json FROM sg_economic_signals "
                    "WHERE theater_id = %s AND created_at >= %s ORDER BY signal_ts DESC",
                    (theater_id, cutoff),
                ).fetchall()
            except Exception:
                rows = []
        finally:
            conn.close()

        if not rows:
            return {"score": 0.0, "alert_rate": 0.0, "signals": 0, "source": "economic"}

        alerts = 0
        total = len(rows)
        for _, val, meta_json in rows:
            try:
                meta = json.loads(meta_json or "{}")
                if meta.get("cusum_alert"):
                    alerts += 1
            except Exception:
                pass

        alert_rate = alerts / total if total else 0.0
        # Score = alert rate × coverage factor (more signals = more confidence)
        coverage = min(1.0, total / 50)
        score = _clamp(alert_rate * 0.7 + coverage * 0.3)

        return {
            "score": round(score, 4),
            "alert_rate": round(alert_rate, 4),
            "alerts": alerts,
            "signals": total,
            "source": "economic",
        }


# ── Military Signal Scorer ───────────────────────────────────────────────────

class MilitarySignalScorer:
    """Score military threat from sg_military_signal_scores + sg_ghost_signals."""

    def score(self, theater_id: str, window_days: int = 30) -> float:
        return self.score_detail(theater_id, window_days)["score"]

    def score_detail(self, theater_id: str, window_days: int = 30) -> dict[str, Any]:
        cutoff = _cutoff(window_days)
        conn = _get_conn()
        try:
            # Military composite scores
            mil_rows = conn.execute(
                "SELECT composite_score, confidence FROM sg_military_signal_scores "
                "WHERE created_at >= %s ORDER BY created_at DESC LIMIT 20",
                (cutoff,),
            ).fetchall()
            # Ghost track anomaly signals
            ghost_rows = conn.execute(
                "SELECT confidence FROM sg_ghost_signals "
                "WHERE detected_at >= %s ORDER BY detected_at DESC LIMIT 50",
                (cutoff,),
            ).fetchall()
        except Exception:
            try:
                mil_rows = conn.execute(
                    "SELECT composite_score, confidence FROM sg_military_signal_scores "
                    "WHERE created_at >= %s ORDER BY created_at DESC LIMIT 20",
                    (cutoff,),
                ).fetchall()
                ghost_rows = conn.execute(
                    "SELECT confidence FROM sg_ghost_signals "
                    "WHERE detected_at >= %s ORDER BY detected_at DESC LIMIT 50",
                    (cutoff,),
                ).fetchall()
            except Exception:
                mil_rows = []
                ghost_rows = []
        finally:
            conn.close()

        mil_score = 0.0
        if mil_rows:
            # Weighted by confidence
            weighted = sum(row[0] * row[1] for row in mil_rows if row[1] > 0)
            total_w = sum(row[1] for row in mil_rows if row[1] > 0)
            mil_score = weighted / total_w if total_w > 0 else 0.0

        ghost_score = 0.0
        if ghost_rows:
            high_conf = sum(1 for r in ghost_rows if r[0] >= 0.7)
            ghost_score = min(1.0, high_conf / 10)

        score = _clamp(mil_score * 0.7 + ghost_score * 0.3)

        return {
            "score": round(score, 4),
            "military_composite": round(mil_score, 4),
            "ghost_track_score": round(ghost_score, 4),
            "military_assessments": len(mil_rows),
            "ghost_signals": len(ghost_rows),
            "source": "military",
        }


# ── Diplomatic Signal Scorer ─────────────────────────────────────────────────

class DiplomaticSignalScorer:
    """Score diplomatic tension from sg_iw_indicators (diplomatic + political categories)."""

    def score(self, theater_id: str, window_days: int = 60) -> float:
        return self.score_detail(theater_id, window_days)["score"]

    def score_detail(self, theater_id: str, window_days: int = 60) -> dict[str, Any]:
        conn = _get_conn()
        try:
            rows = conn.execute(
                "SELECT status, weight, category FROM sg_iw_indicators "
                "WHERE theater = %s AND category IN ('diplomatic', 'political', 'economic')",
                (theater_id,),
            ).fetchall()
        except Exception:
            try:
                rows = conn.execute(
                    "SELECT status, weight, category FROM sg_iw_indicators "
                    "WHERE theater = %s AND category IN ('diplomatic', 'political', 'economic')",
                    (theater_id,),
                ).fetchall()
            except Exception:
                rows = []
        finally:
            conn.close()

        if not rows:
            return {"score": 0.0, "observed": 0, "total": 0, "source": "diplomatic"}

        total_weight = sum(float(r[1] or 1.0) for r in rows)
        observed_weight = sum(
            float(r[1] or 1.0) for r in rows if r[0] in ("observed", "confirmed")
        )
        score = _clamp(observed_weight / total_weight if total_weight > 0 else 0.0)

        return {
            "score": round(score, 4),
            "observed": sum(1 for r in rows if r[0] in ("observed", "confirmed")),
            "denied": sum(1 for r in rows if r[0] == "denied"),
            "total": len(rows),
            "source": "diplomatic",
        }


# ── Infrastructure Scorer ────────────────────────────────────────────────────

class InfrastructureScorer:
    """Score infrastructure stress from sg_infrastructure_signals."""

    def score(self, theater_id: str, window_days: int = 30) -> float:
        return self.score_detail(theater_id, window_days)["score"]

    def score_detail(self, theater_id: str, window_days: int = 30) -> dict[str, Any]:
        cutoff = _cutoff(window_days)
        conn = _get_conn()
        try:
            rows = conn.execute(
                "SELECT signal_value, confidence FROM sg_infrastructure_signals "
                "WHERE computed_at >= %s ORDER BY computed_at DESC LIMIT 30",
                (cutoff,),
            ).fetchall()
        except Exception:
            try:
                rows = conn.execute(
                    "SELECT signal_value, confidence FROM sg_infrastructure_signals "
                    "WHERE computed_at >= %s ORDER BY computed_at DESC LIMIT 30",
                    (cutoff,),
                ).fetchall()
            except Exception:
                rows = []
        finally:
            conn.close()

        if not rows:
            return {"score": 0.0, "signals": 0, "source": "infrastructure"}

        weighted = sum(float(r[0]) * float(r[1]) for r in rows)
        total_w = sum(float(r[1]) for r in rows)
        score = _clamp(weighted / total_w if total_w > 0 else 0.0)

        return {
            "score": round(score, 4),
            "signals": len(rows),
            "avg_confidence": round(total_w / len(rows), 4),
            "source": "infrastructure",
        }


# ── Information / Narrative Scorer ───────────────────────────────────────────

class InformationScorer:
    """Score information/narrative threat from sg_information_scores + sg_iw_events."""

    def score(self, theater_id: str, window_days: int = 30) -> float:
        return self.score_detail(theater_id, window_days)["score"]

    def score_detail(self, theater_id: str, window_days: int = 30) -> dict[str, Any]:
        cutoff = _cutoff(window_days)
        conn = _get_conn()
        try:
            info_rows = conn.execute(
                "SELECT information_score, rhetoric_score, dehumanization_index, "
                "cyber_recon_score, disinformation_surge "
                "FROM sg_information_scores "
                "WHERE created_at >= %s ORDER BY created_at DESC LIMIT 10",
                (cutoff,),
            ).fetchall()
        except Exception:
            try:
                info_rows = conn.execute(
                    "SELECT information_score, rhetoric_score, dehumanization_index, "
                    "cyber_recon_score, disinformation_surge "
                    "FROM sg_information_scores "
                    "WHERE created_at >= %s ORDER BY created_at DESC LIMIT 10",
                    (cutoff,),
                ).fetchall()
            except Exception:
                info_rows = []
        finally:
            conn.close()

        if not info_rows:
            return {"score": 0.0, "assessments": 0, "source": "information"}

        latest = info_rows[0]
        info_score = float(latest[0] or 0)
        rhetoric = float(latest[1] or 0)
        dehumanization = float(latest[2] or 0)
        cyber_recon = float(latest[3] or 0)
        disinfo_surge = float(latest[4] or 0)

        score = _clamp(
            info_score * 0.3
            + rhetoric * 0.2
            + dehumanization * 0.2
            + cyber_recon * 0.15
            + disinfo_surge * 0.15
        )

        return {
            "score": round(score, 4),
            "information_score": info_score,
            "rhetoric_score": rhetoric,
            "dehumanization_index": dehumanization,
            "cyber_recon_score": cyber_recon,
            "disinformation_surge": disinfo_surge,
            "assessments": len(info_rows),
            "source": "information",
        }


# ── Composite scorer ─────────────────────────────────────────────────────────

def score_all_domains(theater_id: str, window_days: int = 30) -> dict[str, Any]:
    """Run all 5 domain scorers and return a PMESII-ready dict."""
    econ = EconomicSignalScorer().score_detail(theater_id, window_days)
    mil = MilitarySignalScorer().score_detail(theater_id, window_days)
    dipl = DiplomaticSignalScorer().score_detail(theater_id, window_days)
    infra = InfrastructureScorer().score_detail(theater_id, window_days)
    info = InformationScorer().score_detail(theater_id, window_days)

    return {
        "theater_id": theater_id,
        "window_days": window_days,
        "scored_at": _NOW().isoformat(),
        "domains": {
            "economic": econ["score"],
            "military": mil["score"],
            "political": dipl["score"],
            "information": info["score"],
            "infrastructure": infra["score"],
        },
        "detail": {
            "economic": econ,
            "military": mil,
            "diplomatic": dipl,
            "infrastructure": infra,
            "information": info,
        },
    }
