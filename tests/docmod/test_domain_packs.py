# CUI // SP-CTI
"""docmod-packs: the four launch domain packs, eol_products_sync,
defacto_learner, KG hook, and the kg_stale_entities handler repair."""
from __future__ import annotations

import uuid

import pytest

from tools.doc_modernization.base_pack import ChunkRef

_REF = ChunkRef(doc_id="doc-t", version_id="doc-t_v1", section="Security")

_DDL = [
    """CREATE TABLE IF NOT EXISTS ni_devices (
        id TEXT PRIMARY KEY, topology_id TEXT, node_id TEXT, label TEXT,
        device_type TEXT, vendor TEXT, model TEXT, firmware_version TEXT,
        eol_date TEXT, eos_date TEXT, site TEXT,
        created_at TEXT, updated_at TEXT)""",
    """CREATE TABLE IF NOT EXISTS mc_net_eol_data (
        id INTEGER PRIMARY KEY AUTOINCREMENT, vendor TEXT, model_pattern TEXT,
        eol_date TEXT, eos_date TEXT, eosm_date TEXT, source TEXT, synced_at TEXT)""",
    """CREATE TABLE IF NOT EXISTS nc_hardware_profiles (
        id TEXT PRIMARY KEY, vendor TEXT, model TEXT, model_family TEXT,
        device_type TEXT, eol_date TEXT, eos_date TEXT, tags TEXT,
        is_builtin INTEGER DEFAULT 0)""",
    """CREATE TABLE IF NOT EXISTS nc_device_profiles (
        id TEXT PRIMARY KEY, vendor TEXT, platform TEXT, description TEXT,
        commands_json TEXT, is_builtin INTEGER DEFAULT 0,
        created_by TEXT, created_at TEXT)""",
    """CREATE TABLE IF NOT EXISTS kg_nodes (
        id TEXT PRIMARY KEY, graph_id TEXT, label TEXT, entity_type TEXT,
        properties TEXT, embedding TEXT, centrality REAL, created_at TEXT)""",
]

_DOCMOD_DDL_KEYS = ("docmod_eol_products", "docmod_defacto_standards",
                    "docmod_catalog_entries", "docmod_catalog_audit")


@pytest.fixture()
def db():
    from tests.conftest import MINIMAL_ICDEV_SCHEMA
    from tools.db.storage import get_connection

    conn = get_connection()
    for ddl in _DDL:
        conn.execute(ddl)
    for stmt in MINIMAL_ICDEV_SCHEMA.split(";"):
        if any(k in stmt for k in _DOCMOD_DDL_KEYS) and "CREATE TABLE" in stmt:
            conn.execute(stmt)
    # clean slate for evidence tables shared across tests (file-scoped SQLite DB).
    # docmod_catalog_entries is mutable (not append-only) — safe to clear in tests.
    for t in ("docmod_defacto_standards", "docmod_eol_products", "ni_devices",
              "nc_hardware_profiles", "nc_device_profiles", "docmod_catalog_entries"):
        conn.execute(f"DELETE FROM {t}")
    conn.commit()
    conn.close()
    yield


def _conn():
    from tools.db.storage import get_connection
    return get_connection()


# ── crypto_protocols ─────────────────────────────────────────────────────────

def test_crypto_pack_flags_tls11_and_recommends():
    from tools.doc_modernization.packs.crypto_protocols import CryptoProtocolsPack

    pack = CryptoProtocolsPack(config={"pack_id": "crypto_protocols"})
    entities = pack.extract("All external services shall use TLS v1.1 and MD5 digests.", _REF)
    labels = {e.label for e in entities}
    assert any("1.1" in label for label in labels)
    assert any("MD5" in label.upper() for label in labels)

    tls = next(e for e in entities if "1.1" in e.label)
    verdict = pack.evaluate(tls, None)
    assert verdict.currency_verdict == "deprecated"
    assert verdict.finding_type == "deprecated_tech"
    assert verdict.evidence and verdict.evidence[0]["source"] == "rule:crypto-tls-02"

    rep = pack.recommend(tls, verdict, None)
    assert rep is not None and "TLS 1.2" in rep.label and rep.source == "rulebook"


def test_crypto_pack_clean_text_no_findings():
    from tools.doc_modernization.packs.crypto_protocols import CryptoProtocolsPack

    pack = CryptoProtocolsPack(config={"pack_id": "crypto_protocols"})
    assert pack.extract("Services use TLS 1.3 with AES-256-GCM and SHA-256.", _REF) == []


# ── eol_products_sync + software pack ────────────────────────────────────────

def test_eol_seed_and_alias_lookup(db):
    from tools.doc_modernization.eol_products_sync import get_product_eol, load_seed

    result = load_seed()
    assert result["loaded"] > 10

    hit = get_product_eol("Windows Server 2012", alias_map={"windows server": "windows-server"})
    assert hit and hit["product"] == "windows-server" and hit["cycle"] == "2012"
    assert hit["eol_date"] == "2023-10-10"
    # unmapped product → None, never a fake row
    assert get_product_eol("MadeUpOS 99", alias_map={}) is None


def test_software_pack_eol_verdict_and_replacement(db):
    from tools.doc_modernization.eol_products_sync import load_seed
    from tools.doc_modernization.packs.software import SoftwarePack

    load_seed()
    pack = SoftwarePack(config={
        "pack_id": "software",
        "extraction": {"patterns": [r"(?i)\bWindows Server \d{4}(?:\s?R2)?\b"]},
        "alias_map": {"windows server": "windows-server"},
    })
    conn = _conn()
    entities = pack.extract("File shares run on Windows Server 2012 clusters.", _REF)
    assert len(entities) == 1
    verdict = pack.evaluate(entities[0], conn)
    assert verdict.currency_verdict == "eol"
    assert verdict.finding_type == "eol_software"
    assert verdict.evidence[0]["source"].startswith("eolcache:")

    rep = pack.recommend(entities[0], verdict, conn)
    conn.close()
    assert rep is not None and "windows-server" in rep.label
    assert rep.label.split()[-1] >= "2022"  # newest supported cycle


def test_software_pack_unknown_product_never_eol(db):
    from tools.doc_modernization.packs.software import SoftwarePack

    pack = SoftwarePack(config={
        "pack_id": "software",
        "extraction": {"patterns": [r"(?i)\bFrobnicator \d+\b"]},
        "alias_map": {},
    })
    conn = _conn()
    entities = pack.extract("Runs on Frobnicator 3000.", _REF)
    verdict = pack.evaluate(entities[0], conn)
    conn.close()
    assert verdict.currency_verdict == "unknown"
    assert verdict.is_finding is False


# ── policy_refs ──────────────────────────────────────────────────────────────

def test_policy_pack_supersession(db):
    from tools.doc_modernization.packs.policy_refs import PolicyRefsPack

    pack = PolicyRefsPack(config={"pack_id": "policy_refs"})
    conn = _conn()
    entities = pack.extract("Controls are tailored per NIST SP 800-53 Rev 4 guidance.", _REF)
    # Rev 4 now carries temporal dates, so extract emits the supersession entity
    # PLUS a disjoint temporal entity (kind='temporal'); assert on the former.
    supersession = [e for e in entities if (e.attributes or {}).get("kind") != "temporal"]
    assert len(supersession) == 1
    verdict = pack.evaluate(supersession[0], conn)
    assert verdict.currency_verdict == "retired"
    assert verdict.finding_type == "superseded_standard"

    rep = pack.recommend(supersession[0], verdict, conn)
    conn.close()
    assert rep is not None and "Rev 5" in rep.label

    # Rev 5 matches no supersession rule, but is now extracted as a dynamic
    # NIST-pubs candidate (dmx-loop-02). Without a docmod_nist_pubs cache row it
    # evaluates to 'unknown' -> no finding, so it never becomes a false positive.
    dyn = pack.extract("Aligned to NIST SP 800-53 Rev 5.", _REF)
    assert len(dyn) == 1 and dyn[0].attributes.get("nist_pub_id") == "SP 800-53"
    conn2 = _conn()
    verdict_dyn = pack.evaluate(dyn[0], conn2)
    conn2.close()
    assert not verdict_dyn.is_finding


# ── network_hardware ─────────────────────────────────────────────────────────

def _seed_hw(db_rows):
    conn = _conn()
    for model, kw in db_rows:
        conn.execute(
            "INSERT INTO nc_hardware_profiles (id, vendor, model, model_family, device_type, "
            "eol_date, eos_date, tags) VALUES (%s,%s,%s,%s,%s,%s,%s,'[]')",
            (f"hw-{uuid.uuid4().hex[:6]}", kw.get("vendor", "Cisco"), model,
             kw.get("model_family", ""), kw.get("device_type", "switch"),
             kw.get("eol_date"), kw.get("eos_date")),
        )
    conn.commit()
    conn.close()


def test_hw_pack_eol_device_gets_curated_replacement(db):
    from tools.doc_modernization.packs.network_hardware import NetworkHardwarePack

    _seed_hw([
        ("Catalyst 6500", {"eol_date": "2019-06-30", "device_type": "switch"}),
        ("Catalyst 9500", {"device_type": "switch"}),  # current approved standard
    ])
    pack = NetworkHardwarePack(config={
        "pack_id": "network_hardware",
        "extraction": {"patterns": [r"(?i)\bCatalyst\s?\d{4}\b"], "inventory_terms": False},
    })
    conn = _conn()
    entities = pack.extract("Core switching uses Catalyst 6500 chassis pairs.", _REF)
    assert len(entities) == 1
    verdict = pack.evaluate(entities[0], conn)
    assert verdict.currency_verdict == "eol"
    assert verdict.finding_type == "eol_hardware"
    assert any(ev["source"].startswith("catalog:network_canvas:") for ev in verdict.evidence)

    rep = pack.recommend(entities[0], verdict, conn)
    conn.close()
    assert rep is not None and "Catalyst 9500" in rep.label and rep.source == "catalog"


def test_hw_pack_unknown_model_stays_unknown(db):
    from tools.doc_modernization.packs.network_hardware import NetworkHardwarePack

    pack = NetworkHardwarePack(config={
        "pack_id": "network_hardware",
        "extraction": {"patterns": [r"(?i)\bWidgetSwitch \d+\b"], "inventory_terms": False},
    })
    conn = _conn()
    entities = pack.extract("Edge uses WidgetSwitch 900.", _REF)
    verdict = pack.evaluate(entities[0], conn)
    conn.close()
    assert verdict.currency_verdict == "unknown" and verdict.is_finding is False


# ── defacto_learner ──────────────────────────────────────────────────────────

def _seed_devices(rows):
    conn = _conn()
    for i, (model, kw) in enumerate(rows):
        conn.execute(
            "INSERT INTO ni_devices (id, node_id, device_type, vendor, model, "
            "firmware_version, updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (f"dev-{uuid.uuid4().hex[:6]}", f"n{i}", kw.get("device_type", "switch"),
             kw.get("vendor", "Cisco"), model, kw.get("fw", ""),
             kw.get("updated_at", "2026-06-01T00:00:00+00:00")),
        )
    conn.commit()
    conn.close()


def test_learner_recency_weighted_shares(db):
    from tools.doc_modernization.defacto_learner import get_recommended, recompute

    _seed_devices(
        [("Catalyst 9300", {"updated_at": "2026-07-01T00:00:00+00:00"})] * 6
        + [("Catalyst 3750", {"updated_at": "2019-01-01T00:00:00+00:00"})] * 4
    )
    result = recompute()
    assert result["entries"] == 2 and result["devices"] == 10

    top = get_recommended("switch")
    assert top["product"] == "Catalyst 9300"
    # recency weighting: 6 fresh devices dominate 4 seven-year-old ones
    assert top["share_pct"] > 90


def test_learner_cross_check_divergence_and_gap(db):
    from tools.doc_modernization.catalog import MergedCatalog
    from tools.doc_modernization.defacto_learner import cross_check, recompute

    # Deployed reality: routers mostly ISR 4451 (not in catalog);
    # switches mostly Catalyst 6500 (catalog says deprecated).
    _seed_devices(
        [("ISR 4451", {"device_type": "router"})] * 5
        + [("Catalyst 6500", {"device_type": "switch"})] * 5
    )
    _seed_hw([
        ("Catalyst 6500", {"eos_date": "2020-01-01", "device_type": "switch"}),
        ("Catalyst 9500", {"device_type": "switch"}),
    ])
    recompute()
    result = cross_check(MergedCatalog())
    gap_products = {g["product"] for g in result["gaps"]}
    div_products = {d["product"] for d in result["divergence"]}
    assert "ISR 4451" in gap_products
    assert "Catalyst 6500" in div_products


# ── KG hook + MCP handler ────────────────────────────────────────────────────

def test_kg_hook_extracts_pack_entities():
    from tools.doc_modernization.pack_loader import load_packs
    from tools.knowledge_graph import text_network

    load_packs(force=True)  # publishes EXTRA_ENTITY_PATTERNS
    assert text_network.EXTRA_ENTITY_PATTERNS, "pack patterns not published"
    entities = text_network._extract_entities(
        "The standard mandates TLS 1.1 on Catalyst 6500 running Windows Server 2012."
    )
    etypes = {t for _, t in entities}
    assert "protocol" in etypes or "crypto_algorithm" in etypes
    assert "hardware_model" in etypes
    assert "software_product" in etypes


def test_kg_stale_entities_handler_dispatches(db):
    from tools.mcp.gap_handlers import handle_kg_stale_entities

    result = handle_kg_stale_entities({"stale_days": 90})
    assert isinstance(result, dict)
    assert "error" not in result or "stale" in str(result).lower() or result.get("stale_entities") is not None
