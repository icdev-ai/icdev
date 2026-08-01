#!/usr/bin/env python3
# CUI // SP-CTI
"""Unit tests for Migration Canvas grounding wiring (cnr-mdc-03).

Asserts every LLM-drafted migration response carries a grounding verdict —
validated citations OR a grounding-warning flag — built on the shared
tools/quality/citation_grounding.py primitives (not re-implemented).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.migration_canvas.grounding import GROUNDING_WARNING, assess_response


def test_ungrounded_text_carries_warning():
    """Free-form guidance with no citations gets a warning + abstain band."""
    g = assess_response("Rehost the Oracle DB onto Aurora PostgreSQL.", model="m1")
    assert g["has_citations"] is False
    assert g["grounded"] is False
    assert g["grounding_warning"] == GROUNDING_WARNING
    assert g["confidence_band"] == "abstain"
    assert g["model"] == "m1"


def test_cited_text_is_grounded_when_sources_valid():
    """Text citing an available source is grounded, no warning."""
    text = "Use CDC replication [source: KB-7] to minimize downtime."
    g = assess_response(text, allowed_sources=["KB-7"])
    assert g["has_citations"] is True
    assert g["grounded"] is True
    assert g["grounding_warning"] is None
    assert g["confidence_band"] == "include"
    assert "KB-7" in g["cited_sources"]


def test_hallucinated_citation_is_flagged():
    """Citing an unavailable source is worse than no citation — flagged."""
    text = "Follow the runbook [source: KB-99]."
    g = assess_response(text, allowed_sources=["KB-7"])
    assert g["grounded"] is False
    assert g["hallucinated_citations"] == ["KB-99"]
    assert g["grounding_warning"] and "KB-99" in g["grounding_warning"]


def test_empty_text_is_ungrounded():
    g = assess_response("")
    assert g["grounded"] is False
    assert g["grounding_warning"] == GROUNDING_WARNING


def test_cam_llm_reply_attaches_grounding():
    """migration_chat_advisor cam replies carry a grounding key."""
    from tools.migration_canvas.migration_chat_advisor import _cam_llm_reply
    reply = _cam_llm_reply("General migration advice with no sources.")
    assert reply["mode"] == "cam"
    assert "grounding" in reply
    assert reply["grounding"]["grounding_warning"] == GROUNDING_WARNING
