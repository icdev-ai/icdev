# CUI // SP-CTI
"""xit-decl-01 — the ICDEV[domain] declaration and the process-identity check.

Red-first: ``icdev_domain.yaml`` and ``icdev/core`` do not exist at the merge
base, so every test here fails there.
"""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
import yaml

from icdev.core import context as core_context
from icdev.core import domain as core_domain
from icdev.core import paths as core_paths

REPO_ROOT = Path(__file__).resolve().parents[2]


# --------------------------------------------------------------------------- #
# The checked-in declaration for ICDEV[IT]
# --------------------------------------------------------------------------- #
def test_it_declaration_exists_and_reproduces_todays_constants():
    dom = core_domain.load_domain(REPO_ROOT / "icdev_domain.yaml")
    assert dom.key == "it"
    assert dom.env_prefix == "ICDEV"
    assert dom.source == "file"
    assert dom.root == REPO_ROOT
    assert dom.db.backend == "postgresql"
    assert dom.db.name_env == "ICDEV_PG_DATABASE"
    assert dom.db.dsn_env == "ICDEV_DATABASE_URL"
    # `icdev_e2e` is the throwaway E2E database (qa-fail-6a87916931be3793):
    # declared so a routine local `npx playwright test` does not have to stand
    # the identity guard down. The canonical board stays first.
    assert dom.db.databases == ("icdev", "icdev_e2e")
    assert dom.dashboard_port == 5050
    assert dom.sensitivity.column == "classification"
    assert dom.components == "args/component_registry.yaml"
    assert dom.env("PG_DATABASE") == "ICDEV_PG_DATABASE"


def test_builtin_default_matches_the_checked_in_file():
    """A wheel / scaffolded project gets the builtin default; it must not drift."""
    file_dom = core_domain.load_domain(REPO_ROOT / "icdev_domain.yaml")
    builtin = core_domain.parse_domain(
        core_domain.BUILTIN_DEFAULT, root=REPO_ROOT, source="builtin_default", path=None
    )
    for attr in ("key", "env_prefix", "sensitivity", "dashboard_port",
                 "components", "kanban_board", "mcp_servers", "forge_dirs"):
        assert getattr(builtin, attr) == getattr(file_dom, attr), attr
    # `db` is compared field by field. `databases` is the ONE field the checked-in
    # file may legitimately extend: this tree declares its throwaway E2E database
    # (`icdev_e2e`, qa-fail-6a87916931be3793) beside the canonical board, and a
    # wheel / scaffolded project -- which has no Playwright suite and no fixture
    # writes to isolate -- has no such database to declare. What must not drift
    # is everything else, and the canonical name: the file may ADD a disposable
    # database, never lose or rename the one the builtin names.
    for field in ("backend", "name_env", "dsn_env", "sqlite_path_env", "migrations"):
        assert getattr(builtin.db, field) == getattr(file_dom.db, field), f"db.{field}"
    assert set(builtin.db.databases) <= set(file_dom.db.databases), (
        f"the checked-in file lost a builtin database: {builtin.db.databases} "
        f"vs {file_dom.db.databases}"
    )
    assert file_dom.db.databases[0] == builtin.db.databases[0], "the canonical board must stay first"


def test_declared_paths_exist_in_this_checkout():
    dom = core_domain.load_domain(REPO_ROOT / "icdev_domain.yaml")
    for rel in (dom.components, dom.sensitivity.labels_file, dom.kanban_external_repos,
                *dom.db.migrations, *dom.forge_dirs):
        assert (REPO_ROOT / rel).exists(), rel


# --------------------------------------------------------------------------- #
# Loading rules
# --------------------------------------------------------------------------- #
def _write_domain(root: Path, **overrides) -> Path:
    data = {
        "schema_version": 1,
        "domain": {"key": "ft", "name": "ICDEV[FT]", "env_prefix": "FIN"},
        "db": {"backend": "postgresql", "databases": ["icdev_ft"]},
        "dashboard": {"port": 5200},
    }
    for k, v in overrides.items():
        data[k] = v
    p = root / core_paths.DOMAIN_FILE
    p.write_text(yaml.safe_dump(data), encoding="utf-8")
    return p


def test_key_and_env_prefix_come_from_the_file_not_the_environment(tmp_path, monkeypatch):
    p = _write_domain(tmp_path)
    monkeypatch.setenv("ICDEV_DOMAIN", "it")  # must be ignored: there is no such switch
    dom = core_domain.load_domain(p)
    assert dom.key == "ft" and dom.env_prefix == "FIN"
    # env var names default from the prefix when the file does not spell them out
    assert dom.db.name_env == "FIN_PG_DATABASE"
    assert dom.db.dsn_env == "FIN_DATABASE_URL"
    assert dom.dashboard_port == 5200


def test_missing_file_yields_builtin_default_unless_required(tmp_path, monkeypatch):
    monkeypatch.setenv(core_paths.PROJECT_ROOT_ENV, str(tmp_path))
    monkeypatch.delenv(core_domain.REQUIRE_DOMAIN_ENV, raising=False)
    dom = core_domain.load_domain()
    assert dom.source == "builtin_default" and dom.key == "it" and dom.path is None
    monkeypatch.setenv(core_domain.REQUIRE_DOMAIN_ENV, "1")
    with pytest.raises(core_domain.DomainError):
        core_domain.load_domain()


@pytest.mark.parametrize("bad", [
    {"domain": {"key": "FT", "env_prefix": "FIN"}},          # key must be lowercase
    {"domain": {"key": "ft", "env_prefix": "fin"}},          # prefix must be UPPER
    {"domain": {"key": "ft", "env_prefix": "FIN"}, "dashboard": {"port": 0}},
    {"domain": {"key": "ft", "env_prefix": "FIN"}, "schema_version": 99},
    {"domain": {"key": "ft", "env_prefix": "FIN"}, "db": {"databases": "icdev_ft"}},
])
def test_invalid_declarations_are_refused(tmp_path, bad):
    data = {"schema_version": 1, "db": {"databases": ["x"]}}
    data.update(bad)
    p = tmp_path / core_paths.DOMAIN_FILE
    p.write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(core_domain.DomainError):
        core_domain.load_domain(p)


# --------------------------------------------------------------------------- #
# Root resolution
# --------------------------------------------------------------------------- #
def test_repo_root_prefers_the_calling_files_source_checkout_over_cwd(tmp_path, monkeypatch):
    """The deliberate deviation: a worktree's code must not bind to the cwd's repo."""
    other = tmp_path / "other_parent"
    other.mkdir()
    _write_domain(other)
    monkeypatch.chdir(other)
    monkeypatch.delenv(core_paths.PROJECT_ROOT_ENV, raising=False)
    assert core_paths.repo_root(anchor=__file__) == REPO_ROOT
    assert core_paths.describe(anchor=__file__)["source"] == "source_checkout"


def test_repo_root_uses_cwd_domain_file_for_installed_code(tmp_path, monkeypatch):
    parent = tmp_path / "parent"
    parent.mkdir()
    _write_domain(parent)
    monkeypatch.chdir(parent)
    monkeypatch.delenv(core_paths.PROJECT_ROOT_ENV, raising=False)
    # Pretend the kernel lives in site-packages: the anchor walk is skipped.
    monkeypatch.setattr(core_paths, "_is_installed", lambda p: True)
    assert core_paths.repo_root(anchor=__file__) == parent.resolve()
    assert core_paths.find_domain_file() == (parent / core_paths.DOMAIN_FILE).resolve()


def test_project_root_env_wins(tmp_path, monkeypatch):
    monkeypatch.setenv(core_paths.PROJECT_ROOT_ENV, str(tmp_path))
    assert core_paths.repo_root(anchor=__file__) == tmp_path.resolve()
    assert core_paths.describe(anchor=__file__)["source"] == "env"


def test_legacy_resolvers_are_delegates_and_agree():
    from icdev import _paths as legacy
    from tools.llm.config_path import resolve_llm_config_path

    assert legacy.get_project_root() == core_paths.repo_root()
    assert legacy.get_data_path("args") == core_paths.data_path("args") == REPO_ROOT / "args"
    assert resolve_llm_config_path() == REPO_ROOT / "args" / "llm_config.yaml"


def test_config_path_env_override_then_root_then_packaged(tmp_path, monkeypatch):
    override = tmp_path / "llm.yaml"
    override.write_text("x: 1", encoding="utf-8")
    monkeypatch.setenv("TEST_CFG_ENV", str(override))
    assert core_paths.config_path("args/llm_config.yaml", env="TEST_CFG_ENV") == override.resolve()
    monkeypatch.delenv("TEST_CFG_ENV")
    assert core_paths.config_path("args/llm_config.yaml", anchor=__file__) == REPO_ROOT / "args" / "llm_config.yaml"
    missing = core_paths.config_path("args/does_not_exist.yaml", anchor=__file__, packaged=tmp_path / "pk.yaml")
    assert missing == tmp_path / "pk.yaml"


# --------------------------------------------------------------------------- #
# Identity
# --------------------------------------------------------------------------- #
@pytest.fixture
def it_domain():
    return core_domain.load_domain(REPO_ROOT / "icdev_domain.yaml")


def test_identity_unmeasured_when_no_database_is_named(it_domain):
    rep = core_context.check_identity(domain=it_domain, environ={})
    assert rep.verdict == "unmeasured" and rep.database_observed is None
    core_context.assert_identity(domain=it_domain, environ={})  # must not raise


def test_identity_matches_declared_database_by_name_or_dsn(it_domain):
    rep = core_context.check_identity(domain=it_domain, environ={"ICDEV_PG_DATABASE": "icdev"})
    assert rep.verdict == "match" and rep.database_source == "ICDEV_PG_DATABASE"
    rep = core_context.check_identity(
        domain=it_domain, environ={"ICDEV_DATABASE_URL": "postgresql://u:p@localhost:5432/icdev"}
    )
    assert rep.verdict == "match" and rep.database_source == "ICDEV_DATABASE_URL"


def test_identity_refuses_another_parents_database(it_domain, monkeypatch):
    monkeypatch.delenv(core_context.IDENTITY_GUARD_ENV, raising=False)
    env = {"ICDEV_PG_DATABASE": "icdev_ft"}
    rep = core_context.check_identity(domain=it_domain, environ=env)
    assert rep.verdict == "mismatch" and rep.enforced is True
    with pytest.raises(core_context.IdentityMismatch):
        core_context.assert_identity(domain=it_domain, environ=env)
    # name_env wins over a DSN that happens to agree — storage.py's precedence
    env2 = {"ICDEV_PG_DATABASE": "icdev_ft", "ICDEV_DATABASE_URL": "postgresql://u:p@h/icdev"}
    assert core_context.check_identity(domain=it_domain, environ=env2).verdict == "mismatch"


def test_identity_guard_env_stands_the_refusal_down_but_still_reports(it_domain, monkeypatch):
    monkeypatch.setenv(core_context.IDENTITY_GUARD_ENV, "0")
    rep = core_context.assert_identity(domain=it_domain, environ={"ICDEV_PG_DATABASE": "icdev_ft"})
    assert rep.verdict == "mismatch" and rep.enforced is False


def test_declaration_without_databases_asserts_nothing(tmp_path):
    p = _write_domain(tmp_path, db={"backend": "postgresql"})
    dom = core_domain.load_domain(p)
    rep = core_context.check_identity(domain=dom, environ={"FIN_PG_DATABASE": "anything"})
    assert rep.verdict == "unmeasured"


def test_cli_exit_codes():
    base = [sys.executable, "-m", "icdev.core.context", "--check", "--no-env"]
    env = {k: v for k, v in os.environ.items()
           if k not in ("ICDEV_PG_DATABASE", "ICDEV_DATABASE_URL", "ICDEV_PROJECT_ROOT",
                        core_context.IDENTITY_GUARD_ENV)}
    env["PYTHONIOENCODING"] = "utf-8"
    ok = subprocess.run(base + ["--json"], cwd=REPO_ROOT, env=env, capture_output=True, text=True)
    assert ok.returncode == 0, ok.stderr
    assert '"verdict": "unmeasured"' in ok.stdout
    bad = subprocess.run(base, cwd=REPO_ROOT, env={**env, "ICDEV_PG_DATABASE": "icdev_ft"},
                         capture_output=True, text=True)
    assert bad.returncode == 1, bad.stdout + bad.stderr
    assert "MISMATCH" in bad.stdout


# --------------------------------------------------------------------------- #
# The entry points consume it (declared-but-unconsumed guard)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("rel", [
    "tools/dashboard/app.py", "tools/genesis/daemon.py", "tools/db/migrate.py",
    "tools/kanban/cli.py",
])
def test_entry_points_call_assert_identity(rel):
    src = (REPO_ROOT / rel).read_text(encoding="utf-8")
    assert "assert_identity(" in src, f"{rel} does not consume assert_identity"
    mirror = REPO_ROOT / "icdev" / rel
    if mirror.exists():
        assert "assert_identity(" in mirror.read_text(encoding="utf-8"), f"mirror {rel} drifted"


def test_status_reports_the_domain():
    from tools.cli.enable import _domain_summary

    d = _domain_summary()
    assert d.get("key") == "it" and d.get("source") == "file"
    assert d.get("identity") in ("match", "unmeasured")


def test_core_package_is_stdlib_plus_yaml_only():
    """The seam must be importable before tools.db.storage, from either namespace.

    Read from the INSTALLED distribution since xcore-cut-02 — this parent no longer ships
    ``icdev/core/``. That is the stronger check: the property has to hold for the core this
    parent actually resolves, not for a copy it happened to carry. icdev-core's own CI
    re-derives it on every core change, which is where a violation would originate.
    """
    from tools.workflow.core_api_manifest import module_source

    src = "\n".join(
        module_source(f"icdev.core.{m}").read_text(encoding="utf-8")
        for m in ("paths", "domain", "context")
    )
    for banned in ("from tools.", "import tools", "icdev.tools", "flask", "psycopg", "sqlite3"):
        assert banned not in textwrap.dedent(src), banned
