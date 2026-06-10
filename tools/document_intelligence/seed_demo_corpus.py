# CUI // SP-CTI
"""Seed the Document Intelligence Canvas with a real networking knowledge corpus.

Fetches public, authoritative networking documents from the web (IETF RFC editor —
stable plain-text URLs) spanning the document categories a network team actually
keeps in a knowledge base: SOPs, guidelines, policies, conventions, runbooks, and
whitepapers. Each is downloaded to a temp file and ingested through the real
``ingest_file`` pipeline (provider -> RAG chunk/embed -> KG bridge -> dic_documents
+ dic_versions), under a dedicated collection so they are visible at
``/document-intelligence/collections``.

Plain-text sources are used deliberately: the builtin text extractor handles them
without the optional ``pypdf`` dependency.

Run::

    python tools/document_intelligence/seed_demo_corpus.py --json          # fast (no LLM summary)
    python tools/document_intelligence/seed_demo_corpus.py --rich --json   # + LLM summary/metadata/KG
    python tools/document_intelligence/seed_demo_corpus.py --list          # show the corpus, fetch nothing
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import urllib.request
from pathlib import Path

COLLECTION_ID = "net_knowledge"
COLLECTION_NAME = "Networking Knowledge Base"
COLLECTION_DESC = "Networking SOPs, guidelines, policies, conventions, runbooks, and whitepapers (public IETF sources)."
# CUI is the platform's baseline marking; the RLS classification lattice is
# CUI-based, so rows marked 'UNCLASSIFIED' are filtered out of the dashboard.
# (Source docs are public, but the platform stores/serves them at CUI.)
CLASSIFICATION = "CUI"

# (category, title, filename, url) — one authoritative public doc per category.
CORPUS = [
    ("policy", "Site Security Handbook (RFC 2196)",
     "RFC2196_Site_Security_Handbook.txt",
     "https://www.rfc-editor.org/rfc/rfc2196.txt"),
    ("convention", "Address Allocation for Private Internets (RFC 1918)",
     "RFC1918_Private_Address_Allocation.txt",
     "https://www.rfc-editor.org/rfc/rfc1918.txt"),
    ("guideline", "Operational Security Requirements for Large ISP IP Network Infrastructure (RFC 3871)",
     "RFC3871_Operational_Security_Requirements.txt",
     "https://www.rfc-editor.org/rfc/rfc3871.txt"),
    ("sop", "BGP Operations and Security (RFC 7454)",
     "RFC7454_BGP_Operations_and_Security.txt",
     "https://www.rfc-editor.org/rfc/rfc7454.txt"),
    ("runbook", "Operational Security Current Practices in ISP Environments (RFC 4778)",
     "RFC4778_Operational_Security_Current_Practices.txt",
     "https://www.rfc-editor.org/rfc/rfc4778.txt"),
    ("whitepaper", "Software-Defined Networking (SDN): Layers and Architecture Terminology (RFC 7426)",
     "RFC7426_SDN_Layers_and_Architecture.txt",
     "https://www.rfc-editor.org/rfc/rfc7426.txt"),
]

_UA = "Mozilla/5.0 (ICDEV-DIC-seeder; +https://www.rfc-editor.org)"


def _ensure_collection(conn) -> None:
    row = conn.execute(
        "SELECT collection_id FROM dic_collections WHERE collection_id=?", (COLLECTION_ID,)
    ).fetchone()
    if row:
        return
    from tools.document_intelligence.ingest_orchestrator import _now, _resolve_context
    tenant_id, _ = _resolve_context(None, None)
    conn.execute(
        "INSERT INTO dic_collections (collection_id, name, description, owner_id, "
        "retention_days, classification, tenant_id, created_at) VALUES (?,?,?,?,?,?,?,?)",
        (COLLECTION_ID, COLLECTION_NAME, COLLECTION_DESC, "net-admin", 3650,
         CLASSIFICATION, tenant_id, _now()),
    )
    conn.commit()


def _download(url: str, dest: Path) -> int:
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310 (trusted IETF host)
        data = resp.read()
    with open(dest, "wb") as _fh:
        _fh.write(data)
    return len(data)


def main() -> int:
    ap = argparse.ArgumentParser(description="Seed DIC with a networking knowledge corpus")
    ap.add_argument("--rich", action="store_true",
                    help="enable LLM summary + metadata + KG bridge (slower)")
    ap.add_argument("--embed", action="store_true",
                    help="generate vector embeddings during ingest (BM25 search works without this)")
    ap.add_argument("--reset", action="store_true",
                    help="delete existing docs/versions/sections in the collection before re-ingest")
    ap.add_argument("--list", action="store_true", help="print the corpus and exit")
    ap.add_argument("--json", action="store_true", help="machine-readable report")
    args = ap.parse_args()

    if args.list:
        for cat, title, fn, url in CORPUS:
            print(f"[{cat:11}] {title}\n             {url}")
        return 0

    from tools.db.storage import get_connection
    from tools.document_intelligence.ingest_orchestrator import ingest_file

    # Create the collection on its own short-lived connection, then close it.
    # Each ingest below opens its OWN fresh connection (conn=None) so one failure
    # cannot cascade-close the others, and no transaction is held open across the
    # (potentially slow) ingest work.
    setup_conn = get_connection()
    try:
        if args.reset:
            doc_ids = [r[0] for r in setup_conn.execute(
                "SELECT doc_id FROM dic_documents WHERE collection_id=?", (COLLECTION_ID,)
            ).fetchall()]
            for did in doc_ids:
                for tbl in ("dic_sections", "dic_versions", "dic_chunk_links", "dic_documents"):
                    try:
                        setup_conn.execute(f"DELETE FROM {tbl} WHERE doc_id=?", (did,))  # nosec B608
                    except Exception:
                        pass
            setup_conn.commit()
        _ensure_collection(setup_conn)
    finally:
        setup_conn.close()

    tmpdir = Path(tempfile.gettempdir()) / "dic_net_corpus"
    tmpdir.mkdir(parents=True, exist_ok=True)

    rich = args.rich
    results = []
    for cat, title, fn, url in CORPUS:
        rec = {"category": cat, "title": title, "url": url}
        dest = tmpdir / fn
        try:
            rec["bytes"] = _download(url, dest)
        except Exception as exc:
            rec["error"] = f"download failed: {exc}"
            results.append(rec)
            continue
        try:
            outcome = ingest_file(
                str(dest),
                COLLECTION_ID,
                classification=CLASSIFICATION,
                created_by="net-admin",
                embed=args.embed,
                summarize=rich,
                bridge_kg=rich,
                clean_ocr=False,
                extract_metadata=rich,
                extract_identifiers=rich,
                extract_correspondence=False,
                detect_date_anomalies=False,
                detect_duplicate_blocks=False,
                detect_workload_anomaly=False,
                conn=None,
            )
            rec.update({
                "doc_id": outcome.doc_id,
                "version_id": outcome.version_id,
                "chunks": outcome.chunks,
                "chunks_embedded": outcome.chunks_embedded,
                "provider": outcome.provider,
                "errors": outcome.errors,
            })
        except Exception as exc:
            rec["error"] = f"ingest failed: {exc}"
        results.append(rec)

    ok = [r for r in results if r.get("doc_id")]
    summary = {
        "collection_id": COLLECTION_ID,
        "collection_name": COLLECTION_NAME,
        "ingested": len(ok),
        "total": len(CORPUS),
        "results": results,
        "view_at": "http://localhost:5050/document-intelligence/collections",
    }
    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        print(f"Seeded {len(ok)}/{len(CORPUS)} networking docs into '{COLLECTION_NAME}' ({COLLECTION_ID})")
        for r in results:
            if r.get("doc_id"):
                print(f"  ✓ [{r['category']:11}] {r['title']}  "
                      f"({r.get('chunks', 0)} chunks, {r.get('chunks_embedded', 0)} embedded)")
            else:
                print(f"  ✗ [{r['category']:11}] {r['title']} — {r.get('error', 'unknown error')}")
        print(f"\nView at: {summary['view_at']}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
