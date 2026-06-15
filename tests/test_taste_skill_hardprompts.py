"""Tests for Taste Skill design-quality hardprompts (adapt-taste-04)."""
import pytest
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
CREATIVE_DQ = BASE_DIR / "hardprompts" / "creative" / "design_quality.md"
SLIDES_DQ = BASE_DIR / "hardprompts" / "slides" / "design_quality.md"

EXPECTED_DIALS = [
    "LAYOUT_DENSITY",
    "TYPOGRAPHY_CONTRAST",
    "COLOR_USAGE",
    "COMPONENT_SPECIFICITY",
    "MOTION_LEVEL",
]


def test_creative_design_quality_exists():
    assert CREATIVE_DQ.exists(), f"Missing {CREATIVE_DQ}"


def test_slides_design_quality_exists():
    assert SLIDES_DQ.exists(), f"Missing {SLIDES_DQ}"


def test_creative_dq_contains_all_dials():
    text = CREATIVE_DQ.read_text(encoding="utf-8")
    for dial in EXPECTED_DIALS:
        assert dial in text, f"Missing dial {dial} in creative/design_quality.md"


def test_slides_dq_contains_all_dials():
    text = SLIDES_DQ.read_text(encoding="utf-8")
    for dial in EXPECTED_DIALS:
        assert dial in text, f"Missing dial {dial} in slides/design_quality.md"


def test_creative_dq_has_spec_content_placeholder():
    text = CREATIVE_DQ.read_text(encoding="utf-8")
    assert "{spec_content}" in text, "creative/design_quality.md must have {spec_content} placeholder"


def test_slides_dq_has_spec_content_placeholder():
    text = SLIDES_DQ.read_text(encoding="utf-8")
    assert "{spec_content}" in text, "slides/design_quality.md must have {spec_content} placeholder"


def test_creative_dq_render_smoke():
    """Template substitution must not raise."""
    text = CREATIVE_DQ.read_text(encoding="utf-8")
    rendered = text.replace("{spec_content}", "Test feature spec content here.")
    assert "Test feature spec content here." in rendered


def test_slides_dq_render_smoke():
    text = SLIDES_DQ.read_text(encoding="utf-8")
    rendered = text.replace("{spec_content}", "Test deck spec content here.")
    assert "Test deck spec content here." in rendered


def test_creative_dq_has_output_format_section():
    text = CREATIVE_DQ.read_text(encoding="utf-8")
    assert "Output Format" in text or "output format" in text.lower()


def test_slides_dq_has_output_format_section():
    text = SLIDES_DQ.read_text(encoding="utf-8")
    assert "Output Format" in text or "output format" in text.lower()
