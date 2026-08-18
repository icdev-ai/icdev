#!/usr/bin/env python3
"""Arming the seed-time identity check, and the narrowing. CUI // SP-CTI

rem-hyg-04 turns rem-hyg-02's report into a refusal. Three properties decide
whether that was done correctly, and each of them fails while looking fine:

  THE SWITCH IS READ
      ``KANBAN_IDENTITY_CHECK`` has to reach the seeder. A kill switch nothing
      reads is the ``hook_points:`` block in ``args/extension_config.yaml``:
      present, documented, inert. An UNRECOGNISED value must also resolve to the
      safe default *and say so* — ``KANBAN_IDENTITY_CHECK=enforced`` is one
      keystroke from ``enforce`` and would otherwise leave an operator believing
      the check was armed.

  THE NARROWING IS ONE PREDICATE
      The survey's NARROWED column and the seeder's refusal must describe the
      same population. Two copies of "is this a finding?" is how a measured rate
      and an enforced rate drift apart while both look measured — which is the
      whole reason rem-hyg-03 had to exist.

  THE REFUSAL COMES BEFORE THE INSERT
      A refusal raised part-way through the insert loop leaves half a batch on
      the board. Asserted by proving the ValueError arrives instead of the first
      database call, not merely alongside it.

Deterministic: the registry is injected in every case and no test touches a real
``args/projects.yaml`` or a real board.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.kanban import identity_survey as survey_mod  # noqa: E402
from tools.kanban import landed_check as lc  # noqa: E402
from tools.kanban import task_factory  # noqa: E402
from tools.kanban import task_identity as ti  # noqa: E402


#: A registry with one card owning ``rem-`` and declaring one epic. Small on
#: purpose: every id below resolves to exactly one of the four outcomes.
_CARDS = [
    ti.Card(key="rem", prefix="rem-", epics=("hyg",)),
    ti.Card(key="noepics", prefix="ne-", epics=()),
]

_CLAIMED = "rem-hyg-04"          # an epic claims it
_GATE = "rem-gate-00"            # holds the card, never counted, never an orphan
_NO_EPIC = "rem-ghost-01"        # card owns the prefix, no epic claims it
_NO_CARD_SHAPED = "zzz-live-01"  # a card is genuinely missing
_NO_CARD_OPAQUE = "task-fd99a9c8ae"  # the dashboard's own create-task API


@pytest.fixture(autouse=True)
def _fixed_registry(monkeypatch):
    """Never read the real args/projects.yaml — its contents change weekly."""
    monkeypatch.setattr(ti, "load_cards", lambda path=None: list(_CARDS))


@pytest.fixture(autouse=True)
def _default_posture(monkeypatch):
    """Start every test from an unset switch, whatever the shell had."""
    monkeypatch.delenv(ti.MODE_ENV, raising=False)


# --------------------------------------------------------------------------- #
# the switch
# --------------------------------------------------------------------------- #

def test_the_default_is_report_because_the_survey_said_so():
    """15.81% narrowed over 30d is ten times 'refusing routine work'."""
    assert ti.mode() == "report"
    assert ti.DEFAULT_MODE == "report"


@pytest.mark.parametrize("raw,expected", [
    ("enforce", "enforce"), ("ENFORCE", "enforce"), ("1", "enforce"),
    ("true", "enforce"), ("yes", "enforce"),
    ("report", "report"), ("warn", "report"), ("  report  ", "report"),
    ("off", "off"), ("0", "off"), ("false", "off"), ("none", "off"),
    ("", "report"),
])
def test_the_switch_accepts_the_landed_checks_vocabulary(monkeypatch, raw, expected):
    """Same spellings as KANBAN_LANDED_CHECK, so one habit works for both."""
    monkeypatch.setenv(ti.MODE_ENV, raw)
    assert ti.mode() == expected


def test_an_unrecognised_value_falls_back_to_report_and_says_so(monkeypatch):
    """A typo must not silently read as armed."""
    said = []
    monkeypatch.setattr(ti.logger, "warning",
                        lambda msg, *a: said.append(msg % a if a else msg))
    monkeypatch.setenv(ti.MODE_ENV, "enforced")

    assert ti.mode() == "report", "a typo must fail SAFE, not to enforce"
    assert said, "falling back silently is how an operator believes it is armed"
    assert "NOT armed" in said[0]
    assert "enforced" in said[0], "name the value that was rejected"


# --------------------------------------------------------------------------- #
# the narrowing
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("task_id,expected", [
    (_NO_CARD_OPAQUE, False),          # the dashboard's create-task API
    ("mc-reflex-0f01f09f", False),     # an autonomous writer's namespace
    ("task-fd99a9c8ae-d5", False),     # a decomposed child of an opaque parent
    (_NO_CARD_SHAPED, True),           # a card is genuinely missing
    ("mvs-audit-03-d1", True),         # a decomposed child of card work
])
def test_only_card_shaped_orphans_are_refusable(task_id, expected):
    assert ti.is_enforceable(ti.REASON_NO_CARD, task_id) is expected


def test_no_epic_is_refusable_whatever_the_id_looks_like():
    """A registered card owns the prefix, so somebody already calls this work."""
    assert ti.is_enforceable(ti.REASON_NO_EPIC, "rem-ghost") is True
    assert ti.is_enforceable(ti.REASON_NO_EPIC, _NO_EPIC) is True


@pytest.mark.parametrize("reason", [
    ti.REASON_CLAIMED, ti.REASON_GATE, ti.REASON_NOT_ID_SHAPED,
    ti.REASON_NO_REGISTRY,
])
def test_a_non_actionable_reason_is_never_refusable(reason):
    """An unreadable registry and a gate sentinel are not findings at all."""
    assert ti.is_enforceable(reason, _NO_CARD_SHAPED) is False


def test_the_survey_and_the_seeder_narrow_identically():
    """The property rem-hyg-03 exists to guarantee: one predicate, two readers.

    If these ever diverge, the surveyed fire rate stops describing the enforced
    one and the evidence behind the default becomes fiction.
    """
    ids = [_CLAIMED, _GATE, _NO_EPIC, _NO_CARD_SHAPED, _NO_CARD_OPAQUE,
           "another-orphan-02", "mc-reflex-0f01f09f"]
    rows = [(i, "backlog", None) for i in ids]

    report = survey_mod.survey(rows, cards=_CARDS)
    refused = {f["task_id"] for f in ti.check_batch(ids, cards=_CARDS)
               if f["enforced"]}

    assert report["totals"]["actionable_narrowed"] == len(refused)
    assert refused == {_NO_EPIC, _NO_CARD_SHAPED, "another-orphan-02"}


def test_findings_carry_the_verdict_and_the_reported_ones_say_they_are_exempt():
    findings = {f["task_id"]: f for f in
                ti.check_batch([_NO_CARD_OPAQUE, _NO_CARD_SHAPED, _NO_EPIC],
                               cards=_CARDS)}

    assert set(findings) == {_NO_CARD_OPAQUE, _NO_CARD_SHAPED, _NO_EPIC}, \
        "an exempt id is still REPORTED — the row really is counted by nothing"
    assert findings[_NO_CARD_OPAQUE]["enforced"] is False
    assert findings[_NO_CARD_OPAQUE]["shape"] == ti.SHAPE_OPAQUE
    assert "REPORT ONLY" in findings[_NO_CARD_OPAQUE]["detail"], \
        "a finding that will never be refused must say so where it is read"
    assert findings[_NO_CARD_SHAPED]["enforced"] is True
    assert findings[_NO_EPIC]["enforced"] is True
    assert findings[_NO_EPIC]["shape"] is None, "shape only sorts no_card rows"


# --------------------------------------------------------------------------- #
# the seeder
# --------------------------------------------------------------------------- #

class _ReachedTheDatabase(Exception):
    """Raised by the stub below so a test can prove where seeding got to."""


@pytest.fixture()
def stop_at_db(monkeypatch):
    """Halt create_tasks at its first DB call, without writing a board row.

    Getting this wrong is not a failing test — it is a test that quietly seeds
    the LIVE board. ``import tools.db.storage`` does NOT bind the same object
    that ``from tools.db.storage import get_connection`` reads from, because the
    ``tools`` shim resolves the former to ``icdev.tools.db.storage`` while
    ``sys.modules['tools.db.storage']`` stays a distinct module object. Patch
    every alias that exists — same fixture as test_landed_check_wiring.py.
    """
    def _stop(*a, **kw):
        raise _ReachedTheDatabase()

    import tools.db.storage as storage_alias

    for name in ("tools.db.storage", "icdev.tools.db.storage"):
        module = sys.modules.get(name)
        if module is not None:
            monkeypatch.setattr(module, "get_connection", _stop, raising=False)
    monkeypatch.setattr(storage_alias, "get_connection", _stop, raising=False)
    # Isolate the identity check from its two neighbours in create_tasks.
    monkeypatch.setattr(task_factory, "_sentinel_shaped_work", lambda specs: [])
    monkeypatch.setattr(lc, "mode", lambda: "off")


@pytest.fixture()
def seed_warnings(monkeypatch):
    """Capture task_factory's warnings.

    Not ``caplog``: these go through ``tools.logging.icdev_logger``, whose
    handlers do not propagate to the root logger, so caplog sees an empty record
    list and the assertion passes or fails for the wrong reason.
    """
    captured: list[str] = []

    class _Recorder:
        def warning(self, msg, *args):
            captured.append(msg % args if args else msg)

        def __getattr__(self, _name):
            return lambda *a, **kw: None

    monkeypatch.setattr(task_factory, "logger", _Recorder())
    return captured


def test_enforce_refuses_before_any_insert(monkeypatch, stop_at_db):
    monkeypatch.setenv(ti.MODE_ENV, "enforce")

    # A ValueError, NOT _ReachedTheDatabase: the refusal has to come first, or a
    # batch half-lands and the board is left inconsistent.
    with pytest.raises(ValueError) as excinfo:
        task_factory.create_tasks([{"id": _NO_CARD_SHAPED, "title": "x"}])

    message = str(excinfo.value)
    assert "refusing to seed" in message
    assert _NO_CARD_SHAPED in message, "name the id"
    assert "zzz-<epic>-01" in message, \
        "end in an edit rather than a puzzle — follow _work_id_suggestion"
    assert "args/projects.yaml" in message, "name where the fix goes"
    assert ti.MODE_ENV in message, "name the way out"


def test_enforce_names_every_offending_id_not_just_the_first(monkeypatch, stop_at_db):
    monkeypatch.setenv(ti.MODE_ENV, "enforce")
    with pytest.raises(ValueError) as excinfo:
        task_factory.create_tasks([
            {"id": _NO_CARD_SHAPED, "title": "a"},
            {"id": _NO_EPIC, "title": "b"},
            {"id": _CLAIMED, "title": "c"},
        ])
    message = str(excinfo.value)
    assert _NO_CARD_SHAPED in message and _NO_EPIC in message
    assert _CLAIMED not in message, "a claimed id is not a finding"
    assert "2 task id(s)" in message


def test_enforce_does_not_refuse_an_opaque_machine_id(monkeypatch, stop_at_db,
                                                      seed_warnings):
    """The narrowing, at the seam that matters.

    ``task-<hex>`` is what the dashboard's create-task API generates. Refusing it
    would block routine work on every seeding path — the exact defect the
    PreToolUse survey found in eight of twelve checks.
    """
    monkeypatch.setenv(ti.MODE_ENV, "enforce")
    with pytest.raises(_ReachedTheDatabase):
        task_factory.create_tasks([{"id": _NO_CARD_OPAQUE, "title": "x"}])
    assert any(_NO_CARD_OPAQUE in m for m in seed_warnings), \
        "exempt from the refusal, never exempt from the report"


def test_report_logs_the_finding_and_still_seeds(monkeypatch, stop_at_db,
                                                 seed_warnings):
    monkeypatch.setenv(ti.MODE_ENV, "report")
    with pytest.raises(_ReachedTheDatabase):
        task_factory.create_tasks([{"id": _NO_CARD_SHAPED, "title": "x"}])
    assert any("unclaimed task id" in m and _NO_CARD_SHAPED in m
               for m in seed_warnings), "report mode reported nothing"


def test_off_does_not_even_ask(monkeypatch, stop_at_db, seed_warnings):
    """`off` must skip the work, not merely discard the answer."""
    asked = []
    monkeypatch.setattr(ti, "check_batch",
                        lambda specs, cards=None: asked.append(specs) or [])
    monkeypatch.setenv(ti.MODE_ENV, "off")

    with pytest.raises(_ReachedTheDatabase):
        task_factory.create_tasks([{"id": _NO_CARD_SHAPED, "title": "x"}])
    assert asked == [], "off still consulted the registry"
    assert not seed_warnings


def test_a_claimed_batch_is_never_refused(monkeypatch, stop_at_db, seed_warnings):
    monkeypatch.setenv(ti.MODE_ENV, "enforce")
    with pytest.raises(_ReachedTheDatabase):
        task_factory.create_tasks([{"id": _CLAIMED, "title": "a"},
                                   {"id": _GATE, "title": "b"}])
    assert not seed_warnings, "a gate sentinel is not an orphan"


def test_an_unreadable_registry_never_refuses(monkeypatch, stop_at_db):
    """FAIL-OPEN. Every id would look unowned, which is 3,000 fabrications."""
    monkeypatch.setattr(ti, "load_cards", lambda path=None: [])
    monkeypatch.setenv(ti.MODE_ENV, "enforce")
    with pytest.raises(_ReachedTheDatabase):
        task_factory.create_tasks([{"id": _NO_CARD_SHAPED, "title": "x"}])


def test_a_broken_identity_check_never_breaks_seeding(monkeypatch, stop_at_db):
    def _boom(specs, cards=None):
        raise RuntimeError("yaml exploded")

    monkeypatch.setattr(ti, "check_batch", _boom)
    monkeypatch.setenv(ti.MODE_ENV, "enforce")
    # Must get PAST the identity check to the DB, not be refused by it.
    with pytest.raises(_ReachedTheDatabase):
        task_factory.create_tasks([{"id": _NO_CARD_SHAPED, "title": "x"}])
