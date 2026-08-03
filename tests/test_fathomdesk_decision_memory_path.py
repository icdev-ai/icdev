# CUI // SP-CTI
"""decision_memory writes must be redirectable away from the tracked repo file.

The module's default log is data/fathomdesk_decisions.md, which is checked in.
Any test that reached store_decision() therefore appended fabricated trading
decisions to a repo file, and the session auto-commit hook committed them.
FATHOMDESK_DECISIONS_PATH exists so a test can point the log at a tmp dir; these
tests pin that contract and pin that the default is still the tracked file when
the variable is absent.
"""
from __future__ import annotations

import importlib


def _module():
    return importlib.import_module("tools.fathomdesk.decision_memory")


def test_default_path_is_the_repo_data_file(monkeypatch):
    dm = _module()
    monkeypatch.delenv("FATHOMDESK_DECISIONS_PATH", raising=False)
    assert dm._log_path() == dm._LOG_PATH
    assert dm._log_path().name == "fathomdesk_decisions.md"


def test_env_override_redirects_the_path(monkeypatch, tmp_path):
    dm = _module()
    target = tmp_path / "decisions.md"
    monkeypatch.setenv("FATHOMDESK_DECISIONS_PATH", str(target))
    assert dm._log_path() == target


def test_store_decision_writes_to_the_override_not_the_repo(monkeypatch, tmp_path):
    dm = _module()
    target = tmp_path / "decisions.md"
    monkeypatch.setenv("FATHOMDESK_DECISIONS_PATH", str(target))
    before = dm._LOG_PATH.read_text(encoding="utf-8") if dm._LOG_PATH.exists() else None

    dm.store_decision(
        "ZZZZ",
        "2026-05-01",
        {"direction": "BUY", "confidence": 0.9, "reasoning": "unit test"},
    )

    assert target.exists(), "override path was not written"
    assert "ZZZZ" in target.read_text(encoding="utf-8")
    after = dm._LOG_PATH.read_text(encoding="utf-8") if dm._LOG_PATH.exists() else None
    assert after == before, "tracked data/fathomdesk_decisions.md was modified"


def test_parse_entries_finds_every_h2_block():
    """_parse_entries walked the re.split output from index 0.

    re.split with a capture group returns [preamble, header, body, ...], so
    headers are at odd indices; starting at 0 matched nothing and get_pending()
    returned [] no matter how many entries the log held.
    """
    import re

    dm = _module()
    log = (
        "# FathomDesk Decisions\n\n"
        "## [2026-05-01|AAPL|BUY|pending]\n"
        "- date: 2026-05-01\n- ticker: AAPL\n- direction: BUY\n"
        "- confidence: 0.8000\n- reasoning_summary: first\n\n"
        "## [2026-05-02|MSFT|SELL|resolved]\n"
        "- date: 2026-05-02\n- ticker: MSFT\n- direction: SELL\n"
        "- confidence: 0.6000\n- reasoning_summary: second\n\n"
    )
    expected = len(re.findall(r"^## \[", log, re.M))
    assert expected == 2

    assert len(dm._parse_entries(log)) == expected
    assert [e["ticker"] for e in dm._parse_entries(log)] == ["AAPL", "MSFT"]
    assert [e["ticker"] for e in dm._parse_entries(log, "pending")] == ["AAPL"]
    assert [e["ticker"] for e in dm._parse_entries(log, "resolved")] == ["MSFT"]


def test_get_pending_reads_back_from_the_override(monkeypatch, tmp_path):
    dm = _module()
    monkeypatch.setenv("FATHOMDESK_DECISIONS_PATH", str(tmp_path / "decisions.md"))

    dm.store_decision(
        "YYYY",
        "2026-05-02",
        {"direction": "SELL", "confidence": 0.7, "reasoning": "unit test"},
    )

    pending = dm.get_pending("YYYY")
    assert len(pending) == 1
    assert pending[0]["ticker"] == "YYYY"
