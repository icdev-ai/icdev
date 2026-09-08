# CUI // SP-CTI
"""Seed and tear down a workspace fixture for the dwr-ws-03 E2E.

WHY A FIXTURE AND NOT THE LIVE BOARD. Measured 2026-09-08, all 58 pending
``dic_suggestions`` on this deployment carry ``anchor_basis`` NULL, and the
document holding 47 of them has ZERO ``dic_sections`` rows. The accept door
refuses an unanchored proposal with a 409 by design (dwr-anchor-05), so on the
live board there is nothing acceptable to accept -- a spec driven off it would
prove that the refusal path works and nothing about the decision path.

WHAT IS SEEDED: one document, one draft version, three sections, and one
crowdsourced proposal per section through the SAME
``suggestion_store.create_suggestion`` and ``whole_section_anchor`` the
``/api/sections/<id>/suggest`` route calls -- never a hand-written INSERT of an
anchor, which could declare a span the store would not.

THREE SECTIONS AND NOT ONE, deliberately: a crowdsourced proposal anchors the
WHOLE section, so three on one section would leave the second and third
provably stale the moment the first is accepted. That is the stale-anchor path,
which has its own assertions; this fixture is for the decision path.

Everything it writes is removed by ``--teardown``, by id, and nothing else.
"""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

#: Every table this fixture writes. Teardown deletes from each, by id.
_TABLES = ("dic_suggestions", "dic_sections", "dic_versions", "dic_documents")

SECTIONS = [
    ("Transport security", "The enclave SHALL use TLS 1.1 for all transport."),
    ("Edge hardware", "Edge routing is served by the Catalyst 6500 chassis."),
    ("Management plane", "Devices are polled over SNMPv2c from the NOC."),
]
PROPOSALS = [
    "The enclave SHALL use TLS 1.3 for all transport.",
    "Edge routing is served by the Catalyst 9500 chassis.",
    "Devices are polled over SNMPv3 from the NOC.",
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def seed(collection_id: str = "default") -> dict:
    from tools.db.storage import get_connection
    from tools.document_intelligence.collection_registry import ensure_collection
    from tools.document_intelligence.ingest_orchestrator import _ensure_schema
    from tools.document_intelligence.suggestion_store import (
        create_suggestion, whole_section_anchor,
    )

    tag = uuid.uuid4().hex[:12]
    doc_id = "dic_doc_ws03e2e_%s" % tag
    version_id = "dic_ver_ws03e2e_%s" % tag
    now = _now()
    section_ids = []

    with get_connection() as conn:
        # The DIC tables are created by the ingest path, not by
        # `init_icdev_db.py`, so a fresh database has none of them. Ensured
        # through the ingest orchestrator's own DDL rather than a copy of it.
        _ensure_schema(conn)
        ensure_collection(conn, collection_id, name="default",
                          tenant_id="default", classification="CUI")
        conn.execute(
            "INSERT INTO dic_documents (doc_id, collection_id, filename, title, "
            "content_type, created_at, tenant_id, classification) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (doc_id, collection_id, "ws03-e2e.md",
             "dwr-ws-03 E2E — decide without reloading", "text/markdown",
             now, "default", "CUI"),
        )
        conn.execute(
            "INSERT INTO dic_versions (version_id, doc_id, version_no, origin, status, "
            "created_at, created_by, tenant_id, classification) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (version_id, doc_id, 1, "human_authored", "draft", now, "e2e",
             "default", "CUI"),
        )
        for i, (heading, content) in enumerate(SECTIONS):
            section_id = "dic_sec_ws03e2e_%s_%d" % (tag, i)
            section_ids.append(section_id)
            conn.execute(
                "INSERT INTO dic_sections (section_id, version_id, doc_id, heading, "
                "content, status, origin, created_at, created_by, tenant_id, classification) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (section_id, version_id, doc_id, heading, content, "draft",
                 "human", now, "e2e", "default", "CUI"),
            )
        conn.commit()

    suggestion_ids = []
    for section_id, (_, content), proposed in zip(section_ids, SECTIONS, PROPOSALS):
        anchor = whole_section_anchor(section_id, content)
        suggestion_ids.append(create_suggestion(
            section_id=section_id,
            # Every per-document surface filters on this column. The
            # `/api/sections/<id>/suggest` route left it empty until this card
            # (see blueprint.api_section_suggest); a fixture that also left it
            # empty would render an empty rail beside three sections.
            doc_id=doc_id,
            collection_id=collection_id,
            canvas_source="crowdsource",
            suggested_content=proposed,
            current_content=content,
            rationale="dwr-ws-03 E2E fixture",
            tenant_id="default",
            classification="CUI",
            origin_kind="crowdsource",
            **anchor,
        ))

    return {"doc_id": doc_id, "version_id": version_id,
            "section_ids": section_ids, "suggestion_ids": suggestion_ids,
            "proposals": PROPOSALS,
            # The section text AS SEEDED, index-aligned with `section_ids`. A
            # consumer asserting on the document before a decision must not
            # hard-code this prose: two copies of it drift, and the copy in the
            # test is the one that silently stops describing the fixture.
            "current": [content for _, content in SECTIONS]}


def teardown(doc_id: str) -> dict:
    """Remove exactly what ``seed`` wrote for this doc_id, and nothing else.

    Suggestions are removed by SECTION as well as by document. A deployment
    that has not taken this card's ``api_section_suggest`` fix writes an empty
    ``doc_id`` on a crowdsourced row, and a teardown keyed on ``doc_id`` alone
    would report ``0`` while leaving the rows behind -- a clean-looking report
    over residue, which is the shape this whole epic exists to refuse. The
    section prefix is this fixture's own id namespace and matches nothing else.
    """
    from tools.db.storage import get_connection

    removed: dict = {}
    with get_connection() as conn:
        sections = [r[0] if isinstance(r, (list, tuple)) else r["section_id"]
                    for r in conn.execute(
                        "SELECT section_id FROM dic_sections WHERE doc_id = %s",
                        (doc_id,)).fetchall()]
        try:
            n = 0
            cur = conn.execute("DELETE FROM dic_suggestions WHERE doc_id = %s", (doc_id,))
            n += getattr(cur, "rowcount", 0) or 0
            for section_id in sections:
                cur = conn.execute(
                    "DELETE FROM dic_suggestions WHERE section_id = %s", (section_id,))
                n += getattr(cur, "rowcount", 0) or 0
            removed["dic_suggestions"] = n
        except Exception as exc:  # noqa: BLE001 — report, never mask
            removed["dic_suggestions"] = "error: %s" % exc
        for table in ("dic_sections", "dic_versions", "dic_documents"):
            try:
                cur = conn.execute("DELETE FROM %s WHERE doc_id = %%s" % table, (doc_id,))
                removed[table] = getattr(cur, "rowcount", None)
            except Exception as exc:  # noqa: BLE001
                removed[table] = "error: %s" % exc
        conn.commit()
    return {"doc_id": doc_id, "sections": len(sections), "removed": removed}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--seed", action="store_true")
    ap.add_argument("--teardown", metavar="DOC_ID")
    ap.add_argument("--collection-id", default="default")
    args = ap.parse_args()
    if args.seed:
        print(json.dumps(seed(args.collection_id)))
        return 0
    if args.teardown:
        print(json.dumps(teardown(args.teardown)))
        return 0
    ap.error("one of --seed / --teardown is required")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
