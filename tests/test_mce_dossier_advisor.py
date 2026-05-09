# CUI // SP-CTI
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

"""Tests for tools.migration_canvas.dossier_advisor — get_guidance_for_step and _resolve_categories."""

from tools.migration_canvas.dossier_advisor import (
    _resolve_categories,
    get_guidance_for_step,
    STEP_CATEGORY_MAP,
)


def test_resolve_categories_direct_db_values():
    """DB-valid categories pass through unchanged."""
    result = _resolve_categories(["compliance", "security"])
    assert result == ["compliance", "security"]


def test_resolve_categories_conceptual_fallback():
    """Conceptual names map to their DB fallbacks (discovery→other, inventory→infrastructure)."""
    result = _resolve_categories(["discovery", "inventory"])
    assert result == ["other", "infrastructure"]


def test_get_guidance_unknown_step_returns_empty():
    """A step number not in STEP_CATEGORY_MAP returns an empty list."""
    assert 99 not in STEP_CATEGORY_MAP
    result = get_guidance_for_step(99)
    assert result == []


def test_get_guidance_step2_graceful_on_missing_db(monkeypatch):
    """get_guidance_for_step(2) returns [] when DB is unavailable (graceful fallback)."""
    def _bad_conn():
        raise RuntimeError("no DB")
    monkeypatch.setattr(
        "tools.migration_canvas.dossier_advisor.get_connection", _bad_conn
    )
    result = get_guidance_for_step(2)
    assert result == []
