# CUI // SP-CTI
"""xit-gen-01 — GENESIS_APPS comes from args/genesis_apps.yaml, and a missing
sibling answers root_missing instead of raising from a cwd that does not exist."""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
import yaml

from tools.genesis import apps_registry as reg

REPO_ROOT = Path(__file__).resolve().parents[2]
LEGACY_KEYS = {"name", "root", "daemon", "promoter", "env_var", "db"}


def test_checked_in_registry_has_the_legacy_shape_and_the_new_sibling():
    apps = reg.load_genesis_apps(REPO_ROOT)
    for key in ("icdev", "govchain", "govproposal", "trading-engine", "trading-strategy",
                "ninjaflow", "signalforge", "icdev-ft"):
        assert key in apps, key
        assert LEGACY_KEYS <= set(apps[key]), key
    me = apps["icdev"]
    assert Path(me["root"]).resolve() == REPO_ROOT and me["available"] is True
    assert Path(me["db"]) == REPO_ROOT / "data" / "icdev.db"
    assert me["promoter"] == "tools/genesis/promoter.py"
    ft = apps["icdev-ft"]
    assert ft["root_env"] == "ICDEV_FT_ROOT" and ft["env_var"] == "FIN_GENESIS_ENABLED"


def test_root_env_wins_over_the_sibling_fallback(tmp_path):
    entry = {"root_env": "X_ROOT", "root_fallback": "sibling"}
    base = tmp_path / "repo"
    (base).mkdir()
    chosen = tmp_path / "elsewhere"
    chosen.mkdir()
    assert reg.resolve_root(entry, base, {"X_ROOT": str(chosen)}) == chosen.resolve()
    # unset, or set to a path that is not a directory -> sibling fallback
    assert reg.resolve_root(entry, base, {}) == (tmp_path / "sibling").resolve()
    assert reg.resolve_root(entry, base, {"X_ROOT": str(tmp_path / "nope")}) == (tmp_path / "sibling").resolve()
    assert reg.resolve_root({"root_fallback": "."}, base, {}) == base.resolve()


def test_unreadable_yaml_degrades_to_this_repository_only(tmp_path):
    bad = tmp_path / "genesis_apps.yaml"
    bad.write_text("{{{ not yaml", encoding="utf-8")
    apps = reg.load_genesis_apps(REPO_ROOT, config_path=bad)
    assert list(apps) == ["icdev"] and apps["icdev"]["available"] is True
    missing = reg.load_genesis_apps(REPO_ROOT, config_path=tmp_path / "absent.yaml")
    assert list(missing) == ["icdev"]


def test_root_missing_answer_names_the_env_var(tmp_path):
    cfg_path = tmp_path / "apps.yaml"
    cfg_path.write_text(yaml.safe_dump({"apps": {
        "icdev": {"name": "me", "root_fallback": "."},
        "ghost": {"name": "Ghost", "root_env": "GHOST_ROOT", "root_fallback": "ghost"},
    }}), encoding="utf-8")
    apps = reg.load_genesis_apps(REPO_ROOT, config_path=cfg_path, environ={})
    assert apps["ghost"]["available"] is False
    answer = reg.root_missing(apps["ghost"])
    assert answer["error"] == "root_missing" and answer["root_env"] == "GHOST_ROOT"
    assert reg.root_missing(apps["icdev"]) is None


def test_dashboard_reads_the_registry_and_guards_every_subprocess():
    src = (REPO_ROOT / "tools" / "dashboard" / "app.py").read_text(encoding="utf-8")
    assert "GENESIS_APPS = _load_genesis_apps(BASE_DIR)" in src
    assert "Path(BASE_DIR).parent / \"govchain\"" not in src  # the literal table is gone
    # every promoter subprocess and _genesis_run answer root_missing first
    assert src.count("_genesis_root_missing(cfg)") == 5
    mirror = (REPO_ROOT / "icdev" / "tools" / "dashboard" / "app.py").read_text(encoding="utf-8")
    assert mirror.count("_genesis_root_missing(cfg)") == 5


def test_launcher_skips_an_absent_trading_dashboard(monkeypatch):
    import importlib

    launcher = importlib.import_module("tools.genesis.launcher")
    monkeypatch.setenv("ICDEV_TRADING_DASHBOARD_ENABLED", "0")
    assert launcher._trading_dashboard_available() is False
    assert launcher._start_trading_dashboard() == (None, None)
    monkeypatch.delenv("ICDEV_TRADING_DASHBOARD_ENABLED")
    monkeypatch.setattr(launcher.os.path, "isfile", lambda p: False)
    assert launcher._trading_dashboard_available() is False
    src = (REPO_ROOT / "tools" / "genesis" / "launcher.py").read_text(encoding="utf-8")
    assert "if td_proc is not None and td_proc.poll() is not None" in src


@pytest.fixture
def client(icdev_db, monkeypatch):
    """Authenticated client on the shared app singleton (the tests/test_app.py convention)."""
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(icdev_db))
    monkeypatch.delenv("ICDEV_FT_ROOT", raising=False)
    monkeypatch.setenv("ICDEV_IDENTITY_GUARD", "0")

    import tools.dashboard.auth as _auth

    monkeypatch.setattr(_auth, "DB_PATH", str(icdev_db))

    from tools.dashboard.app import app

    app.config["TESTING"] = True
    with app.test_client() as tc:
        with tc.session_transaction() as sess:
            sess["user_id"] = "test-admin"
        yield tc


@pytest.mark.parametrize("route", ["/api/genesis/status?app=icdev-ft", "/api/genesis/all-status"])
def test_genesis_routes_answer_root_missing_for_the_absent_sibling(client, route):
    resp = client.get(route)
    assert resp.status_code == 200, resp.data[:200]
    data = resp.get_json()
    body = data if "status?" in route else data.get("icdev-ft")
    assert body is not None
    assert body.get("error") in ("root_missing", "not_found"), body


def test_genesis_page_renders_with_an_absent_sibling(client):
    resp = client.get("/genesis?app=icdev-ft")
    assert resp.status_code == 200
    assert b"ICDEV[FT]" in resp.data


def test_yaml_is_environment_agnostic():
    text = (REPO_ROOT / "args" / "genesis_apps.yaml").read_text(encoding="utf-8")
    assert "C:\\" not in text and "/home/" not in text and "/Users/" not in text
    data = yaml.safe_load(text)["apps"]
    for key, entry in data.items():
        assert entry.get("root_env"), f"{key} has no root_env"
        assert textwrap.dedent(str(entry.get("root_fallback", ""))) != ""
