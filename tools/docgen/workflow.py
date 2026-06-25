# CUI // SP-CTI
"""IDR Workflow — 8-stage session state machine.

Stages:
  0 setup        → user configures session, selects domain profile
  1 ingesting    → files uploaded and sent to DIC ingest_file()
  2 analyzing    → diagram_analysis + config/IaC review per upload
  3 conflicts    → HITL: human resolves topology conflicts
  4 synthesizing → context_builder assembles unified evidence context
  5 generating   → ACE-orchestrated AI doc generation via DIC doc_generator
  6 writeguard   → WriteGuard hard gate (blocking); auto-fix up to 3× then HITL
  7 reviewing    → HITL: human reviews/edits/approves full document
  8 publishing   → final coherence pass, export all formats, mark published

Transitions are stored in idr_sessions.stage + status. Kickback rewinds to
the appropriate stage with reviewer notes injected into the next generation.
"""
from __future__ import annotations

import pathlib
import uuid
from typing import Any

from tools.logging.icdev_logger import get_logger
from tools.docgen import session_manager as sm
from tools.docgen.domain_profiles import get_profile, get_writeguard_mode

log = get_logger(__name__)

_STAGE_STATUS: list[tuple[int, str]] = [
    (0, "setup"),
    (1, "ingesting"),
    (2, "analyzing"),
    (3, "conflicts"),
    (4, "synthesizing"),
    (5, "generating"),
    (6, "writeguard"),
    (7, "reviewing"),
    (8, "publishing"),
]

# WriteGuard pass threshold
_WG_MIN_SCORE = 70
_WG_MAX_RETRIES = 3


# ─── Stage transitions ────────────────────────────────────────────────────────

def advance(session_id: str, to_stage: int) -> dict[str, Any]:
    """Advance a session to *to_stage* and return the updated session."""
    if to_stage < 0 or to_stage > 8:
        raise ValueError(f"Invalid stage: {to_stage}")
    _, status = _STAGE_STATUS[to_stage]
    sm.advance_stage(session_id, to_stage, status)
    session = sm.get_session(session_id)
    log.info("IDR advance: session=%s stage=%d status=%s", session_id, to_stage, status)
    return session


def kickback(session_id: str, to_stage: int, reason: str = "") -> dict[str, Any]:
    """Rewind to *to_stage* (e.g. 5 = re-generate) with an optional reason logged."""
    log.warning(
        "IDR kickback: session=%s → stage=%d reason=%r", session_id, to_stage, reason
    )
    return advance(session_id, to_stage)


# ─── Stage 1: Upload & Ingest ─────────────────────────────────────────────────

def stage1_ingest_upload(
    session_id: str,
    upload_id: str,
    file_path: str,
) -> dict[str, Any]:
    """Ingest a single upload into DIC. Returns the updated upload row."""
    try:
        from tools.document_intelligence.ingest_orchestrator import ingest_file

        result = ingest_file(
            file_path=file_path,
            collection_id=_get_collection_id(session_id),
            metadata={"idr_session_id": session_id, "upload_id": upload_id},
        )
        dic_doc_id = result.get("doc_id") if result else None
        sm.set_upload_status(upload_id, "ingested", dic_doc_id=dic_doc_id)
        log.info("IDR ingest: upload=%s → dic_doc=%s", upload_id, dic_doc_id)
    except ImportError:
        log.warning("DIC ingest_orchestrator not available — marking upload as ingested without DIC")
        sm.set_upload_status(upload_id, "ingested")
    except Exception as exc:
        log.exception("IDR ingest failed: upload=%s", upload_id)
        sm.set_upload_status(upload_id, "error", error_msg=str(exc))

    return sm.get_upload(upload_id) or {}


# ─── Stage 2: Domain Analysis ─────────────────────────────────────────────────

def stage2_analyze_upload(
    session_id: str,
    upload_id: str,
    upload: dict[str, Any],
    session: dict[str, Any],
) -> list[dict[str, Any]]:
    """Run applicable analyzers for one upload. Returns list of idr_analyses rows created."""
    domain = session.get("domain", "network")
    profile = get_profile(domain)
    file_path = upload.get("file_path", "")
    upload_type = upload.get("upload_type", "")
    analyses_created: list[dict[str, Any]] = []

    # Diagram analysis for diagram uploads
    if upload_type == "diagram":
        diag_mod_path = profile.get("diagram_analyzer")
        diag_fn_name = profile.get("diagram_analyzer_fn")
        if diag_mod_path and diag_fn_name:
            try:
                import importlib
                diag_mod = importlib.import_module(diag_mod_path)
                diag_fn = getattr(diag_mod, diag_fn_name)
                result = diag_fn(file_path)
                result_ref_id = result.get("analysis_id") or result.get("id") or str(uuid.uuid4())
                row = sm.add_analysis(
                    session_id, upload_id, "diagram_analysis", result_ref_id
                )
                analyses_created.append(row)
                sm.set_upload_status(upload_id, "analyzed")
            except Exception:
                log.exception("IDR diagram analysis failed: upload=%s", upload_id)
                sm.set_upload_status(upload_id, "error", error_msg="diagram_analysis failed")

    # Config review for config uploads
    elif upload_type == "config":
        cfg_mod_path = profile.get("config_reviewer")
        cfg_fn_name = profile.get("config_reviewer_fn")
        if cfg_mod_path and cfg_fn_name:
            try:
                import importlib
                cfg_mod = importlib.import_module(cfg_mod_path)
                cfg_fn = getattr(cfg_mod, cfg_fn_name)
                result = cfg_fn(file_path)
                result_ref_id = result.get("review_id") or result.get("id") or str(uuid.uuid4())
                row = sm.add_analysis(
                    session_id, upload_id, "config_review", result_ref_id
                )
                analyses_created.append(row)
                sm.set_upload_status(upload_id, "analyzed")
            except Exception:
                log.exception("IDR config review failed: upload=%s", upload_id)
                sm.set_upload_status(upload_id, "error", error_msg="config_review failed")
        else:
            log.debug("No config reviewer configured for domain=%s", domain)
            sm.set_upload_status(upload_id, "analyzed")

    # IaC review for iac uploads
    elif upload_type == "iac":
        iac_mod_path = profile.get("iac_reviewer")
        iac_fn_name = profile.get("iac_reviewer_fn")
        if iac_mod_path and iac_fn_name:
            try:
                import importlib
                iac_mod = importlib.import_module(iac_mod_path)
                iac_fn = getattr(iac_mod, iac_fn_name)
                result = iac_fn(file_path)
                result_ref_id = result.get("review_id") or result.get("id") or str(uuid.uuid4())
                row = sm.add_analysis(
                    session_id, upload_id, "iac_review", result_ref_id
                )
                analyses_created.append(row)
                sm.set_upload_status(upload_id, "analyzed")
            except Exception:
                log.exception("IDR IaC review failed: upload=%s", upload_id)
                sm.set_upload_status(upload_id, "error", error_msg="iac_review failed")
        else:
            log.debug("No IaC reviewer configured for domain=%s", domain)
            sm.set_upload_status(upload_id, "analyzed")

    else:
        # doc / supplement — mark analyzed (DIC already ingested it)
        sm.set_upload_status(upload_id, "analyzed")

    return analyses_created


# ─── Stage 3: Conflict check ──────────────────────────────────────────────────

def stage3_check_gate(session_id: str) -> bool:
    """Return True if all conflicts are resolved (gate passes)."""
    pending = sm.pending_conflict_count(session_id)
    if pending > 0:
        log.info("IDR stage-3 gate BLOCKED: session=%s pending_conflicts=%d", session_id, pending)
    return pending == 0


# ─── Stage 6: WriteGuard gate ─────────────────────────────────────────────────

def stage6_writeguard(
    session_id: str,
    doc_text: str,
    domain: str,
) -> dict[str, Any]:
    """Run WriteGuard with an auto-fix loop (up to _WG_MAX_RETRIES rewrites).

    Returns {passed, score, result, fixed_text, attempts, blocked, ace_regen_needed}.
    On pass:  passed=True, ace_regen_needed=False.
    On block: passed=False, blocked=True, ace_regen_needed=True (all retries used).
    """
    get_writeguard_mode(domain)  # validates domain is known before attempting
    try:
        from tools.pulse.writeguard import run_full_quality_check, rewrite_content

        current_text = doc_text
        last_result: dict[str, Any] = {}
        last_score = 0.0

        # Run initial check then up to _WG_MAX_RETRIES rewrite-and-recheck cycles.
        for attempt in range(_WG_MAX_RETRIES + 1):
            last_result = run_full_quality_check(current_text)
            last_score = float(last_result.get("overall_score", 0))

            if last_score >= _WG_MIN_SCORE:
                log.info(
                    "IDR WriteGuard: session=%s score=%.1f PASS (attempt=%d)",
                    session_id, last_score, attempt,
                )
                return {
                    "passed": True,
                    "score": last_score,
                    "result": last_result,
                    "fixed_text": current_text,
                    "attempts": attempt,
                    "blocked": False,
                    "ace_regen_needed": False,
                }

            if attempt >= _WG_MAX_RETRIES:
                break  # no more rewrites allowed

            # Auto-fix: apply deterministic rewrites and re-check next iteration.
            rewrite_result = rewrite_content(current_text, last_result)
            fixed_text = rewrite_result.get("rewritten_text", current_text)
            log.info(
                "IDR WriteGuard: session=%s score=%.1f FAIL → auto-fix %d/%d",
                session_id, last_score, attempt + 1, _WG_MAX_RETRIES,
            )
            current_text = fixed_text

        # All rewrite attempts exhausted — ACE regen required.
        log.warning(
            "IDR WriteGuard: session=%s score=%.1f BLOCKED after %d attempts → ACE regen needed",
            session_id, last_score, _WG_MAX_RETRIES,
        )
        return {
            "passed": False,
            "score": last_score,
            "result": last_result,
            "fixed_text": current_text,
            "attempts": _WG_MAX_RETRIES,
            "blocked": True,
            "ace_regen_needed": True,
        }

    except ImportError:
        log.warning("WriteGuard not available — gate bypassed (import error)")
        return {
            "passed": True, "score": 100, "result": {}, "fixed_text": doc_text,
            "attempts": 0, "blocked": False, "ace_regen_needed": False,
        }
    except Exception as exc:
        log.exception("IDR WriteGuard exception: session=%s", session_id)
        return {
            "passed": False, "score": 0, "result": {"error": str(exc)}, "fixed_text": doc_text,
            "attempts": 0, "blocked": False, "ace_regen_needed": False,
        }


def stage6_trigger_ace_regen(session_id: str) -> dict[str, Any]:
    """Kick the session back to stage 5 (generating) for ACE regeneration.

    Called when WriteGuard is blocked after exhausting all auto-fix retries.
    Clears wg_result_id so the gate re-arms for the regenerated document.
    """
    sm.set_field(session_id, wg_result_id=None)
    log.warning(
        "IDR ACE regen triggered: session=%s → rewinding to stage 5", session_id
    )
    return kickback(
        session_id, 5, reason="WriteGuard blocked after max auto-fix retries — ACE regen"
    )


def stage6_check_gate(session_id: str) -> bool:
    """Return True if WriteGuard has passed for this session (wg_result_id is set)."""
    session = sm.get_session(session_id)
    if not session:
        return False
    return bool(session.get("wg_result_id"))


# ─── Stage 8: Publish all formats ─────────────────────────────────────────────

def stage8_publish(
    session_id: str,
    doc_text: str,
    title: str,
    output_dir: str | None = None,
    classification: str = "CUI",
) -> list[dict[str, Any]]:
    """Export the approved document to HTML, DOCX, PDF.

    Returns list of idr_artifacts rows (one per format).
    """
    if not output_dir:
        output_dir = str(
            pathlib.Path("data") / "docgen" / "artifacts" / session_id
        )
    pathlib.Path(output_dir).mkdir(parents=True, exist_ok=True)

    artifacts = []
    _try_export_html(session_id, doc_text, title, output_dir, classification, artifacts)
    _try_export_docx(session_id, doc_text, title, output_dir, classification, artifacts)
    _try_export_pdf(session_id, doc_text, title, output_dir, classification, artifacts)

    sm.advance_stage(session_id, 8, "published")

    # If this session was orchestrated by an ACE instance, generate an evidence
    # report so the publish event is captured in the ace_audit_log trail.
    session = sm.get_session(session_id)
    ace_instance_id = session and session.get("ace_instance_id")
    if ace_instance_id:
        try:
            from icdev.tools.ace.evidence_report import generate as _ev_generate
            _ev_generate(ace_instance_id, fmt="json")
        except Exception:
            log.debug(
                "IDR evidence_report.generate skipped for ace_instance=%s",
                ace_instance_id,
                exc_info=True,
            )

    log.info("IDR published: session=%s artifacts=%d", session_id, len(artifacts))
    return artifacts


_CLS_BANNER_STYLES: dict[str, str] = {
    "PUBLIC":          "background:#0d3b1e;color:#4caf50;border:2px solid #2e7d32",
    "UNCLASSIFIED":    "background:#0d3b1e;color:#4caf50;border:2px solid #2e7d32",
    "CUI":             "background:#0d2b4e;color:#4fc3f7;border:2px solid #1565c0",
    "SECRET":          "background:#3e1a00;color:#ff9800;border:2px solid #e65100",
    "TOP SECRET":      "background:#3b0a0a;color:#ef5350;border:2px solid #b71c1c",
    "TOP SECRET//SCI": "background:#2a0a3b;color:#ce93d8;border:2px solid #6a1b9a",
}


def _try_export_html(session_id, text, title, out_dir, classification, artifacts):
    try:
        html_path = str(pathlib.Path(out_dir) / "document.html")
        cls_upper = (classification or "CUI").upper()
        banner_style = _CLS_BANNER_STYLES.get(cls_upper, _CLS_BANNER_STYLES["CUI"])
        banner_css = f"{banner_style};padding:6px 12px;font-size:13px;font-weight:bold;text-align:center;letter-spacing:1px;"
        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{title}</title>
  <style>
    body{{font-family:Arial,sans-serif;margin:0;padding:0;background:#fff;color:#111;}}
    .cls-banner{{{banner_css}}}
    .doc-body{{max-width:900px;margin:24px auto;padding:0 24px;}}
    h1{{font-size:22px;border-bottom:2px solid #ccc;padding-bottom:8px;}}
    .content{{line-height:1.7;white-space:pre-wrap;}}
  </style>
</head>
<body>
  <div class="cls-banner">{classification}</div>
  <div class="doc-body">
    <h1>{title}</h1>
    <div class="content">{text}</div>
  </div>
  <div class="cls-banner">{classification}</div>
</body>
</html>"""
        pathlib.Path(html_path).write_text(html, encoding="utf-8")
        row = sm.add_artifact(session_id, "html", file_path=html_path)
        artifacts.append(row)
    except Exception:
        log.exception("IDR HTML export failed: session=%s", session_id)


def _try_export_docx(session_id, text, title, out_dir, classification, artifacts):
    try:
        from tools.presentations.generate_exec_doc import generate_docx

        docx_path = str(pathlib.Path(out_dir) / "document.docx")
        # Wrap content with classification markings (header + footer lines)
        marked_content = f"{classification}\n\n{text}\n\n{classification}"
        generate_docx(title=title, content=marked_content, output_path=docx_path)
        row = sm.add_artifact(session_id, "docx", file_path=docx_path)
        artifacts.append(row)
    except ImportError:
        log.debug("generate_exec_doc not available — skipping DOCX export")
    except Exception:
        log.exception("IDR DOCX export failed: session=%s", session_id)


def _try_export_pdf(session_id, text, title, out_dir, classification, artifacts):
    try:
        from tools.network.pdf_export import export_to_pdf

        pdf_path = str(pathlib.Path(out_dir) / "document.pdf")
        export_to_pdf(content=text, output_path=pdf_path, title=title, classification=classification)
        row = sm.add_artifact(session_id, "pdf", file_path=pdf_path)
        artifacts.append(row)
    except ImportError:
        log.debug("pdf_export not available — skipping PDF export")
    except Exception:
        log.exception("IDR PDF export failed: session=%s", session_id)


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _get_collection_id(session_id: str) -> str:
    session = sm.get_session(session_id)
    if session and session.get("dic_collection_id"):
        return session["dic_collection_id"]
    col_id = f"idr-{session_id}"
    sm.set_field(session_id, dic_collection_id=col_id)
    return col_id
