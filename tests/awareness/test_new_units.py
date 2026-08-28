# CUI // SP-CTI
"""A capability added by THIS card must have run once (wire-run-01).

THE GAP THIS FILLS. ``check_capability_liveness`` compares a whole-class count against a
grandfathered budget, so a unit added by the card under review disappears into a backlog of 510
units that are *allowed* to be inert. Measured 2026-08-27: 512 of 622 declared units have never
been consumed, 510 of them explicitly budgeted. A new unit is therefore invisible to the one
check that exists to catch it -- it is one more inert unit in a class already over budget, and
the author cannot tell their own omission from the backlog.

``new_units`` asks the narrower question a done gate can act on: *was this unit's declaration
added in this diff, and has it ever produced a consumption event?* The remedy is to run it once.
"""
from __future__ import annotations

import pathlib
import subprocess

import pytest

from tools.awareness import capability_consumption as cc

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]


def _report(classes):
    return {"classes": classes, "totals": {}}


def _cls(name, *, inert=(), truncated=False, available=True):
    return {
        "capability_class": name,
        "inert_units": list(inert),
        "inert_units_truncated": truncated,
        "telemetry_available": available,
        "telemetry_table": "some_table",
    }


@pytest.fixture
def stub(monkeypatch):
    """Drive new_units from both seams: what was measured, and what the diff added."""

    def _install(classes, added):
        monkeypatch.setattr(cc, "collect", lambda **kw: _report(classes))
        monkeypatch.setattr(cc, "_added_lines", lambda *a, **k: added)

    return _install


# ---------------------------------------------------------------------------
# The two halves of the question
# ---------------------------------------------------------------------------


def test_a_newly_declared_unit_with_no_events_is_named(stub):
    stub([_cls("reflex", inert=["brand_new_reflex"])], '+    "brand_new_reflex",')

    result = cc.new_units("origin/main")

    assert result["state"] == "measured"
    assert [f["unit"] for f in result["findings"]] == ["brand_new_reflex"]
    assert result["findings"][0]["capability_class"] == "reflex"


def test_a_unit_that_already_has_events_is_silent(stub):
    """The same diff, the same line -- but the unit is not inert, so it has RUN.

    This is the half that keeps the check honest. A gate that fired on every declaration
    touched by a diff would fire on a rename, a reformat and a re-sort, and would be turned off
    inside a week.
    """
    stub([_cls("reflex", inert=[])], '+    "brand_new_reflex",')

    result = cc.new_units("origin/main")

    assert result["state"] == "measured"
    assert result["findings"] == []


def test_a_unit_that_is_inert_but_not_in_the_diff_is_silent(stub):
    """The 510 grandfathered units. They are inert and they are not this card's business."""
    stub([_cls("reflex", inert=["some_old_inert_reflex"])], "+    unrelated_line = 1")

    assert cc.new_units("origin/main")["findings"] == []


# ---------------------------------------------------------------------------
# Every way of not knowing, and none of them is a clean bill
# ---------------------------------------------------------------------------


def test_no_class_scanned_is_unmeasurable_never_clean(stub):
    """The defect caught on this function's FIRST live run.

    Every class reported telemetry unavailable (a worktree with no .env silently reads an empty
    SQLite database), and the function printed `0 findings` and exited 0 -- a clean bill from a
    measurement that never happened. Zero findings is a claim about the diff; zero scanned
    classes is a claim about nothing.
    """
    stub([_cls("reflex", available=False)], "+ anything")

    result = cc.new_units("origin/main")

    assert result["state"] == "unmeasurable"
    assert result["reason"]


def test_an_unreadable_diff_is_undiffable_never_clean(stub):
    """`_added_lines` returns None for a git that could not answer -- distinct from '' for a
    diff that added nothing. Merging the two reports a clean bill for a repo it failed to read."""
    stub([_cls("reflex", inert=["brand_new_reflex"])], None)

    result = cc.new_units("origin/main")

    assert result["state"] == "unmeasurable"
    assert any(
        "git could not diff" in e["reason"] for e in result["classes_undiffable"]
    )


def test_a_truncated_inert_list_is_undiffable(stub):
    """`inert_units` is capped for display. A new unit that fell off the end would read as
    consumed -- a false clean produced by a display limit."""
    stub([_cls("reflex", inert=["brand_new_reflex"], truncated=True)], '+ "brand_new_reflex"')

    result = cc.new_units("origin/main")

    assert result["state"] == "unmeasurable"
    assert result["findings"] == []


def test_an_empty_report_is_unmeasurable(monkeypatch):
    monkeypatch.setattr(cc, "collect", lambda **kw: _report([]))
    assert cc.new_units("origin/main")["state"] == "unmeasurable"


# ---------------------------------------------------------------------------
# The map is complete, and stays complete
# ---------------------------------------------------------------------------


def test_every_capability_class_is_mapped_or_declared_undiffable():
    """A twelfth class added without a decision here would silently never be scanned."""
    unaccounted = sorted(
        set(cc.PROBES) - set(cc.DECLARATION_FILES) - set(cc.UNDIFFABLE_CLASSES)
    )
    assert unaccounted == [], (
        f"capability classes with no declaration file and no undiffable reason: {unaccounted}. "
        "Add the declaring path to DECLARATION_FILES, or name it in UNDIFFABLE_CLASSES with "
        "the reason a diff cannot answer for it."
    )


def test_no_class_is_both_mapped_and_undiffable():
    assert set(cc.DECLARATION_FILES) & set(cc.UNDIFFABLE_CLASSES) == set()


def test_every_declared_path_exists():
    """A path that has moved makes its class silently unscannable -- the diff comes back empty
    and reads as 'nothing new', which is the fabricated clean bill again."""
    missing = [
        rel
        for paths in cc.DECLARATION_FILES.values()
        for rel in paths
        if not (REPO_ROOT / rel).exists()
    ]
    assert missing == [], f"declaration paths that no longer exist: {sorted(set(missing))}"


def test_the_remedy_is_to_run_it_never_to_raise_a_budget(stub):
    stub([_cls("reflex", inert=["brand_new_reflex"])], '+ "brand_new_reflex"')

    remedy = cc.new_units("origin/main")["findings"][0]["remedy"]

    assert "do NOT raise a budget" in remedy
    assert "external_only_surfaces" in remedy


# ---------------------------------------------------------------------------
# `_added_lines` against a real repository
# ---------------------------------------------------------------------------


def _git(*args, cwd):
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    """A real git repo. The monkeypatched tests above never exercise git itself."""
    _git("init", "-q", cwd=tmp_path)
    _git("config", "user.email", "t@example.com", cwd=tmp_path)
    _git("config", "user.name", "t", cwd=tmp_path)
    (tmp_path / "decl.py").write_text('NAMES = [\n    "old_unit",\n]\n', encoding="utf-8")
    _git("add", "-A", cwd=tmp_path)
    _git("commit", "-qm", "base", cwd=tmp_path)
    return tmp_path


def test_added_lines_reports_only_additions(repo):
    base = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(repo), capture_output=True, text=True
    ).stdout.strip()
    (repo / "decl.py").write_text(
        'NAMES = [\n    "old_unit",\n    "new_unit",\n]\n', encoding="utf-8"
    )
    _git("add", "-A", cwd=repo)
    _git("commit", "-qm", "add new_unit", cwd=repo)

    added = cc._added_lines(base, ("decl.py",), root=repo)

    assert added is not None
    assert "new_unit" in added
    assert "old_unit" not in added, "an untouched declaration must not read as added"


def test_added_lines_returns_none_for_an_unknown_ref(repo):
    assert cc._added_lines("no-such-ref", ("decl.py",), root=repo) is None


def test_added_lines_returns_empty_for_a_diff_that_added_nothing(repo):
    assert cc._added_lines("HEAD", ("decl.py",), root=repo) == ""
