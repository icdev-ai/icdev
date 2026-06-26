# CUI // SP-CTI
"""IDR Context Builder — assembles unified context for Stage 4 / 5 generation.

Pulls together:
  - RAG+KG evidence from DIC collection (grounded search)
  - Merged NDC 6-tab analysis findings (from all diagram analyses)
  - Config / IaC review findings
  - Supplemental uploads (email snippets, meeting notes)
  - WriteGuard document template structure (section headings, required content)
  - ACE coworker team specification

Evidence Priority Tiers (highest → lowest confidence):
  OPERATOR   — supplemental_text (free-form operator notes; overrides contradictions)
  EMAIL      — email upload bodies + attachments (high confidence)
  SESSION-DOC — primary uploaded documents (standard)
  DIC-KB     — full-collection RAG search hits (standard)
  KG-ENTITY  — Knowledge Graph entity expansion results (supporting)

The assembled context dict is passed into ACEController.launch() and then into
doc_generator.generate_document() as supplemental evidence.
"""
from __future__ import annotations

import email as _email_stdlib
import pathlib
import re
from typing import Any

from tools.logging.icdev_logger import get_logger

log = get_logger(__name__)

_MAX_SOURCE_CHARS = 40_000  # ~10k tokens — cap per file to avoid context explosion

# Evidence tier labels used by doc_generator to weight conflicting claims.
TIER_OPERATOR = "OPERATOR"
TIER_EMAIL = "EMAIL"
TIER_SOURCE = "SESSION-DOC"
TIER_DIC_KB = "DIC-KB"
TIER_KG = "KG-ENTITY"

# Regex for extracting candidate KG entity strings from text
_RE_IP = re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}(?:/\d+)?\b")
_RE_IDENT = re.compile(r"\b[A-Z][A-Z0-9\-]{2,}\b")


def _extract_text_from_file(file_path: str, upload_type: str = "doc") -> str:
    """Best-effort text extraction from PDF, DOCX, plain text, email, or image files."""
    p = pathlib.Path(file_path)
    if not p.is_file():
        return ""
    suffix = p.suffix.lower()
    try:
        if upload_type == "email" or suffix in (".eml", ".msg"):
            return _extract_email(str(p))
        if upload_type == "diagram" and suffix in (".png", ".jpg", ".jpeg", ".tiff", ".bmp", ".gif"):
            return _extract_image_text(str(p))
        if suffix == ".pdf":
            try:
                import pypdf
                reader = pypdf.PdfReader(str(p))
                return "\n".join(page.extract_text() or "" for page in reader.pages)
            except ImportError:
                pass
            try:
                import pdfminer.high_level as _pm
                return _pm.extract_text(str(p))
            except ImportError:
                pass
            return ""
        if suffix in (".docx", ".doc"):
            try:
                import docx as _docx
                doc = _docx.Document(str(p))
                return "\n".join(para.text for para in doc.paragraphs if para.text.strip())
            except Exception:
                return ""
        # Plain text / markdown / yaml / json / unknown — read as text
        return p.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        log.warning("IDR text extraction failed for %s: %s", file_path, exc)
        return ""


def _extract_email(file_path: str) -> str:
    """Parse an EML file using Python stdlib email module.

    Returns a structured string: headers + body (plain-text preferred) + attachment excerpts.
    Treated as TIER_EMAIL — high confidence.
    """
    try:
        p = pathlib.Path(file_path)
        raw = p.read_bytes()
        msg = _email_stdlib.message_from_bytes(raw)

        header_lines = []
        for hdr in ("From", "To", "Cc", "Subject", "Date"):
            val = msg.get(hdr, "")
            if val:
                header_lines.append(f"{hdr}: {val}")
        headers = "\n".join(header_lines)

        body = ""
        attachments: list[str] = []
        if msg.is_multipart():
            for part in msg.walk():
                ct = part.get_content_type()
                cd = str(part.get("Content-Disposition", ""))
                if ct == "text/plain" and "attachment" not in cd:
                    try:
                        body = part.get_payload(decode=True).decode(
                            part.get_content_charset("utf-8"), errors="replace"
                        )
                    except Exception:
                        pass
                elif "attachment" in cd and ct == "text/plain":
                    try:
                        att_text = part.get_payload(decode=True).decode(
                            part.get_content_charset("utf-8"), errors="replace"
                        )
                        fname = part.get_filename("attachment")
                        attachments.append(f"[Attachment: {fname}]\n{att_text[:5000]}")
                    except Exception:
                        pass
        else:
            try:
                body = msg.get_payload(decode=True).decode(
                    msg.get_content_charset("utf-8"), errors="replace"
                )
            except Exception:
                body = str(msg.get_payload())

        parts = []
        if headers:
            parts.append(f"Email headers:\n{headers}")
        if body:
            parts.append(f"Email body:\n{body[:10000]}")
        parts.extend(attachments[:3])  # cap at 3 attachments
        return "\n\n".join(parts)
    except Exception as exc:
        log.warning("IDR email extraction failed for %s: %s", file_path, exc)
        return ""


def _extract_image_text(file_path: str) -> str:
    """Extract text from an image via OCR (pytesseract / easyocr fallback).

    Uses tools.document_intelligence.extractors when available. Degrades silently
    if neither OCR library nor vision route is available.
    """
    try:
        from tools.document_intelligence.extractors import _extract_image as _di_extract
        result = _di_extract(pathlib.Path(file_path))
        return result.text if result and result.text else ""
    except Exception:
        pass
    # Fallback: try pytesseract directly
    try:
        import pytesseract
        from PIL import Image
        return pytesseract.image_to_string(Image.open(file_path))
    except Exception:
        pass
    log.warning("IDR image OCR not available for %s — skipping", file_path)
    return ""


def _expand_kg_entities(
    source_texts: list[str],
    config_findings: list[dict[str, Any]],
    diagram_findings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Extract named entities from evidence and expand via Knowledge Graph.

    Returns a list of chunk dicts: {text: str, tier: TIER_KG, source: str}.
    Degrades gracefully if KG is unavailable (returns []).
    """
    # Collect candidate entities
    candidates: set[str] = set()
    full_text = "\n".join(source_texts)

    # IP addresses / CIDR blocks
    for m in _RE_IP.findall(full_text):
        candidates.add(m)

    # All-caps identifiers (protocols, device names, acronyms)
    for m in _RE_IDENT.findall(full_text):
        candidates.add(m)

    # Config finding titles
    for f in config_findings:
        finding = f.get("finding") or f
        if isinstance(finding, dict):
            title = finding.get("title", "")
            if title:
                candidates.add(title[:60])

    # Diagram finding titles
    for df in diagram_findings:
        for item in (df.get("security_items") or []):
            t = item.get("title", "")
            if t:
                candidates.add(t[:60])

    # Deduplicate + cap
    entity_list = sorted(candidates)[:20]
    if not entity_list:
        return []

    kg_chunks: list[dict[str, Any]] = []
    try:
        from tools.knowledge_graph.graph_rag import retrieve as _kg_retrieve

        for entity in entity_list:
            try:
                kg_result = _kg_retrieve(
                    query=entity,
                    profile="security",
                    top_k=5,
                    compress=False,
                )
                context_text = (kg_result or {}).get("context", "")
                if context_text and len(context_text.strip()) > 20:
                    kg_chunks.append({
                        "text": f"[{TIER_KG}] KG entity '{entity}':\n{context_text[:1500]}",
                        "tier": TIER_KG,
                        "source": f"kg:{entity}",
                    })
            except Exception as exc:
                log.debug("KG expand failed for entity '%s': %s", entity, exc)
    except ImportError:
        log.debug("KG graph_rag not available — skipping entity expansion")

    log.info(
        "IDR KG expansion: %d entities queried → %d chunks",
        len(entity_list), len(kg_chunks),
    )
    return kg_chunks


def build_context(
    session: dict[str, Any],
    uploads: list[dict[str, Any]],
    analyses: list[dict[str, Any]],
    merged_graph: dict[str, Any] | None = None,
    config_review_results: list[dict[str, Any]] | None = None,
    supplemental_text: str | None = None,
    template_structure: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build and return the full generation context for this IDR session.

    Returns a dict with the following keys:
        session_id, domain, doc_type, classification, ace_roles,
        evidence_blocks, topology_summary, config_findings,
        template_sections, supplemental_notes, query_string,
        kg_chunks, supplemental_text.
    """
    domain = session.get("domain", "network")
    doc_type = session.get("doc_type", "runbook")

    # 1. Topology summary from merged graph
    topology_summary: dict[str, Any] = {}
    if merged_graph:
        nodes = merged_graph.get("nodes", [])
        edges = merged_graph.get("edges", [])
        stitched = merged_graph.get("_stitched_hosts", [])
        topology_summary = {
            "node_count": len(nodes),
            "edge_count": len(edges),
            "stitched_hosts": stitched,
            "node_types": _count_by_type(nodes, "type"),
            "sample_nodes": [
                {"label": n.get("label"), "type": n.get("type"), "sources": n.get("sources", [])}
                for n in nodes[:20]
            ],
        }

    # 2. Config / IaC review findings — from caller-supplied results AND stored result_json
    import json as _json
    config_findings: list[dict[str, Any]] = []
    if config_review_results:
        for cr in config_review_results:
            if isinstance(cr, dict):
                config_findings.extend(_flatten_findings(cr))
    # Pull persisted findings from Stage 2 result_json (config_review / iac_review)
    for analysis in (analyses or []):
        a_type = analysis.get("analysis_type", "")
        raw_json = analysis.get("result_json")
        if a_type in ("config_review", "iac_review", "firewall_review") and raw_json:
            try:
                stored = _json.loads(raw_json)
                config_findings.extend(_flatten_findings(stored))
                log.debug(
                    "IDR context: loaded %s findings from analysis %s",
                    a_type, analysis.get("id", "?"),
                )
            except Exception as exc:
                log.debug("IDR context: skipping result_json for %s: %s", a_type, exc)

    # 3. Diagram analysis findings — load rich data from stored result_json
    diagram_findings: list[dict[str, Any]] = []
    for analysis in (analyses or []):
        if analysis.get("analysis_type") != "diagram_analysis":
            continue
        entry: dict[str, Any] = {
            "upload_id": analysis.get("upload_id"),
            "result_ref_id": analysis.get("result_ref_id"),
            "status": analysis.get("status"),
        }
        raw_json = analysis.get("result_json")
        if raw_json:
            try:
                stored = _json.loads(raw_json)
                tabs = stored.get("tabs") or {}
                security_items: list[dict] = []
                for tab_key in ("security", "compliance", "remediate"):
                    for item in tabs.get(tab_key, []):
                        if isinstance(item, dict):
                            security_items.append({"tab": tab_key, **item})
                if security_items:
                    entry["security_items"] = security_items
                if stored.get("cloud_context"):
                    entry["cloud_context"] = stored["cloud_context"]
            except Exception:
                pass
        diagram_findings.append(entry)

    # 4. Extract text from uploads — doc/supplement/email with tier labels
    source_texts: list[str] = []
    email_texts: list[str] = []

    for up in (uploads or []):
        utype = up.get("upload_type", "")
        fpath = up.get("file_path", "")
        fname = up.get("filename", fpath)

        if utype == "email":
            raw = _extract_text_from_file(fpath, upload_type="email")
            if raw.strip():
                trimmed = raw[:_MAX_SOURCE_CHARS]
                email_texts.append(
                    f"[{TIER_EMAIL} — HIGH CONFIDENCE]\n--- Email: {fname} ---\n{trimmed}"
                )
                log.info("IDR context: extracted %d chars from email %s", len(trimmed), fname)

        elif utype in ("doc", "supplement"):
            raw = _extract_text_from_file(fpath, upload_type=utype)
            if raw.strip():
                trimmed = raw[:_MAX_SOURCE_CHARS]
                source_texts.append(
                    f"[{TIER_SOURCE}]\n--- Source: {fname} ---\n{trimmed}"
                )
                log.info("IDR context: extracted %d chars from %s", len(trimmed), fname)

        elif utype == "diagram":
            raw = _extract_text_from_file(fpath, upload_type="diagram")
            if raw.strip():
                trimmed = raw[:5000]  # diagrams: smaller cap
                source_texts.append(
                    f"[{TIER_SOURCE}]\n--- Diagram OCR: {fname} ---\n{trimmed}"
                )
                log.info("IDR context: extracted %d chars from diagram %s", len(trimmed), fname)

    # 5. KG entity enrichment
    all_source_text_combined = "\n".join(source_texts + email_texts)
    kg_chunks = _expand_kg_entities(
        source_texts=[all_source_text_combined],
        config_findings=config_findings,
        diagram_findings=diagram_findings,
    )

    # 6. Assemble supplemental_notes (tier-ordered: OPERATOR first, then EMAIL, then SOURCE)
    sup_text = (supplemental_text or "").strip()
    notes_parts: list[str] = []
    if sup_text:
        notes_parts.append(f"[{TIER_OPERATOR} — AUTHORITATIVE CONTEXT]\n{sup_text}")
    notes_parts.extend(email_texts)
    notes_parts.extend(source_texts)
    if kg_chunks:
        kg_block = "\n\n".join(c["text"] for c in kg_chunks[:10])
        notes_parts.append(f"[{TIER_KG}] Knowledge Graph entity context:\n{kg_block}")

    supplemental_notes = "\n\n".join(notes_parts)

    # 7. Template sections (WriteGuard template structure)
    template_sections: list[dict[str, Any]] = []
    if template_structure:
        template_sections = template_structure.get("sections", [])

    # 8. Build the natural-language query string for DIC search + ACE
    query_string = _build_query(
        session, topology_summary, config_findings, supplemental_notes, diagram_findings,
        supplemental_text=sup_text,
    )

    # 9. Evidence blocks — placeholders; DIC search happens in workflow.py
    evidence_blocks: list[dict[str, Any]] = []

    # Load domain profile for ace_roles
    try:
        from tools.docgen.domain_profiles import get_ace_roles
        ace_roles = get_ace_roles(domain)
    except Exception:
        ace_roles = ["technical_writer"]

    context: dict[str, Any] = {
        "session_id": session["id"],
        "domain": domain,
        "doc_type": doc_type,
        "title": session.get("title", "Untitled Document"),
        "classification": session.get("classification", "CUI"),
        "ace_roles": ace_roles,
        "evidence_blocks": evidence_blocks,
        "topology_summary": topology_summary,
        "diagram_findings": diagram_findings,
        "config_findings": config_findings,
        "template_sections": template_sections,
        "supplemental_notes": supplemental_notes,
        "supplemental_text": sup_text,
        "kg_chunks": kg_chunks,
        "query_string": query_string,
        "upload_count": len(uploads or []),
        "analysis_count": len(analyses or []),
    }

    log.info(
        "IDR context built: session=%s domain=%s nodes=%d config_findings=%d "
        "kg_chunks=%d ace_roles=%s",
        session["id"], domain,
        topology_summary.get("node_count", 0),
        len(config_findings),
        len(kg_chunks),
        ace_roles,
    )
    return context


def _count_by_type(nodes: list[dict], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for n in nodes:
        val = str(n.get(key, "unknown"))
        counts[val] = counts.get(val, 0) + 1
    return counts


def _flatten_findings(review_result: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten a config_review result into a list of finding dicts."""
    findings = []
    for section in ("security_compliance", "optimization", "remediation"):
        items = review_result.get(section, [])
        if isinstance(items, list):
            for item in items:
                findings.append({"section": section, "finding": item})
        elif isinstance(items, dict):
            for k, v in items.items():
                findings.append({"section": section, "key": k, "finding": v})
    return findings


def _build_query(
    session: dict[str, Any],
    topology_summary: dict[str, Any],
    config_findings: list[dict[str, Any]],
    supplemental_notes: str = "",
    diagram_findings: list[dict[str, Any]] | None = None,
    supplemental_text: str = "",
) -> str:
    parts = []

    # OPERATOR context goes first — highest authority anchor for the LLM
    if supplemental_text:
        parts.append(
            f"[{TIER_OPERATOR} — AUTHORITATIVE CONTEXT]\n{supplemental_text[:4000]}"
        )

    parts.append(
        f"Generate a {session.get('doc_type', 'runbook')} for a {session.get('domain', 'network')} environment.",
    )
    if topology_summary.get("node_count"):
        node_types = topology_summary.get("node_types", {})
        type_str = ", ".join(f"{v} {k}" for k, v in sorted(node_types.items(), key=lambda x: -x[1])[:5])
        parts.append(f"The network consists of {topology_summary['node_count']} devices ({type_str}).")
    if topology_summary.get("stitched_hosts"):
        parts.append(
            f"Cross-team shared infrastructure: {', '.join(topology_summary['stitched_hosts'][:5])}."
        )
    if config_findings:
        cat1 = [f for f in config_findings if "cat1" in str(f).lower() or "critical" in str(f).lower()]
        high = [f for f in config_findings if "high" in str(f).lower()]
        if cat1:
            parts.append(
                f"CRITICAL: {len(cat1)} CAT1/critical findings from config/IaC review require immediate remediation."
            )
        elif high:
            parts.append(
                f"There are {len(high)} high-severity findings from config/IaC review that must be addressed."
            )
        elif config_findings:
            parts.append(f"Config/IaC review identified {len(config_findings)} findings to incorporate.")

        # Inline security/compliance findings (top-5) — severity-ranked
        sec_findings = [f for f in config_findings if f.get("section") == "security_compliance"]
        for f in sec_findings[:5]:
            finding = f.get("finding") or f
            if isinstance(finding, dict):
                title = finding.get("title", "")
                detail = finding.get("detail", "")
                sev = finding.get("severity", "")
                if title:
                    parts.append(f"Finding ({sev}): {title} — {detail[:200]}")

        # Inline optimization recommendations (top-3)
        opt_findings = [f for f in config_findings if f.get("section") == "optimization"]
        if opt_findings:
            opt_parts = []
            for f in opt_findings[:3]:
                finding = f.get("finding") or f
                if isinstance(finding, dict):
                    title = finding.get("title", "")
                    rec = finding.get("recommendation", finding.get("detail", ""))
                    if title:
                        opt_parts.append(f"{title}: {rec[:150]}")
            if opt_parts:
                parts.append("Optimization recommendations: " + "; ".join(opt_parts) + ".")

        # Inline remediation steps (top-3)
        rem_findings = [f for f in config_findings if f.get("section") == "remediation"]
        if rem_findings:
            rem_titles = []
            for f in rem_findings[:3]:
                finding = f.get("finding") or f
                if isinstance(finding, dict):
                    title = finding.get("title", "")
                    if title:
                        rem_titles.append(title)
            if rem_titles:
                parts.append("Required remediations: " + "; ".join(rem_titles) + ".")

    if diagram_findings:
        diag_sec_items: list[str] = []
        diag_comp_items: list[str] = []
        diag_rem_items: list[str] = []
        for df in diagram_findings:
            for item in (df.get("security_items") or []):
                t = item.get("title") or ""
                tab = item.get("tab", "security")
                if t:
                    if tab == "security":
                        diag_sec_items.append(t)
                    elif tab == "compliance":
                        diag_comp_items.append(t)
                    elif tab == "remediate":
                        diag_rem_items.append(t)
        if diag_sec_items:
            parts.append(
                f"Diagram analysis — security items ({len(diag_sec_items)}): "
                + "; ".join(diag_sec_items[:5]) + "."
            )
        if diag_comp_items:
            parts.append(
                f"Diagram compliance gaps ({len(diag_comp_items)}): "
                + "; ".join(diag_comp_items[:3]) + "."
            )
        if diag_rem_items:
            parts.append(
                f"Diagram-identified remediations: " + "; ".join(diag_rem_items[:3]) + "."
            )
    parts.append("Include operational procedures, risk mitigations, and remediation steps. Cite all facts.")
    if supplemental_notes:
        parts.append(f"\n\nSource document content:\n{supplemental_notes[:_MAX_SOURCE_CHARS]}")
    return " ".join(parts)
