# CUI // SP-CTI
"""ICDEV™ Pulse scheduler — multi-stage pipeline with LLM router integration.

Updated 2026-03-12: Added qwen3.5 drafting, Claude Sonnet rewriting,
multi-step wizard support, and per-article quality stats.

Pipeline stages:
  1. Research   — Web scraping + Ollama qwen3.5 synthesis (zero Claude)
  2. Draft      — Qwen3.5 generates article from template (zero Claude)
  3. Quality    — WriteGuard deterministic analysis (grammar, readability,
                  plagiarism, AI detection, tone)
  4. Rewrite    — Claude Sonnet rewrites based on findings (planner tier)
  5. Review     — Human approval gate (dashboard wizard)
  6. Publish    — Export + push to WordPress

FORGE-compliant: LLM calls go through tools/llm/router.py.
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import json
import traceback
from datetime import datetime, timezone
from uuid import uuid4


from tools.pulse.engine.researcher import (
    research_with_local_first,
    synthesize_research,
)
from tools.pulse.engine.topic_clusterer import (
    cluster_research,
    get_next_topic,
    mark_cluster_used,
    rank_clusters,
    save_clusters,
)
from tools.pulse.engine.drafter import (
    process_draft,
    draft_article_via_llm,
    rewrite_article_via_llm,
    _extract_title,
    _extract_tldr,
    _count_words,
    _strip_llm_preamble,
)
from tools.pulse.engine.image_generator import create_post_image
from tools.pulse.engine.video_finder import find_videos_for_post
from tools.pulse.engine.video_generator import create_post_video
from tools.pulse.engine.seo_optimizer import optimize_post, generate_schema_json
from tools.pulse.engine.exporter import export_both
from tools.pulse.config import SCHEDULE_INTERVAL
from tools.pulse.db import (
    get_connection,
    init_db,
    insert_row,
    update_row,
    get_existing_titles,
    get_existing_slugs,
)
from tools.pulse.writeguard import run_full_quality_check, rewrite_content

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Phase 1: Research + Synthesis
# ---------------------------------------------------------------------------


def research_phase(
    topic_override: str | None = None,
    template_type: str = "challenge_solution",
) -> dict:
    """Run research and Ollama synthesis (two-tier: Ollama → Claude Code).

    Args:
        topic_override: If provided, skip web research and use this topic.
        template_type: 'challenge_solution' or 'feature_spotlight' — selects topic pool.

    Returns:
        dict with 'research_results', 'synthesis', 'cluster', 'status'.
    """
    init_db()

    if topic_override:
        logger.info("Using topic override: %s", topic_override)
        cluster = {
            "id": f"cl-{uuid4().hex[:12]}",
            "name": topic_override,
            "description": topic_override,
            "pain_points": [topic_override],
            "research_ids": [],
            "priority_score": 1.0,
        }
        return {
            "research_results": [],
            "synthesis": {
                "synthesis": f"Topic override: {topic_override}",
                "top_themes": [{"theme": topic_override}],
                "recommended_angles": [topic_override],
                "pain_point_summary": {},
                "model_used": "override",
                "status": "topic_override",
            },
            "cluster": cluster,
            "status": "ok",
            "stage": "research",
        }

    # Step 1: Local-first research (RAG/KG → web scraping for gaps)
    logger.info("Phase 1a: Researching topics (local-first, template=%s)...", template_type)
    research_results = research_with_local_first(template_type=template_type)
    logger.info("Found %d research results", len(research_results))

    if not research_results:
        return {
            "research_results": [],
            "synthesis": None,
            "cluster": None,
            "status": "no_results",
            "stage": "research",
            "error": "No research results found",
        }

    # Step 1b: Ollama synthesis (two-tier: Ollama analyzes, Claude Code confirms)
    logger.info("Phase 1b: Synthesizing research with Ollama...")
    synthesis = synthesize_research(research_results)
    logger.info(
        "Synthesis complete (model: %s, status: %s)",
        synthesis.get("model_used"),
        synthesis.get("status"),
    )

    # Step 2: Cluster topics (deterministic TF-IDF)
    logger.info("Phase 2: Clustering topics...")
    clusters = cluster_research(research_results)
    clusters = rank_clusters(clusters)

    if not clusters:
        cluster = get_next_topic()
        if not cluster:
            return {
                "research_results": research_results,
                "synthesis": synthesis,
                "cluster": None,
                "status": "no_clusters",
                "stage": "research",
                "error": "No topic clusters available",
            }
    else:
        # Persist clusters so mark_cluster_used can find them later
        try:
            save_clusters(clusters)
        except Exception as e:
            logger.warning("Failed to save clusters: %s", e)
        cluster = clusters[0]  # Highest priority

    return {
        "research_results": research_results,
        "synthesis": synthesis,
        "cluster": cluster,
        "all_clusters": clusters[:5],  # Top 5 for selection
        "status": "ok",
        "stage": "research",
    }


# ---------------------------------------------------------------------------
# Phase 2: Draft via LLM (qwen3.5 scanner tier)
# ---------------------------------------------------------------------------


def draft_phase(
    cluster: dict,
    template_type: str = "challenge_solution",
    research_context: str = "",
    capability_context: str = "",
) -> dict:
    """Generate article draft using qwen3.5 via LLM router.

    Scanner tier: zero Claude tokens.

    Args:
        cluster: Topic cluster from research phase.
        template_type: 'challenge_solution' or 'feature_spotlight'.
        research_context: Synthesis text from research.
        capability_context: ICDEV™ capability reference for prompt injection.

    Returns:
        dict with 'body_markdown', 'model_used', 'status', 'stage'.
    """
    logger.info("Phase 2: Drafting article via LLM router (template: %s)...", template_type)

    result = draft_article_via_llm(cluster, template_type, research_context, capability_context)
    result["stage"] = "draft"

    if result.get("status") == "drafted":
        word_count = _count_words(result.get("body_markdown", ""))
        logger.info(
            "Draft complete: %d words (model: %s)",
            word_count,
            result.get("model_used"),
        )
        result["word_count"] = word_count
    else:
        logger.warning("Draft failed: %s", result.get("error", "unknown"))

    return result


# ---------------------------------------------------------------------------
# Phase 3-4: Post-processing (image, video, SEO, save, quality, rewrite)
# ---------------------------------------------------------------------------


def _resolve_placeholders(
    body_md: str,
    hero_image: str | None,
    videos: list[dict],
) -> str:
    """Resolve placeholder markers in the Markdown body."""
    import re

    # Hero image — handle both [HERO_IMAGE] and [HERO_IMAGE: description]
    hero_re = re.compile(r"\[HERO_IMAGE(?::[^\]]*)?\]")
    if hero_image:
        safe_hero = hero_image.replace("\\", "/")
        body_md = hero_re.sub(lambda _: f"![Hero]({safe_hero})", body_md)
    else:
        body_md = hero_re.sub("", body_md)

    # Source references
    body_md = re.sub(
        r"\[SOURCE_REF:(.*?)\]",
        lambda m: f"[source]({m.group(1)})",
        body_md,
    )

    # Video embeds — use embed_html (iframe) when available, else markdown link
    _used_video_urls = set()

    def _match_video(match):
        topic = match.group(1).lower().strip()
        topic_words = set(topic.split())
        best_video = None
        best_score = 0
        for v in videos:
            if v.get("url") in _used_video_urls:
                continue
            v_topic = (v.get("source_pain_point", "") or v.get("topic", "") or v.get("title", "")).lower()
            # Substring match
            if topic in v_topic or v_topic in topic:
                score = 2.0
            else:
                # Word overlap
                v_words = set(v_topic.split())
                overlap = len(topic_words & v_words)
                score = overlap / max(len(topic_words), 1)
            if score > best_score:
                best_score = score
                best_video = v
        # Accept if any word overlaps
        if best_video and best_score >= 0.3:
            _used_video_urls.add(best_video.get("url"))
            embed = best_video.get("embed_html", "")
            if embed:
                return embed
            url = best_video.get("url", best_video.get("watch_url", ""))
            title = best_video.get("title", "Video")
            if url:
                return f"[{title}]({url})"
        # No match — assign next unused video if available
        for v in videos:
            if v.get("url") not in _used_video_urls:
                _used_video_urls.add(v["url"])
                embed = v.get("embed_html", "")
                if embed:
                    return embed
                url = v.get("url", "")
                if url:
                    return f"[{v.get('title', 'Video')}]({url})"
        return ""

    body_md = re.sub(r"\[VIDEO_EMBED:(.*?)\]", _match_video, body_md)

    # Strip remaining unresolved placeholders
    body_md = re.sub(r"\[HERO_IMAGE(?::[^\]]*)?\]", "", body_md)
    body_md = re.sub(r"\[VIDEO_EMBED:[^\]]*\]", "", body_md)
    body_md = re.sub(r"\[SOURCE_REF:[^\]]*\]", "", body_md)

    # Strip LLM-generated placeholder lines (Documentation, Community links)
    body_md = re.sub(
        r"^.*Placeholder for (?:Documentation|Community).*$",
        "",
        body_md,
        flags=re.MULTILINE,
    )

    return body_md


_VALID_POST_COLUMNS = {
    "id",
    "title",
    "slug",
    "status",
    "topic",
    "pain_points",
    "tldr",
    "body_markdown",
    "body_html",
    "seo_title",
    "seo_description",
    "seo_keywords",
    "og_image_url",
    "hero_image_path",
    "video_embeds",
    "source_urls",
    "word_count",
    "readability_score",
    "plagiarism_score",
    "ai_detection_score",
    "writeguard_passed",
    "author_id",
    "reviewer_id",
    "review_notes",
    "schema_json",
    "frontmatter",
    "capabilities_referenced",
    "generated_video_path",
    "generated_video_method",
    "generated_video_duration",
    "judge_color",
    "judge_composite",
    "judge_combined",
    "created_at",
    "updated_at",
    "published_at",
    "archived_at",
}


def _enrich_media(run_id: str, post_data: dict, cluster: dict, template_type: str = "challenge_solution") -> None:
    """Generate hero image, generated video, find YouTube videos, and cross-link; mutates post_data in place."""
    logger.info("[%s] Generating hero image...", run_id)
    try:
        # Build rich topic string from cluster for better image prompt matching
        pain_pts = cluster.get("pain_points", [])
        if isinstance(pain_pts, str):
            try:
                pain_pts = json.loads(pain_pts)
            except (json.JSONDecodeError, TypeError):
                pain_pts = [pain_pts]
        topic_parts = [cluster.get("name", ""), cluster.get("description", "")]
        topic_parts.extend(pain_pts if isinstance(pain_pts, list) else [])
        rich_topic = " ".join(str(p) for p in topic_parts if p)
        image_result = create_post_image(post_data["title"], rich_topic)
        if image_result.get("path"):
            post_data["hero_image_path"] = image_result["path"]
        post_data["og_image_url"] = image_result.get("url") or image_result.get("path", "")
    except Exception as e:
        logger.warning("[%s] Image generation failed (non-fatal): %s", run_id, e)

    # Generate a hero video clip (LTX-Video 2B or animated SVG fallback)
    logger.info("[%s] Generating hero video clip...", run_id)
    try:
        video_result = create_post_video(post_data["title"], cluster.get("name", ""))
        if video_result.get("success"):
            post_data["generated_video_path"] = video_result.get("path", "")
            post_data["generated_video_method"] = video_result.get("method", "")
            post_data["generated_video_duration"] = video_result.get("duration_sec", 0)
            logger.info(
                "[%s] Video generated: %s (method=%s, %.1fs)",
                run_id,
                video_result.get("path"),
                video_result.get("method"),
                video_result.get("duration_sec", 0),
            )
        else:
            logger.warning("[%s] Video generation failed: %s", run_id, video_result.get("error"))
    except Exception as e:
        logger.warning("[%s] Video generation failed (non-fatal): %s", run_id, e)

    # Feature Spotlight template does not use embedded videos
    if template_type == "feature_spotlight":
        logger.info("[%s] Skipping video search (feature_spotlight template)", run_id)
        post_data["video_embeds"] = []
        return

    logger.info("[%s] Finding relevant videos...", run_id)
    try:
        pain_points = cluster.get("pain_points", [])
        if isinstance(pain_points, str):
            pain_points = json.loads(pain_points)
        post_data["video_embeds"] = find_videos_for_post(pain_points[:3])
    except Exception as e:
        logger.warning("[%s] Video search failed (non-fatal): %s", run_id, e)
        post_data["video_embeds"] = []


def _inject_cross_links(run_id: str, body_md: str, current_title: str) -> str:
    """Inject internal cross-links to other Pulse articles in the body.

    Finds the most relevant existing post based on keyword overlap with the
    current article, then inserts a "Related Reading" callout before the
    conclusion section. Deterministic, zero LLM.
    """
    try:
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT id, title, slug, topic FROM pulse_posts "
                "WHERE status IN ('draft', 'published', 'approved', 'in_review') "
                "ORDER BY created_at DESC LIMIT 50"
            ).fetchall()
        if not rows:
            return body_md

        current_words = set(w.lower() for w in current_title.split() if len(w) > 3)
        best_match = None
        best_score = 0
        for row in rows:
            row_title = row["title"] or ""
            if row_title.lower().strip() == current_title.lower().strip():
                continue  # skip self
            row_words = set(w.lower() for w in row_title.split() if len(w) > 3)
            overlap = len(current_words & row_words)
            # Also check topic overlap
            topic_words = set(w.lower() for w in (row["topic"] or "").split() if len(w) > 3)
            topic_overlap = len(current_words & topic_words)
            score = overlap + topic_overlap * 0.5
            if score > best_score:
                best_score = score
                best_match = row

        if not best_match or best_score < 1:
            # No good match — pick most recent different post
            for row in rows:
                if (row["title"] or "").lower().strip() != current_title.lower().strip():
                    best_match = row
                    break

        if not best_match:
            return body_md

        link_title = best_match["title"] or "Related Article"
        crosslink_block = (
            f"\n\n---\n\n"
            f"> **Related Reading:** [{link_title}](/articles/{best_match['id']})"
            f" — Explore more on this topic in our article library.\n\n"
        )

        # Insert before the last ## heading (usually Conclusion or CTA)
        import re

        headings = list(re.finditer(r"^##\s+", body_md, re.MULTILINE))
        if len(headings) >= 2:
            insert_pos = headings[-1].start()
            body_md = body_md[:insert_pos] + crosslink_block + body_md[insert_pos:]
        else:
            # Append before end
            body_md += crosslink_block

        logger.info("[%s] Cross-linked to: %s", run_id, link_title[:50])
    except Exception as e:
        logger.warning("[%s] Cross-linking failed (non-fatal): %s", run_id, e)

    return body_md


def _apply_seo(run_id: str, post_data: dict) -> None:
    """Run SEO optimization, cross-linking, and flatten fields; mutates post_data in place."""
    logger.info("[%s] Optimizing SEO...", run_id)
    post_data["content"] = post_data.get("body_markdown", "")
    optimized = optimize_post(post_data)
    post_data.update(optimized)
    post_data["schema_json"] = generate_schema_json(post_data)

    # Inject cross-links to related Pulse articles
    logger.info("[%s] Injecting cross-links...", run_id)
    post_data["body_markdown"] = _inject_cross_links(
        run_id,
        post_data.get("body_markdown", ""),
        post_data.get("title", ""),
    )

    # Compute and store SEO score
    from tools.pulse.engine.seo_optimizer import analyze_seo_score

    seo_analysis = analyze_seo_score(post_data)
    post_data["seo_score"] = seo_analysis.get("score", 0)
    logger.info(
        "[%s] SEO score: %d/100 (%s) — %d recommendations",
        run_id,
        seo_analysis["score"],
        seo_analysis["grade"],
        len(seo_analysis.get("recommendations", [])),
    )

    seo_data = post_data.pop("seo", {})
    post_data["seo_title"] = seo_data.get("title", post_data.get("title", ""))
    post_data["seo_description"] = seo_data.get("meta_description", "")
    post_data["seo_keywords"] = json.dumps(seo_data.get("keywords", []))


def _ensure_unique_slug(slug: str) -> str:
    """Ensure slug is unique by appending a numeric suffix if needed."""
    existing = get_existing_slugs()
    if slug not in existing:
        return slug
    for i in range(2, 100):
        candidate = f"{slug}-{i}"
        if candidate not in existing:
            logger.info("Slug '%s' already exists, using '%s'", slug, candidate)
            return candidate
    # Fallback: append uuid fragment
    return f"{slug}-{uuid4().hex[:6]}"


def _save_post(run_id: str, post_data: dict, cluster: dict) -> str:
    """Serialize, filter, and insert post into DB. Returns post_id."""
    post_id = f"post-{uuid4().hex[:12]}"
    post_data["id"] = post_id
    post_data["status"] = "draft"
    post_data["topic"] = cluster.get("name", cluster.get("topic", ""))
    post_data["pain_points"] = json.dumps(cluster.get("pain_points", []))

    # Enforce unique slug
    post_data["slug"] = _ensure_unique_slug(post_data.get("slug", ""))

    for key in ("video_embeds", "source_urls", "schema_json"):
        if key in post_data and not isinstance(post_data[key], str):
            post_data[key] = json.dumps(post_data[key])

    filtered = {k: v for k, v in post_data.items() if k in _VALID_POST_COLUMNS}
    insert_row("posts", filtered)
    logger.info("[%s] Post saved: %s (slug: %s)", run_id, post_id, post_data["slug"])
    return post_id


_MAX_REWRITE_ATTEMPTS = 3


def _handle_rewrite(run_id: str, post_id: str, body: str, quality: dict, auto_rewrite: bool):
    """Handle rewrite logic (auto or manual prep). Returns (quality, rewrite_data).

    Auto mode loops up to _MAX_REWRITE_ATTEMPTS times, stopping early when
    quality passes.  The final rewrite_data carries 'rewrite_attempts' so
    callers can surface the iteration count.
    """
    rewrite_data = None
    if auto_rewrite and not quality.get("passed", False):
        current_body = body
        for attempt in range(1, _MAX_REWRITE_ATTEMPTS + 1):
            logger.info(
                "[%s] Quality check failed — triggering rewrite (attempt %d/%d)...",
                run_id,
                attempt,
                _MAX_REWRITE_ATTEMPTS,
            )
            rewrite_data = _auto_rewrite(run_id, post_id, current_body, quality)
            if not rewrite_data or rewrite_data.get("status") != "rewritten":
                break
            rewrite_data["rewrite_attempts"] = attempt
            current_body = rewrite_data.get("body_markdown", current_body)
            quality = _run_quality_check(run_id, post_id, current_body)
            logger.info(
                "[%s] Post-rewrite quality (attempt %d): %s (score: %.1f)",
                run_id,
                attempt,
                "PASSED" if quality["passed"] else "STILL NEEDS REVIEW",
                quality["overall_score"],
            )
            if quality.get("passed", False):
                break
    elif not auto_rewrite and not quality.get("passed", False):
        logger.info("[%s] Quality failed — preparing rewrite instructions", run_id)
        try:
            rewrite_data = rewrite_content(body, quality)
        except Exception as e:
            logger.warning("[%s] Rewrite prep failed: %s", run_id, e)
    return quality, rewrite_data


def _build_result(ctx: dict) -> dict:
    """Assemble the final pipeline result dict.

    ctx keys: run_id, post_data, quality, rewrite_data, template_type, exports.
    """
    rewrite_data = ctx.get("rewrite_data")
    quality = ctx["quality"]
    post_data = ctx["post_data"]
    final_body = (
        rewrite_data.get("body_markdown", "")
        if rewrite_data and rewrite_data.get("body_markdown")
        else post_data.get("body_markdown", "")
    )
    result = {
        "run_id": ctx["run_id"],
        "status": "completed",
        "stage": "quality_check",
        "post_id": post_data.get("id", ""),
        "title": post_data.get("title", ""),
        "slug": post_data.get("slug", ""),
        "word_count": _count_words(final_body),
        "template_type": ctx.get("template_type", ""),
        "writeguard_passed": quality.get("passed", False),
        "quality_score": quality.get("overall_score", 0),
        "quality_details": quality.get("details", {}),
        "quality_stats": _extract_quality_stats(quality),
        "exports": ctx.get("exports", {}),
        "recommendations": quality.get("recommendations", []),
        "auto_rewritten": rewrite_data is not None and rewrite_data.get("status") == "rewritten",
        "rewrite_model": rewrite_data.get("model_used", "") if rewrite_data else "",
        "judge_color": post_data.get("judge_color", ""),
        "judge_composite": post_data.get("judge_composite", 0),
        "judge_combined": post_data.get("judge_combined", 0),
    }
    exports = ctx.get("exports") or {}
    if isinstance(exports, dict) and exports.get("blocked"):
        result["publish_blocked"] = True
        result["publish_block_reason"] = exports.get("reason", "")
    if not quality.get("passed", False) and rewrite_data:
        result["rewrite_needed"] = True
        if rewrite_data.get("findings"):
            result["rewrite_findings"] = rewrite_data["findings"]
        if rewrite_data.get("rewrite_instructions"):
            result["rewrite_instructions"] = rewrite_data["rewrite_instructions"]
    return result


def post_processing(
    body_markdown: str,
    cluster: dict,
    source_urls: list[str] | None = None,
    mark_used: bool = False,
    template_type: str = "challenge_solution",
    auto_rewrite: bool = True,
    progress_callback: callable | None = None,
    capabilities_referenced: list[dict] | None = None,
) -> dict:
    """Run full pipeline: process -> image -> video -> SEO -> save -> quality -> rewrite -> export."""
    capabilities_referenced = capabilities_referenced or []
    run_id = f"run-{uuid4().hex[:12]}"
    started = datetime.now(timezone.utc).isoformat()
    insert_row(
        "schedule_log",
        {
            "id": run_id,
            "run_type": "llm_pipeline" if auto_rewrite else "claude_code_orchestrated",
            "status": "running",
            "topic_cluster_id": cluster.get("id", ""),
            "started_at": started,
        },
    )

    try:
        # Strip LLM preamble before processing
        body_markdown = _strip_llm_preamble(body_markdown)
        post_data = process_draft(body_markdown, cluster)
        if source_urls:
            post_data["source_urls"] = source_urls
        logger.info("[%s] Processing: %s (%d words)", run_id, post_data["title"], post_data["word_count"])

        _enrich_media(run_id, post_data, cluster, template_type=template_type)

        hero = post_data.get("og_image_url") or post_data.get("hero_image_path", "")
        videos = post_data.get("video_embeds", []) if isinstance(post_data.get("video_embeds"), list) else []
        post_data["body_markdown"] = _resolve_placeholders(post_data.get("body_markdown", ""), hero or None, videos)

        _apply_seo(run_id, post_data)

        # Auto-match capabilities if none were provided upstream
        if not capabilities_referenced:
            try:
                from tools.pulse.engine.capability_scanner import match_capabilities

                title_text = post_data.get("title", "")
                topic_text = cluster.get("name", "")
                cap_keywords = [kw for kw in f"{title_text} {topic_text}".split() if len(kw) > 2]
                matched = match_capabilities(cap_keywords, top_n=5)
                if matched:
                    capabilities_referenced = [
                        {"slug": c["slug"], "name": c["name"], "domain": c["domain"], "score": c.get("match_score", 0)}
                        for c in matched
                    ]
                    logger.info("[%s] Auto-matched %d capabilities", run_id, len(capabilities_referenced))
            except Exception as e:
                logger.warning("[%s] Capability auto-match failed: %s", run_id, e)

        if capabilities_referenced:
            post_data["capabilities_referenced"] = json.dumps(capabilities_referenced)
        post_id = _save_post(run_id, post_data, cluster)
        post_data["id"] = post_id

        logger.info("[%s] Running WriteGuard quality checks...", run_id)
        if progress_callback:
            try:
                progress_callback("quality_check")
            except Exception:
                pass
        quality = _run_quality_check(run_id, post_id, post_data.get("body_markdown", ""))

        # LLM Judge (Prometheus-2) — semantic quality evaluation
        judge_result = _run_llm_judge(run_id, post_id, post_data.get("body_markdown", ""), quality)
        if judge_result:
            post_data["judge_color"] = judge_result.get("color_rating", {}).get("color", "")
            post_data["judge_composite"] = judge_result.get("composite_score", 0)
            post_data["judge_combined"] = judge_result.get("combined_score", 0)

        if progress_callback:
            try:
                progress_callback("rewrite")
            except Exception:
                pass
        quality, rewrite_data = _handle_rewrite(
            run_id, post_id, post_data.get("body_markdown", ""), quality, auto_rewrite
        )

        if progress_callback:
            try:
                progress_callback("publish")
            except Exception:
                pass
        exports = _publish_stage(run_id, post_id, post_data)
        if mark_used:
            mark_cluster_used(cluster.get("id", ""))

        update_row(
            "schedule_log",
            run_id,
            {
                "status": "completed",
                "post_id": post_id,
                "completed_at": datetime.now(timezone.utc).isoformat(),
            },
        )

        result = _build_result(
            {
                "run_id": run_id,
                "post_data": post_data,
                "quality": quality,
                "rewrite_data": rewrite_data,
                "template_type": template_type,
                "exports": exports,
            }
        )
        logger.info(
            "[%s] Pipeline complete: %s",
            run_id,
            json.dumps(
                {k: v for k, v in result.items() if k not in ("quality_details", "rewrite_findings")},
                indent=2,
            ),
        )
        return result

    except Exception as e:
        logger.error("[%s] Pipeline failed: %s\n%s", run_id, e, traceback.format_exc())
        update_row(
            "schedule_log",
            run_id,
            {
                "status": "failed",
                "error_message": str(e)[:1000],
                "completed_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        return {"run_id": run_id, "status": "failed", "stage": "error", "error": str(e)}


def _publish_stage(run_id: str, post_id: str, post_data: dict) -> dict:
    """Automated publish/export stage, gated on the LLM-judge verdict (nav-intel-09).

    Fail-closed, mirroring ``publish_all_approved`` in the WordPress/Hostinger
    publishers: a RED verdict — or a judge that errored / never ran (no verdict
    on record) — skips the publish stage entirely and logs the reason. Any
    other color (green/yellow/purple/blue) proceeds to export.
    """
    from tools.pulse.publish_gate import evaluate_publish_gate

    gate = evaluate_publish_gate(post_data)
    if gate["blocked"]:
        logger.warning(
            "[%s] Auto-publish SKIPPED for post %s — %s (verdict: %s)",
            run_id,
            post_id,
            gate["reason"],
            gate["verdict"] or "absent",
        )
        return {
            "skipped": True,
            "blocked": True,
            "post_id": post_id,
            "verdict": gate["verdict"],
            "reason": gate["reason"],
        }
    logger.info(
        "[%s] Publish gate cleared (verdict: %s) — exporting post %s",
        run_id,
        gate["verdict"],
        post_id,
    )
    return export_both(post_id)


def _run_llm_judge(run_id: str, post_id: str, body: str, quality: dict) -> dict | None:
    """Run LLM-as-a-Judge (Prometheus-2) for semantic quality evaluation.

    Returns judge result dict or None if judge is unavailable/skipped.
    """
    try:
        from tools.writing.llm_judge import evaluate_and_store, init_judge_db

        init_judge_db()

        wg_score = quality.get("overall_score", 0)
        logger.info("[%s] Running LLM Judge (WriteGuard score: %.1f)...", run_id, wg_score)

        result = evaluate_and_store(
            text=body,
            content_type="blog",
            writeguard_score=wg_score,
            post_id=post_id,
        )

        if result.get("status") == "evaluated":
            color = result.get("color_rating", {})
            logger.info(
                "[%s] LLM Judge: %s (%s) — composite %.1f/5, combined %.1f/5",
                run_id,
                color.get("color", "?").upper(),
                color.get("label", "?"),
                result.get("composite_score", 0),
                result.get("combined_score", 0),
            )
            # Update post with judge scores
            update_row(
                "posts",
                post_id,
                {
                    "judge_color": color.get("color", ""),
                    "judge_composite": result.get("composite_score", 0),
                    "judge_combined": result.get("combined_score", 0),
                },
            )
            return result
        elif result.get("status") == "skipped":
            logger.info("[%s] LLM Judge skipped: %s", run_id, result.get("reason", ""))
        else:
            logger.warning("[%s] LLM Judge failed: %s", run_id, result.get("error", "unknown"))
    except Exception as e:
        logger.warning("[%s] LLM Judge unavailable (non-fatal): %s", run_id, e)
    return None


def _run_quality_check(run_id: str, post_id: str, body: str) -> dict:
    """Run WriteGuard and update post with scores.

    v6b: Stores named composite scores (Correctness, Clarity, Delivery,
    Originality, Engagement) mapped to existing DB columns:
      readability_score  → overall WriteGuard Score (0-100)
      grammar_score      → Correctness composite
      tone_score         → Delivery composite
      plagiarism_score   → Originality composite
      ai_detection_score → Engagement composite

    Also persists results to wg_analysis_results via shared API layer so
    the /writeguard dashboard History tab shows all Pulse analyses.
    """
    try:
        quality = run_full_quality_check(body)
        composites = quality.get("composites", {})
        stats = _extract_quality_stats(quality)
        update_row(
            "posts",
            post_id,
            {
                "writeguard_passed": 1 if quality["passed"] else 0,
                # overall WriteGuard Score stored in readability_score column
                "readability_score": quality.get("overall_score", 0),
                # Named composites stored in existing score columns
                "grammar_score": composites.get("correctness", stats.get("grammar", 0)),
                "tone_score": composites.get("delivery", stats.get("tone", 0)),
                "plagiarism_score": composites.get("originality", stats.get("plagiarism", 0)),
                "ai_detection_score": composites.get("engagement", stats.get("ai_detection", 0)),
            },
        )
        logger.info(
            "[%s] WriteGuard: %s (overall: %.1f, correctness: %.0f, clarity: %.0f, "
            "delivery: %.0f, originality: %.0f, engagement: %.0f, badge: %s)",
            run_id,
            "PASSED" if quality["passed"] else "NEEDS REVIEW",
            quality["overall_score"],
            composites.get("correctness", 0),
            composites.get("clarity", 0),
            composites.get("delivery", 0),
            composites.get("originality", 0),
            composites.get("engagement", 0),
            composites.get("color_badge", "?"),
        )
        # Persist to shared wg_analysis_results for history/trend dashboard
        try:
            from tools.dashboard.api.writeguard import save_analysis_result
            save_analysis_result(
                body,
                quality,
                mode="inline",
                document_name=f"pulse:{post_id}",
            )
        except Exception as _persist_err:
            logger.debug("[%s] WriteGuard persist to wg_analysis_results skipped: %s", run_id, _persist_err)
        return quality
    except Exception as e:
        logger.warning("[%s] WriteGuard check failed: %s", run_id, e)
        return {"passed": False, "overall_score": 0, "details": {}, "recommendations": [str(e)]}


def _extract_findings(quality: dict) -> list[dict]:
    """Extract findings from a WriteGuard quality result."""
    findings = []
    for key, result in quality.get("details", {}).items():
        if not isinstance(result, dict):
            continue
        for f in result.get("findings", result.get("issues", [])):
            if isinstance(f, dict):
                findings.append(
                    {
                        "category": key,
                        "message": f.get("message", f.get("description", "")),
                        "suggestion": f.get("suggestion", f.get("replacement", "")),
                    }
                )
            elif isinstance(f, str):
                findings.append({"category": key, "message": f, "suggestion": ""})
    for rec in quality.get("recommendations", []):
        if isinstance(rec, str):
            findings.append({"category": "recommendation", "message": rec, "suggestion": ""})
        elif isinstance(rec, dict):
            findings.append(
                {
                    "category": "recommendation",
                    "message": rec.get("message", ""),
                    "suggestion": rec.get("suggestion", ""),
                }
            )
    return findings


def _auto_rewrite(run_id: str, post_id: str, body: str, quality: dict) -> dict:
    """Auto-rewrite via Claude Sonnet and save updated content."""
    try:
        findings = _extract_findings(quality)

        if not findings:
            logger.info("[%s] No findings for rewrite — skipping", run_id)
            return {"status": "no_findings", "body_markdown": body}

        # Apply deterministic fixes first
        try:
            from tools.writing.rewriter import _apply_deterministic_fixes

            body, _ = _apply_deterministic_fixes(body)
        except ImportError:
            pass

        # Rewrite via Claude Sonnet
        result = rewrite_article_via_llm(body, findings)

        if result.get("status") == "rewritten" and result.get("body_markdown"):
            # Save rewritten content
            new_body = result["body_markdown"]
            update_row(
                "posts",
                post_id,
                {
                    "body_markdown": new_body,
                    "word_count": _count_words(new_body),
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                },
            )
            logger.info(
                "[%s] Auto-rewrite complete: model=%s",
                run_id,
                result.get("model_used"),
            )
        return result

    except Exception as e:
        logger.error("[%s] Auto-rewrite failed: %s", run_id, e)
        return {"status": "rewrite_failed", "error": str(e), "body_markdown": body}


def _extract_quality_stats(quality: dict) -> dict:
    """Extract per-dimension quality stats for dashboard display.

    v6b: Prefers named composite scores when available; falls back to raw
    base-dimension scores for backward compatibility.

    Returns dict with: grammar (→ correctness), readability (→ clarity),
    plagiarism (→ originality), ai_detection (→ engagement), tone (→ delivery),
    overall, grade_level.
    """
    details = quality.get("details", {})
    composites = quality.get("composites", {})
    return {
        "grammar": composites.get("correctness", details.get("grammar", {}).get("score", 0)),
        "readability": composites.get("clarity", details.get("readability", {}).get("score", 0)),
        "plagiarism": composites.get("originality", details.get("plagiarism", {}).get("score", 0)),
        "ai_detection": composites.get("engagement", details.get("ai_detection", {}).get("score", 0)),
        "tone": composites.get("delivery", details.get("tone", {}).get("score", 0)),
        "overall": quality.get("overall_score", 0),
        "grade_level": details.get("readability", {}).get("grade_level", 0),
        "grammar_issues": details.get("grammar", {}).get("issue_count", 0),
    }


# ---------------------------------------------------------------------------
# Full automated pipeline: research → draft → quality → rewrite
# ---------------------------------------------------------------------------


def run_full_pipeline(
    topic_override: str | None = None,
    template_type: str = "challenge_solution",
    auto_rewrite: bool = True,
    progress_callback: callable | None = None,
) -> dict:
    """Run the complete pipeline: research → draft → process → quality → rewrite.

    This is the new entry point that automates the full flow using LLM router.

    Args:
        topic_override: Optional topic to use instead of research.
        template_type: Article template ('challenge_solution' or 'feature_spotlight').
        auto_rewrite: Whether to auto-rewrite via Claude Sonnet.
        progress_callback: Optional callable(stage: str) called at each pipeline stage.

    Returns:
        Pipeline result dict.
    """

    def _notify(stage: str):
        if progress_callback:
            try:
                progress_callback(stage)
            except Exception:
                pass

    init_db()

    # Phase 1: Research
    _notify("research")
    research = research_phase(topic_override, template_type=template_type)
    if research.get("status") not in ("ok", "topic_override"):
        return research

    cluster = research.get("cluster")
    if not cluster:
        return {**research, "error": "No cluster available"}

    # Extract research context for drafting
    synthesis = research.get("synthesis", {})
    research_context = ""
    if isinstance(synthesis, dict):
        research_context = synthesis.get("synthesis", "")
        themes = synthesis.get("top_themes", [])
        if themes:
            research_context += "\n\nKey themes:\n" + "\n".join(
                f"- {t.get('theme', t) if isinstance(t, dict) else t}" for t in themes[:5]
            )

    # Inject existing titles into research context so LLM avoids duplicates
    existing_titles = get_existing_titles()
    if existing_titles:
        avoid_list = "\n".join(f"- {t}" for t in existing_titles[:20])
        research_context += (
            "\n\n## IMPORTANT: Avoid Duplicate Topics\n"
            "The following articles have ALREADY been written. "
            "You MUST choose a DIFFERENT angle, topic, or perspective. "
            "Do NOT reuse these titles or cover the same ground:\n"
            f"{avoid_list}\n"
        )

    # Phase 1c: Capability matching (D-PULSE-CAP-1, deterministic)
    capability_context = ""
    capabilities_referenced = []
    try:
        from tools.pulse.engine.capability_scanner import match_capabilities, format_capability_context

        topic_text = cluster.get("name", cluster.get("topic", ""))
        pain_pts = cluster.get("pain_points", [])
        if isinstance(pain_pts, str):
            import json as _json

            pain_pts = _json.loads(pain_pts)
        cap_keywords = topic_text.split() + (pain_pts if isinstance(pain_pts, list) else [])
        cap_keywords = [kw for kw in cap_keywords if len(kw) > 2]
        matched_caps = match_capabilities(cap_keywords, top_n=5)
        if matched_caps:
            capability_context = format_capability_context(matched_caps)
            capabilities_referenced = [
                {"slug": c["slug"], "name": c["name"], "domain": c["domain"], "score": c.get("match_score", 0)}
                for c in matched_caps
            ]
            logger.info("Matched %d capabilities for article context", len(matched_caps))
    except Exception as e:
        logger.warning("Capability matching skipped: %s", e)

    # Phase 2: Draft (with capability context injected)
    _notify("draft")
    draft = draft_phase(cluster, template_type, research_context, capability_context)
    if not draft.get("body_markdown"):
        return {
            "status": "draft_failed",
            "stage": "draft",
            "error": draft.get("error", "Draft produced no content"),
            "research": research,
        }

    # Phase 3-4: Post-processing (image, video, SEO, save, quality, rewrite)
    _notify("quality_check")
    result = post_processing(
        body_markdown=draft["body_markdown"],
        cluster=cluster,
        source_urls=cluster.get("source_urls", []),
        mark_used=True,
        template_type=template_type,
        auto_rewrite=auto_rewrite,
        progress_callback=progress_callback,
        capabilities_referenced=capabilities_referenced,
    )

    # Attach research metadata
    result["research_count"] = len(research.get("research_results", []))
    result["draft_model"] = draft.get("model_used", "")

    return result


# ---------------------------------------------------------------------------
# Post-rewrite update (manual rewrite trigger)
# ---------------------------------------------------------------------------


def update_post_content(post_id: str, body_markdown: str) -> dict:
    """Update a post with rewritten content.

    Saves the new content, re-runs WriteGuard quality check,
    and re-exports.

    Args:
        post_id: The post ID to update.
        body_markdown: New markdown content.

    Returns:
        dict with updated quality results and stats.
    """
    init_db()

    word_count = _count_words(body_markdown)
    title = _extract_title(body_markdown)
    tldr = _extract_tldr(body_markdown)

    update_row(
        "posts",
        post_id,
        {
            "body_markdown": body_markdown,
            "title": title,
            "word_count": word_count,
            "tldr": tldr,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    logger.info("Post %s updated: %d words", post_id, word_count)

    # Re-run quality check
    quality = run_full_quality_check(body_markdown)
    stats = _extract_quality_stats(quality)
    update_row(
        "posts",
        post_id,
        {
            "writeguard_passed": 1 if quality["passed"] else 0,
            "readability_score": quality.get("overall_score", 0),
            "grammar_score": stats.get("grammar", 0),
            "plagiarism_score": stats.get("plagiarism", 0),
            "ai_detection_score": stats.get("ai_detection", 0),
            "tone_score": stats.get("tone", 0),
        },
    )
    logger.info(
        "Post %s quality after rewrite: %s (score: %.1f)",
        post_id,
        "PASSED" if quality["passed"] else "STILL NEEDS REVIEW",
        quality["overall_score"],
    )

    # Re-export
    exports = export_both(post_id)

    return {
        "post_id": post_id,
        "word_count": word_count,
        "writeguard_passed": quality.get("passed", False),
        "quality_score": quality.get("overall_score", 0),
        "quality_stats": stats,
        "exports": exports,
        "recommendations": quality.get("recommendations", []),
    }


def rewrite_post_via_llm(post_id: str) -> dict:
    """Trigger Claude Sonnet rewrite for a specific post.

    Reads the current post content, runs WriteGuard to get findings,
    then rewrites via LLM router (pulse_rewrite → Claude Sonnet).

    Args:
        post_id: The post ID to rewrite.

    Returns:
        dict with rewrite results and updated quality stats.
    """
    init_db()

    from tools.pulse.db import get_row

    post = get_row("posts", post_id)
    if not post:
        return {"status": "error", "error": f"Post not found: {post_id}"}

    body = post.get("body_markdown", "")
    if not body:
        return {"status": "error", "error": "Post has no content"}

    # Run quality check to get findings
    quality = run_full_quality_check(body)
    findings = _extract_findings(quality)

    if not findings:
        return {
            "status": "no_findings",
            "post_id": post_id,
            "quality_score": quality.get("overall_score", 0),
            "quality_stats": _extract_quality_stats(quality),
            "message": "No issues found — content looks good",
        }

    # Apply deterministic fixes first
    try:
        from tools.writing.rewriter import _apply_deterministic_fixes

        body, _ = _apply_deterministic_fixes(body)
    except ImportError:
        pass

    # Rewrite via Claude Sonnet
    result = rewrite_article_via_llm(body, findings)

    if result.get("status") == "rewritten" and result.get("body_markdown"):
        # Save and re-check quality
        return update_post_content(post_id, result["body_markdown"])

    return {
        "status": result.get("status", "failed"),
        "post_id": post_id,
        "error": result.get("error", "Rewrite produced no content"),
    }


def enrich_post_with_capabilities(post_id: str) -> dict:
    """Rewrite a post with ICDEV™ capability context injected.

    Matches capabilities based on post title/topic, injects capability context
    into the rewrite prompt, and triggers a Claude Sonnet rewrite.

    Args:
        post_id: The post ID to enrich.

    Returns:
        dict with rewrite results and matched capabilities.
    """
    init_db()

    from tools.pulse.db import get_row

    post = get_row("posts", post_id)
    if not post:
        return {"status": "error", "error": f"Post not found: {post_id}"}

    body = post.get("body_markdown", "")
    if not body:
        return {"status": "error", "error": "Post has no content"}

    title = post.get("title", "")
    topic = post.get("topic", "")

    # Match capabilities
    from tools.pulse.engine.capability_scanner import match_capabilities, format_capability_context

    keywords = (title + " " + topic).split()
    keywords = [kw.strip(',:;.!?()"') for kw in keywords if len(kw) > 2]
    matched = match_capabilities(keywords, top_n=5)

    if not matched:
        return {"status": "no_capabilities", "post_id": post_id, "message": "No capabilities matched"}

    capability_context = format_capability_context(matched)
    caps_ref = [
        {"slug": c["slug"], "name": c["name"], "domain": c["domain"], "score": c.get("match_score", 0)} for c in matched
    ]

    # Store capabilities_referenced
    with get_connection() as conn:
        conn.execute(
            "UPDATE pulse_posts SET capabilities_referenced = %s WHERE id = %s",
            (json.dumps(caps_ref), post_id),
        )

    # Build capability-enrichment findings (synthetic finding to trigger rewrite)
    findings = [
        {
            "category": "capability_enrichment",
            "message": "Article should reference ICDEV™ capabilities naturally",
            "suggestion": "Weave relevant ICDEV™ capability descriptions into the narrative. Do NOT include CLI commands, internal tool paths, or references to Pulse, SAM.gov, or any backend tooling names.",
        }
    ]

    # Rewrite with capability context
    result = rewrite_article_via_llm(body, findings, capability_context)

    if result.get("status") == "rewritten" and result.get("body_markdown"):
        update_result = update_post_content(post_id, result["body_markdown"])
        update_result["capabilities_matched"] = len(caps_ref)
        update_result["capabilities"] = caps_ref
        return update_result

    return {
        "status": result.get("status", "failed"),
        "post_id": post_id,
        "capabilities_matched": len(caps_ref),
        "capabilities": caps_ref,
        "error": result.get("error", "Rewrite produced no content"),
    }


# ---------------------------------------------------------------------------
# Legacy compatibility wrappers
# ---------------------------------------------------------------------------


def run_pipeline(topic_override: str | None = None) -> dict:
    """Legacy wrapper — research phase only (for dashboard/CLI)."""
    return research_phase(topic_override)


def run_pipeline_from_draft(
    topic: str,
    body_markdown: str,
    source_urls: list[str] | None = None,
) -> dict:
    """Run the pipeline from a pre-written draft (Claude Code entry point)."""
    cluster = {
        "id": f"cl-{uuid4().hex[:12]}",
        "name": topic,
        "description": topic,
        "pain_points": [topic],
        "research_ids": [],
        "priority_score": 1.0,
    }

    # Capability matching (deterministic, zero-LLM)
    capabilities_referenced = []
    try:
        from tools.pulse.engine.capability_scanner import match_capabilities

        cap_keywords = [kw for kw in topic.split() if len(kw) > 2]
        matched_caps = match_capabilities(cap_keywords, top_n=5)
        if matched_caps:
            capabilities_referenced = [
                {"slug": c["slug"], "name": c["name"], "domain": c["domain"], "score": c.get("match_score", 0)}
                for c in matched_caps
            ]
            logger.info("Matched %d capabilities for draft pipeline", len(matched_caps))
    except Exception as e:
        logger.warning("Capability matching skipped in draft pipeline: %s", e)

    return post_processing(
        body_markdown=body_markdown,
        cluster=cluster,
        source_urls=source_urls,
        mark_used=False,
        auto_rewrite=True,
        capabilities_referenced=capabilities_referenced,
    )


def run_scheduled():
    """Run the pipeline on a schedule using the schedule library."""
    import schedule
    import time

    init_db()
    logger.info("Starting ICDEV™ Pulse scheduler (%s)...", SCHEDULE_INTERVAL)
    logger.info("NOTE: Scheduled mode runs research + cluster only. Claude Code must write the article.")

    if SCHEDULE_INTERVAL == "daily":
        schedule.every().day.at("06:00").do(research_phase)
    elif SCHEDULE_INTERVAL == "weekly":
        schedule.every().monday.at("06:00").do(research_phase)
    else:
        schedule.every().day.at("06:00").do(research_phase)

    # SAM.gov → Pulse bridge schedule (D-SAM-PULSE-1)
    try:
        from tools.pulse.engine.sam_bridge import (
            _load_sam_bridge_config,
            run_sam_to_pulse,
        )

        sam_config = _load_sam_bridge_config()
        if sam_config.get("enabled", False):
            sam_schedule = sam_config.get("schedule", "weekly")
            if sam_schedule == "daily":
                schedule.every().day.at("08:00").do(run_sam_to_pulse)
            elif sam_schedule == "weekly":
                schedule.every().wednesday.at("08:00").do(run_sam_to_pulse)
            logger.info("SAM-to-Pulse bridge scheduled: %s", sam_schedule)
    except Exception as exc:
        logger.warning("SAM bridge scheduling skipped: %s", exc)

    # Run immediately on first start
    research_phase()

    while True:
        schedule.run_pending()
        time.sleep(60)
