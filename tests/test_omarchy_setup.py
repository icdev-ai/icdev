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
        self.pg_ready = pg_ready
        self.pg_running = pg_ready
        self.mcp = set()
        self.timer_on = False
        self.calls: list[list[str]] = []

    def which(self, name):
        return f"/usr/bin/{name}" if name in self.bins else None

    def run(self, argv, env=None):
        argv = list(argv)
        self.calls.append(argv)
        joined = " ".join(argv)
        if argv[0] == "psql" and "pg_extension" in joined:
            return Result(0, "1\n") if self.pg_ready else Result(2, "", "connection refused")
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
                return Result(0 if self.timer_on else 1)
            if argv[2:4] == ["enable", "--now"]:
                self.timer_on = True
            if argv[2:4] == ["disable", "--now"]:
                self.timer_on = False
            return Result(0)
        return Result(0)  # sudo -u postgres psql/createdb, icdev-init-db


@pytest.fixture
def make_host(tmp_path, monkeypatch):
    home = tmp_path / "home"
    root = tmp_path / "root"
    home.mkdir()
    # The checkout pieces the steps read: unit templates, llm config.
    for rel in ("scripts/systemd/icdev-genesis.service", "scripts/systemd/icdev-genesis.timer",
                "args/llm_config.yaml"):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes((REPO / rel).read_bytes())
    environ = {"XDG_CONFIG_HOME": str(home / ".config"),
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
    pg = report["steps"][0]
    if not pg_ready:
        cmds = "\n".join(pg["commands"])
        assert "sudo pacman -S --needed --noconfirm postgresql pgvector" in cmds
        assert "initdb" in cmds and "createdb -O icdev icdev" in cmds
        assert "CREATE EXTENSION IF NOT EXISTS vector" in cmds
        assert "icdev.tools.db.init_icdev_db" in cmds
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
