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

def test_sync_writes_policy_evidence_row(db, monkeypatch):
    from tools.doc_modernization import nist_pubs_sync as nps

    monkeypatch.setattr(nps, "_fetch_feed", lambda url, timeout: (_RSS, "ok"))
    out = nps.sync(force=True)
    assert out["synced"] == 2
    assert out["fetch_status"] == "ok"

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

    def _boom(url, timeout):
        raise AssertionError("feed must not be fetched inside the cadence window")

    monkeypatch.setattr(nps, "_fetch_feed", _boom)
    out = nps.sync(force=False)
    assert out["synced"] == 0 and "cadence" in out["skipped"]
    # A cadence skip fetched nothing — it must not be reported as a fetch failure.
    assert out["fetch_status"] == "not_attempted"

    # force=True bypasses the cadence gate and fetches.
    monkeypatch.setattr(nps, "_fetch_feed", lambda url, timeout: (_ATOM, "ok"))
    out2 = nps.sync(force=True)
    assert out2["synced"] == 1


# ── air-gap: skip-clean, never raise ─────────────────────────────────────────

def test_sync_offline_flag_skips(db, monkeypatch):
    from tools.doc_modernization import nist_pubs_sync as nps

    monkeypatch.setattr(nps, "_config", lambda: {"offline": True})
    monkeypatch.setattr(nps, "_fetch_feed",
                        lambda url, timeout: (_ for _ in ()).throw(AssertionError("no fetch")))
    out = nps.sync()
    assert out["synced"] == 0 and "offline" in out["skipped"]
    assert out["fetch_status"] == "offline"
    # An air-gapped site loads its substrate deliberately (--seed / --import);
    # the offline branch must not quietly do it on the site's behalf.
    assert "seeded" not in out


def test_sync_feed_unavailable_falls_back_to_seed(db, monkeypatch):
    from tools.doc_modernization import nist_pubs_sync as nps

    # Egress down: _fetch_feed returns no body. sync() must not raise, must not
    # write nist.gov rows -- and, because the cache is EMPTY, must fall back to
    # the static seed so policy_refs is never left without a substrate.
    monkeypatch.setattr(nps, "_fetch_feed", lambda url, timeout: (None, "unreachable"))
    out = nps.sync(force=True)
    assert out["synced"] == 0 and "unreachable" in out["skipped"]
    assert out["fetch_status"] == "unreachable"
    assert out["seeded"] > 0 and out["cache_rows"] == out["seeded"]

    row = nps.get_latest_revision("SP 800-53")
    assert row is not None and row["source"] == "seed"   # seeded, not fabricated live


def test_seed_fallback_does_not_touch_a_populated_cache(db, monkeypatch):
    from tools.doc_modernization import nist_pubs_sync as nps

    # A live row already exists -> the fallback must leave it alone. Overwriting
    # a fresher nist.gov row with the static seed would silently regress the
    # cache every time egress blipped.
    _seed_nist_pub("SP 800-53", 9, source="nist.gov")
    monkeypatch.setattr(nps, "_fetch_feed", lambda url, timeout: (None, "unreachable"))
    out = nps.sync(force=True)

    assert out["seeded"] == 0 and out["cache_status"] == "populated"
    row = nps.get_latest_revision("SP 800-53")
    assert row["revision_num"] == 9 and row["source"] == "nist.gov"


def test_sync_reports_404_distinctly_from_offline(db, monkeypatch):
    """A retired feed URL must NOT read as air-gap (cef-fnd-02).

    The CSRC RSS feed this module was built against answers HTTP 404 as of
    2026-08-17. The old code collapsed that into "feed unavailable (offline?)"
    -- the same string a genuinely air-gapped host produces -- so a broken
    configuration was indistinguishable from the posture the module is designed
    for. Exercise the real _fetch_feed so the HTTPError branch itself is under
    test, not a stubbed status string.
    """
    import urllib.error

    from tools.doc_modernization import nist_pubs_sync as nps

    def _raise_404(req, timeout=None):
        raise urllib.error.HTTPError(
            req.full_url, 404, "Not Found", hdrs=None, fp=None)

    monkeypatch.setattr("urllib.request.urlopen", _raise_404)
    out = nps.sync(force=True)

    assert out["fetch_status"] == "feed_not_found"
    assert "404" in out["skipped"]
    assert "offline" not in out["skipped"].lower()
    # ...and the substrate is still provided rather than left at zero rows.
    assert out["seeded"] > 0


def test_sync_reports_empty_feed_distinctly(db, monkeypatch):
    from tools.doc_modernization import nist_pubs_sync as nps

    # 200 OK but nothing parseable: a live-but-wrong endpoint. Distinct from
    # both a 404 and an unreachable host, because the fix differs for each.
    monkeypatch.setattr(
        nps, "_fetch_feed",
        lambda url, timeout: ("<rss version='2.0'><channel/></rss>", "ok"))
    out = nps.sync(force=True)
    assert out["synced"] == 0 and out["fetch_status"] == "empty_feed"


def test_cache_row_count_separates_absent_from_empty(db):
    """An ABSENT table and an EMPTY one are different failures.

    docmod_nist_pubs was absent on the live database until cef-fnd-02 (its DDL
    landed as flat migration 282, whose version the squash baseline had already
    recorded applied, so it never ran). "No writer has run" and "no migration
    has run" send you to different fixes, so they must not both report 0.
    """
    from tools.doc_modernization.nist_pubs_sync import _cache_row_count

    conn = _conn()
    try:
        assert _cache_row_count(conn) == 0          # exists, empty (db fixture)
    finally:
        conn.close()

    missing = sqlite3.connect(":memory:")
    missing.row_factory = sqlite3.Row
    try:
        assert _cache_row_count(missing) is None    # absent -> None, never 0
    finally:
        missing.close()


def test_fetch_feed_refuses_non_https():
    from tools.doc_modernization.nist_pubs_sync import _fetch_feed

    body, status = _fetch_feed("http://insecure.example/feed.xml", 5)
    assert body is None and status == "not_https"


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
