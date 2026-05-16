#!/usr/bin/env python3
# CUI // SP-PROPIN
# Controlled by: GovProposal Portal
# CUI Category: PROPIN
# Distribution: D
# POC: GovProposal System Administrator
"""RAG-based content generation for proposal sections.

Drafts proposal sections by retrieving relevant content from the knowledge base
and past performance library, then combining it with win themes, evaluation
criteria, and compliance requirements to generate responsive proposal text.

Falls back to template-based generation when no LLM provider is available.

Usage:
    python tools/proposal/content_drafter.py --draft --proposal-id "prop-123" --section-id "sec-1" --json
    python tools/proposal/content_drafter.py --draft-volume --proposal-id "prop-123" --volume technical --json
    python tools/proposal/content_drafter.py --exec-summary --proposal-id "prop-123" --json
    python tools/proposal/content_drafter.py --refresh --proposal-id "prop-123" --section-id "sec-1" --json
    python tools/proposal/content_drafter.py --status --proposal-id "prop-123" --json
"""

import argparse
import json
import os
import re
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = Path(os.environ.get(
    "GOVPROPOSAL_DB_PATH", str(BASE_DIR / "data" / "govproposal.db")
))

# ---------------------------------------------------------------------------
# Optional imports — degrade gracefully
# ---------------------------------------------------------------------------
try:
    import yaml  # noqa: F401
except ImportError:  # pragma: no cover
    yaml = None

try:
    import openai as openai_mod  # type: ignore
except ImportError:  # pragma: no cover
    openai_mod = None

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_db(db_path=None):
    """Return an SQLite connection with WAL + FK enabled."""
    path = str(db_path or DB_PATH)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


def _now():
    """UTC ISO-8601 timestamp."""
    return datetime.now(timezone.utc).isoformat()


def _uid():
    """Short UUID for primary keys."""
    return str(uuid.uuid4())[:12]


def _audit(conn, event_type, action, entity_type=None, entity_id=None, details=None):
    """Append-only audit trail entry."""
    conn.execute(
        "INSERT INTO audit_trail (event_type, actor, action, entity_type, entity_id, details, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (event_type, "content_drafter", action, entity_type, entity_id, details, _now()),
    )


# ---------------------------------------------------------------------------
# KB search (lightweight — searches kb_entries + past_performances via SQL)
# ---------------------------------------------------------------------------

def _search_kb(conn, query, entry_types=None, limit=5):
    """Search knowledge base entries by keyword matching.

    Args:
        conn: SQLite connection.
        query: Search query text.
        entry_types: Optional list of entry_type values to filter by.
        limit: Max results.

    Returns:
        list of kb_entry dicts.
    """
    words = re.findall(r"[a-zA-Z]{3,}", query.lower())
    if not words:
        return []

    # Build LIKE clauses for keyword matching across title, content, tags, keywords
    conditions = []
    params = []
    for word in words[:8]:  # Cap at 8 keywords
        conditions.append(
            "(LOWER(title) LIKE ? OR LOWER(content) LIKE ? OR LOWER(tags) LIKE ? OR LOWER(keywords) LIKE ?)"
        )
        pattern = f"%{word}%"
        params.extend([pattern, pattern, pattern, pattern])

    where = " OR ".join(conditions)

    type_filter = ""
    if entry_types:
        placeholders = ",".join("?" * len(entry_types))
        type_filter = f" AND entry_type IN ({placeholders})"
        params.extend(entry_types)

    sql = (
        f"SELECT id, entry_type, title, content, tags, keywords, quality_score, win_rate "
        f"FROM kb_entries WHERE is_active = 1 AND ({where}){type_filter} "
        f"ORDER BY quality_score DESC NULLS LAST, usage_count DESC "
        f"LIMIT ?"
    )
    params.append(limit)

    rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def _search_past_performances(conn, query, limit=3):
    """Search past performance entries by keyword matching.

    Args:
        conn: SQLite connection.
        query: Search query text.
        limit: Max results.

    Returns:
        list of past_performance dicts.
    """
    words = re.findall(r"[a-zA-Z]{3,}", query.lower())
    if not words:
        return []

    conditions = []
    params = []
    for word in words[:6]:
        conditions.append(
            "(LOWER(contract_name) LIKE ? OR LOWER(scope_description) LIKE ? "
            "OR LOWER(technical_approach) LIKE ? OR LOWER(key_accomplishments) LIKE ? "
            "OR LOWER(relevance_tags) LIKE ?)"
        )
        pattern = f"%{word}%"
        params.extend([pattern] * 5)

    where = " OR ".join(conditions)
    sql = (
        f"SELECT id, contract_name, contract_number, agency, contract_value, "
        f"role, scope_description, technical_approach, key_accomplishments, "
        f"metrics_achieved, cpars_rating "
        f"FROM past_performances WHERE is_active = 1 AND ({where}) "
        f"ORDER BY cpars_rating ASC, contract_value DESC NULLS LAST "
        f"LIMIT ?"
    )
    params.append(limit)

    rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def _get_win_themes(conn, proposal_id):
    """Retrieve win themes for a proposal (or its opportunity)."""
    rows = conn.execute(
        "SELECT id, theme_text, supporting_evidence, discriminator_type, strength_rating "
        "FROM win_themes WHERE proposal_id = ? ORDER BY strength_rating DESC NULLS LAST",
        (proposal_id,),
    ).fetchall()
    if not rows:
        # Try via opportunity_id
        opp_row = conn.execute(
            "SELECT opportunity_id FROM proposals WHERE id = ?", (proposal_id,),
        ).fetchone()
        if opp_row:
            rows = conn.execute(
                "SELECT id, theme_text, supporting_evidence, discriminator_type, strength_rating "
                "FROM win_themes WHERE opportunity_id = ? ORDER BY strength_rating DESC NULLS LAST",
                (opp_row["opportunity_id"],),
            ).fetchall()
    return [dict(r) for r in rows]


def _get_eval_criteria(conn, proposal_id):
    """Retrieve Section M evaluation criteria from the compliance matrix."""
    rows = conn.execute(
        "SELECT requirement_id, requirement_text "
        "FROM compliance_matrices "
        "WHERE proposal_id = ? AND source = 'section_m' "
        "ORDER BY requirement_id",
        (proposal_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def _get_section_requirements(conn, proposal_id, section_id=None, section_number=None):
    """Get compliance matrix requirements mapped to a specific section."""
    if section_id:
        sec_row = conn.execute(
            "SELECT section_number, section_title, volume, page_limit "
            "FROM proposal_sections WHERE id = ?",
            (section_id,),
        ).fetchone()
        if sec_row:
            section_number = sec_row["section_number"]

    if not section_number:
        return [], None

    reqs = conn.execute(
        "SELECT requirement_id, requirement_text, source "
        "FROM compliance_matrices "
        "WHERE proposal_id = ? AND section_number = ?",
        (proposal_id, section_number),
    ).fetchall()

    sec_info = None
    if section_id:
        sec_row = conn.execute(
            "SELECT id, section_number, section_title, volume, page_limit, content, status "
            "FROM proposal_sections WHERE id = ?",
            (section_id,),
        ).fetchone()
        sec_info = dict(sec_row) if sec_row else None

    return [dict(r) for r in reqs], sec_info


# ---------------------------------------------------------------------------
# LLM invocation
# ---------------------------------------------------------------------------

def _llm_available():
    """Check if an LLM provider is configured and available."""
    if openai_mod is None:
        return False
    api_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("GOVPROPOSAL_LLM_KEY")
    return bool(api_key)


def _call_llm(system_prompt, user_prompt, max_tokens=2000):
    """Call an OpenAI-compatible LLM.  Returns generated text or None on failure."""
    if not _llm_available():
        return None

    api_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("GOVPROPOSAL_LLM_KEY", "")
    base_url = os.environ.get("GOVPROPOSAL_LLM_BASE_URL") or os.environ.get("OPENAI_BASE_URL")
    model = os.environ.get("GOVPROPOSAL_LLM_MODEL", "gpt-4o-mini")

    try:
        client_kwargs = {"api_key": api_key}
        if base_url:
            client_kwargs["base_url"] = base_url
        client = openai_mod.OpenAI(**client_kwargs)
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=max_tokens,
            temperature=0.4,
        )
        return response.choices[0].message.content
    except Exception as e:
        # Log but don't crash — fall back to template
        print(f"LLM call failed ({e}), falling back to template.", file=sys.stderr)
        return None


# ---------------------------------------------------------------------------
# Template-based fallback drafting
# ---------------------------------------------------------------------------

_TEMPLATES = {
    "technical": (
        "{company} proposes to address the requirement for {requirement_summary} using our proven "
        "capabilities in {capability_area}. Our approach leverages {methodology} to deliver "
        "{outcome}.\n\n"
        "{kb_content}\n\n"
        "This approach directly addresses the evaluation criteria by {eval_response}.\n\n"
        "{win_theme_text}"
    ),
    "past_performance": (
        "Under the {contract_name} contract with {agency}, {company} demonstrated relevant "
        "experience in {scope}. Key accomplishments include {accomplishments}.\n\n"
        "The contract, valued at {value}, was performed in the role of {role}. "
        "{cpars_text}\n\n"
        "This experience is directly relevant to the current requirement because {relevance}."
    ),
    "management": (
        "{company}'s management approach for this program follows {methodology} with a "
        "{team_structure} organizational structure.\n\n"
        "Key elements of our management approach include:\n"
        "- Program oversight and governance\n"
        "- Risk management and mitigation\n"
        "- Quality assurance and control\n"
        "- Communication and reporting\n"
        "- Transition planning\n\n"
        "{kb_content}\n\n"
        "{win_theme_text}"
    ),
    "cost": (
        "{company} has developed a competitive and realistic cost proposal that reflects our "
        "understanding of the requirements and our efficient approach to delivery.\n\n"
        "Our pricing is informed by relevant past performance on similar contracts and "
        "current market rates for the required labor categories.\n\n"
        "{win_theme_text}"
    ),
    "executive_summary": (
        "EXECUTIVE SUMMARY\n\n"
        "{company} is pleased to submit this proposal in response to {solicitation}. "
        "We offer a comprehensive solution that addresses all stated requirements with a "
        "proven approach backed by relevant past performance.\n\n"
        "Key Discriminators:\n{win_themes_list}\n\n"
        "Our approach delivers the following benefits:\n{benefits}\n\n"
        "With our team of qualified professionals and demonstrated track record, "
        "{company} is uniquely positioned to deliver exceptional results for this program."
    ),
    "default": (
        "{company} addresses the requirement for {requirement_summary} through our "
        "established capabilities.\n\n"
        "{kb_content}\n\n"
        "{win_theme_text}"
    ),
}


def _template_draft(volume, section_info, requirements, kb_results, pp_results,
                    win_themes, eval_criteria, company_name=None):
    """Generate draft content using templates when LLM is unavailable."""
    company = company_name or os.environ.get("GOVPROPOSAL_COMPANY_NAME", "[Company]")
    template_key = volume if volume in _TEMPLATES else "default"
    template = _TEMPLATES[template_key]

    # Build requirement summary
    req_summary = "the stated requirements"
    if requirements:
        req_texts = [r.get("requirement_text", "")[:80] for r in requirements[:3]]
        req_summary = "; ".join(req_texts)

    # Build KB content summary
    kb_content = ""
    capability_area = "relevant technical domains"
    methodology = "industry best practices"
    if kb_results:
        kb_parts = []
        for kb in kb_results[:3]:
            title = kb.get("title", "")
            content_snippet = (kb.get("content", "") or "")[:300]
            kb_parts.append(f"{title}: {content_snippet}")
            if kb.get("entry_type") == "capability":
                capability_area = title
            elif kb.get("entry_type") == "methodology":
                methodology = title
        kb_content = "\n\n".join(kb_parts)

    # Build past performance content
    if pp_results and template_key == "past_performance":
        pp = pp_results[0]
        cpars_text = ""
        if pp.get("cpars_rating"):
            cpars_text = f"Our CPARS rating for this contract was '{pp['cpars_rating']}'."
        return template.format(
            company=company,
            contract_name=pp.get("contract_name", "[Contract]"),
            agency=pp.get("agency", "[Agency]"),
            scope=pp.get("scope_description", "[scope]")[:200],
            accomplishments=pp.get("key_accomplishments", "[accomplishments]")[:200],
            value=f"${pp.get('contract_value', 0):,.0f}" if pp.get("contract_value") else "[value]",
            role=pp.get("role", "prime"),
            cpars_text=cpars_text,
            relevance="it demonstrates our ability to deliver similar solutions in a comparable environment",
        )

    # Build win theme text
    win_theme_text = ""
    win_themes_list = ""
    if win_themes:
        theme_bullets = []
        for wt in win_themes[:5]:
            theme_bullets.append(f"- {wt.get('theme_text', '')}")
        win_themes_list = "\n".join(theme_bullets)
        win_theme_text = f"Our key discriminators include:\n{win_themes_list}"

    # Build eval response
    eval_response = "demonstrating a clear, compliant, and well-structured approach"
    if eval_criteria:
        criteria_text = eval_criteria[0].get("requirement_text", "")[:80]
        eval_response = f"directly addressing the evaluation criterion: {criteria_text}"

    # Build benefits for exec summary
    benefits = "- Reduced risk through proven methodologies\n- Cost efficiency through established processes\n- High-quality deliverables backed by past performance"

    section_title = (section_info or {}).get("section_title", "")
    solicitation = section_title if section_title else "this solicitation"

    try:
        return template.format(
            company=company,
            requirement_summary=req_summary[:200],
            capability_area=capability_area,
            methodology=methodology,
            outcome="high-quality, compliant deliverables on schedule and within budget",
            kb_content=kb_content if kb_content else "[Knowledge base content will be inserted here]",
            eval_response=eval_response,
            win_theme_text=win_theme_text,
            win_themes_list=win_themes_list if win_themes_list else "- [Win themes to be developed]",
            benefits=benefits,
            team_structure="matrixed",
            solicitation=solicitation,
        )
    except KeyError:
        # Fallback if template has unexpected placeholders
        return template.replace("{", "{{").replace("}", "}}")


# ---------------------------------------------------------------------------
# LLM-based drafting
# ---------------------------------------------------------------------------

def _llm_draft(volume, section_info, requirements, kb_results, pp_results,
               win_themes, eval_criteria, feedback=None, company_name=None):
    """Generate draft content using an LLM with RAG context."""
    company = company_name or os.environ.get("GOVPROPOSAL_COMPANY_NAME", "[Company]")

    system_prompt = (
        "You are a senior government proposal writer. You write clear, compliant, "
        "persuasive proposal content that directly addresses evaluation criteria. "
        "Use active voice, specific details, and quantified metrics when available. "
        "Always address each requirement explicitly. "
        f"Write on behalf of {company}. "
        "Do not include classification markings or headers — those are added separately. "
        "Format with professional proposal structure: topic sentences, supporting evidence, "
        "and benefit statements."
    )

    # Build context sections
    context_parts = []

    # Requirements
    if requirements:
        req_text = "\n".join(
            f"- [{r.get('requirement_id', '?')}] {r.get('requirement_text', '')[:200]}"
            for r in requirements
        )
        context_parts.append(f"REQUIREMENTS TO ADDRESS:\n{req_text}")

    # Section info
    if section_info:
        sec_text = (
            f"SECTION: {section_info.get('section_number', '')} — {section_info.get('section_title', '')}\n"
            f"Volume: {section_info.get('volume', '')}\n"
        )
        if section_info.get("page_limit"):
            sec_text += f"Page limit: {section_info['page_limit']} pages\n"
        context_parts.append(sec_text)

    # KB content (RAG retrieval)
    if kb_results:
        kb_text = "\n---\n".join(
            f"[{kb.get('entry_type', 'kb')}] {kb.get('title', '')}\n{(kb.get('content', '') or '')[:500]}"
            for kb in kb_results[:5]
        )
        context_parts.append(f"RELEVANT KNOWLEDGE BASE CONTENT:\n{kb_text}")

    # Past performance
    if pp_results:
        pp_text = "\n---\n".join(
            f"Contract: {pp.get('contract_name', '')} ({pp.get('agency', '')})\n"
            f"Scope: {(pp.get('scope_description', '') or '')[:300]}\n"
            f"Accomplishments: {(pp.get('key_accomplishments', '') or '')[:200]}\n"
            f"CPARS: {pp.get('cpars_rating', 'N/A')}"
            for pp in pp_results[:3]
        )
        context_parts.append(f"RELEVANT PAST PERFORMANCE:\n{pp_text}")

    # Win themes
    if win_themes:
        wt_text = "\n".join(f"- {wt.get('theme_text', '')}" for wt in win_themes[:5])
        context_parts.append(f"WIN THEMES TO WEAVE IN:\n{wt_text}")

    # Evaluation criteria
    if eval_criteria:
        ec_text = "\n".join(
            f"- [{ec.get('requirement_id', '?')}] {ec.get('requirement_text', '')[:150]}"
            for ec in eval_criteria[:5]
        )
        context_parts.append(f"EVALUATION CRITERIA (from Section M):\n{ec_text}")

    # Feedback for refresh
    if feedback:
        context_parts.append(f"REVIEWER FEEDBACK TO INCORPORATE:\n{feedback}")

    user_prompt = (
        f"Draft the {volume or 'proposal'} section content for the following:\n\n"
        + "\n\n".join(context_parts)
        + "\n\nGenerate professional, compliant proposal content that addresses all listed requirements."
    )

    return _call_llm(system_prompt, user_prompt, max_tokens=3000)


# ---------------------------------------------------------------------------
# Core drafting functions
# ---------------------------------------------------------------------------

def draft_section(proposal_id, section_id=None, section_number=None, db_path=None):
    """Draft a proposal section using RAG.

    Steps:
      1. Load section outline/requirements from compliance matrix
      2. Search KB for relevant content
      3. Search past_performances for relevant PP narratives
      4. Build context from retrieved content + win themes + evaluation criteria
      5. Generate draft via LLM (or template fallback)
      6. Store draft in proposal_sections.content
      7. Update section status to 'drafted'

    Returns:
        dict with drafted content and metadata.
    """
    conn = _get_db(db_path)
    try:
        # Resolve section
        if section_id:
            sec_row = conn.execute(
                "SELECT id, proposal_id, section_number, section_title, volume, page_limit, content, status "
                "FROM proposal_sections WHERE id = ? AND proposal_id = ?",
                (section_id, proposal_id),
            ).fetchone()
        elif section_number:
            sec_row = conn.execute(
                "SELECT id, proposal_id, section_number, section_title, volume, page_limit, content, status "
                "FROM proposal_sections WHERE proposal_id = ? AND section_number = ?",
                (proposal_id, section_number),
            ).fetchone()
        else:
            return {"error": "Either --section-id or --section-number is required"}

        if not sec_row:
            return {"error": f"Section not found (proposal={proposal_id}, section_id={section_id}, section_number={section_number})"}

        section_info = dict(sec_row)
        section_id = section_info["id"]
        volume = section_info["volume"]

        # 1. Load requirements from compliance matrix
        requirements, _ = _get_section_requirements(conn, proposal_id, section_id=section_id)

        # Build search query from section title + requirements
        search_query = f"{section_info.get('section_title', '')} "
        for req in requirements[:5]:
            search_query += f"{req.get('requirement_text', '')[:100]} "

        # 2. Search KB
        kb_types_by_volume = {
            "technical": ["capability", "solution_architecture", "methodology", "tool_technology"],
            "management": ["management_approach", "methodology", "corporate_overview"],
            "past_performance": ["case_study"],
            "cost": ["corporate_overview"],
            "executive_summary": ["corporate_overview", "win_theme", "capability"],
        }
        kb_types = kb_types_by_volume.get(volume)
        kb_results = _search_kb(conn, search_query, entry_types=kb_types, limit=5)

        # 3. Search past performances
        pp_results = _search_past_performances(conn, search_query, limit=3)

        # 4. Get win themes and eval criteria
        win_themes = _get_win_themes(conn, proposal_id)
        eval_criteria = _get_eval_criteria(conn, proposal_id)

        # 5. Generate draft (LLM or template)
        company_name = os.environ.get("GOVPROPOSAL_COMPANY_NAME")
        content = None
        method = "template"

        if _llm_available():
            content = _llm_draft(
                volume, section_info, requirements, kb_results,
                pp_results, win_themes, eval_criteria,
                company_name=company_name,
            )
            if content:
                method = "llm"

        if not content:
            content = _template_draft(
                volume, section_info, requirements, kb_results,
                pp_results, win_themes, eval_criteria,
                company_name=company_name,
            )

        # 6. Store draft
        word_count = len(content.split()) if content else 0
        # Rough page estimate: ~250 words per page
        page_count = round(word_count / 250, 1) if word_count > 0 else 0.0

        # Track KB sources used
        kb_source_ids = [kb["id"] for kb in kb_results]
        pp_source_ids = [pp["id"] for pp in pp_results]
        sources = json.dumps({"kb": kb_source_ids, "past_performance": pp_source_ids})

        conn.execute(
            "UPDATE proposal_sections SET content = ?, word_count = ?, page_count = ?, "
            "kb_sources = ?, status = 'drafted', updated_at = ? "
            "WHERE id = ?",
            (content, word_count, page_count, sources, _now(), section_id),
        )

        # Update KB usage counts
        for kb_id in kb_source_ids:
            conn.execute(
                "UPDATE kb_entries SET usage_count = usage_count + 1, last_used_at = ?, "
                "last_used_in = ? WHERE id = ?",
                (_now(), proposal_id, kb_id),
            )

        # 7. Update compliance matrix entries as partially_addressed
        if requirements:
            for req in requirements:
                conn.execute(
                    "UPDATE compliance_matrices SET compliance_status = 'partially_addressed' "
                    "WHERE proposal_id = ? AND requirement_id = ? AND compliance_status = 'not_addressed'",
                    (proposal_id, req.get("requirement_id")),
                )

        _audit(conn, "proposal.section_drafted",
               f"Drafted section {section_info['section_number']} via {method}",
               "proposal_section", section_id,
               json.dumps({"method": method, "word_count": word_count,
                            "kb_sources": len(kb_source_ids),
                            "pp_sources": len(pp_source_ids)}))
        conn.commit()

        return {
            "proposal_id": proposal_id,
            "section_id": section_id,
            "section_number": section_info["section_number"],
            "section_title": section_info["section_title"],
            "volume": volume,
            "content": content,
            "word_count": word_count,
            "page_count": page_count,
            "method": method,
            "kb_sources_used": len(kb_source_ids),
            "pp_sources_used": len(pp_source_ids),
            "status": "drafted",
            "drafted_at": _now(),
        }
    finally:
        conn.close()


def draft_volume(proposal_id, volume, db_path=None):
    """Draft all sections within a given volume.

    Args:
        proposal_id: The proposal ID.
        volume: Volume name (technical, management, past_performance, cost, etc.)

    Returns:
        dict with results for each section.
    """
    conn = _get_db(db_path)
    try:
        rows = conn.execute(
            "SELECT id, section_number, section_title, status "
            "FROM proposal_sections "
            "WHERE proposal_id = ? AND volume = ? "
            "ORDER BY section_number",
            (proposal_id, volume),
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        return {
            "error": f"No sections found for volume '{volume}' in proposal '{proposal_id}'"
        }

    results = []
    drafted_count = 0
    skipped_count = 0
    error_count = 0

    for row in rows:
        section_id = row["id"]
        # Skip already-final/locked sections
        if row["status"] in ("final", "locked"):
            results.append({
                "section_id": section_id,
                "section_number": row["section_number"],
                "status": "skipped",
                "reason": f"Section is {row['status']}",
            })
            skipped_count += 1
            continue

        result = draft_section(proposal_id, section_id=section_id, db_path=db_path)
        if "error" in result:
            results.append({
                "section_id": section_id,
                "section_number": row["section_number"],
                "status": "error",
                "error": result["error"],
            })
            error_count += 1
        else:
            results.append({
                "section_id": section_id,
                "section_number": result.get("section_number"),
                "section_title": result.get("section_title"),
                "status": "drafted",
                "word_count": result.get("word_count", 0),
                "method": result.get("method"),
            })
            drafted_count += 1

    return {
        "proposal_id": proposal_id,
        "volume": volume,
        "total_sections": len(rows),
        "drafted": drafted_count,
        "skipped": skipped_count,
        "errors": error_count,
        "sections": results,
        "drafted_at": _now(),
    }


def draft_executive_summary(proposal_id, db_path=None):
    """Generate an executive summary from all volume content and win themes.

    Creates or updates the executive_summary section.

    Returns:
        dict with executive summary content.
    """
    conn = _get_db(db_path)
    try:
        # Load proposal info
        prop_row = conn.execute(
            "SELECT id, title, opportunity_id, win_themes, volumes FROM proposals WHERE id = ?",
            (proposal_id,),
        ).fetchone()
        if not prop_row:
            return {"error": f"Proposal '{proposal_id}' not found"}

        # Load all drafted section content
        section_rows = conn.execute(
            "SELECT volume, section_number, section_title, content, word_count "
            "FROM proposal_sections WHERE proposal_id = ? AND content IS NOT NULL "
            "ORDER BY volume, section_number",
            (proposal_id,),
        ).fetchall()

        # Build volume summaries
        volume_summaries = {}
        for sr in section_rows:
            vol = sr["volume"]
            if vol not in volume_summaries:
                volume_summaries[vol] = []
            content = (sr["content"] or "")[:500]
            volume_summaries[vol].append(f"{sr['section_title']}: {content}")

        # Get win themes
        win_themes = _get_win_themes(conn, proposal_id)
        eval_criteria = _get_eval_criteria(conn, proposal_id)

        # Get opportunity info
        opp_title = ""
        if prop_row["opportunity_id"]:
            opp_row = conn.execute(
                "SELECT title, solicitation_number, agency FROM opportunities WHERE id = ?",
                (prop_row["opportunity_id"],),
            ).fetchone()
            if opp_row:
                opp_title = f"{opp_row['title']} ({opp_row.get('solicitation_number', '')})"

        # Generate exec summary
        company_name = os.environ.get("GOVPROPOSAL_COMPANY_NAME", "[Company]")
        content = None
        method = "template"

        if _llm_available():
            system_prompt = (
                "You are a senior government proposal writer specializing in executive summaries. "
                "Write a compelling, concise executive summary that highlights key discriminators, "
                "summarizes the technical and management approach, references past performance, "
                f"and makes a clear case for why {company_name} should be selected. "
                "Use active voice and quantified results."
            )

            vol_text = ""
            for vol_name, summaries in volume_summaries.items():
                vol_text += f"\n\n{vol_name.upper()} VOLUME HIGHLIGHTS:\n"
                vol_text += "\n".join(summaries[:5])

            wt_text = "\n".join(f"- {wt.get('theme_text', '')}" for wt in win_themes[:5])

            user_prompt = (
                f"Write an executive summary for the proposal: {prop_row['title']}\n"
                f"Solicitation: {opp_title}\n\n"
                f"WIN THEMES:\n{wt_text}\n\n"
                f"VOLUME CONTENT SUMMARIES:{vol_text}\n\n"
                "Generate a professional executive summary of 400-600 words."
            )

            content = _call_llm(system_prompt, user_prompt, max_tokens=2000)
            if content:
                method = "llm"

        if not content:
            # Template fallback
            win_themes_list = "\n".join(
                f"- {wt.get('theme_text', '')}" for wt in win_themes[:5]
            ) or "- [Win themes to be developed]"

            benefits_parts = []
            for vol_name, summaries in volume_summaries.items():
                if summaries:
                    benefits_parts.append(f"- {vol_name.title()}: {summaries[0][:100]}")
            benefits = "\n".join(benefits_parts) or "- Comprehensive, compliant solution addressing all requirements"

            content = _TEMPLATES["executive_summary"].format(
                company=company_name,
                solicitation=opp_title or prop_row["title"],
                win_themes_list=win_themes_list,
                benefits=benefits,
            )

        # Store or update exec summary section
        word_count = len(content.split()) if content else 0
        page_count = round(word_count / 250, 1)

        existing = conn.execute(
            "SELECT id FROM proposal_sections WHERE proposal_id = ? AND volume = 'executive_summary'",
            (proposal_id,),
        ).fetchone()

        if existing:
            conn.execute(
                "UPDATE proposal_sections SET content = ?, word_count = ?, page_count = ?, "
                "status = 'drafted', updated_at = ? WHERE id = ?",
                (content, word_count, page_count, _now(), existing["id"]),
            )
            section_id = existing["id"]
        else:
            section_id = _uid()
            conn.execute(
                "INSERT INTO proposal_sections "
                "(id, proposal_id, volume, section_number, section_title, content, "
                "word_count, page_count, status, created_at, updated_at) "
                "VALUES (?, ?, 'executive_summary', '0', 'Executive Summary', ?, ?, ?, 'drafted', ?, ?)",
                (section_id, proposal_id, content, word_count, page_count, _now(), _now()),
            )

        _audit(conn, "proposal.exec_summary_drafted",
               f"Drafted executive summary via {method}",
               "proposal_section", section_id,
               json.dumps({"method": method, "word_count": word_count}))
        conn.commit()

        return {
            "proposal_id": proposal_id,
            "section_id": section_id,
            "content": content,
            "word_count": word_count,
            "page_count": page_count,
            "method": method,
            "volumes_referenced": list(volume_summaries.keys()),
            "win_themes_used": len(win_themes),
            "drafted_at": _now(),
        }
    finally:
        conn.close()


def refresh_section(proposal_id, section_id, feedback=None, db_path=None):
    """Re-draft a section with optional feedback/direction.

    Args:
        proposal_id: The proposal ID.
        section_id: The section to re-draft.
        feedback: Optional reviewer feedback to incorporate.

    Returns:
        dict with refreshed content.
    """
    conn = _get_db(db_path)
    try:
        sec_row = conn.execute(
            "SELECT id, section_number, section_title, volume, page_limit, content, status, version "
            "FROM proposal_sections WHERE id = ? AND proposal_id = ?",
            (section_id, proposal_id),
        ).fetchone()

        if not sec_row:
            return {"error": f"Section '{section_id}' not found in proposal '{proposal_id}'"}

        if sec_row["status"] in ("locked",):
            return {"error": f"Section is locked and cannot be refreshed"}

        section_info = dict(sec_row)
        volume = section_info["volume"]

        # Load requirements
        requirements, _ = _get_section_requirements(conn, proposal_id, section_id=section_id)

        # Build search query
        search_query = f"{section_info.get('section_title', '')} "
        for req in requirements[:5]:
            search_query += f"{req.get('requirement_text', '')[:100]} "

        # KB search
        kb_types_by_volume = {
            "technical": ["capability", "solution_architecture", "methodology", "tool_technology"],
            "management": ["management_approach", "methodology"],
            "past_performance": ["case_study"],
        }
        kb_types = kb_types_by_volume.get(volume)
        kb_results = _search_kb(conn, search_query, entry_types=kb_types, limit=5)
        pp_results = _search_past_performances(conn, search_query, limit=3)
        win_themes = _get_win_themes(conn, proposal_id)
        eval_criteria = _get_eval_criteria(conn, proposal_id)
    finally:
        conn.close()

    # Generate new draft
    company_name = os.environ.get("GOVPROPOSAL_COMPANY_NAME")
    content = None
    method = "template"

    if _llm_available():
        content = _llm_draft(
            volume, section_info, requirements, kb_results,
            pp_results, win_themes, eval_criteria,
            feedback=feedback, company_name=company_name,
        )
        if content:
            method = "llm"

    if not content:
        content = _template_draft(
            volume, section_info, requirements, kb_results,
            pp_results, win_themes, eval_criteria,
            company_name=company_name,
        )

    # Store refreshed content with incremented version
    word_count = len(content.split()) if content else 0
    page_count = round(word_count / 250, 1)
    new_version = (section_info.get("version") or 1) + 1

    conn = _get_db(db_path)
    try:
        conn.execute(
            "UPDATE proposal_sections SET content = ?, word_count = ?, page_count = ?, "
            "status = 'drafted', version = ?, updated_at = ? WHERE id = ?",
            (content, word_count, page_count, new_version, _now(), section_id),
        )
        _audit(conn, "proposal.section_refreshed",
               f"Refreshed section {section_info['section_number']} v{new_version}",
               "proposal_section", section_id,
               json.dumps({"method": method, "version": new_version,
                            "feedback": (feedback or "")[:200]}))
        conn.commit()
    finally:
        conn.close()

    return {
        "proposal_id": proposal_id,
        "section_id": section_id,
        "section_number": section_info["section_number"],
        "section_title": section_info["section_title"],
        "volume": volume,
        "content": content,
        "word_count": word_count,
        "page_count": page_count,
        "version": new_version,
        "method": method,
        "feedback_incorporated": bool(feedback),
        "refreshed_at": _now(),
    }


def get_draft_status(proposal_id, db_path=None):
    """Return drafting status for all sections in a proposal.

    Returns:
        dict with per-section and per-volume status summaries.
    """
    conn = _get_db(db_path)
    try:
        rows = conn.execute(
            "SELECT id, volume, section_number, section_title, word_count, "
            "page_count, page_limit, status, version, updated_at "
            "FROM proposal_sections WHERE proposal_id = ? "
            "ORDER BY volume, section_number",
            (proposal_id,),
        ).fetchall()

        if not rows:
            return {"error": f"No sections found for proposal '{proposal_id}'"}

        sections = []
        by_volume = {}
        total_words = 0
        total_pages = 0.0

        for r in rows:
            sec = dict(r)
            sections.append(sec)
            total_words += sec.get("word_count") or 0
            total_pages += sec.get("page_count") or 0.0

            vol = sec["volume"]
            if vol not in by_volume:
                by_volume[vol] = {"total": 0, "drafted": 0, "outline": 0, "reviewed": 0,
                                  "final": 0, "locked": 0, "word_count": 0}
            by_volume[vol]["total"] += 1
            status_key = sec["status"] if sec["status"] in by_volume[vol] else "outline"
            by_volume[vol][status_key] = by_volume[vol].get(status_key, 0) + 1
            by_volume[vol]["word_count"] += sec.get("word_count") or 0

        # Overall completion
        total_sections = len(sections)
        drafted_or_beyond = sum(1 for s in sections if s["status"] not in ("outline", "drafting"))
        completion = round(drafted_or_beyond / total_sections * 100, 1) if total_sections > 0 else 0.0

        return {
            "proposal_id": proposal_id,
            "total_sections": total_sections,
            "completion_percent": completion,
            "total_word_count": total_words,
            "total_page_count": round(total_pages, 1),
            "by_volume": by_volume,
            "sections": sections,
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="RAG-based content generation for proposal sections."
    )
    parser.add_argument("--draft", action="store_true", help="Draft a single proposal section")
    parser.add_argument("--draft-volume", action="store_true", help="Draft all sections in a volume")
    parser.add_argument("--exec-summary", action="store_true", help="Generate executive summary")
    parser.add_argument("--refresh", action="store_true", help="Re-draft a section with optional feedback")
    parser.add_argument("--status", action="store_true", help="Get draft status for all sections")
    parser.add_argument("--proposal-id", help="Proposal ID")
    parser.add_argument("--section-id", help="Section ID")
    parser.add_argument("--section-number", help="Section number (alternative to --section-id)")
    parser.add_argument("--volume", help="Volume name for --draft-volume")
    parser.add_argument("--feedback", help="Reviewer feedback for --refresh")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    if not args.proposal_id:
        parser.error("--proposal-id is required")

    result = {}

    if args.draft:
        if not args.section_id and not args.section_number:
            parser.error("--draft requires --section-id or --section-number")
        result = draft_section(args.proposal_id, section_id=args.section_id,
                               section_number=args.section_number)
    elif args.draft_volume:
        if not args.volume:
            parser.error("--draft-volume requires --volume")
        result = draft_volume(args.proposal_id, args.volume)
    elif args.exec_summary:
        result = draft_executive_summary(args.proposal_id)
    elif args.refresh:
        if not args.section_id:
            parser.error("--refresh requires --section-id")
        result = refresh_section(args.proposal_id, args.section_id, feedback=args.feedback)
    elif args.status:
        result = get_draft_status(args.proposal_id)
    else:
        parser.print_help()
        sys.exit(1)

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        if "error" in result:
            print(f"ERROR: {result['error']}", file=sys.stderr)
            sys.exit(1)
        if args.draft or args.refresh:
            print(f"Section: {result.get('section_number', '?')} — {result.get('section_title', '')}")
            print(f"Volume:  {result.get('volume', '')}")
            print(f"Method:  {result.get('method', 'unknown')}")
            print(f"Words:   {result.get('word_count', 0)}")
            print(f"Pages:   {result.get('page_count', 0)}")
            if result.get("version"):
                print(f"Version: {result['version']}")
            print(f"\n{'='*60}\n")
            print(result.get("content", "[No content generated]"))
        elif args.draft_volume:
            print(f"Volume '{result.get('volume', '')}' drafting complete:")
            print(f"  Drafted:  {result.get('drafted', 0)}")
            print(f"  Skipped:  {result.get('skipped', 0)}")
            print(f"  Errors:   {result.get('errors', 0)}")
            for sec in result.get("sections", []):
                status_icon = {"drafted": "+", "skipped": "-", "error": "!"}
                icon = status_icon.get(sec.get("status", ""), "?")
                print(f"  [{icon}] {sec.get('section_number', '?')}: {sec.get('section_title', '')}")
        elif args.exec_summary:
            print(f"Executive Summary ({result.get('word_count', 0)} words, {result.get('method', '')})")
            print(f"Volumes referenced: {', '.join(result.get('volumes_referenced', []))}")
            print(f"\n{'='*60}\n")
            print(result.get("content", "[No content generated]"))
        elif args.status:
            print(f"Draft Status — {result.get('total_sections', 0)} sections, "
                  f"{result.get('completion_percent', 0)}% complete")
            print(f"Total words: {result.get('total_word_count', 0)} | "
                  f"Pages: {result.get('total_page_count', 0)}")
            for vol, stats in result.get("by_volume", {}).items():
                print(f"\n  {vol.upper()} ({stats['total']} sections, {stats['word_count']} words):")
                print(f"    Drafted: {stats.get('drafted', 0)} | "
                      f"Reviewed: {stats.get('reviewed', 0)} | "
                      f"Final: {stats.get('final', 0)} | "
                      f"Outline: {stats.get('outline', 0)}")
        else:
            print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
