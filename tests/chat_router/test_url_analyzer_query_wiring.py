# CUI // SP-CTI
"""The two-pass filter must actually be *reachable* from the defect site.

`oss-filter-01` exists because `url_analyzer` cut page content at
`_MAX_CONTENT` characters regardless of where the answer sat.  Pass 2 only
fixes that if a query reaches it, so these tests pin the wiring rather than
the filter: `analyze()` -> `fetch_content(query=...)` -> `extract(query=...)`.

Without them the BM25 pass is dead code at the one call site it was built for.
"""

from __future__ import annotations

import importlib

import pytest

url_analyzer = importlib.import_module("tools.chat_router.url_analyzer")


_HTML = (
    "<html><head><title>Doc</title></head><body>"
    "<nav>home about contact</nav>"
    "<article><h1>Doc</h1><p>" + ("filler sentence about nothing in particular. " * 40)
    + "</p></article></body></html>"
)


def test_fetch_content_forwards_query_to_extract(monkeypatch):
    """The query must survive the hop into the extractor, not be dropped."""
    seen: dict = {}

    def fake_extract(html, *, query=None, **kwargs):
        seen["query"] = query
        return {
            "title": "Doc",
            "raw_text": "raw",
            "fit_markdown": "# Doc\n\nfiltered body",
            "links": [],
            "dropped_blocks": [],
            "reason": "test",
        }

    page_extract = importlib.import_module("tools.http.page_extract")
    monkeypatch.setattr(page_extract, "extract", fake_extract)
    monkeypatch.setattr(url_analyzer, "_fetch", lambda url: _HTML)

    content, source_type = url_analyzer.fetch_content(
        "https://example.test/doc", query="observability coverage"
    )

    assert source_type == "web"
    assert seen["query"] == "observability coverage"
    assert content == "# Doc\n\nfiltered body"


def test_fetch_content_falls_back_when_extraction_fails(monkeypatch):
    """A broken extractor must degrade to the regex strip, never break the fetch."""

    def boom(html, *, query=None, **kwargs):
        raise RuntimeError("extractor exploded")

    page_extract = importlib.import_module("tools.http.page_extract")
    monkeypatch.setattr(page_extract, "extract", boom)
    monkeypatch.setattr(url_analyzer, "_fetch", lambda url: _HTML)

    content, source_type = url_analyzer.fetch_content("https://example.test/doc", query="q")

    assert source_type == "web"
    assert "filler sentence" in content


@pytest.mark.parametrize(
    "canvas_type",
    ["odc", "intake", "unknown-canvas"],
)
def test_analyze_passes_the_canvas_lens_as_the_query(monkeypatch, canvas_type):
    """The lens is the only "what is the reader after" signal we have — use it."""
    seen: dict = {}

    def fake_fetch_content(url, query=None):
        seen["query"] = query
        return "page body", "web"

    monkeypatch.setattr(url_analyzer, "fetch_content", fake_fetch_content)

    # Short-circuit the LLM call; we only care about what reached the fetcher.
    monkeypatch.setitem(
        __import__("sys").modules,
        "tools.llm.router",
        type("M", (), {"LLMRouter": lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no llm"))}),
    )

    url_analyzer.analyze("https://example.test/doc", canvas_type=canvas_type)

    expected = url_analyzer._CANVAS_LENS.get(canvas_type.lower(), url_analyzer._DEFAULT_LENS)
    assert seen["query"] == expected
    assert seen["query"], "lens must be a non-empty query, not None"


def test_error_fetches_still_short_circuit(monkeypatch):
    """Reordering the lens above the fetch must not swallow the error path."""
    monkeypatch.setattr(
        url_analyzer, "fetch_content", lambda url, query=None: ("boom", "error")
    )

    result = url_analyzer.analyze("https://example.test/doc", canvas_type="odc")

    assert result["source_type"] == "error"
    assert result["error"] == "boom"
