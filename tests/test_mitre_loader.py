# CUI // SP-CTI
"""Tests for tools/observability_canvas/mitre_loader.py — 4 acceptance cases."""

from tools.observability_canvas.mitre_loader import MitreTechnique, load_techniques


def test_load_techniques_returns_nonempty_list():
    """load_techniques() with no filter returns a non-empty list of MitreTechnique."""
    result = load_techniques()
    assert isinstance(result, list)
    assert len(result) > 0
    assert all(isinstance(t, MitreTechnique) for t in result)


def test_load_techniques_includes_sub_techniques():
    """Sub-techniques are present and have is_sub_technique=True with a parent_id."""
    result = load_techniques()
    subs = [t for t in result if t.is_sub_technique]
    assert len(subs) > 0, "Expected at least one sub-technique"
    for sub in subs:
        assert sub.parent_id is not None
        assert "." in sub.id, f"Sub-technique ID should contain '.': {sub.id}"


def test_load_techniques_tactic_filter():
    """tactic_filter restricts results to techniques belonging to that tactic only."""
    all_techniques = load_techniques()
    filtered = load_techniques(tactic_filter="TA0001")

    assert len(filtered) > 0, "Expected results for TA0001"
    assert len(filtered) < len(all_techniques), "Filter should reduce the result set"
    for t in filtered:
        assert "TA0001" in t.tactic_ids, f"Expected TA0001 in tactic_ids, got {t.tactic_ids}"


def test_load_techniques_deterministic_ordering():
    """Results are sorted deterministically by technique ID (ascending lexicographic)."""
    result = load_techniques()
    ids = [t.id for t in result]
    assert ids == sorted(ids), "Techniques must be sorted by ID in ascending order"

    # Second call must produce identical ordering
    result2 = load_techniques()
    assert [t.id for t in result2] == ids
