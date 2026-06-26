# CUI // SP-CTI
"""IDR Context Builder — assembles unified context for Stage 4 / 5 generation.

Pulls together:
  - RAG+KG evidence from DIC collection (grounded search)
  - Merged NDC 6-tab analysis findings (from all diagram analyses)
  - Config / IaC review findings
  - Supplemental uploads (email snippets, meeting notes)
  - WriteGuard document template structure (section headings, required content)
  - ACE coworker team specification

The assembled context dict is passed into ACEController.launch() and then into
doc_generator.generate_document() as supplemental evidence.
"""
from __future__ import annotations

import pathlib
from typing import Any

from tools.logging.icdev_logger import get_logger

log = get_logger(__name__)

_MAX_SOURCE_CHARS = 40_000  # ~10k tokens — cap per file to avoid context explosion


def _extract_text_from_file(file_path: str) -> str:
    """Best-effort text extraction from PDF, DOCX, or plain text files."""
    p = pathlib.Path(file_path)
    if not p.is_file():
        return ""
    suffix = p.suffix.lower()
    try:
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
        # Plain text / markdown / yaml / json
        return p.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        log.warning("IDR text extraction failed for %s: %s", file_path, exc)
        return ""


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
        template_sections, supplemental_notes, query_string.
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

    # 2. Config / IaC review findings summary
    config_findings: list[dict[str, Any]] = []
    if config_review_results:
        for cr in config_review_results:
            if isinstance(cr, dict):
                config_findings.extend(_flatten_findings(cr))

    # 3. Diagram analysis findings — pull from analyses meta
    diagram_findings: list[dict[str, Any]] = []
    for analysis in (analyses or []):
        if analysis.get("analysis_type") == "diagram_analysis":
            diagram_findings.append({
                "upload_id": analysis.get("upload_id"),
                "result_ref_id": analysis.get("result_ref_id"),
                "status": analysis.get("status"),
            })

    # 4. Extract text from doc/supplement uploads and fold into supplemental notes
    source_texts: list[str] = []
    for up in (uploads or []):
        if up.get("upload_type") in ("doc", "supplement") and up.get("file_path"):
            raw = _extract_text_from_file(up["file_path"])
            if raw.strip():
                trimmed = raw[:_MAX_SOURCE_CHARS]
                source_texts.append(
                    f"--- Source: {up.get('filename', up['file_path'])} ---\n{trimmed}"
                )
                log.info(
                    "IDR context: extracted %d chars from %s",
                    len(trimmed), up.get("filename"),
                )
    source_block = "\n\n".join(source_texts)
    supplemental_notes = "\n\n".join(
        filter(None, [source_block, (supplemental_text or "").strip()])
    )

    # 5. Template sections (WriteGuard template structure)
    template_sections: list[dict[str, Any]] = []
    if template_structure:
        template_sections = template_structure.get("sections", [])

    # 6. Build the natural-language query string for DIC search + ACE
    query_string = _build_query(session, topology_summary, config_findings, supplemental_notes)

    # 7. Evidence blocks — placeholders; DIC search happens in workflow.py
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
        "query_string": query_string,
        "upload_count": len(uploads or []),
        "analysis_count": len(analyses or []),
    }

    log.info(
        "IDR context built: session=%s domain=%s nodes=%d config_findings=%d ace_roles=%s",
        session["id"], domain,
        topology_summary.get("node_count", 0),
        len(config_findings),
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
) -> str:
    parts = [
        f"Generate a {session.get('doc_type', 'runbook')} for a {session.get('domain', 'network')} environment.",
    ]
    if topology_summary.get("node_count"):
        node_types = topology_summary.get("node_types", {})
        type_str = ", ".join(f"{v} {k}" for k, v in sorted(node_types.items(), key=lambda x: -x[1])[:5])
        parts.append(f"The network consists of {topology_summary['node_count']} devices ({type_str}).")
    if topology_summary.get("stitched_hosts"):
        parts.append(
            f"Cross-team shared infrastructure: {', '.join(topology_summary['stitched_hosts'][:5])}."
        )
    if config_findings:
        high_sev = [f for f in config_findings if "critical" in str(f).lower() or "high" in str(f).lower()]
        if high_sev:
            parts.append(f"There are {len(high_sev)} high/critical findings from config review that must be addressed.")
    parts.append("Include operational procedures, risk mitigations, and remediation steps. Cite all facts.")
    if supplemental_notes:
        parts.append(f"\n\nSource document content:\n{supplemental_notes[:_MAX_SOURCE_CHARS]}")
    return " ".join(parts)
