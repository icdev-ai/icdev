# CUI // SP-CTI
"""HITL Workflow Report Generator.

Orchestrates: style guide resolution → section routing → LLM synthesis (or
assembled fallback) → Jinja2 HTML rendering → citation population.
"""
from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from tools.db.storage import get_connection
from tools.workflow_hitl.report_schema import ReportSection, get_sections
from tools.workflow_hitl.section_router import SectionRouter, RoutedChunk, SectionRouteResult

logger = get_logger(__name__)

_TEMPLATE_DIR = Path(__file__).resolve().parent.parent.parent / "context" / "workflow_report_templates"
_DEFAULT_MAX_WORDS = 800


# ── Public API ────────────────────────────────────────────────────────────────

def generate_report(
    instance_id: str,
    report_type: str = "standard_audit",
    style_guide_id: Optional[str] = None,
    generated_by: str = "system",
) -> str:
    """Generate a report for a workflow instance. Returns wf_generated_reports.id."""
    report_id = f"wfr-{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc).isoformat()

    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO wf_generated_reports
               (id, instance_id, report_type, style_guide_id, status,
                generated_by, generated_at, updated_at)
               VALUES (%s,%s,%s,%s,'generating',%s,%s,%s)""",
            (report_id, instance_id, report_type, style_guide_id,
             generated_by, now, now),
        )
        try:
            conn.commit()
        except Exception:
            pass
    finally:
        conn.close()

    try:
        content_html, content_json, stats = _do_generate(
            report_id, instance_id, report_type, style_guide_id
        )
        _update_report(report_id, "completed",
                       content_html=content_html,
                       content_json=json.dumps(content_json),
                       word_count=stats["word_count"],
                       section_count=stats["section_count"],
                       citation_count=stats["citation_count"])
        logger.info("generate_report: %s completed (%d sections, %d citations)",
                    report_id, stats["section_count"], stats["citation_count"])
    except Exception as exc:
        _update_report(report_id, "failed", error_message=str(exc))
        logger.exception("generate_report: failed for instance=%s", instance_id)
        raise

    return report_id


def get_report(report_id: str) -> Optional[dict]:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM wf_generated_reports WHERE id=%s", (report_id,)
        ).fetchone()
        if not row:
            return None
        report = dict(row)
        if report.get("content_json"):
            try:
                report["sections"] = json.loads(report["content_json"])
            except Exception:
                pass
        return report
    finally:
        conn.close()


def list_reports(instance_id: Optional[str] = None) -> list[dict]:
    conn = get_connection()
    try:
        if instance_id:
            rows = conn.execute(
                "SELECT * FROM wf_generated_reports WHERE instance_id=%s ORDER BY generated_at DESC",
                (instance_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM wf_generated_reports ORDER BY generated_at DESC LIMIT 100"
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ── Core pipeline ─────────────────────────────────────────────────────────────

def _do_generate(
    report_id: str,
    instance_id: str,
    report_type: str,
    style_guide_id: Optional[str],
) -> tuple[str, list[dict], dict]:
    # 1. Load instance + applicable document templates
    instance = _load_instance(instance_id)
    if not instance:
        raise ValueError(f"Instance {instance_id!r} not found")

    canvas_type = instance.get("canvas_type")
    project_id = instance.get("project_id") or ""
    applicable_template_ids = _get_applicable_template_ids(instance_id, instance)

    # 2. Load report sections
    sections = get_sections(report_type)
    if not sections:
        raise ValueError(f"Unknown report_type {report_type!r} — no sections seeded")

    # 3. Resolve style guide
    style_rules = _resolve_style_guide(style_guide_id, canvas_type, project_id)

    # 4. Run section router
    router = SectionRouter(project_id=project_id)
    routed = router.route(sections, applicable_template_ids)

    # 5. Generate section content
    section_section_map = {s.key: s for s in sections}
    rendered_sections: list[dict] = []
    all_citations: list[dict] = []

    for route_result in routed:
        section_def = section_section_map.get(route_result.section_key)
        if not section_def:
            continue
        prose, citations = _generate_section_content(
            section=section_def,
            chunks=route_result.chunks,
            style_rules=style_rules,
            report_type=report_type,
        )
        rendered_sections.append({
            "section_key": route_result.section_key,
            "section_name": route_result.section_name,
            "content": prose,
            "chunk_count": len(route_result.chunks),
        })
        all_citations.extend(citations)
        _store_section_chunks(report_id, route_result)

    # 6. Render HTML
    content_html = _render_html(report_type, instance, rendered_sections, style_rules)

    # 7. Persist citations to wf_citations
    _persist_citations(instance_id, all_citations)

    total_words = sum(len(s["content"].split()) for s in rendered_sections)
    return content_html, rendered_sections, {
        "word_count": total_words,
        "section_count": len(rendered_sections),
        "citation_count": len(all_citations),
    }


def _generate_section_content(
    section: ReportSection,
    chunks: list[RoutedChunk],
    style_rules: dict,
    report_type: str,
) -> tuple[str, list[dict]]:
    """Generate prose for one section. Returns (prose, citations_list)."""
    if not chunks:
        return "*No source material available for this section.*", []

    citations = []
    for i, chunk in enumerate(chunks, 1):
        citations.append({
            "inline_ref": f"[{i}]",
            "chunk_id": chunk.chunk_id,
            "source_doc": chunk.filename or chunk.wf_document_template_id,
            "section": chunk.section_heading,
            "page_number": chunk.page_number,
            "excerpt": chunk.content[:300],
            "relevance_score": chunk.relevance_score,
        })

    no_llm = os.getenv("ICDEV_NO_LLM", "false").lower() in ("true", "1")
    if no_llm:
        return _assemble_section_no_llm(section, chunks, citations, style_rules), citations

    return _llm_synthesize_section(section, chunks, citations, style_rules, report_type), citations


def _assemble_section_no_llm(
    section: ReportSection,
    chunks: list[RoutedChunk],
    citations: list[dict],
    style_rules: dict,
) -> str:
    """Deterministic assembly: quoted excerpts with citation numbers."""
    lines = [f"**{section.name}**\n"]
    for i, chunk in enumerate(chunks, 1):
        heading = f" — *{chunk.section_heading}*" if chunk.section_heading else ""
        source = chunk.filename or chunk.wf_document_template_id or "source"
        lines.append(f"[{i}] {chunk.content.strip()}")
        lines.append(f"  *(Source: {source}{heading})*\n")
    return "\n".join(lines)


def _llm_synthesize_section(
    section: ReportSection,
    chunks: list[RoutedChunk],
    citations: list[dict],
    style_rules: dict,
    report_type: str,
) -> str:
    """Use LLM to synthesize prose from chunks. Falls back to assembly on error."""
    tone = style_rules.get("tone", "formal technical")
    max_words = min(section.max_words, style_rules.get("max_section_words", _DEFAULT_MAX_WORDS))
    citation_style = style_rules.get("citation_style", "numeric")

    source_material = "\n\n".join(
        f"[{i}] (Source: {c['source_doc']}, Section: {c['section'] or 'N/A'})\n{c['excerpt']}"
        for i, c in enumerate(citations, 1)
    )

    prompt = (
        f"You are writing the \"{section.name}\" section of a {report_type.replace('_', ' ')} report.\n\n"
        f"Tone and style: {tone}.\n"
        f"Maximum length: {max_words} words.\n"
        f"Citation style: {citation_style} — use inline markers like [1], [2] for each fact cited.\n\n"
        f"Section purpose: {section.description}\n\n"
        f"Source material (use these; cite with [N]):\n{source_material}\n\n"
        f"Write the section now. Do not include the section title — only the body content."
    )

    try:
        from tools.llm.router import LLMRouter
        from tools.llm.provider import LLMRequest
        router = LLMRouter()
        response = router.invoke(
            "report_generation",
            LLMRequest(
                messages=[{"role": "user", "content": prompt}],
                max_tokens=max_words * 2,
            ),
        )
        text = (response.content or "").strip()
        return text if text else _assemble_section_no_llm(
            section, chunks, citations, style_rules
        )
    except Exception as exc:
        logger.warning("LLM synthesis failed for section %r: %s — using assembly fallback", section.key, exc)
        return _assemble_section_no_llm(section, chunks, citations, style_rules)


# ── Style guide ───────────────────────────────────────────────────────────────

def _resolve_style_guide(
    style_guide_id: Optional[str],
    canvas_type: Optional[str],
    project_id: str,
) -> dict:
    """Load wg_style_guides YAML and return parsed rules dict."""
    defaults = {
        "tone": "formal technical",
        "max_section_words": 800,
        "citation_style": "numeric",
        "heading_level": "h2",
        "classification_banner": "CUI // SP-CTI",
    }
    guide_yaml = None

    conn = get_connection()
    try:
        if style_guide_id:
            row = conn.execute(
                "SELECT guide_yaml FROM wg_style_guides WHERE id=%s", (style_guide_id,)
            ).fetchone()
            if row:
                guide_yaml = row[0]
        elif project_id:
            row = conn.execute(
                """SELECT guide_yaml FROM wg_style_guides
                   WHERE scope='project' AND scope_id=%s AND is_active=1
                   ORDER BY created_at DESC LIMIT 1""",
                (project_id,),
            ).fetchone()
            if row:
                guide_yaml = row[0]
    except Exception as exc:
        logger.debug("style guide lookup failed: %s", exc)
    finally:
        conn.close()

    if not guide_yaml:
        return defaults

    try:
        import yaml
        parsed = yaml.safe_load(guide_yaml) or {}
        return {**defaults, **parsed}
    except Exception:
        return defaults


# ── HTML rendering ────────────────────────────────────────────────────────────

def _render_html(
    report_type: str,
    instance: dict,
    sections: list[dict],
    style_rules: dict,
) -> str:
    template_path = _TEMPLATE_DIR / f"{report_type}.html"
    if not template_path.exists():
        template_path = _TEMPLATE_DIR / "standard_audit.html"

    try:
        from jinja2 import Environment, FileSystemLoader, select_autoescape
        env = Environment(
            loader=FileSystemLoader(str(_TEMPLATE_DIR)),
            autoescape=select_autoescape(["html"]),
        )
        tmpl = env.get_template(template_path.name)
        return tmpl.render(
            instance=instance,
            sections=sections,
            style=style_rules,
            report_type=report_type,
            generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            classification=style_rules.get("classification_banner", "CUI // SP-CTI"),
        )
    except Exception as exc:
        logger.warning("Jinja2 render failed: %s — using plain HTML", exc)
        return _plain_html(report_type, instance, sections, style_rules)


def _plain_html(report_type: str, instance: dict, sections: list[dict], style_rules: dict) -> str:
    classification = style_rules.get("classification_banner", "CUI // SP-CTI")
    parts = [
        f"<!DOCTYPE html><html><head><meta charset='utf-8'><title>{report_type.replace('_',' ').title()}</title></head><body>",
        f"<div class='classification-banner'>{classification}</div>",
        f"<h1>{report_type.replace('_', ' ').title()}</h1>",
        f"<p><strong>Instance:</strong> {instance.get('id', 'N/A')} | "
        f"<strong>Canvas:</strong> {instance.get('canvas_type', 'N/A')} | "
        f"<strong>Generated:</strong> {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}</p>",
        "<hr>",
    ]
    for sec in sections:
        heading_level = style_rules.get("heading_level", "h2")
        parts.append(f"<{heading_level}>{sec['section_name']}</{heading_level}>")
        content = sec["content"].replace("\n", "<br>")
        parts.append(f"<div class='section-content'>{content}</div>")
    parts.append(f"<div class='classification-banner'>{classification}</div></body></html>")
    return "\n".join(parts)


# ── DB helpers ────────────────────────────────────────────────────────────────

def _load_instance(instance_id: str) -> Optional[dict]:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM wf_instances WHERE id=%s", (instance_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _get_applicable_template_ids(instance_id: str, instance: dict) -> list[str]:
    """Return doc_template_ids applicable to this instance.

    Looks at the template's stages_json for required_docs, plus all AI-reference
    standards for the instance's canvas_type.
    """
    template_ids: set[str] = set()
    conn = get_connection()
    try:
        # From workflow template stages
        tmpl_row = conn.execute(
            "SELECT stages_json FROM wf_templates WHERE id=%s", (instance.get("template_id", ""),)
        ).fetchone()
        if tmpl_row:
            try:
                stages = json.loads(tmpl_row[0] or "[]")
                for stage in stages:
                    for doc in stage.get("required_docs", []):
                        template_ids.add(doc["doc_template_id"])
            except Exception:
                pass

        # AI-reference standards for canvas_type
        canvas_type = instance.get("canvas_type")
        if canvas_type:
            rows = conn.execute(
                """SELECT id FROM wf_document_templates
                   WHERE is_ai_reference=1
                   AND (canvas_type=%s OR canvas_type IS NULL)""",
                (canvas_type,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id FROM wf_document_templates WHERE is_ai_reference=1"
            ).fetchall()
        for r in rows:
            template_ids.add(r[0])
    finally:
        conn.close()
    return list(template_ids)


def _store_section_chunks(report_id: str, route_result: SectionRouteResult) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn = get_connection()
    try:
        for chunk in route_result.chunks:
            conn.execute(
                """INSERT OR IGNORE INTO wf_report_section_chunks
                   (id, report_id, section_key, chunk_id, relevance_score, rank, created_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                (f"wrsc-{uuid.uuid4().hex[:12]}", report_id,
                 route_result.section_key, chunk.chunk_id,
                 chunk.relevance_score, chunk.rank, now),
            )
        try:
            conn.commit()
        except Exception:
            pass
    finally:
        conn.close()


def _persist_citations(instance_id: str, citations: list[dict]) -> None:
    from tools.workflow_hitl import citation_manager as cm
    for c in citations:
        try:
            cm.add_citation(
                instance_id=instance_id,
                stage="report",
                source_doc=c.get("source_doc", "unknown"),
                section=c.get("section", ""),
                page_number=c.get("page_number"),
                excerpt=c.get("excerpt", ""),
                cited_by="system",
                cited_in_type="report",
                cited_in_id=c.get("chunk_id", ""),
            )
        except Exception as exc:
            logger.debug("citation persist failed: %s", exc)


def _update_report(
    report_id: str,
    status: str,
    content_html: str = "",
    content_json: str = "",
    word_count: int = 0,
    section_count: int = 0,
    citation_count: int = 0,
    error_message: Optional[str] = None,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn = get_connection()
    try:
        conn.execute(
            """UPDATE wf_generated_reports SET status=%s, content_html=%s, content_json=%s,
               word_count=%s, section_count=%s, citation_count=%s, error_message=%s, updated_at=%s
               WHERE id=%s""",
            (status, content_html, content_json, word_count, section_count,
             citation_count, error_message, now, report_id),
        )
        try:
            conn.commit()
        except Exception:
            pass
    finally:
        conn.close()
