# CUI // SP-CTI
"""docmod-core: engine foundation tests — constants, pack loader, scanner
(dedupe / supersede / incremental skip), latest-state helper, catalog providers.
"""
from __future__ import annotations

import re
import uuid

import pytest


# ── fixtures ──────────────────────────────────────────────────────────────────

_DDL = [
    # Superset shape shared with tests/docmod/test_import_from_docgen.py — the
    # first CREATE wins under IF NOT EXISTS, so both files must agree.
    """CREATE TABLE IF NOT EXISTS dic_documents (
        doc_id TEXT PRIMARY KEY, collection_id TEXT, title TEXT, filename TEXT,
        status TEXT, origin TEXT, classification TEXT, template_type TEXT,
        writeguard_mode TEXT, source_idr_session_id TEXT, source_wg_result_id TEXT,
        tenant_id TEXT, created_at TEXT)""",
    """CREATE TABLE IF NOT EXISTS dic_versions (
        version_id TEXT PRIMARY KEY, doc_id TEXT NOT NULL,
        version_no INTEGER NOT NULL DEFAULT 1, origin TEXT, status TEXT,
        assigned_to TEXT, review_notes TEXT, content_sha256 TEXT,
        created_at TEXT, created_by TEXT, tenant_id TEXT, classification TEXT)""",
    """CREATE TABLE IF NOT EXISTS dic_sections (
        section_id TEXT PRIMARY KEY, version_id TEXT NOT NULL,
        doc_id TEXT NOT NULL, heading TEXT NOT NULL, content TEXT,
        citations_json TEXT, status TEXT DEFAULT 'draft',
        origin TEXT DEFAULT 'ai_generated', assigned_to TEXT, reviewed_by TEXT,
        reviewed_at TEXT, created_at TEXT, created_by TEXT, tenant_id TEXT,
        classification TEXT)""",
    """CREATE TABLE IF NOT EXISTS nc_hardware_profiles (
        id TEXT PRIMARY KEY, vendor TEXT, model TEXT, model_family TEXT,
        device_type TEXT, eol_date TEXT, eos_date TEXT, tags TEXT,
        is_builtin INTEGER DEFAULT 0)""",
    """CREATE TABLE IF NOT EXISTS nc_device_profiles (
        id TEXT PRIMARY KEY, vendor TEXT, platform TEXT, description TEXT,
        commands_json TEXT, is_builtin INTEGER DEFAULT 0,
        created_by TEXT, created_at TEXT)""",
]

# docmod_* tables come from tests/conftest MINIMAL_ICDEV_SCHEMA — but that
# schema is only applied by the opt-in icdev_db fixture, so create the subset
# we exercise here directly on the shared get_connection() DB.
_DOCMOD_DDL_KEYS = (
    "docmod_scan_runs", "docmod_findings", "docmod_doc_scan_state",
    "docmod_catalog_entries", "docmod_catalog_audit",
)


@pytest.fixture()
def db():
    from tests.conftest import MINIMAL_ICDEV_SCHEMA
    from tools.db.storage import get_connection

    conn = get_connection()
    for ddl in _DDL:
        conn.execute(ddl)
    # Pull the docmod DDL blocks out of the shared schema string so the test
    # tables always match the canonical definitions.
    for stmt in MINIMAL_ICDEV_SCHEMA.split(";"):
        if any(k in stmt for k in _DOCMOD_DDL_KEYS) and "CREATE TABLE" in stmt:
            conn.execute(stmt)
    conn.commit()
    conn.close()
    yield


def _seed_doc(text_sections: list[tuple[str, str]], doc_id: str | None = None) -> str:
    from tools.db.storage import get_connection

    doc_id = doc_id or f"doc-{uuid.uuid4().hex[:8]}"
    version_id = f"{doc_id}_v1"
    conn = get_connection()
    conn.execute(
        "INSERT INTO dic_documents (doc_id, collection_id, title, status, origin, created_at) "
        "VALUES (%s,'col-t','T','approved','human_authored','2026-01-01')",
        (doc_id,),
    )
    conn.execute(
        "INSERT INTO dic_versions (version_id, doc_id, version_no, origin, status, created_at) "
        "VALUES (%s,%s,1,'human_authored','approved','2026-01-01')",
        (version_id, doc_id),
    )
    for i, (heading, content) in enumerate(text_sections):
        conn.execute(
            "INSERT INTO dic_sections (section_id, version_id, doc_id, heading, content, created_at) "
            "VALUES (%s,%s,%s,%s,%s,'2026-01-01')",
            (f"{doc_id}-s{i:03d}", version_id, doc_id, heading, content),
        )
    conn.commit()
    conn.close()
    return doc_id


class TlsStubPack:
    """Minimal deterministic pack: flags 'TLS 1.1' as deprecated_tech."""

    def __init__(self, snapshot: str = "static-evidence-v1"):
        from tools.doc_modernization.base_pack import DomainPack  # noqa: F401
        self.pack_id = "tls_stub"
        self.entity_types = ["protocol"]
        self.config = {}
        self._snapshot = snapshot

    def extract(self, text, chunk_ref):
        from tools.doc_modernization.base_pack import CandidateEntity
        return [
            CandidateEntity(label=m.group(0), entity_type="protocol",
                            pack_id=self.pack_id, chunk_ref=chunk_ref, raw_match=m.group(0))
            for m in re.finditer(r"TLS 1\.1", text)
        ]

    def evaluate(self, entity, conn):
        from tools.doc_modernization.base_pack import Verdict
        return Verdict(
            currency_verdict="deprecated", finding_type="deprecated_tech",
            severity="high", rationale="TLS 1.1 is deprecated (rulebook crypto-tls-01)",
            confidence=1.0,
            evidence=[{"source": "rule:crypto-tls-01", "detail": "TLS <1.2 deprecated", "date": ""}],
        )

    def recommend(self, entity, verdict, conn):
        from tools.doc_modernization.base_pack import Replacement
        return Replacement(label="TLS 1.2 or higher", source="rulebook",
                           source_ref="crypto-tls-01")

    def evidence_snapshot(self, conn):
        return self._snapshot


# ── constants ─────────────────────────────────────────────────────────────────

def test_constants_sanity():
    from tools.doc_modernization import constants as c

    assert "deprecated_tech" in c.FINDING_TYPES
    assert "catalog_gap" in c.FINDING_TYPES
    assert set(c.APPEND_ONLY_TABLES) == {
        "docmod_findings", "docmod_scan_runs", "docmod_catalog_audit"
    }
    assert c.CONF_INCLUDE == 0.7 and c.CONF_ABSTAIN == 0.4
    # vocab mirrors migration 257 CHECK constraints
    assert "aging" not in c.CURRENCY_VERDICTS
    assert {"retired", "divergent"} <= set(c.CURRENCY_VERDICTS)
    assert {"resolved", "stale"} <= set(c.FINDING_STATES)
    assert c.SCAN_SCOPE_TYPES == ["all", "collection", "doc"]


def test_append_only_tables_registered_in_hook():
    from pathlib import Path

    hook = (Path(__file__).resolve().parents[2] / "tools" / "hooks" / "shared_checks.py").read_text(
        encoding="utf-8"
    )
    for t in ("docmod_findings", "docmod_scan_runs", "docmod_catalog_audit"):
        assert f'"{t}"' in hook, f"{t} missing from APPEND_ONLY_TABLES"


# ── pack loader ───────────────────────────────────────────────────────────────

def test_load_packs_loads_all_enabled_packs():
    from tools.doc_modernization.pack_loader import load_packs

    packs = load_packs(force=True)
    assert set(packs) == {
        "crypto_protocols", "network_hardware", "software", "policy_refs",
        "change_control", "evidence_currency",
    }


def test_load_packs_validation_errors(tmp_path, monkeypatch):
    from tools.doc_modernization import pack_loader

    monkeypatch.setattr(pack_loader, "PACKS_DIR", tmp_path)
    (tmp_path / "bad.yaml").write_text("pack_id: x\nlabel: X\n", encoding="utf-8")
    with pytest.raises(pack_loader.PackValidationError, match="missing required key"):
        pack_loader.load_packs(force=True)

    (tmp_path / "bad.yaml").write_text(
        "pack_id: x\nlabel: X\nevaluator: no.such.Module\nentity_types: [protocol]\n",
        encoding="utf-8",
    )
    with pytest.raises(pack_loader.PackValidationError, match="not importable"):
        pack_loader.load_packs(force=True)


# ── scanner ───────────────────────────────────────────────────────────────────

def test_scan_finds_deprecated_tls_and_dedupes(db):
    from tools.db.storage import get_connection
    from tools.doc_modernization import get_findings
    from tools.doc_modernization.scanner import scan_document

    doc_id = _seed_doc([
        ("Security", "All services shall use TLS 1.1 for transport."),
        ("Appendix", "Legacy note: TLS 1.1 remains in scope."),  # same entity again
    ])
    conn = get_connection()
    result = scan_document(doc_id, conn=conn, packs={"tls_stub": TlsStubPack()})
    conn.close()

    assert result["scanned"] is True
    assert result["findings_new"] == 1  # deduped across chunks

    open_f = get_findings(doc_id=doc_id, state="open")
    assert len(open_f) == 1
    f = open_f[0]
    assert f["finding_type"] == "deprecated_tech"
    assert f["currency_verdict"] == "deprecated"
    assert f["recommended_replacement"] == "TLS 1.2 or higher"
    assert "rule:crypto-tls-01" in (f["evidence_json"] or "")


def test_incremental_skip_and_supersede(db):
    from tools.db.storage import get_connection
    from tools.doc_modernization import get_findings
    from tools.doc_modernization.scanner import scan_document

    doc_id = _seed_doc([("Security", "Use TLS 1.1 everywhere.")])
    pack = TlsStubPack()
    conn = get_connection()
    r1 = scan_document(doc_id, conn=conn, packs={"tls_stub": pack})
    assert r1["findings_new"] == 1

    # Unchanged doc + unchanged evidence => skipped
    r2 = scan_document(doc_id, conn=conn, packs={"tls_stub": pack})
    assert r2["scanned"] is False and r2["reason"] == "unchanged"

    # New approved version without the entity => finding superseded
    v2 = f"{doc_id}_v2"
    conn.execute(
        "INSERT INTO dic_versions (version_id, doc_id, version_no, origin, status, created_at) "
        "VALUES (%s,%s,2,'human_authored','approved','2026-02-01')",
        (v2, doc_id),
    )
    conn.execute(
        "INSERT INTO dic_sections (section_id, version_id, doc_id, heading, content, created_at) "
        "VALUES (%s,%s,%s,'Security','Use TLS 1.3 everywhere.','2026-02-01')",
        (f"{doc_id}-v2-s000", v2, doc_id),
    )
    conn.commit()
    r3 = scan_document(doc_id, conn=conn, packs={"tls_stub": pack})
    conn.close()

    assert r3["scanned"] is True
    assert r3["findings_resolved"] == 1
    latest = get_findings(doc_id=doc_id)
    assert len(latest) == 1 and latest[0]["state"] == "superseded"
    assert latest[0]["supersedes_id"]  # chain intact
    assert get_findings(doc_id=doc_id, state="open") == []


def test_confidence_threshold_filters_findings(db, monkeypatch):
    from tools.db.storage import get_connection
    from tools.doc_modernization import pack_loader
    from tools.doc_modernization.scanner import scan_document

    monkeypatch.setattr(pack_loader, "load_config", lambda: {"confidence_threshold": 0.9})

    class LowConfPack(TlsStubPack):
        def evaluate(self, entity, conn):
            v = super().evaluate(entity, conn)
            v.confidence = 0.5
            return v

    doc_id = _seed_doc([("S", "TLS 1.1 here.")])
    conn = get_connection()
    # scanner imports load_config from pack_loader at module import; patch there too
    from tools.doc_modernization import scanner as scanner_mod
    monkeypatch.setattr(scanner_mod, "load_config", lambda: {"confidence_threshold": 0.9})
    result = scan_document(doc_id, conn=conn, packs={"tls_stub": LowConfPack()})
    conn.close()
    assert result["findings_new"] == 0


# ── catalog providers ─────────────────────────────────────────────────────────

def _seed_hw_profile(model: str, vendor: str = "Cisco", device_type: str = "switch",
                     eos_date: str | None = None, eol_date: str | None = None,
                     model_family: str = ""):
    from tools.db.storage import get_connection

    conn = get_connection()
    conn.execute(
        "INSERT INTO nc_hardware_profiles (id, vendor, model, model_family, device_type, "
        "eol_date, eos_date, tags) VALUES (%s,%s,%s,%s,%s,%s,%s,'[]')",
        (f"hw-{uuid.uuid4().hex[:6]}", vendor, model, model_family, device_type,
         eol_date, eos_date),
    )
    conn.commit()
    conn.close()


def test_network_adapter_reads_curated_profiles(db):
    from tools.doc_modernization.catalog import NetworkCatalogAdapter

    _seed_hw_profile("Catalyst 9300")                                   # current
    _seed_hw_profile("Catalyst 6500", eos_date="2020-01-01")            # past EOS
    _seed_hw_profile("Catalyst 3750", eol_date="2016-01-01")            # past EOL

    adapter = NetworkCatalogAdapter()
    approved = adapter.get_approved("network_hardware")
    deprecated = adapter.get_deprecated("network_hardware")

    assert any(e.product == "Catalyst 9300" for e in approved)
    statuses = {e.product: e.status for e in deprecated}
    assert statuses.get("Catalyst 6500") == "deprecated"
    assert statuses.get("Catalyst 3750") == "retired"
    assert all(e.provider_source == "network_canvas" for e in approved + deprecated)
    # other domains served empty
    assert adapter.get_approved("software") == []


def test_merged_catalog_labels_sources_and_lookup(db):
    from tools.doc_modernization.catalog import (
        GenericStoreProvider,
        MergedCatalog,
        NetworkCatalogAdapter,
    )
    from tools.db.storage import get_connection

    _seed_hw_profile("Nexus 9500", device_type="switch")
    conn = get_connection()
    conn.execute(
        "INSERT INTO docmod_catalog_entries (entry_id, domain, category, vendor, product, "
        "status, source, created_at) VALUES ('cat-1','network_hardware','switch','Arista',"
        "'7050X','approved','manual','2026-01-01')",
    )
    conn.commit()
    conn.close()

    merged = MergedCatalog([GenericStoreProvider(), NetworkCatalogAdapter()])
    approved = merged.get_approved("network_hardware")
    sources = {e.product: e.provider_source for e in approved}
    assert sources.get("7050X") == "generic"
    assert sources.get("Nexus 9500") == "network_canvas"

    hit = merged.lookup("network_hardware", "the old Nexus 9500 chassis")
    assert hit is not None and hit.product == "Nexus 9500"
    assert merged.lookup("network_hardware", "completely unknown device") is None


def test_propose_from_defacto_creates_draft_and_audit(db):
    from tools.doc_modernization.catalog import propose_from_defacto
    from tools.db.storage import get_connection

    entry_id = propose_from_defacto({
        "domain": "software", "category": "os", "vendor": "Canonical",
        "product": "Ubuntu", "version": "24.04",
    }, actor="tester")

    conn = get_connection()
    entry = dict(conn.execute(
        "SELECT * FROM docmod_catalog_entries WHERE entry_id=%s", (entry_id,)
    ).fetchone())
    audits = [dict(r) for r in conn.execute(
        "SELECT * FROM docmod_catalog_audit WHERE entry_id=%s", (entry_id,)
    ).fetchall()]
    conn.close()

    assert entry["source"] == "promoted_from_defacto"
    assert audits and audits[0]["event_type"] == "promote" and audits[0]["actor"] == "tester"
