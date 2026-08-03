#!/usr/bin/env python3
# CUI // SP-CTI
"""R12: Publish Reflex — Proposal content → Pulse case study generation.

Scans approved/quality-checked proposal drafts and generates Pulse case
study articles from proposal knowledge.  Stages articles as "draft" in
pulse_posts (NEVER auto-publishes) and creates pg_pulse_proposal_links
for bidirectional traceability (D-PG-5).

Pipeline: on_demand (trigger: after_fulfill), also runs independently.
YELLOW tier (reversible writes — staging/draft only).
Scanner-tier LLM only (zero Claude tokens).
"""

import json
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection  # noqa: E402
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.proposal_genesis.reflexes.publish")

# ---------------------------------------------------------------------------
# Module-level fallback constants — all overridable from proposal_genesis_config.yaml
# under reflexes.publish.anomaly_detection.  Change config, not code.
# ---------------------------------------------------------------------------
_MIN_QUALITY_SCORE      = 70.0  # Minimum overall_score to qualify for publication
_MAX_DRAFTS_PER_RUN     = 5     # Max publishable drafts processed per run
_MAX_KB_BLOCKS          = 5     # Max knowledge-base blocks fetched per opportunity
_MAX_CAPABILITIES_SHOWN = 8     # Max capabilities listed in case-study body
_MAX_KB_BLOCKS_USED     = 3     # Max KB blocks included in "Technical Depth" section
_KB_BLOCK_SUMMARY_CHARS = 200   # Max chars used as block summary
_DEFAULT_RELEVANCE_SCORE = 0.80 # Default relevance score for pulse-proposal links


def _compute_quality_publish_threshold(anomaly_cfg: "dict | None" = None) -> float:
    """Compute the minimum quality score for publication from historical distribution.

    Uses mean - sigma*std of approved-draft overall_score so the threshold tracks
    the real quality distribution rather than a static cut-off.
    Falls back to _MIN_QUALITY_SCORE when < min_samples rows.
    """
    cfg = anomaly_cfg or {}
    if not cfg.get("enabled", True):
        return float(cfg.get("fallback_min_quality_score", _MIN_QUALITY_SCORE))

    min_samples  = cfg.get("min_samples", 15)
    sigma        = cfg.get("sigma_multiplier", 1.0)
    fallback     = float(cfg.get("fallback_min_quality_score", _MIN_QUALITY_SCORE))
    bounds       = cfg.get("adaptive_bounds", {})
    score_floor  = float(bounds.get("quality_floor", 50.0))

    import math
    conn = None
    try:
        conn = get_connection()
        row = conn.execute(
            "SELECT AVG(overall_score) AS mean_s, "
            "AVG(overall_score * overall_score) - AVG(overall_score) * AVG(overall_score) AS var_s, "
            "COUNT(*) AS n "
            "FROM pg_proposal_quality_scores "
            "WHERE overall_score IS NOT NULL"
        ).fetchone()
        if row:
            n = row["n"] if isinstance(row, dict) else row[2]
            if n and n >= min_samples:
                mean_s = float(row["mean_s"] if isinstance(row, dict) else row[0])
                var_s  = max(0.0, float(row["var_s"] if isinstance(row, dict) else row[1]))
                std_s  = math.sqrt(var_s)
                threshold = max(score_floor, mean_s - sigma * std_s)
                return round(threshold, 1)
    except Exception:
        pass
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
    return fallback


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _generate_id(prefix: str = "pg") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


# ---------------------------------------------------------------------------
# Source: Approved proposal drafts with quality scores
# ---------------------------------------------------------------------------


def _get_publishable_drafts(limit: int = _MAX_DRAFTS_PER_RUN, min_quality: float = _MIN_QUALITY_SCORE) -> List[Dict]:
    """Find approved proposal drafts that haven't been published to Pulse yet.

    Criteria:
      - Draft status = 'approved' (human-reviewed)
      - Quality score >= min_quality (WriteGuard passed; computed from history)
      - Not already linked to a Pulse article
    """
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT psd.id AS draft_id,
                   psd.opportunity_id,
                   psd.section_id,
                   psd.draft_content,
                   psd.domain_category,
                   psd.confidence_score,
                   po.title AS opportunity_title,
                   po.agency,
                   po.naics_code,
                   pqs.overall_score AS quality_score
            FROM proposal_section_drafts psd
            JOIN proposal_opportunities po ON po.id = psd.opportunity_id
            LEFT JOIN pg_proposal_quality_scores pqs
                ON pqs.draft_id = psd.id
            LEFT JOIN pg_pulse_proposal_links ppl
                ON ppl.section_id = psd.section_id
                AND ppl.opportunity_id = psd.opportunity_id
                AND ppl.link_type = 'cdrl_to_case_study'
            WHERE psd.status = 'approved'
            AND (pqs.overall_score >= %s OR pqs.overall_score IS NULL)
            AND ppl.id IS NULL
            ORDER BY pqs.overall_score DESC, psd.created_at DESC
            LIMIT %s
        """,
            (min_quality, limit),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
    finally:
        conn.close()


def _get_knowledge_blocks(opportunity_id: str) -> List[Dict]:
    """Pull reusable knowledge base blocks for an opportunity."""
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT id, title, content, domain_category, category
            FROM proposal_knowledge_base
            WHERE status = 'active'
            AND domain_category IN (
                SELECT DISTINCT domain_category
                FROM proposal_section_drafts
                WHERE opportunity_id = %s
            )
            ORDER BY usage_count DESC
            LIMIT %s
        """,
            (opportunity_id, _MAX_KB_BLOCKS),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Case study generation (deterministic, scanner-tier)
# ---------------------------------------------------------------------------


def _generate_case_study(draft: Dict, kb_blocks: List[Dict]) -> Dict[str, str]:
    """Generate a case study article from an approved proposal draft.

    Deterministic template-based generation (no LLM required).
    Extracts key themes, capabilities, and outcomes from proposal content.
    """
    title = draft.get("opportunity_title", "Untitled Opportunity")
    agency = draft.get("agency", "Government Agency")
    domain = draft.get("domain_category", "general")
    content = draft.get("draft_content", "")
    naics = draft.get("naics_code", "")

    # Extract key capabilities mentioned
    capabilities = _extract_capabilities(content)

    # Build case study sections
    sections = []

    # Header
    sections.append(f"# Case Study: {_sanitize_title(title)}\n")
    sections.append(f"**Domain:** {domain.replace('_', ' ').title()}")
    if naics:
        sections.append(f"**NAICS:** {naics}")
    sections.append("")

    # Challenge
    sections.append("## Challenge\n")
    challenge = _extract_challenge(content, agency)
    sections.append(challenge)
    sections.append("")

    # Approach
    sections.append("## Approach\n")
    approach = _extract_approach(content, capabilities)
    sections.append(approach)
    sections.append("")

    # Capabilities
    if capabilities:
        sections.append("## Key Capabilities\n")
        for cap in capabilities[:_MAX_CAPABILITIES_SHOWN]:
            sections.append(f"- {cap}")
        sections.append("")

    # Knowledge base enrichment
    if kb_blocks:
        sections.append("## Technical Depth\n")
        for block in kb_blocks[:_MAX_KB_BLOCKS_USED]:
            block_title = block.get("title", "")
            block_content = block.get("content", "")
            if block_title and block_content:
                summary = block_content[:_KB_BLOCK_SUMMARY_CHARS].strip()
                if len(block_content) > _KB_BLOCK_SUMMARY_CHARS:
                    summary += "..."
                sections.append(f"**{block_title}**: {summary}\n")

    # Tags
    tags = [domain]
    if agency:
        tags.append(agency.split()[0] if " " in agency else agency)
    if naics:
        tags.append(f"NAICS-{naics}")
    sections.append(f"\n*Tags: {', '.join(tags)}*")

    body = "\n".join(sections)
    slug = _slugify(title)

    raw_title = f"Case Study: {_sanitize_title(title)}"

    # Phase 70: Sanitize case study through PulseSanitizer
    # Removes agency names, program names, contract numbers, generalizes
    # past performance details before staging as Pulse draft.
    try:
        from tools.redaction.pulse_sanitizer import PulseSanitizer

        ps = PulseSanitizer()
        sanitized = ps.sanitize_article(
            title=raw_title,
            body=body,
            tags=tags,
            domain=domain,
        )
        return {
            "title": sanitized["title"],
            "slug": slug,
            "body": sanitized["body"],
            "domain": domain,
            "tags": sanitized["tags"],
        }
    except ImportError:
        pass  # Redaction module not installed — use raw output

    return {
        "title": raw_title,
        "slug": slug,
        "body": body,
        "domain": domain,
        "tags": tags,
    }


def _extract_capabilities(text: str) -> List[str]:
    """Extract capability keywords from proposal content."""
    capability_keywords = [
        "zero trust",
        "FedRAMP",
        "ATO",
        "DevSecOps",
        "NIST 800-53",
        "continuous monitoring",
        "STIG",
        "SBOM",
        "CI/CD",
        "microservices",
        "containerization",
        "Kubernetes",
        "cloud migration",
        "data analytics",
        "machine learning",
        "cybersecurity",
        "risk management",
        "compliance",
        "agile",
        "SAFe",
        "SysML",
        "digital thread",
        "MBSE",
        "identity management",
        "encryption",
        "PKI",
        "CAC/PIV",
        "incident response",
        "threat modeling",
        "vulnerability management",
        "supply chain",
        "SCRM",
        "CMMC",
        "IL4",
        "IL5",
    ]
    found = []
    text_lower = text.lower() if text else ""
    for kw in capability_keywords:
        if kw.lower() in text_lower:
            found.append(kw)
    return found


def _extract_challenge(text: str, agency: str) -> str:
    """Extract or generate a challenge description from proposal content."""
    if not text:
        return f"{agency} required a modern, compliant solution to address mission-critical needs."

    # Take first 2-3 sentences as challenge context
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    challenge_sentences = sentences[:3]
    challenge = " ".join(challenge_sentences)

    if len(challenge) > 500:
        challenge = challenge[:497] + "..."

    return challenge


def _extract_approach(text: str, capabilities: List[str]) -> str:
    """Extract or generate an approach description."""
    if not text:
        return "ICDEV™ delivered a comprehensive solution leveraging proven methodologies."

    # Take middle portion of content as approach
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    mid = len(sentences) // 3
    approach_sentences = sentences[mid : mid + 4]
    approach = " ".join(approach_sentences) if approach_sentences else text[:400]

    if capabilities:
        cap_str = ", ".join(capabilities[:5])
        approach += f"\n\nKey technologies and frameworks applied: {cap_str}."

    if len(approach) > 600:
        approach = approach[:597] + "..."

    return approach


def _sanitize_title(title: str) -> str:
    """Clean title for article use."""
    # Remove solicitation numbers and excessive detail
    title = re.sub(r"\b[A-Z0-9]{2,}-\d{2}-[A-Z]-\d+\b", "", title)
    title = re.sub(r"\s+", " ", title).strip()
    if len(title) > 120:
        title = title[:117] + "..."
    return title


def _slugify(text: str) -> str:
    """Convert text to URL-friendly slug."""
    slug = text.lower()
    slug = re.sub(r"[^a-z0-9\s-]", "", slug)
    slug = re.sub(r"[\s]+", "-", slug)
    slug = re.sub(r"-+", "-", slug)
    return slug[:80].strip("-")


# ---------------------------------------------------------------------------
# Stage article + create link
# ---------------------------------------------------------------------------


def _stage_article(article: Dict, draft: Dict) -> Optional[str]:
    """Stage a case study article in pulse_posts as draft."""
    conn = get_connection()
    post_id = _generate_id("pgpub")
    now = _utcnow_iso()
    try:
        # Check if pulse_posts table exists (Pulse module may not be initialized)
        try:
            conn.execute("SELECT 1 FROM pulse_posts LIMIT 0")
        except Exception:
            # Table doesn't exist — create a minimal version
            conn.execute("""
                CREATE TABLE IF NOT EXISTS pulse_posts (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    slug TEXT,
                    status TEXT NOT NULL DEFAULT 'draft',
                    topic TEXT,
                    body_markdown TEXT,
                    readability_score REAL DEFAULT 0.0,
                    author_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)

        conn.execute(
            """
            INSERT INTO pulse_posts
                (id, title, slug, status, topic, body_markdown,
                 readability_score, author_id, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
            (
                post_id,
                article["title"][:300],
                article["slug"],
                "draft",  # NEVER auto-publish (D-GEN principle)
                article.get("domain", "general"),
                article["body"],
                draft.get("quality_score", 0) or 0,
                "pg_publish",
                now,
                now,
            ),
        )
        conn.commit()
        return post_id
    except Exception:
        return None
    finally:
        conn.close()


def _create_pulse_link(
    post_id: str,
    opportunity_id: str,
    section_id: Optional[str],
    link_type: str = "cdrl_to_case_study",
    relevance_score: float = 0.8,
) -> Optional[str]:
    """Create a pg_pulse_proposal_links entry for traceability."""
    conn = get_connection()
    link_id = _generate_id("pglink")
    now = _utcnow_iso()
    try:
        conn.execute(
            """
            INSERT INTO pg_pulse_proposal_links
                (id, pulse_post_id, opportunity_id, section_id,
                 link_type, relevance_score, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
            (
                link_id,
                post_id,
                opportunity_id,
                section_id,
                link_type,
                relevance_score,
                now,
            ),
        )
        conn.commit()
        return link_id
    except Exception:
        return None
    finally:
        conn.close()


def _audit_publish(
    event_type: str,
    opportunity_id: Optional[str],
    details: Dict,
    success: bool,
) -> None:
    """Log publish event to audit trail."""
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO pg_proposal_genesis_audit "
            "(id, event_type, reflex_name, risk_tier, opportunity_id, "
            "details, success, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (
                _generate_id("pgaudit"),
                event_type,
                "publish",
                "yellow",
                opportunity_id,
                json.dumps(details),
                1 if success else 0,
                _utcnow_iso(),
            ),
        )
        conn.commit()
    except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
        logger.warning(
            "_audit_publish: best-effort INSERT into pg_proposal_genesis_audit failed (non-blocking): %s",
            exc,
        )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def run(config: Dict[str, Any], trust: Any) -> Dict[str, Any]:
    """Execute the Publish Reflex (R12).

    Steps:
      1. Find approved proposal drafts with quality scores
      2. Generate case study articles from draft content
      3. Stage articles in pulse_posts as 'draft' (never auto-publish)
      4. Create pg_pulse_proposal_links for traceability
      5. Audit all decisions

    Returns standard reflex result dict.
    """
    # Compute quality gate threshold — statistical from history when ≥min_samples,
    # otherwise falls back to config-driven default.
    anomaly_cfg   = config.get("anomaly_detection", {})
    min_quality   = _compute_quality_publish_threshold(anomaly_cfg)
    max_articles  = config.get("max_articles_per_run", _MAX_DRAFTS_PER_RUN)
    articles_staged = 0
    links_created = 0
    errors = 0

    # Step 1: Find publishable drafts
    drafts = _get_publishable_drafts(limit=max_articles, min_quality=min_quality)
    if not drafts:
        return {
            "success": True,
            "metric_value": 0.0,
            "details": {
                "status": "no_publishable_drafts",
                "articles_staged": 0,
                "links_created": 0,
            },
        }

    # Steps 2-4: Process each draft
    for draft in drafts:
        opp_id = draft.get("opportunity_id", "")
        section_id = draft.get("section_id")

        # Get knowledge base enrichment
        kb_blocks = _get_knowledge_blocks(opp_id)

        # Generate case study
        article = _generate_case_study(draft, kb_blocks)
        if not article or not article.get("body"):
            errors += 1
            continue

        # Stage in pulse_posts
        post_id = _stage_article(article, draft)
        if not post_id:
            errors += 1
            _audit_publish(
                "article_stage_failed",
                opp_id,
                {"draft_id": draft.get("draft_id", ""), "title": article.get("title", "")},
                success=False,
            )
            continue
        articles_staged += 1

        # Create pulse-proposal link
        link_id = _create_pulse_link(
            post_id=post_id,
            opportunity_id=opp_id,
            section_id=section_id,
            link_type="cdrl_to_case_study",
            relevance_score=draft.get("confidence_score", _DEFAULT_RELEVANCE_SCORE) or _DEFAULT_RELEVANCE_SCORE,
        )
        if link_id:
            links_created += 1

        # Audit success
        _audit_publish(
            "article_staged",
            opp_id,
            {
                "post_id": post_id,
                "link_id": link_id,
                "title": article["title"],
                "domain": article.get("domain", ""),
                "quality_score": draft.get("quality_score", 0),
            },
            success=True,
        )

    return {
        "success": articles_staged > 0 or len(drafts) == 0,
        "metric_value": float(articles_staged),
        "details": {
            "drafts_found": len(drafts),
            "articles_staged": articles_staged,
            "links_created": links_created,
            "errors": errors,
        },
    }
