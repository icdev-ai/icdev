#!/usr/bin/env python3
# CUI // SP-CTI
"""PMESII-PT Weighted Risk Index (WRI) compositor.

Computes a composite WRI score 0-100 from the eight PMESII-PT operational
environment dimensions using weights from args/strategos_iw_config.yaml, then
maps the WRI to an Escalation Ladder rung (1–5).

Usage
-----
    from tools.strategos.iw_engine import PMESIIPTCompositor

    comp = PMESIIPTCompositor()

    # Compute from explicit per-dimension scores (floats in [0, 1])
    result = comp.compute_and_save({
        "political": 0.6, "military": 0.8, "economic": 0.5,
        "social": 0.4, "information": 0.7, "infrastructure": 0.6,
        "physical_environment": 0.3, "time": 0.5,
    })
    # result = {"wri": 64.3, "escalation_rung": 4, "rung_label": "Crisis Response", ...}

    # Read latest stored assessment (falls back to zero-score if table empty)
    latest = comp.get_latest()
"""
from __future__ import annotations

import json
import pathlib
from datetime import datetime, timezone
from typing import Any
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.strategos.iw_engine")

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DIMENSIONS = (
    "political",
    "military",
    "economic",
    "social",
    "information",
    "infrastructure",
    "physical_environment",
    "time",
)

_CONFIG_PATH = pathlib.Path(__file__).parents[2] / "args" / "strategos_iw_config.yaml"

_DEFAULT_WEIGHTS: dict[str, float] = {
    "political":            0.15,
    "military":             0.20,
    "economic":             0.15,
    "social":               0.10,
    "information":          0.15,
    "infrastructure":       0.15,
    "physical_environment": 0.07,
    "time":                 0.03,
}

_DEFAULT_LADDER = [
    (0,  20,  1, "Routine Monitoring",   "#10b981"),
    (20, 40,  2, "Elevated Watch",       "#22d3ee"),
    (40, 60,  3, "Heightened Readiness", "#f59e0b"),
    (60, 80,  4, "Crisis Response",      "#f97316"),
    (80, 101, 5, "Imminent Action",      "#ef4444"),
]

_DEFAULT_DOMINANT_THRESHOLD = 0.70


# ---------------------------------------------------------------------------
# Config loader
# ---------------------------------------------------------------------------

def _load_config() -> dict[str, Any]:
    if yaml is None or not _CONFIG_PATH.exists():
        return {}
    with _CONFIG_PATH.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


# ---------------------------------------------------------------------------
# PMESIIPTCompositor
# ---------------------------------------------------------------------------

class PMESIIPTCompositor:
    """Weighted composite WRI from 8 PMESII-PT dimensions."""

    def __init__(self) -> None:
        cfg = _load_config().get("pmesii_pt", {})

        raw_weights = cfg.get("weights", _DEFAULT_WEIGHTS)
        total = sum(float(raw_weights.get(d, 0)) for d in _DIMENSIONS) or 1.0
        self.weights: dict[str, float] = {
            d: float(raw_weights.get(d, _DEFAULT_WEIGHTS.get(d, 0))) / total
            for d in _DIMENSIONS
        }

        ladder_cfg = cfg.get("escalation_ladder", {})
        if ladder_cfg:
            self.ladder = []
            for key in sorted(ladder_cfg):
                rung_cfg = ladder_cfg[key]
                rung_num = int(key.replace("rung", ""))
                self.ladder.append((
                    float(rung_cfg["min"]),
                    float(rung_cfg["max"]),
                    rung_num,
                    rung_cfg["label"],
                    rung_cfg["color"],
                ))
        else:
            self.ladder = list(_DEFAULT_LADDER)

        self.dominant_threshold: float = float(
            cfg.get("dominant_threshold", _DEFAULT_DOMINANT_THRESHOLD)
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def compute_wri(self, scores: dict[str, float]) -> float:
        """Return WRI in [0, 100] from per-dimension scores in [0, 1]."""
        wri = 0.0
        for dim in _DIMENSIONS:
            score = max(0.0, min(1.0, float(scores.get(dim, 0.0))))
            wri += score * self.weights[dim]
        return round(wri * 100, 2)

    def map_escalation_rung(self, wri: float) -> tuple[int, str, str]:
        """Return (rung_number, label, hex_color) for a given WRI."""
        for lo, hi, rung, label, color in self.ladder:
            if lo <= wri < hi:
                return rung, label, color
        # clamp to last rung if WRI == 100
        last = self.ladder[-1]
        return last[2], last[3], last[4]

    def compute_and_save(
        self,
        scores: dict[str, float],
        source: str | None = None,
    ) -> dict[str, Any]:
        """Compute WRI, map to rung, persist to DB, and return full assessment dict."""
        wri = self.compute_wri(scores)
        rung, label, color = self.map_escalation_rung(wri)

        dominant = [
            d for d in _DIMENSIONS
            if float(scores.get(d, 0.0)) >= self.dominant_threshold
        ]

        assessment: dict[str, Any] = {
            "wri":               wri,
            "escalation_rung":   rung,
            "rung_label":        label,
            "rung_color":        color,
            "dominant_indicators": dominant,
            "dimensions": {
                d: round(max(0.0, min(1.0, float(scores.get(d, 0.0)))), 4)
                for d in _DIMENSIONS
            },
            "weights": self.weights,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        self._persist(assessment, source)
        return assessment

    def get_latest(self) -> dict[str, Any]:
        """Return the most recent stored WRI assessment, or a zeroed fallback."""
        try:
            from tools.db.storage import get_connection  # noqa: PLC0415
            conn = get_connection()
            try:
                row = conn.execute(
                    "SELECT * FROM sg_wri_assessments ORDER BY created_at DESC LIMIT 1"
                ).fetchone()
            finally:
                conn.close()
        except Exception:
            row = None

        if row is None:
            return self._zeroed_assessment()

        row = dict(row)
        scores = {d: float(row.get(d, 0.0)) for d in _DIMENSIONS}
        wri = float(row["wri"])
        rung, label, color = self.map_escalation_rung(wri)

        try:
            dominant = json.loads(row.get("dominant_indicators") or "[]")
        except (json.JSONDecodeError, TypeError):
            dominant = []

        return {
            "wri":                 wri,
            "escalation_rung":     rung,
            "rung_label":          label,
            "rung_color":          color,
            "dominant_indicators": dominant,
            "dimensions":          {d: scores[d] for d in _DIMENSIONS},
            "weights":             self.weights,
            "timestamp":           row.get("created_at", ""),
            "source":              row.get("source"),
        }

    def derive_from_db(self) -> dict[str, Any]:
        """Heuristically derive dimension scores from existing Strategos DB tables.

        Falls back to zeros for any table that doesn't exist yet.
        """
        scores: dict[str, float] = {d: 0.0 for d in _DIMENSIONS}

        try:
            from tools.db.storage import get_connection  # noqa: PLC0415
            conn = get_connection()

            def _safe(sql: str, default: float = 0.0) -> float:
                try:
                    row = conn.execute(sql).fetchone()
                    return float(row[0]) if row and row[0] is not None else default
                except Exception:
                    try:
                        conn.rollback()
                    except Exception:
                        pass
                    return default

            # Military — ORBAT unit count normalised to 1 000 as saturation
            mil_units = _safe("SELECT COUNT(*) FROM sg_orbat_units")
            scores["military"] = min(1.0, mil_units / 1000)

            # Information — active IW effects
            info_effects = _safe("SELECT COUNT(*) FROM sg_iw_effects")
            scores["information"] = min(1.0, info_effects / 50)

            # Infrastructure — supply-chain disruption count
            infra_nodes = _safe(
                "SELECT COUNT(*) FROM sg_supply_nodes WHERE criticality >= 8"
            )
            scores["infrastructure"] = min(1.0, infra_nodes / 20)

            # Economic — severe conflict events as proxy
            econ_events = _safe(
                "SELECT COUNT(*) FROM sg_conflict_events "
                "WHERE event_type IN ('supply_disruption','sanction','blockade')"
            )
            scores["economic"] = min(1.0, econ_events / 30)

            # Social — displacement / humanitarian events
            social_events = _safe(
                "SELECT COUNT(*) FROM sg_conflict_events "
                "WHERE event_type IN ('displacement','riot','protest')"
            )
            scores["social"] = min(1.0, social_events / 40)

            # Political — HITL pending items as governance-stress proxy
            hitl_pending = _safe(
                "SELECT COUNT(*) FROM sg_hitl_items WHERE status = 'pending'"
            )
            scores["political"] = min(1.0, hitl_pending / 20)

            conn.close()
        except Exception:
            pass

        return self.compute_and_save(scores, source="auto_derived")

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _persist(self, assessment: dict[str, Any], source: str | None) -> None:
        try:
            import importlib  # noqa: PLC0415
            try:
                mod = importlib.import_module(
                    "tools.db.migrations.063_sg_wri_assessments.up"
                )
                mod.up()
            except Exception:
                pass

            from tools.db.storage import get_connection, is_pg  # noqa: PLC0415
            ph = "%s" if is_pg() else "?"
            dims = assessment["dimensions"]
            conn = get_connection()
            try:
                conn.execute(
                    f"INSERT INTO sg_wri_assessments "
                    f"(wri, escalation_rung, political, military, economic, social, "
                    f"information, infrastructure, physical_environment, time, "
                    f"dominant_indicators, source) "
                    f"VALUES ({','.join([ph]*12)})",  # nosec B608
                    (
                        assessment["wri"],
                        assessment["escalation_rung"],
                        dims["political"],
                        dims["military"],
                        dims["economic"],
                        dims["social"],
                        dims["information"],
                        dims["infrastructure"],
                        dims["physical_environment"],
                        dims["time"],
                        json.dumps(assessment["dominant_indicators"]),
                        source,
                    ),
                )
                conn.commit()
            finally:
                conn.close()
        except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
            logger.warning("_persist: best-effort INSERT into sg_wri_assessments failed (non-blocking): %s", exc)

    def _zeroed_assessment(self) -> dict[str, Any]:
        dims = {d: 0.0 for d in _DIMENSIONS}
        return {
            "wri":                 0.0,
            "escalation_rung":     1,
            "rung_label":          "Routine Monitoring",
            "rung_color":          "#10b981",
            "dominant_indicators": [],
            "dimensions":          dims,
            "weights":             self.weights,
            "timestamp":           datetime.now(timezone.utc).isoformat(),
            "source":              None,
        }
