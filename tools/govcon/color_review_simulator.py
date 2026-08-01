# CUI // SP-CTI
from __future__ import annotations

# ICDEV™ GovCon AI Color Team Review Simulator — Phase 62 (§3.12)
# Simulates Shipley color team reviews (Blue/Pink/Red/Green/Gold)
# using deterministic scoring + optional LLM enhancement.

"""
Color Review Simulator — AI-powered Shipley color team review simulation.

Simulates five Shipley review types with three independent deterministic
critics (Compliance, Persuasiveness, Readability) and a consensus engine.

Review Types:
    Blue  — Capture plan & win strategy review (after R3 Shape)
    Pink  — Outline/storyboard review (after storyboard complete)
    Red   — Government evaluator simulation scored against Section M
    Green — Pricing review (cost compliance + technical consistency)
    Gold  — Final executive review (all critics combined)
    White — Compliance-check review (regulatory/policy adherence gate)

Critics:
    Compliance    — Section M requirement coverage, shall statement gaps,
                    formatting compliance
    Persuasiveness — Win theme density, discriminator mentions, vague
                     language detection, specificity scoring
    Readability   — Flesch-Kincaid grade level, sentence length, passive
                    voice percentage, acronym-at-first-use, tone consistency

Consensus:
    GO:          0 critical + 0 major findings
    CONDITIONAL: 0 critical findings (majors acceptable)
    NOGO:        any critical findings

Usage:
    python tools/govcon/color_review_simulator.py --opportunity-id "opp-xxx" --review-type red --json
    python tools/govcon/color_review_simulator.py --opportunity-id "opp-xxx" --review-type pink --json
    python tools/govcon/color_review_simulator.py --opportunity-id "opp-xxx" --history --json
    python tools/govcon/color_review_simulator.py --opportunity-id "opp-xxx" --trends --json
    python tools/govcon/color_review_simulator.py --opportunity-id "opp-xxx" --resolve --finding-id "f-xxx" --status addressed --json
    python tools/govcon/color_review_simulator.py --opportunity-id "opp-xxx" --export --review-type red --json
"""

import argparse
import json
import re
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# Path bootstrapping for CLI invocation
_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.db.storage import get_connection, table_exists  # noqa: E402
from tools.daemon.base import generate_id, utcnow_iso  # noqa: E402

_DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(_ROOT / "data" / "icdev.db")))

# ── Constants ─────────────────────────────────────────────────────────

REVIEW_TYPES = ("blue", "pink", "red", "green", "gold", "white")

SEVERITIES = ("critical", "major", "minor", "observation")

CATEGORIES = ("compliance", "persuasion", "readability", "formatting", "pricing", "strategy")

# Which critics run for each review type
REVIEW_CRITICS: Dict[str, List[str]] = {
    "blue": ["strategy"],
    "pink": ["compliance", "persuasiveness", "readability"],
    "red": ["compliance", "persuasiveness", "readability"],
    "green": ["pricing"],
    "gold": ["compliance", "persuasiveness", "readability", "pricing"],
    "white": ["compliance"],  # compliance-check gate before executive sign-off
}

# Vague / weak language patterns (D354 — deterministic keyword matching)
VAGUE_PATTERNS = [
    re.compile(r"\b(?:various|several|multiple|numerous|many)\b", re.IGNORECASE),
    re.compile(r"\b(?:as\s+(?:needed|required|appropriate|necessary))\b", re.IGNORECASE),
    re.compile(r"\b(?:etc|and\s+so\s+on|and\s+more)\b", re.IGNORECASE),
    re.compile(r"\b(?:leverage|utilize|synergize|optimize)\b", re.IGNORECASE),
    re.compile(
        r"\b(?:world[\s-]?class|best[\s-]?in[\s-]?class|cutting[\s-]?edge|state[\s-]?of[\s-]?the[\s-]?art)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b(?:robust|scalable|innovative|holistic|comprehensive)\b", re.IGNORECASE),
]

# Passive voice indicators
PASSIVE_PATTERNS = re.compile(
    r"\b(?:is|are|was|were|been|being|be)\s+"
    r"(?:being\s+)?(?:\w+ed|built|done|found|given|known|made|run|said|seen|shown|told|written)\b",
    re.IGNORECASE,
)


# ── Helpers ───────────────────────────────────────────────────────────


def _get_db():
    """Get a DB connection with WAL mode enabled."""
    conn = get_connection()
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _now():
    """Return current UTC time as ISO-8601 string."""
    return utcnow_iso()


def _audit(conn, action: str, details: str = "", actor: str = "color_review_simulator"):
    """Append-only audit trail entry (NIST AU, D6)."""
    try:
        conn.execute(
            "INSERT INTO audit_trail (created_at, event_type, actor, action, details, session_id) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (generate_id("aud"), _now(), "govcon.color_review", actor, action, details, "govcon"),
        )
    except Exception:
        pass


def _table_exists(conn, table_name: str) -> bool:
    """Check if a table exists in the database (backend-aware, translation-independent)."""
    return table_exists(conn, table_name)


def _count_syllables(word: str) -> int:
    """Count syllables in a word (reuses WriteGuard D-WG-2 pattern)."""
    word = word.lower().strip(".,!?;:'\"()-")
    if not word:
        return 0
    count = 0
    prev_vowel = False
    for ch in word:
        is_v = ch in "aeiouy"
        if is_v and not prev_vowel:
            count += 1
        prev_vowel = is_v
    if word.endswith("e") and count > 1:
        count -= 1
    return max(1, count)


def _flesch_kincaid_grade(text: str) -> float:
    """Compute Flesch-Kincaid grade level for text."""
    sentences = [s.strip() for s in re.split(r"[.!?]+", text) if s.strip()]
    words = text.split()
    if not sentences or not words:
        return 0.0
    syllables = sum(_count_syllables(w) for w in words)
    grade = 0.39 * (len(words) / len(sentences)) + 11.8 * (syllables / len(words)) - 15.59
    return max(0.0, min(25.0, grade))


def _get_sections(conn, opportunity_id: str) -> List[Dict]:
    """Load proposal sections with their draft content for an opportunity.

    Returns a list of dicts with: id, section_number, title, volume_id,
    word_limit, page_limit, content (from latest approved/draft).
    """
    sections = []

    if not _table_exists(conn, "proposal_sections"):
        return sections

    rows = conn.execute(
        "SELECT id, section_number, title, volume_id, word_limit, page_limit, "
        "current_word_count, current_page_count, status "
        "FROM proposal_sections WHERE opportunity_id = %s ORDER BY sort_order, section_number",
        (opportunity_id,),
    ).fetchall()

    for row in rows:
        section = dict(row)
        # Try to get section content from proposal_section_drafts
        content = ""
        if _table_exists(conn, "proposal_section_drafts"):
            draft = conn.execute(
                "SELECT draft_content FROM proposal_section_drafts "
                "WHERE opportunity_id = %s AND section_id = %s AND status IN ('approved', 'draft') "
                "ORDER BY CASE status WHEN 'approved' THEN 0 ELSE 1 END, created_at DESC LIMIT 1",
                (opportunity_id, row["id"]),
            ).fetchone()
            if draft:
                content = draft["draft_content"] or ""
        section["content"] = content
        sections.append(section)

    return sections


def _get_next_iteration(conn, opportunity_id: str, review_type: str) -> int:
    """Compute the next iteration number for a review type."""
    if not _table_exists(conn, "pg_review_findings"):
        return 1
    row = conn.execute(
        "SELECT COALESCE(MAX(review_iteration), 0) AS max_iter "
        "FROM pg_review_findings WHERE opportunity_id = %s AND review_type = %s",
        (opportunity_id, review_type),
    ).fetchone()
    return (row["max_iter"] if row else 0) + 1


# ── Compliance Critic ─────────────────────────────────────────────────


def _run_compliance_critic(opportunity_id: str, sections: List[Dict], review_type: str) -> List[Dict]:
    """Check Section M requirement coverage and shall statement gaps.

    For each requirement in pg_compliance_matrix with source_section='M':
        - 'gap' status        -> critical finding
        - 'partial' status    -> major finding
        - 'na' / 'addressed'  -> no finding

    Also checks formatting compliance (page/word limits) when available.
    """
    findings: List[Dict] = []
    conn = _get_db()

    # ── Section M requirement coverage ────────────────────────────────
    if _table_exists(conn, "pg_compliance_matrix"):
        matrix_rows = conn.execute(
            "SELECT id, requirement_id, requirement_text, compliance_status, "
            "assigned_section, evaluation_factor "
            "FROM pg_compliance_matrix WHERE opportunity_id = %s AND source_section = 'M'",
            (opportunity_id,),
        ).fetchall()

        total_reqs = len(matrix_rows)
        gaps = [r for r in matrix_rows if r["compliance_status"] == "gap"]
        partials = [r for r in matrix_rows if r["compliance_status"] == "partial"]
        addressed = [r for r in matrix_rows if r["compliance_status"] == "addressed"]

        for gap_req in gaps:
            findings.append(
                {
                    "severity": "critical",
                    "category": "compliance",
                    "finding_text": (
                        f"Section M requirement '{gap_req['requirement_text'][:120]}' "
                        f"(eval factor: {gap_req['evaluation_factor'] or 'unspecified'}) "
                        f"has NO corresponding proposal section. This is an evaluation gap."
                    ),
                    "recommendation": (
                        f"Create or assign a proposal section addressing requirement "
                        f"'{gap_req['requirement_id']}'. Map to Section M evaluation criteria."
                    ),
                    "section_id": gap_req["assigned_section"],
                }
            )

        for part_req in partials:
            findings.append(
                {
                    "severity": "major",
                    "category": "compliance",
                    "finding_text": (
                        f"Section M requirement '{part_req['requirement_text'][:120]}' "
                        f"is only PARTIALLY addressed. Government evaluators may score this "
                        f"as a weakness."
                    ),
                    "recommendation": (
                        f"Expand response for requirement '{part_req['requirement_id']}' "
                        f"to fully address all evaluation sub-criteria."
                    ),
                    "section_id": part_req["assigned_section"],
                }
            )

        # Summary observation if coverage is incomplete
        if total_reqs > 0:
            coverage_pct = len(addressed) / total_reqs
            if coverage_pct < 1.0:
                findings.append(
                    {
                        "severity": "observation",
                        "category": "compliance",
                        "finding_text": (
                            f"Section M compliance coverage: {coverage_pct:.0%} "
                            f"({len(addressed)}/{total_reqs} addressed, "
                            f"{len(gaps)} gaps, {len(partials)} partial)."
                        ),
                        "recommendation": "Resolve all gaps before submission.",
                        "section_id": None,
                    }
                )
    else:
        findings.append(
            {
                "severity": "minor",
                "category": "compliance",
                "finding_text": (
                    "Compliance matrix (pg_compliance_matrix) not populated. "
                    "Cannot verify Section M requirement coverage."
                ),
                "recommendation": (
                    f"Run: python tools/govcon/compliance_populator.py --populate --opp-id {opportunity_id} --json"
                ),
                "section_id": None,
            }
        )

    # ── Formatting compliance (page/word limits) ──────────────────────
    for section in sections:
        word_limit = section.get("word_limit")
        page_limit = section.get("page_limit")
        content = section.get("content", "")
        word_count = len(content.split()) if content else 0

        if word_limit and word_count > word_limit:
            over_pct = (word_count - word_limit) / word_limit * 100
            severity = "critical" if over_pct > 10 else "major"
            findings.append(
                {
                    "severity": severity,
                    "category": "formatting",
                    "finding_text": (
                        f"Section '{section.get('title', section.get('section_number', '?'))}' "
                        f"exceeds word limit: {word_count:,} words vs {word_limit:,} limit "
                        f"({over_pct:.0f}% over)."
                    ),
                    "recommendation": (
                        f"Reduce by {word_count - word_limit:,} words. "
                        f"Focus on removing redundancies and tightening language."
                    ),
                    "section_id": section.get("id"),
                }
            )

        if page_limit and section.get("current_page_count"):
            page_count = section["current_page_count"]
            if page_count > page_limit:
                findings.append(
                    {
                        "severity": "critical",
                        "category": "formatting",
                        "finding_text": (
                            f"Section '{section.get('title', '?')}' exceeds page limit: "
                            f"{page_count} pages vs {page_limit} limit."
                        ),
                        "recommendation": (
                            f"Reduce by {page_count - page_limit} page(s). Pages over the limit may not be evaluated."
                        ),
                        "section_id": section.get("id"),
                    }
                )

    conn.close()
    return findings


# ── Persuasiveness Critic ─────────────────────────────────────────────


def _run_persuasiveness_critic(opportunity_id: str, sections: List[Dict]) -> List[Dict]:
    """Evaluate win theme density, discriminator mentions, and specificity.

    Checks:
        - Win theme presence per section (from pg_win_themes)
        - Discriminator mention count
        - Vague / buzzword language patterns
        - Ghost competitor references
        - "So what" factor: sections that describe WHAT without WHY/benefit
    """
    findings: List[Dict] = []
    conn = _get_db()

    # ── Load win themes and discriminators ────────────────────────────
    win_themes: List[Dict] = []
    discriminators: List[Dict] = []
    ghosts: List[Dict] = []

    if _table_exists(conn, "pg_win_themes"):
        theme_rows = conn.execute(
            "SELECT id, theme_type, theme_statement, ghost_competitor, priority, status "
            "FROM pg_win_themes WHERE opportunity_id = %s AND status = 'active'",
            (opportunity_id,),
        ).fetchall()

        for tr in theme_rows:
            entry = dict(tr)
            if entry["theme_type"] == "win_theme":
                win_themes.append(entry)
            elif entry["theme_type"] == "discriminator":
                discriminators.append(entry)
            elif entry["theme_type"] == "ghost_strategy":
                ghosts.append(entry)

    if not win_themes and not discriminators:
        findings.append(
            {
                "severity": "major",
                "category": "strategy",
                "finding_text": (
                    "No win themes or discriminators defined for this opportunity. "
                    "Proposal lacks strategic positioning framework."
                ),
                "recommendation": (
                    "Define at least 3 win themes and 2 discriminators in the "
                    "Win Theme Registry before proceeding with content."
                ),
                "section_id": None,
            }
        )
        conn.close()
        return findings

    # ── Per-section analysis ──────────────────────────────────────────
    sections_with_content = [s for s in sections if s.get("content", "").strip()]

    if not sections_with_content:
        findings.append(
            {
                "severity": "major",
                "category": "persuasion",
                "finding_text": "No sections have content to evaluate.",
                "recommendation": "Draft section content before running persuasiveness review.",
                "section_id": None,
            }
        )
        conn.close()
        return findings

    total_theme_hits = 0
    total_disc_hits = 0
    total_vague_hits = 0

    for section in sections_with_content:
        content = section.get("content", "")
        content_lower = content.lower()
        section_title = section.get("title", section.get("section_number", "?"))
        section_id = section.get("id")

        # ── Win theme presence ────────────────────────────────────────
        section_theme_hits = 0
        for theme in win_themes:
            statement = theme.get("theme_statement", "")
            # Extract keywords from theme (words > 4 chars)
            keywords = [
                w.lower()
                for w in re.findall(r"\b\w{5,}\b", statement)
                if w.lower()
                not in {"their", "there", "these", "those", "which", "about", "would", "should", "could", "while"}
            ]
            matched = sum(1 for kw in keywords if kw in content_lower)
            if keywords and matched >= max(1, len(keywords) // 3):
                section_theme_hits += 1

        total_theme_hits += section_theme_hits
        if section_theme_hits == 0 and win_themes:
            findings.append(
                {
                    "severity": "major",
                    "category": "persuasion",
                    "finding_text": (
                        f"Section '{section_title}' does not reflect any of the "
                        f"{len(win_themes)} defined win themes. Evaluators may not "
                        f"see strategic differentiation."
                    ),
                    "recommendation": (
                        "Weave at least one win theme into this section. "
                        "Connect your approach to the customer's stated objectives."
                    ),
                    "section_id": section_id,
                }
            )

        # ── Discriminator mentions ────────────────────────────────────
        section_disc_hits = 0
        for disc in discriminators:
            statement = disc.get("theme_statement", "")
            keywords = [
                w.lower()
                for w in re.findall(r"\b\w{5,}\b", statement)
                if w.lower()
                not in {"their", "there", "these", "those", "which", "about", "would", "should", "could", "while"}
            ]
            matched = sum(1 for kw in keywords if kw in content_lower)
            if keywords and matched >= max(1, len(keywords) // 3):
                section_disc_hits += 1
        total_disc_hits += section_disc_hits

        # ── Vague language detection ──────────────────────────────────
        section_vague_count = 0
        vague_examples: List[str] = []
        for pattern in VAGUE_PATTERNS:
            matches = pattern.findall(content)
            section_vague_count += len(matches)
            vague_examples.extend(matches[:3])

        total_vague_hits += section_vague_count
        if section_vague_count >= 5:
            severity = "major" if section_vague_count >= 10 else "minor"
            findings.append(
                {
                    "severity": severity,
                    "category": "persuasion",
                    "finding_text": (
                        f"Section '{section_title}' contains {section_vague_count} "
                        f"instances of vague/buzzword language "
                        f"(e.g., {', '.join(set(vague_examples[:4]))}). "
                        f"Government evaluators want specifics, not buzzwords."
                    ),
                    "recommendation": (
                        "Replace vague language with specific quantities, names, "
                        "tools, timelines, or measurable outcomes."
                    ),
                    "section_id": section_id,
                }
            )

        # ── "So what" factor ──────────────────────────────────────────
        # Sections that describe capability without stating customer benefit
        benefit_patterns = re.compile(
            r"\b(?:result(?:ing|s)?|benefit|enabling|improv(?:e|ing|ement)|reduc(?:e|ing|tion)|"
            r"sav(?:e|ing|es)|accelerat(?:e|ing)|ensur(?:e|ing|es)|deliver(?:s|ing)?|"
            r"eliminat(?:e|ing)|prevent(?:s|ing)?)\b",
            re.IGNORECASE,
        )
        word_count = len(content.split())
        if word_count > 100:
            benefit_matches = len(benefit_patterns.findall(content))
            benefit_density = benefit_matches / (word_count / 100)
            if benefit_density < 1.0:
                findings.append(
                    {
                        "severity": "minor",
                        "category": "persuasion",
                        "finding_text": (
                            f"Section '{section_title}' has low benefit language density "
                            f"({benefit_density:.1f} per 100 words). Content describes "
                            f"capabilities without explaining customer benefits."
                        ),
                        "recommendation": (
                            "After each capability statement, add a 'so what' sentence: "
                            "'This results in...' or 'This enables the Government to...'"
                        ),
                        "section_id": section_id,
                    }
                )

    # ── Ghost competitor references ───────────────────────────────────
    if ghosts:
        all_content = " ".join(s.get("content", "") for s in sections_with_content).lower()
        for ghost in ghosts:
            competitor_name = ghost.get("ghost_competitor", "")
            if competitor_name and competitor_name.lower() in all_content:
                findings.append(
                    {
                        "severity": "critical",
                        "category": "persuasion",
                        "finding_text": (
                            f"DIRECT COMPETITOR REFERENCE: '{competitor_name}' mentioned "
                            f"in proposal text. Government proposals must ghost competitors "
                            f"indirectly — never name them."
                        ),
                        "recommendation": (
                            f"Remove all direct references to '{competitor_name}'. "
                            f"Use indirect ghosting: 'unlike approaches that rely on...' "
                            f"or 'other providers may...'"
                        ),
                        "section_id": None,
                    }
                )

    # ── Summary observation ───────────────────────────────────────────
    theme_coverage = (
        total_theme_hits / (len(sections_with_content) * len(win_themes)) if win_themes and sections_with_content else 0
    )
    findings.append(
        {
            "severity": "observation",
            "category": "persuasion",
            "finding_text": (
                f"Persuasiveness summary: {total_theme_hits} theme hits across "
                f"{len(sections_with_content)} sections ({theme_coverage:.0%} coverage), "
                f"{total_disc_hits} discriminator mentions, "
                f"{total_vague_hits} vague language instances."
            ),
            "recommendation": ("Target: every section references at least 1 win theme, 0 vague language instances."),
            "section_id": None,
        }
    )

    conn.close()
    return findings


# ── Readability Critic ────────────────────────────────────────────────


def _run_readability_critic(sections: List[Dict]) -> List[Dict]:
    """Evaluate readability metrics for proposal sections.

    Checks:
        - Flesch-Kincaid grade level (target: 10-12 for government proposals)
        - Average sentence length (target: < 25 words)
        - Passive voice percentage (target: < 20%)
        - Acronym-at-first-use consistency
        - Multi-author tone consistency (vocabulary diversity)
    """
    findings: List[Dict] = []
    sections_with_content = [s for s in sections if s.get("content", "").strip()]

    if not sections_with_content:
        return findings

    all_grade_levels: List[float] = []
    all_avg_sentence_lengths: List[float] = []
    global_acronyms: Dict[str, str] = {}  # acronym -> first section seen

    for section in sections_with_content:
        content = section.get("content", "")
        section_title = section.get("title", section.get("section_number", "?"))
        section_id = section.get("id")

        # ── Flesch-Kincaid grade level ────────────────────────────────
        grade = _flesch_kincaid_grade(content)
        all_grade_levels.append(grade)

        if grade > 16:
            findings.append(
                {
                    "severity": "major",
                    "category": "readability",
                    "finding_text": (
                        f"Section '{section_title}' has Flesch-Kincaid grade level {grade:.1f} "
                        f"(target: 10-12). Content is too complex for rapid government evaluation."
                    ),
                    "recommendation": (
                        "Simplify sentence structure. Break complex sentences into shorter ones. "
                        "Replace multi-syllable words with simpler alternatives where possible."
                    ),
                    "section_id": section_id,
                }
            )
        elif grade < 8:
            findings.append(
                {
                    "severity": "minor",
                    "category": "readability",
                    "finding_text": (
                        f"Section '{section_title}' has Flesch-Kincaid grade level {grade:.1f} "
                        f"(target: 10-12). Content may be perceived as too simplistic."
                    ),
                    "recommendation": (
                        "Increase technical depth. Add domain-specific terminology "
                        "to demonstrate subject matter expertise."
                    ),
                    "section_id": section_id,
                }
            )

        # ── Average sentence length ───────────────────────────────────
        sentences = [s.strip() for s in re.split(r"[.!?]+", content) if s.strip()]
        words = content.split()
        avg_sent_len = len(words) / len(sentences) if sentences else 0
        all_avg_sentence_lengths.append(avg_sent_len)

        if avg_sent_len > 30:
            findings.append(
                {
                    "severity": "major",
                    "category": "readability",
                    "finding_text": (
                        f"Section '{section_title}' has average sentence length of "
                        f"{avg_sent_len:.0f} words (target: < 25). Long sentences lose "
                        f"evaluators."
                    ),
                    "recommendation": (
                        "Break sentences longer than 25 words. Use bullet points for "
                        "lists of capabilities or requirements."
                    ),
                    "section_id": section_id,
                }
            )
        elif avg_sent_len > 25:
            findings.append(
                {
                    "severity": "minor",
                    "category": "readability",
                    "finding_text": (
                        f"Section '{section_title}' average sentence length "
                        f"({avg_sent_len:.0f} words) slightly above target of 25."
                    ),
                    "recommendation": "Consider splitting longest sentences.",
                    "section_id": section_id,
                }
            )

        # ── Passive voice percentage ──────────────────────────────────
        passive_matches = PASSIVE_PATTERNS.findall(content)
        passive_pct = (len(passive_matches) / len(sentences) * 100) if sentences else 0

        if passive_pct > 30:
            findings.append(
                {
                    "severity": "major",
                    "category": "readability",
                    "finding_text": (
                        f"Section '{section_title}' has {passive_pct:.0f}% passive voice "
                        f"(target: < 20%). Passive voice weakens assertions and "
                        f"obscures accountability."
                    ),
                    "recommendation": (
                        "Convert passive to active: 'The system was designed by our team' "
                        "-> 'Our team designed the system'. Active voice shows ownership."
                    ),
                    "section_id": section_id,
                }
            )
        elif passive_pct > 20:
            findings.append(
                {
                    "severity": "minor",
                    "category": "readability",
                    "finding_text": (f"Section '{section_title}' passive voice at {passive_pct:.0f}% (target: < 20%)."),
                    "recommendation": "Reduce passive constructions for stronger tone.",
                    "section_id": section_id,
                }
            )

        # ── Acronym-at-first-use check ────────────────────────────────
        # Find all uppercase acronyms (2+ caps)
        acronyms_in_section = re.findall(r"\b([A-Z]{2,})\b", content)
        unique_acronyms = set(acronyms_in_section)

        for acr in unique_acronyms:
            # Check if the section defines the acronym: "Federal Risk and Authorization Management Program (FedRAMP)"
            definition_pattern = re.compile(
                r"[A-Z][a-z]+(?:\s+(?:and|of|for|the|in|on|to|a|an)\s+|\s+[A-Z])[^\(]*\(" + re.escape(acr) + r"\)",
                re.IGNORECASE,
            )
            defined_here = bool(definition_pattern.search(content))

            if acr not in global_acronyms:
                # First time seeing this acronym
                global_acronyms[acr] = section_title
                if not defined_here and len(acr) >= 3:
                    findings.append(
                        {
                            "severity": "minor",
                            "category": "readability",
                            "finding_text": (
                                f"Acronym '{acr}' first appears in section '{section_title}' without definition."
                            ),
                            "recommendation": (
                                f"Define '{acr}' at first use: 'Full Name ({acr})'. "
                                f"Include in acronym list if provided."
                            ),
                            "section_id": section_id,
                        }
                    )

    # ── Multi-author tone consistency ─────────────────────────────────
    if len(sections_with_content) >= 3:
        # Compare vocabulary diversity (type-token ratio) across sections
        ttrs: List[float] = []
        for section in sections_with_content:
            words = re.findall(r"\b\w+\b", section.get("content", "").lower())
            if len(words) >= 50:
                ttr = len(set(words)) / len(words)
                ttrs.append(ttr)

        if len(ttrs) >= 3:
            ttr_range = max(ttrs) - min(ttrs)
            if ttr_range > 0.15:
                findings.append(
                    {
                        "severity": "minor",
                        "category": "readability",
                        "finding_text": (
                            f"Tone inconsistency detected across sections. "
                            f"Vocabulary diversity ranges from {min(ttrs):.2f} to "
                            f"{max(ttrs):.2f} (spread: {ttr_range:.2f}). "
                            f"This suggests multiple authors with different writing styles."
                        ),
                        "recommendation": (
                            "Perform a 'white glove' pass for consistent voice. "
                            "Normalize sentence structure, formality level, and "
                            "transition language across all sections."
                        ),
                        "section_id": None,
                    }
                )

    # ── Summary observation ───────────────────────────────────────────
    avg_grade = sum(all_grade_levels) / len(all_grade_levels) if all_grade_levels else 0
    avg_sent = sum(all_avg_sentence_lengths) / len(all_avg_sentence_lengths) if all_avg_sentence_lengths else 0
    findings.append(
        {
            "severity": "observation",
            "category": "readability",
            "finding_text": (
                f"Readability summary: avg grade level {avg_grade:.1f} (target: 10-12), "
                f"avg sentence length {avg_sent:.0f} words (target: < 25), "
                f"{len(global_acronyms)} unique acronyms found."
            ),
            "recommendation": "Target: grade level 10-12, sentence length < 25 words, < 20% passive voice.",
            "section_id": None,
        }
    )

    return findings


# ── Strategy Critic (Blue team only) ──────────────────────────────────


def _run_strategy_critic(opportunity_id: str, sections: List[Dict]) -> List[Dict]:
    """Evaluate capture strategy alignment for Blue team reviews.

    Checks:
        - Win themes defined
        - Competitive analysis present
        - Teaming strategy established
        - Key personnel identified
    """
    findings: List[Dict] = []
    conn = _get_db()

    # ── Win themes defined ────────────────────────────────────────────
    theme_count = 0
    if _table_exists(conn, "pg_win_themes"):
        row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM pg_win_themes WHERE opportunity_id = %s AND status = 'active'",
            (opportunity_id,),
        ).fetchone()
        theme_count = row["cnt"] if row else 0

    if theme_count == 0:
        findings.append(
            {
                "severity": "critical",
                "category": "strategy",
                "finding_text": "No win themes defined. Proposal lacks strategic differentiation.",
                "recommendation": "Define at least 3 win themes aligned to Section M evaluation criteria.",
                "section_id": None,
            }
        )
    elif theme_count < 3:
        findings.append(
            {
                "severity": "major",
                "category": "strategy",
                "finding_text": f"Only {theme_count} win theme(s) defined. Best practice is 3-5.",
                "recommendation": "Add win themes that map directly to Section M evaluation factors.",
                "section_id": None,
            }
        )

    # ── Competitive analysis ──────────────────────────────────────────
    ghost_count = 0
    if _table_exists(conn, "pg_win_themes"):
        row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM pg_win_themes "
            "WHERE opportunity_id = %s AND theme_type = 'ghost_strategy' AND status = 'active'",
            (opportunity_id,),
        ).fetchone()
        ghost_count = row["cnt"] if row else 0

    if ghost_count == 0:
        findings.append(
            {
                "severity": "major",
                "category": "strategy",
                "finding_text": "No ghost competitor strategies defined. Cannot differentiate against competition.",
                "recommendation": "Identify top 2-3 competitors and create ghosting strategies.",
                "section_id": None,
            }
        )

    # ── Teaming strategy ──────────────────────────────────────────────
    teaming_count = 0
    if _table_exists(conn, "pg_teaming_workshare"):
        row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM pg_teaming_workshare WHERE opportunity_id = %s",
            (opportunity_id,),
        ).fetchone()
        teaming_count = row["cnt"] if row else 0

    if teaming_count == 0:
        findings.append(
            {
                "severity": "minor",
                "category": "strategy",
                "finding_text": "No teaming partners / workshare defined. Verify if sole-source or teaming needed.",
                "recommendation": "If teaming is planned, define workshare allocations and TA agreements.",
                "section_id": None,
            }
        )

    conn.close()
    return findings


# ── Pricing Critic (Green team) ───────────────────────────────────────


def _run_pricing_critic(opportunity_id: str, sections: List[Dict]) -> List[Dict]:
    """Evaluate cost volume for Green team reviews.

    Checks:
        - Cost volume exists and has pricing
        - Labor categories allocated
        - Rate competitiveness (against PTW estimate if available)
        - Technical-cost consistency (staffing mentioned in technical aligns with cost)
    """
    findings: List[Dict] = []
    conn = _get_db()

    # ── Cost volume existence ─────────────────────────────────────────
    cost_volume = None
    if _table_exists(conn, "pg_cost_volumes"):
        cost_volume = conn.execute(
            "SELECT * FROM pg_cost_volumes WHERE opportunity_id = %s ORDER BY updated_at DESC LIMIT 1",
            (opportunity_id,),
        ).fetchone()

    if not cost_volume:
        findings.append(
            {
                "severity": "critical",
                "category": "pricing",
                "finding_text": "No cost volume defined for this opportunity.",
                "recommendation": "Create cost volume with labor categories, rates, and pricing strategy.",
                "section_id": None,
            }
        )
        conn.close()
        return findings

    cost_volume = dict(cost_volume)

    # ── Total evaluated price ─────────────────────────────────────────
    tep = cost_volume.get("total_evaluated_price")
    if not tep or tep <= 0:
        findings.append(
            {
                "severity": "critical",
                "category": "pricing",
                "finding_text": "Total Evaluated Price is not set or is zero.",
                "recommendation": "Calculate TEP from labor, ODC, subcontractor, and fee components.",
                "section_id": None,
            }
        )

    # ── PTW (Price to Win) comparison ─────────────────────────────────
    ptw_low = cost_volume.get("ptw_estimate_low")
    ptw_high = cost_volume.get("ptw_estimate_high")
    if tep and ptw_low and ptw_high:
        if tep > ptw_high:
            findings.append(
                {
                    "severity": "critical",
                    "category": "pricing",
                    "finding_text": (
                        f"TEP (${tep:,.0f}) exceeds PTW high estimate (${ptw_high:,.0f}). "
                        f"Price is likely not competitive."
                    ),
                    "recommendation": (
                        "Identify cost reduction opportunities: reduce FTEs, negotiate "
                        "sub rates, lower fee percentage, or adjust labor mix."
                    ),
                    "section_id": None,
                }
            )
        elif tep < ptw_low:
            findings.append(
                {
                    "severity": "major",
                    "category": "pricing",
                    "finding_text": (
                        f"TEP (${tep:,.0f}) is below PTW low estimate (${ptw_low:,.0f}). "
                        f"Government may question realism of pricing."
                    ),
                    "recommendation": (
                        "Review pricing for realism. Ensure rates meet FAR cost "
                        "adequacy requirements and support the proposed technical approach."
                    ),
                    "section_id": None,
                }
            )

    # ── Labor category coverage ───────────────────────────────────────
    lcat_count = 0
    if _table_exists(conn, "pg_lcat_allocations"):
        row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM pg_lcat_allocations WHERE cost_volume_id = %s",
            (cost_volume.get("id", ""),),
        ).fetchone()
        lcat_count = row["cnt"] if row else 0

    if lcat_count == 0:
        findings.append(
            {
                "severity": "major",
                "category": "pricing",
                "finding_text": "No labor categories allocated in cost volume.",
                "recommendation": "Allocate LCATs with BLS SOC codes, FTE counts, and hourly rates.",
                "section_id": None,
            }
        )

    # ── Rate components ───────────────────────────────────────────────
    for component, label in [
        ("fringe_rate", "Fringe rate"),
        ("overhead_rate", "Overhead rate"),
        ("g_and_a_rate", "G&A rate"),
    ]:
        value = cost_volume.get(component)
        if value is None:
            findings.append(
                {
                    "severity": "minor",
                    "category": "pricing",
                    "finding_text": f"{label} is not set in cost volume.",
                    "recommendation": f"Set {label} based on approved indirect rate structure.",
                    "section_id": None,
                }
            )

    conn.close()
    return findings


# ── Consensus Engine ──────────────────────────────────────────────────


def _compute_consensus(findings: List[Dict]) -> str:
    """Compute review consensus from findings.

    GO:          0 critical + 0 major findings
    CONDITIONAL: 0 critical findings (majors acceptable)
    NOGO:        any critical findings
    """
    critical_count = sum(1 for f in findings if f.get("severity") == "critical")
    major_count = sum(1 for f in findings if f.get("severity") == "major")

    if critical_count > 0:
        return "nogo"
    elif major_count > 0:
        return "conditional"
    else:
        return "go"


# ── Main Entry Point ──────────────────────────────────────────────────


def simulate_review(
    opportunity_id: str,
    review_type: str,
    sections: Optional[List[Dict]] = None,
) -> Dict[str, Any]:
    """Run a full color team review simulation.

    Args:
        opportunity_id: The proposal opportunity ID.
        review_type: One of 'blue', 'pink', 'red', 'green', 'gold'.
        sections: Optional pre-loaded sections. If None, loads from DB.

    Returns:
        Dict with: status, review_type, iteration, consensus, findings,
        scores, total counts by severity, and stored finding IDs.
    """
    if review_type not in REVIEW_TYPES:
        return {
            "status": "error",
            "error": f"Invalid review_type '{review_type}'. Must be one of: {', '.join(REVIEW_TYPES)}",
        }

    conn = _get_db()

    # Load sections if not provided
    if sections is None:
        sections = _get_sections(conn, opportunity_id)

    iteration = _get_next_iteration(conn, opportunity_id, review_type)

    # ── Run appropriate critics ───────────────────────────────────────
    all_findings: List[Dict] = []
    critic_names = REVIEW_CRITICS.get(review_type, [])
    scores: Dict[str, float] = {}

    if "compliance" in critic_names:
        compliance_findings = _run_compliance_critic(opportunity_id, sections, review_type)
        all_findings.extend(compliance_findings)
        # Score: ratio of non-critical/major findings to total
        total = len(compliance_findings)
        issues = sum(1 for f in compliance_findings if f["severity"] in ("critical", "major"))
        scores["compliance"] = round(1.0 - (issues / max(total, 1)), 2)

    if "persuasiveness" in critic_names:
        persuasion_findings = _run_persuasiveness_critic(opportunity_id, sections)
        all_findings.extend(persuasion_findings)
        total = len(persuasion_findings)
        issues = sum(1 for f in persuasion_findings if f["severity"] in ("critical", "major"))
        scores["persuasiveness"] = round(1.0 - (issues / max(total, 1)), 2)

    if "readability" in critic_names:
        readability_findings = _run_readability_critic(sections)
        all_findings.extend(readability_findings)
        total = len(readability_findings)
        issues = sum(1 for f in readability_findings if f["severity"] in ("critical", "major"))
        scores["readability"] = round(1.0 - (issues / max(total, 1)), 2)

    if "strategy" in critic_names:
        strategy_findings = _run_strategy_critic(opportunity_id, sections)
        all_findings.extend(strategy_findings)
        total = len(strategy_findings)
        issues = sum(1 for f in strategy_findings if f["severity"] in ("critical", "major"))
        scores["strategy"] = round(1.0 - (issues / max(total, 1)), 2)

    if "pricing" in critic_names:
        pricing_findings = _run_pricing_critic(opportunity_id, sections)
        all_findings.extend(pricing_findings)
        total = len(pricing_findings)
        issues = sum(1 for f in pricing_findings if f["severity"] in ("critical", "major"))
        scores["pricing"] = round(1.0 - (issues / max(total, 1)), 2)

    # ── Consensus ─────────────────────────────────────────────────────
    consensus = _compute_consensus(all_findings)

    # ── Compute composite score ───────────────────────────────────────
    composite_score = round(sum(scores.values()) / max(len(scores), 1), 2) if scores else 0.0

    # ── Count by severity ─────────────────────────────────────────────
    severity_counts = {sev: 0 for sev in SEVERITIES}
    for f in all_findings:
        sev = f.get("severity", "observation")
        if sev in severity_counts:
            severity_counts[sev] += 1

    # ── Store findings in pg_review_findings ──────────────────────────
    stored_ids: List[str] = []
    now = _now()

    if _table_exists(conn, "pg_review_findings"):
        for finding in all_findings:
            finding_id = generate_id("rf")
            try:
                conn.execute(
                    "INSERT INTO pg_review_findings "
                    "(id, opportunity_id, review_type, review_iteration, section_id, "
                    "severity, category, finding_text, recommendation, "
                    "resolution_status, created_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (
                        finding_id,
                        opportunity_id,
                        review_type,
                        iteration,
                        finding.get("section_id"),
                        finding["severity"],
                        finding["category"],
                        finding["finding_text"],
                        finding.get("recommendation", ""),
                        "open",
                        now,
                    ),
                )
                stored_ids.append(finding_id)
                finding["id"] = finding_id
            except Exception:
                pass

        _audit(
            conn,
            f"simulate_{review_type}",
            json.dumps(
                {
                    "opportunity_id": opportunity_id,
                    "iteration": iteration,
                    "consensus": consensus,
                    "total_findings": len(all_findings),
                    "critical": severity_counts["critical"],
                    "major": severity_counts["major"],
                }
            ),
        )
        conn.commit()

    conn.close()

    return {
        "status": "ok",
        "review_type": review_type,
        "iteration": iteration,
        "consensus": consensus,
        "composite_score": composite_score,
        "scores": scores,
        "severity_counts": severity_counts,
        "total_findings": len(all_findings),
        "findings": all_findings,
        "stored_finding_ids": stored_ids,
        "sections_evaluated": len(sections),
        "created_at": now,
    }


# ── History & Trends ──────────────────────────────────────────────────


def get_review_history(opportunity_id: str) -> Dict[str, Any]:
    """Get all review findings grouped by review type and iteration.

    Returns chronological history showing improvement across iterations.
    """
    conn = _get_db()

    if not _table_exists(conn, "pg_review_findings"):
        conn.close()
        return {"status": "ok", "reviews": [], "total_findings": 0}

    rows = conn.execute(
        "SELECT id, review_type, review_iteration, section_id, severity, "
        "category, finding_text, recommendation, resolution_status, "
        "resolution_notes, created_at, resolved_at "
        "FROM pg_review_findings WHERE opportunity_id = %s "
        "ORDER BY review_type, review_iteration, severity",
        (opportunity_id,),
    ).fetchall()

    conn.close()

    # Group by review_type -> iteration
    reviews: Dict[str, Dict[int, List[Dict]]] = {}
    for row in rows:
        rt = row["review_type"]
        it = row["review_iteration"]
        if rt not in reviews:
            reviews[rt] = {}
        if it not in reviews[rt]:
            reviews[rt][it] = []
        reviews[rt][it].append(dict(row))

    # Build structured output
    review_list: List[Dict] = []
    for rt in sorted(reviews.keys()):
        for it in sorted(reviews[rt].keys()):
            findings = reviews[rt][it]
            severity_counts = {sev: 0 for sev in SEVERITIES}
            for f in findings:
                sev = f.get("severity", "observation")
                if sev in severity_counts:
                    severity_counts[sev] += 1

            resolved = sum(1 for f in findings if f["resolution_status"] != "open")
            consensus = _compute_consensus(findings)

            review_list.append(
                {
                    "review_type": rt,
                    "iteration": it,
                    "consensus": consensus,
                    "total_findings": len(findings),
                    "severity_counts": severity_counts,
                    "resolved": resolved,
                    "open": len(findings) - resolved,
                    "created_at": findings[0]["created_at"] if findings else None,
                    "findings": findings,
                }
            )

    return {
        "status": "ok",
        "opportunity_id": opportunity_id,
        "reviews": review_list,
        "total_findings": len(rows),
    }


def get_finding_trends(opportunity_id: str) -> Dict[str, Any]:
    """Track improvement across review iterations.

    Computes per-review-type trend showing critical/major/minor/observation
    counts across iterations — improvement should show decreasing counts.
    """
    conn = _get_db()

    if not _table_exists(conn, "pg_review_findings"):
        conn.close()
        return {"status": "ok", "trends": {}, "improvement_summary": {}}

    rows = conn.execute(
        "SELECT review_type, review_iteration, severity, COUNT(*) AS cnt "
        "FROM pg_review_findings WHERE opportunity_id = %s "
        "GROUP BY review_type, review_iteration, severity "
        "ORDER BY review_type, review_iteration",
        (opportunity_id,),
    ).fetchall()

    conn.close()

    # Build trend structure
    trends: Dict[str, List[Dict]] = {}
    for row in rows:
        rt = row["review_type"]
        it = row["review_iteration"]
        if rt not in trends:
            trends[rt] = []

        # Find or create iteration entry
        iter_entry = None
        for entry in trends[rt]:
            if entry["iteration"] == it:
                iter_entry = entry
                break
        if not iter_entry:
            iter_entry = {"iteration": it, "critical": 0, "major": 0, "minor": 0, "observation": 0, "total": 0}
            trends[rt].append(iter_entry)

        sev = row["severity"]
        if sev in iter_entry:
            iter_entry[sev] = row["cnt"]
        iter_entry["total"] += row["cnt"]

    # Compute improvement summary
    improvement_summary: Dict[str, Dict] = {}
    for rt, iterations in trends.items():
        if len(iterations) >= 2:
            first = iterations[0]
            last = iterations[-1]
            first_issues = first["critical"] + first["major"]
            last_issues = last["critical"] + last["major"]
            improvement = first_issues - last_issues
            improvement_pct = (improvement / max(first_issues, 1)) * 100

            improvement_summary[rt] = {
                "first_iteration": first["iteration"],
                "last_iteration": last["iteration"],
                "first_critical_major": first_issues,
                "last_critical_major": last_issues,
                "improvement": improvement,
                "improvement_pct": round(improvement_pct, 1),
                "direction": "improving" if improvement > 0 else ("stable" if improvement == 0 else "degrading"),
            }
        elif len(iterations) == 1:
            improvement_summary[rt] = {
                "first_iteration": iterations[0]["iteration"],
                "last_iteration": iterations[0]["iteration"],
                "first_critical_major": iterations[0]["critical"] + iterations[0]["major"],
                "last_critical_major": iterations[0]["critical"] + iterations[0]["major"],
                "improvement": 0,
                "improvement_pct": 0.0,
                "direction": "single_iteration",
            }

    return {
        "status": "ok",
        "opportunity_id": opportunity_id,
        "trends": trends,
        "improvement_summary": improvement_summary,
    }


# ── Finding Resolution ────────────────────────────────────────────────


def resolve_finding(
    finding_id: str,
    status: str,
    notes: Optional[str] = None,
) -> Dict[str, Any]:
    """Resolve a review finding by updating its resolution status.

    Args:
        finding_id: The finding ID to resolve.
        status: One of 'addressed', 'deferred', 'wontfix'.
        notes: Optional resolution notes.

    Returns:
        Dict with status and updated finding.
    """
    valid_statuses = ("addressed", "deferred", "wontfix")
    if status not in valid_statuses:
        return {
            "status": "error",
            "error": f"Invalid resolution status '{status}'. Must be one of: {', '.join(valid_statuses)}",
        }

    conn = _get_db()

    if not _table_exists(conn, "pg_review_findings"):
        conn.close()
        return {"status": "error", "error": "pg_review_findings table does not exist."}

    existing = conn.execute("SELECT * FROM pg_review_findings WHERE id = %s", (finding_id,)).fetchone()

    if not existing:
        conn.close()
        return {"status": "error", "error": f"Finding '{finding_id}' not found."}

    now = _now()
    conn.execute(
        "UPDATE pg_review_findings SET resolution_status = %s, resolution_notes = %s, resolved_at = %s WHERE id = %s",
        (status, notes or "", now, finding_id),
    )

    _audit(
        conn,
        "resolve_finding",
        json.dumps(
            {
                "finding_id": finding_id,
                "old_status": existing["resolution_status"],
                "new_status": status,
                "notes": notes or "",
            }
        ),
    )
    conn.commit()

    updated = conn.execute("SELECT * FROM pg_review_findings WHERE id = %s", (finding_id,)).fetchone()
    conn.close()

    return {
        "status": "ok",
        "finding": dict(updated) if updated else {},
        "resolved_at": now,
    }


# ── Export ────────────────────────────────────────────────────────────


def export_review(
    opportunity_id: str,
    review_type: str,
    iteration: Optional[int] = None,
) -> str:
    """Export a review as formatted Markdown for stakeholder distribution.

    Args:
        opportunity_id: The proposal opportunity ID.
        review_type: One of 'blue', 'pink', 'red', 'green', 'gold'.
        iteration: Specific iteration to export. If None, exports the latest.

    Returns:
        Formatted Markdown string.
    """
    conn = _get_db()

    if not _table_exists(conn, "pg_review_findings"):
        conn.close()
        return "# No review findings available\n\nThe pg_review_findings table does not exist."

    # Determine iteration
    if iteration is None:
        row = conn.execute(
            "SELECT MAX(review_iteration) AS max_iter FROM pg_review_findings "
            "WHERE opportunity_id = %s AND review_type = %s",
            (opportunity_id, review_type),
        ).fetchone()
        iteration = row["max_iter"] if row and row["max_iter"] else 0

    if iteration == 0:
        conn.close()
        return f"# No {review_type.upper()} team reviews found for {opportunity_id}\n"

    rows = conn.execute(
        "SELECT * FROM pg_review_findings "
        "WHERE opportunity_id = %s AND review_type = %s AND review_iteration = %s "
        "ORDER BY CASE severity "
        "  WHEN 'critical' THEN 0 "
        "  WHEN 'major' THEN 1 "
        "  WHEN 'minor' THEN 2 "
        "  WHEN 'observation' THEN 3 "
        "END, category",
        (opportunity_id, review_type, iteration),
    ).fetchall()

    conn.close()

    findings = [dict(r) for r in rows]
    consensus = _compute_consensus(findings)
    severity_counts = {sev: 0 for sev in SEVERITIES}
    for f in findings:
        sev = f.get("severity", "observation")
        if sev in severity_counts:
            severity_counts[sev] += 1

    # ── Build Markdown ────────────────────────────────────────────────
    review_labels = {
        "blue": "Blue Team (Capture/Strategy)",
        "pink": "Pink Team (Storyboard/Outline)",
        "red": "Red Team (Government Evaluator Simulation)",
        "green": "Green Team (Pricing)",
        "gold": "Gold Team (Final Executive)",
    }

    consensus_labels = {
        "go": "GO - Proceed to next phase",
        "conditional": "CONDITIONAL - Proceed with resolution of major findings",
        "nogo": "NO-GO - Critical findings must be resolved before proceeding",
    }

    lines: List[str] = []
    lines.append("# CUI // SP-CTI")
    lines.append("")
    lines.append(f"# {review_labels.get(review_type, review_type.upper())} Review Report")
    lines.append("")
    lines.append(f"**Opportunity:** {opportunity_id}")
    lines.append(f"**Iteration:** {iteration}")
    lines.append(f"**Date:** {_now()[:10]}")
    lines.append(f"**Consensus:** **{consensus_labels.get(consensus, consensus.upper())}**")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append("| Severity | Count |")
    lines.append("|----------|-------|")
    for sev in SEVERITIES:
        lines.append(f"| {sev.capitalize()} | {severity_counts[sev]} |")
    lines.append(f"| **Total** | **{len(findings)}** |")
    lines.append("")

    # Group findings by severity
    for sev in SEVERITIES:
        sev_findings = [f for f in findings if f["severity"] == sev]
        if not sev_findings:
            continue

        lines.append(f"## {sev.capitalize()} Findings ({len(sev_findings)})")
        lines.append("")

        for i, f in enumerate(sev_findings, 1):
            status_icon = {
                "open": "[OPEN]",
                "addressed": "[RESOLVED]",
                "deferred": "[DEFERRED]",
                "wontfix": "[WONTFIX]",
            }.get(f.get("resolution_status", "open"), "[OPEN]")

            lines.append(f"### {sev.capitalize()}-{i}: {f['category'].capitalize()} {status_icon}")
            lines.append("")
            lines.append(f"**Finding:** {f['finding_text']}")
            lines.append("")
            if f.get("recommendation"):
                lines.append(f"**Recommendation:** {f['recommendation']}")
                lines.append("")
            if f.get("section_id"):
                lines.append(f"**Section:** {f['section_id']}")
                lines.append("")
            if f.get("resolution_notes"):
                lines.append(f"**Resolution Notes:** {f['resolution_notes']}")
                lines.append("")

    lines.append("---")
    lines.append("*Generated by ICDEV™ AI Color Team Review Simulator*")
    lines.append("")
    lines.append("# CUI // SP-CTI")

    return "\n".join(lines)


# ── CLI ───────────────────────────────────────────────────────────────


def _print_human(result: Dict, action: str) -> None:
    """Human-readable output for terminal display."""
    status = result.get("status", "unknown")
    print(f"\n{'=' * 70}")
    print(f"  ICDEV™ AI Color Team Review Simulator — {status.upper()}")
    print(f"{'=' * 70}")

    if status == "error":
        print(f"\n  ERROR: {result.get('error', 'Unknown error')}\n")
        return

    if action == "simulate":
        rt = result.get("review_type", "?").upper()
        consensus = result.get("consensus", "?").upper()
        iteration = result.get("iteration", "?")
        composite = result.get("composite_score", 0)

        consensus_color = {
            "GO": "\033[92m",  # green
            "CONDITIONAL": "\033[93m",  # yellow
            "NOGO": "\033[91m",  # red
        }.get(consensus, "")
        reset = "\033[0m"

        print(f"\n  Review Type:    {rt} Team (iteration #{iteration})")
        print(f"  Consensus:      {consensus_color}{consensus}{reset}")
        print(f"  Composite Score: {composite:.0%}")
        print(f"  Sections:       {result.get('sections_evaluated', 0)}")
        print()

        scores = result.get("scores", {})
        if scores:
            print("  Critic Scores:")
            for critic, score in scores.items():
                bar = "#" * int(score * 20) + "-" * (20 - int(score * 20))
                print(f"    {critic:<16} [{bar}] {score:.0%}")
            print()

        counts = result.get("severity_counts", {})
        print("  Findings by Severity:")
        for sev in SEVERITIES:
            cnt = counts.get(sev, 0)
            marker = "!!" if sev == "critical" and cnt > 0 else ""
            print(f"    {sev.capitalize():<14} {cnt:>3} {marker}")
        print(f"    {'Total':<14} {result.get('total_findings', 0):>3}")
        print()

        # Show critical and major findings
        findings = result.get("findings", [])
        critical_major = [f for f in findings if f.get("severity") in ("critical", "major")]
        if critical_major:
            print("  Critical/Major Findings:")
            for f in critical_major:
                sev = f.get("severity", "?").upper()
                cat = f.get("category", "?")
                text = f.get("finding_text", "")[:100]
                print(f"    [{sev}] ({cat}) {text}")
            print()

    elif action == "history":
        reviews = result.get("reviews", [])
        print(f"\n  Total findings: {result.get('total_findings', 0)}")
        print(f"  Reviews: {len(reviews)}")
        print()
        for rev in reviews:
            rt = rev["review_type"].upper()
            it = rev["iteration"]
            consensus = rev["consensus"].upper()
            total = rev["total_findings"]
            resolved = rev.get("resolved", 0)
            print(f"    {rt} #{it}: {consensus} ({total} findings, {resolved} resolved)")

    elif action == "trends":
        summary = result.get("improvement_summary", {})
        for rt, data in summary.items():
            direction = data.get("direction", "?")
            pct = data.get("improvement_pct", 0)
            print(f"\n  {rt.upper()}: {direction} ({pct:+.0f}%)")
            print(f"    Iterations: {data['first_iteration']} -> {data['last_iteration']}")
            print(f"    Critical+Major: {data['first_critical_major']} -> {data['last_critical_major']}")

    elif action == "resolve":
        finding = result.get("finding", {})
        print(f"\n  Finding:    {finding.get('id', '?')}")
        print(f"  Status:     {finding.get('resolution_status', '?')}")
        print(f"  Resolved:   {result.get('resolved_at', '?')}")

    print()


def main():
    parser = argparse.ArgumentParser(description="ICDEV™ AI Color Team Review Simulator (§3.12)")

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--review-type",
        choices=REVIEW_TYPES,
        help="Run review simulation: blue, pink, red, green, gold",
    )
    group.add_argument("--history", action="store_true", help="Show review history")
    group.add_argument("--trends", action="store_true", help="Show finding trends")
    group.add_argument("--resolve", action="store_true", help="Resolve a finding")
    group.add_argument("--export", action="store_true", help="Export review as Markdown")

    parser.add_argument("--opportunity-id", required=True, help="Opportunity ID")
    parser.add_argument("--finding-id", help="Finding ID (for --resolve)")
    parser.add_argument("--status", help="Resolution status (for --resolve): addressed, deferred, wontfix")
    parser.add_argument("--notes", help="Resolution notes (for --resolve)")
    parser.add_argument("--iteration", type=int, help="Specific iteration (for --export)")
    parser.add_argument("--export-review-type", choices=REVIEW_TYPES, help="Review type (for --export)")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--human", action="store_true", help="Human-readable output")

    args = parser.parse_args()

    result: Dict[str, Any] = {}
    action = ""

    if args.review_type:
        action = "simulate"
        result = simulate_review(args.opportunity_id, args.review_type)

    elif args.history:
        action = "history"
        result = get_review_history(args.opportunity_id)

    elif args.trends:
        action = "trends"
        result = get_finding_trends(args.opportunity_id)

    elif args.resolve:
        action = "resolve"
        if not args.finding_id or not args.status:
            result = {"status": "error", "error": "--finding-id and --status are required for --resolve"}
        else:
            result = resolve_finding(args.finding_id, args.status, args.notes)

    elif args.export:
        action = "export"
        export_rt = args.export_review_type or "red"
        markdown = export_review(args.opportunity_id, export_rt, args.iteration)
        if args.json:
            result = {"status": "ok", "markdown": markdown, "review_type": export_rt}
        else:
            print(markdown)
            return

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    elif args.human:
        _print_human(result, action)
    else:
        print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()

# CUI // SP-CTI
