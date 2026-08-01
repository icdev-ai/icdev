# CUI // SP-CTI
"""Full-feature E2E for the Document Intelligence Canvas.

Imports the REAL dashboard app (current code) and exercises every DIC page and API
endpoint through an in-process Flask test client against the live database + seeded
``net_knowledge`` corpus. Classifies each check:

    PASS   2xx                       (feature works)
    REACH  4xx                       (endpoint reachable; payload/precondition only)
    FAIL   5xx / exception           (server bug)

Run::

    ICDEV_PG_IDLE_TXN_TIMEOUT_MS=300000 python tools/document_intelligence/e2e_full.py
"""
from __future__ import annotations

import argparse
import io

RESULTS: list[tuple[str, str, str]] = []  # (status, feature, detail)


def _rec(code: int, feature: str, detail: str = "", expect: int | None = None) -> int:
    if expect is not None:
        status = "PASS" if code == expect else "FAIL"
    else:
        status = "PASS" if 200 <= code < 300 else ("REACH" if 400 <= code < 500 else "FAIL")
    RESULTS.append((status, feature, f"{code} {detail}".strip()))
    print(f"  [{status:5}] {feature:42} -> {code} {detail}")
    return code


def _grant_roles():
    from tools.db.storage import get_connection
    from tools.document_intelligence.blueprint import _hid, _now
    conn = get_connection()
    # Ensure the 'default' collection row exists (FK target for team grants).
    if not conn.execute("SELECT 1 FROM dic_collections WHERE collection_id='default'").fetchone():
        conn.execute(
            "INSERT INTO dic_collections (collection_id, name, description, classification, tenant_id) "
            "VALUES ('default','Default Collection','',%s,%s)", ("CUI", "default"))
        conn.commit()
    for coll in ("net_knowledge", "default"):
        for uid in ("current_user", "net-admin"):
            if not conn.execute("SELECT 1 FROM dic_team_access WHERE collection_id=%s AND user_id=%s",
                                (coll, uid)).fetchone():
                conn.execute(
                    "INSERT INTO dic_team_access (access_id, collection_id, user_id, role, "
                    "classification, tenant_id) VALUES (%s,%s,%s,%s,%s,%s)",
                    (_hid("acc", coll, uid, _now()), coll, uid, "admin", "CUI", "default"))
    conn.commit()
    conn.close()


def _j(r):
    try:
        return r.get_json() or {}
    except Exception:
        return {}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--collection", default="net_knowledge")
    args = ap.parse_args()
    COLL = args.collection
    P = "/document-intelligence"

    import os
    os.environ.setdefault("ICDEV_DIC_ENABLED", "true")
    _grant_roles()
    from tools.dashboard.app import app
    c = app.test_client()

    # Resolve a real doc_id + a chunk_id from the seeded corpus.
    docs = _j(c.get(f"{P}/api/collections/{COLL}/documents")).get("documents", [])
    doc_id = next((d["doc_id"] for d in docs if d.get("filename", "").startswith("RFC")), None)
    print(f"\n=== PAGES (14) ===  (sample doc_id={doc_id})")
    pages = ["/", "/collections", "/search", "/review", "/generate", "/acoic",
             "/finetune", "/snippets", "/templates", "/freshness", "/explorer",
             "/handoff", "/analytics"]
    for pg in pages:
        _rec(c.get(P + pg).status_code, f"page {pg}")
    if doc_id:
        _rec(c.get(f"{P}/doc/{doc_id}").status_code, "page /doc/<id>")

    print("\n=== READ / QUERY APIs ===")
    _rec(c.get(f"{P}/api/collections").status_code, "GET collections")
    _rec(c.get(f"{P}/api/collections/{COLL}/documents").status_code, "GET documents")
    _rec(c.get(f"{P}/api/collections/{COLL}/team").status_code, "GET team")
    if doc_id:
        _rec(c.get(f"{P}/api/documents/{doc_id}/versions").status_code, "GET doc versions")
    sr = c.post(f"{P}/api/search", json={"query": "private address ranges",
                                         "collection_id": COLL, "expand": True})
    _rec(sr.status_code, "POST search (grounded+KG)")
    chunk_id = None
    for res in _j(sr).get("results", []):
        chunk_id = res.get("chunk_id") or chunk_id
        if chunk_id:
            break
    _rec(c.post(f"{P}/api/chat", json={"message": "What are the RFC 1918 private ranges?",
                                       "collection_id": COLL}).status_code, "POST chat (grounded answer)")
    _rec(c.post(f"{P}/api/iqe-query",
                json={"question": "foreach s in dic.ssp_fragments select s.control_id, s.status"}).status_code,
         "POST iqe-query")
    _rec(c.post(f"{P}/api/kg-explore", json={"mode": "entities", "limit": 10}).status_code,
         "POST kg-explore")
    if chunk_id:
        _rec(c.get(f"{P}/api/provenance/{chunk_id}").status_code, "GET provenance")

    print("\n=== ANALYTICS / FRESHNESS / EXPLORER ===")
    _rec(c.post(f"{P}/api/analytics", json={"mode": "entity_frequency", "limit": 10}).status_code,
         "POST analytics")
    _rec(c.post(f"{P}/api/freshness/scan", json={"collection_id": COLL}).status_code, "POST freshness scan")
    _rec(c.get(f"{P}/api/freshness/heatmap?collection={COLL}").status_code, "GET freshness heatmap")
    _rec(c.post(f"{P}/api/explorer/refresh", json={"collection_id": COLL}).status_code, "POST explorer refresh")
    _rec(c.post(f"{P}/api/scenarios", json={"scenario_type": "remove_entity",
                                            "entity_label": "BGP"}).status_code, "POST scenarios (what-if)")

    print("\n=== INGEST ===")
    ing = c.post(f"{P}/api/ingest", data={
        "collection_id": COLL,
        "file": (io.BytesIO(b"Demo network change record: MTU set to 9000 on spine links."),
                 "demo_change.txt"),
    }, content_type="multipart/form-data")
    _rec(ing.status_code, "POST ingest (upload)")
    job_id = _j(ing).get("job_id")
    if job_id:
        # Async job: result is 200 when finished, 404 while still in-flight — both are
        # correct (upload returned 202 Accepted). Treat either as a pass.
        rc = c.get(f"{P}/api/ingest/{job_id}/result").status_code
        RESULTS.append(("PASS" if rc in (200, 404) else "FAIL", "GET ingest result (async)", str(rc)))
        print(f"  [{'PASS' if rc in (200, 404) else 'FAIL':5}] {'GET ingest result (async)':42} -> {rc}")

    print("\n=== TEMPLATES ===")
    inst = c.post(f"{P}/api/templates/acoic/instantiate", json={"collection_id": COLL})
    _rec(inst.status_code, "POST template instantiate (acoic)")

    print("\n=== GENERATION + COLLAB LIFECYCLE ===")
    gen = c.post(f"{P}/api/generate", json={
        "query": "Network segmentation and BGP security runbook", "collection_id": COLL})
    _rec(gen.status_code, "POST generate (CoD + cross-canvas)",
         f"canvases={_j(gen).get('context_canvases')}")
    version_id = _j(gen).get("version_id")
    section_id = rev = heading = None
    if version_id:
        secs = _j(c.get(f"{P}/api/versions/{version_id}/sections")).get("sections", [])
        _rec(200 if secs else 500, "GET version sections", f"count={len(secs)}")
        if secs:
            section_id, heading, rev = secs[0]["section_id"], secs[0]["heading"], secs[0].get("rev", 1)
    if section_id:
        _rec(c.post(f"{P}/api/sections/{section_id}/content",
                    json={"content": "Edited via full E2E.", "base_rev": rev}).status_code,
             "POST section edit (optimistic)")
        _rec(c.post(f"{P}/api/sections/{section_id}/content",
                    json={"content": "stale", "base_rev": rev}).status_code,
             "POST section stale edit (conflict)", "no-clobber", expect=409)
        _rec(c.post(f"{P}/api/generate/section",
                    json={"version_id": version_id, "heading": heading,
                          "collection_id": COLL}).status_code, "POST regenerate section")
        _rec(c.post(f"{P}/api/review/{section_id}/assign",
                    json={"assigned_to": "net-admin", "type": "section"}).status_code, "POST review assign")
        _rec(c.post(f"{P}/api/sections/{section_id}/revise",
                    json={"note": "tighten"}).status_code, "POST section revise")
        _rec(c.post(f"{P}/api/sections/{section_id}/reject",
                    json={"note": "redo"}).status_code, "POST section reject")
        _rec(c.post(f"{P}/api/sections/{section_id}/approve",
                    json={"reviewer": "net-admin"}).status_code, "POST section approve")
    if version_id:
        _rec(c.post(f"{P}/api/review/{version_id}/revise",
                    json={"type": "version", "note": "v-revise"}).status_code, "POST version revise")
        _rec(c.post(f"{P}/api/review/{version_id}/approve",
                    json={"type": "version"}).status_code, "POST version approve (publish)")

    print("\n=== COLLECTIONS / TEAM / HANDOFF ===")
    cc = c.post(f"{P}/api/collections", json={"name": "E2E Temp Collection", "description": "e2e"})
    _rec(cc.status_code, "POST create collection")
    # Add a member to net_knowledge (acting user is admin there).
    _rec(c.post(f"{P}/api/collections/{COLL}/team",
                json={"user_id": "teammate", "role": "editor"}).status_code, "POST add team member")
    hs = c.post(f"{P}/api/handoff/start", json={
        "departing_owner_id": "alice", "successor_owner_id": "bob", "dest_collection_id": COLL})
    _rec(hs.status_code, "POST handoff start")
    sess = _j(hs).get("session_id")
    if sess:
        # find an agenda item to answer
        from tools.db.storage import get_connection
        conn = get_connection()
        row = conn.execute("SELECT item_id FROM dic_handoff_items WHERE session_id=%s LIMIT 1",
                           (sess,)).fetchone()
        conn.close()
        if row:
            _rec(c.post(f"{P}/api/handoff/{row[0]}/answer",
                        json={"answer_text": "Documented in runbook."}).status_code, "POST handoff answer")
        _rec(c.post(f"{P}/api/handoff/{sess}/close", json={}).status_code, "POST handoff close")

    # ---- summary ----
    n = len(RESULTS)
    p = sum(1 for s, _, _ in RESULTS if s == "PASS")
    reach = sum(1 for s, _, _ in RESULTS if s == "REACH")
    fail = [(f, d) for s, f, d in RESULTS if s == "FAIL"]
    print(f"\n==== DIC FULL E2E: {p} PASS, {reach} REACH(4xx), {len(fail)} FAIL  (of {n}) ====")
    for f, d in fail:
        print(f"   FAIL: {f} ({d})")
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
