# CUI // SP-CTI
"""Unit tests for the swallowed-INSERT CLI gate (swp-swallow-01-d1).

These pin the CLI contract only — argument handling, ``file:line`` rendering,
and exit codes. The AST detection rules themselves belong to
``tools/refactor/swallowed_persistence.py`` and are covered by
``tests/test_coherence_swallowed_persistence.py``; duplicating them here would
recreate the drift this wrapper exists to avoid.

Both directions are pinned: the gate must exit 1 on a synthetic violation, and
must exit 0 on best-effort code that logs.
"""

import sys
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.dev.check_swallowed_inserts import (  # noqa: E402
    EXIT_CLEAN,
    EXIT_USAGE,
    EXIT_VIOLATIONS,
    main,
)

SWALLOWED = """
    def record(conn, name):
        try:
            conn.execute("INSERT INTO zz_fixture_events (name) VALUES (?)", (name,))
            conn.commit()
        except Exception:
            pass
    """

LOGGED = """
    import logging

    logger = logging.getLogger(__name__)


    def record(conn, name):
        try:
            conn.execute("INSERT INTO zz_fixture_events (name) VALUES (?)", (name,))
            conn.commit()
        except Exception as exc:
            logger.warning("record: best-effort INSERT failed (non-blocking): %s", exc)
    """


def _write(tmp_path: Path, body: str, name: str = "mod.py") -> Path:
    """Drop a module into a fake ``tools/`` tree so the scan reaches it."""
    pkg = tmp_path / "tools" / "sample"
    pkg.mkdir(parents=True, exist_ok=True)
    target = pkg / name
    target.write_text(textwrap.dedent(body), encoding="utf-8")
    return target


def _line_of(path: Path, needle: str) -> int:
    """1-based line number of the first line containing ``needle``."""
    lines = path.read_text(encoding="utf-8").splitlines()
    for index, line in enumerate(lines, start=1):
        if needle in line:
            return index
    raise AssertionError(f"{needle!r} not found in {path}")


def _run(tmp_path: Path, *extra: str) -> int:
    return main(["--root", str(tmp_path), "--path", "tools", *extra])


def test_catches_synthetic_violation(tmp_path, capsys):
    """A swallowed INSERT exits 1 and is reported at the `except` line."""
    target = _write(tmp_path, SWALLOWED)
    expected_line = _line_of(target, "except Exception:")

    exit_code = _run(tmp_path)

    assert exit_code == EXIT_VIOLATIONS
    stdout = capsys.readouterr().out
    assert f"tools/sample/mod.py:{expected_line}:" in stdout
    assert "zz_fixture_events" in stdout


def test_passes_on_clean_code(tmp_path, capsys):
    """Best-effort persistence that logs is legal — exit 0, nothing reported."""
    _write(tmp_path, LOGGED)

    exit_code = _run(tmp_path)

    assert exit_code == EXIT_CLEAN
    assert "clean:" in capsys.readouterr().out


def test_missing_path_is_a_usage_error(tmp_path, capsys):
    """A path that does not exist must not be silently scanned as empty."""
    exit_code = main(["--root", str(tmp_path), "--path", "does/not/exist"])

    assert exit_code == EXIT_USAGE
    assert "no such path" in capsys.readouterr().err
