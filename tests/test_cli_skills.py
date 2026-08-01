# CUI // SP-CTI
"""Tests for the `icdev skills` CLI (sag-skl-02).

Hermetic: the local registry and the marketplace catalog/install managers are
faked (module-object monkeypatch — shim-aware), so no DB or filesystem install
runs. Verifies the CLI routes install/update through the EXISTING marketplace
functions (the 7-gate pipeline) rather than fetching/writing skills directly.
"""
from __future__ import annotations

import json

import tools.cli.skills as skills
import tools.marketplace.catalog_manager as cat_mod
import tools.marketplace.install_manager as inst_mod
import tools.skills.registry as reg_mod


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


def test_list_plain(monkeypatch, capsys):
    monkeypatch.setattr(
        reg_mod, "load_registry",
        lambda rebuild=False: {"skills": {"icdev-status": {"description": "status skill"}}, "count": 1},
    )
    rc = skills.main(["list"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "icdev-status" in out and "status skill" in out


def test_list_json(monkeypatch, capsys):
    monkeypatch.setattr(reg_mod, "load_registry", lambda rebuild=False: {"skills": {"icdev-x": {}}})
    rc = skills.main(["list", "--json"])
    assert rc == 0
    assert json.loads(capsys.readouterr().out)["count"] == 1


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------


_ASSETS = [
    {"slug": "compliance-pack", "name": "Compliance Pack", "description": "NIST controls", "current_version": "1.2", "catalog_tier": "central", "tags": "nist"},
    {"slug": "viz-helper", "name": "Viz Helper", "description": "charts", "current_version": "0.9", "catalog_tier": "tenant", "tags": "viz"},
]


def test_search_filters(monkeypatch, capsys):
    monkeypatch.setattr(cat_mod, "list_assets", lambda asset_type=None, limit=50: list(_ASSETS))
    rc = skills.main(["search", "nist"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "compliance-pack" in out and "viz-helper" not in out


def test_search_empty_lists_all_json(monkeypatch, capsys):
    monkeypatch.setattr(cat_mod, "list_assets", lambda asset_type=None, limit=50: list(_ASSETS))
    rc = skills.main(["search", "--json"])
    assert rc == 0
    assert json.loads(capsys.readouterr().out)["count"] == 2


def test_search_marketplace_unavailable(monkeypatch, capsys):
    def _boom(**k):
        raise RuntimeError("no catalog table")

    monkeypatch.setattr(cat_mod, "list_assets", _boom)
    rc = skills.main(["search", "x"])
    assert rc == 1
    assert "unavailable" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# install — routes through install_manager (7-gate pipeline)
# ---------------------------------------------------------------------------


def test_install_routes_through_pipeline(monkeypatch, capsys):
    asset = {
        "id": "mkt-1",
        "slug": "compliance-pack",
        "asset_type": "skill",
        "current_version": "1.2",
        "versions": [
            {"id": "ver-12", "version": "1.2"},
            {"id": "ver-11", "version": "1.1"},
        ],
    }
    monkeypatch.setattr(cat_mod, "get_asset", lambda slug=None, asset_id=None: asset)
    captured = {}

    def _install(asset_id, version_id, tenant_id, project_id, installed_by, install_path):
        captured.update(locals())
        return {"installation_id": "inst-9", "status": "installed"}

    monkeypatch.setattr(inst_mod, "install_asset", _install)
    rc = skills.main(["install", "compliance-pack", "--json"])
    assert rc == 0
    # correct asset + resolved (current) version routed to the marketplace installer
    assert captured["asset_id"] == "mkt-1"
    assert captured["version_id"] == "ver-12"
    assert json.loads(capsys.readouterr().out)["installation_id"] == "inst-9"


def test_install_specific_version(monkeypatch):
    asset = {
        "id": "mkt-1", "asset_type": "skill", "current_version": "1.2",
        "versions": [{"id": "ver-12", "version": "1.2"}, {"id": "ver-11", "version": "1.1"}],
    }
    monkeypatch.setattr(cat_mod, "get_asset", lambda slug=None, asset_id=None: asset)
    captured = {}
    monkeypatch.setattr(
        inst_mod, "install_asset",
        lambda *a, **k: captured.update(zip(
            ["asset_id", "version_id", "tenant_id", "project_id", "installed_by", "install_path"], a
        )) or {"installation_id": "i", "status": "installed"},
    )
    skills.main(["install", "x", "--version", "1.1"])
    assert captured["version_id"] == "ver-11"


def test_install_no_such_asset(monkeypatch, capsys):
    monkeypatch.setattr(cat_mod, "get_asset", lambda slug=None, asset_id=None: None)
    rc = skills.main(["install", "ghost"])
    assert rc == 2
    assert "no marketplace skill" in capsys.readouterr().err


def test_install_rejected_by_pipeline(monkeypatch, capsys):
    asset = {"id": "m", "asset_type": "skill", "current_version": "1.0", "versions": [{"id": "v", "version": "1.0"}]}
    monkeypatch.setattr(cat_mod, "get_asset", lambda slug=None, asset_id=None: asset)

    def _reject(*a, **k):
        raise ValueError("IL incompatible")

    monkeypatch.setattr(inst_mod, "install_asset", _reject)
    rc = skills.main(["install", "x"])
    assert rc == 1
    assert "rejected" in capsys.readouterr().err


def test_install_rejects_non_skill(monkeypatch, capsys):
    monkeypatch.setattr(
        cat_mod, "get_asset",
        lambda slug=None, asset_id=None: {"id": "g", "asset_type": "goal", "versions": [{"id": "v"}]},
    )
    rc = skills.main(["install", "somegoal"])
    assert rc == 2
    assert "not a skill" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# update
# ---------------------------------------------------------------------------


def test_update_up_to_date(monkeypatch, capsys):
    monkeypatch.setattr(inst_mod, "check_updates", lambda tid: {"updates_available": []})
    rc = skills.main(["update"])
    assert rc == 0
    assert "up to date" in capsys.readouterr().out


def test_update_dry_run(monkeypatch, capsys):
    monkeypatch.setattr(
        inst_mod, "check_updates",
        lambda tid: {"updates_available": [
            {"installation_id": "i1", "asset_id": "a1", "asset_name": "Pack",
             "installed_version": "1.0", "current_version": "1.1", "asset_type": "skill"}
        ]},
    )
    rc = skills.main(["update", "--dry-run"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "1.0 -> 1.1" in out


def test_update_applies(monkeypatch, capsys):
    monkeypatch.setattr(
        inst_mod, "check_updates",
        lambda tid: {"updates_available": [
            {"installation_id": "i1", "asset_id": "a1", "asset_name": "Pack",
             "installed_version": "1.0", "current_version": "1.1", "asset_type": "skill"}
        ]},
    )
    monkeypatch.setattr(
        cat_mod, "get_asset",
        lambda slug=None, asset_id=None: {"id": "a1", "current_version": "1.1", "versions": [{"id": "ver-11", "version": "1.1"}]},
    )
    calls = []
    monkeypatch.setattr(inst_mod, "update_asset", lambda inst_id, vid, by: calls.append((inst_id, vid, by)))
    rc = skills.main(["update"])
    assert rc == 0
    assert calls == [("i1", "ver-11", "default")]


# ---------------------------------------------------------------------------
# unit
# ---------------------------------------------------------------------------


def test_resolve_version_id():
    asset = {"current_version": "2.0", "versions": [{"id": "b", "version": "2.0"}, {"id": "a", "version": "1.0"}]}
    assert skills._resolve_version_id(asset) == "b"
    assert skills._resolve_version_id(asset, "1.0") == "a"
    assert skills._resolve_version_id({"versions": []}) is None
