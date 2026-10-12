# CUI // SP-CTI
"""Tests for check_template_variable_parity (task-coh-3a357f8a).

Regression coverage for the root-cause fix of the 476-violation false-positive
flood: the old finder regex-flagged the first identifier of every
``{{ ... }}`` / ``{% if ... %}`` / ``{% for ... %}``, so loop targets, Jinja
keywords, macro names, and guarded names were reported as missing context.

Builds a synthetic tools/<pkg>/blueprint.py + tools/dashboard/templates tree
under a scratch dir (not pytest's tmp_path: the checker skips blueprint paths
containing "test", and tmp_path always matches), points PROJECT_ROOT at it,
and asserts both directions: real
UndefinedError candidates are still flagged, while template-scoped definitions,
keywords, guards, context-processor names, and bare safe usages are not.
"""
from __future__ import annotations

import pathlib
import shutil
import sys
import tempfile


ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.workflow import coherence_checker as cc  # noqa: E402


# The checker skips any blueprint path containing "test" (test-fixture guard).
# pytest's tmp_path lives under /tmp/pytest-*/test_*, so tmp_path trees are
# invisible to it. Build scratch repos in a plain tempfile dir instead.
def _make_repo():
    return pathlib.Path(tempfile.mkdtemp(prefix="coh-parity-")) / "repo"


def _build_tree(root: pathlib.Path, py_files: dict, templates: dict) -> pathlib.Path:
    """Write blueprint/template files under a synthetic repo root."""
    (root / "tools" / "dashboard" / "templates").mkdir(parents=True)
    (root / "icdev").mkdir(parents=True)
    for rel, body in py_files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    for name, body in templates.items():
        (root / "tools" / "dashboard" / "templates" / name).write_text(
            body, encoding="utf-8"
        )
    return root


def _run(monkeypatch, py_files: dict, templates: dict):
    scratch = _make_repo()
    try:
        repo = _build_tree(scratch, py_files, templates)
        monkeypatch.setattr(cc, "PROJECT_ROOT", repo)
        return cc.check_template_variable_parity()
    finally:
        shutil.rmtree(scratch.parent, ignore_errors=True)


BP = "tools/x/blueprint.py"


# ---------------------------------------------------------------------------
# True positive: strict usage of a name no call site provides → warn
# ---------------------------------------------------------------------------

def test_missing_strict_var_flagged(monkeypatch):
    result = _run(
        monkeypatch,
        {BP: "from flask import render_template\n"
             "def v():\n    return render_template('t.html', other=1)\n"},
        {"t.html": "{{ missing_var.attr }}\n{{ other }}\n"},
    )
    assert result.status == "warn", f"Expected warn, got {result.status}"
    assert any("missing_var" in m for m in result.missing), result.missing
    assert not any("other" in m for m in result.missing), result.missing
    # WARN-only contract: may come from a context processor — never fail.
    assert result.status != "fail"


# ---------------------------------------------------------------------------
# Template-scoped definitions are not context variables
# ---------------------------------------------------------------------------

def test_loop_set_macro_from_import_not_flagged(monkeypatch):
    template = (
        "{% for d, i in items %}{{ d.attr }}{{ i }}{% endfor %}\n"
        "{% set total = items|length %}{{ total }}\n"
        "{% macro card(t) %}{{ t }}{% endmacro %}{{ card(name) }}\n"
        "{% from 'macros.html' import badge as pill %}{{ pill(name) }}\n"
        "{% with x = name %}{{ x }}{% endwith %}\n"
    )
    result = _run(
        monkeypatch,
        {BP: "from flask import render_template\n"
             "def v():\n    return render_template('t.html', items=[], name='x')\n"},
        {"t.html": template},
    )
    assert result.status == "pass", f"Expected pass, got {result.status}: {result.actual}"


# ---------------------------------------------------------------------------
# Jinja keywords, operators, and test names are never variables
# ---------------------------------------------------------------------------

def test_jinja_keywords_not_flagged(monkeypatch):
    template = (
        "{% if name is defined %}ok{% endif %}\n"
        "{% if items is iterable %}list{% endif %}\n"
        "{% if not flag %}off{% endif %}\n"
        "{{ 'a' if flag else 'b' }}\n"
        "{{ none }} {{ true }} {{ false }}\n"
    )
    result = _run(
        monkeypatch,
        {BP: "from flask import render_template\n"
             "def v():\n    return render_template('t.html')\n"},
        {"t.html": template},
    )
    assert result.status == "pass", f"Expected pass, got {result.status}: {result.actual}"


# ---------------------------------------------------------------------------
# Guarded names cannot raise UndefinedError
# ---------------------------------------------------------------------------

def test_guarded_names_not_flagged(monkeypatch):
    template = (
        "{{ maybe|default('n/a') }}\n"
        "{{ fallback or 'x' }}\n"
        "{% if flaggy %}<b>on</b>{% endif %}\n"
    )
    result = _run(
        monkeypatch,
        {BP: "from flask import render_template\n"
             "def v():\n    return render_template('t.html')\n"},
        {"t.html": template},
    )
    assert result.status == "pass", f"Expected pass, got {result.status}: {result.actual}"


# ---------------------------------------------------------------------------
# Per-template contract = UNION of kwargs across all call sites in the file
# ---------------------------------------------------------------------------

def test_union_of_kwargs_across_call_sites(monkeypatch):
    template = (
        "{% if mode == 'list' %}{% for r in rows %}{{ r.x }}{% endfor %}"
        "{% else %}{{ row.x }}{% endif %}\n"
    )
    result = _run(
        monkeypatch,
        {BP: "from flask import render_template\n"
             "def a():\n    return render_template('t.html', mode='list', rows=[])\n"
             "def b():\n    return render_template('t.html', mode='detail', row=None)\n"},
        {"t.html": template},
    )
    assert result.status == "pass", f"Expected pass, got {result.status}: {result.actual}"


# ---------------------------------------------------------------------------
# Context processor names are treated as provided
# ---------------------------------------------------------------------------

def test_context_processor_names_provided(monkeypatch):
    py_files = {
        BP: "from flask import render_template\n"
            "def v():\n    return render_template('t.html')\n",
        "tools/x/ctx.py": "def register(app):\n"
            "    @app.context_processor\n"
            "    def inject():\n"
            "        return {'shared_thing': 1}\n",
    }
    result = _run(
        monkeypatch, py_files,
        {"t.html": "{{ shared_thing.attr }}\n"},
    )
    assert result.status == "pass", f"Expected pass, got {result.status}: {result.actual}"


def test_context_processor_naming_convention(monkeypatch):
    py_files = {
        BP: "from flask import render_template\n"
            "def v():\n    return render_template('t.html')\n",
        "tools/x/ctx.py": "def nav_context_processor():\n"
            "    return {'nav_extra': []}\n",
    }
    result = _run(
        monkeypatch, py_files,
        {"t.html": "{% for l in nav_extra %}{{ l.href }}{% endfor %}\n"},
    )
    assert result.status == "pass", f"Expected pass, got {result.status}: {result.actual}"


# ---------------------------------------------------------------------------
# Bare output / bare truthiness are safe under Jinja2's default Undefined
# ---------------------------------------------------------------------------

def test_bare_usage_not_flagged(monkeypatch):
    result = _run(
        monkeypatch,
        {BP: "from flask import render_template\n"
             "def v():\n    return render_template('t.html')\n"},
        {"t.html": "{{ bare_maybe }}\n{% if bare_flag %}y{% endif %}\n"},
    )
    assert result.status == "pass", f"Expected pass, got {result.status}: {result.actual}"


# ---------------------------------------------------------------------------
# Dynamic **kwargs call sites are skipped (conservative)
# ---------------------------------------------------------------------------

def test_double_star_kwargs_calls_skipped(monkeypatch):
    result = _run(
        monkeypatch,
        {BP: "from flask import render_template\n"
             "def v(ctx):\n    return render_template('t.html', **ctx)\n"},
        {"t.html": "{{ any_name.attr }}\n"},
    )
    assert result.status == "pass", f"Expected pass, got {result.status}: {result.actual}"
