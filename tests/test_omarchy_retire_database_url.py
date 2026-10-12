# CUI // SP-CTI
"""`icdev omarchy setup --postgres docker` retires a stale ICDEV_DATABASE_URL (omx-host-04).

ICDEV_DATABASE_URL OUTRANKS ICDEV_PG_*, so a docker setup that leaves one in
.env points ICDEV at a database that is not the container -- on the laptop
(omx-host-01) the container's port with no password, so every connection
failed auth while the env step said `ok`. Setup now backs .env up and comments
the line out; `status` reports it `failed`; neither ever prints the value.
"""
# ruff: noqa: F811 -- pytest fixtures imported from the sibling suites, then requested by name
from __future__ import annotations

import os
import stat

import pytest

from tests.test_omarchy_postgres_docker import DockerWorld, make_dhost  # noqa: F401 -- fixture
from tests.test_omarchy_setup import FakeWorld, make_host  # noqa: F401 -- fixture
from tools.cli import omarchy
from tools.cli.setup_wizard import read_env

STALE = "postgresql://icdev:s3cret-elsewhere@db.example.internal:5433/icdev"


def _step(report, name):
    return next(s for s in report["steps"] if s["step"] == name)


def _backups(host):
    return sorted(host.env_file.parent.glob(".env.bak-*"))


def _seed(host, url=STALE, extra=""):
    host.env_file.parent.mkdir(parents=True, exist_ok=True)
    host.env_file.write_text(f"# operator notes\nICDEV_DATABASE_URL={url}\nOTHER=1\n{extra}",
                             encoding="utf-8")
    return host.env_file.read_text(encoding="utf-8")


def test_docker_setup_backs_up_and_comments_out_a_stale_url(make_dhost, capsys):
    world = DockerWorld()
    host = make_dhost(world)
    original = _seed(host)

    assert omarchy.main(["setup", "--postgres", "docker"], host=host) == 0
    out = capsys.readouterr().out

    [bak] = _backups(host)
    # The postgres step wrote the generated password first; the backup is .env as
    # the env step found it, URL line intact.
    assert bak.read_text(encoding="utf-8").startswith(original)
    assert oct(bak.stat().st_mode) == oct(host.env_file.stat().st_mode)
    text = host.env_file.read_text(encoding="utf-8")
    [retired] = [ln for ln in text.splitlines() if "ICDEV_DATABASE_URL" in ln]
    assert retired.startswith("# retired by icdev omarchy setup ")
    assert retired.endswith(f"(postgres -> docker): ICDEV_DATABASE_URL={STALE}")
    env = read_env(host.env_file)
    assert "ICDEV_DATABASE_URL" not in env
    assert env["ICDEV_PG_HOST"] == "127.0.0.1" and env["ICDEV_PG_DATABASE"] == "icdev"
    assert env["OTHER"] == "1" and text.startswith("# operator notes\n")
    # Name the key and the backup; never the value (it may carry a password).
    assert "ICDEV_DATABASE_URL" in out and bak.name in out
    assert STALE not in out and "s3cret-elsewhere" not in out


def test_dry_run_lists_the_retirement_and_changes_nothing(make_dhost):
    host = make_dhost(DockerWorld())
    original = _seed(host)
    report = omarchy.run("setup", host, postgres="docker", dry_run=True)
    env = _step(report, "env")
    assert env["state"] == "planned"
    assert any("retire ICDEV_DATABASE_URL" in a for a in env["actions"]), env
    assert STALE not in str(report)
    assert host.env_file.read_text(encoding="utf-8") == original
    assert not _backups(host)


def test_rerun_after_retirement_is_ok_with_no_second_backup(make_dhost):
    world = DockerWorld()
    host = make_dhost(world)
    _seed(host)
    assert omarchy.run("setup", host, postgres="docker")["exit"] == 0
    after = host.env_file.read_text(encoding="utf-8")

    again = omarchy.run("setup", host)
    assert _step(again, "env")["state"] == "skipped", again
    assert len(_backups(host)) == 1
    assert host.env_file.read_text(encoding="utf-8") == after
    assert _step(omarchy.run("status", host), "env")["state"] == "ok"


@pytest.mark.parametrize("hostname", ["127.0.0.1", "localhost", "[::1]"])
def test_a_url_already_pointing_at_the_container_is_left_alone(make_dhost, hostname):
    world = DockerWorld()
    host = make_dhost(world)
    host.env_file.write_text(f"{omarchy.PG_PASSWORD_ENV}=pw123\n", encoding="utf-8")
    url = f"postgresql://icdev:pw123@{hostname}:5432/icdev"
    host.env_file.write_text(f"{omarchy.PG_PASSWORD_ENV}=pw123\nICDEV_DATABASE_URL={url}\n",
                             encoding="utf-8")
    report = omarchy.run("setup", host, postgres="docker")
    assert _step(report, "env")["state"] in ("done", "skipped"), report
    assert read_env(host.env_file)["ICDEV_DATABASE_URL"] == url
    assert not _backups(host)


def test_a_url_with_the_wrong_password_is_still_stale(make_dhost):
    host = make_dhost(DockerWorld())
    _seed(host, url="postgresql://icdev@localhost:5432/icdev")   # the laptop's line: no password
    assert omarchy.run("setup", host, postgres="docker")["exit"] == 0
    assert "ICDEV_DATABASE_URL" not in read_env(host.env_file)
    assert len(_backups(host)) == 1


def test_native_mode_never_touches_the_url(make_host):
    world = FakeWorld(omarchy_on=False, pg_ready=True)
    host = make_host(world)
    original = _seed(host)
    report = omarchy.run("setup", host, postgres="native", yes=True)
    assert _step(report, "env")["state"] in ("done", "skipped"), report
    assert read_env(host.env_file)["ICDEV_DATABASE_URL"] == STALE
    assert host.env_file.read_text(encoding="utf-8").startswith(original)
    assert not _backups(host)


def test_status_with_a_stale_url_is_failed_with_the_remedy(make_dhost, capsys):
    world = DockerWorld()
    host = make_dhost(world)
    assert omarchy.run("setup", host, postgres="docker")["exit"] == 0
    with host.env_file.open("a", encoding="utf-8") as fh:
        fh.write(f"ICDEV_DATABASE_URL={STALE}\n")
    before = host.env_file.read_text(encoding="utf-8")

    report = omarchy.run("status", host)
    env = _step(report, "env")
    assert env["state"] == "failed" and report["exit"] == 1
    assert "ICDEV_DATABASE_URL" in env["detail"] and "omarchy setup" in env["detail"]
    assert STALE not in str(report)
    assert host.env_file.read_text(encoding="utf-8") == before and not _backups(host)
    omarchy.main(["status"], host=host)
    assert STALE not in capsys.readouterr().out


def test_an_unwritable_env_fails_the_step_never_ok(make_dhost):
    world = DockerWorld()
    host = make_dhost(world)
    assert omarchy.run("setup", host, postgres="docker")["exit"] == 0
    with host.env_file.open("a", encoding="utf-8") as fh:
        fh.write(f"ICDEV_DATABASE_URL={STALE}\n")
    os.chmod(host.env_file, stat.S_IREAD)
    try:
        report = omarchy.run("setup", host)
    finally:
        os.chmod(host.env_file, stat.S_IREAD | stat.S_IWRITE)
    env = _step(report, "env")
    assert env["state"] == "failed" and report["exit"] == 1, report
    assert "ICDEV_DATABASE_URL" in env["error"] and "comment" in env["error"]
    assert STALE not in str(report)
    assert read_env(host.env_file)["ICDEV_DATABASE_URL"] == STALE
