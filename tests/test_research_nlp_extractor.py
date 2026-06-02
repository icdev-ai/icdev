"""Tests for the NLP link extractor in tools/genesis/reflexes/research.py.

Covers:
- Gate disabled → returns None (regex fallback path)
- Gate enabled, LLM returns links → list returned and cached
- Gate enabled, LLM returns empty response → None
- Gate enabled, import fails → None
- Cache hit avoids second LLM call
- html_scrape feed integration: NLP path used when enabled, regex fallback when disabled
"""
import importlib
import sys
import types
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _reload_research(nlp_enabled: bool):
    """Reload the research module with the NLP gate set to the desired value."""
    env_patch = {"ICDEV_RESEARCH_NLP_SCRAPER": "true" if nlp_enabled else ""}
    with patch.dict("os.environ", env_patch, clear=False):
        # Drop any cached module so the module-level constants re-evaluate
        for mod_name in list(sys.modules):
            if "research" in mod_name and "reflexes" in mod_name:
                del sys.modules[mod_name]
        import tools.genesis.reflexes.research as m
        return m


def _fake_llm_response(content: str):
    class FakeResp:
        pass
    r = FakeResp()
    r.content = content

    class FakeRouter:
        def invoke(self, fn, req):
            return r

    class FakeModule:
        class LLMRouter(FakeRouter):
            pass

        class LLMRequest:
            def __init__(self, **kwargs):
                pass

    return FakeModule


# ---------------------------------------------------------------------------
# Unit tests for _extract_links_nlp
# ---------------------------------------------------------------------------

class TestExtractLinksNlp:
    def test_disabled_returns_none(self):
        mod = _reload_research(nlp_enabled=False)
        result = mod._extract_links_nlp("<html><a href='/foo'>foo</a></html>", {})
        assert result is None

    def test_enabled_returns_links(self):
        mod = _reload_research(nlp_enabled=True)
        mod._NLP_LINK_CACHE.clear()

        fake = _fake_llm_response("/foo/bar\n/baz/qux\n")
        with patch.dict("sys.modules", {
            "tools.llm.router": fake,
            "tools.llm.provider": fake,
        }):
            result = mod._extract_links_nlp("<html><a href='/foo/bar'>x</a></html>", {})

        assert result == ["/foo/bar", "/baz/qux"]

    def test_enabled_empty_response_returns_none(self):
        mod = _reload_research(nlp_enabled=True)
        mod._NLP_LINK_CACHE.clear()

        fake = _fake_llm_response("")
        with patch.dict("sys.modules", {
            "tools.llm.router": fake,
            "tools.llm.provider": fake,
        }):
            result = mod._extract_links_nlp("<html></html>", {})

        assert result is None

    def test_enabled_none_response_returns_none(self):
        mod = _reload_research(nlp_enabled=True)
        mod._NLP_LINK_CACHE.clear()

        class FakeResp:
            content = None

        class FakeRouter:
            def invoke(self, fn, req):
                return FakeResp()

        class FakeModule:
            LLMRouter = FakeRouter

            class LLMRequest:
                def __init__(self, **kwargs):
                    pass

        with patch.dict("sys.modules", {
            "tools.llm.router": FakeModule,
            "tools.llm.provider": FakeModule,
        }):
            result = mod._extract_links_nlp("<html></html>", {})

        assert result is None

    def test_import_error_returns_none(self):
        mod = _reload_research(nlp_enabled=True)
        mod._NLP_LINK_CACHE.clear()

        with patch.dict("sys.modules", {
            "tools.llm.router": None,
            "tools.llm.provider": None,
        }):
            result = mod._extract_links_nlp("<html></html>", {})

        assert result is None

    def test_cache_hit_skips_second_llm_call(self):
        mod = _reload_research(nlp_enabled=True)
        mod._NLP_LINK_CACHE.clear()

        call_count = [0]

        class FakeResp:
            content = "/cached/link\n"

        class FakeRouter:
            def invoke(self, fn, req):
                call_count[0] += 1
                return FakeResp()

        class FakeModule:
            LLMRouter = FakeRouter

            class LLMRequest:
                def __init__(self, **kwargs):
                    pass

        html = "<html><a href='/cached/link'>x</a></html>"
        with patch.dict("sys.modules", {
            "tools.llm.router": FakeModule,
            "tools.llm.provider": FakeModule,
        }):
            r1 = mod._extract_links_nlp(html, {})
            r2 = mod._extract_links_nlp(html, {})  # same input → cache hit

        assert r1 == ["/cached/link"]
        assert r2 == ["/cached/link"]
        assert call_count[0] == 1  # LLM called only once

    def test_scrape_pattern_included_in_cache_key(self):
        """Different scrape_pattern → different cache entries."""
        mod = _reload_research(nlp_enabled=True)
        mod._NLP_LINK_CACHE.clear()

        responses = ["/match/a\n", "/match/b\n"]
        idx = [0]

        class FakeResp:
            @property
            def content(self_):
                val = responses[idx[0]]
                idx[0] += 1
                return val

        class FakeRouter:
            def invoke(self, fn, req):
                return FakeResp()

        class FakeModule:
            LLMRouter = FakeRouter

            class LLMRequest:
                def __init__(self, **kwargs):
                    pass

        html = "<html><a href='/match/a'>a</a><a href='/match/b'>b</a></html>"
        with patch.dict("sys.modules", {
            "tools.llm.router": FakeModule,
            "tools.llm.provider": FakeModule,
        }):
            r1 = mod._extract_links_nlp(html, {"scrape_pattern": "alpha"})
            r2 = mod._extract_links_nlp(html, {"scrape_pattern": "beta"})

        assert r1 != r2


# ---------------------------------------------------------------------------
# Integration: html_scrape feed path in run()
# ---------------------------------------------------------------------------

class TestHtmlScrapeFeedIntegration:
    """Verify that run() uses NLP extractor when enabled, regex when disabled."""

    _SIMPLE_HTML = '<html><a href="/advisory/2025-001">cve</a></html>'

    def _run_with_html_feed(self, mod, html_content, feed_extra=None):
        """Patch _fetch_url and call run() with a single html_scrape feed."""
        feed = {"name": "test-scrape", "type": "html_scrape", "url": "https://example.com"}
        if feed_extra:
            feed.update(feed_extra)

        with patch.object(mod, "_fetch_url", return_value=html_content), \
             patch.object(mod, "_load_feeds", return_value=[feed]), \
             patch.object(mod, "_is_air_gapped", return_value=False):
            return mod.run({}, None)

    def test_nlp_path_used_when_enabled(self):
        mod = _reload_research(nlp_enabled=True)
        mod._NLP_LINK_CACHE.clear()

        fake = _fake_llm_response("/advisory/2025-001\n")
        with patch.dict("sys.modules", {
            "tools.llm.router": fake,
            "tools.llm.provider": fake,
        }):
            result = self._run_with_html_feed(mod, self._SIMPLE_HTML)

        assert result["success"] is True
        feed_res = result["details"]["feed_results"][0]
        assert feed_res["status"] == "ok"
        assert feed_res["new_entries"] == 1

    def test_regex_fallback_when_nlp_disabled(self):
        mod = _reload_research(nlp_enabled=False)

        result = self._run_with_html_feed(mod, self._SIMPLE_HTML)

        assert result["success"] is True
        feed_res = result["details"]["feed_results"][0]
        assert feed_res["status"] == "ok"
        assert feed_res["new_entries"] == 1

    def test_regex_fallback_filters_by_scrape_pattern(self):
        """When NLP disabled, scrape_pattern still filters hrefs."""
        mod = _reload_research(nlp_enabled=False)
        html = '<html><a href="/advisory/x">x</a><a href="/unrelated/y">y</a></html>'

        result = self._run_with_html_feed(mod, html, feed_extra={"scrape_pattern": "advisory"})

        feed_res = result["details"]["feed_results"][0]
        assert feed_res["new_entries"] == 1  # only /advisory/x matched

    def test_no_url_returns_skipped(self):
        mod = _reload_research(nlp_enabled=False)
        feed = {"name": "no-url", "type": "html_scrape"}

        with patch.object(mod, "_load_feeds", return_value=[feed]), \
             patch.object(mod, "_is_air_gapped", return_value=False):
            result = mod.run({}, None)

        feed_res = result["details"]["feed_results"][0]
        assert feed_res["status"] == "skipped"

    def test_fetch_failure_returns_fetch_failed(self):
        mod = _reload_research(nlp_enabled=False)
        feed = {"name": "fail", "type": "html_scrape", "url": "https://example.com"}

        with patch.object(mod, "_fetch_url", return_value=None), \
             patch.object(mod, "_load_feeds", return_value=[feed]), \
             patch.object(mod, "_is_air_gapped", return_value=False):
            result = mod.run({}, None)

        feed_res = result["details"]["feed_results"][0]
        assert feed_res["status"] == "fetch_failed"
