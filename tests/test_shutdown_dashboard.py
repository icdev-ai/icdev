# CUI // SP-CTI
"""shutdown_dashboard stops the stack in the one order that does not fight itself.

start.md states the rule -- stop the SUPERVISOR, never a child by name, never
`taskkill /f /im python.exe` -- and on 2026-09-03 it had to be re-derived by
hand from the process table before a shutdown. These tests pin the derivation:
supervisor first; children only after it is provably gone and only by pid
recorded from the tree; agent workers reported and never stopped by default;
the FT supervisor before its child; verification of pids and ports; a reused
pid refused; an unreadable tree never stopped blind.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.genesis import shutdown_dashboard as S  # noqa: E402

SUP, DASH, SCHED, WATCH, WORKER = 20344, 13616, 30596, 7056, 41000
FT_SUP, FT_CHILD = 37068, 1192
RT_SUP, RT_CHILD = 37700, 19636


class FakeApi:
    """A scripted process tree that records every touch, in order."""

    def __init__(self, procs, listeners=None, survive=()):
        # procs: pid -> {"cmd": str, "children": [pids]}
        self.procs = {pid: dict(v) for pid, v in procs.items()}
        self.alive = set(procs)
        self.survive = set(survive)          # pids that ignore terminate AND kill
        self.calls: list = []
        self._listeners = dict(listeners or {})

    def pid_exists(self, pid):
        return pid in self.alive

    def cmdline(self, pid):
        return self.procs.get(pid, {}).get("cmd") if pid in self.alive else None

    def children(self, pid):
        return list(self.procs.get(pid, {}).get("children", [])) if pid in self.alive else []

    def terminate(self, pid):
        self.calls.append(("terminate", pid))
        if pid not in self.survive:
            self.alive.discard(pid)
            self._listeners = {p: o for p, o in self._listeners.items() if o != pid}

    def kill(self, pid):
        self.calls.append(("kill", pid))
        if pid not in self.survive:
            self.alive.discard(pid)

    def wait_dead(self, pid, timeout):
        return pid not in self.alive

    def find_by_cmdline(self, fragment):
        return sorted(p for p in self.alive if fragment in self.procs[p]["cmd"])

    def listeners(self, ports):
        return {p: o for p, o in self._listeners.items() if p in ports}


def _tree():
    return {
        SUP: {"cmd": "python tools/genesis/launch.py", "children": [DASH, SCHED, WATCH]},
        DASH: {"cmd": "python tools/dashboard/app.py --port 5050", "children": []},
        SCHED: {"cmd": "python tools/genesis/kanban_scheduler.py --interval 60", "children": [WORKER]},
        WATCH: {"cmd": "python tools/ci/pr_watcher.py --daemon", "children": []},
        WORKER: {"cmd": "claude -p ... kanban/rmf-fab-02", "children": []},
        FT_SUP: {"cmd": "python supervise_ft.py", "children": [FT_CHILD]},
        FT_CHILD: {"cmd": "python C:/AI/icdev_ft/launch_ft.py --port 5200", "children": []},
        RT_SUP: {"cmd": "python supervise_rt.py", "children": [RT_CHILD]},
        RT_CHILD: {"cmd": "python C:/AI/icdev_rt/launch_rt.py --host 127.0.0.1 --port 5300", "children": []},
    }


def _lock(tmp_path, pid):
    p = tmp_path / "launcher.pid"
    p.write_text(str(pid), encoding="utf-8")
    return p


def _run(api, tmp_path, pid=SUP, **kw):
    kw.setdefault("grace", 0.01)
    return S.plan_and_run(api=api, pid_file=_lock(tmp_path, pid), **kw)


# --------------------------------------------------------------------------- #
# 1. order: supervisor, then children by recorded pid, then FT, then its child
# --------------------------------------------------------------------------- #
def test_supervisor_goes_first_and_children_only_by_recorded_pid(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "_read_lock", lambda pf: {"state": "up", "pid": SUP, "reason": None})
    api = FakeApi(_tree(), listeners={5050: DASH, 5200: FT_CHILD, 5300: RT_CHILD})
    rep = _run(api, tmp_path)
    touched = [pid for _, pid in api.calls]
    assert touched[0] == SUP, "the supervisor must go first, or it respawns what we stop"
    assert set(touched[1:4]) == {DASH, SCHED, WATCH}
    assert touched.index(FT_SUP) < touched.index(FT_CHILD), "FT supervisor before its child"
    assert touched.index(RT_SUP) < touched.index(RT_CHILD), "RT supervisor before its child"
    assert not api.pid_exists(RT_SUP) and not api.pid_exists(RT_CHILD), "the RT pair is stopped too"
    assert WORKER not in touched, "agent workers are never stopped by default"
    assert rep["state"] == S.STATE_STOPPED and rep["survivors"] == [] and rep["listeners"] == {}
    assert S.exit_code(rep) == 0
    assert not (tmp_path / "launcher.pid").exists(), "the stale lock is removed once the pid is dead"


def test_workers_are_reported_and_left_running_unless_asked(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "_read_lock", lambda pf: {"state": "up", "pid": SUP, "reason": None})
    api = FakeApi(_tree())
    rep = _run(api, tmp_path)
    assert [w["pid"] for w in rep["workers_left_running"]] == [WORKER]
    assert api.pid_exists(WORKER)

    api2 = FakeApi(_tree())
    rep2 = _run(api2, tmp_path, include_workers=True)
    assert rep2["workers_left_running"] == [] and not api2.pid_exists(WORKER)
    assert ("terminate", WORKER) in api2.calls


def test_keep_ft_leaves_the_ft_pair_alone(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "_read_lock", lambda pf: {"state": "up", "pid": SUP, "reason": None})
    api = FakeApi(_tree())
    rep = _run(api, tmp_path, keep_ft=True)
    assert api.pid_exists(FT_SUP) and api.pid_exists(FT_CHILD)
    assert next(s for s in rep["steps"] if s["step"] == "ft")["skipped"] is True
    assert not api.pid_exists(RT_SUP), "--keep-ft keeps FT only; the RT pair still stops"


def test_keep_rt_leaves_the_rt_pair_alone(tmp_path, monkeypatch):
    """ICDEV[RT] is the second external stack (supervise_rt.py -> launch_rt.py
    on :5300). It is a ROW in EXTERNAL_SUPERVISORS, and --keep-rt skips that
    row and nothing else."""
    monkeypatch.setattr(S, "_read_lock", lambda pf: {"state": "up", "pid": SUP, "reason": None})
    api = FakeApi(_tree(), listeners={5300: RT_CHILD})
    rep = _run(api, tmp_path, keep_rt=True)
    assert api.pid_exists(RT_SUP) and api.pid_exists(RT_CHILD)
    assert not api.pid_exists(FT_SUP) and not api.pid_exists(FT_CHILD)
    rt = next(s for s in rep["steps"] if s["step"] == "rt")
    assert rt["skipped"] is True and rt["supervisor_script"] == "supervise_rt.py"
    # a kept pair is still LISTENING, and the report says so rather than reading clean
    assert rep["listeners"] == {"5300": RT_CHILD} and rep["state"] == S.STATE_SURVIVORS


def test_the_external_table_declares_both_stacks_and_their_ports():
    keys = {row[0]: row for row in S.EXTERNAL_SUPERVISORS}
    assert keys["ft"][1:] == ("supervise_ft.py", "launch_ft.py", 5200)
    assert keys["rt"][1:] == ("supervise_rt.py", "launch_rt.py", 5300)
    assert set(S.DEFAULT_PORTS) == {5050, 5200, 5300}


def test_a_wrapper_shell_around_the_ft_supervisor_is_not_stopped(tmp_path, monkeypatch):
    """Git Bash cannot exec(): the shell that launched supervise_ft.py keeps a
    command line naming it, one level up. Measured 2026-09-03: a substring match
    found FOUR pids for one supervisor. Only the innermost is the service."""
    monkeypatch.setattr(S, "_read_lock", lambda pf: {"state": "down", "pid": None, "reason": "no lock"})
    WRAP1, WRAP2 = 2744, 31016
    tree = {
        WRAP1: {"cmd": "bash -c python supervise_ft.py", "children": [WRAP2]},
        WRAP2: {"cmd": "bash await_pg_then_start_ft.sh supervise_ft.py", "children": [FT_SUP]},
        FT_SUP: {"cmd": "python supervise_ft.py", "children": [FT_CHILD]},
        FT_CHILD: {"cmd": "python launch_ft.py --port 5200", "children": []},
    }
    api = FakeApi(tree)
    rep = _run(api, tmp_path)
    touched = [pid for _, pid in api.calls]
    assert touched == [FT_SUP, FT_CHILD], f"only the innermost supervisor and its child: {touched}"
    assert api.pid_exists(WRAP1) and api.pid_exists(WRAP2)
    ft = next(s for s in rep["steps"] if s["step"] == "ft")
    assert [r["supervisor"] for r in ft["results"]] == [FT_SUP]


# --------------------------------------------------------------------------- #
# 2. refusals and verification
# --------------------------------------------------------------------------- #
def test_a_reused_pid_is_refused_and_nothing_is_touched(tmp_path, monkeypatch):
    """The lock names a live pid whose command line is not the launcher."""
    monkeypatch.setattr(S, "_read_lock", lambda pf: {"state": "up", "pid": SUP, "reason": None})
    tree = _tree()
    tree[SUP]["cmd"] = "python some_other_script.py"
    api = FakeApi(tree)
    rep = _run(api, tmp_path)
    assert rep["state"] == S.STATE_UNMEASURABLE and "reused pid" in rep["reason"]
    assert api.calls == [] and S.exit_code(rep) == 2


def test_a_survivor_or_a_listener_is_exit_1_not_silence(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "_read_lock", lambda pf: {"state": "up", "pid": SUP, "reason": None})
    api = FakeApi(_tree(), listeners={5050: 99999}, survive={WATCH})
    rep = _run(api, tmp_path)
    assert rep["state"] == S.STATE_SURVIVORS
    assert rep["survivors"] == [WATCH]
    assert rep["listeners"] == {"5050": 99999}
    assert ("kill", WATCH) in api.calls, "terminate then kill, then report -- never assume"
    assert S.exit_code(rep) == 1


def test_a_stale_lock_is_already_down_and_the_lock_is_cleaned(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "_read_lock", lambda pf: {"state": "down", "pid": SUP,
                                                    "reason": "launcher.pid names pid 20344, which is not running (stale lock)"})
    api = FakeApi({})
    rep = _run(api, tmp_path)
    assert rep["state"] == S.STATE_ALREADY_DOWN and S.exit_code(rep) == 0
    assert api.calls == []
    assert not (tmp_path / "launcher.pid").exists()


def test_an_unreadable_tree_is_never_stopped_blind(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "_read_lock", lambda pf: {"state": "unknown", "pid": None,
                                                    "reason": "launcher.pid unreadable"})
    api = FakeApi(_tree())
    rep = _run(api, tmp_path)
    assert rep["state"] == S.STATE_UNMEASURABLE and api.calls == [] and S.exit_code(rep) == 2


def test_no_psutil_is_unmeasurable(tmp_path, monkeypatch):
    def _boom():
        raise ImportError("no psutil")

    monkeypatch.setattr(S, "PsutilProcApi", _boom)
    rep = S.plan_and_run(api=None, pid_file=_lock(tmp_path, SUP), grace=0.01)
    assert rep["state"] == S.STATE_UNMEASURABLE and S.exit_code(rep) == 2


# --------------------------------------------------------------------------- #
# 3. dry run proves the plan and touches nothing
# --------------------------------------------------------------------------- #
def test_dry_run_records_the_plan_and_touches_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "_read_lock", lambda pf: {"state": "up", "pid": SUP, "reason": None})
    api = FakeApi(_tree(), listeners={5050: DASH})
    rep = _run(api, tmp_path, dry_run=True)
    assert api.calls == []
    assert rep["state"] == "dry_run"
    sup = next(s for s in rep["steps"] if s["step"] == "supervisor")
    assert sup["outcome"] == "would_stop" and [c["pid"] for c in sup["children"]] == [DASH, SCHED, WATCH]
    assert rep["listeners_now"] == {"5050": DASH}
    assert (tmp_path / "launcher.pid").exists(), "a dry run does not remove the lock"


# --------------------------------------------------------------------------- #
# 4. structural: no name-wide kill, ever
# --------------------------------------------------------------------------- #
def test_the_module_never_kills_by_name():
    """start.md: a name filter is what produced three concurrent pr_watchers, and
    `taskkill /f /im python.exe` is forbidden outright."""
    src = Path(S.__file__).read_text(encoding="utf-8")
    for forbidden in ("taskkill", "Stop-Process", "pkill", "killall", "os.system("):
        assert forbidden not in src, f"{forbidden!r} in shutdown_dashboard.py"
    # the only cmdline search is for the FT supervisor, which has no supervisor above it
    assert src.count("find_by_cmdline(") == 2, "one definition, one caller (the FT supervisor)"
