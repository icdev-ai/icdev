# CUI // SP-CTI
"""Slide Deck Research Connector — topic research with air-gap branching.

Connected mode attempts a lightweight web search (DuckDuckGo HTML). If the
search fails or is disabled, it falls back to an LLM-only research summary.
Air-gapped mode uses local LLM knowledge plus an optional internal Knowledge
Graph search; it never calls external web endpoints.

Output schema:
    {
        "summary": str,
        "sources": [{"title": str, "url": str, "snippet": str}],
        "citation_style": str,
    }
"""
from __future__ import annotations

import html
import json
import re
from pathlib import Path
from typing import Any

from tools.airgap.detector import is_airgap
from tools.slides.constants import DEFAULT_CITATION_STYLE, CITATION_STYLES

_BASE_DIR = Path(__file__).resolve().parents[3]
_CONFIG_PATH = _BASE_DIR / "args" / "slides_config.yaml"

# Simple in-process cache keyed by normalized query
_CACHE: dict[str, dict[str, Any]] = {}


def _load_yaml_config() -> dict[str, Any]:
    try:
        import yaml

        if _CONFIG_PATH.exists():
            with open(_CONFIG_PATH, "r", encoding="utf-8") as fh:
                return yaml.safe_load(fh) or {}
    except Exception:
        pass
    return {}


def _config() -> dict[str, Any]:
    return _load_yaml_config().get("research", {})


def _normalize_query(topic: str, occasion: str | None, audience: str | None) -> str:
    parts = [topic]
    if occasion:
        parts.append(occasion)
    if audience:
        parts.append(audience)
    return " ".join(parts).strip().lower()


def _parse_ddg_results(html_text: str, max_results: int = 10) -> list[dict[str, str]]:
    """Parse DuckDuckGo HTML result page into source dicts."""
    sources: list[dict[str, str]] = []
    # Each result is wrapped in a div with class containing 'result'
    blocks = re.split(r'<div class="result results_links[^"]*"', html_text)
    for block in blocks[1:]:
        link_match = re.search(r'<a rel="nofollow" class="result__a" href="([^"]+)"[^>]*>(.*?)</a>', block, re.S)
        if not link_match:
            continue
        url = html.unescape(link_match.group(1))
        title = re.sub(r"<[^>]+>", "", link_match.group(2)).strip()
        snippet_match = re.search(r'<a[^>]*class="result__snippet"[^>]*>(.*?)</a>', block, re.S)
        if not snippet_match:
            snippet_match = re.search(r'<div[^>]*class="result__snippet"[^>]*>(.*?)</div>', block, re.S)
        snippet = ""
        if snippet_match:
            snippet = re.sub(r"<[^>]+>", "", snippet_match.group(1)).strip()
        if title and url and not url.startswith("/"):
            sources.append({
                "title": html.unescape(title)[:200],
                "url": url,
                "snippet": html.unescape(snippet)[:400],
            })
        if len(sources) >= max_results:
            break
    return sources


def _duckduckgo_search(query: str, max_results: int = 10) -> list[dict[str, str]]:
    """Best-effort DuckDuckGo HTML search."""
    try:
        from tools.http.client import request

        url = "https://html.duckduckgo.com/html/"
        resp = request("GET", url, params={"q": query}, timeout=15)
        if resp.status_code != 200:
            return []
        return _parse_ddg_results(resp.text, max_results=max_results)
    except Exception:
        return []


def _llm_research(query: str, airgap: bool) -> dict[str, Any]:
    """Use the local LLM to synthesize a research summary and sources.

    In air-gap mode this relies entirely on the model's weights and any
    local context; in connected mode it is a fallback when web search fails.
    """
    try:
        from tools.llm.router import LLMRouter
        from tools.llm.provider import LLMRequest
    except Exception:
        # Honesty: the LLM is unavailable — do NOT fabricate a research summary
        # or invent citations. Signal degradation so the deck can flag it.
        return {
            "summary": "",
            "sources": [],
            "degraded": True,
        }

    system = (
        "You are a research assistant preparing background for a presentation. "
        "Given a topic, occasion, and audience, produce a concise research summary "
        "and a short list of authoritative sources. "
        "Return ONLY valid JSON with keys: summary (string), sources (list of "
        "{title, url, snippet}). If you do not know a real URL, leave it empty."
    )
    user = f"Topic/occasion/audience: {query}\nWrite a research summary and source list."

    router = LLMRouter()
    request = LLMRequest(
        messages=[{"role": "user", "content": user}],
        system_prompt=system,
        max_tokens=1024,
        temperature=0.3,
        agent_id="slides-research",
        classification="CUI",
        effort="medium",
        skip_injection_scan=True,
    )
    try:
        response = router.invoke("slides_topic_research", request)
        raw = response.content or ""
        # Extract JSON block
        m = re.search(r"\{.*\}", raw, re.S)
        if m:
            parsed = json.loads(m.group(0))
        else:
            parsed = json.loads(raw)
        summary = str(parsed.get("summary", "")).strip()
        sources = [
            {
                "title": str(s.get("title", "")),
                "url": str(s.get("url", "")),
                "snippet": str(s.get("snippet", "")),
            }
            for s in parsed.get("sources", [])
            if isinstance(s, dict)
        ]
        # A real summary is honest content; an empty one means the model
        # returned nothing usable — flag it rather than inventing text.
        return {"summary": summary, "sources": sources, "degraded": not summary}
    except Exception:
        # Parse/generation failure — do not fabricate a summary.
        return {"summary": "", "sources": [], "degraded": True}


def _kg_search(query: str, top_k: int = 5) -> list[dict[str, str]]:
    """Optional internal Knowledge Graph search for air-gap enrichment."""
    try:
        from tools.knowledge_graph.text_network import search_nodes

        result = search_nodes(query)
        nodes = result.get("nodes", [])[:top_k]
        return [
            {
                "title": n.get("label", "KG node"),
                "url": f"/knowledge-graph/graph/{n.get('graph_id', '')}",
                "snippet": f"{n.get('entity_type', 'entity')} in {n.get('graph_name', 'graph')}",
            }
            for n in nodes
        ]
    except Exception:
        return []


def research_topic(
    topic: str,
    occasion: str | None = None,
    target_audience: str | None = None,
    citation_style: str | None = None,
    airgap: bool | None = None,
) -> dict[str, Any]:
    """Research a topic for a general presentation.

    Args:
        topic: Open-ended research topic / title.
        occasion: Optional occasion tag.
        target_audience: Optional audience description.
        citation_style: One of CITATION_STYLES.
        airgap: Override air-gap detection. None = detect.

    Returns:
        Dict with summary, sources, and citation_style.
    """
    if airgap is None:
        try:
            airgap = is_airgap()
        except Exception:
            airgap = True

    cfg = _config()
    cache_ttl = int(cfg.get("cache_ttl_minutes", 0))
    citation_style = (citation_style or cfg.get("default_citation_style") or DEFAULT_CITATION_STYLE)
    if citation_style not in CITATION_STYLES:
        citation_style = DEFAULT_CITATION_STYLE

    query = _normalize_query(topic, occasion, target_audience)
    cache_key = f"{query}::{airgap}"
    if cache_ttl and cache_key in _CACHE:
        cached = _CACHE[cache_key]
        cached["citation_style"] = citation_style
        return cached

    if not airgap:
        sources = _duckduckgo_search(query, max_results=int(cfg.get("max_results", 10)))
        if sources:
            # Build a short summary from source snippets so the engine can cite it
            summary = " ".join(s.get("snippet", "") for s in sources[:5])
            result = {
                "summary": summary,
                "sources": sources,
                "citation_style": citation_style,
                "degraded": False,
            }
            if cache_ttl:
                _CACHE[cache_key] = result
            return result

    # Air-gap or web-search fallback: LLM-only (+ optional KG enrichment)
    llm_result = _llm_research(query, airgap=airgap)
    if airgap:
        kg_sources = _kg_search(query, top_k=3)
        if kg_sources:
            # Merge KG sources after LLM sources; avoid duplicates by title
            seen = {s["title"] for s in llm_result.get("sources", [])}
            for s in kg_sources:
                if s["title"] not in seen:
                    llm_result.setdefault("sources", []).append(s)
                    seen.add(s["title"])

    result = {
        "summary": llm_result.get("summary", ""),
        "sources": llm_result.get("sources", []),
        "citation_style": citation_style,
        "degraded": bool(llm_result.get("degraded", False)),
    }
    if cache_ttl:
        _CACHE[cache_key] = result
    return result


def _format_apa(source: dict[str, str], idx: int) -> str:
    return f"[{idx}] {source.get('title', 'Unknown')}. Retrieved from {source.get('url', '')}"


def _format_mla(source: dict[str, str], idx: int) -> str:
    return f"[{idx}] \"{source.get('title', 'Unknown')}.\" Web. {source.get('url', '')}"


def _format_chicago(source: dict[str, str], idx: int) -> str:
    return f"[{idx}] \"{source.get('title', 'Unknown')}.\" Accessed {source.get('url', '')}."


def _format_inline(source: dict[str, str], idx: int) -> str:
    url = source.get("url", "")
    title = source.get("title", "Source")
    return f"[{idx}] {title}" if not url else f"[{idx}] {title} ({url})"


def format_citations(sources: list[dict[str, str]], style: str | None = None) -> list[str]:
    """Format a list of source dicts into citation strings."""
    style = style or DEFAULT_CITATION_STYLE
    formatters = {
        "apa": _format_apa,
        "mla": _format_mla,
        "chicago": _format_chicago,
        "inline_links": _format_inline,
    }
    formatter = formatters.get(style, _format_inline)
    return [formatter(s, i + 1) for i, s in enumerate(sources) if s]


def _short_citation(source: dict[str, str], idx: int) -> str:
    """Short bracket citation for inline slide use."""
    url = source.get("url", "")
    if url:
        return f"[{idx}]({url})"
    return f"[{idx}]"


def inline_citations(sources: list[dict[str, str]]) -> list[str]:
    """Return short Markdown-style citations for a slide."""
    return [_short_citation(s, i + 1) for i, s in enumerate(sources) if s]


if __name__ == "__main__":
    import sys

    query = sys.argv[1] if len(sys.argv) > 1 else "sustainable aviation technology"
    r = research_topic(query, occasion="keynote", target_audience="industry analysts")
    print(json.dumps(r, indent=2, default=str))
