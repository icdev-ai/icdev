#!/usr/bin/env python3
"""`icdev setup` — the guided post-install walk-through. CUI // SP-CTI.

`pip install icdev` installs the package and `icdev init` copies the payload
out. Neither told you which LLM to use, which database to point at, or how to
map a volume into a container on your OS.

The two things people actually got wrong were a Windows Docker volume path and
an LLM key that silently did not work, so those are what these tests pin
hardest.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tools.cli import setup_wizard as sw


# --------------------------------------------------------------------------- #
# Docker volume mapping — the highest-friction step
# --------------------------------------------------------------------------- #


def test_windows_volume_path_uses_forward_slashes():
    """Docker Desktop rejects the backslashes `os.path` produces.

    This is the single most common Windows setup failure, and it is silent:
    the container starts and the mount is simply empty.
    """
    env = sw.Environment(os_name="Windows", is_windows=True)
    got = sw.compose_volume_path(Path("C:/ai/myproj"), env)
    assert "\\" not in got
    assert got.endswith("/data")
    assert got[1:2] == ":"          # keeps the drive letter


def test_wsl_uses_linux_rules_not_windows():
    """WSL reports as Linux but users think of it as Windows.

    Emitting a `C:/...` bind mount there produces a mount that silently does
    not resolve.
    """
    env = sw.Environment(os_name="Linux", is_wsl=True)
    assert sw.compose_volume_path(Path("/home/u/proj"), env) == "./data"


@pytest.mark.parametrize("os_name", ["Linux", "Darwin"])
def test_posix_hosts_use_a_relative_path(os_name):
    env = sw.Environment(os_name=os_name)
    assert sw.compose_volume_path(Path("/home/u/proj"), env) == "./data"


# --------------------------------------------------------------------------- #
# Compose content
# --------------------------------------------------------------------------- #


def test_compose_uses_pgvector_not_stock_postgres():
    """ICDEV stores embeddings in a `vector` column.

    A plain `postgres:16` image cannot host the RAG schema at all, so this is a
    correctness requirement, not a preference.
    """
    out = sw.render_compose(sw.Environment(os_name="Linux"),
                            use_postgres=True, project_dir=Path("/p"))
    assert "pgvector/pgvector" in out
    assert "image: postgres:" not in out


def test_compose_points_the_app_at_the_service_name():
    """Inside the compose network, `localhost` is the icdev container itself.

    A DSN pointing at localhost is the second-most-common container mistake.
    """
    out = sw.render_compose(sw.Environment(os_name="Linux"),
                            use_postgres=True, project_dir=Path("/p"))
    assert "@postgres:5432" in out
    assert "@localhost:5432" not in out


def test_sqlite_compose_has_no_database_service():
    out = sw.render_compose(sw.Environment(os_name="Linux"),
                            use_postgres=False, project_dir=Path("/p"))
    assert "pgvector" not in out
    assert "ICDEV_STORAGE_BACKEND: sqlite" in out


def test_compose_waits_for_a_healthy_database():
    """Starting the app before PG accepts connections fails the first migration."""
    out = sw.render_compose(sw.Environment(os_name="Linux"),
                            use_postgres=True, project_dir=Path("/p"))
    assert "healthcheck" in out
    assert "service_healthy" in out


def test_compose_is_valid_yaml():
    yaml = pytest.importorskip("yaml")
    out = sw.render_compose(sw.Environment(os_name="Windows", is_windows=True),
                            use_postgres=True, project_dir=Path("C:/ai/p"))
    doc = yaml.safe_load(out)
    assert "services" in doc
    assert {"postgres", "icdev"} <= set(doc["services"])


# --------------------------------------------------------------------------- #
# .env writing — must not destroy a working file
# --------------------------------------------------------------------------- #


def test_update_env_preserves_comments_and_unrelated_keys(tmp_path):
    """`icdev init` writes extensive commentary into .env.

    Rewriting the file from a dict would discard the explanations that are the
    only in-place documentation of what each flag does.
    """
    f = tmp_path / ".env"
    f.write_text("# explains a flag\nKEEP=1\nICDEV_LLM_PROVIDER=old\n", encoding="utf-8")
    sw.update_env(f, {"ICDEV_LLM_PROVIDER": "anthropic"})
    text = f.read_text(encoding="utf-8")
    assert "# explains a flag" in text
    assert "KEEP=1" in text
    assert "ICDEV_LLM_PROVIDER=anthropic" in text
    assert "ICDEV_LLM_PROVIDER=old" not in text


def test_update_env_appends_new_keys(tmp_path):
    f = tmp_path / ".env"
    f.write_text("EXISTING=1\n", encoding="utf-8")
    sw.update_env(f, {"BRAND_NEW": "yes"})
    assert "BRAND_NEW=yes" in f.read_text(encoding="utf-8")


def test_update_env_dry_run_writes_nothing(tmp_path):
    f = tmp_path / ".env"
    f.write_text("A=1\n", encoding="utf-8")
    sw.update_env(f, {"A": "2"}, dry_run=True)
    assert f.read_text(encoding="utf-8") == "A=1\n"


def test_update_env_creates_the_file_when_absent(tmp_path):
    f = tmp_path / "sub" / ".env"
    sw.update_env(f, {"A": "1"})
    assert f.is_file()


def test_read_env_ignores_comments_and_quotes(tmp_path):
    f = tmp_path / ".env"
    f.write_text('# c\nA="v"\nB=\'w\'\n\n', encoding="utf-8")
    assert sw.read_env(f) == {"A": "v", "B": "w"}


# --------------------------------------------------------------------------- #
# LLM chain
# --------------------------------------------------------------------------- #


def test_llm_updates_write_both_slots():
    """An explicit fallback equal to the primary means 'no fallback'.

    An EMPTY one reads as 'not configured yet' — a different thing.
    """
    out = sw.llm_env_updates("anthropic", "anthropic", {})
    assert out["ICDEV_LLM_PROVIDER"] == "anthropic"
    assert out["ICDEV_LLM_FALLBACK_PROVIDER"] == "anthropic"


def test_ollama_gets_a_base_url_by_default():
    out = sw.llm_env_updates("ollama", "ollama", {})
    assert out["OLLAMA_BASE_URL"].startswith("http")


def test_blank_api_keys_are_not_written():
    """Writing `ANTHROPIC_API_KEY=` would look configured and fail at first use."""
    out = sw.llm_env_updates("anthropic", "ollama", {"ANTHROPIC_API_KEY": ""})
    assert "ANTHROPIC_API_KEY" not in out


def test_probe_reports_a_missing_key_without_network(monkeypatch):
    """No key means no probe — do not spend a timeout proving the obvious."""
    monkeypatch.setattr(sw, "_port_open", lambda *a, **k: pytest.fail("should not dial"))
    r = sw.probe_provider(sw.provider_by_key("anthropic"), {})
    assert not r["ok"]
    assert "ANTHROPIC_API_KEY" in r["detail"]


def test_probe_of_a_local_provider_checks_the_port(monkeypatch):
    monkeypatch.setattr(sw, "_port_open", lambda *a, **k: True)
    r = sw.probe_provider(sw.provider_by_key("ollama"), {})
    assert r["ok"]


def test_every_provider_is_probeable():
    """A provider offered in the menu must have a defined probe outcome."""
    for p in sw.PROVIDERS:
        r = sw.probe_provider(p, {p.env_key: "x"} if p.env_key else {})
        assert set(r) == {"ok", "detail"}


# --------------------------------------------------------------------------- #
# Database + RAG defaults
# --------------------------------------------------------------------------- #


def test_postgres_updates_carry_a_dsn():
    out = sw.db_env_updates("postgresql", dsn="postgresql://u:p@h:5432/d")
    assert out["ICDEV_STORAGE_BACKEND"] == "postgresql"
    assert out["ICDEV_DATABASE_URL"].startswith("postgresql://")


def test_sqlite_updates_carry_a_path():
    out = sw.db_env_updates("sqlite", db_path="/p/data/icdev.db")
    assert out["ICDEV_DB_PATH"].endswith("icdev.db")
    assert "ICDEV_DATABASE_URL" not in out


def test_embedding_dimension_defaults_to_the_airgap_safe_value():
    """768 matches nomic / gemini-004 / ibm-slate; 1536 matches only cloud OpenAI.

    Defaulting to 1536 would make an air-gapped install need a schema migration
    before RAG worked at all.
    """
    assert sw.rag_env_updates(enabled=True)["ICDEV_EMBEDDING_DIM"] == "768"


# --------------------------------------------------------------------------- #
# OS guidance + detection
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("env,expect", [
    (sw.Environment(os_name="Windows", is_windows=True, docker=True), "forward slashes"),
    (sw.Environment(os_name="Linux", is_wsl=True, docker=True), "LINUX rules"),
    (sw.Environment(os_name="Darwin", docker=True), "File Sharing"),
    (sw.Environment(os_name="Linux", docker=True), "docker group"),
])
def test_each_os_gets_specific_guidance(env, expect):
    assert any(expect in t for t in sw.os_guidance(env))


def test_missing_docker_steers_to_sqlite():
    tips = sw.os_guidance(sw.Environment(os_name="Linux", docker=False))
    assert any("SQLite" in t for t in tips)


def test_detection_never_raises():
    assert isinstance(sw.detect_environment(Path(".")), sw.Environment)


def test_json_mode_is_non_interactive(capsys):
    import json as _json

    assert sw.main(["--json"]) == 0
    _json.loads(capsys.readouterr().out)


def test_dry_run_writes_no_env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    sw.main(["--non-interactive", "--no-probe", "--dry-run",
             "--env-file", str(tmp_path / ".env")])
    assert not (tmp_path / ".env").exists()
