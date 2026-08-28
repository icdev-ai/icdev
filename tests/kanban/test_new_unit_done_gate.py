# CUI // SP-CTI
"""The done gate asks whether what landed is WIRED (wire-run-01).

`_refuses_done` asks whether the work LANDED. A task can pass that and still declare a
capability nothing has ever run -- which is the "100% done, nothing consumes it" defect this
programme exists for. This is a second, independent rung.

It ships `report`, matching wire-req-01's posture and for the same reason: nothing has measured
how often it fires, and CLAUDE.md treats a check refusing routine work as grounds to stand it
down.
"""
from __future__ import annotations

import inspect

import pytest

from tools.kanban import cli


@pytest.fixture
def finding(monkeypatch):
    """A measured report naming one never-run unit, with a known diff range."""
    from tools.awareness import capability_consumption as cc

    monkeypatch.setattr(cli, "_task_diff_range", lambda tid: ("abc123", "kanban/x-y-01"))
    monkeypatch.setattr(
        cc,
        "new_units",
        lambda since, **kw: {
            "state": "measured",
            "since": since,
            "head": kw.get("head", "HEAD"),
            "findings": [
                {
                    "capability_class": "reflex",
                    "unit": "brand_new_reflex",
                    "declared_in": ["tools/genesis/daemon.py"],
                    "remedy": "run it once",
                }
            ],
            "classes_scanned": ["reflex"],
            "classes_undiffable": [],
        },
    )


# ---------------------------------------------------------------------------
# Posture
# ---------------------------------------------------------------------------


def test_the_gate_ships_in_report_mode(monkeypatch):
    monkeypatch.delenv(cli.NEW_UNIT_GATE_ENV, raising=False)
    assert cli.NEW_UNIT_GATE_DEFAULT == "report"
    assert cli._new_unit_gate_mode() == "report"


def test_an_unknown_mode_falls_back_to_the_default_never_to_enforce(monkeypatch):
    monkeypatch.setenv(cli.NEW_UNIT_GATE_ENV, "yes-please")
    assert cli._new_unit_gate_mode() == cli.NEW_UNIT_GATE_DEFAULT


@pytest.mark.parametrize("mode", ["off", "report", "enforce"])
def test_every_documented_mode_is_honoured(monkeypatch, mode):
    monkeypatch.setenv(cli.NEW_UNIT_GATE_ENV, mode)
    assert cli._new_unit_gate_mode() == mode


# ---------------------------------------------------------------------------
# What each mode does with the SAME finding
# ---------------------------------------------------------------------------


def test_report_mode_warns_and_never_refuses(monkeypatch, finding, capsys):
    monkeypatch.setenv(cli.NEW_UNIT_GATE_ENV, "report")

    assert cli._unwired_units("x-y-01") == ""
    err = capsys.readouterr().err
    assert "brand_new_reflex" in err
    assert "WARNING" in err


def test_enforce_mode_refuses_and_names_the_unit(monkeypatch, finding):
    monkeypatch.setenv(cli.NEW_UNIT_GATE_ENV, "enforce")

    refusal = cli._unwired_units("x-y-01")

    assert refusal
    assert "brand_new_reflex" in refusal
    assert "--new-units" in refusal, "a refusal must carry the command that re-derives it"


def test_off_mode_does_not_even_look(monkeypatch):
    """Proved by making the measurement explode: `off` must not reach it."""
    from tools.awareness import capability_consumption as cc

    def _boom(*a, **k):
        raise AssertionError("off mode consulted the measurement")

    monkeypatch.setenv(cli.NEW_UNIT_GATE_ENV, "off")
    monkeypatch.setattr(cc, "new_units", _boom)
    monkeypatch.setattr(cli, "_task_diff_range", _boom)

    assert cli._unwired_units("x-y-01") == ""


# ---------------------------------------------------------------------------
# Fail-open -- and never silently
# ---------------------------------------------------------------------------


def test_an_undeterminable_range_is_fail_open_and_says_so(monkeypatch, capsys):
    monkeypatch.setenv(cli.NEW_UNIT_GATE_ENV, "enforce")
    monkeypatch.setattr(cli, "_task_diff_range", lambda tid: None)

    assert cli._unwired_units("x-y-01") == ""
    assert "not a clean bill" in capsys.readouterr().err


def test_an_unmeasurable_report_is_fail_open_and_says_so(monkeypatch, capsys):
    from tools.awareness import capability_consumption as cc

    monkeypatch.setenv(cli.NEW_UNIT_GATE_ENV, "enforce")
    monkeypatch.setattr(cli, "_task_diff_range", lambda tid: ("a", "b"))
    monkeypatch.setattr(
        cc, "new_units",
        lambda since, **kw: {"state": "unmeasurable", "reason": "no telemetry", "findings": []},
    )

    assert cli._unwired_units("x-y-01") == ""
    err = capsys.readouterr().err
    assert "UNMEASURABLE" in err
    assert "not a clean bill" in err


def test_a_raising_measurement_never_wedges_a_completion(monkeypatch, capsys):
    from tools.awareness import capability_consumption as cc

    monkeypatch.setenv(cli.NEW_UNIT_GATE_ENV, "enforce")
    monkeypatch.setattr(cli, "_task_diff_range", lambda tid: ("a", "b"))
    monkeypatch.setattr(
        cc, "new_units", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db down"))
    )

    assert cli._unwired_units("x-y-01") == ""
    assert "could not run" in capsys.readouterr().err


def test_no_finding_is_silent(monkeypatch, capsys):
    from tools.awareness import capability_consumption as cc

    monkeypatch.setenv(cli.NEW_UNIT_GATE_ENV, "enforce")
    monkeypatch.setattr(cli, "_task_diff_range", lambda tid: ("a", "b"))
    monkeypatch.setattr(
        cc, "new_units",
        lambda since, **kw: {"state": "measured", "findings": [], "classes_scanned": ["reflex"]},
    )

    assert cli._unwired_units("x-y-01") == ""
    assert capsys.readouterr().err == ""


# ---------------------------------------------------------------------------
# It is actually consulted -- the defect this whole programme is about
# ---------------------------------------------------------------------------


def test_the_done_path_consults_it():
    """A rung nothing calls is the declared-but-unconsumed defect, in the check built to
    catch it."""
    src = inspect.getsource(cli.cmd_set_status)
    assert "_unwired_units(" in src


def test_it_is_a_separate_rung_from_the_merge_check():
    """Folding it into `_refuses_done` would make one env switch govern two different
    questions -- landed, and wired."""
    assert "_unwired_units" not in inspect.getsource(cli._refuses_done)


def test_the_range_is_not_origin_main_to_head():
    """By done-time the work has usually merged, so `origin/main...HEAD` is empty and every
    task reads clean -- a V&V check dispatched after its subject landed, which can never go
    red."""
    src = inspect.getsource(cli._task_diff_range)
    assert "merge-base" in src
    assert "--grep=" in src, "a task with no branch left must still be locatable by its id"


def test_report_mode_is_silent_about_non_measurement(monkeypatch, capsys):
    """A FINDING speaks in both modes; NON-MEASUREMENT speaks only under `enforce`.

    Under `report` this rung refuses nothing by construction, so a per-task note on every
    completion is noise that teaches people to ignore stderr -- which is how the real finding
    gets missed later. It also named every task in a batch, breaking
    `test_cli_refusal_is_all_or_nothing`, whose assertion that only the failing task is named is
    a genuine invariant about batch refusals.
    """
    monkeypatch.setenv(cli.NEW_UNIT_GATE_ENV, "report")
    monkeypatch.setattr(cli, "_task_diff_range", lambda tid: None)

    assert cli._unwired_units("x-y-01") == ""
    assert capsys.readouterr().err == ""
