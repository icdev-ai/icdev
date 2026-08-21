# CUI // SP-CTI
"""xit-decl-04 -- schema ownership manifests and the RLS catalog exemption.

Red-first: the tool, the rules, the manifests and the narrowed exemption do not
exist at the merge base.
"""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
import yaml

from tools.db import schema_ownership as so

REPO_ROOT = Path(__file__).resolve().parents[2]


# --------------------------------------------------------------------------- #
# rules -> owner
# --------------------------------------------------------------------------- #
def test_rules_resolve_known_tables():
    rules = so.load_rules(REPO_ROOT)
    assert so.assign_owner("kanban_tasks", set(), rules)[0] == "core"
    assert so.assign_owner("ad_positions", set(), rules)[0] == "ft"
    assert so.assign_owner("compliance_controls", set(), rules)[0] == "it"
    assert so.assign_owner("schema_migrations", set(), rules)[0] == "core"
    # declaring package decides when no prefix matches
    assert so.assign_owner("zzz_unknown", {"tools/trading/db.py"}, rules) == ("ft", "package:trading")
    assert so.assign_owner("zzz_unknown", {"tools/network/db/init_db.py"}, rules)[0] == "it"
    assert so.assign_owner("zzz_unknown", {"tools/db/migrations/001_x/up.sql"}, rules)[1] == "default"


def test_checked_in_manifests_are_fresh_complete_and_disjoint():
    rep = so.build_report(REPO_ROOT)
    assert rep["unowned"] == [], rep["unowned"][:10]
    assert rep["duplicates"] == []
    assert rep["stale"] == [], rep["stale"][:10]
    assert rep["foreign_owner_touched"] == []
    assert rep["ok"] is True
    assert rep["owners"]["core"] > 500 and rep["owners"]["it"] > 500 and rep["owners"]["ft"] > 100
    assert rep["manifest_size"] >= 1874


def test_manifests_are_one_line_per_table_and_carry_no_exemptions_yet():
    for rel in (so.CORE_MANIFEST_RELPATH, so.DOMAIN_MANIFEST_RELPATH):
        data = yaml.safe_load((REPO_ROOT / rel).read_text(encoding="utf-8"))
        assert set(data) == {"tables", "rls_exempt"}
        assert all(isinstance(v, str) for v in data["tables"].values())
        assert data["rls_exempt"] == []
    core = yaml.safe_load((REPO_ROOT / so.CORE_MANIFEST_RELPATH).read_text(encoding="utf-8"))["tables"]
    assert set(core.values()) == {"core"}
    dom = yaml.safe_load((REPO_ROOT / so.DOMAIN_MANIFEST_RELPATH).read_text(encoding="utf-8"))["tables"]
    assert set(dom.values()) == {"it", "ft"}
    assert so.owner_of("kanban_tasks", REPO_ROOT) == "core"
    assert so.owner_of("ad_positions", REPO_ROOT) == "ft"


# --------------------------------------------------------------------------- #
# the check, against a throwaway repository
# --------------------------------------------------------------------------- #
@pytest.fixture
def repo(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    (tmp_path / "args").mkdir()
    (tmp_path / "args" / "schema_ownership_rules.yaml").write_text(yaml.safe_dump({
        "default": "it",
        "rls_exempt": [],
        "packages": {"core": ["kanban"], "ft": ["trading"]},
        "prefix": [{"match": "ad_", "owner": "ft"}, {"match": "kanban_", "owner": "core"}],
    }), encoding="utf-8")
    (tmp_path / "args" / "schema_ownership_gate.yaml").write_text(yaml.safe_dump({
        "schema_ownership": {"allowed_owners_here": ["core", "it", "ft"]}
    }), encoding="utf-8")
    (tmp_path / "tools" / "kanban").mkdir(parents=True)
    (tmp_path / "tools" / "kanban" / "init_db.py").write_text(
        'DDL = "CREATE TABLE IF NOT EXISTS kanban_tasks (id TEXT)"\n', encoding="utf-8")
    (tmp_path / "tools" / "db" / "migrations" / "001_x").mkdir(parents=True)
    (tmp_path / "tools" / "db" / "migrations" / "001_x" / "up.sql").write_text(
        "CREATE TABLE IF NOT EXISTS ad_positions (id TEXT);\nALTER TABLE kanban_tasks ADD COLUMN x TEXT;\n",
        encoding="utf-8")
    so.regenerate(tmp_path)
    return tmp_path


def test_regenerate_then_check_is_clean(repo):
    rep = so.build_report(repo)
    assert rep["ok"] is True and rep["owners"] == {"core": 1, "it": 0, "ft": 1}


def test_new_table_without_an_owner_rule_still_gets_one_via_default_but_a_stale_manifest_fails(repo):
    (repo / "tools" / "db" / "migrations" / "002_y").mkdir()
    (repo / "tools" / "db" / "migrations" / "002_y" / "up.sql").write_text(
        "CREATE TABLE IF NOT EXISTS brand_new (id TEXT);\n", encoding="utf-8")
    rep = so.build_report(repo)
    assert rep["ok"] is False and rep["unowned"] == ["brand_new"]
    assert any("brand_new" in s for s in rep["stale"])
    so.regenerate(repo)
    rep = so.build_report(repo)
    assert rep["ok"] is True and so.owner_of("brand_new", repo) == "it"


def test_owner_not_allowed_here_is_a_foreign_touch(repo):
    (repo / "args" / "schema_ownership_gate.yaml").write_text(yaml.safe_dump({
        "schema_ownership": {"allowed_owners_here": ["core", "it"]}
    }), encoding="utf-8")
    rep = so.build_report(repo)
    assert rep["ok"] is False
    assert rep["foreign_owner_touched"] and rep["foreign_owner_touched"][0].startswith("ad_positions (owner ft)")


def test_duplicate_across_manifests_is_refused(repo):
    dom = repo / so.DOMAIN_MANIFEST_RELPATH
    data = yaml.safe_load(dom.read_text(encoding="utf-8"))
    data["tables"]["kanban_tasks"] = "it"
    dom.write_text(yaml.safe_dump(data), encoding="utf-8")
    rep = so.build_report(repo)
    assert "kanban_tasks" in rep["duplicates"] and rep["ok"] is False


def test_rls_exempt_flows_from_rules_to_manifest(repo):
    rules = repo / "args" / "schema_ownership_rules.yaml"
    data = yaml.safe_load(rules.read_text(encoding="utf-8"))
    data["rls_exempt"] = ["kanban_tasks"]
    rules.write_text(yaml.safe_dump(data), encoding="utf-8")
    so.regenerate(repo)
    core = yaml.safe_load((repo / so.CORE_MANIFEST_RELPATH).read_text(encoding="utf-8"))
    assert core["rls_exempt"] == ["kanban_tasks"]
    assert so.rls_exempt_tables(repo) == frozenset({"kanban_tasks"})
    assert so.load_manifests(repo)["kanban_tasks"]["rls"] is False


def test_changed_scope_scans_only_the_named_files(repo):
    rep = so.build_report(repo, ["tools/kanban/init_db.py"])
    assert rep["scope"] == "changed" and rep["tables_seen"] == 1 and rep["ok"] is True


# --------------------------------------------------------------------------- #
# row security: the catalog exemption is by NAME
# --------------------------------------------------------------------------- #
def test_catalog_relations_are_exempt_and_application_tables_are_not():
    from tools.security import row_security as rs

    assert rs._is_system_table("SELECT extname FROM pg_extension WHERE extname = 'vector'") is True
    assert rs._is_system_table("SELECT * FROM pg_catalog.pg_tables") is True
    assert rs._is_system_table("SELECT * FROM information_schema.columns") is True
    assert rs._is_system_table("SELECT name FROM sqlite_master") is True
    # an APPLICATION table that merely starts with the catalog prefix is not a catalog
    for app_table in ("pg_capture_plans", "pg_crm_accounts", "pg_cost_volumes", "pg_anything_else"):
        assert rs._is_system_table(f"SELECT * FROM {app_table} WHERE id = 1") is False, app_table
    # nested catalog lookups never exempt an outer application query
    assert rs._is_system_table("SELECT * FROM projects WHERE id IN (SELECT oid FROM pg_class)") is False


def test_predicate_is_injected_into_prefix_lookalike_tables():
    from tools.security import row_security as rs

    sql, _params, _n = rs.inject_row_predicate("SELECT * FROM pg_capture_plans", tenant_id="t1", classifications=["CUI"])
    assert "tenant_id" in sql and "classification" in sql
    sql2, _p2, _n2 = rs.inject_row_predicate("SELECT extname FROM pg_extension", tenant_id="t1", classifications=["CUI"])
    assert sql2 == "SELECT extname FROM pg_extension"


def test_manifest_rls_exempt_is_consumed_by_row_security(monkeypatch):
    from tools.security import row_security as rs

    monkeypatch.setattr(rs, "_manifest_exempt_tables", lambda: frozenset({"some_canvas_table"}))
    assert rs._is_system_table("SELECT * FROM some_canvas_table") is True
    assert rs._is_system_table("SELECT * FROM projects") is False


def test_mirrors_are_identical():
    for rel in ("tools/db/schema_ownership.py", "tools/security/row_security.py"):
        a = (REPO_ROOT / rel).read_bytes()
        b = (REPO_ROOT / "icdev" / rel).read_bytes()
        assert a == b, rel


def test_ci_and_coherence_consume_the_check():
    ci = (REPO_ROOT / ".github" / "workflows" / "icdev-ci.yml").read_text(encoding="utf-8")
    assert "python tools/db/schema_ownership.py --check" in ci
    src = (REPO_ROOT / "tools" / "workflow" / "coherence_checker.py").read_text(encoding="utf-8")
    assert '"schema_ownership": check_schema_ownership' in textwrap.dedent(src)


# --------------------------------------------------------------------------- #
# a derived dump is not a declaring source
# --------------------------------------------------------------------------- #
def test_the_consolidated_snapshot_is_not_a_declaring_source():
    """tools/db/schema/pg_consolidated.sql REPEATS every table the canonical
    database has, filed under tools/db/. Read as a declaring source it resolves
    each of them to package:db (core) whenever the real declaring package has no
    `packages` entry -- measured 2026-08-21 on a regenerated snapshot, 383
    canvas tables (aadc_*, aiml_*, ...) changed owner it -> core in the
    manifests with nothing else in the tree having moved.
    """
    assert so.is_declaring_source("tools/db/schema/pg_consolidated.sql") is False
    assert so.is_declaring_source("tools/db/schema/pg_consolidated.sql".replace("/", "\\")) is False
    assert so.is_declaring_source("tools/agentic_ai_canvas/db/init_db.py") is True
    # still SCANNED -- a table only the canonical database has must get an owner
    rels = {p.relative_to(REPO_ROOT).as_posix() for p in so.ddl_sources(REPO_ROOT)}
    assert "tools/db/schema/pg_consolidated.sql" in rels
    # but it names no package, so a table it alone declares resolves by rule/default
    assert so._declaring_packages({"tools/db/schema/pg_consolidated.sql"}) == []
    assert so._declaring_packages({"tools/db/schema/pg_consolidated.sql", "tools/agentic_ai_canvas/db/init_db.py"}) == ["agentic_ai_canvas"]
    # the live resolution: a canvas table keeps its canvas owner
    rules = so.load_rules(REPO_ROOT)
    created, _ = so.scan_tables(so.ddl_sources(REPO_ROOT), REPO_ROOT)
    owner, how = so.assign_owner("aadc_artifacts", created["aadc_artifacts"], rules)
    assert (owner, how.startswith("package:db")) == ("it", False), (owner, how)
