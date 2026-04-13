"""OPT-40 — tests for tools/memory/auto_consolidate.py."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.memory import auto_consolidate as ac  # noqa: E402


def test_skips_when_db_missing(tmp_path):
    missing = tmp_path / "no_such.db"
    with patch.object(ac, "_memory_db_path", return_value=missing):
        result = ac.run(write_audit=False)
    assert result["triggered"] is False
    assert result.get("skipped") == "memory_db_missing"


def test_skips_under_threshold(tmp_path):
    small = tmp_path / "tiny.db"
    small.write_bytes(b"x" * 100)
    with patch.object(ac, "_memory_db_path", return_value=small):
        result = ac.run(threshold_mb=1, write_audit=False)
    assert result["triggered"] is False
    assert "under threshold" in (result.get("skipped") or "")


def test_force_triggers_even_under_threshold(tmp_path):
    small = tmp_path / "tiny.db"
    small.write_bytes(b"x" * 100)
    fake = MagicMock()
    fake.consolidate_all.return_value = {"processed": 0, "actions": {"MERGE": 0}}
    with patch.object(ac, "_memory_db_path", return_value=small), \
         patch("tools.memory.memory_consolidation.MemoryConsolidator", return_value=fake):
        result = ac.run(threshold_mb=1000, force=True, write_audit=False)
    assert result["triggered"] is True
    fake.consolidate_all.assert_called_once()


def test_triggers_when_over_threshold(tmp_path):
    """File size 2 MB with threshold 1 MB should trigger."""
    big = tmp_path / "big.db"
    big.write_bytes(b"x" * (2 * 1024 * 1024))
    fake = MagicMock()
    fake.consolidate_all.return_value = {"processed": 7, "actions": {"MERGE": 3}}
    with patch.object(ac, "_memory_db_path", return_value=big), \
         patch("tools.memory.memory_consolidation.MemoryConsolidator", return_value=fake):
        result = ac.run(threshold_mb=1, batch_size=25, write_audit=False)
    assert result["triggered"] is True
    assert result["summary"]["processed"] == 7
    fake.consolidate_all.assert_called_once_with(batch_size=25)


def test_dry_run_propagates_to_consolidator(tmp_path):
    big = tmp_path / "big.db"
    big.write_bytes(b"x" * (2 * 1024 * 1024))
    captured: dict = {}

    class FakeConsolidator:
        def __init__(self, dry_run: bool = False):
            captured["dry_run"] = dry_run

        def consolidate_all(self, batch_size):
            return {"processed": 0, "actions": {}}

    with patch.object(ac, "_memory_db_path", return_value=big), \
         patch("tools.memory.memory_consolidation.MemoryConsolidator",
               FakeConsolidator):
        ac.run(threshold_mb=1, dry_run=True, write_audit=False)
    assert captured["dry_run"] is True


def test_error_in_consolidator_captured(tmp_path):
    big = tmp_path / "big.db"
    big.write_bytes(b"x" * (2 * 1024 * 1024))
    fake = MagicMock()
    fake.consolidate_all.side_effect = RuntimeError("boom")
    with patch.object(ac, "_memory_db_path", return_value=big), \
         patch("tools.memory.memory_consolidation.MemoryConsolidator", return_value=fake):
        result = ac.run(threshold_mb=1, write_audit=False)
    assert "error" in result
    assert "boom" in result["error"]


def test_result_shape():
    """Critical result-dict keys must always be present."""
    with patch.object(ac, "_memory_db_path", return_value=Path("/nonexistent")):
        result = ac.run(write_audit=False)
    for key in ("timestamp", "db_path", "db_bytes", "threshold_bytes",
                "dry_run", "force", "triggered", "duration_ms"):
        assert key in result, f"missing {key}"


def test_db_size_bytes_handles_missing(tmp_path):
    assert ac._db_size_bytes(tmp_path / "nope") == 0
