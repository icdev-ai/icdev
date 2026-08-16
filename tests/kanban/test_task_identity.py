#!/usr/bin/env python3
"""The seed-time identity validator, and its report-only wiring. CUI // SP-CTI

rem-hyg-02. The contract is ``<task_prefix><epic_key>-<N>``; a row no epic key
matches is counted by nothing, and a card all of whose rows are unclaimed
vanishes. ``check_project_card_coverage`` finds that afterwards — these tests
pin the version that can be asked BEFORE the row exists.

Three properties matter more than the happy path:

  * nesting resolves to the CHILD card (``aadc-enh-`` beats ``aadc-``), via
    ``prefix_scope.child_prefixes`` rather than a second copy of the rule;
  * a gate sentinel is never an orphan, and an unreadable registry is never a
    finding — both would be fabrications, and this platform ships those;
  * the wiring REPORTS and never refuses (arming is rem-hyg-04), evaluates
    before any insert, and fails open.

The registry is injected in every case: a test that read the live
``args/projects.yaml`` would pass or fail on today's board data.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.kanban import task_factory  # noqa: E402
from tools.kanban import task_identity as ti  # noqa: E402


CARDS = [
    ti.Card(key="aadc", prefix="aadc-", epics=("core", "gate")),
    ti.Card(key="aadc_enh", prefix="aadc-enh-", epics=("ui", "gate")),
    ti.Card(key="rem", prefix="rem-", epics=("tst", "cap", "hyg", "e2e")),
    ti.Card(key="empty", prefix="nothing-", epics=()),
]


# --------------------------------------------------------------------------- #
# resolve
# --------------------------------------------------------------------------- #

def test_a_registered_epic_claims_the_row():
    rep = ti.resolve("rem-hyg-02", CARDS)
    assert rep == {"card": "rem", "prefix": "rem-", "epic": "hyg",
                   "claimed": True, "reason": ti.REASON_CLAIMED}


def test_an_id_no_epic_claims_is_reported_with_its_card():
    rep = ti.resolve("rem-perf-02", CARDS)
    assert rep["claimed"] is False
    assert rep["reason"] == ti.REASON_NO_EPIC
    assert rep["card"] == "rem", "name the card, or nobody knows which one is wrong"
    assert rep["epic"] is None


def test_an_unregistered_prefix_is_a_different_finding_from_a_missing_epic():
    """The HCX case: `hcx-` was in no card while the board held 25 rows."""
    rep = ti.resolve("hcx-live-01", CARDS)
    assert rep["reason"] == ti.REASON_NO_CARD
    assert rep["card"] is None
    assert rep["reason"] != ti.REASON_NO_EPIC, \
        "no card and no epic send you to different fixes; merging them hides one"


def test_the_longest_matching_prefix_wins_so_a_nested_card_keeps_its_rows():
    child = ti.resolve("aadc-enh-ui-03", CARDS)
    assert child["card"] == "aadc_enh", "the parent absorbed a child's row"
    assert child["claimed"] is True

    parent = ti.resolve("aadc-core-01", CARDS)
    assert parent["card"] == "aadc"


def test_nesting_is_not_re_derived_here():
    """The rule lives in prefix_scope; this module must ask it, not copy it."""
    from tools.project import prefix_scope

    calls: list = []
    original = ti.child_prefixes

    def _spy(prefix, all_prefixes):
        calls.append(prefix)
        return original(prefix, all_prefixes)

    ti.child_prefixes = _spy
    try:
        ti.resolve("aadc-enh-ui-03", CARDS)
    finally:
        ti.child_prefixes = original
    assert calls, "resolve answered nesting without consulting prefix_scope"
    assert prefix_scope.child_prefixes("aadc-", [c.prefix for c in CARDS]) == ["aadc-enh-"]


@pytest.mark.parametrize("task_id", ["aadc-gate-00", "aadc-gate-01", "rem-gate-00"])
def test_a_gate_sentinel_is_never_an_orphan(task_id):
    rep = ti.resolve(task_id, CARDS)
    assert rep["claimed"] is True
    assert rep["reason"] == ti.REASON_GATE
    assert rep["reason"] not in ti.ACTIONABLE_REASONS, \
        "a gate holds the card; it is not work and must not enter a progress figure"


def test_a_gate_is_recognised_even_when_the_card_declares_no_gate_epic():
    cards = [ti.Card(key="rem", prefix="rem-", epics=("hyg",))]
    assert ti.resolve("rem-gate-00", cards)["reason"] == ti.REASON_GATE


def test_an_unreadable_registry_is_unmeasured_not_a_finding():
    rep = ti.resolve("rem-hyg-02", [])
    assert rep["reason"] == ti.REASON_NO_REGISTRY
    assert rep["reason"] not in ti.ACTIONABLE_REASONS, \
        "a missing config file must not become a finding against every id"


def test_a_blank_id_is_not_a_card_coverage_defect():
    rep = ti.resolve("   ", CARDS)
    assert rep["reason"] == ti.REASON_NOT_ID_SHAPED
    assert rep["reason"] not in ti.ACTIONABLE_REASONS


def test_a_card_declaring_no_epics_claims_nothing():
    rep = ti.resolve("nothing-at-all-01", CARDS)
    assert rep["reason"] == ti.REASON_NO_EPIC
    assert rep["card"] == "empty"


# --------------------------------------------------------------------------- #
# load_cards
# --------------------------------------------------------------------------- #

def _write_registry(tmp_path, text):
    path = tmp_path / "projects.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_load_cards_keeps_a_card_with_no_epics(tmp_path):
    path = _write_registry(tmp_path, """
projects:
  - key: rem
    task_prefix: rem-
    epics:
      - key: hyg
  - key: bare
    task_prefix: bare-
""")
    cards = {c.key: c for c in ti.load_cards(path)}
    assert cards["rem"].epics == ("hyg",)
    assert cards["bare"].epics == (), \
        "a card claiming nothing is the misconfiguration, not a reason to drop it"


def test_load_cards_keeps_the_first_of_two_identical_prefixes(tmp_path):
    """No predicate assigns a row to one of them — the renderer skips the later."""
    path = _write_registry(tmp_path, """
projects:
  - key: first
    task_prefix: dup-
    epics: [{key: a}]
  - key: second
    task_prefix: dup-
    epics: [{key: b}]
""")
    cards = ti.load_cards(path)
    assert [c.key for c in cards] == ["first"]


def test_load_cards_fails_open_on_an_unreadable_file(tmp_path):
    assert ti.load_cards(tmp_path / "does-not-exist.yaml") == []
    broken = _write_registry(tmp_path, "projects: [oh: no: :\n  - ][")
    assert ti.load_cards(broken) == []


def test_the_live_registry_parses_and_claims_this_very_task():
    """One end-to-end read, so a schema change in projects.yaml is not silent."""
    cards = ti.load_cards()
    assert cards, "args/projects.yaml did not load — every id would read unowned"
    assert ti.resolve("rem-hyg-02", cards)["claimed"] is True


# --------------------------------------------------------------------------- #
# unregistered_prefixes — the board half of the state
# --------------------------------------------------------------------------- #

def test_unregistered_prefixes_groups_live_rows_no_card_owns():
    board = ["rem-hyg-02", "hcx-live-01", "hcx-evt-01", "hcx-gate-00", "aadc-core-01"]
    out = ti.unregistered_prefixes(board, CARDS)
    assert out == {"hcx-": ["hcx-evt-01", "hcx-gate-00", "hcx-live-01"]}, \
        "a gate under an unregistered prefix means the whole card is missing"


def test_unregistered_prefixes_reports_nothing_when_the_registry_is_empty():
    assert ti.unregistered_prefixes(["hcx-live-01"], []) == {}, \
        "with no cards every id looks unowned — that is a fabrication, not a finding"


# --------------------------------------------------------------------------- #
# check_batch
# --------------------------------------------------------------------------- #

def test_check_batch_finds_both_shapes_and_ends_in_an_edit():
    findings = {f["task_id"]: f for f in ti.check_batch(
        [{"id": "rem-hyg-02"}, {"id": "rem-perf-03"}, {"id": "hcx-live-01"},
         {"id": "aadc-gate-00"}], CARDS)}
    assert set(findings) == {"rem-perf-03", "hcx-live-01"}

    no_epic = findings["rem-perf-03"]
    assert no_epic["reason"] == ti.REASON_NO_EPIC
    assert no_epic["suggestion"] == "rem-<epic>-03", "name the id it should have carried"
    assert "hyg" in no_epic["detail"], "list the epics that DO exist"

    assert findings["hcx-live-01"]["reason"] == ti.REASON_NO_CARD
    assert "args/projects.yaml" in findings["hcx-live-01"]["detail"]


def test_check_batch_accepts_bare_ids_for_the_survey():
    assert [f["task_id"] for f in ti.check_batch(["rem-perf-03"], CARDS)] == ["rem-perf-03"]


def test_check_batch_returns_nothing_when_the_registry_is_unreadable():
    assert ti.check_batch([{"id": "hcx-live-01"}], []) == []


def test_check_batch_raises_nothing_on_junk():
    assert ti.check_batch([{"title": "no id"}, {"id": None}, {"id": ""}], CARDS) == []


# --------------------------------------------------------------------------- #
# the wiring: REPORT ONLY, before any insert, fail-open
# --------------------------------------------------------------------------- #

class _ReachedTheDatabase(Exception):
    """Raised by the stub below so a test can prove where seeding got to."""


@pytest.fixture()
def stop_at_db(monkeypatch):
    """Halt create_tasks at its first DB call, without writing a board row.

    Getting this wrong is not a failing test — it is a test that quietly seeds
    the LIVE board. ``create_tasks`` imports ``get_connection`` INSIDE the
    function, and ``tools.db.storage`` and ``icdev.tools.db.storage`` are
    DISTINCT module objects, so every alias has to be patched.
    """
    def _stop(*a, **kw):
        raise _ReachedTheDatabase()

    import tools.db.storage as storage_alias

    for name in ("tools.db.storage", "icdev.tools.db.storage"):
        module = sys.modules.get(name)
        if module is not None:
            monkeypatch.setattr(module, "get_connection", _stop, raising=False)
    monkeypatch.setattr(storage_alias, "get_connection", _stop, raising=False)

    # Silence the OTHER pre-insert checks so a failure here is about this one.
    from tools.kanban import landed_check as lc
    monkeypatch.setattr(lc, "mode", lambda: "off")


@pytest.fixture()
def seed_warnings(monkeypatch):
    """Capture task_factory's warnings.

    Not ``caplog``: these go through ``tools.logging.icdev_logger``, whose
    handlers do not propagate to the root logger, so caplog sees nothing and the
    assertion passes or fails for the wrong reason.
    """
    captured: list = []

    class _Recorder:
        def warning(self, msg, *args):
            captured.append(msg % args if args else msg)

        def __getattr__(self, _name):
            return lambda *a, **kw: None

    monkeypatch.setattr(task_factory, "logger", _Recorder())
    return captured


def test_seeding_an_unclaimed_id_is_reported_and_still_seeds(monkeypatch,
                                                             seed_warnings, stop_at_db):
    monkeypatch.setattr(ti, "load_cards", lambda path=None: CARDS)

    # _ReachedTheDatabase, NOT a ValueError: report mode must not refuse.
    with pytest.raises(_ReachedTheDatabase):
        task_factory.create_tasks([{"id": "hcx-live-01", "title": "x",
                                    "task_type": "build"}])
    assert any("unclaimed task id" in m for m in seed_warnings), "reported nothing"
    assert any("hcx-live-01" in m for m in seed_warnings), "name the id"


def test_the_check_runs_before_any_insert(monkeypatch, seed_warnings):
    """Evaluated ahead of the DB, so the eventual refusal cannot half-land a batch."""
    monkeypatch.setattr(ti, "load_cards", lambda path=None: CARDS)

    def _never(*a, **kw):
        raise AssertionError("reached the database before checking identity")

    import tools.db.storage as storage_alias
    for name in ("tools.db.storage", "icdev.tools.db.storage"):
        module = sys.modules.get(name)
        if module is not None:
            monkeypatch.setattr(module, "get_connection", _never, raising=False)
    monkeypatch.setattr(storage_alias, "get_connection", _never, raising=False)
    from tools.kanban import landed_check as lc
    monkeypatch.setattr(lc, "mode", lambda: "off")

    with pytest.raises(AssertionError):
        task_factory.create_tasks([{"id": "hcx-live-01", "title": "x"}])
    assert any("unclaimed task id" in m for m in seed_warnings), \
        "the warning must be emitted before the insert path, not after it"


def test_a_claimed_batch_is_not_reported(monkeypatch, seed_warnings, stop_at_db):
    monkeypatch.setattr(ti, "load_cards", lambda path=None: CARDS)
    with pytest.raises(_ReachedTheDatabase):
        task_factory.create_tasks([{"id": "rem-hyg-02", "title": "x"},
                                   {"id": "aadc-gate-00", "title": "MANUAL-MODE GATE",
                                    "description": "RISK: not agent work"}])
    assert not any("unclaimed task id" in m for m in seed_warnings)


def test_a_broken_validator_leaves_seeding_exactly_as_it_was(monkeypatch, stop_at_db):
    def _boom(*a, **kw):
        raise RuntimeError("projects.yaml is a directory today")

    monkeypatch.setattr(ti, "check_batch", _boom)
    # Must get PAST the identity check to the DB, not be stopped by it.
    with pytest.raises(_ReachedTheDatabase):
        task_factory.create_tasks([{"id": "hcx-live-01", "title": "x"}])
