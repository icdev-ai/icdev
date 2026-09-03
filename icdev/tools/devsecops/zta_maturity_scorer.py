#!/usr/bin/env python3
# CUI // SP-CTI
"""ZTA 7-Pillar Maturity Scorer — assess Zero Trust Architecture maturity per DoD strategy.

Scores each of the 7 ZTA pillars (User Identity, Device, Network, Application/Workload,
Data, Visibility/Analytics, Automation/Orchestration) from 0.0-1.0 and computes
a weighted aggregate maturity level (Traditional / Advanced / Optimal).

ADR D120: ZTA maturity model uses DoD 7-pillar scoring tracked per project per pillar.
ADR D123: ZTA posture score feeds into cATO monitor as additional evidence dimension.

Usage:
    python tools/devsecops/zta_maturity_scorer.py --project-id "proj-123" --all --json
    python tools/devsecops/zta_maturity_scorer.py --project-id "proj-123" --pillar network --json
    python tools/devsecops/zta_maturity_scorer.py --project-id "proj-123" --trend --json
"""

import argparse
import json
import os
import uuid
import sys
from pathlib import Path

# kax-conflict-05: run by path, sys.path[0] is this file's own directory — never
# the import root. Bootstrap it before the first first-party import below.
# parents[N] is whatever holds this file's `tools` package: the repo root in
# tools/, and <repo>/icdev in the icdev/ mirror (which is what a wheel ships).
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.db.storage import get_connection
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(BASE_DIR / "data" / "icdev.db")))

try:
    import yaml
except ImportError:
    yaml = None

PILLARS = [
    "user_identity",
    "device",
    "network",
    "application_workload",
    "data",
    "visibility_analytics",
    "automation_orchestration",
]

# rmf-zt-02: the band a pillar carries when NOTHING about it could be measured.
# It is deliberately not a fourth rung below 'traditional' — a pillar nobody
# assessed is not a pillar assessed and found immature, and the two must never
# render the same. Admitted into the zta_maturity_scores CHECK by migration
# 20260903003116.
UNMEASURED = "unmeasured"

# Strings that are PRESENT in evidence_data and still carry no evidence. A
# writer that stored an empty container has recorded a tick, not a measurement.
_EMPTY_EVIDENCE_LITERALS = {"", "null", "none", "{}", "[]", '""', "''"}


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def _load_config() -> dict:
    """Load ZTA config from YAML."""
    config_path = BASE_DIR / "args" / "zta_config.yaml"
    if yaml and config_path.exists():
        with open(config_path) as f:
            return yaml.safe_load(f) or {}
    return {
        "pillars": {p: {"weight": 1.0 / len(PILLARS)} for p in PILLARS},
        "maturity_levels": {
            "traditional": {"score_range": [0.0, 0.33]},
            "advanced": {"score_range": [0.34, 0.66]},
            "optimal": {"score_range": [0.67, 1.0]},
        },
    }


def _get_db():
    conn = get_connection()
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


# ---------------------------------------------------------------------------
# Evidence gathering
# ---------------------------------------------------------------------------


def has_evidence_data(value) -> bool:
    """True when a ``zta_posture_evidence`` row carries actual supporting data.

    This is the whole distinction the pillar scores now rest on. A row whose
    ``evidence_data`` is NULL — or an empty string, or an empty JSON container —
    is a CHECKBOX: some writer set ``status='current'`` and recorded nothing
    behind it. Counting those as evidence is what let a pillar report a maturity
    band nobody had assessed.

    A scalar ``0`` or ``false`` IS evidence: "we measured it and it was zero" is
    a real answer, and the same trap that makes an empty result look measured
    would, inverted, make a measured zero look absent.
    """
    if value is None:
        return False
    if isinstance(value, (bytes, bytearray)):
        try:
            value = value.decode("utf-8")
        except UnicodeDecodeError:
            return True  # opaque bytes are still SOMETHING; only absence is not
    if isinstance(value, str):
        return value.strip().lower() not in _EMPTY_EVIDENCE_LITERALS
    if isinstance(value, (dict, list, tuple, set)):
        return bool(value)
    return True


def _gather_pillar_evidence(project_id: str, pillar: str, conn) -> dict:
    """Gather evidence for a specific ZTA pillar from project data.

    Checks: project controls (NIST 800-53), devsecops profile, and the ZTA
    posture evidence table.

    Every check reports its own ``measured`` flag, and an UNMEASURED check
    contributes NOTHING to the pillar score rather than a 0.0 (rmf-zt-02). The
    two are different facts and averaging the first in as the second is how a
    pillar over an empty table scored a maturity band: with no rows at all the
    old ``current / len(evidence_types)`` was structurally 0/5, and with rows
    whose ``evidence_data`` was NULL it was a ratio over a checkbox list.
    """
    config = _load_config()
    pillar_def = config.get("pillars", {}).get(pillar, {})
    nist_controls = pillar_def.get("nist_800_53_controls", [])
    evidence_types = pillar_def.get("evidence_types", [])

    evidence = {"pillar": pillar, "checks": []}

    # --- NIST 800-53 control implementations for this pillar ---------------
    if nist_controls:
        placeholders = ",".join(["%s"] * len(nist_controls))
        # `implementation_status`, NOT `status` — project_controls has never had
        # a `status` column, in init_icdev_db.py or on any live database. This
        # query raised UndefinedColumn on PostgreSQL on every call, which is why
        # zta_maturity_scores held 0 rows: the scorer could not complete a single
        # assessment. It survived because the test fixture DECLARED a `status`
        # column, so the suite passed against a schema that does not exist
        # (rmf-zt-02).
        rows = conn.execute(
            f"""SELECT control_id, implementation_status FROM project_controls
                WHERE project_id = %s AND control_id IN ({placeholders})""",  # nosec B608 -- table/column names are internal constants, not user input
            [project_id] + nist_controls,
        ).fetchall()

        implemented = sum(1 for r in rows if r["implementation_status"] == "implemented")
        total = len(nist_controls)
        check = {
            "type": "nist_controls",
            "implemented": implemented,
            "total": total,
            "rows_present": len(rows),
        }
        if rows:
            # Rows exist, so this IS a measurement — including a measured 0.0,
            # which is a real finding and must keep rendering as one.
            check["measured"] = True
            check["state"] = "measured"
            check["score"] = round(implemented / total, 3)
        else:
            # No control row was ever recorded for this pillar. implemented/total
            # would be 0/5 — a constant wearing the name of a measurement.
            check["measured"] = False
            check["state"] = "no_control_rows"
            check["score"] = None
        evidence["checks"].append(check)

    # --- ZTA posture evidence ---------------------------------------------
    rows = (
        conn.execute(
            """SELECT evidence_type, status, evidence_data FROM zta_posture_evidence
           WHERE project_id = %s AND evidence_type IN ({})""".format(  # nosec B608 -- table/column names are internal constants, not user input
                ",".join(["%s"] * len(evidence_types))
            ),
            [project_id] + evidence_types,
        ).fetchall()
        if evidence_types
        else []
    )

    total_types = len(evidence_types)
    current_rows = [r for r in rows if r["status"] == "current"]
    backed = [r for r in current_rows if has_evidence_data(r["evidence_data"])]
    attested = [r for r in current_rows if not has_evidence_data(r["evidence_data"])]

    check = {
        "type": "posture_evidence",
        "total": total_types,
        "rows_present": len(rows),
        "current": len(current_rows),
        # THE TWO NUMBERS, never merged into one. `evidence_backed` is what the
        # deployment can prove; `self_attested` is what it merely claims.
        "evidence_backed": len(backed),
        "self_attested": len(attested),
        "evidence_backed_types": sorted(r["evidence_type"] for r in backed),
        "self_attested_types": sorted(r["evidence_type"] for r in attested),
    }
    # The self-attested ratio is reported ALONGSIDE the score and never folded
    # into it. It is None — never 0.0 — when nothing was declared or nothing was
    # ticked, so "claimed nothing" cannot read as "claimed zero".
    check["self_attested_score"] = (
        round(len(current_rows) / total_types, 3)
        if total_types and current_rows
        else None
    )

    if total_types == 0:
        check["measured"] = False
        check["state"] = "not_declared"
        check["score"] = None
    elif not backed:
        # THE DEFECT rmf-zt-02 EXISTS FOR. Either the table holds no row for this
        # pillar at all, or every 'current' row is a tick with nothing behind it.
        # In both cases current/total is a ratio over a checkbox list, so refuse
        # to produce one.
        check["measured"] = False
        check["state"] = "self_attested_only" if current_rows else (
            "no_evidence_rows" if not rows else "no_current_rows"
        )
        check["score"] = None
    else:
        check["measured"] = True
        check["state"] = "evidence_backed"
        check["score"] = round(len(backed) / total_types, 3)
    evidence["checks"].append(check)

    # --- DevSecOps profile stages -----------------------------------------
    profile_row = conn.execute(
        "SELECT active_stages FROM devsecops_profiles WHERE project_id = %s", (project_id,)
    ).fetchone()

    if profile_row:
        active_stages = json.loads(profile_row["active_stages"] or "[]")
        # Map pillars to relevant DevSecOps stages
        pillar_stage_map = {
            "user_identity": [],
            "device": [],
            "network": ["policy_as_code"],
            "application_workload": ["sast", "container_scan", "image_signing"],
            "data": ["secret_detection", "sbom_attestation"],
            "visibility_analytics": ["sca", "license_compliance"],
            "automation_orchestration": ["rasp", "policy_as_code"],
        }
        relevant = pillar_stage_map.get(pillar, [])
        if relevant:
            active_relevant = [s for s in relevant if s in active_stages]
            # A profile row exists, so the stage set IS observed — an empty
            # active list is a measured 0.0, not an absence.
            evidence["checks"].append(
                {
                    "type": "devsecops_stages",
                    "active": active_relevant,
                    "total_relevant": len(relevant),
                    "measured": True,
                    "state": "measured",
                    "score": round(len(active_relevant) / len(relevant), 3),
                }
            )

    return evidence


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def score_pillar(project_id: str, pillar: str) -> dict:
    """Score a single ZTA pillar (0.0 - 1.0), or report it UNMEASURED.

    ``score`` is ``None`` — never 0.0 — and ``maturity_level`` is
    ``'unmeasured'`` when no check for this pillar could be measured. A pillar
    nobody has assessed and a pillar assessed and found immature justify
    opposite decisions, so they must never render as the same number.

    ``self_attested_score`` is carried BESIDE ``score`` and is never averaged
    into it: it is the fraction of the pillar's declared posture evidence types
    carrying a 'current' tick, whatever is or is not behind that tick.

    Returns:
        Dict with pillar, score, maturity_level, self_attested_score,
        self_attested_maturity, measured, evidence.
    """
    if pillar not in PILLARS:
        return {"error": f"Invalid pillar: {pillar}", "valid_pillars": PILLARS}

    conn = _get_db()
    try:
        evidence = _gather_pillar_evidence(project_id, pillar, conn)
        checks = evidence["checks"]
        measured = [c for c in checks if c.get("measured") and c.get("score") is not None]

        if measured:
            score = round(min(sum(c["score"] for c in measured) / len(measured), 1.0), 3)
            maturity = _score_to_maturity(score)
        else:
            score = None
            maturity = UNMEASURED

        # The self-attested number stands alone, from the posture check only.
        posture = next((c for c in checks if c["type"] == "posture_evidence"), {})
        self_attested_score = posture.get("self_attested_score")
        self_attested_maturity = (
            _score_to_maturity(self_attested_score)
            if self_attested_score is not None
            else UNMEASURED
        )

        # Store score. `score` is NULL and `maturity_level` is 'unmeasured' for
        # an unmeasured pillar — migration 20260903003116 widened the CHECK.
        score_id = f"zta-{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """INSERT INTO zta_maturity_scores
               (id, project_id, pillar, score, maturity_level, evidence, created_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (score_id, project_id, pillar, score, maturity, json.dumps(checks), now),
        )
        conn.commit()

        return {
            "project_id": project_id,
            "pillar": pillar,
            "score": score,
            "maturity_level": maturity,
            "measured": bool(measured),
            "measured_checks": [c["type"] for c in measured],
            "unmeasured_checks": [
                {"type": c["type"], "state": c.get("state")}
                for c in checks
                if not c.get("measured")
            ],
            "evidence_backed": posture.get("evidence_backed"),
            "self_attested": posture.get("self_attested"),
            "self_attested_score": self_attested_score,
            "self_attested_maturity": self_attested_maturity,
            "evidence": checks,
            "assessed_at": now,
        }
    finally:
        conn.close()


def score_all_pillars(project_id: str) -> dict:
    """Score all 7 ZTA pillars and compute weighted aggregate.

    Returns:
        Dict with per-pillar scores, overall score, maturity level.
    """
    config = _load_config()
    pillar_weights = {p: config.get("pillars", {}).get(p, {}).get("weight", 1.0 / len(PILLARS)) for p in PILLARS}

    pillar_results = []
    weighted_sum = 0.0
    total_weight = 0.0
    attested_sum = 0.0
    attested_weight = 0.0

    for pillar in PILLARS:
        result = score_pillar(project_id, pillar)
        if "error" in result:
            continue
        pillar_results.append(result)
        weight = pillar_weights.get(pillar, 0.0)
        # An UNMEASURED pillar is left out of the numerator AND the denominator.
        # Carrying it at 0.0 would drag the aggregate down as if it had been
        # assessed and failed; carrying it at its weight with no score would
        # silently rescale everything else.
        if result["score"] is not None:
            weighted_sum += result["score"] * weight
            total_weight += weight
        if result.get("self_attested_score") is not None:
            attested_sum += result["self_attested_score"] * weight
            attested_weight += weight

    measured_pillars = [r["pillar"] for r in pillar_results if r["score"] is not None]
    unmeasured_pillars = [r["pillar"] for r in pillar_results if r["score"] is None]

    # None, NEVER 0.0, over an empty denominator (args/perfect_score_gate.yaml
    # bans the mirror-image `else 100.0`; the same rule bans a fabricated floor).
    overall_score = round(weighted_sum / total_weight, 3) if total_weight > 0 else None
    overall_maturity = (
        _score_to_maturity(overall_score) if overall_score is not None else UNMEASURED
    )
    self_attested_overall = (
        round(attested_sum / attested_weight, 3) if attested_weight > 0 else None
    )
    self_attested_overall_maturity = (
        _score_to_maturity(self_attested_overall)
        if self_attested_overall is not None
        else UNMEASURED
    )

    # Store overall score
    conn = _get_db()
    try:
        score_id = f"zta-{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """INSERT INTO zta_maturity_scores
               (id, project_id, pillar, score, maturity_level, evidence, created_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (
                score_id,
                project_id,
                "overall",
                overall_score,
                overall_maturity,
                json.dumps(
                    {
                        "pillars": [
                            {
                                "pillar": r["pillar"],
                                "score": r["score"],
                                "self_attested_score": r.get("self_attested_score"),
                            }
                            for r in pillar_results
                        ],
                        "measured_pillars": measured_pillars,
                        "unmeasured_pillars": unmeasured_pillars,
                        "self_attested_score": self_attested_overall,
                    }
                ),
                now,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    # Identify weakest pillars — only among those actually MEASURED. Ranking an
    # unmeasured pillar as "weakest" would send a human to fix a pillar that may
    # be fine and is merely unobserved.
    scored = [r for r in pillar_results if r["score"] is not None]
    sorted_pillars = sorted(scored, key=lambda x: x["score"])
    weakest = sorted_pillars[:2]

    # DIC Canvas Synergy — emit ZIG gap events for below-threshold pillars (dsyn-emit-03)
    # An unmeasured pillar emits NO gap event: a gap is a claim about posture,
    # and there is no posture reading to make it from.
    _ZIG_PILLAR_THRESHOLD = 0.70
    for r in scored:
        if r["score"] < _ZIG_PILLAR_THRESHOLD:
            try:
                from tools.security.zig.event_emitter import emit_pillar_gap_detected
                emit_pillar_gap_detected(
                    pillar_name=r["pillar"],
                    current_score=round(r["score"] * 100, 1),
                    threshold=_ZIG_PILLAR_THRESHOLD * 100,
                    project_id=project_id,
                )
            except Exception:
                pass  # event emission never blocks scoring

    return {
        "project_id": project_id,
        "overall_score": overall_score,
        "overall_maturity": overall_maturity,
        # The second number, alongside and never merged with the first.
        "self_attested_score": self_attested_overall,
        "self_attested_maturity": self_attested_overall_maturity,
        # Coverage, so a partial aggregate can never read as full coverage.
        "declared_pillars": len(PILLARS),
        "measured_pillars": measured_pillars,
        "unmeasured_pillars": unmeasured_pillars,
        "pillar_scores": {r["pillar"]: r["score"] for r in pillar_results},
        "self_attested_pillar_scores": {
            r["pillar"]: r.get("self_attested_score") for r in pillar_results
        },
        "pillar_details": pillar_results,
        "weakest_pillars": [{"pillar": w["pillar"], "score": w["score"]} for w in weakest],
        "recommendation": _generate_recommendation(
            overall_maturity, weakest, unmeasured_pillars
        ),
    }


def run_scheduled_assessment(project_id: str, drift_threshold: float = 0.1) -> dict:
    """Run a scheduled ZTA assessment and detect score drift (G-18).

    Compares the current all-pillar score against the most recent previous
    assessment. If overall score drops by more than drift_threshold, a drift
    alert is emitted.

    Args:
        project_id: Project identifier.
        drift_threshold: Fractional drop (e.g. 0.1 = 10%) that triggers a drift alert.

    Returns:
        Dict with current score, previous score, drift, and alert flag.
    """
    from tools.logging.icdev_logger import get_logger as _gl
    _log = _gl("devsecops.zta_scheduler")

    # Run current assessment
    current = score_all_pillars(project_id)
    # None when nothing was measured. Defaulting to 0.0 here would turn an
    # unmeasured board into a full-height drop against any real prior score.
    current_score = current.get("overall_score")

    # Retrieve the previous assessment (the row before the one just inserted)
    conn = _get_db()
    try:
        rows = conn.execute(
            """SELECT score, created_at
               FROM zta_maturity_scores
               WHERE project_id = %s AND pillar = 'overall'
               ORDER BY created_at DESC
               LIMIT 2""",
            (project_id,),
        ).fetchall()
    finally:
        conn.close()

    previous_score: float | None = None
    if len(rows) >= 2 and rows[1]["score"] is not None:
        # rows[0] is the one just written, rows[1] is the previous. A NULL score
        # is an UNMEASURED assessment, not a zero — comparing against it would
        # manufacture a 100%-of-current "drift" out of an absent baseline.
        previous_score = float(rows[1]["score"])

    # None, never 0.0: "no drift" and "drift could not be computed" are
    # different facts and only the first is reassuring.
    drift: float | None = None
    drift_alert = False
    if previous_score is not None and current_score is not None:
        drift = previous_score - current_score  # positive = score dropped
        drift_alert = drift >= drift_threshold

    if drift_alert:
        _log.warning(
            "ZTA maturity drift detected: project=%s previous=%.3f current=%.3f drop=%.3f (threshold=%.3f)",
            project_id, previous_score, current_score, drift, drift_threshold,
        )
        # DIC Canvas Synergy — emit posture score drop event (dsyn-emit-03)
        try:
            from tools.security.zig.event_emitter import emit_posture_score_drop
            emit_posture_score_drop(
                pillar_name="overall",
                previous_score=round(previous_score * 100, 1),
                current_score=round(current_score * 100, 1),
                project_id=project_id,
            )
        except Exception:
            pass  # event emission never blocks assessment
    elif current_score is None:
        _log.info(
            "ZTA scheduled assessment: project=%s score=UNMEASURED maturity=%s "
            "(unmeasured pillars: %s) — drift not computable",
            project_id, current.get("overall_maturity"),
            ", ".join(current.get("unmeasured_pillars", [])) or "none",
        )
    else:
        _log.info(
            "ZTA scheduled assessment: project=%s score=%.3f maturity=%s drift=%s",
            project_id, current_score, current.get("overall_maturity"),
            "unmeasured" if drift is None else f"{drift:.3f}",
        )

    return {
        "project_id": project_id,
        "current_score": current_score,
        "previous_score": previous_score,
        "drift": None if drift is None else round(drift, 4),
        "drift_alert": drift_alert,
        "drift_threshold": drift_threshold,
        "overall_maturity": current.get("overall_maturity"),
        "assessed_at": current.get("assessed_at", datetime.now(timezone.utc).isoformat()),
        "pillar_scores": current.get("pillar_scores", {}),
        # Carried through so a caller reading `drift: null` can tell an
        # unmeasured board from a first-ever assessment.
        "self_attested_score": current.get("self_attested_score"),
        "measured_pillars": current.get("measured_pillars", []),
        "unmeasured_pillars": current.get("unmeasured_pillars", []),
    }


def get_trend(project_id: str, days: int = 90) -> dict:
    """Get ZTA maturity score trend over time.

    Returns:
        Dict with historical scores for overall and per-pillar.
    """
    conn = _get_db()
    try:
        rows = conn.execute(
            """SELECT pillar, score, maturity_level, created_at
               FROM zta_maturity_scores
               WHERE project_id = %s AND created_at >= datetime('now', %s)
               ORDER BY created_at ASC""",
            (project_id, f"-{days} days"),
        ).fetchall()

        trend = {}
        for row in rows:
            pillar = row["pillar"]
            if pillar not in trend:
                trend[pillar] = []
            trend[pillar].append(
                {
                    "score": row["score"],
                    "maturity_level": row["maturity_level"],
                    "date": row["created_at"],
                }
            )

        return {
            "project_id": project_id,
            "period_days": days,
            "trends": trend,
            "data_points": len(rows),
        }
    finally:
        conn.close()


def get_latest_score(project_id: str | None = None) -> dict | None:
    """Read the most recent persisted ZTA maturity scores for a project.

    Read-only accessor consumed by the ZIG bridge
    (tools/security_canvas/zig_assessor.py::_try_zta_bridge). It never runs a
    new assessment — it returns whatever ``score_all_pillars`` last persisted
    to ``zta_maturity_scores``. The bridge calls this with no arguments and
    reads ``pillar_scores[<pillar_key>]``, so the returned pillar scores are
    the raw persisted values in the **0.0–1.0** range (the same scale the
    scorer stores; the CHECK constraint bounds ``score`` to [0.0, 1.0]).

    Args:
        project_id: Project to read. When None, the project of the most
            recently created score row is used (latest assessment wins).

    Returns:
        Dict of the shape::

            {
                "project_id": str,
                "overall_score": float (0.0-1.0) | None,
                "overall_maturity": str,     # incl. 'unmeasured'
                "pillar_scores": {pillar_key: float 0.0-1.0, ...},
                "unmeasured_pillars": [pillar_key, ...],
                "assessed_at": str | None,   # ISO timestamp of latest row
            }

        or ``None`` when no assessment has ever been persisted.

    A pillar the scorer could not measure persists ``score = NULL`` and is
    OMITTED from ``pillar_scores`` (it is named in ``unmeasured_pillars``
    instead), so the bridge's ``zta_key in pillar_scores`` test falls through to
    the pure ZIG score rather than importing a fabricated 0.0 (rmf-zt-02).
    ``overall_score`` is ``None`` — never 0.0 — on an unmeasured deployment.
    """
    conn = _get_db()
    try:
        if project_id is None:
            latest = conn.execute(
                "SELECT project_id FROM zta_maturity_scores "
                "ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
            if not latest:
                return None
            project_id = latest["project_id"]

        rows = conn.execute(
            """SELECT pillar, score, maturity_level, created_at
               FROM zta_maturity_scores
               WHERE project_id = %s
               ORDER BY created_at ASC""",
            (project_id,),
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        return None

    # Later rows (ASC by created_at) overwrite earlier ones, so each pillar
    # ends up holding its most recent score.
    latest_by_pillar: dict[str, dict] = {}
    for r in rows:
        latest_by_pillar[r["pillar"]] = {
            # None stays None — an UNMEASURED pillar persists score = NULL, and
            # coercing it to 0.0 here would hand the ZIG bridge a hard zero for a
            # pillar nobody assessed (rmf-zt-02). The bridge drops a None.
            "score": float(r["score"]) if r["score"] is not None else None,
            "maturity_level": r["maturity_level"],
            "created_at": r["created_at"],
        }

    # Unmeasured pillars are OMITTED from pillar_scores rather than carried as
    # None: every reader indexes this map to get a number.
    pillar_scores = {
        p: latest_by_pillar[p]["score"]
        for p in PILLARS
        if p in latest_by_pillar and latest_by_pillar[p]["score"] is not None
    }
    unmeasured_pillars = [
        p
        for p in PILLARS
        if p in latest_by_pillar and latest_by_pillar[p]["score"] is None
    ]
    overall = latest_by_pillar.get("overall", {})
    assessed_at = max(
        (v["created_at"] for v in latest_by_pillar.values() if v["created_at"]),
        default=None,
    )

    return {
        "project_id": project_id,
        # None, never 0.0 — an unmeasured overall must not read as 'traditional'.
        "overall_score": overall.get("score"),
        "overall_maturity": overall.get("maturity_level") or UNMEASURED,
        "pillar_scores": pillar_scores,
        "unmeasured_pillars": unmeasured_pillars,
        "assessed_at": assessed_at,
    }


def latest_posture_summary(project_id: str | None = None) -> dict:
    """Render-ready summary of the last persisted ZTA assessment (rmf-zt-02).

    READ-ONLY. It never runs an assessment — a browse surface that scored seven
    pillars on page load would put a database sweep on every render, and a page
    that writes what it displays cannot be used to check the writer.

    The two maturity numbers are returned as TWO fields and are never combined:

        evidence_backed_score   what the deployment can PROVE — the weighted
                                score over pillars with an evidence-backed
                                signal. None when no pillar had one.
        self_attested_score     what the deployment CLAIMS — the weighted score
                                over 'current' posture-evidence ticks, whatever
                                is or is not behind them. None when nothing was
                                ticked.

    ``state`` separates the four reasons a score can be absent, because only one
    of them is a statement about the system's posture:

        never_assessed  no zta_maturity_scores row exists. Nobody has looked.
        unmeasured      an assessment ran and NO pillar had evidence behind it.
        partial         some pillars measured, some not.
        measured        every declared pillar had an evidence-backed signal.

    ``never_assessed`` and ``unmeasured`` are NOT clean bills of health, and the
    template says so in words rather than drawing an empty bar — an empty bar is
    what a MEASURED zero looks like.
    """
    summary = {
        "project_id": project_id,
        "state": "never_assessed",
        "evidence_backed_score": None,
        "evidence_backed_maturity": UNMEASURED,
        "self_attested_score": None,
        "self_attested_maturity": UNMEASURED,
        "declared_pillars": len(PILLARS),
        "measured_pillars": [],
        "unmeasured_pillars": [],
        "pillars": [],
        "assessed_at": None,
    }

    conn = _get_db()
    try:
        if project_id is None:
            latest = conn.execute(
                "SELECT project_id FROM zta_maturity_scores "
                "ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
            if not latest:
                return summary
            project_id = latest["project_id"]
            summary["project_id"] = project_id

        rows = conn.execute(
            """SELECT pillar, score, maturity_level, evidence, created_at
               FROM zta_maturity_scores
               WHERE project_id = %s
               ORDER BY created_at ASC""",
            (project_id,),
        ).fetchall()
    except Exception as exc:  # noqa: BLE001 - an unreadable store is not a clean board
        summary["state"] = "never_assessed"
        summary["error"] = f"zta_maturity_scores unreadable: {exc}"
        return summary
    finally:
        conn.close()

    if not rows:
        return summary

    # Later rows (ASC) overwrite earlier ones, so each pillar keeps its latest.
    latest_by_pillar: dict[str, dict] = {}
    for r in rows:
        latest_by_pillar[r["pillar"]] = dict(r)

    overall = latest_by_pillar.get("overall", {})
    payload = {}
    if overall.get("evidence"):
        try:
            parsed = json.loads(overall["evidence"])
            if isinstance(parsed, dict):
                payload = parsed
        except (ValueError, TypeError):
            payload = {}

    attested_by_pillar = {
        entry.get("pillar"): entry.get("self_attested_score")
        for entry in payload.get("pillars", [])
        if isinstance(entry, dict)
    }

    pillars = []
    for p in PILLARS:
        row = latest_by_pillar.get(p)
        if row is None:
            pillars.append(
                {
                    "pillar": p,
                    "label": p.replace("_", " ").title(),
                    "score": None,
                    "maturity_level": UNMEASURED,
                    "self_attested_score": None,
                    "measured": False,
                }
            )
            continue
        pillars.append(
            {
                "pillar": p,
                "label": p.replace("_", " ").title(),
                "score": row["score"],
                "maturity_level": row["maturity_level"] or UNMEASURED,
                "self_attested_score": attested_by_pillar.get(p),
                "measured": row["score"] is not None,
            }
        )

    summary["pillars"] = pillars
    summary["measured_pillars"] = [p["pillar"] for p in pillars if p["measured"]]
    summary["unmeasured_pillars"] = [p["pillar"] for p in pillars if not p["measured"]]
    summary["evidence_backed_score"] = overall.get("score")
    summary["evidence_backed_maturity"] = overall.get("maturity_level") or UNMEASURED
    summary["self_attested_score"] = payload.get("self_attested_score")
    summary["self_attested_maturity"] = (
        _score_to_maturity(summary["self_attested_score"])
        if summary["self_attested_score"] is not None
        else UNMEASURED
    )
    summary["assessed_at"] = max(
        (r["created_at"] for r in rows if r["created_at"]), default=None
    )

    if not summary["measured_pillars"]:
        summary["state"] = "unmeasured"
    elif summary["unmeasured_pillars"]:
        summary["state"] = "partial"
    else:
        summary["state"] = "measured"
    return summary


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _score_to_maturity(score: float) -> str:
    """Map score to maturity level."""
    config = _load_config()
    levels = config.get("maturity_levels", {})
    for level_id, level_def in levels.items():
        lo, hi = level_def.get("score_range", [0, 1])
        if lo <= score <= hi:
            return level_id
    return "traditional"


def _generate_recommendation(maturity: str, weakest: list, unmeasured: list | None = None) -> str:
    """Generate improvement recommendation.

    An unmeasured pillar is named as a COLLECTION gap, never as a maturity gap —
    "go and measure it" and "go and improve it" are different instructions, and
    the first must not be issued as the second.
    """
    unmeasured = unmeasured or []
    unmeasured_names = [p.replace("_", " ").title() for p in unmeasured]

    if maturity == UNMEASURED:
        return (
            "ZTA maturity is UNMEASURED: no pillar has an evidence-backed signal. "
            "Collect posture evidence (zta_posture_evidence.evidence_data) or record "
            "NIST 800-53 control status before reading any maturity number. This is "
            "not a clean bill of health."
        )

    parts = []
    if maturity == "optimal":
        parts.append("ZTA maturity is optimal. Maintain continuous monitoring and improvement.")
    elif weakest:
        weak_names = [w["pillar"].replace("_", " ").title() for w in weakest]
        target = "optimal" if maturity == "advanced" else "advanced"
        parts.append(
            f"Focus on improving {' and '.join(weak_names)} pillars to reach {target} maturity."
        )
    if unmeasured_names:
        parts.append(
            f"{len(unmeasured_names)} pillar(s) are UNMEASURED and excluded from the score: "
            f"{', '.join(unmeasured_names)}."
        )
    return " ".join(parts)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Rendering
#
# The two maturity numbers are printed on SEPARATE lines under SEPARATE labels
# and are never combined into one figure (rmf-zt-02). "Evidence-backed" is what
# the deployment can PROVE; "self-attested" is what it has merely TICKED. One
# number cannot say both, and the blended one is the one that reads as
# reassurance.
# ---------------------------------------------------------------------------

_BAR_WIDTH = 20


def _bar(score: float | None) -> str:
    """Render a score bar, or a distinct UNMEASURED rule.

    An unmeasured pillar deliberately does NOT draw an empty bar: an empty bar
    is exactly what a MEASURED 0% looks like, and those two justify opposite
    decisions — one is "go and fix it", the other is "go and look at it".
    """
    if score is None:
        return "─" * _BAR_WIDTH
    filled = int(score * _BAR_WIDTH)
    return "█" * filled + "░" * (_BAR_WIDTH - filled)


def _pct(score: float | None) -> str:
    return "  n/a" if score is None else f"{score:.1%}"


def _render_overall(result: dict) -> None:
    print(f"Project: {result['project_id']}")
    print()
    print("  Maturity — two numbers, never merged:")
    print(
        f"    Evidence-backed (proven)   {_bar(result.get('overall_score'))} "
        f"{_pct(result.get('overall_score')):>6s}  "
        f"[{(result.get('overall_maturity') or UNMEASURED).upper()}]"
    )
    print(
        f"    Self-attested  (claimed)   {_bar(result.get('self_attested_score'))} "
        f"{_pct(result.get('self_attested_score')):>6s}  "
        f"[{(result.get('self_attested_maturity') or UNMEASURED).upper()}]"
    )

    unmeasured = result.get("unmeasured_pillars") or []
    measured = result.get("measured_pillars") or []
    declared = result.get("declared_pillars", len(PILLARS))
    print()
    print(f"  Coverage: {len(measured)} of {declared} pillars measured.")
    if unmeasured:
        print(
            "    UNMEASURED, excluded from the score — this is NOT a clean bill "
            "of health:"
        )
        print("      " + ", ".join(p.replace("_", " ").title() for p in unmeasured))

    print()
    print("  Per pillar (evidence-backed | self-attested):")
    attested = result.get("self_attested_pillar_scores", {})
    for pillar, score in result.get("pillar_scores", {}).items():
        label = pillar.replace("_", " ").title()
        suffix = "" if score is not None else "   UNMEASURED"
        print(
            f"    {label:26s} {_bar(score)} {_pct(score):>6s} | "
            f"self-attested {_pct(attested.get(pillar)):>6s}{suffix}"
        )
    if result.get("recommendation"):
        print()
        print(f"  Recommendation: {result['recommendation']}")


def _render_pillar(result: dict) -> None:
    print(f"Pillar: {result['pillar'].replace('_', ' ').title()}")
    print(
        f"  Evidence-backed (proven)   {_pct(result.get('score')):>6s}  "
        f"[{(result.get('maturity_level') or UNMEASURED).upper()}]"
    )
    print(
        f"  Self-attested  (claimed)   {_pct(result.get('self_attested_score')):>6s}  "
        f"[{(result.get('self_attested_maturity') or UNMEASURED).upper()}]"
    )
    if result.get("evidence_backed") is not None:
        print(
            f"  Posture evidence rows: {result['evidence_backed']} evidence-backed, "
            f"{result.get('self_attested')} self-attested"
        )
    if not result.get("measured"):
        states = ", ".join(
            f"{c['type']}={c['state']}" for c in result.get("unmeasured_checks", [])
        )
        print(f"  UNMEASURED — nothing about this pillar could be measured ({states}).")
        print("  This is not a clean bill of health.")


def main():
    parser = argparse.ArgumentParser(description="ZTA 7-Pillar Maturity Scorer")
    parser.add_argument("--project-id", required=True, help="Project identifier")
    parser.add_argument("--pillar", choices=PILLARS, help="Score a specific pillar")
    parser.add_argument("--all", action="store_true", help="Score all 7 pillars + aggregate")
    parser.add_argument("--trend", action="store_true", help="Show maturity trend")
    parser.add_argument("--schedule", action="store_true", help="Run scheduled assessment with drift detection (G-18)")
    parser.add_argument("--drift-threshold", type=float, default=0.1, help="Drift alert threshold (default 0.1)")
    parser.add_argument("--days", type=int, default=90, help="Trend window in days")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--human", action="store_true", help="Human-readable output")
    args = parser.parse_args()

    if args.pillar:
        result = score_pillar(args.project_id, args.pillar)
    elif args.all:
        result = score_all_pillars(args.project_id)
    elif args.trend:
        result = get_trend(args.project_id, args.days)
    elif args.schedule:
        result = run_scheduled_assessment(args.project_id, drift_threshold=args.drift_threshold)
    else:
        result = score_all_pillars(args.project_id)

    if args.json or not args.human:
        print(json.dumps(result, indent=2))
    else:
        if "error" in result:
            print(f"ERROR: {result['error']}")
        elif "overall_score" in result:
            _render_overall(result)
        elif "pillar" in result:
            _render_pillar(result)


if __name__ == "__main__":
    main()
