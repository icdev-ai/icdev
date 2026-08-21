# CUI // SP-CTI
"""/start must DEFER to the supervisor, not race it (autonomy-id-03).

`tools/genesis/launch.py` -> `launcher.main()` is a supervisor: it holds a pid
lock, starts six services and restarts any that die on a 30s loop. `/start`
steps 8 and 9 `Start-Process` two of those children DIRECTLY, so on a machine
where the supervisor is already up they race it — and lose SILENTLY. Measured
2026-08-20: manual PIDs 30876/15684 dead inside 20s, no traceback, no stderr,
while the supervisor's own children were alive and dispatching. The
`-RedirectStandardOutput` that started them also TRUNCATED the log, so the
healthy pair read as a total failure.

THE TWO INVARIANTS, both of which fail GREEN if broken:

  1. `ensure` never starts a CHILD, and never starts a second supervisor. The
     failure mode is a duplicate, which looks like a successful start.
  2. `unknown` is never treated as `down`. A lock we could not read is not proof
     that nothing is running, and starting on that assumption is how duplicates
     begin.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.genesis import supervisor_status as ss  # noqa: E402


@pytest.fixture()
def pid_file(tmp_path):
    return tmp_path / "launcher.pid"


# --------------------------------------------------------------------------- #
# 1. Three supervisor states, and `unknown` is not `down`
# --------------------------------------------------------------------------- #
def test_no_lock_file_is_down(pid_file):
    assert ss.supervisor(pid_file)["state"] == "down"


def test_a_live_pid_is_up(pid_file, monkeypatch):
    pid_file.write_text("4242", encoding="utf-8")
    import tools.compat.platform_utils as pu
    monkeypatch.setattr(pu, "pid_exists", lambda _p: True)
    got = ss.supervisor(pid_file)
    assert got["state"] == "up" and got["pid"] == 4242


def test_a_stale_lock_is_down_and_says_so(pid_file, monkeypatch):
    """The pid file outlives an unclean exit. That IS down — but the reason must
    name the stale lock, or the next reader deletes a lock that is live."""
    pid_file.write_text("4242", encoding="utf-8")
    import tools.compat.platform_utils as pu
    monkeypatch.setattr(pu, "pid_exists", lambda _p: False)
    got = ss.supervisor(pid_file)
    assert got["state"] == "down"
    assert "stale" in got["reason"]


def test_an_unreadable_lock_is_unknown_never_down(pid_file):
    """`down` authorises starting a supervisor. Garbage in the lock file is not
    evidence that none is running."""
    pid_file.write_text("not-a-pid", encoding="utf-8")
    got = ss.supervisor(pid_file)
    assert got["state"] == "unknown", "an unreadable lock authorised a second supervisor"


def test_an_untestable_pid_is_unknown_never_down(pid_file, monkeypatch):
    pid_file.write_text("4242", encoding="utf-8")

    def _boom(_p):
        raise OSError("cannot query the process table")

    import tools.compat.platform_utils as pu
    monkeypatch.setattr(pu, "pid_exists", _boom)
    assert ss.supervisor(pid_file)["state"] == "unknown"


# --------------------------------------------------------------------------- #
# 2. ensure() defers — the whole point of the card
# --------------------------------------------------------------------------- #
def test_ensure_defers_when_a_supervisor_is_running(tmp_path, monkeypatch):
    (tmp_path / ".tmp" / "genesis").mkdir(parents=True)
    (tmp_path / ".tmp" / "genesis" / "launcher.pid").write_text("77", encoding="utf-8")
    import tools.compat.platform_utils as pu
    monkeypatch.setattr(pu, "pid_exists", lambda _p: True)

    started = []
    result = ss.ensure(runner=lambda argv: started.append(argv), root=tmp_path)

    assert result["action"] == "deferred"
    assert not started, "a second supervisor was started beside a live one"


def test_ensure_defers_when_the_state_is_unknown(tmp_path, monkeypatch):
    """Starting on uncertainty is how duplicates begin. Deferring costs a
    message; starting costs a second supervisor nobody can see."""
    (tmp_path / ".tmp" / "genesis").mkdir(parents=True)
    (tmp_path / ".tmp" / "genesis" / "launcher.pid").write_text("junk", encoding="utf-8")

    started = []
    result = ss.ensure(runner=lambda argv: started.append(argv), root=tmp_path)

    assert result["action"] == "deferred"
    assert not started


def test_ensure_starts_the_supervisor_when_none_is_running(tmp_path):
    started = []

    def _runner(argv):
        started.append(argv)
        return 999

    result = ss.ensure(runner=_runner, root=tmp_path)
    assert result["action"] == "started" and result["pid"] == 999
    assert len(started) == 1


def test_ensure_starts_the_SUPERVISOR_and_never_a_child(tmp_path):
    """A child started beside a live supervisor is reaped silently; a child
    started WITHOUT one has nothing watching it when it dies. Either way the
    answer is the same: start the supervisor."""
    started = []
    ss.ensure(runner=lambda argv: started.append(argv) or 1, root=tmp_path)

    argv = started[0]
    assert ss.SUPERVISOR_SCRIPT in argv[-1], argv
    for child in ("daemon.py", "kanban_scheduler.py", "pr_watcher.py",
                  "dashboard/app.py"):
        assert not any(child in str(a) for a in argv), (
            f"ensure() started a CHILD ({child}) instead of the supervisor"
        )


def test_a_failed_start_is_reported_not_swallowed(tmp_path):
    def _boom(_argv):
        raise OSError("no python")

    assert ss.ensure(runner=_boom, root=tmp_path)["action"] == "failed"


# --------------------------------------------------------------------------- #
# 3. Unmeasured is not down
# --------------------------------------------------------------------------- #
def test_an_unreadable_process_table_is_unmeasured_not_down(monkeypatch):
    """"Nothing is running" authorises a start. "We could not look" does not."""
    import tools.compat.platform_utils as pu

    def _boom(_m):
        raise OSError("no process table")

    monkeypatch.setattr(pu, "find_pids_by_cmdline", _boom)
    kids = ss.children()
    assert kids and all(c["pids"] is None for c in kids)
    assert all(c["running"] is None for c in kids), (
        "an unreadable process table reported services as DOWN"
    )


def test_an_unrecorded_code_version_is_none_never_a_version(monkeypatch):
    """A service that has not registered, or a deployment where the identity
    migration has not run, reports null — not the tip, and not 'current'."""
    monkeypatch.setattr(ss, "_identity_rows", lambda: {})
    assert all(c["code_version"] is None for c in ss.children())


# --------------------------------------------------------------------------- #
# 4. The service list cannot silently drift from what the launcher starts
# --------------------------------------------------------------------------- #
def test_every_launcher_service_is_reported():
    """One rule, two renderings. A service added to launcher.py without an entry
    here would simply go unreported — the report would look complete and be
    short by one, which is the failure mode that reads as healthy."""
    import ast
    import inspect

    from tools.genesis import launcher

    tree = ast.parse(inspect.getsource(launcher))
    starters = {n.name for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and n.name.startswith("_start_")}
    # `_start_ollama` is a dependency check, not a supervised child.
    starters.discard("_start_ollama")

    assert len(starters) == len(ss.SERVICES), (
        f"launcher starts {sorted(starters)} but SERVICES has "
        f"{[s.name for s in ss.SERVICES]}"
    )


def test_the_module_kills_nothing():
    """It reports and it starts a supervisor. Stopping a child is `launcher`'s
    business, by verified pid. `/start`'s own `taskkill /f /im python.exe` has
    already taken out unrelated tooling here once — and since the supervisor is
    itself python.exe, that command kills the one process that would have
    restarted everything else."""
    import inspect

    src = inspect.getsource(ss)
    body = src.split('"""', 2)[-1]  # drop the module docstring, which discusses it
    for forbidden in ("taskkill", "kill_process", "terminate(", "Stop-Process"):
        assert forbidden not in body, f"the reporter reached for {forbidden}"


def test_a_shell_that_merely_mentions_a_service_is_not_counted(monkeypatch):
    """THE false positive this reporter shipped on its first live run.

    `find_pids_by_cmdline` substring-matches the whole joined command line across
    processes of ANY name, so the fragment "pr_watcher" also matches
    `bash -c '... pr_watcher ...'` — a diagnostic shell that merely typed it.
    Unfiltered, this reporter told a human there were THREE pr_watchers racing on
    auto-merge when there was one, plus two greps of its own.

    `launcher._kill_stale_instances` already excludes those (it measured five
    matches, four of them shells). The reporter must use the SAME exclusion, not
    a second copy of the rule — a reporter that disagrees with the killer
    describes a fleet the launcher does not have.
    """
    import tools.compat.platform_utils as pu
    from tools.genesis import launcher

    monkeypatch.setattr(pu, "find_pids_by_cmdline", lambda _m: [111, 222])
    # 222 only matches because the fragment appears after `-c`.
    monkeypatch.setattr(launcher, "_is_inline_snippet",
                        lambda pid, _frag: pid == 222)

    kids = {c["name"]: c for c in ss.children()}
    for c in kids.values():
        assert c["pids"] == [111], (
            f"{c['name']} counted a shell that merely mentioned it: {c['pids']}"
        )


def test_the_reporter_and_the_killer_share_one_exclusion():
    """Not a second copy: `_is_inline_snippet` is imported, never reimplemented.
    Six enforcement sites re-deriving one predicate is how a reporter comes to
    describe a policy the system does not have (the deps.py lesson)."""
    import inspect

    src = inspect.getsource(ss.children)
    assert "_is_inline_snippet" in src
    assert "def _is_inline_snippet" not in inspect.getsource(ss), (
        "the exclusion was reimplemented instead of imported"
    )
