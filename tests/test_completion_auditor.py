"""Tests for tools/quality/completion_auditor.py.

The completion auditor scores every dashboard canvas against the 8-component
completeness gate (CLAUDE.md) and emits a deterministic scorecard. These tests
pin down:

  * scorecard math (pct == 100 * present / total, rounded to 1 dp)
  * JSON output shape stability (so downstream consumers don't break)
  * gap detection for a synthetic *incomplete* canvas (the ACE-style failure)
  * that ``run_audit()`` covers EVERY discovered canvas (the discovery-based
    successor to the task's ``audit_all()/_CANVAS_DEFS`` framing — discovery is
    a strict superset of the legacy ``_CANVAS_DEFS`` registration list)

All scenarios drive off a synthetic on-disk template tree via a monkeypatched
``PROJECT_ROOT`` — there is no live DB dependency.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.quality import completion_auditor as ca


# ---------------------------------------------------------------------------
# Synthetic canvas tree helpers
# ---------------------------------------------------------------------------

def _make_canvas(root: Path, name: str, components: set[str]) -> None:
    """Materialize the subset of the 8 completeness components for one canvas.

    ``components`` is the set of COMPONENT_KEYS to make *present*. Anything not
    listed is left absent so the auditor scores it as a gap.
    """
    templates = root / "tools" / "dashboard" / "templates" / name
    icdev_templates = root / "icdev" / "tools" / "dashboard" / "templates" / name

    # 1. template — also the carrier for the iqe_widget marker (8)
    if "template" in components:
        templates.mkdir(parents=True, exist_ok=True)
        body = "<html>page</html>"
        if "iqe_widget" in components:
            body = '{% include "includes/iqe_query_widget.html" %}'
        (templates / "page.html").write_text(body, encoding="utf-8")

    # 2. icdev/ mirror parity
    if "icdev_mirror" in components:
        icdev_templates.mkdir(parents=True, exist_ok=True)
        (icdev_templates / "page.html").write_text("<html>mirror</html>", encoding="utf-8")

    # 3. blueprint with a @route decorator
    if "blueprint_route" in components:
        bp = root / "tools" / name
        bp.mkdir(parents=True, exist_ok=True)
        (bp / "blueprint.py").write_text(
            "bp = object()\n@bp.route('/x')\ndef x():\n    return 'x'\n", encoding="utf-8"
        )

    # 4. backing module (any .py other than __init__/blueprint)
    if "backing_module" in components:
        mod = root / "tools" / name
        mod.mkdir(parents=True, exist_ok=True)
        (mod / "service.py").write_text("VALUE = 1\n", encoding="utf-8")

    # 6. IQE adapter
    if "iqe_adapter" in components:
        adapters = root / "tools" / "iqe" / "adapters"
        adapters.mkdir(parents=True, exist_ok=True)
        (adapters / f"{name}.py").write_text("# adapter\n", encoding="utf-8")

    # 7. IQE seed queries
    if "iqe_seed_queries" in components:
        q = root / "context" / "iqe" / "queries" / name
        q.mkdir(parents=True, exist_ok=True)
        (q / "q1.yaml").write_text("name: q1\n", encoding="utf-8")


def _base_html_with(*canvases: str) -> str:
    """A base.html body that holds a nav link for each named canvas."""
    return "<nav>" + "".join(f'<a href="/{c}">{c}</a>' for c in canvases) + "</nav>"


@pytest.fixture()
def synthetic_root(tmp_path, monkeypatch):
    """A monkeypatched PROJECT_ROOT pointing at an empty synthetic repo tree."""
    (tmp_path / "tools" / "dashboard" / "templates").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(ca, "PROJECT_ROOT", tmp_path)
    return tmp_path


# ---------------------------------------------------------------------------
# Scorecard math
# ---------------------------------------------------------------------------

def test_pct_is_present_over_total():
    """pct == round(100 * score / total, 1) for every score in range."""
    for score in range(0, ca.TOTAL_COMPONENTS + 1):
        cs = ca.CanvasScore(canvas="c", score=score)
        assert cs.total == ca.TOTAL_COMPONENTS
        assert cs.pct == round(100.0 * score / ca.TOTAL_COMPONENTS, 1)

    full = ca.CanvasScore(canvas="c", score=ca.TOTAL_COMPONENTS)
    assert full.pct == 100.0
    assert ca.CanvasScore(canvas="c", score=0).pct == 0.0


def test_pct_guards_zero_total():
    """A zero-total score never divides by zero."""
    cs = ca.CanvasScore(canvas="c", score=0, total=0)
    assert cs.pct == 0.0


def test_missing_lists_only_absent_components():
    present = {k: False for k in ca.COMPONENT_KEYS}
    present["template"] = True
    present["nav_link"] = True
    cs = ca.CanvasScore(canvas="c", present=present, score=2)
    assert "template" not in cs.missing
    assert "nav_link" not in cs.missing
    # everything else is reported missing
    expected = [k for k in ca.COMPONENT_KEYS if k not in ("template", "nav_link")]
    assert sorted(cs.missing) == sorted(expected)


def test_score_equals_present_count(synthetic_root):
    """audit_canvas score is exactly the count of present components."""
    _make_canvas(synthetic_root, "alpha", {"template", "backing_module", "iqe_adapter"})
    cs = ca.audit_canvas("alpha", _base_html_with("alpha"))
    # template + backing_module + iqe_adapter + nav_link(from base) == 4
    assert cs.score == sum(1 for v in cs.present.values() if v)
    assert cs.present["template"] is True
    assert cs.present["backing_module"] is True
    assert cs.present["iqe_adapter"] is True
    assert cs.present["nav_link"] is True
    assert cs.score == 4


# ---------------------------------------------------------------------------
# Gap detection — synthetic incomplete canvas (the ACE failure mode)
# ---------------------------------------------------------------------------

def test_incomplete_canvas_flags_gaps(synthetic_root):
    """A canvas with only a template scores 1/8 and lists the 7 gaps."""
    _make_canvas(synthetic_root, "ghost", {"template"})
    cs = ca.audit_canvas("ghost", _base_html_with())  # no nav link

    assert cs.score == 1
    assert cs.present["template"] is True
    assert cs.total == ca.TOTAL_COMPONENTS
    assert cs.pct == round(100.0 / ca.TOTAL_COMPONENTS, 1)

    expected_missing = [k for k in ca.COMPONENT_KEYS if k != "template"]
    assert sorted(cs.missing) == sorted(expected_missing)


def test_complete_canvas_has_no_gaps(synthetic_root):
    """A canvas with all 8 components scores 8/8 with empty missing list."""
    _make_canvas(synthetic_root, "full", set(ca.COMPONENT_KEYS))
    cs = ca.audit_canvas("full", _base_html_with("full"))

    assert cs.score == ca.TOTAL_COMPONENTS
    assert cs.pct == 100.0
    assert cs.missing == []
    assert all(cs.present[k] for k in ca.COMPONENT_KEYS)


def test_mirror_parity_requires_matching_filename(synthetic_root):
    """icdev_mirror is False unless every source template has a mirror twin."""
    # template present, but no icdev mirror
    _make_canvas(synthetic_root, "drift", {"template"})
    cs = ca.audit_canvas("drift", _base_html_with())
    assert cs.present["icdev_mirror"] is False


# ---------------------------------------------------------------------------
# JSON shape stability
# ---------------------------------------------------------------------------

def _write_base_html(root: Path, *canvases: str) -> None:
    """run_audit() reads base.html from disk for the nav_link check."""
    base = root / "tools" / "dashboard" / "templates" / "base.html"
    base.write_text(_base_html_with(*canvases), encoding="utf-8")


def test_json_shape_is_stable(synthetic_root):
    _make_canvas(synthetic_root, "full", set(ca.COMPONENT_KEYS))
    _make_canvas(synthetic_root, "ghost", {"template"})
    _write_base_html(synthetic_root, "full")  # full canvas gets its nav link
    results = ca.run_audit()

    payload = json.loads(ca.to_json(results))

    # top-level keys
    assert set(payload.keys()) == {
        "total_canvases",
        "fully_complete",
        "incomplete",
        "components",
        "canvases",
    }
    assert payload["total_canvases"] == 2
    assert payload["fully_complete"] == 1
    assert payload["incomplete"] == 1
    assert payload["components"] == ca.COMPONENT_KEYS

    # per-canvas keys
    for entry in payload["canvases"]:
        assert set(entry.keys()) == {
            "canvas",
            "score",
            "total",
            "pct",
            "present",
            "missing",
        }
        assert set(entry["present"].keys()) == set(ca.COMPONENT_KEYS)
        assert entry["total"] == ca.TOTAL_COMPONENTS
        # present/missing are consistent with score
        assert entry["score"] == sum(1 for v in entry["present"].values() if v)
        assert sorted(entry["missing"]) == sorted(
            k for k, v in entry["present"].items() if not v
        )


def test_json_counts_sum_to_total(synthetic_root):
    _make_canvas(synthetic_root, "full", set(ca.COMPONENT_KEYS))
    _make_canvas(synthetic_root, "ghost", {"template"})
    payload = json.loads(ca.to_json(ca.run_audit()))
    assert payload["fully_complete"] + payload["incomplete"] == payload["total_canvases"]


# ---------------------------------------------------------------------------
# Coverage: run_audit() covers every discovered canvas
# ---------------------------------------------------------------------------

def test_run_audit_covers_all_discovered_canvases(synthetic_root):
    """Every canvas directory with a template appears exactly once in the audit.

    This is the discovery-based successor to the task's ``audit_all() covers
    all _CANVAS_DEFS keys`` requirement: ``_discover_canvases`` enumerates every
    template dir (a strict superset of the legacy _CANVAS_DEFS list), and
    run_audit must score each one.
    """
    names = {"alpha", "bravo", "charlie"}
    for n in names:
        _make_canvas(synthetic_root, n, {"template"})

    templates_dir = synthetic_root / "tools" / "dashboard" / "templates"
    discovered = set(ca._discover_canvases(templates_dir))
    assert discovered == names

    results = ca.run_audit()
    audited = {r.canvas for r in results}
    assert audited == discovered
    assert len(results) == len(names)  # no canvas scored twice


def test_discover_ignores_dirs_without_html(synthetic_root):
    """A template subdir with no .html file is not a canvas."""
    templates_dir = synthetic_root / "tools" / "dashboard" / "templates"
    (templates_dir / "empty").mkdir()
    _make_canvas(synthetic_root, "real", {"template"})

    discovered = set(ca._discover_canvases(templates_dir))
    assert discovered == {"real"}
    assert "empty" not in discovered


def test_run_audit_sorted_least_to_most_complete(synthetic_root):
    """Results are deterministically ordered: ascending score, then name."""
    _make_canvas(synthetic_root, "zfull", set(ca.COMPONENT_KEYS))
    _make_canvas(synthetic_root, "amid", {"template", "backing_module"})
    _make_canvas(synthetic_root, "ghost", {"template"})

    results = ca.run_audit()
    scores = [r.score for r in results]
    assert scores == sorted(scores)
    # tie-break is alphabetical by canvas name within equal scores
    for a, b in zip(results, results[1:]):
        assert (a.score, a.canvas) <= (b.score, b.canvas)


# ---------------------------------------------------------------------------
# Markdown rendering (smoke — shape, not exact bytes)
# ---------------------------------------------------------------------------

def test_markdown_lists_every_canvas(synthetic_root):
    _make_canvas(synthetic_root, "full", set(ca.COMPONENT_KEYS))
    _make_canvas(synthetic_root, "ghost", {"template"})
    md = ca.to_markdown(ca.run_audit())

    assert "# Completion Scorecard" in md
    assert "| full |" in md
    assert "| ghost |" in md
    # header carries all 8 component columns
    for key in ca.COMPONENT_KEYS:
        assert key in md
