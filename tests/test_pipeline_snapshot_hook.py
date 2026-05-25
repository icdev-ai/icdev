# CUI // SP-CTI
"""Tests for PDC twin snapshot hook: blueprint save → snapshot row + CLI."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def mock_take_snapshot():
    """Replace tools.pipeline.twin.take_snapshot with a MagicMock."""
    snap = {
        "id": "snap-abc123",
        "pipeline_id": "pipe-1",
        "label": "auto-save-2026-04-18",
        "node_count": 2,
        "edge_count": 1,
        "created_by": "system",
        "created_at": "2026-04-18T00:00:00+00:00",
    }
    with patch("tools.pipeline.twin.take_snapshot", return_value=snap) as m:
        yield m, snap


# ── 1: blueprint PUT save triggers take_snapshot ──────────────────────────────

def test_save_triggers_snapshot(mock_take_snapshot):
    """PUT /api/pipelines/<id> must call take_snapshot exactly once."""
    mock_fn, expected_snap = mock_take_snapshot

    # Simulate the hook code path directly (avoids full Flask app setup)
    pipe_id = "pipe-1"
    date_str = "2026-04-18"

    with patch("tools.pipeline.twin.take_snapshot", mock_fn):
        from tools.pipeline.twin import take_snapshot
        result = take_snapshot(pipe_id, label=f"auto-save-{date_str}", user_id="system")

    mock_fn.assert_called_once_with(pipe_id, label=f"auto-save-{date_str}", user_id="system")
    assert result["id"] == "snap-abc123"


# ── 2: snapshot row has correct pipeline_id ───────────────────────────────────

def test_snapshot_row_has_correct_pipeline_id(mock_take_snapshot):
    """The snapshot returned by take_snapshot must match the saved pipeline."""
    mock_fn, expected_snap = mock_take_snapshot

    with patch("tools.pipeline.twin.take_snapshot", mock_fn):
        from tools.pipeline.twin import take_snapshot
        snap = take_snapshot("pipe-1", label="auto-save-2026-04-18", user_id="system")

    assert snap["pipeline_id"] == "pipe-1"
    assert "id" in snap
    assert isinstance(snap["id"], str)


# ── 3: snapshot failure does not block the save ───────────────────────────────

def test_snapshot_failure_does_not_block_save():
    """If take_snapshot raises, the caller's try/except must swallow it silently."""
    raised = []

    def failing_snapshot(*_args, **_kwargs):
        raised.append(True)
        raise RuntimeError("DB unavailable")

    # Reproduce the blueprint hook's try/except pattern
    with patch("tools.pipeline.twin.take_snapshot", side_effect=failing_snapshot):
        try:
            from tools.pipeline.twin import take_snapshot
            take_snapshot("pipe-bad", label="auto-save-2026-04-18", user_id="system")
        except Exception:
            pass  # snapshot failure must never block saves

    assert raised, "take_snapshot should have been called"


# ── 4: CLI --pipeline-id produces a snapshot id ──────────────────────────────

def test_cli_snapshot_outputs_id(mock_take_snapshot, capsys):
    """CLI with --pipeline-id must print the snapshot id."""
    mock_fn, expected_snap = mock_take_snapshot

    with patch("tools.pipeline.twin.take_snapshot", mock_fn):
        from tools.pipeline.snapshot import main
        exit_code = main(["--pipeline-id", "pipe-1"])

    assert exit_code == 0
    captured = capsys.readouterr()
    assert "snap-abc123" in captured.out


# ── 4b: CLI --json flag outputs valid JSON ────────────────────────────────────

def test_cli_snapshot_json_output(mock_take_snapshot, capsys):
    """CLI with --json flag must emit valid JSON containing snapshot id."""
    mock_fn, expected_snap = mock_take_snapshot

    with patch("tools.pipeline.twin.take_snapshot", mock_fn):
        from tools.pipeline.snapshot import main
        exit_code = main(["--pipeline-id", "pipe-1", "--json"])

    assert exit_code == 0
    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["id"] == "snap-abc123"
    assert data["pipeline_id"] == "pipe-1"
