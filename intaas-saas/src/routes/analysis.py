#!/usr/bin/env python3
"""Analysis routes — submit URL/text for multiperspectivity analysis."""
import logging

from fastapi import APIRouter, Request

from src.db import dynamo
from src.models import AnalyzeRequest
from src.services import (
    article_fetcher,
    gdelt_service,
    mediacloud_service,
    newsapi_service,
    search_service,
    youtube_service,
)

logger = logging.getLogger("intaas.routes.analysis")
router = APIRouter()


@router.post("/analyze")
async def analyze(req: AnalyzeRequest, request: Request):
    """Submit a URL or text for multiperspectivity analysis.

    1. Creates a topic
    2. Fetches related articles from GDELT (65 languages)
    3. Runs deterministic bias scoring on each article
    4. Queues LLM judge evaluation (async, via SageMaker)
    5. Returns topic_id for polling
    """
    # Handle YouTube URLs — extract transcript + use cleaned title for GDELT search
    youtube_data = None
    youtube_search_query = ""
    yt_title = ""
    yt_content = ""
    if req.url and youtube_service.is_youtube_url(req.url):
        youtube_data = youtube_service.analyze_youtube_url(req.url)
        yt_title = youtube_data.get("title", "")
        yt_content = youtube_data.get("content", "")
        youtube_search_query = youtube_data.get("search_query", "")

    title = req.title or yt_title or req.url or "Untitled analysis"

    # Check for existing topic with same title that has results
    existing = dynamo.list_topics(limit=100)
    for ex in existing:
        if (ex.get("title", "").strip() == title.strip()
                and int(ex.get("source_count", 0)) > 0):
            return {
                "topic_id": ex["topic_id"],
                "title": ex["title"],
                "status": ex.get("status", "ready"),
                "articles_found": int(ex.get("source_count", 0)),
                "languages": int(ex.get("language_count", 0)),
                "cached": True,
                "message": "Topic already analyzed. Loading cached results.",
            }

    topic = dynamo.create_topic(
        title=title,
        description=req.text or "",
        source_url=req.url or "",
    )
    topic_id = topic["topic_id"]

    # Store YouTube video as first article if applicable
    articles = []
    if youtube_data and yt_content:
        yt_article = dynamo.store_article(
            topic_id=topic_id,
            source_id="youtube",
            title=yt_title or "YouTube Video",
            url=req.url or "",
            content=yt_content,
            language="en",
            published_at="",
        )
        articles.append(yt_article)

    # Fetch related articles from GDELT + other sources
    gdelt_debug = {}
    if req.url or req.text or req.title:
        query = youtube_search_query or req.title or req.url or req.text or ""
        gdelt_results = []
        newsapi_results = []
        mediacloud_results = []

        # GDELT with retry (rate limit returns 0 results)
        import time as _time
        for attempt in range(3):
            try:
                gdelt_results = gdelt_service.search_articles(
                    query, source_url=req.url or "",
                )
                if gdelt_results:
                    break
                if attempt < 2:
                    _time.sleep(7)
            except Exception as e:
                logger.warning("GDELT attempt %d error: %s", attempt, e)
                if attempt < 2:
                    _time.sleep(7)
        try:
            newsapi_results = newsapi_service.search_articles(query, max_results=10)
        except Exception as e:
            logger.warning("NewsAPI error: %s", e)
        try:
            mediacloud_results = mediacloud_service.search_articles(query, max_results=10)
        except Exception as e:
            logger.warning("MediaCloud error: %s", e)

        # Merge all sources, deduplicate by URL
        all_results = gdelt_results + newsapi_results + mediacloud_results
        seen_urls = set()
        gdelt_results = []
        for r in all_results:
            url_key = r.get("url", "")
            if url_key and url_key not in seen_urls:
                seen_urls.add(url_key)
                gdelt_results.append(r)
        gdelt_debug = {
            "query_used": query[:100],
            "gdelt": len([
                r for r in all_results
                if r.get("provider") not in ("newsapi", "mediacloud")
            ]),
            "newsapi": len(newsapi_results),
            "mediacloud": len(mediacloud_results),
            "total_deduped": len(gdelt_results),
        }

        for result in gdelt_results[:20]:
            # Fetch actual article content for top 5 (rest use title only)
            content = result.get("content", "")
            article_url = result.get("url", "")
            if article_url and len(content) < 100:
                fetched = article_fetcher.fetch_article_text(article_url)
                if fetched:
                    content = fetched

            article = dynamo.store_article(
                topic_id=topic_id,
                source_id=result.get("source_id", "unknown"),
                title=result.get("title", ""),
                url=article_url,
                content=content or result.get("title", ""),
                language=result.get("language", "en"),
                published_at=result.get("published_at", ""),
            )
            articles.append(article)

    source_count = len(articles)
    languages = len({a.get("language", "en") for a in articles})
    status = "ready" if articles else "no_sources"
    dynamo.update_topic_status(topic_id, status, source_count, languages)

    return {
        "topic_id": topic_id,
        "title": title,
        "status": status,
        "articles_found": source_count,
        "languages": languages,
        "gdelt_debug": gdelt_debug,
        "message": f"Found {source_count} sources in {languages} languages. "
                   "Use GET /api/v1/perspectives/{topic_id} for side-by-side comparison.",
    }


@router.get("/search")
async def search(q: str = "", request: Request = None):
    """Full-text search across all analyzed articles."""
    if not q or len(q) < 2:
        return {"error": "Query too short", "results": []}
    results = search_service.search_articles(q)
    return {"query": q, "results": results, "count": len(results)}


@router.get("/topics")
async def list_topics(request: Request):
    """List analyzed topics, deduplicated by title (keeps latest)."""
    topics = dynamo.list_topics(limit=100)

    # Dedup: keep the best version per title (prefer ready > no_sources)
    seen: dict[str, dict] = {}
    for t in topics:
        title = t.get("title", "").strip()
        existing = seen.get(title)
        if not existing:
            seen[title] = t
        else:
            # Prefer topic with sources over one without
            e_count = int(existing.get("source_count", 0))
            t_count = int(t.get("source_count", 0))
            if t_count > e_count:
                seen[title] = t

    deduped = sorted(
        seen.values(),
        key=lambda x: x.get("created_at", ""),
        reverse=True,
    )

    return {
        "topics": [
            {
                "topic_id": t["topic_id"],
                "title": t["title"],
                "status": t.get("status", "unknown"),
                "source_count": int(t.get("source_count", 0)),
                "created_at": t.get("created_at", ""),
            }
            for t in deduped
        ],
        "count": len(deduped),
    }


@router.post("/topics/{topic_id}/rescore")
async def rescore_topic(topic_id: str, request: Request):
    """Re-fetch article content and re-score bias for an existing topic.

    Use this to upgrade topics that were created before the article
    fetcher was added (they only have title-based scoring).
    """
    topic = dynamo.get_topic(topic_id)
    if not topic:
        return {"error": "Topic not found"}

    articles = dynamo.get_articles_for_topic(topic_id)
    if not articles:
        return {"error": "No articles to rescore", "topic_id": topic_id}

    rescored = 0
    for article in articles:
        content = article.get("content", "")
        article_url = article.get("url", "")

        # Re-fetch if content is just the title (< 100 chars)
        if article_url and len(content) < 100:
            fetched = article_fetcher.fetch_article_text(article_url)
            if fetched:
                content = fetched

        if len(content) > 100:
            rescored += 1

    # Clear cached analysis results
    dynamo.clear_cached_results(topic_id)

    # Delete old perspectives so they regenerate with new scores
    old_perspectives = dynamo.get_perspectives(topic_id)
    from src.db.dynamo import _perspectives_table
    for p in old_perspectives:
        _perspectives_table().delete_item(
            Key={"PK": p["PK"], "SK": p["SK"]},
        )

    # Re-store articles with fetched content
    for article in articles:
        article_url = article.get("url", "")
        if article_url and len(article.get("content", "")) < 100:
            fetched = article_fetcher.fetch_article_text(article_url)
            if fetched:
                from src.db.dynamo import _articles_table
                _articles_table().update_item(
                    Key={"PK": article["PK"], "SK": article["SK"]},
                    UpdateExpression="SET content = :c",
                    ExpressionAttributeValues={":c": fetched[:10000]},
                )

    return {
        "topic_id": topic_id,
        "articles_total": len(articles),
        "articles_rescored": rescored,
        "perspectives_cleared": len(old_perspectives),
        "message": "Perspectives cleared. Visit the topic page to regenerate with new scores.",
    }


@router.get("/topics/{topic_id}")
async def get_topic(topic_id: str, request: Request):
    """Get full topic detail with perspectives."""
    topic = dynamo.get_topic(topic_id)
    if not topic:
        return {"error": "Topic not found"}, 404

    perspectives = dynamo.get_perspectives(topic_id)
    articles = dynamo.get_articles_for_topic(topic_id)

    return {
        "topic_id": topic_id,
        "title": topic.get("title", ""),
        "status": topic.get("status", ""),
        "source_count": int(topic.get("source_count", 0)),
        "language_count": int(topic.get("language_count", 0)),
        "perspectives": len(perspectives),
        "articles": len(articles),
        "created_at": topic.get("created_at", ""),
    }
