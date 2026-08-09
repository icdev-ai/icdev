# CUI // SP-CTI
"""ACE SkillPromoter — SIPA-gated genesis reflex for autonomous skill promotion.

Runs daily as a genesis reflex.  For each pending ``ace_skill_candidates`` row:

  1. Write the candidate YAML to a temp file.
  2. Run SIPA ``assess()`` in provenance-blind mode.
  3. SIPA verdict "clean" or "low_risk" → write to ``args/ace/roles/candidates/``
     and mark status = 'promoted' (human still reviews before live).
  4. Any other verdict → mark status = 'rejected' with SIPA details attached.
  5. Promote event logged to ``component_audit_log`` (append-only).

Guard rails enforced here:
  - Candidates go to ``roles/candidates/``, NOT the live ``roles/`` directory.
    A human must mv the file to live (or use the Kanban UI).
  - ``ace_skill_candidates`` is append-only — status is updated in-place but
    the row is never deleted (NIST AU immutability).
  - Promotion limit: max 5 candidates per reflex run to avoid flooding.

Usage (genesis daemon)::

    from icdev.tools.ace.skill_promoter import run
    result = run(config={}, trust=None)
"""
from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from icdev.tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

_DB_ENV = "ICDEV_ACE_DB_URL"
_ROLES_CANDIDATES_DIR = Path(__file__).parents[3] / "args" / "ace" / "roles" / "candidates"
_MAX_PER_RUN = 5

# SIPA verdicts that are acceptable for promotion to candidates/
_PROMOTABLE_VERDICTS = {"clean", "low_risk"}

#: Sentinel meaning "the scanner did not run", distinct from any verdict SIPA
#: itself returns. Never promotable; handled as skipped so it retries.
_VERDICT_UNAVAILABLE = "unavailable"


# ---------------------------------------------------------------------------
# Genesis reflex entry point
# ---------------------------------------------------------------------------


def run(config: dict[str, Any], trust: Any) -> dict[str, Any]:
    """Execute one reflex pass: assess pending skill candidates.

    Returns:
        Dict with keys: assessed, promoted, rejected, skipped, errors.
    """
    summary: dict[str, Any] = {
        "assessed": 0,
        "promoted": 0,
        "rejected": 0,
        "skipped": 0,
        "errors": [],
    }

    pending = _load_pending(limit=_MAX_PER_RUN)
    if not pending:
        return summary

    for row in pending:
        candidate_id = row["id"]
        role_id = row["role_id"]
        candidate_yaml = row["candidate_yaml"]

        try:
            verdict, score = _run_sipa(candidate_yaml, role_id)
            summary["assessed"] += 1

            if verdict == _VERDICT_UNAVAILABLE:
                # The scanner did not run, so nothing has been vetted. Skip
                # rather than reject: 'rejected' is terminal, but a scanner
                # outage should be retried once SIPA is back.
                _mark_skipped(candidate_id, "SIPA unavailable — not assessed")
                summary["skipped"] += 1
            elif verdict in _PROMOTABLE_VERDICTS:
                _promote(candidate_id, role_id, candidate_yaml, verdict, score)
                summary["promoted"] += 1
            else:
                _reject(candidate_id, f"SIPA verdict={verdict} score={score}")
                summary["rejected"] += 1

        except Exception as exc:
            summary["errors"].append({"candidate_id": candidate_id, "error": str(exc)})
            _mark_skipped(candidate_id, f"error: {exc}")
            summary["skipped"] += 1

    return summary


# ---------------------------------------------------------------------------
# SIPA gate
# ---------------------------------------------------------------------------


def _run_sipa(candidate_yaml: str, role_id: str) -> tuple[str, float]:
    """Write candidate YAML to a temp file and run SIPA Mode B assessment.

    Returns (verdict, risk_score).  Falls back to ("clean", 0.0) if SIPA is
    not available (imports fail or DB not initialized) — degradable-first.
    """
    try:
        from tools.integrity.engine import assess
    except ImportError:
        # FAIL CLOSED. This gate stands between an LLM-authored role spec and a
        # promoted, staffable role. Returning "clean" when the scanner is simply
        # absent means an unvetted candidate is promoted on the strength of the
        # scanner not being installed — the opposite of what the gate is for.
        # "unavailable" is handled by the caller as skipped, not promoted.
        logger.warning("skill_promoter: SIPA unavailable (import failed) — refusing to promote")
        return "unavailable", 0.0

    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=f"_{role_id}.yaml",
        delete=False,
        encoding="utf-8",
    ) as fh:
        fh.write(candidate_yaml)
        tmp_path = fh.name

    try:
        result = assess(
            source=tmp_path,
            mode="provenance_blind",
            declared_purpose=f"ACE skill candidate: {role_id}",
        )
        verdict = result.get("verdict", "unknown")
        score = float(result.get("risk_score", 0.0))
        return verdict, score
    except Exception as exc:  # noqa: BLE001
        # FAIL CLOSED, same reasoning as the ImportError branch above. A scanner
        # that errored has not vetted anything; treating that as "clean" made the
        # gate strongest exactly when it was working and absent when it was not.
        logger.warning("skill_promoter: SIPA assessment errored (%s) — refusing to promote", exc)
        return "unavailable", 0.0
    finally:
        try:
            Path(tmp_path).unlink(missing_ok=True)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# DB operations
# ---------------------------------------------------------------------------


def _load_pending(limit: int = _MAX_PER_RUN) -> list[dict[str, Any]]:
    """Return up to *limit* pending skill candidates."""
    try:
        from icdev.tools.db.storage import get_canvas_connection

        conn = get_canvas_connection(_DB_ENV)
        try:
            rows = conn.execute(
                "SELECT id, role_id, source_role, instance_id, candidate_yaml, trust_tier "
                "FROM ace_skill_candidates WHERE status = 'pending' "
                "ORDER BY created_at ASC LIMIT %s",
                (limit,),
            ).fetchall()
            return [dict(zip(("id", "role_id", "source_role", "instance_id", "candidate_yaml", "trust_tier"), r)) for r in rows]
        finally:
            conn.close()
    except Exception:
        return []


def _promote(
    candidate_id: str,
    role_id: str,
    candidate_yaml: str,
    verdict: str,
    score: float,
) -> None:
    """Write YAML to roles/candidates/ and update status = 'promoted'."""
    _ROLES_CANDIDATES_DIR.mkdir(parents=True, exist_ok=True)
    out_path = _ROLES_CANDIDATES_DIR / f"{role_id}.yaml"
    out_path.write_text(candidate_yaml, encoding="utf-8", newline="")

    _update_status(candidate_id, "promoted", sipa_verdict=verdict, sipa_score=score)

    # Best-effort: log to component_audit_log
    try:
        from tools.config.component_registry import log_component_audit

        log_component_audit(
            event_type="skill_promoted",
            actor="ace_skill_promoter",
            component_key=role_id,
            details={
                "candidate_id": candidate_id,
                "sipa_verdict": verdict,
                "sipa_score": score,
                "output_path": str(out_path),
                "note": "Pending human review in roles/candidates/",
            },
        )
    except Exception:
        pass


def _reject(candidate_id: str, reason: str) -> None:
    """Mark status = 'rejected' with reason."""
    _update_status(candidate_id, "rejected", rejection_reason=reason)


def _mark_skipped(candidate_id: str, reason: str) -> None:
    """Mark status = 'skipped' (error during assessment)."""
    _update_status(candidate_id, "skipped", rejection_reason=reason)


def _update_status(
    candidate_id: str,
    status: str,
    *,
    sipa_verdict: str | None = None,
    sipa_score: float | None = None,
    rejection_reason: str | None = None,
) -> None:
    try:
        from icdev.tools.db.storage import get_canvas_connection

        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        conn = get_canvas_connection(_DB_ENV)
        try:
            conn.execute(
                "UPDATE ace_skill_candidates SET status = %s, sipa_verdict = %s, "
                "sipa_score = %s, rejection_reason = %s, resolved_at = %s WHERE id = %s",
                (status, sipa_verdict, sipa_score, rejection_reason, now, candidate_id),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass
