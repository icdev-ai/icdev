# CUI // SP-CTI
"""A daemon must pick up its own code changes without a human.

pr_watcher was restarted BY HAND four times on 2026-08-09, and between each
restart it ran fixes that had already merged. Twice the board looked broken when
the only fault was a daemon serving code from hours earlier. Fixing the
underlying bug does not help if the fix cannot reach the process that needs it.
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path


from tools.genesis import code_reload as cr


def test_the_watch_set_is_what_was_actually_imported():
    """Not a hand-maintained list: a change to error_classifier changes
    pr_watcher's behaviour as much as a change to pr_watcher.py, and a list would
    have missed most of a day's merges."""
    snap = cr.snapshot()
    assert snap, "expected some repo modules to be imported"
    root = str(Path(__file__).resolve().parents[2])
    assert all(p.startswith(root) for p in snap), "only repo files may be watched"
    assert any(p.endswith("code_reload.py") for p in snap)


def test_a_changed_mtime_is_detected():
    before = {"/repo/a.py": 100.0, "/repo/b.py": 200.0}
    after = {"/repo/a.py": 100.0, "/repo/b.py": 201.0}
    assert cr.changed_files(before, after) == ["/repo/b.py"]


def test_a_NEWLY_IMPORTED_file_is_not_a_change():
    """This assertion used to say the opposite, and that was the bug.

    A path that appears only in the later snapshot is a lazy import — the same
    file that was always on disk, loaded when some code path first reached it.
    Treating it as new code made the daemon re-exec, re-baseline, run one cycle,
    lazily import something else and re-exec again: kanban_scheduler restarted
    roughly once a minute and never finished a dispatch.
    """
    assert cr.changed_files({}, {"/repo/new.py": 1.0}) == []
    # ...and the real signal still works: a file we HAD loaded, now rewritten.
    assert cr.changed_files({"/repo/a.py": 1.0}, {"/repo/a.py": 2.0}) == ["/repo/a.py"]


def test_a_vanished_file_is_NOT_a_change():
    """An import that disappeared is not the code this process is running, and a
    file deleted mid-write would trigger a restart that fixes nothing."""
    assert cr.changed_files({"/repo/gone.py": 1.0}, {}) == []


# ── the restart decision ────────────────────────────────────────────────────
class _Exec:
    def __init__(self):
        self.calls = []

    def __call__(self, exe, argv):
        self.calls.append((exe, argv))


def test_it_reexecs_when_code_changed():
    ex = _Exec()
    root = Path(__file__).resolve().parents[2]
    # A baseline with every loaded file at mtime 0 — so they all read as
    # rewritten. An empty baseline no longer means "everything changed".
    stale = {path: 0.0 for path in cr.snapshot(root)}
    cr.restart_if_code_changed(
        stale, started_at=time.time() - 10_000, execv=ex, root=root)
    assert ex.calls, "expected a re-exec"
    exe, argv = ex.calls[0]
    assert argv[0] == exe, "must re-exec through the interpreter, not argv[0]"


def test_it_refuses_to_restart_before_the_minimum_uptime():
    """A restart loop is worse than stale code: it never finishes a poll, and
    every cycle looks like a fresh start so the loop itself is hard to see."""
    ex = _Exec()
    root = Path(__file__).resolve().parents[2]
    stale = {path: 0.0 for path in cr.snapshot(root)}
    changed = cr.restart_if_code_changed(
        stale, started_at=time.time(), execv=ex, root=root)
    assert ex.calls == []
    assert changed, "it should still report what changed"


def test_nothing_changed_means_nothing_happens():
    root = Path(__file__).resolve().parents[2]
    base = cr.snapshot(root)
    ex = _Exec()
    assert cr.restart_if_code_changed(
        base, started_at=time.time() - 10_000, execv=ex, root=root) == []
    assert ex.calls == []


def test_disabled_never_restarts():
    ex = _Exec()
    assert cr.restart_if_code_changed(
        {}, started_at=time.time() - 10_000, enabled=False, execv=ex) == []
    assert ex.calls == []


def test_a_failed_reexec_does_not_kill_the_daemon():
    """Running stale is bad; dying is worse."""
    def boom(exe, argv):
        raise OSError("execv refused")
    cr.restart_if_code_changed(
        {}, started_at=time.time() - 10_000, execv=boom,
        root=Path(__file__).resolve().parents[2])


def test_both_daemons_check_after_the_work_not_during():
    """A restart mid-dispatch abandons a task the scheduler had just claimed;
    mid-poll it could abandon a merge in flight."""
    root = Path(__file__).resolve().parents[2]
    for rel, marker in (
        ("tools/ci/pr_watcher.py", "report = self.poll_once()"),
        ("tools/genesis/kanban_scheduler.py", "time.sleep(args.interval)"),
    ):
        text = (root / rel).read_text(encoding="utf-8")
        call = text.index("restart_if_code_changed(")
        assert text.index(marker) < call or marker == "time.sleep(args.interval)", rel
        if marker == "time.sleep(args.interval)":
            # the check must come BEFORE the sleep that ends the cycle
            assert call < text.rindex(marker), rel


# ── pulling: the guard is the point, not the pull ───────────────────────────
class _Git:
    """Scripted git. Keyed on the first argument of each call."""

    def __init__(self, **replies):
        self.replies = replies
        self.calls = []

    def __call__(self, args, **kw):
        self.calls.append(list(args))
        rc, out = self.replies.get(args[0], (0, ""))
        return type("P", (), {"returncode": rc, "stdout": out, "stderr": ""})()

    @property
    def merged(self):
        return any(a[:2] == ["merge", "--ff-only"] for a in self.calls)


def _reset_throttle():
    cr._last_pull = 0.0


def test_it_pulls_when_nothing_local_is_at_risk():
    _reset_throttle()
    g = _Git(**{"rev-parse": (0, "main\n"), "fetch": (0, ""),
                "diff": (0, "tools/ci/pr_watcher.py\n"), "status": (0, "")})
    out = cr.pull_if_safe(runner=g)
    assert out["pulled"] is True and g.merged


def test_it_REFUSES_when_an_incoming_file_is_locally_modified():
    """The whole reason this is guarded: a blind pull in a daemon either fails
    every cycle or clobbers work nobody asked it to touch."""
    _reset_throttle()
    g = _Git(**{"rev-parse": (0, "main\n"), "fetch": (0, ""),
                "diff": (0, "args/projects.yaml\n"),
                "status": (0, " M args/projects.yaml\n")})
    out = cr.pull_if_safe(runner=g)
    assert out["pulled"] is False
    assert out["conflicts"] == ["args/projects.yaml"]
    assert not g.merged


def test_unrelated_local_edits_do_not_block_it():
    """Refusing on ANY dirt would mean never pulling on a working checkout."""
    _reset_throttle()
    g = _Git(**{"rev-parse": (0, "main\n"), "fetch": (0, ""),
                "diff": (0, "tools/ci/pr_watcher.py\n"),
                "status": (0, " M docs/notes.md\n")})
    assert cr.pull_if_safe(runner=g)["pulled"] is True


def test_it_never_moves_a_checkout_that_is_not_on_main():
    """Someone may be working there; a daemon must not move it under them."""
    for head in ("feat/something\n", "HEAD\n", ""):
        _reset_throttle()          # the throttle is module-global; reset per case
        g = _Git(**{"rev-parse": (0, head)})
        out = cr.pull_if_safe(runner=g)
        assert out["pulled"] is False and "not on main" in out["reason"]
        assert not g.merged


def test_a_merge_in_progress_stops_it():
    _reset_throttle()
    g = _Git(**{"rev-parse": (0, "main\n"), "fetch": (0, ""),
                "diff": (0, "a.py\n"), "status": (0, "UU a.py\n")})
    out = cr.pull_if_safe(runner=g)
    assert out["pulled"] is False and out["reason"] == "merge in progress"


def test_a_non_fast_forward_is_never_forced():
    _reset_throttle()
    g = _Git(**{"rev-parse": (0, "main\n"), "fetch": (0, ""),
                "diff": (0, "a.py\n"), "status": (0, ""), "merge": (1, "")})
    assert cr.pull_if_safe(runner=g)["pulled"] is False


def test_a_rename_is_read_from_its_NEW_path():
    """`R  old -> new` would otherwise register the wrong file as modified."""
    _reset_throttle()
    g = _Git(**{"rev-parse": (0, "main\n"), "fetch": (0, ""),
                "diff": (0, "new.py\n"), "status": (0, "R  old.py -> new.py\n")})
    assert cr.pull_if_safe(runner=g)["pulled"] is False


def test_it_is_throttled():
    """A 30s poll does not need to ask the forge every cycle."""
    _reset_throttle()
    g = _Git(**{"rev-parse": (0, "main\n"), "fetch": (0, ""),
                "diff": (0, "a.py\n"), "status": (0, "")})
    assert cr.pull_if_safe(runner=g)["pulled"] is True
    assert cr.pull_if_safe(runner=g)["reason"] == "throttled"


def test_git_unavailable_is_a_reason_not_an_exception():
    """This runs inside someone else's poll loop."""
    _reset_throttle()
    def boom(args, **kw):
        raise OSError("no git")
    assert cr.pull_if_safe(runner=boom)["pulled"] is False


def test_a_daemon_that_only_LAZY_IMPORTS_never_restarts():
    """The regression, stated as the loop it caused.

    A daemon takes its baseline at startup, then imports more modules as it
    works — connectors, leases, linkers, all reached for the first time on some
    later cycle. If each of those counts as changed code the daemon re-execs,
    and after re-exec it does the same thing again, forever. It never gets far
    enough into a cycle to dispatch anything, which is exactly how the board
    stopped moving while the scheduler process looked perfectly healthy.
    """
    baseline = {"/repo/scheduler.py": 100.0}
    # Everything a running daemon subsequently pulls in, none of it modified.
    after = dict(baseline)
    for lazy in ("pr_linker.py", "leases.py", "connector.py", "github.py"):
        after[f"/repo/{lazy}"] = 500.0
    assert cr.changed_files(baseline, after) == [], (
        "lazy imports must not look like new code — this is the restart loop")


def test_a_REAL_edit_is_still_caught_amid_lazy_imports():
    """The fix must not buy quiet by going blind: an actual rewrite of a file the
    process already loaded is the whole point of the feature."""
    baseline = {"/repo/scheduler.py": 100.0, "/repo/util.py": 100.0}
    after = {"/repo/scheduler.py": 100.0, "/repo/util.py": 999.0,
             "/repo/lazy.py": 500.0}
    assert cr.changed_files(baseline, after) == ["/repo/util.py"]


# --------------------------------------------------------------------------- #
# Re-exec must not strand the old process's listening socket (Windows)
# --------------------------------------------------------------------------- #
# The dashboard was found on 2026-08-15 accepting TCP connections and answering
# none of them. The listening socket on :5050 was owned by a PID that no longer
# existed, holding six ESTABLISHED and six CLOSE_WAIT connections, while the live
# process sat idle in its own serve_forever accept loop. Cause: os.execv on
# Windows is not an in-place image replacement -- it spawns a new process that
# INHERITS the caller's handles and then kills the caller. Every health signal
# said fine; nothing was served.


def _record_respawn(monkeypatch, platform):
    """Capture what respawn() does on a given platform without doing it."""
    calls = {"popen": [], "exit": [], "execv": []}
    monkeypatch.setattr(cr.os, "name", platform)
    monkeypatch.setattr(cr.subprocess, "Popen",
                        lambda argv, **kw: calls["popen"].append((argv, kw)))
    monkeypatch.setattr(cr.os, "_exit", lambda code: calls["exit"].append(code))
    monkeypatch.setattr(cr.os, "execv",
                        lambda path, argv: calls["execv"].append((path, argv)))
    return calls


def test_windows_respawn_never_lets_the_replacement_inherit_a_socket(monkeypatch):
    """close_fds=True is the whole fix: no inheritance, no stranded listener."""
    calls = _record_respawn(monkeypatch, "nt")
    cr.respawn(["python.exe", "app.py"])

    assert not calls["execv"], (
        "os.execv on Windows hands the replacement this process's open handles, "
        "including a server's listening socket — that is the defect"
    )
    assert len(calls["popen"]) == 1
    argv, kwargs = calls["popen"][0]
    assert argv == ["python.exe", "app.py"]
    assert kwargs["close_fds"] is True, (
        "without close_fds the replacement inherits the listening socket and "
        "the dead parent's copy keeps swallowing connections"
    )
    assert calls["exit"] == [0], (
        "the old process must die hard so the kernel closes its socket; "
        "sys.exit on a watcher thread would only unwind that thread"
    )


def test_windows_respawn_hands_over_stdout_so_the_replacement_still_logs(monkeypatch):
    """close_fds=True means NO handles unless named — including the log file.

    These daemons run with stdout redirected to .tmp/*.log. Trading a hung
    server for a mute one is not a fix.
    """
    calls = _record_respawn(monkeypatch, "nt")
    cr.respawn(["python.exe", "app.py"])
    _, kwargs = calls["popen"][0]

    assert kwargs.get("stdin") == cr.subprocess.DEVNULL
    # stdout/stderr are passed when they have a real handle behind them. Under
    # pytest's capture they may not, and respawn must still proceed.
    for name in ("stdout", "stderr"):
        assert name not in kwargs or isinstance(kwargs[name], int)


def test_posix_respawn_still_uses_execv(monkeypatch):
    """POSIX execv IS an in-place replacement and was never the problem.

    Paired with the Windows test on purpose: "just stop using execv everywhere"
    would break the platform where re-exec works correctly.
    """
    calls = _record_respawn(monkeypatch, "posix")
    cr.respawn(["/usr/bin/python3", "app.py"])

    assert calls["execv"] == [("/usr/bin/python3", ["/usr/bin/python3", "app.py"])]
    assert not calls["popen"]
    assert not calls["exit"]


def test_a_stream_with_no_real_handle_is_skipped_not_fatal(monkeypatch):
    """A restart must not fail because stdout was captured."""
    class _Captured:
        def flush(self):
            pass

        def fileno(self):
            raise ValueError("underlying buffer detached")

    monkeypatch.setattr(cr.sys, "stdout", _Captured())
    monkeypatch.setattr(cr.sys, "stderr", _Captured())
    streams = cr._inheritable_std_streams()

    assert streams == {"stdin": cr.subprocess.DEVNULL}


_RESPAWN_SERVER = '''
import pathlib, socket, sys, time
sys.path.insert(0, {root!r})
from tools.genesis import code_reload as cr

role, portfile, pidfile = sys.argv[1], pathlib.Path(sys.argv[2]), pathlib.Path(sys.argv[3])
if role == "parent":
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(5)
    # Exactly what werkzeug.serving does to its listening socket, so its own
    # reloader child can reuse the port via WERKZEUG_SERVER_FD. Python makes
    # sockets NON-inheritable by default (PEP 446), so without this line the
    # bug does not reproduce and the test would pass against the defect.
    srv.set_inheritable(True)
    portfile.write_text(str(srv.getsockname()[1]), encoding="utf-8")
    # srv is deliberately NOT closed. A real server re-execs with its listening
    # socket wide open, and that is exactly the handle under test.
    cr.respawn([sys.executable, __file__, "child", str(portfile), str(pidfile)])
else:
    import os
    pidfile.write_text(str(os.getpid()), encoding="utf-8")
    time.sleep(20)
'''


def test_a_listening_socket_never_outlives_the_process_that_made_it(tmp_path: Path):
    """The regression, measured end to end rather than by inspecting kwargs.

    A parent binds a listening socket and re-execs WITHOUT closing it, which is
    what a real server does. The invariant either way: no listening socket may
    be left behind in a process that has exited.

    The two platforms satisfy it differently, and BOTH are asserted here rather
    than skipping one. On POSIX execv is a true in-place replacement -- same
    PID, one process, so there is no second process to strand anything in. On
    Windows there are two processes, so the invariant becomes a live claim about
    the port: once the parent is gone it must REFUSE connections.

    With the defect Windows does not refuse. The replacement inherits the
    handle, the kernel keeps accepting into a socket nobody reads, and a connect
    succeeds against a server that will never answer -- which is exactly why the
    hung dashboard passed every health signal anyone thought to check.
    """
    root = Path(cr.__file__).resolve().parents[2]
    script = tmp_path / "respawn_server.py"
    script.write_text(_RESPAWN_SERVER.format(root=str(root)), encoding="utf-8")
    portfile, pidfile = tmp_path / "port.txt", tmp_path / "child.pid"

    parent = subprocess.Popen([sys.executable, str(script), "parent",
                               str(portfile), str(pidfile)])
    try:
        deadline = time.time() + 60
        while time.time() < deadline and not portfile.exists():
            time.sleep(0.1)
        assert portfile.exists(), "the parent never bound a port"
        port = int(portfile.read_text(encoding="utf-8"))

        assert parent.wait(timeout=60) is not None, "the parent never exited"
        # The replacement must be up, or "refused" would prove only that
        # nothing is running -- the fabricated-pass this test exists to avoid.
        while time.time() < deadline and not pidfile.exists():
            time.sleep(0.1)
        assert pidfile.exists(), "the replacement never started"
        replacement_pid = int(pidfile.read_text(encoding="utf-8").strip())

        if os.name != "nt":
            # execv replaced the image in place: one process throughout, so the
            # socket is still held by its own creator and nothing is stranded.
            assert replacement_pid == parent.pid, (
                "os.execv did not replace the process in place — there are now "
                "two processes and the listening socket may be stranded in one"
            )
            return

        probe = socket.socket()
        probe.settimeout(5)
        try:
            probe.connect(("127.0.0.1", port))
            connected = True
        except OSError:
            connected = False
        finally:
            probe.close()

        assert not connected, (
            f"port {port} still accepts connections after the parent exited — "
            "the replacement inherited the listening socket, so every request "
            "lands in a buffer no one reads"
        )
    finally:
        parent.kill()
        if pidfile.exists():
            subprocess.run(["taskkill", "/F", "/PID", pidfile.read_text(encoding="utf-8").strip()],
                           capture_output=True, check=False)
