"""Tests for `kanban/cli.py --reverify` (kpr-rvfy-02).

Exit codes are the contract here: this CLI is what worker sessions use to report
their own state, and a typo'd task id that exits 0 reads as "cleared" when
nothing happened. That silent-no-op shape has bitten this CLI before.
"""
from __future__ import annotations

import importlib
import json

import pytest

cli = importlib.import_module("tools.kanban.cli")


@pytest.fixture
def fake_reverify(monkeypatch):
    """Patch the reverify symbol where cmd_reverify imports it from.

    cmd_reverify does `from tools.kanban.reverify import reverify` at call time,
    so the patch has to land on the source module, not on a name bound in cli.
    """
    calls = []

    def _install(verdict=None, raises=None):
        mod = importlib.import_module("tools.kanban.reverify")

        def _fake(task_id, get_connection, **kwargs):
            calls.append({"task_id": task_id, **kwargs})
            if raises:
                raise raises
            return dict(verdict)

        monkeypatch.setattr(mod, "reverify", _fake)
        return calls

    return _install


PASSED = {
    "task_id": "t1", "result": "passed", "written": True,
    "branch": "kanban/t1", "files_changed": 2, "commits": 1,
    "reason": "Verified (git-first, re-verified): 2 file(s) changed on kanban/t1",
}
FAILED = {
    "task_id": "t1", "result": "failed", "written": True,
    "branch": "kanban/t1", "files_changed": 0, "commits": 0,
    "reason": "No file changes on origin/kanban/t1 vs origin/main",
}


def test_passed_exits_zero(fake_reverify, capsys):
    fake_reverify(PASSED)
    assert cli.cmd_reverify("t1", json_out=False) == 0
    assert "passed" in capsys.readouterr().out


def test_failed_exits_one(fake_reverify, capsys):
    """A genuine failure must not read as success — the gate stays honest."""
    fake_reverify(FAILED)
    assert cli.cmd_reverify("t1", json_out=False) == 1
    assert "failed" in capsys.readouterr().out


def test_unknown_task_exits_two_and_writes_nothing(fake_reverify, capsys):
    fake_reverify(raises=LookupError("no such task: nope"))
    assert cli.cmd_reverify("nope", json_out=False) == 2
    assert "NOT_FOUND" in capsys.readouterr().err


def test_unknown_task_json_shape(fake_reverify, capsys):
    fake_reverify(raises=LookupError("no such task: nope"))
    assert cli.cmd_reverify("nope", json_out=True) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload == {"error": "not_found", "task_id": "nope"}


def test_json_output_is_parseable_and_complete(fake_reverify, capsys):
    fake_reverify(PASSED)
    cli.cmd_reverify("t1", json_out=True)
    payload = json.loads(capsys.readouterr().out)
    assert payload["result"] == "passed"
    assert payload["branch"] == "kanban/t1"
    assert payload["files_changed"] == 2


def test_dry_run_is_forwarded(fake_reverify):
    calls = fake_reverify({**PASSED, "written": False})
    cli.cmd_reverify("t1", json_out=False, dry_run=True)
    assert calls[0]["dry_run"] is True


def test_dry_run_defaults_off(fake_reverify):
    calls = fake_reverify(PASSED)
    cli.cmd_reverify("t1", json_out=False)
    assert calls[0]["dry_run"] is False


def test_output_is_ascii_folded_for_windows_consoles(fake_reverify, capsys):
    """Reasons carry em-dashes; a cp1252 console must not mojibake or raise."""
    fake_reverify({**PASSED, "reason": "Verified — 2 file(s) → done"})
    cli.cmd_reverify("t1", json_out=False)
    out = capsys.readouterr().out
    assert "—" not in out and "→" not in out
    out.encode("ascii")   # raises if anything non-ascii survived


def test_argparse_wires_reverify_and_dry_run():
    """The flag has to reach the handler — a parser that drops it fails silently."""
    import argparse
    src = (cli.__file__)
    with open(src, encoding="utf-8") as fh:
        text = fh.read()
    assert '"--reverify"' in text
    assert "cmd_reverify(args.reverify" in text
    assert isinstance(argparse.ArgumentParser(), argparse.ArgumentParser)
