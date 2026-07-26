#!/usr/bin/env python3
"""DIC CoVe publish gate — CUI // SP-CTI.

Chain-of-Verification re-interrogates each claim independently. It shipped as
`agx-verify-01` with no DIC call site, so the strongest anti-hallucination
check available was inert.

Two things make wiring it non-trivial, and both are what these tests are about:

1. It multiplies LLM calls per artifact, so it must be opt-in and must run only
   after the cheap deterministic gates have passed.
2. `cove_guard` fails CLOSED — when the architecture raises (no provider, budget
   exhausted) it reports `blocked`. That is right for a connected deployment and
   wrong for an air-gapped one, where every approval would be blocked by a check
   that never ran. "Unverifiable" is not "wrong".
"""
from __future__ import annotations

import pytest

from tools.document_intelligence import consistency_checker as cc


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in ("ICDEV_DIC_COVE_GATE", "ICDEV_DIC_COVE_ON_ERROR",
                "ICDEV_DIC_COVE_MAX_QUESTIONS"):
        monkeypatch.delenv(var, raising=False)


# --------------------------------------------------------------------------- #
# Opt-in
# --------------------------------------------------------------------------- #


def test_gate_is_off_by_default():
    """CoVe costs real money per approval; it is never inherited."""
    assert cc.cove_enabled() is False
    r = cc.check_version_cove("any-version")
    assert r == {"enabled": False, "blocked": False, "findings": [],
                 "unrunnable": False, "reason": "disabled"}


@pytest.mark.parametrize("val,expected", [
    ("1", True), ("true", True), ("YES", True), ("on", True),
    ("0", False), ("false", False), ("", False), ("maybe", False),
])
def test_toggle_parsing(monkeypatch, val, expected):
    monkeypatch.setenv("ICDEV_DIC_COVE_GATE", val)
    assert cc.cove_enabled() is expected


def test_disabled_gate_does_no_work(monkeypatch):
    """Off must mean no DB read and no LLM call, not a suppressed result."""
    def explode(*a, **k):
        raise AssertionError("the disabled gate must not touch the database")

    monkeypatch.setattr(cc, "get_connection", explode)
    assert cc.check_version_cove("v1")["enabled"] is False


# --------------------------------------------------------------------------- #
# Failure posture — the reason this needed design, not just a call
# --------------------------------------------------------------------------- #


def _fake_sections(monkeypatch, rows):
    class _Conn:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def execute(self, *a, **k):
            class _C:
                def fetchall(_s): return rows
            return _C()
    monkeypatch.setattr(cc, "get_connection", lambda *a, **k: _Conn())


_SECTION = {"section_id": "s1", "heading": "Scope", "content": "A claim.",
            "citations_json": '[{"chunk_id":"c1"}]', "origin": "ai_generated"}


def test_unrunnable_gate_warns_by_default(monkeypatch):
    """Air-gap default: a check that could not run must not block publishing."""
    _fake_sections(monkeypatch, [_SECTION])
    monkeypatch.setenv("ICDEV_DIC_COVE_GATE", "1")
    monkeypatch.setattr(
        "tools.quality.cove_guard.cove_guard",
        lambda *a, **k: {"method": "error", "needs_revision": True,
                         "decision": {"error": "no provider reachable"}},
    )
    r = cc.check_version_cove("v1")
    assert r["unrunnable"] is True
    assert r["blocked"] is False, "an unrunnable gate must not block by default"
    assert r["findings"] == [], "'could not run' is not a content defect"


def test_unrunnable_gate_blocks_when_configured(monkeypatch):
    """Connected deployments can still demand fail-closed."""
    _fake_sections(monkeypatch, [_SECTION])
    monkeypatch.setenv("ICDEV_DIC_COVE_GATE", "1")
    monkeypatch.setenv("ICDEV_DIC_COVE_ON_ERROR", "block")
    monkeypatch.setattr(
        "tools.quality.cove_guard.cove_guard",
        lambda *a, **k: {"method": "error", "needs_revision": True, "decision": {}},
    )
    assert cc.check_version_cove("v1")["blocked"] is True


def test_real_defect_blocks_regardless_of_on_error(monkeypatch):
    """A gate that RAN and found a contradiction always blocks."""
    _fake_sections(monkeypatch, [_SECTION])
    monkeypatch.setenv("ICDEV_DIC_COVE_GATE", "1")
    monkeypatch.setenv("ICDEV_DIC_COVE_ON_ERROR", "warn")
    monkeypatch.setattr(
        "tools.quality.cove_guard.cove_guard",
        lambda *a, **k: {"method": "cove", "needs_revision": True,
                         "contradicted_claims": [{"question": "Is X true?",
                                                  "verdict": "contradicted"}]},
    )
    r = cc.check_version_cove("v1")
    assert r["blocked"] is True
    assert r["findings"][0]["issue"] == "cove_contradicted"


def test_force_never_blocks(monkeypatch):
    _fake_sections(monkeypatch, [_SECTION])
    monkeypatch.setenv("ICDEV_DIC_COVE_GATE", "1")
    monkeypatch.setattr(
        "tools.quality.cove_guard.cove_guard",
        lambda *a, **k: {"method": "cove", "needs_revision": True,
                         "contradicted_claims": []},
    )
    r = cc.check_version_cove("v1", force=True)
    assert r["blocked"] is False
    assert r["findings"], "findings still reported so the override can be audited"


def test_clean_run_passes(monkeypatch):
    _fake_sections(monkeypatch, [_SECTION])
    monkeypatch.setenv("ICDEV_DIC_COVE_GATE", "1")
    monkeypatch.setattr(
        "tools.quality.cove_guard.cove_guard",
        lambda *a, **k: {"method": "cove", "needs_revision": False,
                         "contradicted_claims": []},
    )
    r = cc.check_version_cove("v1")
    assert (r["blocked"], r["findings"], r["reason"]) == (False, [], "clean")


# --------------------------------------------------------------------------- #
# Population + shape
# --------------------------------------------------------------------------- #


def test_human_sections_are_not_verified(monkeypatch):
    """Same population as the citation gate — no second source of truth."""
    _fake_sections(monkeypatch, [{**_SECTION, "origin": "human_authored"}])
    monkeypatch.setenv("ICDEV_DIC_COVE_GATE", "1")
    monkeypatch.setattr(
        "tools.quality.cove_guard.cove_guard",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not verify human prose")),
    )
    assert cc.check_version_cove("v1")["reason"] == "no_ai_sections"


def test_findings_shape_matches_the_sibling_gates(monkeypatch):
    _fake_sections(monkeypatch, [_SECTION])
    monkeypatch.setenv("ICDEV_DIC_COVE_GATE", "1")
    monkeypatch.setattr(
        "tools.quality.cove_guard.cove_guard",
        lambda *a, **k: {"method": "cove", "needs_revision": True,
                         "contradicted_claims": [{"question": "q"}]},
    )
    for f in cc.check_version_cove("v1")["findings"]:
        assert set(f) >= {"item_number", "issue", "detail"}


def test_cove_guard_is_an_allowed_audit_gate():
    """Overriding it must be recordable — that needed migration 300."""
    from tools.quality.citation_grounding import PUBLISH_GATES

    assert "cove_guard" in PUBLISH_GATES


def test_approve_route_runs_cove_after_the_cheap_gates():
    """Order matters: never spend an LLM pass on a draft already rejected."""
    import inspect

    from tools.document_intelligence import blueprint

    src = inspect.getsource(blueprint.api_review_approve)
    assert src.index("citation_defects") < src.index("check_version_cove"), (
        "cove_guard must run only after placeholder_guard and citation_guard pass"
    )
