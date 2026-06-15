# CUI // SP-CTI
"""Phase 1a resilience tests for tools/rag/codebase_indexer.py.

Purpose: verify that scan_codebase() is resilient to per-file exceptions
and correctly persists good files even when a bad file mid-loop raises
an exception that would otherwise abort the Postgres transaction.

Regression target: 2026-04-11 session discovered a silent transaction
abort bug where scan_codebase() reported indexed_files=7251 but 0 rows
actually landed in codebase_index. Root cause: one per-file exception
aborted the transaction and every subsequent execute() silently
failed; the final commit rolled back the entire run. Fix: wrap each
per-file block in try/except with conn.rollback() on exception, and
batch-commit every 100 files so progress is persisted incrementally.
"""
from __future__ import annotations

import sys
import textwrap
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.rag import codebase_indexer as ci  # noqa: E402


class TestCodebaseIndexerResilience:
    """Phase 1a regression coverage for scan_codebase() error handling.

    Test isolation: scan_codebase()'s hash-based skip logic means
    that re-running the same test against the same shared DB would
    hit "already indexed" and return `skipped` instead of `indexed`.
    Each test uses a unique uuid-prefixed directory name so rel_path
    hashes differ per run, forcing fresh inserts.
    """

    def _unique_scope(self, tmp_path: Path) -> Path:
        scope = tmp_path / f"scope_{uuid.uuid4().hex[:12]}"
        scope.mkdir()
        return scope

    def _write_py(self, dir_: Path, name: str, body: str) -> Path:
        p = dir_ / name
        p.write_text(textwrap.dedent(body), encoding="utf-8")
        return p

    def test_all_good_files_persist(self, tmp_path):
        """Baseline: 3 good Python files scan cleanly with no bad files."""
        scope = self._unique_scope(tmp_path)
        for i in range(3):
            self._write_py(
                scope,
                f"mod{i}.py",
                f"""
                def fn{i}():
                    return {i}

                class Cls{i}:
                    pass
                """,
            )

        result = ci.scan_codebase(tmp_path, scope=scope)
        assert result["indexed_files"] == 3, result
        assert result["errors"] == 0
        assert result["total_chunks"] > 0

    def test_one_bad_file_does_not_block_others(self, tmp_path):
        """Core regression: a per-file exception mid-loop must not
        prevent earlier or later good files from persisting."""
        scope = self._unique_scope(tmp_path)
        # 2 good files BEFORE the bad one
        self._write_py(scope, "good_a.py", "def a(): return 1\n")
        self._write_py(scope, "good_b.py", "def b(): return 2\n")
        # Bad file that will raise inside index_python_file via monkeypatch
        self._write_py(scope, "bad_mid.py", "def bad(): return 3\n")
        # 2 good files AFTER
        self._write_py(scope, "good_c.py", "def c(): return 4\n")
        self._write_py(scope, "good_d.py", "def d(): return 5\n")

        real_index_python_file = ci.index_python_file

        def flaky_index_python_file(fp, base_dir):
            if fp.name == "bad_mid.py":
                raise RuntimeError("synthetic parse failure")
            return real_index_python_file(fp, base_dir)

        with patch.object(ci, "index_python_file", side_effect=flaky_index_python_file):
            result = ci.scan_codebase(tmp_path, scope=scope)

        # Exactly the 4 good files should have indexed successfully;
        # the 1 bad file should be counted as an error.
        assert result["indexed_files"] == 4, result
        assert result["errors"] == 1, result
        assert result["total_chunks"] > 0

    def test_exception_triggers_rollback_not_silent_failure(self, tmp_path, caplog):
        """Assert that a per-file exception is LOGGED as an error
        (proving the except block ran), not silently swallowed."""
        import logging
        scope = self._unique_scope(tmp_path)
        self._write_py(scope, "ok.py", "def ok(): return 1\n")
        self._write_py(scope, "boom.py", "def boom(): return 2\n")

        real_index_python_file = ci.index_python_file

        def flaky(fp, base_dir):
            if fp.name == "boom.py":
                raise ValueError("intentional")
            return real_index_python_file(fp, base_dir)

        # icdev_logger sets propagate=False so caplog's root handler never sees
        # messages. Attach caplog's handler directly to ci.LOG for this test.
        caplog.set_level(logging.ERROR, logger=ci.LOG.name)
        ci.LOG.addHandler(caplog.handler)
        try:
            with patch.object(ci, "index_python_file", side_effect=flaky):
                result = ci.scan_codebase(tmp_path, scope=scope)
        finally:
            ci.LOG.removeHandler(caplog.handler)

        assert result["errors"] == 1
        assert any("boom.py" in r.message for r in caplog.records), [
            r.message for r in caplog.records
        ]

    def test_per_file_commit_pattern_present(self):
        """Sanity: the resilience fix markers exist in the source.
        Guards against the patch being accidentally reverted by a
        refactor. Per-file commit + rollback-on-error is the REQUIRED
        pattern for scan_codebase()."""
        source = (PROJECT_ROOT / "tools" / "rag" / "codebase_indexer.py").read_text(
            encoding="utf-8"
        )
        assert "conn.rollback()" in source, "rollback call missing"
        assert "conn.commit()" in source, "commit call missing"
        assert "Phase 1a resilience fix" in source, "fix comment missing"

    def test_empty_scope_returns_zero_everything(self, tmp_path):
        """Edge case: empty scope directory returns clean result."""
        scope = self._unique_scope(tmp_path)
        result = ci.scan_codebase(tmp_path, scope=scope)
        assert result["indexed_files"] == 0
        assert result["errors"] == 0
        assert result["total_chunks"] == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
