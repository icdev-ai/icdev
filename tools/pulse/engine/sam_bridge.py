# CUI // SP-CTI
"""SAM.gov to Pulse Article Bridge — extracts pain points from federal
solicitations and generates evergreen Pulse blog articles.

Pipeline:
    1. Query sam_gov_opportunities for recent unprocessed opportunities
    2. Extract pain points via regex + keyword matching (deterministic)
    3. Classify pain points by domain (reuses govcon _DOMAIN_KEYWORDS)
    4. Cluster into article topics (one per domain per opportunity)
    5. Deduplicate against existing pulse_posts and pulse_sam_article_log
    6. Strip specific identifiers (solicitation #, agency, due date)
    7. Feed into run_full_pipeline(topic_override=..., template_type="challenge_solution")
    8. Log to pulse_sam_article_log (append-only, NIST AU)

Design decisions:
    D-SAM-PULSE-1: Bridge in tools/pulse/engine/ (produces Pulse content, reads GovCon tables)
    D-SAM-PULSE-2: Extraction is deterministic regex + keyword (zero LLM)
    D-SAM-PULSE-3: pulse_sam_article_log (append-only) tracks opportunity -> article mapping
    D-SAM-PULSE-4: Articles are generic/evergreen — strips sol numbers, agency names, dates
    D-SAM-PULSE-5: One opportunity can spawn multiple articles (one per domain category)

Usage:
    python tools/pulse/engine/sam_bridge.py --run --json
    python tools/pulse/engine/sam_bridge.py --dry-run --json
    python tools/pulse/engine/sam_bridge.py --list-pending --json
    python tools/pulse/engine/sam_bridge.py --stats --json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

# ── Project imports ───────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.logging.icdev_logger import get_logger  # noqa: E402

try:
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env")
except ImportError:
    pass

from tools.db.storage import get_connection  # noqa: E402
from tools.pulse.db import (  # noqa: E402
    get_existing_titles,
    init_db,
    insert_row,
    update_row,
)

logger = get_logger("pulse.sam_bridge")

# ── Domain keywords (reused from govcon/knowledge_ingestion.py) ───────
_DOMAIN_KEYWORDS: dict[str, list[str]] = {
    "devsecops": [
        "devsecops",
        "ci/cd",
        "pipeline",
        "jenkins",
        "gitlab",
        "devops",
        "continuous integration",
        "continuous delivery",
        "container",
    ],
    "ai_ml": [
        "artificial intelligence",
        "machine learning",
        "ai",
        "ml",
        "neural network",
        "deep learning",
        "nlp",
        "llm",
        "model training",
    ],
    "ato_rmf": [
        "ato",
        "rmf",
        "authority to operate",
        "risk management framework",
        "authorization boundary",
        "isso",
        "issm",
        "accreditation",
    ],
    "cloud": [
        "cloud",
        "aws",
        "azure",
        "govcloud",
        "saas",
        "paas",
        "iaas",
        "serverless",
        "microservice",
        "kubernetes",
    ],
    "security": [
        "security",
        "cybersecurity",
        "stig",
        "vulnerability",
        "penetration",
        "zero trust",
        "encryption",
        "fips",
        "cac",
        "pki",
    ],
    "compliance": [
        "compliance",
        "fedramp",
        "nist",
        "cmmc",
        "800-53",
        "800-171",
        "control",
        "audit",
        "assessment",
    ],
    "data": [
        "data",
        "database",
        "analytics",
        "etl",
        "data lake",
        "warehouse",
        "migration",
        "schema",
        "api",
        "integration",
    ],
    "management": [
        "management",
        "pmo",
        "schedule",
        "milestone",
        "wbs",
        "earned value",
        "risk register",
        "staffing plan",
    ],
}


# ── Configuration ─────────────────────────────────────────────────────


def _load_sam_bridge_config() -> dict:
    """Load sam_bridge config from args/pulse_templates.yaml."""
    import yaml

    config_path = PROJECT_ROOT / "args" / "pulse_templates.yaml"
    if not config_path.exists():
        logger.warning("pulse_templates.yaml not found, using defaults")
        return {"enabled": False}

    with open(config_path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    return data.get("sam_bridge", {"enabled": False})


# ── Opportunity query ─────────────────────────────────────────────────


def _get_unprocessed_opportunities(
    days_back: int = 30,
    filters: dict | None = None,
) -> list[dict]:
    """Query sam_gov_opportunities not yet in pulse_sam_article_log.

    Uses LEFT JOIN to find opportunities that haven't been processed yet.
    Applies optional filters: naics_codes, keywords, agencies, notice_types.
    """
    filters = filters or {}

    with get_connection() as conn:
        # Check if sam_gov_opportunities table exists
        try:
            conn.execute("SELECT 1 FROM sam_gov_opportunities LIMIT 1").fetchone()
        except Exception:
            logger.warning("sam_gov_opportunities table not found — run SAM scanner first")
            return []

        # Base query: opportunities not already in the article log.
        # Use title + description combined length since SAM API often stores
        # description as a URL reference rather than full text.
        min_len = filters.get("min_description_length", 200)
        sql = """
            SELECT o.*
            FROM sam_gov_opportunities o
            LEFT JOIN pulse_sam_article_log a ON o.id = a.sam_opportunity_id
            WHERE a.id IS NULL
              AND (LENGTH(COALESCE(o.description, '')) >= ?
                   OR LENGTH(COALESCE(o.title, '')) >= 30)
        """
        params: list = [min_len]

        # Notice type filter — match both short codes AND full names
        notice_types = filters.get("notice_types", "")
        if notice_types:
            type_list = [t.strip() for t in notice_types.split(",") if t.strip()]
            if type_list:
                # Map short codes to full SAM.gov names
                code_to_name = {
                    "o": "Solicitation",
                    "p": "Presolicitation",
                    "r": "Sources Sought",
                    "s": "Special Notice",
                    "k": "Combined Synopsis/Solicitation",
                }
                expanded = []
                for t in type_list:
                    expanded.append(t)  # keep original
                    if t in code_to_name:
                        expanded.append(code_to_name[t])
                placeholders = ", ".join(["?"] * len(expanded))
                sql += f" AND o.notice_type IN ({placeholders})"
                params.extend(expanded)

        # NAICS filter
        naics_codes = filters.get("naics_codes", [])
        if naics_codes:
            placeholders = ", ".join(["?"] * len(naics_codes))
            sql += f" AND o.naics_code IN ({placeholders})"
            params.extend(naics_codes)

        # Agency filter
        agencies = filters.get("agencies", [])
        for agency in agencies:
            sql += " AND LOWER(o.agency) LIKE ?"
            params.append(f"%{agency.lower()}%")

        # Keyword filter on title + description
        keywords = filters.get("keywords", [])
        for kw in keywords:
            sql += " AND (LOWER(o.title) LIKE ? OR LOWER(o.description) LIKE ?)"
            params.extend([f"%{kw.lower()}%", f"%{kw.lower()}%"])

        sql += " ORDER BY o.posted_date DESC LIMIT 100"

        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]


# ── Pain point extraction (deterministic, zero LLM) ──────────────────


def _classify_domain(text: str) -> str:
    """Classify a sentence into a domain using word-boundary keyword matching."""
    scores: dict[str, int] = {}

    for domain, keywords in _DOMAIN_KEYWORDS.items():
        count = sum(1 for kw in keywords if re.search(r"\b" + re.escape(kw) + r"\b", text, re.IGNORECASE))
        if count > 0:
            scores[domain] = count

    if not scores:
        return "general"
    return max(scores, key=scores.get)  # type: ignore[arg-type]


def _extract_pain_points(description: str, config: dict) -> list[dict]:
    """Extract pain points from opportunity description.

    Strategy:
      1. Split description into sentences
      2. Match shall-statement patterns (shall, must, required to, etc.)
      3. Score each sentence against challenge_keywords
      4. Group matched sentences by domain_category
      5. Return groups with >= 2 matched sentences as pain points
    """
    extraction = config.get("extraction", {})
    shall_patterns = extraction.get(
        "shall_patterns",
        [
            r"shall\b",
            r"must\b",
            r"required to\b",
            r"contractor will\b",
        ],
    )
    challenge_keywords = extraction.get(
        "challenge_keywords",
        [
            "cybersecurity",
            "compliance",
            "authorization",
            "zero trust",
            "DevSecOps",
            "FedRAMP",
            "CMMC",
            "ATO",
            "automation",
            "modernization",
            "cloud",
            "migration",
            "security",
            "software",
            "data center",
            "network",
            "IT services",
            "enterprise",
            "infrastructure",
            "digital",
            "analytics",
            "intelligence",
            "surveillance",
            "logistics",
            "supply chain",
            "artificial intelligence",
            "machine learning",
            "SBOM",
            "vulnerability",
            "encryption",
            "STIG",
            "kubernetes",
            "container",
            "microservice",
            "API",
            "legacy system",
            "agile",
            "DevOps",
        ],
    )

    # Split into sentences
    sentences = re.split(r"[.!?]+", description)

    # For short texts (titles), lower the minimum sentence length
    min_sent_len = 15 if len(description) < 300 else 30

    scored: list[dict] = []
    for sent in sentences:
        sent = sent.strip()
        if len(sent) < min_sent_len:
            continue

        # Check shall-patterns
        has_shall = any(re.search(p, sent, re.IGNORECASE) for p in shall_patterns)

        # Check challenge keywords (word-boundary match to avoid
        # false positives like "AI" matching "affairs" or "NAICS")
        matched_kw = [kw for kw in challenge_keywords if re.search(r"\b" + re.escape(kw) + r"\b", sent, re.IGNORECASE)]

        # For short texts, also accept domain-keyword matches as sufficient
        domain_hit = _classify_domain(sent) != "general" if not matched_kw else False

        if has_shall or len(matched_kw) >= 1 or domain_hit:
            domain = _classify_domain(sent)
            scored.append(
                {
                    "text": sent,
                    "domain": domain,
                    "keywords": matched_kw,
                    "has_shall": has_shall,
                }
            )

    # Group by domain
    by_domain: dict[str, list[dict]] = defaultdict(list)
    for s in scored:
        by_domain[s["domain"]].append(s)

    pain_points: list[dict] = []
    # Accept 1 matched sentence per domain — the old threshold of 2 was
    # filtering out too many legitimate opportunities.
    for domain, items in by_domain.items():
        if len(items) >= 1:
            pain_points.append(
                {
                    "text": " ".join(i["text"] for i in items[:5]),
                    "domain": domain,
                    "keywords": list({kw for i in items for kw in i["keywords"]}),
                    "sentence_count": len(items),
                }
            )

    return pain_points


# ── Generalization (strip specifics, create evergreen topic) ──────────


def _generalize_topic(pain_point: dict, angles: dict) -> str:
    """Convert specific RFP pain point into generic/evergreen article topic.

    Strips solicitation numbers, agency names, dates.
    Uses domain_angles mapping as a base, then appends unique keywords so
    that each topic is distinct enough to survive dedup.
    """
    domain = pain_point.get("domain", "general")
    keywords = pain_point.get("keywords", [])

    # Use the configured angle for this domain
    angle = angles.get(domain, "Federal IT Modernization Challenges")

    # Build a descriptive topic from the angle + top keywords.
    # IMPORTANT: Always append keywords so topics are unique per opportunity.
    # Without this, every cloud opp maps to the same generic angle string
    # and the dedup layer kills them all after the first one.
    if keywords:
        # Pick up to 3 unique keywords that aren't already in the angle
        angle_lower = angle.lower()
        # Filter out very short keywords (< 3 chars) that add no specificity
        unique_kw = [kw for kw in keywords if kw.lower() not in angle_lower and len(kw) >= 3][:3]
        if unique_kw:
            return f"{angle}: {', '.join(kw.title() for kw in unique_kw)}"

    # If no unique keywords, append a hash fragment so the topic is still
    # distinct from the base angle (prevents false dedup).
    text_hash = hashlib.sha256(pain_point.get("text", "")[:200].encode()).hexdigest()[:6]
    return f"{angle} [{text_hash}]"


# ── Deduplication (3-layer, matches existing Pulse patterns) ──────────


def _content_hash(pain_points: list[dict]) -> str:
    """SHA-256 hash of extracted pain points for exact dedup."""
    canonical = json.dumps(
        sorted(
            [{"d": p["domain"], "k": sorted(p.get("keywords", []))} for p in pain_points],
            key=lambda x: x["d"],
        ),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def _check_duplicate(
    topic: str,
    content_hash_val: str,
    config: dict,
) -> tuple[bool, str]:
    """Check if this topic already exists.

    Three-layer dedup (matches existing Pulse patterns):
      Layer 1: Content hash match in pulse_sam_article_log (exact dedup)
      Layer 2: Title word overlap against existing pulse_posts (>60% = dup)
      Layer 3: Existing titles injected into LLM context (handled by pipeline)
    """
    dedup_cfg = config.get("dedup", {})
    threshold = dedup_cfg.get("similarity_threshold", 0.6)

    with get_connection() as conn:
        # Layer 1: Exact content hash match
        try:
            row = conn.execute(
                "SELECT id FROM pulse_sam_article_log WHERE content_hash = %s",
                (content_hash_val,),
            ).fetchone()
            if row:
                return True, f"content_hash_match:{row['id']}"
        except Exception:
            pass  # Table may not exist yet

        # Also check article_topic for very similar topics — but ONLY against
        # entries that actually produced articles (drafted/published).  Comparing
        # against skipped/pending entries causes cascading false positives since
        # many share the same generic domain angle.
        try:
            existing_topics = conn.execute(
                "SELECT article_topic FROM pulse_sam_article_log WHERE pipeline_status IN ('drafted', 'published')"
            ).fetchall()
            for et in existing_topics:
                if et["article_topic"] and _word_overlap(topic, et["article_topic"]) > threshold:
                    return True, f"sam_topic_overlap:{et['article_topic']}"
        except Exception:
            pass

    # Layer 2: Title word overlap against existing pulse_posts
    existing_titles = get_existing_titles()
    for title in existing_titles:
        if _word_overlap(topic, title) > threshold:
            return True, f"title_overlap:{title}"

    return False, ""


def _word_overlap(a: str, b: str) -> float:
    """Compute word overlap ratio between two strings (words > 4 chars)."""
    words_a = {w.lower() for w in a.split() if len(w) > 4}
    words_b = {w.lower() for w in b.split() if len(w) > 4}
    if not words_a or not words_b:
        return 0.0
    overlap = words_a & words_b
    return len(overlap) / min(len(words_a), len(words_b))


# ── Cluster builder ───────────────────────────────────────────────────


def _build_cluster(topic: str, pain_points: list[dict], domain: str) -> dict:
    """Build a cluster dict compatible with run_full_pipeline()."""
    return {
        "id": f"sam-{uuid4().hex[:8]}",
        "name": topic,
        "description": topic,
        "pain_points": json.dumps([p.get("text", "")[:200] for p in pain_points]),
        "research_ids": json.dumps([]),
        "priority_score": 0.8,
        "used_count": 0,
        "source_urls": [],
        "source": "sam_gov",
        "domain": domain,
    }


# ── Main orchestrator ─────────────────────────────────────────────────


def run_sam_to_pulse(
    dry_run: bool = False,
    max_articles: int | None = None,
) -> dict:
    """Main entry point: scan opportunities -> extract -> dedupe -> generate articles.

    Args:
        dry_run: If True, extract and classify but don't generate articles.
        max_articles: Maximum articles to generate in this run (overrides config).

    Returns:
        Dict with: status, articles_generated, articles_skipped,
        opportunities_processed, details[].
    """
    config = _load_sam_bridge_config()
    if not config.get("enabled", False):
        return {"status": "disabled", "message": "sam_bridge.enabled is false"}

    init_db()

    # Backfill URL-only / short descriptions before processing so extraction
    # has the richest possible text to work with.
    try:
        from tools.govcon.sam_scanner import backfill_descriptions

        bf_result = backfill_descriptions(limit=50, delay=0.3)
        logger.info(
            "Description backfill: %d updated, %d checked",
            bf_result.get("updated_count", 0),
            bf_result.get("total_checked", 0),
        )
    except Exception as exc:
        logger.warning("Description backfill skipped: %s", exc)

    if max_articles is None:
        max_articles = config.get("max_articles_per_run", 5)

    filters = config.get("filters", {})
    filters["min_description_length"] = config.get("min_description_length", 200)
    angles = config.get("domain_angles", {})
    template_type = config.get("template_type", "challenge_solution")
    auto_rewrite = config.get("auto_rewrite", True)

    # Step 1: Get unprocessed opportunities
    opportunities = _get_unprocessed_opportunities(filters=filters)
    logger.info("Found %d unprocessed SAM.gov opportunities", len(opportunities))

    if not opportunities:
        return {
            "status": "ok",
            "message": "No unprocessed opportunities found",
            "articles_generated": 0,
            "articles_skipped": 0,
            "opportunities_processed": 0,
            "details": [],
        }

    details: list[dict] = []
    articles_generated = 0
    articles_skipped = 0
    now = datetime.now(timezone.utc).isoformat()

    for opp in opportunities:
        opp_id = opp.get("id", "")
        opp_title = opp.get("title", "")
        description = opp.get("description", "")

        # Build extraction text from available fields.
        # SAM API often stores description as a URL reference, so fall back
        # to title + agency + naics for domain signal.
        desc_is_url = (description or "").startswith("http")
        if desc_is_url or not description or len(description) < 50:
            # Compose synthetic text from structured fields
            parts = [opp_title or ""]
            agency = opp.get("agency", "") or ""
            if agency:
                parts.append(f"Agency: {agency}")
            naics = opp.get("naics_code", "") or ""
            if naics:
                parts.append(f"NAICS: {naics}")
            set_aside = opp.get("set_aside_type", "") or ""
            if set_aside:
                parts.append(f"Set-aside: {set_aside}")
            extraction_text = ". ".join(parts)
        else:
            extraction_text = description

        if len(extraction_text) < 30:
            details.append(
                {
                    "opportunity_id": opp_id,
                    "title": opp_title,
                    "status": "skipped",
                    "reason": "insufficient_content",
                }
            )
            continue

        # Step 2: Extract pain points
        pain_points = _extract_pain_points(extraction_text, config)

        # Fallback: if extraction found nothing, try classifying title alone.
        # Many valid opps (e.g. "Post Quantum Cryptography Support") have
        # short descriptions but obviously relevant titles.
        if not pain_points and opp_title:
            title_domain = _classify_domain(opp_title)
            if title_domain != "general":
                pain_points = [
                    {
                        "text": opp_title,
                        "domain": title_domain,
                        "keywords": [opp_title],
                        "sentence_count": 1,
                    }
                ]

        if not pain_points:
            # Only log to DB on real runs — dry-runs must not consume opps
            if not dry_run:
                insert_row(
                    "sam_article_log",
                    {
                        "id": f"sal-{uuid4().hex[:12]}",
                        "sam_opportunity_id": opp_id,
                        "opportunity_title": opp_title[:200] if opp_title else "",
                        "domain_category": "",
                        "extracted_pain_points": json.dumps([]),
                        "article_topic": "",
                        "pipeline_status": "skipped",
                        "skip_reason": "no_pain_points_extracted",
                        "content_hash": hashlib.sha256(description[:500].encode()).hexdigest()[:16],
                        "created_at": now,
                    },
                )
            details.append(
                {
                    "opportunity_id": opp_id,
                    "title": opp_title,
                    "status": "skipped",
                    "reason": "no_pain_points_extracted",
                }
            )
            articles_skipped += 1
            continue

        # Step 2b: Demand signal detection (D-PULSE-CAP-2/3)
        try:
            from tools.pulse.engine.demand_detector import detect_demand_signals

            flat_keywords = []
            for pp_item in pain_points:
                if isinstance(pp_item, dict):
                    flat_keywords.extend(pp_item.get("keywords", []))
                elif isinstance(pp_item, str):
                    flat_keywords.append(pp_item)
            detect_demand_signals(flat_keywords, opp_id, opp_title or "")
        except Exception as e:
            logger.warning("Demand detection skipped for %s: %s", opp_id, e)

        # Step 3: Process each domain group as a potential article
        for pp in pain_points:
            if articles_generated >= max_articles:
                break

            domain = pp["domain"]
            topic = _generalize_topic(pp, angles)
            c_hash = _content_hash([pp])

            # Step 4: Dedup check (3-layer)
            is_dup, dup_reason = _check_duplicate(topic, c_hash, config)
            if is_dup:
                if not dry_run:
                    insert_row(
                        "sam_article_log",
                        {
                            "id": f"sal-{uuid4().hex[:12]}",
                            "sam_opportunity_id": opp_id,
                            "opportunity_title": opp_title[:200] if opp_title else "",
                            "domain_category": domain,
                            "extracted_pain_points": json.dumps(pp.get("keywords", [])),
                            "article_topic": topic,
                            "pipeline_status": "skipped",
                            "skip_reason": f"duplicate:{dup_reason}",
                            "content_hash": c_hash,
                            "created_at": now,
                        },
                    )
                details.append(
                    {
                        "opportunity_id": opp_id,
                        "title": opp_title,
                        "topic": topic,
                        "domain": domain,
                        "status": "skipped",
                        "reason": f"duplicate:{dup_reason}",
                    }
                )
                articles_skipped += 1
                continue

            # Step 5: Log as pending
            log_id = f"sal-{uuid4().hex[:12]}"
            insert_row(
                "sam_article_log",
                {
                    "id": log_id,
                    "sam_opportunity_id": opp_id,
                    "opportunity_title": opp_title[:200] if opp_title else "",
                    "domain_category": domain,
                    "extracted_pain_points": json.dumps(pp.get("keywords", [])),
                    "article_topic": topic,
                    "pipeline_status": "pending",
                    "content_hash": c_hash,
                    "created_at": now,
                },
            )

            if dry_run:
                details.append(
                    {
                        "opportunity_id": opp_id,
                        "title": opp_title,
                        "topic": topic,
                        "domain": domain,
                        "keywords": pp.get("keywords", []),
                        "sentence_count": pp.get("sentence_count", 0),
                        "status": "pending",
                        "log_id": log_id,
                        "dry_run": True,
                    }
                )
                continue

            # Step 6: Generate article via existing Pulse pipeline
            logger.info(
                "Generating article: %s (domain=%s, opp=%s)",
                topic,
                domain,
                opp_id,
            )
            try:
                from tools.pulse.engine.scheduler import run_full_pipeline

                result = run_full_pipeline(
                    topic_override=topic,
                    template_type=template_type,
                    auto_rewrite=auto_rewrite,
                )

                post_id = result.get("post_id", "")
                pipeline_status = "drafted" if result.get("status") in ("ok", "completed") else "failed"

                update_row(
                    "sam_article_log",
                    log_id,
                    {
                        "post_id": post_id,
                        "pipeline_status": pipeline_status,
                    },
                )

                details.append(
                    {
                        "opportunity_id": opp_id,
                        "title": opp_title,
                        "topic": topic,
                        "domain": domain,
                        "status": pipeline_status,
                        "post_id": post_id,
                        "log_id": log_id,
                        "quality_score": result.get("quality_score"),
                        "writeguard_passed": result.get("writeguard_passed"),
                        "word_count": result.get("word_count"),
                    }
                )
                articles_generated += 1

            except Exception as exc:
                logger.error("Pipeline failed for topic '%s': %s", topic, exc)
                update_row(
                    "sam_article_log",
                    log_id,
                    {
                        "pipeline_status": "failed",
                        "skip_reason": f"pipeline_error:{exc}",
                    },
                )
                details.append(
                    {
                        "opportunity_id": opp_id,
                        "title": opp_title,
                        "topic": topic,
                        "domain": domain,
                        "status": "failed",
                        "error": str(exc),
                        "log_id": log_id,
                    }
                )

        if articles_generated >= max_articles:
            logger.info("Reached max_articles limit (%d)", max_articles)
            break

    return {
        "status": "ok",
        "dry_run": dry_run,
        "articles_generated": articles_generated,
        "articles_skipped": articles_skipped,
        "opportunities_processed": len(opportunities),
        "template_type": template_type,
        "details": details,
        "classification": "CUI",
    }


# ── Query helpers ─────────────────────────────────────────────────────


def get_pending_topics() -> dict:
    """List extracted topics that haven't been turned into articles yet."""
    with get_connection() as conn:
        try:
            rows = conn.execute(
                "SELECT id, sam_opportunity_id, opportunity_title, domain_category, "
                "article_topic, extracted_pain_points, created_at "
                "FROM pulse_sam_article_log "
                "WHERE pipeline_status = 'pending' "
                "ORDER BY created_at DESC"
            ).fetchall()
            return {
                "status": "ok",
                "count": len(rows),
                "topics": [dict(r) for r in rows],
            }
        except Exception:
            return {"status": "ok", "count": 0, "topics": []}


def process_pending(max_articles: int | None = None) -> dict:
    """Process existing pending entries through the Pulse pipeline.

    Unlike run_sam_to_pulse (which discovers NEW opportunities), this
    function picks up entries already in pulse_sam_article_log with
    pipeline_status='pending' and pushes them through run_full_pipeline.
    """
    config = _load_sam_bridge_config()
    template_type = config.get("template_type", "challenge_solution")
    auto_rewrite = config.get("auto_rewrite", True)

    init_db()

    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, sam_opportunity_id, opportunity_title, "
            "domain_category, article_topic "
            "FROM pulse_sam_article_log "
            "WHERE pipeline_status = 'pending' "
            "ORDER BY created_at ASC"
        ).fetchall()

    pending = [dict(r) for r in rows]
    if max_articles:
        pending = pending[:max_articles]

    if not pending:
        return {"status": "ok", "message": "No pending items", "articles_generated": 0}

    details: list[dict] = []
    generated = 0

    for entry in pending:
        log_id = entry["id"]
        topic = entry["article_topic"]
        domain = entry["domain_category"]

        if not topic:
            logger.warning("Pending entry %s has no topic — skipping", log_id)
            continue

        logger.info("Processing pending: %s (domain=%s)", topic, domain)
        try:
            from tools.pulse.engine.scheduler import run_full_pipeline

            result = run_full_pipeline(
                topic_override=topic,
                template_type=template_type,
                auto_rewrite=auto_rewrite,
            )

            post_id = result.get("post_id", "")
            status = "drafted" if result.get("status") in ("ok", "completed") else "failed"

            update_row(
                "sam_article_log",
                log_id,
                {
                    "post_id": post_id,
                    "pipeline_status": status,
                },
            )

            details.append(
                {
                    "log_id": log_id,
                    "topic": topic,
                    "domain": domain,
                    "status": status,
                    "post_id": post_id,
                    "quality_score": result.get("quality_score"),
                    "writeguard_passed": result.get("writeguard_passed"),
                    "word_count": result.get("word_count"),
                }
            )
            generated += 1

        except Exception as exc:
            logger.error("Pipeline failed for pending '%s': %s", topic, exc)
            update_row(
                "sam_article_log",
                log_id,
                {
                    "pipeline_status": "failed",
                    "skip_reason": f"pipeline_error:{exc}",
                },
            )
            details.append(
                {
                    "log_id": log_id,
                    "topic": topic,
                    "status": "failed",
                    "error": str(exc),
                }
            )

    return {
        "status": "ok",
        "articles_generated": generated,
        "total_pending": len(pending),
        "details": details,
        "classification": "CUI",
    }


def get_stats() -> dict:
    """Statistics on SAM-to-Pulse pipeline."""
    with get_connection() as conn:
        try:
            total = conn.execute("SELECT COUNT(*) as n FROM pulse_sam_article_log").fetchone()["n"]

            by_status = conn.execute(
                "SELECT pipeline_status, COUNT(*) as n FROM pulse_sam_article_log GROUP BY pipeline_status"
            ).fetchall()

            by_domain = conn.execute(
                "SELECT domain_category, COUNT(*) as n "
                "FROM pulse_sam_article_log "
                "WHERE pipeline_status IN ('drafted', 'published') "
                "GROUP BY domain_category"
            ).fetchall()

            return {
                "status": "ok",
                "total_processed": total,
                "by_status": {r["pipeline_status"]: r["n"] for r in by_status},
                "by_domain": {r["domain_category"]: r["n"] for r in by_domain},
                "classification": "CUI",
            }
        except Exception:
            return {
                "status": "ok",
                "total_processed": 0,
                "by_status": {},
                "by_domain": {},
            }


# ── CLI ───────────────────────────────────────────────────────────────


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="SAM.gov to Pulse Article Bridge (CUI // SP-CTI)")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--run", action="store_true", help="Run pipeline")
    group.add_argument(
        "--process-pending", action="store_true", help="Process existing pending entries through pipeline"
    )
    group.add_argument("--dry-run", action="store_true", help="Extract without generating")
    group.add_argument("--list-pending", action="store_true", help="List pending topics")
    group.add_argument("--stats", action="store_true", help="Pipeline statistics")

    parser.add_argument("--max-articles", type=int, default=None, help="Max articles to generate (overrides config)")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.run:
        result = run_sam_to_pulse(max_articles=args.max_articles)
    elif args.process_pending:
        result = process_pending(max_articles=args.max_articles)
    elif args.dry_run:
        result = run_sam_to_pulse(dry_run=True, max_articles=args.max_articles)
    elif args.list_pending:
        result = get_pending_topics()
    elif args.stats:
        result = get_stats()
    else:
        result = {"error": "No action specified"}

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
