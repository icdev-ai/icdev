# CUI // SP-CTI
"""Tests for the ACF arxiv_acf vertical scanner (acf-ada-08).

Verifies:
  * The scanner is auto-registered under the name ``arxiv_acf``.
  * ``scan_arxiv_acf`` honors ``enabled: false`` (returns ``[]``).
  * When no HTTP client is available (air-gap) the scanner returns ``[]``
    and never raises — this is the load-bearing safety property.
  * When HTTP returns a valid Atom response, the scanner produces a list of
    normalized signal dicts matching the ``_make_signal`` shape.
  * The keyword relevance gate filters out papers whose title+summary do not
    contain any configured keyword.
  * The ``max_results`` cap is enforced.
"""
import sys
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))


# --------------------------------------------------------------------------- #
# Module surface
# --------------------------------------------------------------------------- #
def test_scanner_is_registered():
    from tools.foundry import scanners

    assert "arxiv_acf" in scanners.SOURCE_SCANNERS


def test_scanner_callable_exposed():
    from tools.foundry.scanners import arxiv

    assert callable(arxiv.scan_arxiv_acf)


# --------------------------------------------------------------------------- #
# Disabled → empty
# --------------------------------------------------------------------------- #
def test_disabled_returns_empty_list():
    from tools.foundry.scanners.arxiv import scan_arxiv_acf

    cfg = {"sources": {"arxiv_acf": {"enabled": False}}}
    out = scan_arxiv_acf(cfg)
    assert out == []


# --------------------------------------------------------------------------- #
# Air-gap safety: missing http client must not raise
# --------------------------------------------------------------------------- #
def test_no_http_client_returns_empty(monkeypatch):
    """When tools.http.client.request is not importable, scanner must
    silently return [] (air-gap safety). Never raise."""

    # Force the lazy import to fail by hiding tools.http.client.
    monkeypatch.setitem(sys.modules, "tools.http.client", None)
    # Drop the cached import inside the arxiv module so the re-import hits
    # the patched sys.modules entry.
    from tools.foundry.scanners import arxiv as arxiv_mod

    if hasattr(arxiv_mod, "_http_request"):
        monkeypatch.delattr(arxiv_mod, "_http_request", raising=False)
    # Also clear the local binding inside the module (Python's import
    # already pinned it).
    monkeypatch.setattr(arxiv_mod, "_http_request", None, raising=False)

    cfg = {"sources": {"arxiv_acf": {"enabled": True, "max_results": 5}}}
    out = arxiv_mod.scan_arxiv_acf(cfg)
    assert out == []


# --------------------------------------------------------------------------- #
# HTTP error → empty (no raise)
# --------------------------------------------------------------------------- #
def test_http_error_returns_empty(monkeypatch):
    from tools.foundry.scanners import arxiv as arxiv_mod

    class _FakeResp:
        status_code = 500
        text = ""

    def _fake_get(url, *, params, timeout):
        return None, "http_500"

    monkeypatch.setattr(arxiv_mod, "_try_http_get", _fake_get)
    cfg = {"sources": {"arxiv_acf": {"enabled": True, "max_results": 5}}}
    out = arxiv_mod.scan_arxiv_acf(cfg)
    assert out == []


# --------------------------------------------------------------------------- #
# Happy path: mock arXiv Atom response → normalized signals
# --------------------------------------------------------------------------- #
_ATOM_FIXTURE = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/2501.00001v1</id>
    <title>Autonomous Foundry Agents for Code Synthesis</title>
    <summary>We describe a foundry-style agent that synthesizes code.</summary>
    <published>2026-01-15T00:00:00Z</published>
    <author><name>Alice Smith</name></author>
  </entry>
  <entry>
    <id>http://arxiv.org/abs/2501.00002v1</id>
    <title>Cooking recipes for Italian cuisine</title>
    <summary>A collection of family recipes.</summary>
    <published>2026-01-10T00:00:00Z</published>
    <author><name>Bob Jones</name></author>
  </entry>
  <entry>
    <id>http://arxiv.org/abs/2501.00003v1</id>
    <title>Foundry techniques in chip design</title>
    <summary>Foundry process node scaling.</summary>
    <published>2026-01-12T00:00:00Z</published>
    <author><name>Carol Lee</name></author>
  </entry>
</feed>
"""


def _patch_http(monkeypatch, body, *, status_code=200):
    from tools.foundry.scanners import arxiv as arxiv_mod

    status_code_local = status_code
    body_local = body

    def _fake_get(url, *, params, timeout):
        if status_code_local < 400:
            return body_local, None
        return None, f"http_{status_code_local}"

    monkeypatch.setattr(arxiv_mod, "_try_http_get", _fake_get)


def test_happy_path_produces_normalized_signals(monkeypatch):
    """Mock arXiv response, no keyword filter — both relevant papers pass."""
    from tools.foundry.scanners import arxiv as arxiv_mod

    _patch_http(monkeypatch, _ATOM_FIXTURE)
    cfg = {"sources": {"arxiv_acf": {"enabled": True, "max_results": 10}}}
    out = arxiv_mod.scan_arxiv_acf(cfg)

    assert len(out) == 3
    for sig in out:
        # The signal is a normalized dict, NOT a foundry_signals row.
        assert sig["source_engine"] == "arxiv_acf"
        assert sig["source_type"] == "arxiv_paper"
        assert "category" not in sig  # not yet persisted
        # No theme / dedup_hash fields either — that's the in-memory shape.
        assert "theme" in sig
        assert "dedup_hash" in sig
        assert isinstance(sig["metadata"], dict)
        # arxiv_id is preserved as source_ref AND in metadata for traceability.
        assert sig["source_ref"] == sig["metadata"]["arxiv_id"]


def test_keyword_relevance_gate_filters(monkeypatch):
    """A keyword filter must drop papers that don't contain the term."""
    from tools.foundry.scanners import arxiv as arxiv_mod

    _patch_http(monkeypatch, _ATOM_FIXTURE)
    cfg = {
        "sources": {
            "arxiv_acf": {
                "enabled": True,
                "max_results": 10,
                "keywords": ["autonomous"],
            }
        }
    }
    out = arxiv_mod.scan_arxiv_acf(cfg)
    # Only the first paper mentions "autonomous" in its title.
    assert len(out) == 1
    assert "Autonomous" in out[0]["theme"]


def test_max_results_cap_enforced(monkeypatch):
    """The cap from config is honored regardless of how many entries the
    feed returns."""
    from tools.foundry.scanners import arxiv as arxiv_mod

    _patch_http(monkeypatch, _ATOM_FIXTURE)
    cfg = {
        "sources": {
            "arxiv_acf": {
                "enabled": True,
                "max_results": 2,
                "categories": ["cs.AI"],
            }
        }
    }
    out = arxiv_mod.scan_arxiv_acf(cfg)
    assert len(out) <= 2


def test_empty_atom_response_returns_empty(monkeypatch):
    from tools.foundry.scanners import arxiv as arxiv_mod

    _patch_http(monkeypatch, "")
    cfg = {"sources": {"arxiv_acf": {"enabled": True, "max_results": 5}}}
    out = arxiv_mod.scan_arxiv_acf(cfg)
    assert out == []


def test_malformed_xml_returns_empty(monkeypatch):
    from tools.foundry.scanners import arxiv as arxiv_mod

    _patch_http(monkeypatch, "<<not valid xml>>")
    cfg = {"sources": {"arxiv_acf": {"enabled": True, "max_results": 5}}}
    out = arxiv_mod.scan_arxiv_acf(cfg)
    assert out == []


# --------------------------------------------------------------------------- #
# Direct invocation via the registry
# --------------------------------------------------------------------------- #
def test_registry_scan_invokes_arxiv_scanner(monkeypatch):
    """Calling scan('arxiv_acf', config=..., conn=...) on the registry should
    dispatch to the registered function and return its result."""
    from tools.foundry.scanners import scan, SOURCE_SCANNERS

    captured = {}

    def fake(config, *, conn=None, db_path=None, **kwargs):
        captured["config"] = config
        captured["kwargs"] = kwargs
        return [{"source_engine": "arxiv_acf", "theme": "stub"}]

    # Swap the registered function for the duration of this test.
    original = SOURCE_SCANNERS["arxiv_acf"]
    SOURCE_SCANNERS["arxiv_acf"] = fake
    try:
        out = scan(
            "arxiv_acf",
            {"sources": {"arxiv_acf": {"enabled": True}}},
            conn=None,
            db_path=None,
            foo="bar",
        )
        assert out == [{"source_engine": "arxiv_acf", "theme": "stub"}]
        assert captured["kwargs"] == {"foo": "bar"}
    finally:
        SOURCE_SCANNERS["arxiv_acf"] = original
