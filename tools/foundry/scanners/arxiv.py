# CUI // SP-CTI
"""ACF vertical scanner — arXiv (academic papers matching ACF concept keywords).

Demonstration of the registry pattern in ``tools.foundry.scanners``. Queries the
arXiv Atom API (no auth, free) for papers matching the concept keywords declared
in ``args/foundry_config.yaml -> sources.arxiv_acf.categories`` and
``.keywords``. Each match is normalized into the same dict shape
``tools.foundry.harvester._make_signal`` produces, so the downstream
``harvest`` / ``_collapse`` / ``persist_signals`` pipeline can consume the output
unmodified.

This scanner is **deterministic + air-gap safe** in the sense that:
  * No LLM call is made.
  * The HTTP request is optional — when ``tools.http.client.request`` is
    unavailable (air-gap) or the request fails, the scanner returns ``[]``
    (logged at WARNING) instead of raising. A signal with
    ``source_type="scan_error"`` would not be appropriate here because the
    arXiv payload is not the ACF concept — silence is the honest answer.
  * Per-source caps (max_results) are honored.

Config (args/foundry_config.yaml)
---------------------------------
::

    foundry:
      sources:
        arxiv_acf:
          enabled: true
          max_results: 25
          categories: [cs.AI, cs.SE, cs.LG]   # arXiv category filter
          keywords: [agent, foundry, autonomous]   # optional term filter
          rate_limit:
            delay_between_requests_seconds: 3
"""
from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any, Optional

from tools.foundry.harvester import _make_signal
from tools.foundry.scanners import register_source

ARXIV_API = "https://export.arxiv.org/api/query"
ARXIV_NS = {"atom": "http://www.w3.org/2005/Atom"}
DEFAULT_TIMEOUT = 30
MAX_BODY_LENGTH = 4000


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sig_id() -> str:
    return f"fsig-{uuid.uuid4().hex[:12]}"


def _try_http_get(url: str, *, params: dict, timeout: int):
    """Return (text, error_code). Never raises. ``text`` is the raw response
    body when status_code < 400, else None."""
    try:
        from tools.http.client import request as _http_request
    except Exception:
        return None, "http_client_unavailable"
    try:
        resp = _http_request("GET", url, params=params, timeout=timeout)
    except Exception as exc:  # noqa: BLE001 - be quiet about transport-level errors
        return None, f"request_error:{exc.__class__.__name__}"
    if getattr(resp, "status_code", 0) >= 400:
        return None, f"http_{getattr(resp, 'status_code', '?')}"
    return getattr(resp, "text", None), None


def _parse_atom_entries(raw_text: str) -> list[dict]:
    """Parse arXiv Atom XML into a list of paper dicts (best-effort)."""
    if not raw_text:
        return []
    try:
        root = ET.fromstring(raw_text)  # nosec B314 -- arXiv response, parsed read-only
    except ET.ParseError:
        return []
    out: list[dict] = []
    for entry in root.findall("atom:entry", ARXIV_NS):
        title_el = entry.find("atom:title", ARXIV_NS)
        summary_el = entry.find("atom:summary", ARXIV_NS)
        id_el = entry.find("atom:id", ARXIV_NS)
        published_el = entry.find("atom:published", ARXIV_NS)

        title = (title_el.text or "").strip().replace("\n", " ") if title_el is not None else ""
        summary = (summary_el.text or "").strip().replace("\n", " ") if summary_el is not None else ""
        paper_url = (id_el.text or "").strip() if id_el is not None else ""
        published = (published_el.text or "").strip() if published_el is not None else ""

        authors: list[str] = []
        for a in entry.findall("atom:author", ARXIV_NS):
            name_el = a.find("atom:name", ARXIV_NS)
            if name_el is not None and name_el.text:
                authors.append(name_el.text.strip())

        arxiv_id = paper_url.split("/abs/")[-1] if "/abs/" in paper_url else paper_url

        out.append(
            {
                "arxiv_id": arxiv_id,
                "title": title,
                "summary": summary,
                "url": paper_url,
                "authors": authors,
                "published": published,
            }
        )
    return out


def _kw_relevance(paper: dict, keywords: list[str]) -> bool:
    """Loose relevance gate — if any keyword appears in title or summary, pass.

    When the vertical config supplies no keywords we accept every paper (the
    category filter alone is enough of a scope)."""
    if not keywords:
        return True
    haystack = f"{paper['title']} {paper['summary']}".lower()
    for kw in keywords:
        if not kw:
            continue
        if kw.lower() in haystack:
            return True
    return False


@register_source("arxiv_acf")
def scan_arxiv_acf(
    config: dict,
    *,
    conn: Any = None,  # noqa: ARG001 -- arXiv scanner does not need DB access
    db_path: Optional[str] = None,  # noqa: ARG001
    **kwargs: Any,
) -> list[dict]:
    """Pull arXiv papers matching ACF concept keywords → foundry signal dicts.

    Honors ``config['sources']['arxiv_acf']``:
      * ``enabled`` (bool, default True) — honor the per-source toggle
      * ``max_results`` (int, default 25) — cap per category
      * ``categories`` (list[str], default ``['cs.AI']``) — arXiv categories
      * ``keywords`` (list[str], default ``[]``) — relevance gate
      * ``rate_limit.delay_between_requests_seconds`` (float, default 3)
    """
    src_cfg = (config or {}).get("sources", {}).get("arxiv_acf", {})
    if isinstance(src_cfg, dict) and src_cfg.get("enabled") is False:
        return []

    max_results = int(src_cfg.get("max_results", 25) or 25)
    categories = src_cfg.get("categories") or ["cs.AI"]
    keywords = src_cfg.get("keywords") or []
    delay = float(
        ((src_cfg.get("rate_limit") or {}).get("delay_between_requests_seconds", 3)) or 3
    )

    signals: list[dict] = []
    for category in categories:
        params = {
            "search_query": f"cat:{category}",
            "start": 0,
            "max_results": max_results,
            "sortBy": "submittedDate",
            "sortOrder": "descending",
        }
        raw, err = _try_http_get(ARXIV_API, params=params, timeout=DEFAULT_TIMEOUT)
        if err or not raw:
            # Air-gap / network failure — surface nothing, keep cycle moving.
            import logging
            logging.getLogger("icdev.foundry.scanners").debug(
                "arxiv_acf scan skipped: %s", err
            )
            time.sleep(delay)
            continue

        papers = _parse_atom_entries(raw)[:max_results]
        for paper in papers:
            if not _kw_relevance(paper, keywords):
                continue
            theme = paper["title"][:500]
            if not theme:
                continue
            # arXiv id doubles as a stable dedup key. We reuse the canonical
            # dedup_hash from harvester so this signal collapses against
            # a Genesis/telemetry signal referencing the same paper.
            kws = [category, "arxiv_paper"]
            for w in re.split(r"\s+", paper["title"]):
                if len(w) > 3:
                    kws.append(w)
            signals.append(
                _make_signal(
                    "arxiv_acf",
                    theme=theme,
                    keywords=kws,
                    summary=paper["summary"][:500],
                    source_type="arxiv_paper",
                    source_ref=paper["arxiv_id"],
                    metadata={
                        "arxiv_id": paper["arxiv_id"],
                        "category": category,
                        "authors": paper["authors"][:5],
                        "published": paper["published"],
                        "url": paper["url"],
                    },
                )
            )
        time.sleep(delay)

    return signals


__all__ = ["scan_arxiv_acf"]
