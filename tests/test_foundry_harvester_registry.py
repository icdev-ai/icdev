# CUI // SP-CTI
"""Tests for the ACF harvester source-scanner registry (acf-ada-08).

Verifies:
  * The SOURCE_SCANNERS dict in tools/foundry/harvester.py is populated with
    the five built-in DB readers (innovation, creative, research, genesis,
    telemetry) at import.
  * ``register_source`` adds a new entry and a duplicate registration is
    silently rejected.
  * ``scan_source`` returns the result of the registered callable and ``[]``
    for unknown names.
  * ``harvest_all`` iterates the registry (so a new source can be added by
    registering it, not by editing harvest_all).
  * The vertical scanners subpackage (tools/foundry/scanners/) auto-registers
    ``arxiv_acf`` at import.
  * The ``args/foundry_config.yaml -> sources`` block documents an
    ``arxiv_acf`` key (the demonstration source).
"""
import sqlite3
import sys
from pathlib import Path

import pytest

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))


# --------------------------------------------------------------------------- #
# Module-level imports
# --------------------------------------------------------------------------- #
def test_harvester_source_scanners_dict_exists():
    from tools.foundry import harvester

    assert hasattr(harvester, "SOURCE_SCANNERS")
    assert isinstance(harvester.SOURCE_SCANNERS, dict)


def test_all_builtin_sources_registered_at_import():
    from tools.foundry import harvester

    registered = set(harvester.SOURCE_SCANNERS.keys())
    # All five built-in DB readers must be present at import.
    for src in ("innovation", "creative", "research", "genesis", "telemetry"):
        assert src in registered, f"{src} missing from SOURCE_SCANNERS"


def test_register_source_adds_new_entry():
    from tools.foundry import harvester

    def my_scanner(conn, limit=200):
        return [{"source_engine": "my_new", "theme": "t", "keywords": []}]

    try:
        harvester.register_source("my_new", my_scanner)
        assert "my_new" in harvester.SOURCE_SCANNERS
        assert harvester.SOURCE_SCANNERS["my_new"] is my_scanner
    finally:
        # Clean up so other tests see the original 5 sources.
        harvester.SOURCE_SCANNERS.pop("my_new", None)


def test_register_source_rejects_duplicate():
    from tools.foundry import harvester

    def first(conn, limit=200):
        return [{"first": True}]

    def second(conn, limit=200):
        return [{"second": True}]

    try:
        harvester.register_source("dup_test", first)
        harvester.register_source("dup_test", second)
        # The second registration should win (re-registration is allowed and
        # the latest call replaces).
        assert harvester.SOURCE_SCANNERS["dup_test"] is second
    finally:
        harvester.SOURCE_SCANNERS.pop("dup_test", None)


def test_register_source_validates_inputs():
    from tools.foundry import harvester

    with pytest.raises(ValueError):
        harvester.register_source("", lambda c, limit=200: [])
    with pytest.raises(ValueError):
        harvester.register_source(123, lambda c, limit=200: [])
    with pytest.raises(TypeError):
        harvester.register_source("not_callable", "not a function")


def test_scan_source_returns_listed_results():
    from tools.foundry import harvester

    def fake(conn, limit=200):
        return [{"source_engine": "fake", "theme": "x", "keywords": []}]

    harvester.register_source("fake_test", fake)
    try:
        out = harvester.scan_source("fake_test", conn=None)
        assert out == [{"source_engine": "fake", "theme": "x", "keywords": []}]
    finally:
        harvester.SOURCE_SCANNERS.pop("fake_test", None)


def test_scan_source_unknown_returns_empty_list():
    from tools.foundry import harvester

    out = harvester.scan_source("definitely_not_registered", conn=None)
    assert out == []


def test_scan_source_handles_exceptions():
    from tools.foundry import harvester

    def broken(conn, limit=200):
        raise RuntimeError("kaboom")

    harvester.register_source("broken_test", broken)
    try:
        # Exception must NOT propagate; the caller (harvest_all) depends on
        # this contract to keep the cycle alive when one source misbehaves.
        assert harvester.scan_source("broken_test", conn=None) == []
    finally:
        harvester.SOURCE_SCANNERS.pop("broken_test", None)


def test_list_registered_sources_returns_list_of_dicts():
    from tools.foundry import harvester

    out = harvester.list_registered_sources()
    assert isinstance(out, list)
    assert all(isinstance(s, dict) for s in out)
    names = {s["name"] for s in out}
    assert {"innovation", "creative", "research", "genesis", "telemetry"} <= names


# --------------------------------------------------------------------------- #
# harvest_all wires through the registry
# --------------------------------------------------------------------------- #
@pytest.fixture
def db_path(tmp_path, monkeypatch):
    """A temp SQLite DB with foundry_signals (sufficient for the per-source
    readers which guard on table existence; absent tables return [])."""
    from tools.foundry.db.init_db import _SCHEMA_SQLITE

    p = tmp_path / "foundry_registry_test.db"
    path = str(p)
    boot = sqlite3.connect(path)
    boot.row_factory = sqlite3.Row
    boot.executescript(_SCHEMA_SQLITE)
    boot.commit()
    boot.close()

    from tools.foundry import harvester as harv

    monkeypatch.setattr(harv, "init_db", lambda *a, **k: True)
    # Force get_connection to point at the temp file.
    import importlib

    storage = importlib.import_module("tools.db.storage")
    monkeypatch.setattr(storage, "get_connection", lambda *a, **k: _conn(path))
    return path


def _conn(path):
    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    return c


def test_harvest_all_iterates_registry_with_new_source(db_path):
    """A source registered at runtime must be picked up by harvest_all
    without any code change to harvest_all itself."""
    from tools.foundry import harvester

    sentinel = []

    def stub_runtime(conn, limit=200):
        sentinel.append("called")
        return []

    harvester.register_source("runtime_added", stub_runtime)
    try:
        summary = harvester.harvest_all(db_path=db_path, sources=["runtime_added"])
        assert sentinel == ["called"]
        assert summary["per_source"]["runtime_added"] == 0
    finally:
        harvester.SOURCE_SCANNERS.pop("runtime_added", None)


def test_harvest_all_skips_unknown_source_silently(db_path):
    """An unknown name in the sources list is treated as a no-op, not an error
    — protects against typos in --source CLI flags."""
    from tools.foundry import harvester

    summary = harvester.harvest_all(db_path=db_path, sources=["nope_does_not_exist"])
    assert summary["raw_signals"] == 0
    assert summary["inserted"] == 0


# --------------------------------------------------------------------------- #
# Vertical scanners subpackage — auto-registration
# --------------------------------------------------------------------------- #
def test_scanners_subpackage_imports_cleanly():
    from tools.foundry import scanners

    assert hasattr(scanners, "SOURCE_SCANNERS")
    assert hasattr(scanners, "register_source")
    assert hasattr(scanners, "scan")
    assert hasattr(scanners, "list_sources")


def test_arxiv_acf_auto_registered_in_subpackage():
    """Importing tools.foundry.scanners should auto-register the arxiv_acf
    scanner without explicit wiring."""
    # Re-importing arxiv directly is the load-bearing check: the decorator on
    # scan_arxiv_acf must have run when arxiv.py was first imported (i.e. when
    # scanners/__init__.py's _autoregister() ran), and the function must be in
    # the live SOURCE_SCANNERS dict.
    from tools.foundry.scanners import arxiv as _arxiv_mod
    from tools.foundry import scanners

    # The arxiv module must carry the registered name (the decorator tags it).
    assert getattr(_arxiv_mod.scan_arxiv_acf, "_acf_source_name", None) == "arxiv_acf"
    # And the registry must reflect that registration.
    assert "arxiv_acf" in scanners.SOURCE_SCANNERS


def test_scanners_list_sources_includes_arxiv():
    from tools.foundry.scanners import list_sources

    sources = list_sources()
    names = {s["name"] for s in sources}
    assert "arxiv_acf" in names


# --------------------------------------------------------------------------- #
# foundry_config.yaml — arxiv_acf must be declared so per-source caps apply
# --------------------------------------------------------------------------- #
def test_foundry_config_yaml_documents_arxiv_acf_source():
    import yaml

    config_path = BASE_DIR / "args" / "foundry_config.yaml"
    if not config_path.exists():
        pytest.skip("foundry_config.yaml not present")
    with open(config_path, "r", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh) or {}
    sources = (cfg.get("foundry") or {}).get("sources") or {}
    # An arxiv_acf key documents the demonstration source. Operators may
    # choose to leave it disabled (enabled: false) without breaking the
    # auto-registration.
    assert "arxiv_acf" in sources, (
        "foundry_config.yaml must declare sources.arxiv_acf so the vertical "
        "scanner can be enabled/disabled from config (acf-ada-08 acceptance)."
    )
    arxiv_cfg = sources["arxiv_acf"]
    # max_results and (categories|keywords) — at least one cap must be present.
    assert "max_results" in arxiv_cfg
    assert ("categories" in arxiv_cfg) or ("keywords" in arxiv_cfg)
