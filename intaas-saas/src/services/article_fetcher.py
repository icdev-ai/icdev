#!/usr/bin/env python3
"""Article content fetcher — extracts text from news article URLs.

Lightweight HTML-to-text extraction using stdlib only.
No newspaper3k/beautifulsoup needed — uses regex on HTML.
"""
import logging
import re
import time

import requests

logger = logging.getLogger("intaas.services.article_fetcher")

TIMEOUT = 15
USER_AGENT = "INTaaS/2.0 (Multiperspectivity Research)"
MIN_FETCH_INTERVAL = 1.0  # 1s between fetches to be polite
_last_fetch = 0.0


def fetch_article_text(url: str, max_chars: int = 5000) -> str:
    """Fetch article URL and extract readable text content.

    Returns extracted text, or empty string on failure.
    """
    global _last_fetch
    if not url or not url.startswith("http"):
        return ""

    # Rate limit fetches
    now = time.time()
    elapsed = now - _last_fetch
    if elapsed < MIN_FETCH_INTERVAL:
        time.sleep(MIN_FETCH_INTERVAL - elapsed)
    _last_fetch = time.time()

    try:
        resp = requests.get(
            url,
            timeout=TIMEOUT,
            headers={"User-Agent": USER_AGENT},
            allow_redirects=True,
        )
        resp.raise_for_status()
        html = resp.text
    except Exception as e:
        logger.debug("Fetch failed for %s: %s", url[:60], e)
        return ""

    return _extract_text(html, max_chars)


def _extract_text(html: str, max_chars: int = 5000) -> str:
    """Extract readable text from HTML using regex.

    Strategy:
      1. Remove script/style/nav/header/footer tags
      2. Extract <p> tag contents
      3. Strip remaining HTML tags
      4. Clean whitespace
    """
    if not html:
        return ""

    # Remove unwanted blocks
    for tag in ["script", "style", "nav", "header", "footer", "aside", "noscript"]:
        html = re.sub(
            rf"<{tag}[^>]*>.*?</{tag}>",
            " ", html, flags=re.DOTALL | re.IGNORECASE,
        )

    # Extract paragraph content (most article text lives in <p> tags)
    paragraphs = re.findall(
        r"<p[^>]*>(.*?)</p>",
        html, flags=re.DOTALL | re.IGNORECASE,
    )

    if paragraphs:
        text = " ".join(paragraphs)
    else:
        # Fallback: extract from <article> or <main>
        article = re.search(
            r"<(?:article|main)[^>]*>(.*?)</(?:article|main)>",
            html, flags=re.DOTALL | re.IGNORECASE,
        )
        text = article.group(1) if article else html

    # Strip all remaining HTML tags
    text = re.sub(r"<[^>]+>", " ", text)

    # Decode HTML entities
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    text = re.sub(r"&quot;", '"', text)
    text = re.sub(r"&#\d+;", " ", text)
    text = re.sub(r"&\w+;", " ", text)

    # Clean whitespace
    text = re.sub(r"\s+", " ", text).strip()

    return text[:max_chars] if text else ""
