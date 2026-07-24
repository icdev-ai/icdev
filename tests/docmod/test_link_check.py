# CUI // SP-CTI
"""dmx-ref-02 — egress-safe URL link-rot detection.

The network is NEVER touched: ``resolver`` and ``probe`` are injected so every
test is deterministic and offline. Coverage:
  * scheme rejection (https-only)
  * private / loopback / metadata IP rejection (literal host and via DNS)
  * DNS-rebinding-style rejection (public hostname resolving to an internal IP)
  * allowlist / denylist
  * broken (404/410) / moved (301) / head-hash-drift finding classification
  * air-gap skip status (unresolvable host + offline config)
  * per-sweep cap enforcement and finding persistence through docmod_findings
"""
from __future__ import annotations

import hashlib
import socket
import uuid

import pytest

from tools.doc_modernization import link_check as lc


# ── helpers ───────────────────────────────────────────────────────────────────

def _public_resolver(host, port, proto=0):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]


def _resolver_returning(ip):
    def _r(host, port, proto=0):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))]
    return _r


def _gaierror_resolver(host, port, proto=0):
    raise socket.gaierror("name resolution failed")


def _probe_map(responses):
    """responses: {url: (code, headers, body)}; body returned only for GET."""
    def _p(url, method, timeout):
        code, headers, body = responses[url]
        return (code, headers, None if method == "HEAD" else body)
    return _p


_CFG = {"timeout_seconds": 2, "max_redirects": 3, "head_hash_bytes": 4096}


# ── URL extraction ────────────────────────────────────────────────────────────

def test_extract_urls_basic_and_trailing_punctuation():
    text = (
        "See https://example.com/a and (https://example.com/b) plus "
        "https://example.com/c. Duplicate https://example.com/a again."
    )
    urls = lc.extract_urls(text)
    assert urls == [
        "https://example.com/a",
        "https://example.com/b",
        "https://example.com/c",
    ]


def test_extract_urls_empty():
    assert lc.extract_urls("") == []
    assert lc.extract_urls("no links here") == []


# ── egress guard: scheme ──────────────────────────────────────────────────────

@pytest.mark.parametrize("url", [
    "http://example.com/x",
    "ftp://example.com/x",
    "file:///etc/passwd",
    "gopher://example.com",
])
def test_scheme_rejected(url):
    allowed, reason, _ips = lc.egress_guard(url, _CFG, resolver=_public_resolver)
    assert allowed is False
    assert reason == "scheme_not_https"


def test_check_url_scheme_is_blocked_not_rotted():
    res = lc.check_url("http://example.com/x", _CFG, resolver=_public_resolver,
                       probe=_probe_map({}))
    assert res["status"] == "blocked"
    assert res["reason"] == "scheme_not_https"


# ── egress guard: internal address space ──────────────────────────────────────

@pytest.mark.parametrize("ip", ["127.0.0.1", "10.0.0.5", "192.168.1.1", "169.254.169.254", "::1"])
def test_literal_internal_ip_host_rejected(ip):
    host = f"[{ip}]" if ":" in ip else ip
    allowed, reason, _ips = lc.egress_guard(f"https://{host}/", _CFG, resolver=_public_resolver)
    assert allowed is False
    assert reason == "denied_ip_range"


def test_private_ip_via_dns_rejected():
    allowed, reason, ips = lc.egress_guard(
        "https://intranet.example.com/", _CFG, resolver=_resolver_returning("10.1.2.3"))
    assert allowed is False
    assert reason == "denied_ip_range"
    assert "10.1.2.3" in ips


def test_metadata_ip_via_dns_rejected():
    # The cloud instance-metadata address must never be reachable.
    allowed, reason, _ips = lc.egress_guard(
        "https://harmless-name.example.com/", _CFG,
        resolver=_resolver_returning("169.254.169.254"))
    assert allowed is False
    assert reason == "denied_ip_range"


def test_dns_rebinding_public_name_internal_ip_rejected():
    # A public-looking hostname that resolves to an internal address is refused:
    # the guard checks the RESOLVED address, not the name.
    res = lc.check_url(
        "https://totally-legit.example.com/data", _CFG,
        resolver=_resolver_returning("172.16.9.9"), probe=_probe_map({}))
    assert res["status"] == "blocked"
    assert res["reason"] == "denied_ip_range"


def test_public_ip_allowed():
    allowed, reason, ips = lc.egress_guard(
        "https://example.com/", _CFG, resolver=_public_resolver)
    assert allowed is True
    assert reason == "ok"
    assert ips == ["93.184.216.34"]


# ── egress guard: allow / deny lists ──────────────────────────────────────────

def test_denylist_blocks_host_and_subdomain():
    cfg = {**_CFG, "denylist": ["evil.test"]}
    for host in ("evil.test", "sub.evil.test"):
        allowed, reason, _ = lc.egress_guard(f"https://{host}/", cfg, resolver=_public_resolver)
        assert allowed is False and reason == "denylisted"


def test_allowlist_restricts_to_listed_hosts():
    cfg = {**_CFG, "allowlist": ["good.test"]}
    ok, reason, _ = lc.egress_guard("https://good.test/", cfg, resolver=_public_resolver)
    assert ok is True
    blocked, reason2, _ = lc.egress_guard("https://other.test/", cfg, resolver=_public_resolver)
    assert blocked is False and reason2 == "not_allowlisted"


def test_denylist_beats_allowlist():
    cfg = {**_CFG, "allowlist": ["x.test"], "denylist": ["x.test"]}
    ok, reason, _ = lc.egress_guard("https://x.test/", cfg, resolver=_public_resolver)
    assert ok is False and reason == "denylisted"


# ── check_url: health classification ──────────────────────────────────────────

@pytest.mark.parametrize("code", [404, 410])
def test_check_url_broken(code):
    url = "https://example.com/gone"
    res = lc.check_url(url, _CFG, resolver=_public_resolver,
                       probe=_probe_map({url: (code, {}, None)}))
    assert res["status"] == "broken"
    assert res["http_status"] == code


def test_check_url_ok():
    url = "https://example.com/live"
    res = lc.check_url(url, _CFG, resolver=_public_resolver,
                       probe=_probe_map({url: (200, {}, b"hello")}))
    assert res["status"] == "ok"
    assert res["head_hash"] == hashlib.sha256(b"hello").hexdigest()


def test_check_url_moved_permanent_redirect():
    src = "https://example.com/old"
    dst = "https://example.com/new"
    res = lc.check_url(src, _CFG, resolver=_public_resolver, probe=_probe_map({
        src: (301, {"location": dst}, None),
        dst: (200, {}, b"body"),
    }))
    assert res["status"] == "moved"
    assert res["redirect_target"] == dst


def test_check_url_transient_redirect_is_ok():
    src = "https://example.com/a"
    dst = "https://example.com/b"
    res = lc.check_url(src, _CFG, resolver=_public_resolver, probe=_probe_map({
        src: (302, {"location": dst}, None),
        dst: (200, {}, b"body"),
    }))
    assert res["status"] == "ok"


def test_check_url_hash_drift_changed():
    url = "https://example.com/doc"
    prev = hashlib.sha256(b"old-content").hexdigest()
    res = lc.check_url(url, _CFG, previous_hash=prev, resolver=_public_resolver,
                       probe=_probe_map({url: (200, {}, b"new-content")}))
    assert res["status"] == "changed"
    assert res["head_hash"] == hashlib.sha256(b"new-content").hexdigest()


def test_check_url_hash_stable_is_ok():
    url = "https://example.com/doc"
    same = hashlib.sha256(b"same").hexdigest()
    res = lc.check_url(url, _CFG, previous_hash=same, resolver=_public_resolver,
                       probe=_probe_map({url: (200, {}, b"same")}))
    assert res["status"] == "ok"


def test_check_url_redirect_loop_is_broken():
    a = "https://example.com/a"
    res = lc.check_url(a, {**_CFG, "max_redirects": 2}, resolver=_public_resolver,
                       probe=_probe_map({a: (302, {"location": a}, None)}))
    assert res["status"] == "broken"


# ── air-gap ───────────────────────────────────────────────────────────────────

def test_unresolvable_host_is_not_checked_never_rotted():
    res = lc.check_url("https://dead.example.com/x", _CFG,
                       resolver=_gaierror_resolver, probe=_probe_map({}))
    assert res["status"] == "not_checked"
    assert res["reason"] == "unresolved"


def test_is_airgap_from_offline_flag():
    assert lc._is_airgap({"_offline": True}) is True
    assert lc._is_airgap({"_offline": False}) is False


# ── DB-backed: cap, persistence, air-gap skip, supersede ──────────────────────

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
]
_DOCMOD_DDL_KEYS = ("docmod_scan_runs", "docmod_findings")


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
    conn.commit()
    conn.close()
    yield


def _seed_doc(urls_by_section, doc_id=None):
    """Seed one approved doc in its OWN collection (the shared test DB persists
    rows across tests, so every test scopes its sweep to this collection)."""
    from tools.db.storage import get_connection

    doc_id = doc_id or f"doc-{uuid.uuid4().hex[:8]}"
    collection_id = f"col-{uuid.uuid4().hex[:8]}"
    version_id = f"{doc_id}_v1"
    conn = get_connection()
    conn.execute(
        "INSERT INTO dic_documents (doc_id, collection_id, title, status, origin, "
        "classification, tenant_id, created_at) "
        "VALUES (%s,%s,'T','approved','human_authored','CUI','tenant-a','2026-01-01')",
        (doc_id, collection_id),
    )
    conn.execute(
        "INSERT INTO dic_versions (version_id, doc_id, version_no, origin, status, created_at) "
        "VALUES (%s,%s,1,'human_authored','approved','2026-01-01')",
        (version_id, doc_id),
    )
    for i, urls in enumerate(urls_by_section):
        body = "Refs: " + " ".join(urls)
        conn.execute(
            "INSERT INTO dic_sections (section_id, version_id, doc_id, heading, content, created_at) "
            "VALUES (%s,%s,%s,%s,%s,'2026-01-01')",
            (f"{version_id}_s{i}", version_id, doc_id, f"H{i}", body),
        )
    conn.commit()
    conn.close()
    return doc_id, collection_id


def _enable(monkeypatch, **overrides):
    cfg = {"enabled": True, "max_urls_per_sweep": 50, "timeout_seconds": 2,
           "max_redirects": 3, "head_hash_bytes": 4096, "_offline": False}
    cfg.update(overrides)
    monkeypatch.setattr(lc, "link_config", lambda: cfg)
    return cfg


def _findings(doc_id):
    from tools.doc_modernization import get_findings
    return [f for f in get_findings(doc_id=doc_id) if f.get("pack_id") == lc.PACK_ID]


def test_corpus_disabled_by_default(db, monkeypatch):
    monkeypatch.setattr(lc, "link_config", lambda: {"enabled": False})
    out = lc.check_corpus_links(probe=_probe_map({}), resolver=_public_resolver)
    assert out["enabled"] is False


def test_corpus_airgap_skips_clean(db, monkeypatch):
    _enable(monkeypatch, _offline=True)
    out = lc.check_corpus_links(probe=_probe_map({}), resolver=_public_resolver)
    assert out["enabled"] is True
    assert out["skipped"] is True
    assert out["status"] == "not checked (no egress)"


def test_corpus_cap_enforced(db, monkeypatch):
    _enable(monkeypatch)
    urls = [f"https://example.com/p{i}" for i in range(5)]
    doc_id, col = _seed_doc([urls])
    responses = {u: (200, {}, b"ok") for u in urls}
    out = lc.check_corpus_links(collection_id=col, cap=2, resolver=_public_resolver,
                                probe=_probe_map(responses))
    assert out["urls_checked"] == 2


def test_corpus_emits_broken_finding(db, monkeypatch):
    _enable(monkeypatch)
    url = "https://example.com/gone"
    doc_id, col = _seed_doc([[url]])
    out = lc.check_corpus_links(collection_id=col, resolver=_public_resolver,
                                probe=_probe_map({url: (404, {}, None)}))
    assert out["findings_new"] == 1
    fs = _findings(doc_id)
    assert len(fs) == 1
    f = fs[0]
    assert f["finding_type"] == "link_rot"
    assert f["currency_verdict"] == "retired"
    assert f["state"] == "open"
    assert f["entity_label"] == url


def test_corpus_emits_moved_finding_with_replacement(db, monkeypatch):
    _enable(monkeypatch)
    src, dst = "https://example.com/old", "https://example.com/new"
    doc_id, col = _seed_doc([[src]])
    out = lc.check_corpus_links(collection_id=col, resolver=_public_resolver, probe=_probe_map({
        src: (301, {"location": dst}, None),
        dst: (200, {}, b"body"),
    }))
    assert out["findings_new"] == 1
    f = _findings(doc_id)[0]
    assert f["currency_verdict"] == "deprecated"
    assert f["recommended_replacement"] == dst


def test_corpus_hash_drift_emits_changed_finding(db, monkeypatch):
    _enable(monkeypatch)
    url = "https://example.com/spec"
    doc_id, col = _seed_doc([[url]])
    # First sweep records the baseline hash (healthy -> no finding).
    out1 = lc.check_corpus_links(collection_id=col, resolver=_public_resolver,
                                 probe=_probe_map({url: (200, {}, b"v1")}))
    assert out1["findings_new"] == 0
    # Second sweep sees drifted content.
    out2 = lc.check_corpus_links(collection_id=col, resolver=_public_resolver,
                                 probe=_probe_map({url: (200, {}, b"v2-different")}))
    assert out2["findings_new"] == 1
    f = _findings(doc_id)[0]
    assert f["currency_verdict"] == "divergent"
    assert f["finding_type"] == "link_rot"


def test_corpus_supersedes_when_link_heals(db, monkeypatch):
    _enable(monkeypatch)
    url = "https://example.com/flaky"
    doc_id, col = _seed_doc([[url]])
    lc.check_corpus_links(collection_id=col, resolver=_public_resolver,
                          probe=_probe_map({url: (404, {}, None)}))
    assert _findings(doc_id)[0]["state"] == "open"
    # Link recovers -> latest state resolves to superseded (no open finding).
    lc.check_corpus_links(collection_id=col, resolver=_public_resolver,
                          probe=_probe_map({url: (200, {}, b"back")}))
    latest = _findings(doc_id)
    assert latest and all(f["state"] == "superseded" for f in latest)


def test_corpus_internal_url_blocked_no_finding(db, monkeypatch):
    _enable(monkeypatch)
    url = "https://internal.example.com/x"
    doc_id, col = _seed_doc([[url]])
    out = lc.check_corpus_links(collection_id=col, resolver=_resolver_returning("10.0.0.9"),
                                probe=_probe_map({}))
    # Blocked by the egress guard -> counted as checked, but not a rot finding.
    assert out["urls_checked"] == 1
    assert out["findings_new"] == 0
    assert _findings(doc_id) == []
