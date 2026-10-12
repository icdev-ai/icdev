# CUI // SP-CTI
"""`icdev omarchy setup --postgres docker` (omx-host-02) -- no real docker anywhere.

The fake daemon keeps STATE (container status, the image it runs, whether the
extension and schema landed), so idempotency is asserted as "the second run is
`skipped` and issued no docker run", not as "it printed something".
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from tests.test_omarchy_setup import FakeWorld, make_host  # noqa: F401 -- make_host is a fixture
from tools.cli import omarchy
from tools.cli.omarchy import Result

REPO = Path(__file__).resolve().parents[1]
PINNED = yaml.safe_load((REPO / "args" / omarchy.PG_CONFIG).read_text(encoding="utf-8"))["image"]
REFUSED = "permission denied while trying to connect to the Docker daemon socket at unix:///var/run/docker.sock"


class DockerWorld(FakeWorld):
    def __init__(self, *, daemon_ok=True, pg_ready=False, **kw):
        super().__init__(omarchy_on=True, pg_ready=pg_ready, **kw)
        self.bins.add("docker")
        self.daemon_ok = daemon_ok
        self.container = None            # {"status": ..., "image": ...}
        self.ext = self.schema_loaded = False
        self.envs: list[dict] = []        # the env= handed to each command

    def run(self, argv, env=None):
        argv = list(argv)
        if env:
            self.envs.append(dict(env))
        if argv[0] != "docker":
            if "icdev.tools.db.bootstrap_pg" in " ".join(argv) and env and "@127.0.0.1" in \
                    env.get("ICDEV_DATABASE_URL", ""):
                self.calls.append(argv)
                self.schema_loaded = True
                return Result(0)
            return super().run(argv, env)
        self.calls.append(argv)
        if argv[1] == "info":
            return Result(0, "28.5.1\n") if self.daemon_ok else Result(1, "", REFUSED)
        if argv[1] == "inspect":
            if self.container is None:
                return Result(1, "", "Error: No such object: icdev-postgres")
            return Result(0, f"{self.container['status']}|{self.container['image']}\n")
        if argv[1] == "run":
            self.container = {"status": "running", "image": argv[-1]}
            return Result(0, "abc123\n")
        if argv[1] == "start":
            self.container["status"] = "running"
            return Result(0)
        if argv[1] == "exec":
            up = self.container is not None and self.container["status"] == "running"
            if "pg_isready" in argv:
                return Result(0 if up else 2)
            if "CREATE EXTENSION IF NOT EXISTS vector" in argv:
                self.ext = True
                return Result(0)
            if omarchy.READY_SQL in argv:
                return Result(0, "1\n" if up and self.ext and self.schema_loaded else "")
        return Result(0)


@pytest.fixture
def make_dhost(make_host):  # noqa: F811
    """make_host plus the args file the docker step reads."""
    def _make(world, platform="linux"):
        host = make_host(world, platform)
        cfg = host.root / "args" / omarchy.PG_CONFIG
        cfg.write_bytes((REPO / "args" / omarchy.PG_CONFIG).read_bytes())
        return host
    return _make


def _step(report, name):
    return next(s for s in report["steps"] if s["step"] == name)


def _docker_calls(world, verb):
    return [c for c in world.calls if c[:2] == ["docker", verb]]


def _env(host):
    from tools.cli.setup_wizard import read_env
    return read_env(host.env_file)


# --------------------------------------------------------------------------- #
# auto: never migrate a working host
# --------------------------------------------------------------------------- #

def test_auto_keeps_a_healthy_native_install_even_when_docker_answers(make_dhost):
    world = DockerWorld(pg_ready=True)
    host = make_dhost(world)
    report = omarchy.run("setup", host, yes=True)
    assert report["postgres"]["mode"] == "native", report["postgres"]
    assert "healthy native" in report["postgres"]["reason"]
    assert _step(report, "postgres")["state"] == "skipped"
    assert not _docker_calls(world, "run") and world.container is None
    assert _env(host)[omarchy.PG_MODE_ENV] == "native"


def test_auto_picks_docker_when_it_answers_and_no_native_exists(make_dhost):
    world = DockerWorld()
    host = make_dhost(world)
    report = omarchy.run("setup", host)            # no --yes: docker mode needs no root
    assert report["postgres"]["mode"] == "docker", report["postgres"]
    assert _step(report, "postgres")["state"] == "done", report
    assert report["exit"] == 0, report
    assert world.ext and world.schema_loaded
    env = _env(host)
    assert env[omarchy.PG_MODE_ENV] == "docker"
    assert env["ICDEV_STORAGE_BACKEND"] == "postgresql"
    assert env["ICDEV_PG_HOST"] == "127.0.0.1" and env["ICDEV_PG_DATABASE"] == "icdev"
    assert env["ICDEV_PG_USER"] == "icdev" and env["ICDEV_PG_PORT"] == "5432"
    assert len(env[omarchy.PG_PASSWORD_ENV]) >= 32
    assert "ICDEV_DATABASE_URL" not in env
    # Never sudo for docker.
    assert not [c for c in world.calls if c[0] == "sudo"]


def test_auto_follows_the_recorded_mode_not_the_probes(make_dhost):
    world = DockerWorld()
    host = make_dhost(world)
    host.env_file.write_text(f"{omarchy.PG_MODE_ENV}=native\n", encoding="utf-8")
    report = omarchy.run("setup", host, dry_run=True)
    assert report["postgres"]["mode"] == "native" and "recorded" in report["postgres"]["reason"]
    assert not _docker_calls(world, "run")


def test_auto_without_docker_is_native_as_before(make_dhost):
    world = FakeWorld(omarchy_on=False, pg_ready=False)
    report = omarchy.run("setup", make_dhost(world), dry_run=True)
    assert report["postgres"]["mode"] == "native"
    assert "sudo pacman" in "\n".join(_step(report, "postgres")["commands"])


# --------------------------------------------------------------------------- #
# the container itself
# --------------------------------------------------------------------------- #

def test_docker_run_binds_loopback_only_and_uses_the_digest_from_args(make_dhost):
    world = DockerWorld()
    omarchy.run("setup", make_dhost(world), postgres="docker")
    [run] = _docker_calls(world, "run")
    assert run[-1] == PINNED and "@sha256:" in PINNED
    assert run[run.index("-p") + 1] == "127.0.0.1:5432:5432"
    assert not any("0.0.0.0" in a for a in run)
    assert run[run.index("--restart") + 1] == "unless-stopped"
    assert run[run.index("--name") + 1] == "icdev-postgres"
    assert run[run.index("-v") + 1] == "icdev-postgres-data:/var/lib/postgresql/data"
    assert "POSTGRES_DB=icdev" in run
    # The image literal lives in args, never in the Python.
    assert "sha256:" not in (REPO / "tools" / "cli" / "omarchy.py").read_text(encoding="utf-8")


def test_password_is_never_in_an_argv_or_the_output(make_dhost, capsys):
    world = DockerWorld()
    host = make_dhost(world)
    assert omarchy.main(["setup", "--postgres", "docker"], host=host) == 0
    text_out = capsys.readouterr().out
    omarchy.main(["setup", "--postgres", "docker", "--json"], host=host)
    omarchy.main(["status", "--json"], host=host)
    out = text_out + capsys.readouterr().out
    pw = _env(host)[omarchy.PG_PASSWORD_ENV]
    assert pw and pw not in out
    assert not [c for c in world.calls if any(pw in a for a in c)]
    # Handed to docker through the environment: `-e POSTGRES_PASSWORD` carries no value.
    [run] = _docker_calls(world, "run")
    assert run[run.index("POSTGRES_PASSWORD") - 1] == "-e"
    assert {"POSTGRES_PASSWORD": pw} in world.envs


def test_dry_run_prints_the_plan_and_changes_nothing(make_dhost):
    world = DockerWorld()
    host = make_dhost(world)
    report = omarchy.run("setup", host, postgres="docker", dry_run=True)
    pg = _step(report, "postgres")
    assert pg["state"] == "planned"
    assert any(c.startswith("docker run") and "127.0.0.1:5432:5432" in c for c in pg["commands"])
    assert not _docker_calls(world, "run") and not host.env_file.exists()


def test_rerun_is_idempotent_and_status_names_the_mode(make_dhost):
    world = DockerWorld()
    host = make_dhost(world)
    assert omarchy.run("setup", host, postgres="docker")["exit"] == 0
    pw = _env(host)[omarchy.PG_PASSWORD_ENV]

    again = omarchy.run("setup", host)              # auto: follows the recorded mode
    assert again["postgres"]["mode"] == "docker"
    assert _step(again, "postgres")["state"] == "skipped", again
    assert _step(again, "env")["state"] == "skipped", again
    assert len(_docker_calls(world, "run")) == 1
    assert _env(host)[omarchy.PG_PASSWORD_ENV] == pw   # never regenerated

    status = omarchy.run("status", host)
    pg = _step(status, "postgres")
    assert pg["state"] == "ok" and pg["detail"].startswith("docker:")
    world.container = None
    pg = _step(omarchy.run("status", host), "postgres")
    assert pg["state"] == "missing" and pg["detail"].startswith("docker:")


def test_a_stopped_container_is_started_not_recreated(make_dhost):
    world = DockerWorld()
    host = make_dhost(world)
    omarchy.run("setup", host, postgres="docker")
    world.container["status"] = "exited"
    report = omarchy.run("setup", host)
    assert _step(report, "postgres")["state"] == "done", report
    assert _docker_calls(world, "start") == [["docker", "start", "icdev-postgres"]]
    assert len(_docker_calls(world, "run")) == 1
    assert world.container["status"] == "running"


def test_a_different_digest_is_reported_not_replaced(make_dhost):
    world = DockerWorld()
    host = make_dhost(world)
    omarchy.run("setup", host, postgres="docker")
    old = "pgvector/pgvector@sha256:" + "0" * 64
    world.container["image"] = old
    pg = _step(omarchy.run("setup", host), "postgres")
    assert pg["state"] == "skipped"
    assert old in pg["detail"] and PINNED in pg["detail"] and "NOT replaced" in pg["detail"]
    assert world.container["image"] == old
    assert not [c for c in world.calls if c[:2] in (["docker", "rm"], ["docker", "stop"])]


def test_existing_container_without_a_password_fails_with_the_remedy(make_dhost):
    world = DockerWorld()
    host = make_dhost(world)
    world.container = {"status": "running", "image": PINNED}
    report = omarchy.run("setup", host, postgres="docker")
    pg = _step(report, "postgres")
    assert pg["state"] == "failed" and omarchy.PG_PASSWORD_ENV in pg["detail"]
    assert not _docker_calls(world, "run")


def test_daemon_refusal_fails_with_the_remedy_and_writes_nothing(make_dhost):
    world = DockerWorld(daemon_ok=False)
    host = make_dhost(world)
    report = omarchy.run("setup", host, postgres="docker")
    pg = _step(report, "postgres")
    assert pg["state"] == "failed" and report["exit"] == 1
    assert "permission denied" in pg["detail"] and "docker group" in pg["detail"]
    assert _step(report, "env")["state"] == "pending"
    assert not host.env_file.exists()
    assert [c[1] for c in world.calls if c[0] == "docker"] == ["info"]
    assert not [c for c in world.calls if c[0] == "sudo"]


def test_auto_with_a_refusing_daemon_falls_back_to_native(make_dhost):
    world = DockerWorld(daemon_ok=False)
    report = omarchy.run("setup", make_dhost(world), dry_run=True)
    assert report["postgres"]["mode"] == "native"


def test_readiness_wait_times_out_with_a_pointer_to_the_logs(make_dhost, monkeypatch):
    world = DockerWorld()
    monkeypatch.setattr(omarchy, "_sleep", lambda s: None)
    with pytest.raises(TimeoutError, match="docker logs icdev-postgres"):
        omarchy._wait_ready(make_dhost(world), "icdev-postgres", 0)


# --------------------------------------------------------------------------- #
# uninstall, platforms, config
# --------------------------------------------------------------------------- #

def test_uninstall_leaves_the_container_and_volume(make_dhost, capsys):
    world = DockerWorld()
    host = make_dhost(world)
    omarchy.run("setup", host, postgres="docker")
    before = len(world.calls)
    report = omarchy.run("uninstall", host)
    assert "postgres" not in {s["step"] for s in report["steps"]}
    assert not [c for c in world.calls[before:] if c[0] == "docker"]
    assert any("container and volume" in k for k in report["kept"])
    assert world.container == {"status": "running", "image": PINNED}
    assert _env(host)[omarchy.PG_PASSWORD_ENV]


def test_docker_mode_is_not_applicable_on_windows(make_dhost):
    world = DockerWorld()
    host = make_dhost(world, platform="windows")
    report = omarchy.run("setup", host, postgres="docker")
    assert _step(report, "postgres")["state"] == "n/a"
    assert _step(report, "env")["state"] == "pending"
    assert not _docker_calls(world, "run") and not host.env_file.exists()
    # auto never picks docker off Linux.
    assert omarchy.run("setup", host, dry_run=True)["postgres"]["mode"] == "native"


def test_config_refuses_a_tag_and_a_public_bind(make_dhost):
    world = DockerWorld()
    host = make_dhost(world)
    cfg_path = host.root / "args" / omarchy.PG_CONFIG
    cfg = yaml.safe_load((REPO / "args" / omarchy.PG_CONFIG).read_text(encoding="utf-8"))
    assert omarchy.pg_docker_config(host)["image"] == PINNED
    cfg_path.write_text(yaml.safe_dump({**cfg, "image": "pgvector/pgvector:pg16"}), encoding="utf-8")
    with pytest.raises(ValueError, match="TAG"):
        omarchy.pg_docker_config(host)
    cfg_path.write_text(yaml.safe_dump({**cfg, "bind": "0.0.0.0"}), encoding="utf-8")
    with pytest.raises(ValueError, match="loopback"):
        omarchy.pg_docker_config(host)
    report = omarchy.run("setup", host, postgres="docker")
    assert _step(report, "postgres")["state"] == "failed" and not _docker_calls(world, "run")


def test_the_packaged_config_matches_the_source_one():
    src = (REPO / "args" / omarchy.PG_CONFIG).read_bytes()
    assert (REPO / "icdev" / "data" / "args" / omarchy.PG_CONFIG).read_bytes() == src


def test_cli_accepts_postgres_choices(make_dhost, capsys):
    world = DockerWorld()
    assert omarchy.main(["setup", "--postgres", "docker", "--dry-run", "--json"], host=make_dhost(world)) == 0
    assert json.loads(capsys.readouterr().out)["postgres"]["mode"] == "docker"
    with pytest.raises(SystemExit):
        omarchy.main(["setup", "--postgres", "podman"], host=make_dhost(world))
