# CUI // SP-CTI
"""Tests for tools.migration_canvas.dossier_advisor."""
from tools.migration_canvas.dossier_advisor import (
    get_guidance_for_step,
    STEP_CATEGORY_MAP,
    _resolve_categories,
)


def test_returns_list_for_valid_step():
    result = get_guidance_for_step(1)
    assert isinstance(result, list)


def test_returns_empty_for_invalid_step():
    result = get_guidance_for_step(99)
    assert result == []


def test_step_category_map_coverage():
    assert set(STEP_CATEGORY_MAP.keys()) == {1, 2, 3, 4, 5, 6, 7}


def test_resolve_categories_known():
    cats = _resolve_categories(["compliance", "security"])
    assert "compliance" in cats
    assert "security" in cats


def test_resolve_categories_fallback():
    cats = _resolve_categories(["inventory"])
    assert "infrastructure" in cats


def test_resolve_categories_dedup():
    cats = _resolve_categories(["compliance", "compliance"])
    assert cats.count("compliance") == 1


def test_resolve_categories_unknown_dropped():
    cats = _resolve_categories(["nonexistent_category"])
    assert cats == []


def test_guidance_item_shape():
    # DB may be empty in test env — just verify shape if data returned
    result = get_guidance_for_step(2)
    for item in result:
        assert "title" in item
        assert "description" in item
        assert "severity" in item
        assert "category" in item


def test_all_steps_return_list():
    for step in range(1, 8):
        result = get_guidance_for_step(step)
        assert isinstance(result, list), f"Step {step} did not return a list"
