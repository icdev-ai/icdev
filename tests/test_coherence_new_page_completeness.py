#!/usr/bin/env python3
# CUI // SP-CTI
"""Unit tests for the hardened `new_page_completeness` coherence check (TCH).

Background (tch-gate-*): the original completeness gate only globbed
``tools/dashboard/templates/*/page.html``. Canvases whose templates are named
differently (``slides/{index,detail,new}.html``, ``mfa/{enroll,challenge}.html``,
``zta/lac_simulator.html``) shipped with no ``icdev/`` mirror at all and slipped
through entirely — and ~44/56 canvases were never checked. ACE slipped exactly
this way.

The hardened ``check_new_page_completeness`` adds a full mirror-parity sub-check
that set-diffs every ``*.html`` in each canvas dir against its ``icdev/`` mirror,
so a canvas with NO ``page.html`` is now examined too.

These tests build fully synthetic temp-canvas fixtures under a patched
``PROJECT_ROOT`` and exercise the gate's behaviour directly — no live DB, no
real ``args/`` whitelist. Patching is shim-aware: we import the canonical
``tools.workflow.coherence_checker`` module object and ``monkeypatch.setattr``
its module globals, then call the function off that same object, so the
function's ``__globals__`` lookups see the patched values (the ``tools.*`` and
``icdev.tools.*`` shim modules are distinct objects).
"""

from pathlib import Path


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------

# Marker the gate scans for as IQE-widget component #7.
PAGE_WIDGET = "{% include 'includes/iqe_query_widget.html' %}"

# Minimal blueprint that satisfies the @route + render_template checks.
BLUEPRINT_PY = (
    "from flask import Blueprint, render_template\n"
    "bp = Blueprint('x', __name__)\n\n\n"
    "@bp.route('/x')\n"
    "def x():\n"
    "    return render_template('x/page.html')\n"
)


def _write(path: Path, text: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _norm(s: str) -> str:
    """Normalize OS path separators so assertions are platform-agnostic."""
    return s.replace("\\", "/")


def make_root(tmp_path: Path) -> Path:
    """Create an empty synthetic project root with the dirs the gate reads."""
    root = tmp_path / "proj"
    (root / "tools" / "dashboard" / "templates").mkdir(parents=True)
    (root / "icdev" / "tools" / "dashboard" / "templates").mkdir(parents=True)
    (root / "tools" / "iqe" / "adapters").mkdir(parents=True)
    (root / "context" / "iqe" / "queries").mkdir(parents=True)
    return root


def write_base_html(root: Path, nav_slugs=()) -> None:
    """Write base.html containing nav links for the given canvas slugs."""
    links = "".join(f'<a href="/{s}">{s}</a>\n' for s in nav_slugs)
    _write(root / "tools" / "dashboard" / "templates" / "base.html", f"<nav>\n{links}</nav>\n")


def build_page_canvas(root: Path, name: str, *, complete: bool = True, mirror: bool = True) -> None:
    """Build a page.html-keyed canvas.

    When ``complete`` is True all seven sibling components are created so the
    8-component gate passes (the caller must still add the nav link via
    ``write_base_html``). When False, only an incomplete page.html is written.
    """
    page_text = f"<html><body>{PAGE_WIDGET if complete else ''}</body></html>"
    _write(root / "tools" / "dashboard" / "templates" / name / "page.html", page_text)
    if mirror:
        _write(
            root / "icdev" / "tools" / "dashboard" / "templates" / name / "page.html",
            page_text,
        )
    if complete:
        _write(root / "tools" / name / "blueprint.py", BLUEPRINT_PY)
        _write(root / "tools" / name / "service.py", "def go():\n    return 1\n")
        _write(root / "tools" / "iqe" / "adapters" / f"{name}.py", "# iqe adapter\n")
        _write(root / "context" / "iqe" / "queries" / name / "seed.yaml", "queries: []\n")


def build_template_only_canvas(root: Path, name: str, template_names, *, mirror: bool = True) -> None:
    """Build a canvas with arbitrary template names and NO page.html.

    This is the slides/mfa/zta class the original gate was blind to.
    """
    for t in template_names:
        text = f"<html><!-- {name}/{t} --></html>"
        _write(root / "tools" / "dashboard" / "templates" / name / t, text)
        if mirror:
            _write(root / "icdev" / "tools" / "dashboard" / "templates" / name / t, text)


def run_check(root: Path, monkeypatch, whitelist: Path = None):
    """Invoke check_new_page_completeness against the synthetic root.

    Shim-aware: patch + call on the SAME canonical module object so the
    function's global lookups (PROJECT_ROOT, whitelist config) resolve to the
    fixtures, not the real repo.
    """
    from tools.workflow import coherence_checker as cc

    monkeypatch.setattr(cc, "PROJECT_ROOT", root)
    # Default to a non-existent path → loader returns an empty set (nothing
    # grandfathered). Tests that need a whitelist pass an explicit file.
    monkeypatch.setattr(
        cc,
        "_PAGE_COMPLETENESS_WHITELIST_CONFIG",
        whitelist if whitelist is not None else (root / "no_such_whitelist.yaml"),
    )
    return cc.check_new_page_completeness()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestHardenedCompleteness:
    """Behavioural tests for the hardened new_page_completeness gate."""

    def test_non_page_canvas_is_now_checked(self, tmp_path, monkeypatch):
        """Regression for the blind spot: a canvas with only index.html (no
        page.html) and no icdev/ mirror must now be flagged. The original gate
        globbed only page.html and never saw such a canvas."""
        root = make_root(tmp_path)
        write_base_html(root)
        build_template_only_canvas(root, "solo", ["index.html"], mirror=False)

        result = run_check(root, monkeypatch)

        assert result.status == "fail"
        missing = [_norm(m) for m in result.missing]
        assert any(
            "solo/index.html" in m and "mirror missing" in m for m in missing
        ), missing

    def test_complete_canvas_passes(self, tmp_path, monkeypatch):
        """A canvas with all 8 components + icdev mirror produces no violations."""
        root = make_root(tmp_path)
        build_page_canvas(root, "alpha", complete=True, mirror=True)
        write_base_html(root, ["alpha"])

        result = run_check(root, monkeypatch)

        assert result.status == "pass", result.missing
        assert result.missing == []
        assert "complete" in result.message.lower()

    def test_missing_icdev_mirror_fails(self, tmp_path, monkeypatch):
        """slides/mfa/zta class: differently-named templates with no icdev/
        mirror must each be flagged as a mirror gap (the exact case the
        hardened sub-check was added to catch)."""
        root = make_root(tmp_path)
        write_base_html(root)
        build_template_only_canvas(
            root, "slides", ["index.html", "detail.html", "new.html"], mirror=False
        )

        result = run_check(root, monkeypatch)

        assert result.status == "fail"
        missing = [_norm(m) for m in result.missing]
        for tmpl in ("index.html", "detail.html", "new.html"):
            assert any(
                f"slides/{tmpl}" in m and "mirror missing" in m for m in missing
            ), (tmpl, missing)

    def test_partial_mirror_flags_only_unmirrored(self, tmp_path, monkeypatch):
        """When some templates are mirrored and others are not, only the
        unmirrored ones are reported — set-diff, not all-or-nothing."""
        root = make_root(tmp_path)
        write_base_html(root)
        # mirror index.html but NOT challenge.html
        build_template_only_canvas(root, "mfa", ["enroll.html"], mirror=True)
        _write(root / "tools" / "dashboard" / "templates" / "mfa" / "challenge.html", "<html></html>")

        result = run_check(root, monkeypatch)

        assert result.status == "fail"
        missing = [_norm(m) for m in result.missing]
        assert any("mfa/challenge.html" in m and "mirror missing" in m for m in missing), missing
        assert not any("mfa/enroll.html" in m for m in missing), missing

    def test_adapter_name_differs_from_dir_passes(self, tmp_path, monkeypatch):
        """dic/sdc style: template dir name (document_intelligence) differs from
        the IQE adapter file name (dic.py), the canvas has no page.html, and all
        templates are mirrored. The gate must NOT false-positive on it."""
        root = make_root(tmp_path)
        write_base_html(root)
        build_template_only_canvas(
            root, "document_intelligence", ["index.html", "search.html"], mirror=True
        )
        # adapter deliberately named differently from the directory
        _write(root / "tools" / "iqe" / "adapters" / "dic.py", "# adapter name differs from dir\n")

        result = run_check(root, monkeypatch)

        assert result.status == "pass", result.missing
        assert result.missing == []

    def test_whitelisted_canvas_is_skipped(self, tmp_path, monkeypatch):
        """A broken canvas listed in the whitelist is skipped by BOTH the
        8-component loop and the mirror-parity loop, flipping fail → pass."""
        root = make_root(tmp_path)
        write_base_html(root)
        # page.html canvas missing every sibling component and its mirror
        build_page_canvas(root, "broken", complete=False, mirror=False)
        # plus a template-only canvas with no mirror, to exercise mirror-skip
        build_template_only_canvas(root, "broken", ["extra.html"], mirror=False)

        # 1) Without a whitelist the fixture fails.
        result_no_wl = run_check(root, monkeypatch)
        assert result_no_wl.status == "fail", result_no_wl.actual

        # 2) Whitelisting the canvas skips it entirely → pass.
        wl = tmp_path / "whitelist.yaml"
        wl.write_text("whitelisted_canvases:\n  - broken\n", encoding="utf-8")
        result_wl = run_check(root, monkeypatch, whitelist=wl)

        assert result_wl.status == "pass", result_wl.missing
        assert result_wl.missing == []
        # the whitelist note is surfaced in the actual/expected lines
        assert "whitelisted" in result_wl.actual[0]

    def test_no_canvases_passes(self, tmp_path, monkeypatch):
        """An empty templates tree is vacuously complete (guards the
        checked_count == 0 branch)."""
        root = make_root(tmp_path)
        write_base_html(root)

        result = run_check(root, monkeypatch)

        assert result.status == "pass"
        assert result.missing == []
