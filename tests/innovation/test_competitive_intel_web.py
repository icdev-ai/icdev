# CUI // SP-CTI
"""Website scanning for website-only competitors (changelog, release notes, RSS/Atom) and the GitHub token."""

from __future__ import annotations

import pytest

from tools.innovation import competitive_intel as ci


class _Resp:
    def __init__(self, status=200, text="", ctype="text/html"):
        self.status_code = status
        self.text = text
        self.content = text.encode("utf-8")
        self.headers = {"content-type": ctype}


@pytest.fixture(autouse=True)
def _clean_caches(monkeypatch):
    monkeypatch.setattr(ci, "_ROBOTS_CACHE", {})
    monkeypatch.setattr(ci, "_GH_TOKEN_CACHE", [])


def _serve(monkeypatch, pages):
    """Route _http_request to a dict of url -> _Resp; unknown urls are 404."""
    calls = []

    def fake(method, url, **kw):
        calls.append(url)
        return pages.get(url, _Resp(404))

    monkeypatch.setattr(ci, "_http_request", fake)
    return calls


HTML = """<html><head><link rel="alternate" type="application/rss+xml" href="/feed"></head><body>
<nav><li>Added a new navigation menu nobody should read</li></nav>
<script>var x = "Added support for scripts";</script>
<h2>Release 4.2</h2>
<ul><li>Added support for incremental schema drift detection</li><li>short</li>
<li>Fixed a typo in the docs footer text area</li></ul>
<footer><p>New footer promotions and enabled cookies everywhere</p></footer>
</body></html>"""

RSS = """<?xml version="1.0"?><rss version="2.0"><channel><title>Blog</title>
<item><title>Introducing column-level lineage for every pipeline</title></item>
<item><title>Quarterly update</title></item></channel></rss>"""

ATOM = """<?xml version="1.0" encoding="utf-8"?><feed xmlns="http://www.w3.org/2005/Atom">
<entry><title>New connector SDK supports deferred truncate</title></entry></feed>"""

BOMB = """<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;">]>
<rss><channel><item><title>&lol2; Added support</title></item></channel></rss>"""


def test_changelog_page_yields_feature_lines_under_their_heading(monkeypatch):
    _serve(monkeypatch, {"https://v.example/changelog": _Resp(text=HTML)})
    feats, src, errs = ci._scan_website({"changelog_url": "https://v.example/changelog"})
    assert src == "https://v.example/changelog"
    assert feats == ["[Release 4.2] Added support for incremental schema drift detection"]
    assert errs == []


def test_nav_script_and_footer_text_is_ignored(monkeypatch):
    _serve(monkeypatch, {"https://v.example/changelog": _Resp(text=HTML)})
    feats, _, _ = ci._scan_website({"changelog_url": "https://v.example/changelog"})
    joined = " ".join(feats)
    assert "navigation" not in joined and "scripts" not in joined and "footer" not in joined


def test_robots_disallow_refuses_the_page(monkeypatch):
    calls = _serve(
        monkeypatch,
        {
            "https://v.example/robots.txt": _Resp(text="User-agent: *\nDisallow: /changelog", ctype="text/plain"),
            "https://v.example/changelog": _Resp(text=HTML),
        },
    )
    feats, src, errs = ci._scan_website({"changelog_url": "https://v.example/changelog"})
    assert feats == [] and src is None
    assert errs == ["changelog_url: robots_disallowed"]
    assert "https://v.example/changelog" not in calls


def test_unreadable_robots_fails_closed(monkeypatch):
    _serve(
        monkeypatch,
        {"https://v.example/robots.txt": _Resp(status=500), "https://v.example/changelog": _Resp(text=HTML)},
    )
    _, _, errs = ci._scan_website({"changelog_url": "https://v.example/changelog"})
    assert errs == ["changelog_url: robots_disallowed"]


def test_rss_and_atom_feeds_give_entry_titles(monkeypatch):
    _serve(
        monkeypatch,
        {
            "https://v.example/rss": _Resp(text=RSS, ctype="application/rss+xml"),
            "https://v.example/atom": _Resp(text=ATOM, ctype="application/atom+xml"),
        },
    )
    rss, _, _ = ci._scan_website({"feed_url": "https://v.example/rss"})
    atom, _, _ = ci._scan_website({"feed_url": "https://v.example/atom"})
    assert rss == ["Introducing column-level lineage for every pipeline"]
    assert atom == ["New connector SDK supports deferred truncate"]


def test_a_feed_with_a_dtd_is_refused_not_expanded(monkeypatch):
    _serve(monkeypatch, {"https://v.example/rss": _Resp(text=BOMB, ctype="application/rss+xml")})
    feats, _, errs = ci._scan_website({"feed_url": "https://v.example/rss"})
    assert feats == []
    assert errs and errs[0].startswith("feed_url: feed_parse:")


def test_feed_url_wins_over_changelog_url(monkeypatch):
    calls = _serve(
        monkeypatch,
        {
            "https://v.example/rss": _Resp(text=RSS, ctype="application/rss+xml"),
            "https://v.example/changelog": _Resp(text=HTML),
        },
    )
    _, src, _ = ci._scan_website({"feed_url": "https://v.example/rss", "changelog_url": "https://v.example/changelog"})
    assert src == "https://v.example/rss"
    assert "https://v.example/changelog" not in calls


def test_a_website_with_no_feature_lines_falls_back_to_its_discovered_feed(monkeypatch):
    page = '<html><head><link rel="alternate" type="application/rss+xml" href="/feed"></head><body><p>Hi</p></body></html>'
    _serve(
        monkeypatch,
        {"https://v.example/": _Resp(text=page), "https://v.example/feed": _Resp(text=RSS, ctype="application/rss+xml")},
    )
    feats, src, _ = ci._scan_website({"website": "https://v.example/"})
    assert src == "https://v.example/feed"
    assert feats == ["Introducing column-level lineage for every pipeline"]


def test_errors_are_reported_per_source_and_never_invent_features(monkeypatch):
    _serve(monkeypatch, {})
    feats, src, errs = ci._scan_website({"changelog_url": "https://v.example/gone", "website": "https://v.example/"})
    assert feats == [] and src is None
    assert errs == ["changelog_url: not_found", "website: not_found"]


def test_oversized_pages_and_non_http_schemes_are_refused(monkeypatch):
    _serve(monkeypatch, {"https://v.example/big": _Resp(text="x" * (ci._WEB_MAX_BYTES + 1))})
    assert ci._fetch_page("https://v.example/big")[2] == "too_large"
    assert ci._fetch_page("file:///etc/passwd")[2] == "unsupported_scheme"


def test_env_token_wins_and_gh_cli_is_the_fallback(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "env-token")
    assert ci._gh_token() == "env-token"
    monkeypatch.delenv("GITHUB_TOKEN")
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.setattr(ci.shutil, "which", lambda name: "/usr/bin/gh")

    class _Out:
        returncode, stdout = 0, "cli-token\n"

    monkeypatch.setattr(ci.subprocess, "run", lambda *a, **k: _Out())
    assert ci._gh_token() == "cli-token"
    assert ci._gh_headers()["Authorization"] == "Bearer cli-token"


def test_no_token_anywhere_sends_no_authorization(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.setattr(ci.shutil, "which", lambda name: None)
    assert "Authorization" not in ci._gh_headers()
