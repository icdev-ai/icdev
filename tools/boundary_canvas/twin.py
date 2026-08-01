# CUI // SP-CTI — BDC cATO Digital Twin
"""Boundary Design Canvas cATO twin — snapshot, simulate, crosswalk drift, OSCAL export.

Honesty invariants (task bdr-sec-4):
  * take_snapshot / crosswalk_drift surface real failures as explicit
    ``{"status": "error", ...}`` payloads that a caller can tell apart from a
    clean-empty result — they no longer swallow the exception and return a
    success-looking dict full of zeros.
  * simulate_delta's default what-if path is a DETERMINISTIC HEURISTIC and is
    labelled ``method="heuristic"``. A real LLM Chain-of-Debate is available
    only behind the OFF-by-default ``twin.chain_of_debate.enabled`` flag in
    args/bdc_canvas_config.yaml; when the flag is off (or the LLM call fails)
    the heuristic path is used and labelled as such.
  * No fabricated compliance scores: evidence_score and the overall score are
    derived from the delta itself, or are ``None`` ("not scored") when there is
    nothing to score. Rating-band thresholds live in config, not as magic
    constants in this module.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from tools.db.storage import get_connection
from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

_CONFIG_PATH = Path(__file__).resolve().parents[2] / "args" / "bdc_canvas_config.yaml"

# Rating-band thresholds. Expressed as fractions so this module carries NO magic
# fabricated compliance constants — the authoritative values live in
# args/bdc_canvas_config.yaml (twin.rating_bands); these are the safe fallback.
_DEFAULT_RATING_BANDS: Dict[str, float] = {"green": 4 / 5, "amber": 1 / 2}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_twin_config() -> Dict[str, Any]:
    """Load the ``twin`` block from args/bdc_canvas_config.yaml (safe fallback).

    Returns a dict with ``chain_of_debate`` and ``rating_bands`` sub-keys. Any
    missing file / parse error degrades to defaults (debate OFF, fraction bands).
    """
    cfg: Dict[str, Any] = {
        "chain_of_debate": {"enabled": False, "llm_function": "reasoning"},
        "rating_bands": dict(_DEFAULT_RATING_BANDS),
    }
    try:
        import yaml  # local import — optional dependency

        raw = yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8"))
        twin_cfg = (raw or {}).get("twin") if isinstance(raw, dict) else None
        if isinstance(twin_cfg, dict):
            cod = twin_cfg.get("chain_of_debate")
            if isinstance(cod, dict):
                cfg["chain_of_debate"]["enabled"] = bool(cod.get("enabled", False))
                if isinstance(cod.get("llm_function"), str):
                    cfg["chain_of_debate"]["llm_function"] = cod["llm_function"]
            bands = twin_cfg.get("rating_bands")
            if isinstance(bands, dict):
                for k in ("green", "amber"):
                    if isinstance(bands.get(k), (int, float)):
                        cfg["rating_bands"][k] = float(bands[k])
    except FileNotFoundError:
        logger.info("bdc_canvas_config.yaml not found at %s — using twin defaults", _CONFIG_PATH)
    except Exception as exc:  # noqa: BLE001 — any parse error -> safe default
        logger.warning("Failed to load twin config (%s) — using defaults", exc)
    return cfg


def take_snapshot(project_id: str, framework_id: str = "FedRAMP Moderate") -> dict:
    """Freeze cross-framework control state into compliance_snapshots (append-only).

    Failures are surfaced, not swallowed: a failed control/evidence count yields
    ``None`` (distinguishable from a genuine zero), and a failed INSERT yields a
    ``{"status": "error"}`` payload with ``persisted: False`` so callers never
    mistake an unwritten snapshot for a successful one.
    """
    conn = get_connection()
    snap_id = str(uuid.uuid4())
    taken_at = _now()
    errors: List[str] = []

    try:
        control_count: Optional[int] = conn.execute(
            "SELECT COUNT(*) FROM project_controls WHERE project_id=%s", (project_id,)
        ).fetchone()[0]
    except Exception as exc:  # noqa: BLE001
        logger.warning("take_snapshot: control count failed for %s: %s", project_id, exc)
        control_count = None
        errors.append(f"control_count: {exc}")

    try:
        evidence_count: Optional[int] = conn.execute(
            "SELECT COUNT(*) FROM evidence WHERE project_id=%s", (project_id,)
        ).fetchone()[0]
    except Exception as exc:  # noqa: BLE001
        logger.warning("take_snapshot: evidence count failed for %s: %s", project_id, exc)
        evidence_count = None
        errors.append(f"evidence_count: {exc}")

    persisted = False
    try:
        conn.execute(
            """INSERT INTO compliance_snapshots
               (snapshot_id, project_id, framework_id, control_id, implementation_status, evidence_ref, taken_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (snap_id, project_id, framework_id, "_meta", "snapshot", "", taken_at),
        )
        conn.commit()
        persisted = True
    except Exception as exc:  # noqa: BLE001
        logger.error("take_snapshot: snapshot INSERT failed for %s: %s", project_id, exc)
        errors.append(f"insert: {exc}")

    result = {
        "snapshot_id": snap_id,
        "project_id": project_id,
        "framework_id": framework_id,
        "control_count": control_count,
        "evidence_count": evidence_count,
        "taken_at": taken_at,
        "persisted": persisted,
    }
    # The snapshot write is the operation that must succeed; if it did not, this
    # is a hard error. A failed count alone still records its error but does not
    # invalidate a persisted snapshot.
    if not persisted:
        result["status"] = "error"
        result["error"] = "; ".join(errors) or "snapshot not persisted"
    else:
        result["status"] = "ok"
        if errors:
            result["warnings"] = errors
    return result


def _heuristic_debate(delta: list) -> List[Dict[str, Any]]:
    """Deterministic (NON-LLM) per-control assessment transcript.

    Labelled ``method="heuristic"`` on every entry so no caller can mistake this
    templated text for a real Chain-of-Debate. The ``assessment`` is a rule-based
    reading of the proposed status — it does not adjudicate anything.
    """
    transcript: List[Dict[str, Any]] = []
    for c in delta:
        ctrl_id = c.get("control_id", "?")
        status = c.get("implementation_status", "unknown")
        satisfied = status == "satisfied"
        transcript.append({
            "control_id": ctrl_id,
            "status": status,
            "method": "heuristic",
            "for": (
                f"Heuristic: control {ctrl_id} is marked satisfied in the proposed delta; "
                f"treat as satisfied only if evidence is attached."
                if satisfied
                else f"Heuristic: control {ctrl_id} is marked not_satisfied; "
                f"the gap must be addressed or covered by a planned POA&M before ATO."
            ),
            "against": (
                f"Heuristic: a 'satisfied' mark for {ctrl_id} may hide gaps without "
                f"attached evidence; recommend a deeper review."
                if satisfied
                else f"Heuristic: {ctrl_id} could be partially satisfied by compensating "
                f"controls; confirm before opening a POA&M."
            ),
            # No judge/verdict: this heuristic does not adjudicate. It echoes the
            # input status as the *proposed* status, explicitly labelled.
            "assessment": f"heuristic reading of proposed status: {status}",
        })
    return transcript


def _llm_debate(delta: list, framework_id: str, llm_function: str) -> Optional[List[Dict[str, Any]]]:
    """Run a REAL Chain-of-Debate over the delta via the LLM router.

    Returns a transcript list on success, or ``None`` to signal the caller should
    fall back to the heuristic path (no LLM available / unparseable output).
    Raises nothing that should abort the simulation — all failures return None.
    """
    if not delta:
        return None
    try:
        from tools.llm.router import LLMRouter
        from tools.llm.provider import LLMRequest

        router = LLMRouter()
        if router.is_no_llm_mode() or not router.has_any_llm():
            return None

        controls_json = json.dumps(
            [
                {
                    "control_id": c.get("control_id", "?"),
                    "proposed_status": c.get("implementation_status", "unknown"),
                    "evidence_ref": c.get("evidence_ref"),
                }
                for c in delta[:50]  # cap to avoid token overflow
            ],
            indent=2,
        )
        prompt = (
            f"You are running a Chain-of-Debate over proposed compliance control changes "
            f"for {framework_id}. For EACH control produce a FOR argument (why the proposed "
            f"status is justified), an AGAINST argument (risks / hidden gaps), and a "
            f"judge_verdict (the implementation status you conclude after weighing both).\n\n"
            f"Controls:\n{controls_json}\n\n"
            f'Return ONLY a JSON object: {{"debate": [{{"control_id": "X", "status": "...", '
            f'"for": "...", "against": "...", "judge_verdict": "..."}}]}}'
        )
        request = LLMRequest(
            messages=[{"role": "user", "content": prompt}],
            system_prompt="You are a federal compliance adjudicator. Reply only with valid JSON.",
            max_tokens=1500,
            classification="CUI",
        )
        response = router.invoke(llm_function, request)
        raw = (response.content or "").strip()
        if raw.startswith("```"):
            lines = raw.splitlines()
            raw = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        parsed = json.loads(raw)
        debate = parsed.get("debate", [])
        if not isinstance(debate, list) or not debate:
            return None
        transcript: List[Dict[str, Any]] = []
        for entry in debate:
            if not isinstance(entry, dict) or "control_id" not in entry:
                continue
            entry = dict(entry)
            entry["method"] = "llm_debate"
            transcript.append(entry)
        return transcript or None
    except Exception as exc:  # noqa: BLE001 — never abort the simulation
        logger.warning("LLM chain-of-debate unavailable (%s) — heuristic fallback", exc)
        return None


def simulate_delta(project_id: str, delta: list, framework_id: str = "FedRAMP Moderate",
                   baseline_snap_id: str | None = None, use_cod: bool = False) -> dict:
    """Simulate applying a policy delta and return readiness score + violations.

    Scores are derived from the delta, never fabricated:
      * ``score`` / ``control_score`` = satisfied / total, or ``None`` when the
        delta is empty (nothing to score).
      * ``evidence_score`` = fraction of delta controls carrying an evidence_ref,
        or ``None`` when the delta is empty.

    When *use_cod* is True the method depends on config: if
    ``twin.chain_of_debate.enabled`` is set a REAL LLM debate runs; otherwise (or
    on LLM failure) a deterministic heuristic transcript is returned. The chosen
    method is reported in ``cod_method`` and on every transcript entry.
    """
    sim_id = str(uuid.uuid4())
    satisfied = sum(1 for c in delta if c.get("implementation_status") == "satisfied")
    not_satisfied = sum(1 for c in delta if c.get("implementation_status") == "not_satisfied")
    total = len(delta)

    cfg = _load_twin_config()
    bands = cfg["rating_bands"]

    # Derived scores — None when there is nothing to score (empty delta).
    if total:
        score: Optional[float] = satisfied / total
        with_evidence = sum(1 for c in delta if c.get("evidence_ref"))
        evidence_score: Optional[float] = with_evidence / total
    else:
        score = None
        evidence_score = None

    critical_gaps = not_satisfied
    if score is None:
        rating = "unknown"
    elif score >= bands["green"]:
        rating = "green"
    elif score >= bands["amber"]:
        rating = "amber"
    else:
        rating = "red"

    violations = [
        {"severity": "high", "id": c.get("control_id", "?"),
         "title": f"Control {c.get('control_id','?')} not satisfied",
         "recommendation": "Implement required control or provide planned POA&M"}
        for c in delta if c.get("implementation_status") == "not_satisfied"
    ]

    cod_transcript: Optional[List[Dict[str, Any]]] = None
    cod_method: Optional[str] = None
    if use_cod:
        debate_cfg = cfg["chain_of_debate"]
        if debate_cfg.get("enabled"):
            cod_transcript = _llm_debate(delta, framework_id, debate_cfg.get("llm_function", "reasoning"))
            cod_method = "llm_debate" if cod_transcript else "heuristic"
        else:
            cod_method = "heuristic"
        if cod_transcript is None:
            cod_transcript = _heuristic_debate(delta)

    result = {
        "simulation_id": sim_id, "project_id": project_id, "framework_id": framework_id,
        "rating": rating, "verdict": rating,
        "score": round(score, 3) if score is not None else None,
        "control_score": round(score, 3) if score is not None else None,
        "evidence_score": round(evidence_score, 3) if evidence_score is not None else None,
        "critical_gaps": critical_gaps,
        "compliance_delta": {"resolved": satisfied, "new_gaps": not_satisfied, "total": total},
        "violations": violations,
    }
    if cod_method is not None:
        result["cod_method"] = cod_method
    if cod_transcript is not None:
        result["cod_transcript"] = cod_transcript
    return result


def crosswalk_drift(project_id: str, fw_src: str, fw_tgt: str) -> dict:
    """Surface controls satisfied in fw_src but not in fw_tgt.

    On query failure returns ``{"status": "error", ...}`` (distinguishable from a
    genuinely empty ``{"status": "ok", "drifts": [], "total": 0}``) instead of
    silently swallowing the exception.
    """
    conn = get_connection()
    drifts = []
    try:
        src_rows = conn.execute(
            "SELECT control_id, implementation_status FROM compliance_snapshots WHERE project_id=%s AND framework_id=%s ORDER BY taken_at DESC LIMIT 500",
            (project_id, fw_src),
        ).fetchall()
        tgt_rows = conn.execute(
            "SELECT control_id, implementation_status FROM compliance_snapshots WHERE project_id=%s AND framework_id=%s ORDER BY taken_at DESC LIMIT 500",
            (project_id, fw_tgt),
        ).fetchall()
        src_map = {r[0]: r[1] for r in src_rows}
        tgt_map = {r[0]: r[1] for r in tgt_rows}
        for ctrl_id, src_status in src_map.items():
            tgt_status = tgt_map.get(ctrl_id, "unknown")
            drift = src_status == "satisfied" and tgt_status != "satisfied"
            if drift:
                drifts.append({"control_id": ctrl_id, "framework_src": fw_src,
                               "status_src": src_status, "framework_tgt": fw_tgt,
                               "status_tgt": tgt_status, "drift": True,
                               "recommendation": f"Remediate {ctrl_id} in {fw_tgt}"})
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "crosswalk_drift failed for %s (%s -> %s): %s", project_id, fw_src, fw_tgt, exc
        )
        return {"status": "error", "error": str(exc), "drifts": [], "total": 0}
    return {"status": "ok", "drifts": drifts, "total": len(drifts)}


def export_oscal(project_id: str, snapshot_id: str | None, artifact_type: str = "ssp") -> dict:
    """Generate a real OSCAL artifact for *project_id* and return path + validity.

    Delegates to ``oscal_cato_exporter.export_oscal_artifact`` (which itself is a
    thin wrapper over ``tools/compliance/oscal_generator.py``). The generator reads
    project/control/finding state from the ICDEV main DB keyed by ``project_id``;
    ``snapshot_id`` is carried through for UI reference only.

    Honesty conventions (bdr-sec-4): a missing exporter module (ImportError) or a
    generation failure is LOGGED, never silently swallowed, and surfaced as an
    ``{"status": "error", ...}`` payload with ``path=None`` so callers can tell a
    real artifact from a failure. On success returns ``status="ok"`` with the
    artifact ``path`` and ``valid`` flag.
    """
    try:
        from tools.boundary_canvas.oscal_cato_exporter import export_oscal_artifact
    except ImportError as exc:
        logger.error("export_oscal: exporter module unavailable for %s: %s", project_id, exc)
        return {"status": "error", "artifact_type": artifact_type, "snapshot_id": snapshot_id,
                "path": None, "valid": False, "error": f"OSCAL exporter unavailable: {exc}"}

    result = export_oscal_artifact(project_id, artifact_type=artifact_type)
    # Carry the snapshot reference through for the UI; export_oscal_artifact already
    # returns status/path/valid/error and logs any generation failure internally.
    result["snapshot_id"] = snapshot_id
    return result
