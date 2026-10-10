# CUI // SP-CTI
"""omx-linux-01: Linux is a first-class host.

Path comparison follows the HOST's case rules (both behaviours exercised on any
OS via ``case_insensitive=`` / a patched ``IS_WINDOWS``), the systemd units the
installer writes are parseable, and the unsupervised services stop by verified
pidfile -- never by name.
"""

from __future__ import annotations

import configparser
import os
from pathlib import Path

import pytest

import tools.compat.platform_utils as pu
import tools.genesis.install_units as iu
import tools.genesis.unsupervised_services as us
import tools.git.worktree_paths as wtp
import tools.kanban.startup_recovery as sr
import tools.kanban.worktree_husks as wh
import tools.kanban.worktree_pool as wp

REPO = Path(__file__).resolve().parents[1]


# ── the helper, both case behaviours ─────────────────────────────────────────


class TestPathKey:
    def test_case_insensitive_mode_folds_case_and_separators(self):
        assert pu.same_path(r"C:\AI\ICDev", "c:/ai/icdev/", case_insensitive=True)

    def test_case_sensitive_mode_keeps_case_distinct(self):
        assert not pu.same_path("/srv/Repo", "/srv/repo", case_insensitive=False)
        assert pu.same_path("/srv/repo/", "/srv/repo", case_insensitive=False)

    def test_posix_root_survives_trailing_slash_strip(self):
        assert pu.path_key("/", case_insensitive=False) == "/"

    def test_component_match_follows_case_mode(self):
        assert pu.path_has_component("/w/OMX-01", "omx-01", case_insensitive=True)
        assert not pu.path_has_component("/w/OMX-01", "omx-01", case_insensitive=False)
        assert pu.path_has_component(r"C:\w\omx-01", "omx-01", case_insensitive=False)
        assert not pu.path_has_component("/w/omx-011", "omx-01", case_insensitive=False)

    def test_default_is_the_host(self, monkeypatch):
        monkeypatch.setattr(pu, "IS_WINDOWS", False)
        assert not pu.same_path("/a/B", "/a/b")
        monkeypatch.setattr(pu, "IS_WINDOWS", True)
        assert pu.same_path("/a/B", "/a/b")


# ── the call sites that lowered unconditionally ──────────────────────────────


class TestGuardsFollowHostCase:
    def test_worktree_pool_norm_is_case_sensitive_on_linux(self, monkeypatch):
        monkeypatch.setattr(pu, "IS_WINDOWS", False)
        assert wp._norm("/srv/wt/Pool-1") != wp._norm("/srv/wt/pool-1")

    def test_worktree_pool_norm_is_case_insensitive_on_windows(self, monkeypatch):
        monkeypatch.setattr(pu, "IS_WINDOWS", True)
        assert wp._norm(r"C:\WT\Pool-1") == wp._norm("c:/wt/pool-1/")

    def test_task_worktree_match_is_case_sensitive_on_linux(self, monkeypatch):
        monkeypatch.setattr(pu, "IS_WINDOWS", False)
        assert not sr.path_mentions_task("/w/worktrees/KAX-RECOVER-04", "kax-recover-04")
        assert sr.path_mentions_task("/w/worktrees/kax-recover-04", "kax-recover-04")

    def test_task_worktree_match_is_case_insensitive_on_windows(self, monkeypatch):
        monkeypatch.setattr(pu, "IS_WINDOWS", True)
        assert sr.path_mentions_task(r"C:\W\.MERGE-KAX-RECOVER-04", "kax-recover-04")

    def test_husk_norm_follows_host_case(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pu, "IS_WINDOWS", False)
        assert wh._norm(tmp_path / "Husk") != wh._norm(tmp_path / "husk")
        monkeypatch.setattr(pu, "IS_WINDOWS", True)
        assert wh._norm(tmp_path / "Husk") == wh._norm(tmp_path / "husk")

    def test_scratchpad_sanction_is_case_correct_for_the_host(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ICDEV_WORKTREE_ROOT", str(tmp_path / "wtroot"))
        repo = tmp_path / "repo"
        exact = tmp_path / "claude" / "proj" / "sess" / "scratchpad" / "wt"
        cased = tmp_path / "Claude" / "proj" / "sess" / "Scratchpad" / "wt"
        assert wtp.is_sanctioned(exact, repo_root=repo)
        # Windows: one directory, sanctioned. POSIX: a tree nobody created.
        assert wtp.is_sanctioned(cased, repo_root=repo) is (os.name == "nt")


# ── systemd units ─────────────────────────────────────────────────────────────


def _parse(text: str) -> configparser.ConfigParser:
    # systemd repeats keys (Environment=); strict=False keeps the parse honest
    # about structure without rejecting that. '%' is a systemd specifier.
    cp = configparser.ConfigParser(strict=False, interpolation=None)
    cp.optionxform = str
    cp.read_string(text)
    return cp


class TestSystemdUnits:
    def test_rendered_units_parse_and_name_the_checkout(self, tmp_path):
        root = tmp_path / "checkout"
        for rel, _ in iu.GENESIS_UNITS + (iu.PRODUCTION_UNIT,):
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_text((REPO / rel).read_text(encoding="utf-8"), encoding="utf-8")
        units = {u["name"]: _parse(u["content"]) for u in iu.plan(root, "/opt/venv/bin/python", production=True)}

        svc = units["icdev-genesis.service"]
        assert svc["Service"]["ExecStart"] == "/opt/venv/bin/python tools/genesis/launch.py"
        assert svc["Service"]["WorkingDirectory"] == root.as_posix()
        assert svc["Service"]["Restart"] == "on-failure"
        timer = units["icdev-genesis.timer"]
        assert timer["Timer"]["Unit"] == "icdev-genesis.service"
        assert timer["Install"]["WantedBy"] == "timers.target"
        assert units["icdev.service"]["Service"]["WorkingDirectory"] == root.as_posix()

    def test_committed_units_are_valid_as_is(self):
        for rel, _ in iu.GENESIS_UNITS + (iu.PRODUCTION_UNIT,):
            _parse((REPO / rel).read_text(encoding="utf-8"))

    def test_service_template_has_no_placeholders(self):
        cp = _parse((REPO / "scripts/icdev.service.template").read_text(encoding="utf-8"))
        assert "User" not in cp["Service"]          # invalid in a --user unit
        assert cp["Unit"]["Documentation"] == "https://github.com/icdev-ai/icdev"
        assert "your-org" not in (REPO / "scripts/icdev.service.template").read_text(encoding="utf-8")

    def test_whitespace_in_root_is_refused(self):
        with pytest.raises(ValueError):
            iu.render_unit("x", Path("/home/a b/icdev"), "/usr/bin/python3")

    def test_linux_install_writes_lf_units_and_uninstall_removes_them(self, tmp_path, monkeypatch):
        monkeypatch.setattr(iu.shutil, "which", lambda _n: None)   # no systemctl here
        unit_dir = tmp_path / "user"
        rep = iu.run("linux", unit_dir, REPO, "/usr/bin/python3")
        assert rep["exit"] == 0
        written = sorted(p.name for p in unit_dir.iterdir())
        assert written == ["icdev-genesis.service", "icdev-genesis.timer"]
        assert b"\r\n" not in (unit_dir / "icdev-genesis.service").read_bytes()
        assert iu.run("linux", unit_dir, REPO, "/usr/bin/python3", uninstall=True)["exit"] == 0
        assert list(unit_dir.iterdir()) == []

    def test_dry_run_writes_nothing(self, tmp_path):
        rep = iu.run("linux", tmp_path / "user", REPO, "/usr/bin/python3", dry_run=True)
        assert rep["exit"] == 0 and len(rep["units"]) == 2
        assert not (tmp_path / "user").exists()

    def test_windows_path_is_unchanged_and_writes_nothing(self, tmp_path):
        rep = iu.run("windows", tmp_path / "user", REPO, "python")
        assert rep["exit"] == 0 and "register_startup.ps1" in rep["message"]
        assert not (tmp_path / "user").exists()

    def test_macos_is_reported_not_skipped(self, tmp_path):
        assert iu.run("macos", tmp_path / "user", REPO, "python")["exit"] == 2


# ── unsupervised services: by verified pidfile, never by name ────────────────


class FakeApi:
    def __init__(self, procs):
        self.procs = dict(procs)        # pid -> script basename
        self.terminated = []

    def pid_exists(self, pid):
        return pid in self.procs

    def find_by_cmdline(self, script):
        return sorted(p for p, s in self.procs.items() if s == script)

    def terminate(self, pid):
        self.terminated.append(pid)
        self.procs.pop(pid, None)

    def kill(self, pid):
        self.procs.pop(pid, None)

    def wait_dead(self, pid, timeout):
        return pid not in self.procs


PORTAL = us.SERVICES[0]


class TestUnsupervisedServices:
    def _pidfile(self, base, pid):
        d = us.pid_dir(base)
        d.mkdir(parents=True, exist_ok=True)
        (d / "portal.pid").write_text(str(pid), encoding="utf-8")
        return d / "portal.pid"

    def test_stop_by_verified_pidfile_then_removes_it(self, tmp_path):
        pf = self._pidfile(tmp_path, 41)
        api = FakeApi({41: "api_gateway.py", 7: "unrelated.py"})
        rows = us.stop(api, [PORTAL], tmp_path, grace=0.1)
        assert api.terminated == [41] and rows[0]["survivors"] == []
        assert not pf.exists()

    def test_reused_pid_in_pidfile_is_never_touched(self, tmp_path):
        self._pidfile(tmp_path, 41)
        api = FakeApi({41: "some_editor.py", 55: "api_gateway.py"})
        rows = us.stop(api, [PORTAL], tmp_path, grace=0.1)
        assert 41 not in api.terminated and api.terminated == [55]
        assert rows[0]["stale_pidfile_pid"] == 41

    def test_dry_run_stops_nothing(self, tmp_path):
        pf = self._pidfile(tmp_path, 41)
        api = FakeApi({41: "api_gateway.py"})
        us.stop(api, [PORTAL], tmp_path, dry_run=True)
        assert api.terminated == [] and pf.exists()

    def test_start_adopts_a_running_service_instead_of_duplicating(self, tmp_path):
        api = FakeApi({90: "api_gateway.py"})
        rows = us.start(api, [PORTAL], tmp_path, spawn=lambda *_: pytest.fail("duplicate spawn"))
        assert rows[0]["action"] == "adopted" and rows[0]["pid"] == 90
        assert (us.pid_dir(tmp_path) / "portal.pid").read_text(encoding="utf-8") == "90"

    def test_start_records_the_spawned_pid(self, tmp_path):
        rows = us.start(FakeApi({}), [PORTAL], tmp_path, spawn=lambda *_: 1234)
        assert rows[0]["action"] == "started"
        assert (us.pid_dir(tmp_path) / "portal.pid").read_text(encoding="utf-8") == "1234"


# ── the one-shot launcher no longer kills every python process ───────────────


def test_launch_dashboard_stops_by_verified_pid_not_by_name(monkeypatch):
    # Imported here, with load_dotenv stubbed: its module body loads the repo
    # .env, which must not leak into the rest of an in-suite run.
    import dotenv
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **k: False)
    import tools.launch_dashboard as ld

    calls = []
    monkeypatch.setattr(ld.subprocess, "run", lambda cmd, **kw: calls.append(cmd) or
                        type("R", (), {"returncode": 0})())
    monkeypatch.setattr(ld.time, "sleep", lambda _s: None)
    ld._stop_stack()
    assert len(calls) == 1
    assert Path(calls[0][1]).name == "shutdown_dashboard.py"
    flat = " ".join(map(str, calls[0])).lower()
    assert "taskkill" not in flat and "pkill" not in flat and "killall" not in flat
