"""End-to-end proof that the transaction-leak guard fails the right test.

Runs pytest in a subprocess over a generated probe module. The probe has to
live in tests/ so the real conftest applies to it; it is named so the normal
suite never collects it (pytest still collects a file passed explicitly) and it
is removed in a finally block.
"""
import subprocess
import sys
import textwrap
from pathlib import Path

PROBE = Path(__file__).parent / "_txn_leak_probe_generated.py"

PROBE_SOURCE = '''
import sqlite3


def test_leaks_a_write_transaction(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "probe.db"))
    conn.execute("CREATE TABLE t (id INTEGER)")
    conn.commit()
    conn.execute("INSERT INTO t (id) VALUES (1)")  # never committed


def test_runs_after_the_leak(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "after.db"))
    conn.execute("CREATE TABLE t (id INTEGER)")
    conn.execute("INSERT INTO t (id) VALUES (1)")
    conn.commit()
    conn.close()
'''


def _run_probe(*extra_args, env=None):
    PROBE.write_text(textwrap.dedent(PROBE_SOURCE), encoding="utf-8")
    try:
        return subprocess.run(
            # --color=no: the child inherits this run's environment, so pytest
            # can decide to emit ANSI codes into a captured pipe. That splits
            # "<nodeid> PASSED" with an escape sequence and every assertion
            # here that matches on the verbose output fails for no real reason.
            [
                sys.executable, "-m", "pytest", str(PROBE),
                "-p", "no:cacheprovider", "-v", "--color=no",
                *extra_args,
            ],
            capture_output=True,
            text=True,
            cwd=str(Path(__file__).parent.parent),
            env=env,
            timeout=120,
        )
    finally:
        PROBE.unlink(missing_ok=True)


def test_leaking_test_fails_and_is_named():
    result = _run_probe()

    assert "Transaction leak" in result.stdout
    # The failure is attributed to the test that leaked, not a later victim.
    assert "test_leaks_a_write_transaction" in result.stdout
    assert result.returncode != 0


def test_leak_does_not_cascade_into_the_next_test():
    result = _run_probe()

    # The guard rolls the leak back, so the following test still passes.
    assert "test_runs_after_the_leak PASSED" in result.stdout


def test_failure_reports_where_the_connection_was_opened():
    """The report has to carry the opening stack, not just the finishing test.

    Regression guard: the origin registry is cleared during the same teardown
    that builds the message, so reading it in the wrong order degrades every
    entry to "(origin not recorded)" while the guard still looks like it works.
    """
    result = _run_probe()

    assert "Opened at:" in result.stdout
    assert "not attributable" not in result.stdout
    # The frame naming the probe's sqlite3.connect call, not just the nodeid.
    assert "_txn_leak_probe_generated.py" in result.stdout
    assert "opened on thread 'MainThread'" in result.stdout


def test_guard_can_be_disabled_by_env(monkeypatch):
    import os

    env = dict(os.environ, ICDEV_TXN_LEAK_GUARD="0")
    result = _run_probe(env=env)

    assert "Transaction leak" not in result.stdout
    assert result.returncode == 0
