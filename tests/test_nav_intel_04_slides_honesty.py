# CUI // SP-CTI
"""nav-intel-04 — /slides deck-honesty regression tests.

The /slides pipeline used to ship canned fallback slides and fabricated
research summaries while unconditionally reporting the deck status as
"completed" — a viewer could not tell a degraded template deck from a real
generated one. This suite locks in the wave honesty standard (cf. Oracle
available:false, FathomDesk simulated:true):

  1. Per-slide provenance — content that fell back to canned text is tagged
     provenance="fallback"; real LLM content is "llm"; intentional title/outro
     templates are "structural".
  2. Deck-level status — a deck that shipped fallback content reports
     "degraded" (some slides/research fell back) or "template" (the whole
     outline is a canned static fallback), never a silent "completed".
  3. No fabricated research — when the LLM is unavailable the research
     connector returns an empty summary and no invented citations, flagged
     degraded=True, instead of the old "Research summary for {query}." stub.
  4. UI — the slides detail page renders a degraded/template banner and a
     per-slide "Fallback" badge; both root and icdev/ template twins carry it.
"""
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ── Fake LLM router plumbing ─────────────────────────────────────────────────
class _FakeResp:
    def __init__(self, content):
        self.content = content


class _FakeRouter:
    """Stand-in for LLMRouter whose invoke returns canned content or raises."""

    def __init__(self, content=None, raise_exc=False):
        self._content = content
        self._raise = raise_exc

    def invoke(self, fn, request):
        if self._raise:
            raise RuntimeError("LLM unavailable")
        return _FakeResp(self._content)


def _patch_router(**kwargs):
    """Patch the LLMRouter symbol the slides modules import lazily."""
    return patch("tools.llm.router.LLMRouter", return_value=_FakeRouter(**kwargs))


# ── (1) Constants ────────────────────────────────────────────────────────────
def test_new_honesty_statuses_registered():
    from tools.slides.constants import (
        DECK_STATUSES, CHECK_DECK_STATUS, DECK_READY_STATUSES,
        PROVENANCE_LLM, PROVENANCE_FALLBACK, PROVENANCE_STRUCTURAL,
    )
    assert "degraded" in DECK_STATUSES
    assert "template" in DECK_STATUSES
    # CHECK constraint string derives from the constant, so it must include them.
    assert "'degraded'" in CHECK_DECK_STATUS
    assert "'template'" in CHECK_DECK_STATUS
    # Degraded/template decks still yield a downloadable artifact.
    assert set(DECK_READY_STATUSES) >= {"completed", "degraded", "template", "auto"}
    assert (PROVENANCE_LLM, PROVENANCE_FALLBACK, PROVENANCE_STRUCTURAL) == (
        "llm", "fallback", "structural",
    )


# ── (2) content_agent per-slide provenance ───────────────────────────────────
def test_slide_llm_success_is_marked_llm_provenance():
    from tools.slides.content_agent import _generate_one
    good_json = (
        '{"title": "Real Slide", "bullets": ["a", "b", "c"], '
        '"speaker_notes": "notes", "visual_context": "vc", '
        '"slide_type": "content", "citations": []}'
    )
    with _patch_router(content=good_json):
        slide = _generate_one("Real Slide", 2, {}, tone="professional")
    assert slide["provenance"] == "llm"
    assert slide["bullets"] == ["a", "b", "c"]


def test_slide_llm_failure_is_marked_fallback_provenance():
    from tools.slides.content_agent import _generate_one
    with _patch_router(raise_exc=True):
        slide = _generate_one("Broken Slide", 2, {}, tone="professional")
    assert slide["provenance"] == "fallback"
    # The canned fallback bullet is the tell-tale placeholder content.
    assert slide["bullets"] == ["Key point: Broken Slide"]


def test_title_and_outro_slides_are_structural_not_fallback():
    from tools.slides.content_agent import _generate_one
    title = _generate_one("Cover", 1, {}, is_title_slide=True)
    outro = _generate_one("Thank You", 9, {}, is_outro=True)
    assert title["provenance"] == "structural"
    assert outro["provenance"] == "structural"


def test_generate_all_tags_every_slide_with_provenance():
    from tools.slides.content_agent import generate_all
    with _patch_router(raise_exc=True):
        slides = generate_all(["Cover", "Middle", "Thank You"], {})
    assert len(slides) == 3
    provs = [s.get("provenance") for s in slides]
    # First=title(structural), middle=content fallback, last=outro(structural).
    assert provs[0] == "structural"
    assert provs[1] == "fallback"
    assert provs[2] == "structural"


# ── (3) orchestrator outline provenance ──────────────────────────────────────
def test_outline_reports_fallback_when_llm_unavailable():
    from tools.slides import orchestrator
    with _patch_router(raise_exc=True):
        titles, used_fallback = orchestrator.plan_outline(
            raw_content={}, deck_title="ICDEV Overview",
            return_provenance=True,
        )
    assert used_fallback is True
    assert isinstance(titles, list) and titles


def test_outline_reports_no_fallback_on_llm_success():
    from tools.slides import orchestrator
    with _patch_router(content='["Cover Slide", "The Problem", "Our Solution"]'):
        titles, used_fallback = orchestrator.plan_outline(
            raw_content={}, deck_title="ICDEV Overview",
            return_provenance=True,
        )
    assert used_fallback is False
    assert "Cover Slide" in titles


def test_outline_default_return_is_plain_list():
    """Back-compat: default (no return_provenance) still returns a bare list."""
    from tools.slides import orchestrator
    with _patch_router(content='["Cover Slide", "The Problem", "Our Solution"]'):
        result = orchestrator.plan_outline(raw_content={}, deck_title="X")
    assert isinstance(result, list)


# ── (4) research connector — no fabrication ──────────────────────────────────
def test_research_does_not_fabricate_when_llm_unavailable():
    from tools.slides import research_connector as rc
    # Force the air-gap LLM path to fail, and neutralize KG enrichment.
    with _patch_router(raise_exc=True), patch.object(rc, "_kg_search", return_value=[]):
        result = rc.research_topic("quantum widgets", airgap=True)
    assert result["degraded"] is True
    assert result["summary"] == ""
    assert result["sources"] == []
    # The old fabricated stub must never appear.
    assert "Research summary for" not in result["summary"]


def test_research_reports_healthy_when_llm_returns_summary():
    from tools.slides import research_connector as rc
    payload = '{"summary": "Real findings about widgets.", "sources": []}'
    with _patch_router(content=payload), patch.object(rc, "_kg_search", return_value=[]):
        result = rc.research_topic("quantum widgets", airgap=True)
    assert result["degraded"] is False
    assert result["summary"] == "Real findings about widgets."


# ── (5) engine deck-level status ─────────────────────────────────────────────
def _run_engine(monkeypatch, tmp_path, mock_outline, mock_slides, raw=None):
    from tools.slides import pptx_builder
    from tools.slides.engine import DeckEngine, DeckRequest

    with (
        patch("tools.slides.orchestrator.plan_outline", return_value=mock_outline),
        patch("tools.slides.content_agent.generate_all", return_value=mock_slides),
        patch.object(DeckEngine, "_gather", return_value=(raw or {})),
        patch.object(pptx_builder, "_OUTPUT_DIR", tmp_path),
        patch.object(DeckEngine, "_create_deck_record", return_value=1),
        patch.object(DeckEngine, "_update_deck_record"),
        patch.object(DeckEngine, "_audit"),
    ):
        req = DeckRequest(title="Test Deck", sources=["canvases"], enable_graphics=False)
        return DeckEngine().run(req)


def test_engine_status_completed_when_all_content_is_real(tmp_path, monkeypatch):
    outline = (["Cover", "Body", "Thank You"], False)
    slides = [
        {"title": "Cover", "slide_type": "title", "bullets": [], "provenance": "structural"},
        {"title": "Body", "slide_type": "content", "bullets": ["x"], "provenance": "llm"},
        {"title": "Thank You", "slide_type": "outro", "bullets": ["y"], "provenance": "structural"},
    ]
    result = _run_engine(monkeypatch, tmp_path, outline, slides)
    assert result.status == "completed"


def test_engine_status_degraded_when_a_slide_falls_back(tmp_path, monkeypatch):
    outline = (["Cover", "Body", "Thank You"], False)
    slides = [
        {"title": "Cover", "slide_type": "title", "bullets": [], "provenance": "structural"},
        {"title": "Body", "slide_type": "content", "bullets": ["Key point: Body"], "provenance": "fallback"},
        {"title": "Thank You", "slide_type": "outro", "bullets": ["y"], "provenance": "structural"},
    ]
    result = _run_engine(monkeypatch, tmp_path, outline, slides)
    assert result.status == "degraded"


def test_engine_status_template_when_outline_is_fallback(tmp_path, monkeypatch):
    # Outline itself is canned (used_fallback=True) — whole structure is template.
    outline = (["Cover", "Body", "Thank You"], True)
    slides = [
        {"title": "Cover", "slide_type": "title", "bullets": [], "provenance": "structural"},
        {"title": "Body", "slide_type": "content", "bullets": ["x"], "provenance": "llm"},
        {"title": "Thank You", "slide_type": "outro", "bullets": ["y"], "provenance": "structural"},
    ]
    result = _run_engine(monkeypatch, tmp_path, outline, slides)
    assert result.status == "template"


def test_engine_status_degraded_when_research_unavailable(tmp_path, monkeypatch):
    # Real outline + real slides, but requested research came back degraded.
    outline = (["Cover", "Body", "Thank You"], False)
    slides = [
        {"title": "Cover", "slide_type": "title", "bullets": [], "provenance": "structural"},
        {"title": "Body", "slide_type": "content", "bullets": ["x"], "provenance": "llm"},
        {"title": "Thank You", "slide_type": "outro", "bullets": ["y"], "provenance": "structural"},
    ]
    raw = {"research": {"summary": "", "sources": [], "degraded": True}}
    result = _run_engine(monkeypatch, tmp_path, outline, slides, raw=raw)
    assert result.status == "degraded"


# ── (6) UI badge markup (both template twins) ────────────────────────────────
@pytest.mark.parametrize(
    "rel",
    [
        "tools/dashboard/templates/slides/detail.html",
        "icdev/tools/dashboard/templates/slides/detail.html",
    ],
)
def test_detail_template_renders_honesty_markup(rel):
    html = (ROOT / rel).read_text(encoding="utf-8")
    # Deck-level banner for both degraded and template statuses.
    assert "deck-honesty-banner" in html
    assert "deck.status == 'template'" in html
    assert "deck.status == 'degraded'" in html
    # Per-slide fallback badge keyed on provenance.
    assert "slide-fallback-badge" in html
    assert "slide.provenance == 'fallback'" in html


@pytest.mark.parametrize(
    "rel",
    [
        "tools/slides/blueprint.py",
        "icdev/tools/slides/blueprint.py",
    ],
)
def test_status_endpoint_marks_done_and_degraded(rel):
    src = (ROOT / rel).read_text(encoding="utf-8")
    # Degraded/template decks are "done" (not stuck polling) and flagged degraded.
    assert '"degraded": status in ("degraded", "template")' in src
    assert 'status in ("completed", "degraded", "template", "auto", "failed")' in src


# ── (7) No fabricated research stub anywhere in the connector source ─────────
@pytest.mark.parametrize(
    "rel",
    [
        "tools/slides/research_connector.py",
        "icdev/tools/slides/research_connector.py",
    ],
)
def test_research_connector_has_no_fabricated_summary_stub(rel):
    src = (ROOT / rel).read_text(encoding="utf-8")
    assert 'f"Research summary for {query}."' not in src
