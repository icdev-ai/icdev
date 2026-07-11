# CUI // SP-CTI
"""Pipeline enforcement-mode surfacing (enforced vs record-only)."""
import importlib

import pytest

from tools.kanban import cli, pipeline


def test_enforce_mode_default_is_record_only(monkeypatch):
    monkeypatch.delenv("KANBAN_PIPELINE_ENFORCE", raising=False)
    assert pipeline._enforce_mode() == "record_only"


@pytest.mark.parametrize("val", ["1", "true", "yes", "TRUE", "Yes"])
def test_enforce_mode_enabled(monkeypatch, val):
    monkeypatch.setenv("KANBAN_PIPELINE_ENFORCE", val)
    assert pipeline._enforce_mode() == "enforced"


@pytest.mark.parametrize("val", ["0", "false", "no", "", "off"])
def test_enforce_mode_disabled_values(monkeypatch, val):
    # matches validated_commit._pipeline_enforce parsing (1/true/yes only)
    monkeypatch.setenv("KANBAN_PIPELINE_ENFORCE", val)
    assert pipeline._enforce_mode() == "record_only"


def test_cli_prints_enforced_mode(monkeypatch, capsys):
    storage = importlib.import_module("tools.kanban.pipeline")
    monkeypatch.setattr(storage, "assemble", lambda tid: {
        "task_id": tid, "current_stage": "code_quality", "enforce_mode": "enforced",
        "stages": [{"key": "implement", "label": "Implement", "state": "completed", "detail": ""}],
        "meta": {},
    })
    rc = cli.cmd_pipeline("t1", json_out=False)
    out = capsys.readouterr().out
    assert rc == 0
    assert "mode: ENFORCED" in out


def test_cli_prints_record_only_mode(monkeypatch, capsys):
    storage = importlib.import_module("tools.kanban.pipeline")
    monkeypatch.setattr(storage, "assemble", lambda tid: {
        "task_id": tid, "current_stage": "implement", "enforce_mode": "record_only",
        "stages": [], "meta": {},
    })
    cli.cmd_pipeline("t1", json_out=False)
    out = capsys.readouterr().out
    assert "mode: RECORD-ONLY" in out


def test_cli_missing_enforce_mode_defaults_record_only(monkeypatch, capsys):
    # older/partial payloads without enforce_mode must not crash — default safe
    storage = importlib.import_module("tools.kanban.pipeline")
    monkeypatch.setattr(storage, "assemble", lambda tid: {
        "task_id": tid, "current_stage": "implement", "stages": [], "meta": {},
    })
    cli.cmd_pipeline("t1", json_out=False)
    assert "mode: RECORD-ONLY" in capsys.readouterr().out
