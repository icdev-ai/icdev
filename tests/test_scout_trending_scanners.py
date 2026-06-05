#!/usr/bin/env python3
# CUI // SP-CTI
"""Regression tests for Scout Pillar 2 (trending) scanners.

Guards against the recurring failure logged in data/scout/*.md:

    scan_github() missing 1 required positional argument: 'config'
    scan_hackernews() missing 1 required positional argument: 'config'

The Scout trending pillar delegates to ``tools.innovation.web_scanner``'s
``scan_github`` / ``scan_hackernews``, both of which require a ``config`` arg.
The delegators must pass one. These tests mock the HTTP layer (via disabled
source config) so they are deterministic and need no live network.
"""

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.innovation import web_scanner
from tools.scout.pillars import trending


# A stub innovation config with every source disabled. With sources off, the
# scanners return immediately without any HTTP call -> deterministic, offline.
STUB_CONFIG = {
    "sources": {
        "github": {"enabled": False},
        "community_forums": {"enabled": False},
    }
}


def test_scan_github_accepts_config_returns_list():
    """web_scanner.scan_github must accept a config and return a list."""
    result = web_scanner.scan_github(STUB_CONFIG)
    assert isinstance(result, list)


def test_scan_hackernews_accepts_config_returns_list():
    """web_scanner.scan_hackernews must accept a config and return a list."""
    result = web_scanner.scan_hackernews(STUB_CONFIG)
    assert isinstance(result, list)


def test_trending_github_delegator_passes_config(monkeypatch):
    """_scan_github_trending must call scan_github WITH a config (no TypeError)."""
    monkeypatch.setattr(web_scanner, "_load_config", lambda: STUB_CONFIG)
    findings = trending._scan_github_trending({})
    assert isinstance(findings, list)
    # No "scan failed" error finding should be produced for a clean disabled run.
    assert not any(f.get("title") == "GitHub trending scan failed" for f in findings)


def test_trending_hackernews_delegator_passes_config(monkeypatch):
    """_scan_hackernews must call scan_hackernews WITH a config (no TypeError)."""
    monkeypatch.setattr(web_scanner, "_load_config", lambda: STUB_CONFIG)
    findings = trending._scan_hackernews({})
    assert isinstance(findings, list)
    assert not any(f.get("title") == "HackerNews scan failed" for f in findings)


if __name__ == "__main__":
    import pytest

    sys.exit(pytest.main([__file__, "-v"]))
