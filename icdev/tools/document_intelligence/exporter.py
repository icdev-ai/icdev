# CUI // SP-CTI
"""DIC version export — the one door a whitepaper leaves the canvas through (rmf-wp-02).

THE DEFECT. DIC had NO export route at all. A version could be drafted,
reviewed, approved and annotated, and the only way its prose left the canvas
was copy-paste from the page -- which passes every TRUST gate this canvas
enforces at approval by never touching one. docgen (tools/docgen) has had the
right shape since cnr-doc-01: ``citation_publish_gate`` (placeholder ->
citation -> claim), a WriteGuard gate that must have PASSED, then
``_try_export_*`` writing one ``idr_artifacts`` row per format. This module is
that shape on DIC's own tables.

THREE GATES, IN THIS ORDER, and every one fails CLOSED:

  placeholder_guard  ``consistency_checker.check_version_consistency`` -- the
                     shared ``placeholder_findings`` over every section.
  citation_guard     ``consistency_checker.check_version_citations`` -- the
                     shared ``citation_gate`` over AI-authored sections, each
                     against the evidence RECORDED for it (``citations_json``).
                     These two are the SAME gates the approve route runs, so an
                     export can never be laxer than an approval. They are the
                     shared grounding primitives docgen's gate composes; a
                     second copy of citation parsing is exactly what the TRUST
                     invariant in CLAUDE.md forbids.
  writeguard         ``tools.pulse.writeguard.run_full_quality_check`` over the
                     ASSEMBLED document -- docgen blocks publish on it
                     (``stage6_check_gate``); DIC never called it.

A gate that could not MEASURE (a DB error under the section read, WriteGuard
unimportable or raising) is ``unmeasured`` and BLOCKS, and no ``force_*`` flag
opens it -- "never publish text no gate could inspect" is docgen's
``grounding_available`` rule, kept. A gate that measured a DEFECT blocks unless
the matching ``force_*`` flag is set AND ``force_reason`` is non-empty (the
pulse.py / docgen precedent); the caller records the override to the
append-only audit before writing the file.

THE ROW IS THE RECORD. One ``dic_artifacts`` row per export, mirroring
``idr_artifacts``, carrying the file's sha256, the WriteGuard score, the full
gate report and whether a human forced it. ``version_status`` is recorded as
it was at export time because export does NOT require ``approved``: a draft
exported for offline review is legitimate, but the artifact must say what it
was.

Formats: ``md`` (the assembled markdown), ``html`` (docgen's sanitised
renderer), ``docx`` (``tools.govcon.rfi_docx_exporter.markdown_to_docx``, the
exporter rmf-docx-01 proved works -- with the classification LABEL as the
header/footer marking, never a hard-coded FOUO string), ``pdf`` (only when
fpdf2 is present; ``pdf_export`` otherwise writes HTML under a .pdf name,
cnr-doc-04). A format whose library is absent reports ``unavailable`` and
writes nothing.

Library only, no CLI. The route is
``GET /document-intelligence/api/versions/<id>/export/<fmt>``.
"""
from __future__ import annotations

import logging
import hashlib
import json
import os
import pathlib
import uuid
from datetime import datetime, timezone
from typing import Any

from tools.logging.icdev_logger import get_logger

_log = logging.getLogger(__name__)

logger = get_logger(__name__)

#: Every format the route accepts. The migration's CHECK constraint and the
#: template's buttons are asserted against THIS tuple, never restated.
EXPORT_FORMATS: tuple[str, ...] = ("md", "html", "docx", "pdf")

#: Gate names. The first two are PUBLISH_GATES vocabulary (recorded to
#: idr_publish_audit on override); ``writeguard`` is a QUALITY gate, recorded on
#: override as a ``dic.hitl_decision`` audit event instead, because the
#: idr_publish_audit CHECK admits only the TRUST guards.
GATE_PLACEHOLDER = "placeholder_guard"
GATE_CITATION = "citation_guard"
GATE_WRITEGUARD = "writeguard"
EXPORT_GATES: tuple[str, ...] = (GATE_PLACEHOLDER, GATE_CITATION, GATE_WRITEGUARD)

#: Where artifacts land. Overridable so a test never writes under data/.
ARTIFACT_DIR_ENV = "ICDEV_DIC_ARTIFACT_DIR"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS dic_artifacts (
    artifact_id      TEXT PRIMARY KEY,
    version_id       TEXT NOT NULL,
    doc_id           TEXT NOT NULL,
    format           TEXT NOT NULL,
    file_path        TEXT,
    sha256           TEXT,
    byte_size        INTEGER,
    title            TEXT,
    version_status   TEXT,
    wg_score         REAL,
    wg_passed        INTEGER,
    gate_report_json TEXT,
    forced           INTEGER NOT NULL DEFAULT 0,
    force_reason     TEXT,
    exported_by      TEXT,
    tenant_id        TEXT,
    classification   TEXT,
    created_at       TEXT NOT NULL
)
"""


class ExportBlocked(Exception):
    """A gate refused the export. ``report`` is the full gate report."""

    def __init__(self, report: dict[str, Any]):
        self.report = report
        super().__init__(f"export blocked by {report.get('gate')}")


class ExportUnavailable(Exception):
    """The requested format's renderer is not installed on this deployment."""


# ── connection / schema ───────────────────────────────────────────────────────

def _conn():
    from tools.db.storage import get_connection

    return get_connection()


def _ensure_schema(conn) -> None:
    cur = conn.cursor()
    cur.execute(_SCHEMA)
    conn.commit()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_get(row: Any, name: str, index: int) -> Any:
    if row is None:
        return None
    if isinstance(row, (list, tuple)):
        return row[index] if len(row) > index else None
    try:
        return row[name]
    except Exception:
        try:
            return row[index]
        except Exception:
            return None


def artifact_dir() -> pathlib.Path:
    return pathlib.Path(
        os.environ.get(ARTIFACT_DIR_ENV)
        or pathlib.Path("data") / "document_intelligence" / "artifacts"
    )


# ── load + assemble ───────────────────────────────────────────────────────────

def load_version(version_id: str) -> dict[str, Any] | None:
    """The version, its document and its sections, or None when absent.

    Raises on a DB error rather than returning None: "no such version" (404)
    and "could not read the version" (500) are different answers.
    """
    conn = _conn()
    try:
        vrow = conn.execute(
            "SELECT version_id, doc_id, version_no, origin, status, tenant_id, classification "
            "FROM dic_versions WHERE version_id = %s LIMIT 1",
            (version_id,),
        ).fetchone()
        if not vrow:
            return None
        version = {
            "version_id": _row_get(vrow, "version_id", 0),
            "doc_id": _row_get(vrow, "doc_id", 1),
            "version_no": _row_get(vrow, "version_no", 2),
            "origin": _row_get(vrow, "origin", 3),
            "status": _row_get(vrow, "status", 4),
            "tenant_id": _row_get(vrow, "tenant_id", 5),
            "classification": _row_get(vrow, "classification", 6),
        }
        drow = conn.execute(
            "SELECT doc_id, title, collection_id, classification, filename "
            "FROM dic_documents WHERE doc_id = %s LIMIT 1",
            (version["doc_id"],),
        ).fetchone()
        doc = {
            "doc_id": _row_get(drow, "doc_id", 0) or version["doc_id"],
            "title": _row_get(drow, "title", 1),
            "collection_id": _row_get(drow, "collection_id", 2),
            "classification": _row_get(drow, "classification", 3),
            "filename": _row_get(drow, "filename", 4),
        }
        srows = conn.execute(
            "SELECT section_id, heading, content, citations_json, status, origin "
            "FROM dic_sections WHERE version_id = %s ORDER BY created_at, section_id",
            (version_id,),
        ).fetchall()
        sections = [
            {
                "section_id": _row_get(r, "section_id", 0),
                "heading": _row_get(r, "heading", 1),
                "content": _row_get(r, "content", 2) or "",
                "citations_json": _row_get(r, "citations_json", 3),
                "status": _row_get(r, "status", 4),
                "origin": _row_get(r, "origin", 5),
            }
            for r in srows
        ]
    finally:
        conn.close()
    return {"version": version, "doc": doc, "sections": sections}


def document_title(bundle: dict[str, Any]) -> str:
    doc = bundle.get("doc") or {}
    return (doc.get("title") or doc.get("filename") or doc.get("doc_id") or "Document").strip()


def document_classification(bundle: dict[str, Any], fallback: str = "CUI") -> str:
    """The classification LABEL for the marking. A LABEL ('CUI'), never a
    banner ('CUI // SP-CTI') -- the same rule the RLS column follows."""
    for source in (bundle.get("version") or {}, bundle.get("doc") or {}):
        value = (source.get("classification") or "").strip()
        if value:
            return value
    return fallback


def assemble_markdown(bundle: dict[str, Any]) -> str:
    """One markdown document from the version's sections, in stored order.

    Headings become ``##``; a section with no heading keeps its prose. The
    citations a section carries are NOT rendered here -- the ``[source: …]``
    tags already sit inline in the prose, and the export is what the reader
    was shown, not a re-composition of it.
    """
    title = document_title(bundle)
    version = bundle.get("version") or {}
    parts = [f"# {title}", ""]
    vno = version.get("version_no")
    if vno is not None:
        parts.append(f"_Version {vno} · status: {version.get('status') or 'unknown'}_")
        parts.append("")
    for sec in bundle.get("sections") or []:
        heading = (sec.get("heading") or "").strip()
        if heading:
            parts.append(f"## {heading}")
            parts.append("")
        content = (sec.get("content") or "").strip()
        if content:
            parts.append(content)
            parts.append("")
    return "\n".join(parts).rstrip() + "\n"


# ── the gate ──────────────────────────────────────────────────────────────────

def _writeguard_check(doc_text: str) -> dict[str, Any]:
    """Run WriteGuard over the assembled document.

    ``measured`` False means the check did NOT run (unimportable or raised),
    which is never the same as passed -- docgen's stage6 test pins the same
    fail-closed posture on ImportError.
    """
    try:
        from tools.pulse.writeguard import run_full_quality_check
    except Exception as exc:  # noqa: BLE001
        return {"measured": False, "passed": None, "overall_score": None,
                "error": f"writeguard unavailable: {exc}"}
    try:
        result = run_full_quality_check(doc_text)
    except Exception as exc:  # noqa: BLE001
        return {"measured": False, "passed": None, "overall_score": None,
                "error": f"writeguard raised: {exc}"}
    if not isinstance(result, dict) or "passed" not in result:
        return {"measured": False, "passed": None, "overall_score": None,
                "error": "writeguard returned no verdict"}
    return {
        "measured": True,
        "passed": bool(result.get("passed")),
        "overall_score": result.get("overall_score"),
        "issues": (result.get("issues") or [])[:20],
        "composites": result.get("composites") or {},
    }


def export_gate(
    version_id: str,
    doc_text: str,
    *,
    force_placeholders: bool = False,
    force_citations: bool = False,
    force_writeguard: bool = False,
    force_reason: str = "",
) -> dict[str, Any]:
    """Placeholder -> citation -> WriteGuard. Returns the report; never raises.

    ``blocked`` True names the FIRST refusing gate in ``gate``. An override is
    honoured only with a non-empty ``force_reason``; each honoured override is
    listed in ``overrides`` (gate -> findings) for the caller to audit BEFORE
    it writes anything. ``unmeasured`` lists gates that could not run; any
    entry there blocks and no flag opens it.
    """
    from tools.document_intelligence.consistency_checker import (
        check_version_citations,
        check_version_consistency,
    )

    force_reason = (force_reason or "").strip()
    report: dict[str, Any] = {
        "blocked": False,
        "gate": None,
        "placeholder_findings": [],
        "citation_findings": [],
        "writeguard": {},
        "overrides": {},
        "unmeasured": [],
        "force_reason": force_reason,
    }

    def _refuse(gate: str) -> dict[str, Any]:
        report["blocked"] = True
        report["gate"] = gate
        return report

    # 1. placeholder_guard
    try:
        consistency = check_version_consistency(version_id)
    except Exception as exc:  # noqa: BLE001
        consistency = {"placeholders": [], "error": str(exc)}
    if consistency.get("error"):
        report["unmeasured"].append({"gate": GATE_PLACEHOLDER, "error": consistency["error"]})
        return _refuse(GATE_PLACEHOLDER)
    placeholder_hits = consistency.get("placeholders") or []
    report["placeholder_findings"] = placeholder_hits
    if placeholder_hits:
        if force_placeholders and force_reason:
            report["overrides"][GATE_PLACEHOLDER] = placeholder_hits
        else:
            return _refuse(GATE_PLACEHOLDER)

    # 2. citation_guard
    try:
        citations = check_version_citations(version_id)
    except Exception as exc:  # noqa: BLE001
        citations = {"findings": [], "error": str(exc)}
    if citations.get("error"):
        report["unmeasured"].append({"gate": GATE_CITATION, "error": citations["error"]})
        return _refuse(GATE_CITATION)
    citation_hits = citations.get("findings") or []
    report["citation_findings"] = citation_hits
    report["ai_section_count"] = citations.get("ai_section_count", 0)
    if citation_hits:
        if force_citations and force_reason:
            report["overrides"][GATE_CITATION] = citation_hits
        else:
            return _refuse(GATE_CITATION)

    # 3. writeguard -- over the ASSEMBLED document, the thing being exported.
    wg = _writeguard_check(doc_text)
    report["writeguard"] = wg
    if not wg.get("measured"):
        report["unmeasured"].append({"gate": GATE_WRITEGUARD, "error": wg.get("error")})
        return _refuse(GATE_WRITEGUARD)
    if not wg.get("passed"):
        if force_writeguard and force_reason:
            report["overrides"][GATE_WRITEGUARD] = [{
                "overall_score": wg.get("overall_score"),
                "issues": wg.get("issues") or [],
            }]
        else:
            return _refuse(GATE_WRITEGUARD)

    return report


# ── renderers ─────────────────────────────────────────────────────────────────

def format_available(fmt: str) -> tuple[bool, str]:
    """(available, reason). A renderer whose library is absent is reported,
    not silently downgraded to another format."""
    import importlib.util

    if fmt not in EXPORT_FORMATS:
        return False, f"unsupported format '{fmt}'"
    if fmt == "docx":
        try:
            from tools.govcon.rfi_docx_exporter import DOCX_AVAILABLE
        except Exception as exc:  # noqa: BLE001
            return False, f"docx exporter unavailable: {exc}"
        return (True, "") if DOCX_AVAILABLE else (False, "python-docx not installed")
    if fmt == "pdf":
        if importlib.util.find_spec("fpdf") is None:
            # cnr-doc-04: pdf_export writes HTML under a .pdf name without fpdf2.
            return False, "fpdf2 not installed"
        return True, ""
    return True, ""


def render(fmt: str, doc_text: str, title: str, classification: str,
           out_dir: pathlib.Path) -> pathlib.Path:
    """Write ``document.<fmt>`` into ``out_dir`` and return its path."""
    available, reason = format_available(fmt)
    if not available:
        raise ExportUnavailable(reason)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"document.{fmt}"

    if fmt == "md":
        marked = f"{classification}\n\n{doc_text}\n\n{classification}\n"
        path.write_text(marked, encoding="utf-8", newline="")
        return path

    if fmt == "html":
        # docgen's renderer: escapes the title/classification and sanitises
        # the rendered markdown (cnr-doc-03). It records to idr_artifacts via
        # session_manager, which is not our table -- so call the pure pieces.
        import html as _html

        from tools.docgen.workflow import _CLS_BANNER_STYLES, _sanitize_html

        cls_upper = (classification or "CUI").upper()
        banner_style = _CLS_BANNER_STYLES.get(cls_upper, _CLS_BANNER_STYLES["CUI"])
        banner_css = (f"{banner_style};padding:6px 12px;font-size:13px;font-weight:bold;"
                      "text-align:center;letter-spacing:1px;")
        safe_title = _html.escape(str(title or ""))
        safe_cls = _html.escape(str(classification or "CUI"))
        try:
            import markdown as _md
        except ImportError as exc:
            # tsg-iso-03: an optional third-party import inside a swallowing handler
            # is indistinguishable from working code -- the handler must SAY it fired.
            _log.warning("python-markdown is not installed (%s); exporting the body as <pre>", exc)
            _md = None
        if _md is None:
            body_html = f"<pre>{_html.escape(doc_text)}</pre>"
        else:
            try:
                body_html = _sanitize_html(_md.markdown(doc_text, extensions=["tables", "fenced_code"]))
            except Exception as exc:  # noqa: BLE001 -- a render failure, reported, then the plain body
                _log.warning("markdown render failed (%s); exporting the body as <pre>", exc)
                body_html = f"<pre>{_html.escape(doc_text)}</pre>"
        html = (
            "<!DOCTYPE html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n"
            f"<title>{safe_title}</title>\n<style>\n"
            "body{font-family:Arial,sans-serif;margin:0;padding:0;background:#fff;color:#111;}\n"
            f".cls-banner{{{banner_css}}}\n"
            ".doc-body{max-width:900px;margin:24px auto;padding:0 24px;}\n"
            ".content{line-height:1.7;}\n"
            ".content table{border-collapse:collapse;width:100%;}\n"
            ".content th,.content td{border:1px solid #ccc;padding:6px 10px;}\n"
            "</style>\n</head>\n<body>\n"
            f"<div class=\"cls-banner\">{safe_cls}</div>\n"
            f"<div class=\"doc-body\"><h1>{safe_title}</h1>"
            f"<div class=\"content\">{body_html}</div></div>\n"
            f"<div class=\"cls-banner\">{safe_cls}</div>\n</body>\n</html>\n"
        )
        path.write_text(html, encoding="utf-8", newline="")
        return path

    if fmt == "docx":
        from tools.govcon.rfi_docx_exporter import markdown_to_docx

        # The classification LABEL is the header/footer marking. The exporter's
        # default is a hard-coded FOUO string, which would mark a CUI document
        # as something it is not.
        markdown_to_docx(doc_text, str(path), classification=classification)
        return path

    if fmt == "pdf":
        from tools.network.pdf_export import export_to_pdf

        export_to_pdf(content=doc_text, output_path=str(path), title=title,
                      classification=classification)
        if not path.is_file():
            raise ExportUnavailable("pdf renderer wrote no file")
        return path

    raise ExportUnavailable(f"unsupported format '{fmt}'")  # pragma: no cover


# ── persistence ───────────────────────────────────────────────────────────────

def record_artifact(
    *,
    version_id: str,
    doc_id: str,
    fmt: str,
    file_path: pathlib.Path,
    title: str,
    version_status: str | None,
    gate_report: dict[str, Any],
    exported_by: str,
    tenant_id: str | None,
    classification: str | None,
) -> dict[str, Any]:
    """INSERT one dic_artifacts row. Raises on failure -- an export whose
    record did not land must not report success (the INSERT/schema rule)."""
    data = file_path.read_bytes()
    wg = gate_report.get("writeguard") or {}
    forced = bool(gate_report.get("overrides"))
    row = {
        "artifact_id": f"art_{uuid.uuid4().hex[:20]}",
        "version_id": version_id,
        "doc_id": doc_id,
        "format": fmt,
        "file_path": str(file_path),
        "sha256": hashlib.sha256(data).hexdigest(),
        "byte_size": len(data),
        "title": title,
        "version_status": version_status,
        "wg_score": wg.get("overall_score"),
        "wg_passed": (None if wg.get("passed") is None else int(bool(wg.get("passed")))),
        "gate_report_json": json.dumps(gate_report, default=str)[:20000],
        "forced": int(forced),
        "force_reason": gate_report.get("force_reason") if forced else None,
        "exported_by": exported_by,
        "tenant_id": tenant_id,
        "classification": classification,
        "created_at": _now(),
    }
    conn = _conn()
    try:
        _ensure_schema(conn)
        cols = ", ".join(row.keys())
        marks = ", ".join(["%s"] * len(row))
        conn.execute(
            f"INSERT INTO dic_artifacts ({cols}) VALUES ({marks})",  # noqa: S608 -- fixed column list
            tuple(row.values()),
        )
        conn.commit()
    finally:
        conn.close()
    return row


def get_artifact(artifact_id: str) -> dict[str, Any] | None:
    conn = _conn()
    try:
        _ensure_schema(conn)
        cur = conn.execute("SELECT * FROM dic_artifacts WHERE artifact_id = %s", (artifact_id,))
        row = cur.fetchone()
        if not row:
            return None
        cols = [d[0] for d in cur.description]
        return dict(zip(cols, row))
    finally:
        conn.close()


def list_artifacts(version_id: str) -> list[dict[str, Any]]:
    conn = _conn()
    try:
        _ensure_schema(conn)
        cur = conn.execute(
            "SELECT artifact_id, version_id, doc_id, format, file_path, sha256, byte_size, "
            "title, version_status, wg_score, wg_passed, forced, force_reason, exported_by, "
            "created_at FROM dic_artifacts WHERE version_id = %s ORDER BY created_at DESC",
            (version_id,),
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
    finally:
        conn.close()


# ── the whole act ─────────────────────────────────────────────────────────────

def export_version(
    version_id: str,
    fmt: str,
    *,
    exported_by: str = "dashboard",
    force_placeholders: bool = False,
    force_citations: bool = False,
    force_writeguard: bool = False,
    force_reason: str = "",
    tenant_id: str | None = None,
    classification: str | None = None,
    on_overrides=None,
) -> dict[str, Any]:
    """Gate, then render, then record. Returns ``{artifact, gate}``.

    Raises ``LookupError`` for an unknown version, ``ExportBlocked`` when a
    gate refuses, ``ExportUnavailable`` when the format cannot be rendered
    here. ``on_overrides(overrides)`` is called AFTER the gate and BEFORE the
    file is written, so the caller can audit a forced export before it exists;
    if it raises, nothing is written.
    """
    fmt = (fmt or "").strip().lower()
    if fmt not in EXPORT_FORMATS:
        raise ExportUnavailable(f"unsupported format '{fmt}'")
    available, reason = format_available(fmt)
    if not available:
        raise ExportUnavailable(reason)

    bundle = load_version(version_id)
    if bundle is None:
        raise LookupError(version_id)
    doc_text = assemble_markdown(bundle)
    title = document_title(bundle)
    marking = document_classification(bundle, fallback=classification or "CUI")

    report = export_gate(
        version_id, doc_text,
        force_placeholders=force_placeholders,
        force_citations=force_citations,
        force_writeguard=force_writeguard,
        force_reason=force_reason,
    )
    if report["blocked"]:
        raise ExportBlocked(report)
    if report["overrides"] and on_overrides is not None:
        on_overrides(report["overrides"])

    out_dir = artifact_dir() / version_id / uuid.uuid4().hex[:8]
    path = render(fmt, doc_text, title, marking, out_dir)
    version = bundle["version"]
    artifact = record_artifact(
        version_id=version_id,
        doc_id=version["doc_id"],
        fmt=fmt,
        file_path=path,
        title=title,
        version_status=version.get("status"),
        gate_report=report,
        exported_by=exported_by,
        tenant_id=tenant_id or version.get("tenant_id"),
        classification=marking,
    )
    logger.info("dic export: version=%s fmt=%s artifact=%s forced=%s",
                version_id, fmt, artifact["artifact_id"], bool(report["overrides"]))
    return {"artifact": artifact, "gate": report}
