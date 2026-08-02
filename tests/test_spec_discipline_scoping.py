# CUI // SP-CTI
"""The Beyoncé Rule must catch untested code without failing on impossible names.

`check_spec_discipline` demanded a changed `test_<basename>.py` for any changed
`tools/` file with public functions. That is a filename lottery, not a coverage
check, and it produced two failures nobody could satisfy:

* **Migrations.** Every migration is `<n>_<slug>/up.py`, so the rule asks each
  one for `test_up.py` — a single filename that cannot serve more than one
  migration. Migration 309, merged since 2026-07-28, fails the rule identically,
  and no migration in this repo has ever had a `test_up.py`.
* **Repeated basenames.** `init_db.py` exists in roughly ten canvases. They
  cannot all map to one `tests/test_init_db.py`.

A rule that cannot be satisfied gets ignored, and an ignored gate protects
nothing — so the fix makes it answer the question it was meant to ask: is this
module actually referenced by a test in the same change?

These tests pin both directions. The exemption must not become a hole.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.workflow import coherence_checker as cc  # noqa: E402


def _run(paths):
    return cc.check_spec_discipline(changed_files=[Path(p) for p in paths])


def _beyonce_failures(result):
    return [m for m in (result.missing or []) if "beyonce-rule" in m]


@pytest.fixture()
def impl(tmp_path, monkeypatch):
    """A module under tools/ with a public function and no test."""
    root = tmp_path
    (root / "tools" / "demo").mkdir(parents=True)
    p = root / "tools" / "demo" / "widget.py"
    p.write_text("def build_widget():\n    return 1\n", encoding="utf-8")
    monkeypatch.setattr(cc, "PROJECT_ROOT", root)
    return root, p


# --------------------------------------------------------------------------
# The rule still works
# --------------------------------------------------------------------------

def test_public_function_with_no_test_still_fails(impl):
    root, p = impl
    assert _beyonce_failures(_run([p])), "an untested public function must fail"


def test_matching_test_filename_satisfies_it(impl):
    root, p = impl
    t = root / "tests" / "test_widget.py"
    t.parent.mkdir(exist_ok=True)
    t.write_text("def test_x():\n    pass\n", encoding="utf-8")
    assert not _beyonce_failures(_run([p, t]))


def test_private_functions_alone_do_not_trigger_it(impl):
    root, p = impl
    p.write_text("def _helper():\n    return 1\n", encoding="utf-8")
    assert not _beyonce_failures(_run([p]))


# --------------------------------------------------------------------------
# Coverage by reference, not by filename
# --------------------------------------------------------------------------

def test_a_differently_named_test_that_imports_the_module_satisfies_it(impl):
    """The init_db.py case: ten canvases cannot share one test filename."""
    root, p = impl
    t = root / "tests" / "test_widget_behaviour.py"
    t.parent.mkdir(exist_ok=True)
    t.write_text(
        "from tools.demo.widget import build_widget\n\n"
        "def test_it():\n    assert build_widget() == 1\n",
        encoding="utf-8",
    )
    assert not _beyonce_failures(_run([p, t]))


def test_a_test_referencing_the_path_satisfies_it(impl):
    """Schema/DDL guards assert on a path rather than importing."""
    root, p = impl
    t = root / "tests" / "test_something_else.py"
    t.parent.mkdir(exist_ok=True)
    t.write_text(
        'PATH = "tools/demo/widget.py"\n\ndef test_it():\n    assert PATH\n',
        encoding="utf-8",
    )
    assert not _beyonce_failures(_run([p, t]))


def test_an_unrelated_test_does_NOT_satisfy_it(impl):
    """The exemption must not become 'any test file anywhere'."""
    root, p = impl
    t = root / "tests" / "test_unrelated.py"
    t.parent.mkdir(exist_ok=True)
    t.write_text("def test_other():\n    assert True\n", encoding="utf-8")
    assert _beyonce_failures(_run([p, t])), (
        "a test that never mentions the module must not count as covering it"
    )


# --------------------------------------------------------------------------
# Migrations are exempt
# --------------------------------------------------------------------------

def test_migration_up_py_is_exempt(tmp_path, monkeypatch):
    root = tmp_path
    d = root / "tools" / "db" / "migrations" / "326_demo"
    d.mkdir(parents=True)
    p = d / "up.py"
    p.write_text("def up():\n    return None\n", encoding="utf-8")
    monkeypatch.setattr(cc, "PROJECT_ROOT", root)
    assert not _beyonce_failures(_run([p])), (
        "every migration is named up.py — they cannot share one test_up.py"
    )


def test_the_exemption_is_scoped_to_the_migrations_directory(tmp_path, monkeypatch):
    """A file merely called up.py elsewhere is NOT exempt."""
    root = tmp_path
    d = root / "tools" / "elevator"
    d.mkdir(parents=True)
    p = d / "up.py"
    p.write_text("def go_up():\n    return None\n", encoding="utf-8")
    monkeypatch.setattr(cc, "PROJECT_ROOT", root)
    assert _beyonce_failures(_run([p])), (
        "the exemption must key on the migrations path, not the filename"
    )


def test_no_changed_files_is_not_a_pass(tmp_path, monkeypatch):
    monkeypatch.setattr(cc, "PROJECT_ROOT", tmp_path)
    result = cc.check_spec_discipline(changed_files=None)
    assert result.status != "fail"
    assert "requires --changed-files" in result.message
