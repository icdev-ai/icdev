# CUI // SP-CTI
"""`icdev omarchy setup|status|uninstall` (omx-setup-01) -- every external command faked.

The fake host keeps STATE: a faked ``createdb`` / ``CREATE EXTENSION`` makes the
database probe succeed afterwards, a faked ``claude mcp add`` makes ``claude mcp
get`` succeed. That is what lets the idempotency test assert a second run is
all ``skipped`` rather than merely asserting the second run printed something.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.cli import omarchy
from tools.cli.omarchy import Host, Result

REPO = Path(__file__).resolve().parents[1]


class FakeWorld:
    def __init__(self, *, omarchy_on: bool, pg_ready: bool, harnesses=("opencode", "claude", "pi")):
        self.bins = set(harnesses) | {"systemctl", "pacman"}
        if omarchy_on:
            self.bins.add("omarchy")
        if pg_ready:
            self.bins.add("psql")
        self.pg_ready = pg_ready          # extension present
        self.schema = pg_ready            # bootstrap_pg finished
        self.schema_fails = False
        self.pg_running = pg_ready
        self.mcp = set()
        self.timers: set = set()
        self.calls: list[list[str]] = []

    @property
    def timer_on(self):
        return omarchy.GENESIS_TIMER in self.timers

    def which(self, name):
        return f"/usr/bin/{name}" if name in self.bins else None

    def run(self, argv, env=None):
        argv = list(argv)
        self.calls.append(argv)
        joined = " ".join(argv)
        if argv[0] == "psql" and "pg_extension" in joined:
            if not self.pg_running:
                return Result(2, "", "connection refused")
            return Result(0, "1\n" if self.pg_ready and self.schema else "")
        if argv[0] == "pg_isready":
            return Result(0 if self.pg_running else 2)
        if argv[:3] == ["sudo", "pacman", "-S"]:
            self.bins.add("psql")
            return Result(0)
        if argv[:2] == ["sudo", "test"]:
            return Result(1)
        if "systemctl enable --now postgresql" in joined:
            self.pg_running = True
            return Result(0)
        if "CREATE EXTENSION" in joined:
            self.pg_ready = True
            return Result(0)
        if "icdev.tools.db.bootstrap_pg" in joined:
            self.schema = not self.schema_fails
            return Result(1, "", "snapshot load failed in batch 3") if self.schema_fails else Result(0)
        if argv[0] in ("claude", "codex") and argv[1] == "mcp":
            if argv[2] == "get":
                return Result(0 if argv[0] in self.mcp else 1)
            if argv[2] == "add":
                self.mcp.add(argv[0])
            if argv[2] == "remove":
                self.mcp.discard(argv[0])
            return Result(0)
        if argv[:2] == ["systemctl", "--user"]:
            if argv[2] in ("is-enabled", "is-active"):
                return Result(0 if argv[-1] in self.timers else 1)
            if argv[2:4] == ["enable", "--now"]:
                self.timers.add(argv[-1])
            if argv[2:4] == ["disable", "--now"]:
                self.timers.discard(argv[-1])
            return Result(0)
        return Result(0)  # sudo -u postgres psql/createdb


@pytest.fixture
def make_host(tmp_path, monkeypatch):
    home = tmp_path / "home"
    root = tmp_path / "root"
    home.mkdir()
    # The checkout pieces the steps read: unit templates, llm config.
    for rel in ("scripts/systemd/icdev-genesis.service", "scripts/systemd/icdev-genesis.timer",
                "args/llm_config.yaml", "icdev/data/bin/omarchy-agent-usage-icdev",
                *(f"icdev/data/omarchy/{n}" for n in (*omarchy.PANEL_UNITS, *omarchy.PANEL_MARKS))):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes((REPO / rel).read_bytes())
    environ = {"XDG_CONFIG_HOME": str(home / ".config"),
               "XDG_STATE_HOME": str(home / ".local" / "state"),
               "OMARCHY_PATH": str(home / ".local" / "share" / "omarchy"),
               "PI_CODING_AGENT_DIR": str(home / ".pi" / "agent")}
    # plugin_dir() (the guard installer) reads the process environment.
    for k, v in environ.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    def _make(world: FakeWorld, platform: str = "linux") -> Host:
        return Host(root=root, home=home, environ=environ, platform=platform,
                    python="/usr/bin/python3", which=world.which, run=world.run)
    return _make


def _states(report):
    return {s["step"]: s["state"] for s in report["steps"]}


MUTATING = ("pacman", "initdb", "createdb", "CREATE", "enable", "add")


@pytest.mark.parametrize("omarchy_on", [True, False])
@pytest.mark.parametrize("pg_ready", [True, False])
def test_dry_run_plan_for_each_omarchy_pg_combination(make_host, omarchy_on, pg_ready):
    world = FakeWorld(omarchy_on=omarchy_on, pg_ready=pg_ready)
    host = make_host(world)
    report = omarchy.run("setup", host, dry_run=True)

    assert report["omarchy"] is omarchy_on
    assert ("not on Omarchy" in report["host"]) is (not omarchy_on)
    assert report["harnesses"] == ["opencode", "claude", "pi"]
    states = _states(report)
    assert list(states) == list(omarchy.STEPS)
    assert states["postgres"] == ("skipped" if pg_ready else "planned")
    for step in ("env", "mcp", "skill", "guard", "systemd"):
        assert states[step] == "planned", step
    assert states["panel"] == ("planned" if omarchy_on else "n/a")
    pg = report["steps"][0]
    if not pg_ready:
        cmds = "\n".join(pg["commands"])
        assert "sudo pacman -S --needed --noconfirm postgresql pgvector" in cmds
        assert "initdb" in cmds and "createdb -O icdev icdev" in cmds
        assert "CREATE EXTENSION IF NOT EXISTS vector" in cmds
        assert "icdev.tools.db.bootstrap_pg" in cmds
    # A dry run executes no mutating command and writes nothing.
    assert not [c for c in world.calls if any(m in " ".join(c) for m in MUTATING)
                and c[2:3] != ["get"] and "is-" not in " ".join(c)]
    assert not (host.root / ".env").exists()
    assert not any(host.home.rglob("*.json"))


def test_setup_then_second_run_is_all_skipped(make_host):
    world = FakeWorld(omarchy_on=True, pg_ready=False)
    host = make_host(world)
    first = omarchy.run("setup", host, yes=True, start=True)
    assert first["exit"] == 0, first
    assert set(_states(first).values()) == {"done"}

    env = (host.root / ".env").read_text(encoding="utf-8")
    assert f"ICDEV_DATABASE_URL={omarchy.DSN}" in env
    assert "ICDEV_STORAGE_BACKEND=postgresql" in env
    oc = json.loads((host.home / ".config" / "opencode" / "opencode.json").read_text(encoding="utf-8"))
    assert oc["mcp"]["icdev-unified"]["type"] == "local"
    pi = json.loads((host.home / ".pi" / "agent" / "mcp.json").read_text(encoding="utf-8"))
    assert "icdev-unified" in pi["mcpServers"]
    assert "claude" in world.mcp
    assert (host.home / ".pi" / "agent" / "extensions" / "icdev-guard.ts").is_file()
    assert (host.home / ".config" / "opencode" / "plugin" / "icdev-guard.ts").is_file()
    unit = host.home / ".config" / "systemd" / "user" / "icdev-genesis.service"
    assert b"\r\n" not in unit.read_bytes()
    assert world.timer_on

    second = omarchy.run("setup", host, yes=True, start=True)
    assert set(_states(second).values()) == {"skipped"}, second
    status = omarchy.run("status", host)
    assert set(_states(status).values()) == {"ok"}


def test_postgres_needs_yes(make_host):
    world = FakeWorld(omarchy_on=False, pg_ready=False)
    report = omarchy.run("setup", make_host(world))
    pg = report["steps"][0]
    assert pg["state"] == "pending" and "--yes" in pg["detail"]
    assert not any(c[:2] == ["sudo", "pacman"] for c in world.calls)


def test_existing_database_url_is_kept(make_host):
    world = FakeWorld(omarchy_on=False, pg_ready=True)
    host = make_host(world)
    (host.root / ".env").write_text("ICDEV_DATABASE_URL=postgresql://me@db/icdev\n", encoding="utf-8")
    report = omarchy.run("setup", host)
    env_step = report["steps"][1]
    assert "kept existing ICDEV_DATABASE_URL" in env_step["detail"]
    text = (host.root / ".env").read_text(encoding="utf-8")
    assert "postgresql://me@db/icdev" in text and omarchy.DSN not in text


def test_status_on_fresh_host_reports_missing(make_host):
    world = FakeWorld(omarchy_on=False, pg_ready=False, harnesses=())
    states = _states(omarchy.run("status", make_host(world)))
    assert states["mcp"] == "n/a" and states["guard"] == "n/a"
    assert states["postgres"] == "missing" and states["systemd"] == "missing"


def test_systemd_not_applicable_off_linux(make_host):
    world = FakeWorld(omarchy_on=False, pg_ready=True)
    states = _states(omarchy.run("setup", make_host(world, platform="windows"), dry_run=True))
    assert states["systemd"] == "n/a"


def test_systemd_not_applicable_on_a_package_install(make_host):
    """The wheel/AUR root has no scripts/systemd: n/a, not failed (omx-ci-01)."""
    world = FakeWorld(omarchy_on=False, pg_ready=True)
    host = make_host(world)
    for unit in (host.root / "scripts" / "systemd").iterdir():
        unit.unlink()
    (host.root / "scripts" / "systemd").rmdir()
    report = omarchy.run("setup", host, yes=True)
    step = next(s for s in report["steps"] if s["step"] == "systemd")
    assert step["state"] == "n/a" and "source checkout" in step["detail"]
    assert report["exit"] == 0
    # --start asked for something that cannot happen here: that IS a failure.
    started = omarchy.run("setup", host, yes=True, start=True)
    assert _states(started)["systemd"] == "failed" and started["exit"] == 1


def test_systemd_start_refusal_on_a_package_install_says_why(make_host):
    """--start refuses with the documented reason, the one deploy/aur/icdev.install prints."""
    world = FakeWorld(omarchy_on=False, pg_ready=True)
    host = make_host(world)
    for unit in (host.root / "scripts" / "systemd").iterdir():
        unit.unlink()
    (host.root / "scripts" / "systemd").rmdir()
    step = next(s for s in omarchy.run("setup", host, yes=True, start=True)["steps"] if s["step"] == "systemd")
    assert omarchy.GENESIS_NEEDS_CHECKOUT in step["detail"]
    hook = (REPO / "deploy" / "aur" / "icdev.install").read_text(encoding="utf-8")
    assert "needs a source checkout" in hook and "--yes --start" not in hook.split("git clone")[0]


# --------------------------------------------------------------------------- #
# omx-setup-02: a failed schema load is retried, never `skipped`
# --------------------------------------------------------------------------- #

def test_failed_schema_load_is_not_reported_skipped_on_rerun(make_host):
    world = FakeWorld(omarchy_on=False, pg_ready=False)
    world.schema_fails = True
    host = make_host(world)
    first = omarchy.run("setup", host, yes=True)
    assert _states(first)["postgres"] == "failed" and first["exit"] == 1
    assert world.pg_ready and not world.schema  # the extension landed, the schema did not

    again = omarchy.run("setup", host, yes=True)
    assert _states(again)["postgres"] == "failed", "a half-built database must never read as skipped"

    world.schema_fails = False
    assert _states(omarchy.run("setup", host, yes=True))["postgres"] == "done"
    assert _states(omarchy.run("setup", host, yes=True))["postgres"] == "skipped"


def test_ready_probe_requires_the_schema_not_only_the_extension():
    sql = omarchy.READY_SQL
    assert "extname = 'vector'" in sql
    assert "to_regclass('public.schema_migrations') IS NOT NULL" in sql
    assert "to_regclass('public.schema_bootstrap_partial') IS NULL" in sql
    from tools.db.bootstrap_pg import PARTIAL_SENTINEL
    assert PARTIAL_SENTINEL == "schema_bootstrap_partial"


def test_the_schema_snapshot_ships_in_the_wheel():
    """bootstrap_pg reads tools/db/schema/pg_consolidated.sql; package-data must carry it."""
    import fnmatch
    import tomllib
    cfg = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    globs = cfg["tool"]["setuptools"]["package-data"]["icdev.tools.db"]
    for name in ("schema/pg_consolidated.sql", "schema/pg_consolidated.meta.json"):
        assert (REPO / "icdev" / "tools" / "db" / name).is_file(), name
        assert any(fnmatch.fnmatch(name, g) for g in globs), name


# --------------------------------------------------------------------------- #
# omx-setup-02: where .env lives on a PACKAGE install
# --------------------------------------------------------------------------- #

def test_project_root_of_a_source_checkout_is_the_checkout(tmp_path):
    root = tmp_path / "icdev"
    (root / ".git").mkdir(parents=True)
    assert omarchy.project_root(root, {}, tmp_path / "home", tmp_path) == root


def test_project_root_of_a_package_install(tmp_path):
    pkg = tmp_path / "site-packages" / "icdev"          # no source markers
    pkg.mkdir(parents=True)
    home = tmp_path / "home"
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    # Nothing declared, cwd is no project: the user's config dir, never the package.
    assert omarchy.project_root(pkg, {}, home, elsewhere) == home / ".config" / "icdev"
    assert omarchy.project_root(pkg, {"XDG_CONFIG_HOME": str(tmp_path / "xdg")}, home, elsewhere) \
        == tmp_path / "xdg" / "icdev"
    # Inside an `icdev init` project (or below it): that project.
    proj = tmp_path / "proj"
    (proj / "src").mkdir(parents=True)
    for m in omarchy.INIT_PROJECT_MARKERS:
        (proj / m).write_text("x", encoding="utf-8")
    assert omarchy.project_root(pkg, {}, home, proj / "src") == proj
    # $ICDEV_PROJECT_ROOT beats both.
    declared = tmp_path / "declared"
    assert omarchy.project_root(pkg, {"ICDEV_PROJECT_ROOT": str(declared)}, home, proj) == declared.resolve()


def test_package_install_writes_env_to_the_project_not_the_package(make_host):
    world = FakeWorld(omarchy_on=False, pg_ready=True)
    host = make_host(world)
    host.project = host.home / ".config" / "icdev"
    report = omarchy.run("setup", host, yes=True)
    assert report["exit"] == 0, report
    env_step = next(s for s in report["steps"] if s["step"] == "env")
    assert env_step["state"] == "done" and "package install" in env_step["detail"]
    assert "ICDEV_STORAGE_BACKEND=postgresql" in (host.project / ".env").read_text(encoding="utf-8")
    assert not (host.root / ".env").exists()
    assert _states(omarchy.run("setup", host, yes=True))["env"] == "skipped"


def test_uninstall_reverses_4_to_7_and_never_the_database(make_host):
    world = FakeWorld(omarchy_on=True, pg_ready=False)
    host = make_host(world)
    omarchy.run("setup", host, yes=True, start=True)
    calls_before = len(world.calls)

    report = omarchy.run("uninstall", host)
    assert [s["step"] for s in report["steps"]] == list(omarchy.REVERSIBLE)
    assert set(_states(report).values()) == {"done"}, report
    new = [" ".join(c) for c in world.calls[calls_before:]]
    assert not any("dropdb" in c or "DROP" in c for c in new)
    assert "claude" not in world.mcp and not world.timer_on
    oc = json.loads((host.home / ".config" / "opencode" / "opencode.json").read_text(encoding="utf-8"))
    assert "icdev-unified" not in oc["mcp"]
    assert not (host.home / ".pi" / "agent" / "extensions" / "icdev-guard.ts").exists()
    assert not (host.home / ".config" / "systemd" / "user" / "icdev-genesis.timer").exists()
    assert (host.root / ".env").is_file()  # .env is not step 4-7

    again = omarchy.run("uninstall", host)
    assert set(_states(again).values()) == {"skipped"}, again


def test_cli_dispatch_and_json(make_host, capsys):
    world = FakeWorld(omarchy_on=True, pg_ready=True)
    rc = omarchy.main(["setup", "--dry-run", "--json"], host=make_host(world))
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["mode"] == "dry-run" and out["omarchy"] is True


# --------------------------------------------------------------------------- #
# Step 8 -- the agents-panel collector (omx-panel-01)
# --------------------------------------------------------------------------- #

def _assets(host):
    return host.home / ".local" / "share" / "omarchy" / "shell" / "plugins" / "agents" / "assets"


def test_panel_installs_collector_mark_and_timer(make_host):
    world = FakeWorld(omarchy_on=True, pg_ready=True)
    host = make_host(world)
    _assets(host).mkdir(parents=True)  # the agents plugin is installed
    report = omarchy.run("setup", host)
    assert _states(report)["panel"] == "done", report

    collector = host.home / ".local" / "bin" / omarchy.PANEL_BIN
    raw = collector.read_bytes()
    assert raw.startswith(b"#!/usr/bin/python3\n") and b"\r\n" not in raw
    assert f'_INSTALLED_ROOT = "{host.root.as_posix()}"'.encode() in raw
    assert (_assets(host) / "icdev.svg").is_file() and (_assets(host) / "icdev-light.svg").is_file()
    assert (host.home / ".config" / "systemd" / "user" / omarchy.PANEL_TIMER).is_file()
    # Enabled without --start: it only reads.
    assert omarchy.PANEL_TIMER in world.timers and not world.timer_on

    assert _states(omarchy.run("setup", host))["panel"] == "skipped"

    record = host.home / ".local" / "state" / "omarchy" / "agents" / "usage" / "icdev.json"
    record.parent.mkdir(parents=True)
    record.write_text("{}", encoding="utf-8")
    assert _states(omarchy.run("uninstall", host))["panel"] == "done"
    assert not collector.exists() and not record.exists()
    assert not (_assets(host) / "icdev.svg").exists()
    assert omarchy.PANEL_TIMER not in world.timers


def test_panel_without_agents_plugin_skips_the_mark(make_host):
    world = FakeWorld(omarchy_on=True, pg_ready=True)
    host = make_host(world)
    omarchy.run("setup", host)
    assert (host.home / ".local" / "bin" / omarchy.PANEL_BIN).is_file()
    assert not _assets(host).exists()


def test_panel_is_not_applicable_off_omarchy(make_host):
    world = FakeWorld(omarchy_on=False, pg_ready=True)
    host = make_host(world)
    assert _states(omarchy.run("setup", host))["panel"] == "n/a"
    assert not (host.home / ".local" / "bin" / omarchy.PANEL_BIN).exists()
