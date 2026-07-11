# CUI // SP-CTI
"""CLI delivery-pipeline view (`python -m tools.kanban.cli --pipeline <id>`)."""
import importlib

from tools.kanban import cli


def test_ascii_folds_arrows_and_dashes():
    # Windows cp1252 consoles can't encode → — … ✓; the CLI must ASCII-fold.
    out = cli._ascii("Merged → main — done … ✓")
    assert "→" not in out and "—" not in out and "…" not in out and "✓" not in out
    assert "->" in out
    out.encode("ascii")  # must not raise


def test_cmd_pipeline_renders_stages(monkeypatch, capsys):
    fake = {
        "task_id": "t1", "current_stage": "code_quality",
        "stages": [
            {"key": "implement", "label": "Implement", "state": "completed", "detail": ""},
            {"key": "code_quality", "label": "Code Quality", "state": "current", "detail": "ruff:0"},
            {"key": "merged", "label": "Merged → main", "state": "pending", "detail": ""},
        ],
        "meta": {"branch_name": "kanban/t1", "commit_subject": "feat: x — y"},
    }
    storage = importlib.import_module("tools.kanban.pipeline")
    monkeypatch.setattr(storage, "assemble", lambda tid: fake)
    rc = cli.cmd_pipeline("t1", json_out=False)
    out = capsys.readouterr().out
    assert rc == 0
    assert "[x] Implement" in out and "[>] Code Quality" in out
    assert "Merged -> main" in out  # arrow folded to ASCII
    assert "→" not in out


def test_cmd_pipeline_not_found_returns_1(monkeypatch, capsys):
    storage = importlib.import_module("tools.kanban.pipeline")
    monkeypatch.setattr(storage, "assemble", lambda tid: {"error": "task_not_found", "task_id": tid})
    rc = cli.cmd_pipeline("nope", json_out=False)
    assert rc == 1


def test_cmd_pipeline_json(monkeypatch, capsys):
    import json as _json
    storage = importlib.import_module("tools.kanban.pipeline")
    monkeypatch.setattr(storage, "assemble", lambda tid: {"task_id": tid, "stages": [], "current_stage": "x"})
    rc = cli.cmd_pipeline("t1", json_out=True)
    out = capsys.readouterr().out
    assert rc == 0
    assert _json.loads(out)["task_id"] == "t1"
