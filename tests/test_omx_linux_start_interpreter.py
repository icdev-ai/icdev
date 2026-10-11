# CUI // SP-CTI
"""/start and /stop on Linux (omx-linux-02).

Three defects measured 2026-10-11 when the operator ran /start on the Omarchy
laptop (Arch, PEP 668, ICDEV in ~/icdev/.venv, PostgreSQL):

1. every step ran bare ``python`` -- the system interpreter, without the
   project's packages;
2. the stuck-task reset wrote ``datetime('now')``, which PostgreSQL rejects, and
   reset a task whose work had already landed on the default branch;
3. /start said nothing true about whether the supervisor outlives a logout.
"""
from __future__ import annotations

import re
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

import _sql_compat
import tools.dx.project_python as pp
import tools.genesis.install_units as iu
from tools.kanban import startup_recovery as sr

ROOT = Path(__file__).resolve().parent.parent
COMMANDS = [ROOT / ".claude" / "commands" / "start.md", ROOT / ".claude" / "commands" / "stop.md"]


def _touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")
    return path


# ── 1. the interpreter ───────────────────────────────────────────────────────


class TestProjectPython:
    def test_linux_layout_picks_repo_venv(self, tmp_path):
        venv_py = _touch(tmp_path / ".venv" / "bin" / "python")
        assert pp.resolve(tmp_path, env={}, os_name="posix", fallback="SYS") == str(venv_py)

    def test_windows_layout_picks_scripts_python_exe(self, tmp_path):
        venv_py = _touch(tmp_path / ".venv" / "Scripts" / "python.exe")
        # a POSIX-layout file beside it must not be picked on Windows
        _touch(tmp_path / ".venv" / "bin" / "python")
        assert pp.resolve(tmp_path, env={}, os_name="nt", fallback="SYS") == str(venv_py)

    def test_active_venv_wins_over_repo_venv(self, tmp_path):
        _touch(tmp_path / "repo" / ".venv" / "bin" / "python")
        active = _touch(tmp_path / "elsewhere" / "bin" / "python")
        got = pp.resolve(tmp_path / "repo", env={"VIRTUAL_ENV": str(tmp_path / "elsewhere")},
                         os_name="posix", fallback="SYS")
        assert got == str(active)

    def test_stale_active_venv_falls_through_to_repo_venv(self, tmp_path):
        venv_py = _touch(tmp_path / ".venv" / "bin" / "python")
        got = pp.resolve(tmp_path, env={"VIRTUAL_ENV": str(tmp_path / "gone")},
                         os_name="posix", fallback="SYS")
        assert got == str(venv_py)

    def test_no_venv_falls_back_to_the_running_interpreter(self, tmp_path):
        assert pp.resolve(tmp_path, env={}, os_name="posix", fallback="SYS") == "SYS"
        assert pp.resolve(tmp_path, env={}, os_name="posix") == sys.executable

    def test_cli_runs_isolated_and_prints_the_host_layout(self, tmp_path):
        """The script is run BY an interpreter that cannot import the project, so
        it must work under ``-I`` (no site-packages, no cwd on sys.path)."""
        rel = ("Scripts", "python.exe") if sys.platform.startswith("win") else ("bin", "python")
        venv_py = _touch(tmp_path.joinpath(".venv", *rel))
        out = subprocess.run(
            [sys.executable, "-I", str(ROOT / "tools" / "dx" / "project_python.py")],
            cwd=str(tmp_path), capture_output=True, text=True, timeout=60,
            env={"PATH": "", "SYSTEMROOT": __import__("os").environ.get("SYSTEMROOT", "")},
        )
        assert out.returncode == 0, out.stderr
        assert Path(out.stdout.strip()) == venv_py.resolve()

    @pytest.mark.parametrize("doc", COMMANDS, ids=lambda p: p.name)
    def test_no_command_block_runs_bare_python(self, doc):
        """Inside a fenced shell block, ``python`` may appear only as the bootstrap
        that runs the resolver -- or in the FT/RT steps, another checkout."""
        bad = []
        in_block = in_here = other_checkout = False
        for n, line in enumerate(doc.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("```"):
                in_block = stripped in ("```powershell", "```bash")
                other_checkout = False
                continue
            if not in_block:
                continue
            other_checkout = other_checkout or "icdev_ft" in line
            if other_checkout:
                continue
            if stripped.startswith("@'"):
                in_here = True
            elif line.startswith("'@"):
                in_here = False
            if in_here or "tools/dx/project_python.py" in line or "icdev_ft" in line:
                continue
            if re.search(r'(?<![\w/.\\$"-])python3?(?= )', line):
                bad.append(f"{doc.name}:{n}: {stripped}")
        assert not bad, "\n".join(bad)

    @pytest.mark.parametrize("doc", COMMANDS, ids=lambda p: p.name)
    def test_every_py_block_resolves_py_first(self, doc):
        """Shell state does not persist between tool calls: a block that calls $PY
        without resolving it runs an empty command."""
        text = doc.read_text(encoding="utf-8")
        for block in re.findall(r"```(?:powershell|bash)\n(.*?)```", text, re.S):
            uses = "& $PY" in block or '"$PY"' in block
            if uses and "tools/dx/project_python.py" not in block:
                # step 8's "offer" block continues the --status block above it
                assert "install_units --platform linux" in block, block


# ── 2. the reset ─────────────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE kanban_tasks (id TEXT PRIMARY KEY, title TEXT, status TEXT,
    executor_type TEXT DEFAULT 'claude_cli', failure_count INTEGER DEFAULT 0,
    last_failure_reason TEXT, updated_at TEXT);
CREATE TABLE agent_sessions (session_id TEXT PRIMARY KEY, agent_type TEXT, pid INTEGER,
    host TEXT, cwd TEXT, started_at TEXT, last_heartbeat TEXT, current_intent TEXT,
    status TEXT DEFAULT 'active');
CREATE TABLE kanban_status_transitions (id TEXT PRIMARY KEY, task_id TEXT,
    from_status TEXT, to_status TEXT, actor TEXT, reason TEXT, recorded_at TEXT);
"""

LANDED = "omxl-land-01"
ORPHAN = "omxl-orph-01"


def _git(repo, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
                   cwd=str(repo), check=True, capture_output=True, timeout=60)


@pytest.fixture
def board(tmp_path, monkeypatch):
    """A SQLite board with one landed and one orphaned in_progress task, and a git
    repo whose origin/main carries the landed task's merge commit."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "base")
    _git(repo, "commit", "-q", "--allow-empty", "-m",
         f"Merge pull request #1 from icdev-ai/kanban/{LANDED}")
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")

    db = tmp_path / "k.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(_SCHEMA)
    for tid in (LANDED, ORPHAN):
        conn.execute("INSERT INTO kanban_tasks (id, title, status) VALUES (?, ?, 'in_progress')",
                     (tid, tid))
    conn.commit()
    conn.close()

    monkeypatch.delenv("KANBAN_LANDED_CHECK", raising=False)
    monkeypatch.setattr(sr, "_send", lambda *a, **k: None)
    monkeypatch.setattr(sr, "scan_live_task_processes", lambda ids: {})
    monkeypatch.setattr(sr, "_lease_holder_pid", lambda tid: None)
    monkeypatch.setattr(sr, "_lease_session", lambda tid: None)
    monkeypatch.setattr(sr, "foreign_scheduler_pid", lambda: 0)
    return {"db": db, "repo": repo, "factory": lambda: _sql_compat.connect(db)}


def _status(db, tid):
    conn = sqlite3.connect(str(db))
    try:
        return conn.execute("SELECT status, updated_at FROM kanban_tasks WHERE id = ?",
                            (tid,)).fetchone()
    finally:
        conn.close()


class TestResetSkipsLanded:
    def test_landed_task_is_held_and_orphan_is_reset(self, board):
        out = sr.recover_interrupted_tasks(conn_factory=board["factory"],
                                           repo_root=board["repo"], notify=False)
        assert _status(board["db"], LANDED)[0] == "in_progress"
        status, updated_at = _status(board["db"], ORPHAN)
        assert status == "backlog"
        assert updated_at  # the bound timestamp was written
        held = {h["id"]: h for h in out["held"]}
        assert held[LANDED]["reason"] == sr.HELD_LANDED
        assert [r["id"] for r in out["reset"]] == [ORPHAN]

    def test_landed_check_off_restores_the_old_reset(self, board, monkeypatch):
        monkeypatch.setenv("KANBAN_LANDED_CHECK", "off")
        sr.recover_interrupted_tasks(conn_factory=board["factory"],
                                     repo_root=board["repo"], notify=False)
        assert _status(board["db"], LANDED)[0] == "backlog"

    def test_unreachable_git_fails_open(self, board, tmp_path):
        """No origin ref -> nothing is 'landed' -> the reset proceeds as before."""
        sr.recover_interrupted_tasks(conn_factory=board["factory"],
                                     repo_root=tmp_path / "not-a-repo", notify=False)
        assert _status(board["db"], LANDED)[0] == "backlog"

    def test_reset_sql_is_portable(self):
        """The UPDATE binds its timestamp; no SQLite-only date function, which
        PostgreSQL rejects (the primary backend)."""
        src = Path(sr.__file__).read_text(encoding="utf-8")
        assert "\"UPDATE kanban_tasks SET status = 'backlog', updated_at = %s \"" in src
        assert not re.search(r"datetime\('now'\)|strftime\(|julianday\(", src)

    def test_start_md_no_longer_carries_hand_written_reset_sql(self):
        text = COMMANDS[0].read_text(encoding="utf-8")
        assert "datetime('now')" not in text
        assert "UPDATE kanban_tasks" not in text
        assert "-m tools.kanban.startup_recovery" in text


# ── 3. persistence ───────────────────────────────────────────────────────────


class TestPersistenceStatus:
    def _fake(self, monkeypatch, answers):
        def capture(argv):
            for key, val in answers.items():
                if all(tok in argv for tok in key.split(":")):
                    return val
            return None

        monkeypatch.setattr(iu, "_capture", capture)

    def _units(self, tmp_path):
        for _, name in iu.GENESIS_UNITS:
            _touch(tmp_path / name)

    def test_enabled_timer_with_linger_survives_both(self, tmp_path, monkeypatch):
        self._units(tmp_path)
        self._fake(monkeypatch, {"systemctl:is-enabled": "enabled",
                                 "systemctl:is-active": "active",
                                 "loginctl:show-user": "yes"})
        monkeypatch.setenv("USER", "op")
        st = iu.persistence_status("linux", tmp_path)
        assert st["units_installed"] and st["survives_reboot"] and st["survives_logout"]
        assert st["advice"] == []

    def test_no_units_offers_install_enable_and_linger(self, tmp_path, monkeypatch):
        self._fake(monkeypatch, {"systemctl:is-enabled": "not-found",
                                 "systemctl:is-active": "inactive",
                                 "loginctl:show-user": "no"})
        monkeypatch.setenv("USER", "op")
        st = iu.persistence_status("linux", tmp_path)
        assert st["survives_reboot"] is False and st["survives_logout"] is False
        joined = "\n".join(st["advice"])
        assert "install_units --platform linux" in joined
        assert "systemctl --user enable --now icdev-genesis.timer" in joined
        assert "loginctl enable-linger" in joined

    def test_unknown_stays_unknown(self, tmp_path, monkeypatch):
        """No systemctl/loginctl (a container, WSL without systemd) is not 'no'."""
        self._fake(monkeypatch, {})
        st = iu.persistence_status("linux", tmp_path)
        assert st["timer_enabled"] is None and st["linger"] is None
        assert st["survives_logout"] is None

    def test_windows_changes_nothing(self, tmp_path):
        st = iu.persistence_status("windows", tmp_path)
        assert "Task Scheduler" in st["message"]

    def test_status_cli_writes_nothing(self, tmp_path, monkeypatch):
        self._fake(monkeypatch, {})
        assert iu.main(["--status", "--platform", "linux", "--unit-dir", str(tmp_path)]) == 0
        assert list(tmp_path.iterdir()) == []

    def test_start_md_offers_the_units_and_reports_linger(self):
        text = COMMANDS[0].read_text(encoding="utf-8")
        assert "-m tools.genesis.install_units --status" in text
        assert "systemctl --user enable --now icdev-genesis.timer" in text
        assert "loginctl enable-linger" in text
