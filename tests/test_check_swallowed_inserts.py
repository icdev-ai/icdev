# [TEMPLATE: CUI // SP-CTI]
"""Unit tests for ``tools/dev/check_swallowed_inserts.py``.

NOTE ON LOCATION: the card asked for ``tools/dev/tests/``. No test can live
under ``tools/``: ``tools/__init__.py`` installs a module-redirect shim whose
``__getattr__`` re-raises ``ModuleNotFoundError`` instead of ``AttributeError``
(PEP 562 requires the latter), so pytest's probe for ``setUpModule`` on each
parent package aborts collection before a single case runs. ``testpaths =
["tests"]`` also means only this directory is collected by a bare ``pytest``.
Both point the same way, so the test lives here and actually executes.

Recurrence protection for the pattern itself does not depend on this file: the
CI-allowlisted ``tests/test_coherence_swallowed_persistence.py`` already fails
the build if a swallowed INSERT reappears. These cases cover the CLI wrapper.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.dev import check_swallowed_inserts as checker  # noqa: E402

VIOLATION_SRC = '''
def persist(conn):
    try:
        conn.execute("INSERT INTO audit_trail (a) VALUES (1)")
    except Exception:
        pass
'''

CLEAN_SRC = '''
import logging

logger = logging.getLogger(__name__)


def persist(conn):
    try:
        conn.execute("INSERT INTO audit_trail (a) VALUES (1)")
    except Exception as exc:
        logger.warning("best-effort INSERT failed (non-blocking): %s", exc)
'''

NARROW_SRC = '''
import sqlite3


def persist(conn):
    try:
        conn.execute("INSERT INTO audit_trail (a) VALUES (1)")
    except sqlite3.IntegrityError:
        pass
'''


def _write(tmp_path: Path, name: str, src: str) -> Path:
    target = tmp_path / name
    target.write_text(src, encoding="utf-8")
    return target


def test_catches_synthetic_violation(tmp_path):
    """A bare `except Exception: pass` over an INSERT is reported with file:line."""
    _write(tmp_path, "bad.py", VIOLATION_SRC)

    findings = checker.scan([tmp_path])

    assert len(findings) == 1
    assert findings[0]["table"] == "audit_trail"
    assert findings[0]["function"] == "persist"
    assert findings[0]["line"] == 5

    rendered = checker.format_text(findings, [tmp_path])
    assert "bad.py" in rendered
    assert ":5:" in rendered
    assert "FAIL" in rendered


def test_passes_on_clean_code(tmp_path):
    """A handler that logs is not a violation."""
    _write(tmp_path, "good.py", CLEAN_SRC)

    findings = checker.scan([tmp_path])

    assert findings == []
    assert "OK" in checker.format_text(findings, [tmp_path])


def test_narrow_handler_is_not_flagged(tmp_path):
    """The author named the failure they expected — that is not silent."""
    _write(tmp_path, "narrow.py", NARROW_SRC)

    assert checker.scan([tmp_path]) == []


def test_exit_code_1_on_violation(tmp_path, capsys):
    _write(tmp_path, "bad.py", VIOLATION_SRC)

    code = checker.main(["--path", str(tmp_path)])

    assert code == checker.EXIT_VIOLATIONS
    assert "bad.py:5:" in capsys.readouterr().out


def test_exit_code_0_when_clean(tmp_path, capsys):
    _write(tmp_path, "good.py", CLEAN_SRC)

    code = checker.main(["--path", str(tmp_path)])

    assert code == checker.EXIT_CLEAN
    assert "OK" in capsys.readouterr().out


def test_json_output_is_parseable(tmp_path, capsys):
    _write(tmp_path, "bad.py", VIOLATION_SRC)

    code = checker.main(["--path", str(tmp_path), "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert code == checker.EXIT_VIOLATIONS
    assert payload["status"] == "fail"
    assert payload["count"] == 1
    assert payload["violations"][0]["table"] == "audit_trail"


def test_missing_detector_exits_2_not_0(monkeypatch, capsys):
    """A vanished detector must fail loudly, never report a clean tree."""

    def _boom(*_args, **_kwargs):
        raise ImportError("no module named tools.refactor.swallowed_persistence")

    monkeypatch.setattr(checker, "scan", _boom)

    code = checker.main([])

    assert code == checker.EXIT_NO_DETECTOR
    assert "cannot import" in capsys.readouterr().err



def test_a_path_that_does_not_exist_is_a_usage_error(capsys):
    """Scanning nothing must not report clean.

    Ported from the parallel swp-swallow-01-d1 implementation, which had this
    assertion where this suite did not. It was right: `--path does/not/exist`
    printed "OK: no swallowed INSERT sites" and exited 0, so a typo'd subtree
    (`--path tools/gvocon`) reported the subsystem clean. A gate that answers
    "fine" about a target it never looked at is the silent pass it exists to
    prevent.
    """
    code = checker.main(["--path", "does/not/exist"])
    assert code == checker.EXIT_USAGE
    assert code != checker.EXIT_CLEAN
    assert "does not exist" in capsys.readouterr().err


def test_a_missing_path_is_a_usage_error_in_json_too(capsys):
    code = checker.main(["--path", "does/not/exist", "--json"])
    assert code == checker.EXIT_USAGE
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "error"
    assert payload["violations"] == []


# Deliberately absent: a whole-tree scan. `tools/` takes ~90s to walk, which
# exceeds the per-test budget — and on Windows pytest-timeout only has the
# thread method, so it kills the interpreter and blames the wrong file (TSH).
# `tests/test_coherence_swallowed_persistence.py::test_real_tree_is_clean`
# already covers the real tree and is on the CI Test job's allowlist.


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
