# CUI // SP-CTI
"""docmod external-feed wiring (dmx-loop-02):

  * NIST publication RSS/Atom poller -> docmod_nist_pubs (policy_refs evidence),
    cadence gating, and air-gap skip-clean behavior.
  * policy_refs dynamic superseded-revision detection from the cache.
  * CVE -> docmod bridge: products cited in documents matched against the
    existing cve_triage store raise HITL drift (mocked acoic sink); a CVE for a
    NON-cited product raises nothing; a missing store degrades to zero emissions.

All network / CVE-store access is mocked; the shared conftest SQLite DB is used
for the docmod_nist_pubs / dic_* / cve_triage tables.
"""
from __future__ import annotations

import re
import sqlite3
import uuid
from datetime import datetime, timezone

import pytest

from tools.doc_modernization.base_pack import CandidateEntity, ChunkRef

_REF = ChunkRef(doc_id="doc-t", version_id="doc-t_v1", section="Standards")

_DDL = [
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
    """CREATE TABLE IF NOT EXISTS kg_nodes (
        id TEXT PRIMARY KEY, graph_id TEXT, label TEXT, entity_type TEXT,
        properties TEXT, embedding TEXT, centrality REAL, created_at TEXT)""",
    """CREATE TABLE IF NOT EXISTS cve_triage (
        id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT,
        cve_id TEXT NOT NULL, package_name TEXT NOT NULL, package_version TEXT,
        severity TEXT, cvss_score REAL, triage_rationale TEXT)""",
]

_DOCMOD_DDL_KEYS = ("docmod_nist_pubs",)


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
    # Clean slate for the file-scoped shared SQLite DB.
    for t in ("docmod_nist_pubs", "cve_triage", "dic_sections", "dic_versions",
              "dic_documents"):
        conn.execute(f"DELETE FROM {t}")
    conn.commit()
    conn.close()
    yield


def _conn():
    from tools.db.storage import get_connection
    return get_connection()


def _seed_nist_pub(pub_id, revision_num, source="seed", synced_at=None):
    # Default to "now" so the cadence-gate test stays within the window
    # regardless of wall-clock date; a fixed literal here is a time-bomb that
    # silently ages past nist_pubs_cadence_hours (24h).
    synced_at = synced_at or datetime.now(timezone.utc).isoformat()
    conn = _conn()
    conn.execute("DELETE FROM docmod_nist_pubs WHERE pub_id=%s", (pub_id,))
    conn.execute(
        "INSERT INTO docmod_nist_pubs (id, pub_id, latest_revision, revision_num, "
        "source, synced_at) VALUES (%s,%s,%s,%s,%s,%s)",
        (f"np-{uuid.uuid4().hex[:8]}", pub_id, f"Rev {revision_num}",
         revision_num, source, synced_at),
    )
    conn.commit()
    conn.close()


def _seed_doc(sections, doc_id=None):
    doc_id = doc_id or f"doc-{uuid.uuid4().hex[:8]}"
    version_id = f"{doc_id}_v1"
    conn = _conn()
    conn.execute(
        "INSERT INTO dic_documents (doc_id, collection_id, title, status, origin, "
        "classification, created_at) VALUES (%s,'col-t','T','approved',"
        "'human_authored','CUI','2026-01-01')",
        (doc_id,),
    )
    conn.execute(
        "INSERT INTO dic_versions (version_id, doc_id, version_no, origin, status, "
        "created_at) VALUES (%s,%s,1,'human_authored','approved','2026-01-01')",
        (version_id, doc_id),
    )
    for i, (heading, content) in enumerate(sections):
        conn.execute(
            "INSERT INTO dic_sections (section_id, version_id, doc_id, heading, "
            "content, created_at) VALUES (%s,%s,%s,%s,%s,'2026-01-01')",
            (f"{doc_id}-s{i:03d}", version_id, doc_id, heading, content),
        )
    conn.commit()
    conn.close()
    return doc_id


# ── NIST feed parser ─────────────────────────────────────────────────────────

_RSS = """<?xml version="1.0"?>
<rss version="2.0"><channel>
  <item><title>SP 800-53 Rev. 4, older release</title><link>https://csrc.nist.gov/x/r4</link></item>
  <item><title>SP 800-53 Rev. 5, Security and Privacy Controls</title>
        <link>https://csrc.nist.gov/pubs/sp/800/53/r5/final</link>
        <pubDate>Wed, 10 Dec 2020 00:00:00 EST</pubDate></item>
  <item><title>NIST SP 800-171 Rev. 3, Protecting CUI</title>
        <link>https://csrc.nist.gov/pubs/sp/800/171/r3/final</link></item>
  <item><title>Unrelated CSRC news item</title><link>https://x</link></item>
</channel></rss>"""

_ATOM = """<feed xmlns="http://www.w3.org/2005/Atom">
  <entry><title>NIST SP 800-63 Rev. 3 Digital Identity Guidelines</title>
    <link href="https://csrc.nist.gov/pubs/sp/800/63/r3/final"/>
    <updated>2017-06-22T00:00:00Z</updated></entry>
</feed>"""


def test_parse_feed_rss_keeps_highest_revision():
    from tools.doc_modernization.nist_pubs_sync import parse_feed

    rows = {r["pub_id"]: r for r in parse_feed(_RSS)}
    assert rows["SP 800-53"]["revision_num"] == 5      # Rev 5 wins over Rev 4
    assert rows["SP 800-53"]["latest_revision"] == "Rev 5"
    assert rows["SP 800-171"]["revision_num"] == 3
    assert "unrelated" not in " ".join(rows).lower()   # non-pub item dropped


def test_parse_feed_atom():
    from tools.doc_modernization.nist_pubs_sync import parse_feed

    rows = {r["pub_id"]: r for r in parse_feed(_ATOM)}
    assert rows["SP 800-63"]["revision_num"] == 3
    assert rows["SP 800-63"]["url"].endswith("/r3/final")


def test_parse_feed_bad_xml_returns_empty():
    from tools.doc_modernization.nist_pubs_sync import parse_feed

    assert parse_feed("<not-xml") == []


# ── NIST poller: feed -> policy evidence row ─────────────────────────────────

def _cfg(**over):
    """Deterministic docmod config for the poller tests.

    The live catalog URL is blanked unless a test opts in, so sync() exercises
    exactly the source under test and never reaches the network.
    """
    base = {
        "offline": False,
        "nist_pubs_cadence_hours": 24,
        "nist_pubs_timeout_seconds": 5,
        "nist_pubs_catalog_url": "",
        "nist_pubs_feed_url": "https://feed.example/publications.xml",
    }
    base.update(over)
    return base


def _fetch_ok(body: bytes):
    """Stand in for nist_pubs_sync._fetch — returns (body, status)."""
    return lambda url, timeout: (body, "ok")


def test_sync_writes_policy_evidence_row(db, monkeypatch):
    from tools.doc_modernization import nist_pubs_sync as nps

    monkeypatch.setattr(nps, "_config", _cfg)
    monkeypatch.setattr(nps, "_fetch", _fetch_ok(_RSS.encode("utf-8")))
    out = nps.sync(force=True)
    assert out["synced"] == 2
    assert out["source"] == "feed"

    row = nps.get_latest_revision("SP 800-53")
    assert row is not None
    assert row["revision_num"] == 5
    assert row["source"] == "nist.gov"


# ── cadence gating ───────────────────────────────────────────────────────────

def test_sync_cadence_gates_recent_run(db, monkeypatch):
    from tools.doc_modernization import nist_pubs_sync as nps

    # A recent live sync already exists -> the next non-forced sync must skip
    # WITHOUT fetching the feed.
    _seed_nist_pub("SP 800-53", 5, source="nist.gov")
    monkeypatch.setattr(nps, "_config", _cfg)

    def _boom(url, timeout):
        raise AssertionError("feed must not be fetched inside the cadence window")

    monkeypatch.setattr(nps, "_fetch", _boom)
    out = nps.sync(force=False)
    assert out["synced"] == 0 and "cadence" in out["skipped"]

    # force=True bypasses the cadence gate and fetches.
    monkeypatch.setattr(nps, "_fetch", _fetch_ok(_ATOM.encode("utf-8")))
    out2 = nps.sync(force=True)
    assert out2["synced"] == 1


# ── air-gap: skip-clean, never raise ─────────────────────────────────────────

def test_sync_offline_flag_skips(db, monkeypatch):
    from tools.doc_modernization import nist_pubs_sync as nps

    monkeypatch.setattr(nps, "_config", lambda: _cfg(offline=True))
    monkeypatch.setattr(nps, "_fetch",
                        lambda url, timeout: (_ for _ in ()).throw(AssertionError("no fetch")))
    out = nps.sync()
    assert out["synced"] == 0 and "offline" in out["skipped"]


def test_sync_feed_unavailable_skips_clean(db, monkeypatch):
    from tools.doc_modernization import nist_pubs_sync as nps

    # Egress down: every source reports unreachable. sync() must not raise and
    # must not write rows.
    monkeypatch.setattr(nps, "_config", _cfg)
    monkeypatch.setattr(nps, "_fetch", lambda url, timeout: (None, "unreachable"))
    out = nps.sync(force=True)
    assert out["synced"] == 0 and "no live source" in out["skipped"]
    assert out["sources"]["feed"] == "unreachable"
    assert nps.get_latest_revision("SP 800-53") is None


def test_fetch_feed_refuses_non_https():
    from tools.doc_modernization.nist_pubs_sync import _fetch_feed

    assert _fetch_feed("http://insecure.example/feed.xml", 5) is None


# ── a dead URL is NOT an air-gap (cef-fnd-02) ────────────────────────────────

def test_fetch_separates_dead_url_from_no_egress(monkeypatch):
    """A 4xx means the URL is retired; a socket error means no egress.

    Merging the two is how the retired CSRC RSS feed reported a benign-looking
    "offline?" skip while never once landing a row.
    """
    import urllib.error
    import urllib.request

    from tools.doc_modernization import nist_pubs_sync as nps

    def _raise(exc):
        def _open(req, timeout=None):
            raise exc
        return _open

    monkeypatch.setattr(
        urllib.request, "urlopen",
        _raise(urllib.error.HTTPError("https://x/y.xml", 404, "Not Found", {}, None)),
    )
    assert nps._fetch("https://x/y.xml", 5)[1] == "url_dead_http_404"

    monkeypatch.setattr(urllib.request, "urlopen",
                        _raise(urllib.error.URLError("no route to host")))
    assert nps._fetch("https://x/y.xml", 5)[1] == "unreachable"

    # Never-configured and plaintext are their own answers, not "unreachable".
    assert nps._fetch("", 5)[1] == "not_configured"
    assert nps._fetch("http://x/y.xml", 5)[1] == "refused_non_https"


def test_sync_reports_dead_url_rather_than_offline(db, monkeypatch):
    from tools.doc_modernization import nist_pubs_sync as nps

    monkeypatch.setattr(nps, "_config", _cfg)
    monkeypatch.setattr(nps, "_fetch", lambda url, timeout: (None, "url_dead_http_404"))
    out = nps.sync(force=True)
    assert out["synced"] == 0
    # The operator must be able to tell "fix the URL" from "you are air-gapped".
    assert out["sources"]["feed"] == "url_dead_http_404"


# ── CSRC catalog (XLSX) parsing ──────────────────────────────────────────────

def _catalog_bytes(rows):
    """Build a minimal CSRC-shaped publications workbook in memory."""
    import io

    # NOT importorskip: openpyxl is a declared requirement and the catalog parser
    # is useless without it. Skipping here would leave the whole live-source path
    # unmeasured while still reporting green.
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Stage", "Substage", "PubID", "Series", "Publication Number",
               "Title", "Citation Date", "Release Date", "URL", "CurrentURL"])
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_parse_catalog_keeps_final_highest_revision():
    from tools.doc_modernization.nist_pubs_sync import parse_catalog

    body = _catalog_bytes([
        ["Final", "", "NIST SP 800-53r4", "SP", "800-53 Rev. 4", "Older", "", "2013", "", ""],
        ["Final", "", "NIST SP 800-53r5", "SP", "800-53 Rev. 5", "Current", "", "12/10/2020",
         "", "https://csrc.nist.gov/pubs/sp/800/53/r5/final"],
        ["Final", "", "NIST SP 800-171r3", "SP", "800-171 Rev. 3", "CUI", "", "05/14/2024", "", ""],
    ])
    rows = {r["pub_id"]: r for r in parse_catalog(body)}
    assert rows["SP 800-53"]["revision_num"] == 5
    assert rows["SP 800-53"]["latest_revision"] == "Rev 5"
    assert rows["SP 800-53"]["url"].endswith("/r5/final")
    assert rows["SP 800-171"]["revision_num"] == 3


def test_parse_catalog_excludes_drafts():
    """A DRAFT does not supersede a final publication.

    Caching a draft revision would flag every document citing the current final
    revision as superseded — a manufactured finding.
    """
    from tools.doc_modernization.nist_pubs_sync import parse_catalog

    body = _catalog_bytes([
        ["Final", "", "NIST SP 800-53r5", "SP", "800-53 Rev. 5", "Current", "", "2020", "", ""],
        ["Draft", "", "NIST SP 800-53r6", "SP", "800-53 Rev. 6", "IPD", "", "2026", "", ""],
    ])
    rows = {r["pub_id"]: r for r in parse_catalog(body)}
    assert rows["SP 800-53"]["revision_num"] == 5


def test_parse_catalog_skips_unrevised_publications():
    """SP 800-207 has no revision — inventing 'Rev 1' would fabricate evidence."""
    from tools.doc_modernization.nist_pubs_sync import parse_catalog

    body = _catalog_bytes([
        ["Final", "", "NIST SP 800-207", "SP", "800-207", "Zero Trust", "", "2020", "", ""],
    ])
    assert parse_catalog(body) == []


def test_parse_catalog_degrades_on_renamed_columns():
    """A schema change upstream yields nothing, not a half-understood catalog."""
    import io

    import openpyxl

    from tools.doc_modernization.nist_pubs_sync import parse_catalog

    wb = openpyxl.Workbook()
    wb.active.append(["Phase", "Series", "Number"])
    buf = io.BytesIO()
    wb.save(buf)
    assert parse_catalog(buf.getvalue()) == []


def test_parse_catalog_bad_bytes_returns_empty():
    from tools.doc_modernization.nist_pubs_sync import parse_catalog

    assert parse_catalog(b"not an xlsx at all") == []


def test_sync_prefers_catalog_over_feed(db, monkeypatch):
    from tools.doc_modernization import nist_pubs_sync as nps

    body = _catalog_bytes([
        ["Final", "", "NIST SP 800-53r5", "SP", "800-53 Rev. 5", "Current", "", "2020", "", ""],
    ])
    monkeypatch.setattr(nps, "_config",
                        lambda: _cfg(nist_pubs_catalog_url="https://csrc.example/pubs.xlsx"))

    def _fetch(url, timeout):
        if url.endswith(".xlsx"):
            return body, "ok"
        raise AssertionError("feed must not be fetched once the catalog succeeds")

    monkeypatch.setattr(nps, "_fetch", _fetch)
    out = nps.sync(force=True)
    assert out["synced"] == 1 and out["source"] == "catalog"
    assert nps.get_latest_revision("SP 800-53")["revision_num"] == 5


def test_refresh_seeds_when_live_sync_lands_nothing(db, monkeypatch, tmp_path):
    """An empty cache makes policy_refs answer 'unknown' forever."""
    from tools.doc_modernization import nist_pubs_sync as nps

    seed = tmp_path / "nist_pubs.yaml"
    seed.write_text(
        "publications:\n"
        "  SP 800-53:\n"
        "    latest_revision: Rev 5\n"
        "    revision_num: 5\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(nps, "SEED_PATH", seed)
    monkeypatch.setattr(nps, "_config", _cfg)
    monkeypatch.setattr(nps, "_fetch", lambda url, timeout: (None, "unreachable"))

    out = nps.refresh(force=True)
    assert out["sync"]["synced"] == 0
    assert out["seed"]["loaded"] == 1
    assert out["rows"] == 1
    row = nps.get_latest_revision("SP 800-53")
    # The seed fallback must never be presented as a live pull.
    assert row["source"] == "seed"


# ── policy_refs dynamic superseded-revision detection ────────────────────────

def test_policy_dynamic_flags_older_revision(db):
    from tools.doc_modernization.packs.policy_refs import PolicyRefsPack

    # NIST now publishes Rev 6; a document that cites Rev 5 is superseded.
    _seed_nist_pub("SP 800-53", 6, source="nist.gov")
    pack = PolicyRefsPack(config={"pack_id": "policy_refs"})

    ents = pack.extract("The system follows NIST SP 800-53 Rev 5 controls.", _REF)
    dyn = [e for e in ents if (e.attributes or {}).get("nist_pub_id") == "SP 800-53"]
    assert len(dyn) == 1 and dyn[0].attributes["cited_revision"] == 5

    conn = _conn()
    verdict = pack.evaluate(dyn[0], conn)
    assert verdict.is_finding
    assert verdict.finding_type == "superseded_standard"
    rep = pack.recommend(dyn[0], verdict, conn)
    conn.close()
    assert rep is not None and rep.label == "SP 800-53 Rev 6"


def test_policy_dynamic_current_revision_no_finding(db):
    from tools.doc_modernization.packs.policy_refs import PolicyRefsPack

    # Cited revision equals the latest -> current, no finding.
    _seed_nist_pub("SP 800-53", 5, source="nist.gov")
    pack = PolicyRefsPack(config={"pack_id": "policy_refs"})
    ents = pack.extract("Aligned to NIST SP 800-53 Rev 5.", _REF)
    dyn = [e for e in ents if (e.attributes or {}).get("nist_pub_id")]
    assert len(dyn) == 1
    conn = _conn()
    verdict = pack.evaluate(dyn[0], conn)
    conn.close()
    assert not verdict.is_finding


# ── CVE -> docmod bridge ─────────────────────────────────────────────────────

class _StubProductPack:
    """Extracts fixed product terms (stands in for network_hardware/software)."""

    def __init__(self, terms):
        self.pack_id = "network_hardware"
        self.entity_types = ["hardware_model"]
        self._terms = terms

    def extract(self, text, chunk_ref):
        out = []
        for term in self._terms:
            for _m in re.finditer(re.escape(term), text, re.IGNORECASE):
                out.append(CandidateEntity(
                    label=term, entity_type="hardware_model", pack_id=self.pack_id,
                    chunk_ref=chunk_ref, raw_match=term))
        return out


def _seed_cve(cve_id, package_name, severity="high", cvss=7.5):
    conn = _conn()
    conn.execute(
        "INSERT INTO cve_triage (project_id, cve_id, package_name, severity, "
        "cvss_score, triage_rationale) VALUES ('proj-1',%s,%s,%s,%s,'triaged')",
        (cve_id, package_name, severity, cvss),
    )
    conn.commit()
    conn.close()


def test_cve_bridge_emits_only_for_cited_products(db, monkeypatch):
    from tools.doc_modernization import cve_bridge

    doc_id = _seed_doc([("Core", "The core layer uses a Catalyst 6500 switch.")])
    _seed_cve("CVE-2021-0001", "Catalyst 6500", severity="high")
    _seed_cve("CVE-2021-0002", "nginx", severity="critical")  # NOT cited

    calls = []

    def _fake_handle_drift(event, ctx=None):
        calls.append(event)
        return {"event_id": f"ev-{len(calls)}", "enqueued": ["q1"], "controls": {}}

    from tools.document_intelligence import acoic
    monkeypatch.setattr(acoic, "handle_drift", _fake_handle_drift)

    conn = _conn()
    out = cve_bridge.bridge_cves(
        conn=conn, packs=[_StubProductPack(["Catalyst 6500", "nginx"])])
    conn.close()

    assert out["emitted"] == 1 and out["matched"] == 1
    assert len(calls) == 1
    ev = calls[0]
    assert ev["source"] == "docmod.cve"
    assert ev["entity"] == "Catalyst 6500"
    assert ev["cve_id"] == "CVE-2021-0001"
    assert ev["document_id"] == doc_id
    assert ev["control_ids"] == ["RA-5", "SI-2"]
    assert ev["dedup_key"] and ev["finding_type"] == "vulnerable_component"
    # The non-cited nginx CVE never produced a drift event.
    assert all(c["cve_id"] != "CVE-2021-0002" for c in calls)


def test_cve_bridge_no_cves_skips(db, monkeypatch):
    from tools.doc_modernization import cve_bridge

    _seed_doc([("Core", "Uses a Catalyst 6500 switch.")])
    # Empty store -> skip cleanly, emit nothing, do not call the sink.
    monkeypatch.setattr(cve_bridge, "_load_cves", lambda conn, project_id=None: [])
    called = []
    from tools.document_intelligence import acoic
    monkeypatch.setattr(acoic, "handle_drift",
                        lambda event, ctx=None: called.append(event))

    conn = _conn()
    out = cve_bridge.bridge_cves(conn=conn, packs=[_StubProductPack(["Catalyst 6500"])])
    conn.close()
    assert out["emitted"] == 0 and not called
    assert out.get("skipped")


def test_cve_load_graceful_when_store_absent():
    from tools.doc_modernization.cve_bridge import _load_cves

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    try:
        assert _load_cves(conn) == []          # no cve_triage table -> [] (no raise)
    finally:
        conn.close()


def test_product_match_rules():
    from tools.doc_modernization.cve_bridge import _product_matches

    assert _product_matches("Catalyst 6500", "catalyst 6500")
    assert _product_matches("Cisco Catalyst 6500 Switch", "Catalyst 6500")
    assert not _product_matches("Catalyst 6500", "nginx")
    assert not _product_matches("Catalyst 6500", "")      # empty package
    assert not _product_matches("X", "ab")                # package too short
