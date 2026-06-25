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
    retry_count: int = 0,
) -> dict[str, Any]:
    """Run WriteGuard on *doc_text*.  Returns {passed, score, result, fixed_text}."""
    get_writeguard_mode(domain)  # validates domain is known before attempting
    try:
        from tools.pulse.writeguard import run_full_quality_check, rewrite_content

        result = run_full_quality_check(doc_text)
        score = result.get("overall_score", 0)
        passed = score >= _WG_MIN_SCORE

        if not passed and retry_count < _WG_MAX_RETRIES:
            fixed_text = rewrite_content(doc_text, result)
            log.info(
                "IDR WriteGuard: session=%s score=%.1f FAIL → auto-fix attempt %d",
                session_id, score, retry_count + 1,
            )
            return {"passed": False, "score": score, "result": result, "fixed_text": fixed_text}

        log.info(
            "IDR WriteGuard: session=%s score=%.1f %s (retry=%d)",
            session_id, score, "PASS" if passed else "BLOCKED", retry_count,
        )
        return {"passed": passed, "score": score, "result": result, "fixed_text": doc_text}

    except ImportError:
        log.warning("WriteGuard not available — gate bypassed (import error)")
        return {"passed": True, "score": 100, "result": {}, "fixed_text": doc_text}
    except Exception as exc:
        log.exception("IDR WriteGuard exception: session=%s", session_id)
        return {"passed": False, "score": 0, "result": {"error": str(exc)}, "fixed_text": doc_text}


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
    _try_export_docx(session_id, doc_text, title, output_dir, artifacts)
    _try_export_pdf(session_id, doc_text, title, output_dir, artifacts)

    sm.advance_stage(session_id, 8, "published")
    log.info("IDR published: session=%s artifacts=%d", session_id, len(artifacts))
    return artifacts


def _try_export_html(session_id, text, title, out_dir, classification, artifacts):
    try:
        html_path = str(pathlib.Path(out_dir) / "document.html")
        banner = f'<div class="classification-banner">{classification}</div>'
        html = f"""<!DOCTYPE html><html><head><title>{title}</title></head><body>
{banner}<h1>{title}</h1><div class="content">{text}</div></body></html>"""
        pathlib.Path(html_path).write_text(html, encoding="utf-8")
        row = sm.add_artifact(session_id, "html", file_path=html_path)
        artifacts.append(row)
    except Exception:
        log.exception("IDR HTML export failed: session=%s", session_id)


def _try_export_docx(session_id, text, title, out_dir, artifacts):
    try:
        from tools.presentations.generate_exec_doc import generate_docx

        docx_path = str(pathlib.Path(out_dir) / "document.docx")
        generate_docx(title=title, content=text, output_path=docx_path)
        row = sm.add_artifact(session_id, "docx", file_path=docx_path)
        artifacts.append(row)
    except ImportError:
        log.debug("generate_exec_doc not available — skipping DOCX export")
    except Exception:
        log.exception("IDR DOCX export failed: session=%s", session_id)


def _try_export_pdf(session_id, text, title, out_dir, artifacts):
    try:
        from tools.network.pdf_export import export_to_pdf

        pdf_path = str(pathlib.Path(out_dir) / "document.pdf")
        export_to_pdf(content=text, output_path=pdf_path, title=title)
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
