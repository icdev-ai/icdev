# CUI // SP-CTI
"""Entity-currency store + feed-driven de-facto learner (cef-fnd-04).

Two defects are under test, and they are the same defect one layer apart:

  * currency evidence existed in three domain-narrow tables and nothing could
    ask all three at once;
  * docmod_defacto_standards held 0 rows because its ONLY input, ni_devices,
    held 0 rows — the writer ran nightly and had nothing to learn from.

So the assertions are about SHAPE, not about any particular vendor or product:
that a source is read from config rather than from code, that curated evidence
keeps its authority, that disagreement survives to the caller, and that two
classes of evidence are never blended into one percentage.
"""
from __future__ import annotations

import json

import pytest

# Tables neither MINIMAL_ICDEV_SCHEMA nor any test fixture creates. Written out
# here with the columns the config references — the same thing tests/docmod does.
_EXTRA_DDL = [
    """CREATE TABLE IF NOT EXISTS mc_net_eol_data (
        id TEXT PRIMARY KEY, vendor TEXT, model_pattern TEXT, eol_date TEXT,
        eos_date TEXT, eosm_date TEXT, source TEXT, synced_at TEXT)""",
    """CREATE TABLE IF NOT EXISTS ni_devices (
        id TEXT PRIMARY KEY, topology_id TEXT, node_id TEXT, label TEXT,
        device_type TEXT, vendor TEXT, model TEXT, firmware_version TEXT,
        created_at TEXT, updated_at TEXT)""",
    """CREATE TABLE IF NOT EXISTS topologies (
        id TEXT PRIMARY KEY, name TEXT, graph_json TEXT, created_at TEXT,
        updated_at TEXT)""",
]

_SCHEMA_KEYS = ("docmod_eol_products", "docmod_catalog_entries",
                "docmod_defacto_standards", "entity_currency")

# Deliberately not real products: the store must not care what an entity IS.
_SOFTWARE = "acme-widget-runtime"
_HARDWARE = "model-alpha-9000"
_AUTHORITY = "example-corp"


@pytest.fixture()
def db(tmp_path, monkeypatch):
    """Isolated SQLite DB carrying only the tables these tests touch."""
    monkeypatch.setenv("ICDEV_DB_PATH", str(tmp_path / "currency.db"))
    from tests.conftest import MINIMAL_ICDEV_SCHEMA
    from tools.db.storage import get_connection

    conn = get_connection()
    for stmt in MINIMAL_ICDEV_SCHEMA.split(";"):
        if "CREATE" in stmt and any(k in stmt for k in _SCHEMA_KEYS):
            conn.execute(stmt)
    for ddl in _EXTRA_DDL:
        conn.execute(ddl)
    conn.commit()
    conn.close()
    yield


def _conn():
    from tools.db.storage import get_connection
    return get_connection()


def _seed_sources(*, eol_rows=(), hw_rows=(), catalog_rows=()):
    conn = _conn()
    for i, (product, cycle, eol, eos, latest) in enumerate(eol_rows):
        conn.execute(
            "INSERT INTO docmod_eol_products (id, product, cycle, eol_date, "
            "eos_date, latest_version, lts, source, synced_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (f"eol-{i}", product, cycle, eol, eos, latest, 0, "seed",
             "2026-01-01T00:00:00+00:00"),
        )
    for i, (vendor, pattern, eol, eos) in enumerate(hw_rows):
        conn.execute(
            "INSERT INTO mc_net_eol_data (id, vendor, model_pattern, eol_date, "
            "eos_date, eosm_date, source, synced_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (f"hw-{i}", vendor, pattern, eol, eos, None, "static_seed",
             "2026-08-01T00:00:00+00:00"),
        )
    for i, (category, vendor, product, status, version) in enumerate(catalog_rows):
        conn.execute(
            "INSERT INTO docmod_catalog_entries (entry_id, domain, category, "
            "vendor, product, version, status, source, updated_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (f"cat-{i}", "network_hardware", category, vendor, product, version,
             status, "manual", "2026-02-01T00:00:00+00:00"),
        )
    conn.commit()
    conn.close()


# ── verdict derivation ────────────────────────────────────────────────────────

def test_verdict_from_dates_distinguishes_every_state():
    from tools.currency.entity_currency import derive_verdict_from_dates as verdict

    today = "2026-08-17"
    assert verdict("2020-01-01", None, today) == "end_of_life"
    # Past EOL dominates a past EOS — past both is end-of-life, not merely
    # unsupported.
    assert verdict("2020-01-01", "2019-01-01", today) == "end_of_life"
    assert verdict(None, "2020-01-01", today) == "end_of_support"
    assert verdict("2030-01-01", None, today) == "scheduled_end_of_life"
    assert verdict(None, "2030-01-01", today) == "current"
    # No announcement is NOT evidence of support. Never 'current', never 'eol'.
    assert verdict(None, None, today) == "unknown"
    assert verdict("", "   ", today) == "unknown"
    # A value that is not an ISO date is dropped rather than lexically compared.
    assert verdict("soon", None, today) == "unknown"


# ── backfill ──────────────────────────────────────────────────────────────────

def test_backfill_reads_every_declared_source_and_is_idempotent(db):
    from tools.currency.entity_currency import backfill, stats

    _seed_sources(
        eol_rows=[(_SOFTWARE, "1.0", "2020-01-01", None, "3.0"),
                  (_SOFTWARE, "3.0", "2031-01-01", None, "3.0")],
        hw_rows=[(_AUTHORITY, _HARDWARE, "2024-01-01", "2022-01-01")],
        catalog_rows=[("chassis", _AUTHORITY, "model-beta-1", "approved", "")],
    )
    first = backfill()
    assert first["errors"] == {}
    assert first["written"] == 4
    assert {s: v["written"] for s, v in first["sources"].items()} == {
        "docmod_eol_products": 2,
        "mc_net_eol_data": 1,
        "docmod_catalog_entries": 1,
    }

    # Re-running the same sources UPDATES; it must not grow the store.
    backfill()
    assert stats()["total"] == 4


def test_backfill_carries_provenance_back_to_the_origin_row(db):
    from tools.currency.entity_currency import backfill, query

    _seed_sources(eol_rows=[(_SOFTWARE, "1.0", "2020-01-01", None, "3.0")])
    backfill(sources=["docmod_eol_products"])

    row = query(_SOFTWARE, entity_type="software_release")[0]
    assert row["provenance_table"] == "docmod_eol_products"
    assert row["provenance_id"] == "eol-0"
    # Raw source fields are kept for the case where the origin row — a MUTABLE
    # upsert cache — has since been overwritten.
    assert json.loads(row["provenance_json"])["cycle"] == "1.0"
    # The two RLS columns have columns of their own and are not copied in.
    assert "classification" not in json.loads(row["provenance_json"])


def test_a_source_with_no_currency_signal_is_recorded_as_unknown(db):
    """"This source has heard of it and knows nothing" is an answer, and it is
    a different answer from "no source has heard of it"."""
    from tools.currency.entity_currency import backfill, resolve
    from tools.currency.entity_currency import load_config

    _seed_sources(eol_rows=[(_SOFTWARE, "9.9", None, None, None)])
    backfill(sources=["docmod_eol_products"])

    hit = resolve(_SOFTWARE, entity_type="software_release")
    assert hit["verdict"] == "unknown"
    # Confidence is clamped for an assertion that asserts nothing.
    assert hit["confidence"] <= float(load_config()["unknown_confidence"])
    assert resolve("nothing-has-ever-heard-of-this") is None


def test_one_unreadable_source_does_not_cost_the_others_their_rows(db):
    from tools.currency.entity_currency import backfill

    conn = _conn()
    conn.execute("DROP TABLE mc_net_eol_data")
    conn.commit()
    conn.close()
    _seed_sources(eol_rows=[(_SOFTWARE, "1.0", "2020-01-01", None, None)])

    out = backfill()
    assert out["written"] == 1
    # A source that FAILED reports why. Reporting zero would be a different
    # finding wearing the same number.
    assert "mc_net_eol_data" in out["errors"]
    assert out["sources"]["docmod_eol_products"]["written"] == 1


# ── resolution ────────────────────────────────────────────────────────────────

def test_curated_authority_beats_a_newer_more_recent_feed(db):
    """The catalog is AUTHORITATIVE per the defacto_learner contract. Authority
    that a fresher timestamp can overturn is not authority."""
    from tools.currency.entity_currency import backfill, resolve

    _seed_sources(
        # The feed says dead, and says so more recently (synced 2026-08-01).
        hw_rows=[(_AUTHORITY, _HARDWARE, "2020-01-01", None)],
        # The curated catalog says approved, asserted earlier (2026-02-01).
        catalog_rows=[("hardware_model", _AUTHORITY, _HARDWARE, "approved", "")],
    )
    backfill()

    hit = resolve(_HARDWARE, entity_type="hardware_model")
    assert hit["source"] == "docmod_catalog_entries"
    assert hit["authoritative"] is True
    assert hit["verdict"] == "current"
    # The disagreement is REPORTED, never resolved away.
    assert hit["conflict"] is True
    assert set(hit["sources_consulted"]) == {"docmod_catalog_entries", "mc_net_eol_data"}
    assert [o["verdict"] for o in hit["others"]] == ["end_of_life"]


def test_catalog_status_maps_through_config_not_code(db):
    from tools.currency.entity_currency import backfill, resolve

    _seed_sources(catalog_rows=[
        ("hardware_model", _AUTHORITY, "model-retired-1", "retired", ""),
        ("hardware_model", _AUTHORITY, "model-deprecated-1", "deprecated", ""),
    ])
    backfill(sources=["docmod_catalog_entries"])

    assert resolve("model-retired-1")["verdict"] == "end_of_life"
    assert resolve("model-deprecated-1")["verdict"] == "deprecated"


def test_entity_type_comes_from_the_row_when_the_source_classifies_itself(db):
    from tools.currency.entity_currency import backfill, query

    _seed_sources(catalog_rows=[
        ("protocol", "", "some-protocol-v1", "deprecated", ""),
        ("chassis", _AUTHORITY, "model-gamma-2", "approved", ""),
    ])
    backfill(sources=["docmod_catalog_entries"])

    assert query("some-protocol-v1")[0]["entity_type"] == "protocol"
    assert query("model-gamma-2")[0]["entity_type"] == "chassis"


# ── the store names no source in code ─────────────────────────────────────────

def test_the_module_hardcodes_no_source_table(db):
    """The point of the store is that a fourth provider is a config entry. If a
    source table name appears in the module, it is not source-agnostic."""
    from pathlib import Path

    from tools.currency import entity_currency as module
    from tools.currency.entity_currency import declared_sources

    source = Path(module.__file__).read_text(encoding="utf-8")
    for spec in declared_sources(enabled_only=False):
        assert spec["table"] not in source, f"{spec['table']} is hardcoded in the module"
        assert spec["id"] not in source, f"{spec['id']} is hardcoded in the module"


# ── the learner's input ───────────────────────────────────────────────────────

def _seed_topology(name, nodes):
    conn = _conn()
    conn.execute(
        "INSERT INTO topologies (id, name, graph_json, updated_at) VALUES (%s,%s,%s,%s)",
        (f"topo-{name}", name, json.dumps({"nodes": nodes}), "2026-08-01T00:00:00+00:00"),
    )
    conn.commit()
    conn.close()


def _seed_devices(rows):
    conn = _conn()
    for i, (device_type, vendor, model) in enumerate(rows):
        conn.execute(
            "INSERT INTO ni_devices (id, node_id, device_type, vendor, model, "
            "firmware_version, updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (f"dev-{i}", f"n{i}", device_type, vendor, model, "",
             "2026-08-01T00:00:00+00:00"),
        )
    conn.commit()
    conn.close()


def test_learner_learns_from_a_declared_json_feed_when_the_inventory_is_empty(db):
    """THE cef-fnd-04 defect: ni_devices empty meant zero learned standards, for
    months, with nothing red. A second declared feed removes the single point of
    failure."""
    from tools.doc_modernization.defacto_learner import recompute

    _seed_topology("t1", [
        {"id": "a", "type": "switch", "config": {"vendor": _AUTHORITY, "model": _HARDWARE}},
        {"id": "b", "type": "switch", "vendor": _AUTHORITY, "model": _HARDWARE},
        # A node naming no product identifies nothing and must be dropped.
        {"id": "c", "type": "switch"},
    ])
    result = recompute()

    assert result["feeds"]["ni_devices"]["records"] == 0
    assert result["feeds"]["topology_nodes"]["records"] == 2
    assert result["entries"] == 1

    conn = _conn()
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM docmod_defacto_standards").fetchall()]
    conn.close()
    assert rows[0]["deploy_count"] == 2
    # Design evidence is LABELLED design wherever it surfaces.
    assert rows[0]["source_feed"] == "topology_nodes"
    assert rows[0]["evidence_kind"] == "design"


def test_learner_never_blends_two_evidence_classes(db):
    """An observed estate and a drawing of one are different claims. Pooling
    them would let 16 diagrams read as deployment reality."""
    from tools.doc_modernization.defacto_learner import get_recommended, recompute

    _seed_devices([("switch", _AUTHORITY, "inventory-model-1")] * 2)
    _seed_topology("t1", [
        {"id": "a", "type": "switch", "config": {"vendor": _AUTHORITY, "model": "design-model-1"}},
    ] * 8)
    recompute()

    conn = _conn()
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM docmod_defacto_standards").fetchall()]
    conn.close()

    by_feed = {r["source_feed"]: r for r in rows}
    assert set(by_feed) == {"ni_devices", "topology_nodes"}
    # Each feed's shares are a share of ITS OWN category total.
    assert by_feed["ni_devices"]["share_pct"] == 100.0
    assert by_feed["topology_nodes"]["share_pct"] == 100.0

    # 2 observed devices outrank 8 drawn ones: precedence is an evidence
    # ordering, not a vote.
    top = get_recommended("switch")
    assert top["product"] == "inventory-model-1"
    assert top["evidence_kind"] == "inventory"


def test_learner_survives_a_feed_whose_table_does_not_exist(db):
    from tools.doc_modernization.defacto_learner import recompute

    conn = _conn()
    conn.execute("DROP TABLE topologies")
    conn.commit()
    conn.close()
    _seed_devices([("router", _AUTHORITY, "inventory-model-2")])

    result = recompute()
    assert result["feeds"]["topology_nodes"]["records"] == 0
    assert result["feeds"]["ni_devices"]["records"] == 1
    assert result["entries"] == 1
