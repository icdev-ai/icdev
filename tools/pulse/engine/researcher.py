"""ICDEV™ Pulse web research engine.

Scrapes DuckDuckGo HTML search results to find developer pain points
across Reddit, StackOverflow, HackerNews, LinkedIn, DEV.to, and Medium.
No API keys required -- uses requests + BeautifulSoup only.
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import logging
import os
import random
import re
import time
from datetime import datetime, timezone
from uuid import uuid4

import requests
from bs4 import BeautifulSoup

from tools.http.client import get_session, request as _http_request
from tools.pulse import config
from tools.pulse.db import insert_row, query_rows

# Configurable project ID — avoids hardcoding "sparkpilot"
PULSE_PROJECT_ID = os.environ.get("ICDEV_PULSE_PROJECT_ID", os.environ.get("ICDEV_PROJECT_ID", "pulse"))


def _is_air_gapped() -> bool:
    return os.environ.get("ICDEV_ENVIRONMENT", "").lower() == "air-gapped"


logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

DDG_URL = "https://html.duckduckgo.com/html/"

# Map config source names to DuckDuckGo site: filter tokens
SOURCE_SITE_MAP: dict[str, str] = {
    "reddit": "site:reddit.com",
    "stackoverflow": "site:stackoverflow.com",
    "hackernews": "site:news.ycombinator.com",
    "linkedin": "site:linkedin.com",
    "dev.to": "site:dev.to",
    "medium": "site:medium.com",
}

# Keywords that boost relevance when found in title/snippet
RELEVANCE_KEYWORDS: list[str] = [
    "challenge",
    "problem",
    "pain",
    "struggle",
    "frustrating",
    "difficult",
    "broken",
    "slow",
    "tedious",
    "manual",
    "risk",
    "vulnerability",
    "compliance",
    "audit",
    "security",
    "governance",
    "authorization",
    "certification",
    "DevSecOps",
    "ATO",
    "FedRAMP",
    "CMMC",
    "NIST",
    "zero trust",
    "SBOM",
    "supply chain",
    "agentic",
    "AI governance",
]

# Pain-point category detection patterns
PAIN_POINT_PATTERNS: dict[str, list[str]] = {
    "compliance_burden": [
        r"compliance",
        r"audit",
        r"ATO",
        r"FedRAMP",
        r"CMMC",
        r"NIST",
        r"accreditation",
        r"authorization",
    ],
    "security_gaps": [
        r"vulnerabilit",
        r"CVE",
        r"breach",
        r"exploit",
        r"zero.?day",
        r"SAST",
        r"DAST",
        r"supply.?chain.?attack",
    ],
    "tooling_friction": [
        r"slow",
        r"manual",
        r"tedious",
        r"broken.?pipeline",
        r"CI.?CD",
        r"developer.?experience",
        r"DX",
        r"toolchain",
    ],
    "ai_governance": [
        r"AI.?governance",
        r"responsible.?AI",
        r"model.?risk",
        r"prompt.?injection",
        r"hallucin",
        r"agentic",
        r"guardrail",
    ],
    "process_overhead": [
        r"process",
        r"bureaucra",
        r"overhead",
        r"bottleneck",
        r"approval",
        r"red.?tape",
        r"waterfall",
    ],
}

# Recency tokens that suggest freshness (rough heuristic from snippet text)
RECENCY_TOKENS: list[str] = [
    "2026",
    "2025",
    "today",
    "this week",
    "just now",
    "recently",
    "latest",
    "new",
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _session() -> requests.Session:
    """Build a requests session with realistic headers."""
    s = get_session()
    s.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://duckduckgo.com/",
        }
    )
    return s


def _classify_pain_point(text: str) -> str:
    """Return the best-matching pain-point category for *text*."""
    text_lower = text.lower()
    scores: dict[str, int] = {}
    for category, patterns in PAIN_POINT_PATTERNS.items():
        hits = sum(1 for p in patterns if re.search(p, text_lower, re.IGNORECASE))
        if hits:
            scores[category] = hits
    if not scores:
        return "general"
    return max(scores, key=scores.get)  # type: ignore[arg-type]


def _score_relevance(title: str, snippet: str) -> float:
    """Score relevance 0-1 based on keyword density and recency signals."""
    combined = f"{title} {snippet}".lower()

    # Keyword density component (0-0.7)
    keyword_hits = sum(1 for kw in RELEVANCE_KEYWORDS if kw.lower() in combined)
    keyword_score = min(keyword_hits / max(len(RELEVANCE_KEYWORDS) * 0.15, 1), 1.0)

    # Recency component (0-0.3)
    recency_hits = sum(1 for tok in RECENCY_TOKENS if tok.lower() in combined)
    recency_score = min(recency_hits / 2.0, 1.0)

    score = (keyword_score * 0.7) + (recency_score * 0.3)
    return round(min(max(score, 0.0), 1.0), 3)


def _detect_source(url: str) -> str:
    """Identify the source platform from a URL."""
    url_lower = url.lower()
    for name, site_filter in SOURCE_SITE_MAP.items():
        domain = site_filter.replace("site:", "")
        if domain in url_lower:
            return name
    return "other"


def _polite_delay() -> None:
    """Sleep 1-2 seconds between requests to avoid rate-limiting."""
    time.sleep(random.uniform(1.0, 2.0))


# ---------------------------------------------------------------------------
# DuckDuckGo scraper
# ---------------------------------------------------------------------------


_ddg_circuit_open = False  # Circuit breaker: skip DDG if it's down


def _scrape_ddg(query: str, max_results: int = 20) -> list[dict]:
    """Scrape DuckDuckGo HTML search results for *query*.

    Returns a list of dicts with keys: title, url, snippet.
    Includes circuit breaker: if DDG times out, skip remaining requests.
    """
    global _ddg_circuit_open
    if _is_air_gapped():
        logger.info("Air-gapped environment — skipping DuckDuckGo scraping for %r", query)
        return []
    if _ddg_circuit_open:
        logger.debug("DDG circuit open — skipping request for %r", query)
        return []

    session = _session()
    results: list[dict] = []
    seen_urls: set[str] = set()

    try:
        resp = session.post(
            DDG_URL,
            data={"q": query, "b": "", "kl": "us-en"},
            timeout=8,  # Reduced from 15s for faster failure
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.warning("DuckDuckGo request failed for %r: %s", query, exc)
        if "timeout" in str(exc).lower() or "connect" in str(exc).lower():
            _ddg_circuit_open = True
            logger.warning("DDG circuit breaker OPEN — skipping remaining web requests")
        return results

    soup = BeautifulSoup(resp.text, "html.parser")

    # DuckDuckGo HTML results are in <div class="result"> blocks
    for result_div in soup.select(".result"):
        if len(results) >= max_results:
            break

        # Title + URL
        link_tag = result_div.select_one(".result__a")
        if not link_tag:
            continue

        title = link_tag.get_text(strip=True)
        href = link_tag.get("href", "")

        # DuckDuckGo wraps URLs in a redirect; extract the actual URL
        url = _extract_url(str(href))
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)

        # Snippet
        snippet_tag = result_div.select_one(".result__snippet")
        snippet = snippet_tag.get_text(strip=True) if snippet_tag else ""

        results.append(
            {
                "title": title,
                "url": url,
                "snippet": snippet,
            }
        )

    return results


def _extract_url(href: str) -> str:
    """Extract the real URL from a DuckDuckGo redirect link."""
    # DDG HTML links look like //duckduckgo.com/l/?uddg=<encoded_url>&...
    if "uddg=" in href:
        from urllib.parse import parse_qs, urlparse

        parsed = urlparse(href)
        qs = parse_qs(parsed.query)
        candidates = qs.get("uddg", [])
        if candidates:
            return candidates[0]
    # Sometimes the href is a direct URL
    if href.startswith("http"):
        return href
    if href.startswith("//"):
        return "https:" + href
    return href


# ---------------------------------------------------------------------------
# URL deduplication against DB
# ---------------------------------------------------------------------------


def _existing_urls() -> set[str]:
    """Return the set of URLs already stored in research_cache."""
    rows = query_rows("research_cache", limit=10000)
    return {r["url"] for r in rows if r.get("url")}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def research_topic(topic: str, max_results: int = 20) -> list[dict]:
    """Research a single topic across all configured sources.

    Searches DuckDuckGo with per-source site filters, scores results,
    deduplicates by URL, and stores new entries in the research_cache table.

    Returns the list of result dicts for this topic.
    """
    logger.info("Researching topic: %s (max_results=%d)", topic, max_results)
    existing = _existing_urls()
    all_results: list[dict] = []
    now = datetime.now(timezone.utc).isoformat()

    per_source_limit = max(max_results // max(len(config.SEARCH_SOURCES), 1), 3)

    for source_name in config.SEARCH_SOURCES:
        site_filter = SOURCE_SITE_MAP.get(source_name)
        if not site_filter:
            continue

        query = f"{topic} {site_filter}"
        logger.debug("Searching: %s", query)

        raw_results = _scrape_ddg(query, max_results=per_source_limit)
        _polite_delay()

        for raw in raw_results:
            url = raw["url"]

            # Deduplicate
            if url in existing:
                logger.debug("Skipping duplicate URL: %s", url)
                continue
            existing.add(url)

            title = raw["title"]
            snippet = raw["snippet"]
            combined_text = f"{title} {snippet}"
            relevance = _score_relevance(title, snippet)
            pain_category = _classify_pain_point(combined_text)
            detected_source = _detect_source(url)

            entry = {
                "id": str(uuid4()),
                "query": topic,
                "source": detected_source,
                "url": url,
                "title": title,
                "snippet": snippet,
                "relevance_score": relevance,
                "pain_point_category": pain_category,
                "fetched_at": now,
            }

            # Persist
            try:
                insert_row("research_cache", entry)
            except Exception as exc:
                logger.warning("Failed to insert research result %s: %s", url, exc)
                continue

            all_results.append(entry)

            if len(all_results) >= max_results:
                break

        if len(all_results) >= max_results:
            break

    # Sort by relevance descending
    all_results.sort(key=lambda r: r["relevance_score"], reverse=True)
    logger.info(
        "Topic %r: found %d new results (top score %.3f)",
        topic,
        len(all_results),
        all_results[0]["relevance_score"] if all_results else 0.0,
    )
    return all_results


def research_all_topics() -> list[dict]:
    """Research all topics defined in config.RESEARCH_TOPICS.

    Shuffles topic order each run and deprioritizes recently-used topics
    to ensure variety across pipeline runs.

    Iterates through each topic, collects results, and returns
    the combined list sorted by relevance score descending.
    """
    logger.info("Starting research across %d topics", len(config.RESEARCH_TOPICS))
    combined: list[dict] = []

    # Shuffle topics for variety; deprioritize recently-used topics
    topics = list(config.RESEARCH_TOPICS)
    random.shuffle(topics)
    try:
        from tools.pulse.db import get_used_topics

        used = {t.lower() for t in get_used_topics()}
        if used:
            # Move recently-used topics to the end so fresh topics get priority
            fresh = [t for t in topics if t.lower() not in used]
            stale = [t for t in topics if t.lower() in used]
            topics = fresh + stale
            logger.info(
                "Topic rotation: %d fresh, %d recently-used (deprioritized)",
                len(fresh),
                len(stale),
            )
    except Exception:
        pass  # Graceful fallback if DB unavailable

    for topic in topics:
        try:
            results = research_topic(topic, max_results=config.SEARCH_MAX_RESULTS)
            combined.extend(results)
        except Exception as exc:
            logger.error("Failed to research topic %r: %s", topic, exc)
            continue
        # Extra delay between topics
        _polite_delay()

    combined.sort(key=lambda r: r["relevance_score"], reverse=True)
    logger.info("Research complete: %d total results across all topics", len(combined))
    return combined


# ---------------------------------------------------------------------------
# Local RAG + Knowledge Graph enrichment (D-KARL-3 parallel retrieval)
# ---------------------------------------------------------------------------


def _enrich_from_local_rag(topics: list[str]) -> list[dict]:
    """Pull relevant context from local RAG/knowledge graph before web scraping.

    Uses parallel_retrieve (D-KARL-3) to fire GraphRAG + corrective RAG +
    source registry concurrently. Zero Claude tokens (scanner-tier only).
    Falls back gracefully if RAG modules unavailable.

    Runs topic queries in parallel via ThreadPoolExecutor for speed.

    Returns research-cache-shaped dicts for seamless integration with
    the clustering and synthesis pipeline.
    """
    results: list[dict] = []

    try:
        from tools.rag.corrective_rag import parallel_retrieve
    except ImportError:
        logger.debug("RAG module not available — skipping local enrichment")
        return results

    now = datetime.now(timezone.utc).isoformat()
    seen_snippets: set[str] = set()

    def _query_one(topic):
        """Query a single topic via parallel_retrieve."""
        try:
            return parallel_retrieve(
                query=topic,
                project_id=PULSE_PROJECT_ID,
                top_k=5,
                aggregate=False,
            )
        except Exception as exc:
            logger.debug("RAG enrichment failed for topic %r: %s", topic, exc)
            return None

    # Fire all topic queries in parallel (instead of sequential loop)
    from concurrent.futures import ThreadPoolExecutor, as_completed

    capped_topics = topics[:5]  # Cap at 5 topics for speed
    rag_results_map: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {pool.submit(_query_one, t): t for t in capped_topics}
        for future in as_completed(futures):
            topic = futures[future]
            rag_result = future.result()
            if rag_result:
                rag_results_map[topic] = rag_result

    for topic, rag_result in rag_results_map.items():
        try:
            pass  # Structured so existing extraction code stays intact

            # Extract documents from each strategy's results
            for strategy_name, strategy_data in rag_result.get("strategy_results", {}).items():
                docs = []
                if isinstance(strategy_data, dict):
                    docs = strategy_data.get("documents", [])
                elif isinstance(strategy_data, list):
                    docs = strategy_data

                for doc in docs:
                    text = doc.get("text", doc.get("content", ""))
                    if not text or len(text) < 50:
                        continue

                    # Deduplicate by snippet prefix
                    snippet = text[:200].strip()
                    if snippet in seen_snippets:
                        continue
                    seen_snippets.add(snippet)

                    title = doc.get("title", "") or text[:80].strip()
                    relevance = _score_relevance(title, snippet)

                    results.append(
                        {
                            "id": str(uuid4()),
                            "query": topic,
                            "source": f"local_rag:{strategy_name}",
                            "url": doc.get("url", f"rag://{strategy_name}/{doc.get('id', 'unknown')}"),
                            "title": title,
                            "snippet": snippet,
                            "full_text": text,
                            "relevance_score": max(relevance, 0.5),  # Boost local results
                            "pain_point_category": _classify_pain_point(text),
                            "fetched_at": now,
                        }
                    )

        except Exception as exc:
            logger.debug("RAG extraction failed for topic %r: %s", topic, exc)
            continue

    if results:
        logger.info(
            "Local RAG enrichment: %d results from knowledge graph + source registry "
            "(zero web requests, zero Claude tokens)",
            len(results),
        )
    return results


def _research_topics_fast(topics: list[str], max_sources: int = 3) -> list[dict]:
    """Research a small set of topics with limited sources for speed.

    Instead of 15 topics × 6 sources (90 requests), this does
    N topics × max_sources sources = much fewer requests.
    """
    logger.info("Fast web research: %d topics × %d sources", len(topics), max_sources)
    combined: list[dict] = []
    sources = config.SEARCH_SOURCES[:max_sources]  # First N sources only

    for topic in topics:
        try:
            # Temporarily narrow SEARCH_SOURCES for this call
            original_sources = config.SEARCH_SOURCES
            config.SEARCH_SOURCES = sources
            results = research_topic(topic, max_results=config.SEARCH_MAX_RESULTS)
            config.SEARCH_SOURCES = original_sources
            combined.extend(results)
        except Exception as exc:
            logger.error("Failed to research topic %r: %s", topic, exc)
            continue
        _polite_delay()

    combined.sort(key=lambda r: r["relevance_score"], reverse=True)
    logger.info("Fast research complete: %d results", len(combined))
    return combined


def research_with_local_first(
    topic_override: str | None = None,
    template_type: str = "challenge_solution",
) -> list[dict]:
    """Research using local-first strategy: RAG/KG → web scraping for gaps.

    1. Query local knowledge graph + RAG (instant, zero tokens)
    2. If insufficient results, fall back to FAST web scraping (3 topics × 3 sources)
    3. Merge and deduplicate

    Args:
        topic_override: If provided, only research this topic.
        template_type: 'challenge_solution' or 'feature_spotlight' — selects topic pool.

    Returns:
        Combined research results sorted by relevance.
    """
    global _ddg_circuit_open
    _ddg_circuit_open = False  # Reset circuit breaker each pipeline run

    if topic_override:
        topics = [topic_override]
    elif template_type == "feature_spotlight":
        topics = list(getattr(config, "FEATURE_SPOTLIGHT_TOPICS", config.RESEARCH_TOPICS))
    else:
        topics = list(getattr(config, "CHALLENGE_SOLUTION_TOPICS", config.RESEARCH_TOPICS))

    # Phase 1: Local RAG enrichment (fast, free, parallelized)
    local_results = _enrich_from_local_rag(topics)
    logger.info("Phase 1 (local RAG): %d results", len(local_results))

    # Phase 2: Web scraping only if local results are insufficient
    # Use FAST strategy: pick 3 random topics, 3 sources max
    web_results: list[dict] = []
    if len(local_results) < 5:
        # Shuffle and pick 3 topics for fast web research
        import random as _rnd

        web_topics = list(topics)
        _rnd.shuffle(web_topics)
        web_topics = web_topics[:3]  # Only research 3 topics, not all 15

        logger.info(
            "Local results insufficient (%d < 5) — fast web research on %d topics: %s",
            len(local_results),
            len(web_topics),
            web_topics,
        )
        web_results = _research_topics_fast(web_topics, max_sources=3)
    else:
        logger.info(
            "Local RAG provided %d results — skipping web scraping entirely (saved network requests + latency)",
            len(local_results),
        )

    # Merge and deduplicate by URL (use url+query for RAG results to avoid over-dedup)
    seen_keys: set[str] = set()
    combined: list[dict] = []
    for r in local_results + web_results:
        url = r.get("url", "")
        source = r.get("source", "")
        # RAG results may share simulated URLs across topics — use url+query as key
        if source.startswith("local_rag:"):
            dedup_key = f"{url}|{r.get('query', '')}"
        else:
            dedup_key = url
        if dedup_key and dedup_key in seen_keys:
            continue
        seen_keys.add(dedup_key)
        combined.append(r)

    combined.sort(key=lambda r: r.get("relevance_score", 0), reverse=True)

    # Phase 3: If STILL no results (DDG down + KG empty), generate topic-based
    # synthetic entries so the pipeline can continue with Ollama drafting
    if not combined:
        now = datetime.now(timezone.utc).isoformat()
        pick = topics[:3] if len(topics) >= 3 else topics
        for topic in pick:
            combined.append(
                {
                    "id": str(uuid4()),
                    "query": topic,
                    "source": "topic_seed",
                    "url": f"topic://{topic.replace(' ', '-')}",
                    "title": topic,
                    "snippet": f"Research topic: {topic}. Write an authoritative article about current challenges and solutions.",  # noqa: E501
                    "relevance_score": 0.6,
                    "pain_point_category": _classify_pain_point(topic),
                    "fetched_at": now,
                }
            )
        logger.info(
            "No external results available — seeded %d topic-based entries for drafting",
            len(combined),
        )

    logger.info(
        "Research complete: %d total (%d local, %d web)",
        len(combined),
        len(local_results),
        len(web_results),
    )
    return combined


# ---------------------------------------------------------------------------
# Ollama synthesis (two-tier: Ollama analyzes → Claude Code confirms)
# ---------------------------------------------------------------------------


def _ollama_available() -> bool:
    """Check whether Ollama is reachable."""
    try:
        resp = _http_request("GET", f"{config.OLLAMA_BASE_URL}/api/tags", timeout=5)
        return resp.status_code == 200
    except (requests.ConnectionError, requests.Timeout):
        return False


def synthesize_research(results: list[dict]) -> dict:
    """Use Ollama qwen3.5 to synthesize research results into insights.

    Two-tier approach: Ollama does initial synthesis, returns structured
    analysis for Claude Code to confirm and finalize.

    Args:
        results: List of research result dicts with title, snippet,
                 relevance_score, pain_point_category.

    Returns:
        dict with keys: synthesis, top_themes, recommended_angles,
        pain_point_summary, model_used, status.
    """
    if not results:
        return {
            "synthesis": "No research results to synthesize.",
            "top_themes": [],
            "recommended_angles": [],
            "pain_point_summary": {},
            "model_used": "none",
            "status": "no_data",
        }

    # Build compact research digest for Ollama
    digest_lines = []
    for i, r in enumerate(results[:30], 1):  # Cap at 30 for context window
        title = r.get("title", "")[:100]
        snippet = r.get("snippet", "")[:200]
        score = r.get("relevance_score", 0)
        category = r.get("pain_point_category", "general")
        digest_lines.append(f"{i}. [{category}] (score: {score:.2f}) {title}\n   {snippet}")

    digest = "\n".join(digest_lines)

    # Pain point distribution
    categories: dict[str, int] = {}
    for r in results:
        cat = r.get("pain_point_category", "general")
        categories[cat] = categories.get(cat, 0) + 1

    system_msg = (
        "You are a research analyst. Analyze the following web research results "
        "about software development and GovTech pain points. Provide a structured "
        "synthesis in JSON format with these exact keys:\n"
        '- "synthesis": 2-3 paragraph summary of key findings\n'
        '- "top_themes": list of 3-5 dominant themes with brief descriptions\n'
        '- "recommended_angles": list of 2-3 article angles worth writing about\n'
        '- "pain_point_summary": object mapping pain categories to brief assessments\n'
        "Return ONLY valid JSON."
    )

    user_msg = (
        f"## Research Results ({len(results)} signals)\n\n"
        f"## Pain Point Distribution\n{categories}\n\n"
        f"## Top Results\n{digest}"
    )

    # Try Ollama first
    ollama_url = f"{config.OLLAMA_BASE_URL}/api/chat"
    model = getattr(config, "OLLAMA_SYNTHESIS_MODEL", None)
    if not model:
        # Pick a fast model: avoid qwen3.5 (thinking mode burns tokens)
        available_fast = ["gemma3:latest", "llama3.2:3b", "ministral-3:8b"]
        try:
            resp = _http_request("GET", f"{config.OLLAMA_BASE_URL}/api/tags", timeout=3)
            installed = {m["name"] for m in resp.json().get("models", [])}
            model = next((m for m in available_fast if m in installed), "qwen3.5:latest")
        except Exception:
            model = "qwen3.5:latest"

    if _ollama_available():
        try:
            logger.info("Synthesizing research with Ollama (%s)...", model)
            resp = _http_request(
                "POST",
                ollama_url,
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system_msg},
                        {"role": "user", "content": user_msg + "\n\n/nothink"},
                    ],
                    "stream": False,
                    "options": {"temperature": 0.3, "num_predict": 1024},
                },
                timeout=20,  # Short timeout — deterministic fallback is fine
            )
            resp.raise_for_status()
            content = resp.json().get("message", {}).get("content", "")

            if content:
                # Try to parse JSON from response
                import json as _json

                # Strip markdown fences
                clean = re.sub(r"```json\s*", "", content)
                clean = re.sub(r"```\s*$", "", clean)
                # Strip thinking tags if present
                clean = re.sub(r"<think>.*?</think>", "", clean, flags=re.DOTALL)
                clean = clean.strip()

                try:
                    parsed = _json.loads(clean)
                    parsed["model_used"] = model
                    parsed["status"] = "synthesized"
                    parsed["raw_response"] = content
                    logger.info("Research synthesis complete via Ollama")
                    return parsed
                except _json.JSONDecodeError:
                    # Return raw text as synthesis
                    logger.warning("Ollama returned non-JSON, using as raw synthesis")
                    return {
                        "synthesis": content,
                        "top_themes": [],
                        "recommended_angles": [],
                        "pain_point_summary": categories,
                        "model_used": model,
                        "status": "synthesized_raw",
                        "raw_response": content,
                    }

        except Exception as exc:
            logger.warning("Ollama synthesis failed: %s", exc)

    # Deterministic fallback — no LLM available
    logger.info("Ollama unavailable — using deterministic research summary")

    # Build deterministic summary from research signals
    top_categories = sorted(categories.items(), key=lambda x: x[1], reverse=True)
    top_titles = [r.get("title", "") for r in results[:5]]

    return {
        "synthesis": (
            f"Analyzed {len(results)} research signals across "
            f"{len(categories)} pain point categories. "
            f"Top category: {top_categories[0][0] if top_categories else 'general'} "
            f"({top_categories[0][1] if top_categories else 0} signals)."
        ),
        "top_themes": [{"theme": cat, "signal_count": count} for cat, count in top_categories[:5]],
        "recommended_angles": top_titles[:3],
        "pain_point_summary": categories,
        "model_used": "deterministic",
        "status": "deterministic_fallback",
    }


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import json
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    # Ensure DB schema exists
    from tools.pulse.db import init_db

    init_db()

    topic_arg = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else None

    if topic_arg:
        results = research_topic(topic_arg)
    else:
        results = research_all_topics()

    print(json.dumps(results, indent=2))
    print(f"\n--- {len(results)} results ---")
