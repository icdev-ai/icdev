#!/usr/bin/env python3
"""Sources routes — OSINT data source management."""
import hashlib
import logging

from fastapi import APIRouter, Request
from pydantic import BaseModel

from src.db import dynamo
from src.services import article_fetcher, credibility_db, source_report_card

logger = logging.getLogger("intaas.routes.sources")
router = APIRouter()

# Default OSINT sources
DEFAULT_SOURCES = [
    {"name": "GDELT", "type": "api", "url": "https://api.gdeltproject.org", "profile": "neutral"},
    {"name": "MediaCloud", "type": "api", "url": "https://search.mediacloud.org", "profile": "neutral"},
    {"name": "RSS Feeds", "type": "rss", "url": "", "profile": "varies"},
    {"name": "Semantic Scholar", "type": "api", "url": "https://api.semanticscholar.org", "profile": "academic"},
    {"name": "YouTube", "type": "api", "url": "https://www.googleapis.com/youtube/v3", "profile": "varies"},
    {"name": "Media Bias/Fact Check", "type": "reference", "url": "https://mediabiasfactcheck.com", "profile": "meta"},
    {"name": "AllSides", "type": "reference", "url": "https://allsides.com", "profile": "meta"},
    {"name": "Wikipedia", "type": "api", "url": "https://en.wikipedia.org/w/api.php", "profile": "neutral"},
]


@router.get("")
async def list_sources(request: Request):
    """List configured OSINT data sources."""
    db_sources = dynamo.list_sources()
    if not db_sources:
        return {
            "sources": DEFAULT_SOURCES,
            "count": len(DEFAULT_SOURCES),
            "source": "defaults",
        }
    return {
        "sources": [
            {
                "source_id": s.get("source_id", ""),
                "name": s.get("name", ""),
                "type": s.get("source_type", ""),
                "url": s.get("url", ""),
                "bias_profile": s.get("bias_profile", "unknown"),
                "article_count": int(s.get("article_count", 0)),
            }
            for s in db_sources
        ],
        "count": len(db_sources),
        "source": "database",
    }


class RegisterSourceRequest(BaseModel):
    name: str
    source_type: str = "url"  # url, document, feed, manual, api
    url: str = ""
    bias_profile: str = "unknown"


class UploadArticleRequest(BaseModel):
    title: str
    content: str
    source_name: str = ""
    url: str = ""
    language: str = "en"
    topic_id: str = ""  # attach to existing topic, or empty for standalone


@router.post("/register")
async def register_source(req: RegisterSourceRequest, request: Request):
    """Register a new OSINT source manually."""
    source = dynamo.register_source(
        name=req.name,
        source_type=req.source_type,
        url=req.url,
        bias_profile=req.bias_profile,
    )
    return {"status": "registered", "source": source}


@router.post("/upload")
async def upload_article(req: UploadArticleRequest, request: Request):
    """Upload an article or document for analysis.

    Users can paste article text, upload document content,
    or provide a URL to fetch. Optionally attach to a topic.
    """
    content = req.content
    title = req.title

    # If URL provided and content is short, try fetching
    if req.url and len(content) < 100:
        fetched = article_fetcher.fetch_article_text(req.url)
        if fetched:
            content = fetched

    if not content or len(content) < 10:
        return {"error": "Content too short. Provide text or a valid URL."}

    # Create or use existing topic
    topic_id = req.topic_id
    if not topic_id:
        topic = dynamo.create_topic(
            title=title,
            description="Manually uploaded article",
            source_url=req.url,
        )
        topic_id = topic["topic_id"]

    # Store the article
    source_id = hashlib.sha256(
        (req.source_name or req.url or title).encode()
    ).hexdigest()[:12]

    article = dynamo.store_article(
        topic_id=topic_id,
        source_id=source_id,
        title=title,
        url=req.url,
        content=content,
        language=req.language,
    )

    dynamo.update_topic_status(topic_id, "ready", 1, 1)

    return {
        "status": "uploaded",
        "topic_id": topic_id,
        "article_id": article["article_id"],
        "content_length": len(content),
        "message": "Article uploaded. View at /topic.html?id=" + topic_id,
    }


@router.get("/credibility")
async def get_credibility_db(request: Request):
    """Return the full source credibility database + stats."""
    return {
        "sources": credibility_db.get_all_sources(),
        "stats": credibility_db.get_stats(),
        "grades": credibility_db.CREDIBILITY_GRADES,
    }


@router.get("/credibility/{domain}")
async def lookup_credibility(domain: str, request: Request):
    """Look up credibility rating for a specific domain."""
    return credibility_db.lookup_source(domain)


@router.get("/report-cards")
async def get_all_report_cards(request: Request):
    """Get report cards for all sources seen across topics."""
    cards = source_report_card.get_all_source_cards()
    return {"sources": cards, "count": len(cards)}


@router.get("/report-card/{domain}")
async def get_source_report_card(domain: str, request: Request):
    """Get detailed report card for a specific source."""
    return source_report_card.generate_report_card(domain)
