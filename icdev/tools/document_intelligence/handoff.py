# CUI // SP-CTI
"""DIC Knowledge Handoff Workflow.

Steps:
  1. Initiate session — departing owner + successor + destination collection.
  2. Auto-build agenda from explorer single-owner findings and high-value docs.
  3. Interview prompts (question per agenda item) → captured answer.
  4. CoD-verified generation — structured doc per agenda area.
  5. Write to destination collection with HITL-gated status.
  6. Update session progress; flag orphaned docs for reassignment.

All outputs are AI-labeled PENDING. Never auto-published.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)


@dataclass
class HandoffSession:
    session_id: str
    departing_owner_id: str
    successor_owner_id: str
    dest_collection_id: str
    title: str
    status: str = "open"
    agenda_count: int = 0
    answered_count: int = 0
    generated_count: int = 0
    orphan_count: int = 0
    tenant_id: str = "default"
    classification: str = "CUI"


@dataclass
class HandoffItem:
    item_id: str
    session_id: str
    item_kind: str  # interview | generated_doc | orphan_flag
    topic: str = ""
    prompt: str = ""
    answer_text: str = ""
    doc_id: str = ""
    version_id: str = ""
    verified: bool = False
    abstained: bool = False
    status: str = "pending"


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hid(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:24]


def start_session(
    departing_owner_id: str,
    successor_owner_id: str,
    dest_collection_id: str,
    *,
    tenant_id: str = "default",
    classification: str = "CUI",
    created_by: str = "system",
) -> HandoffSession:
    """Create a handoff session and auto-build the agenda."""
    from tools.db.storage import get_connection

    session_id = _hid("handoff", departing_owner_id, successor_owner_id, _now_utc())

    # 1. Gather single-owner / tribal-knowledge findings for this owner.
    # (Explorer output is used implicitly via solo_collections query below.)

    # 2. Find docs where departing_owner is the sole recent contributor.
    conn = get_connection()
    agenda_topics: list[str] = []
    orphan_doc_ids: list[str] = []
    try:
        cur = conn.cursor()
        # Collections where departing owner is the only member.
        cur.execute(
            "SELECT collection_id FROM dic_team_access WHERE tenant_id = %s AND user_id = %s "
            "GROUP BY collection_id HAVING COUNT(*) = 1",
            (tenant_id, departing_owner_id),
        )
        solo_collections = [r[0] for r in cur.fetchall()]

        # High-value docs in those collections (most chunks = most knowledge).
        for cid in solo_collections:
            cur.execute(
                "SELECT doc_id, title FROM dic_documents WHERE collection_id = %s AND tenant_id = %s "
                "ORDER BY (SELECT COUNT(*) FROM dic_chunk_links WHERE doc_id = dic_documents.doc_id) DESC LIMIT 5",
                (cid, tenant_id),
            )
            for r in cur.fetchall():
                did, title = (r[0], r[1]) if hasattr(r, "__getitem__") else (r["doc_id"], r.get("title", ""))
                agenda_topics.append(f"Knowledge area: {title or did} (collection {cid})")
                orphan_doc_ids.append(did)
    finally:
        conn.close()

    if not agenda_topics:
        agenda_topics.append(f"General departure interview for {departing_owner_id}")

    session = HandoffSession(
        session_id=session_id,
        departing_owner_id=departing_owner_id,
        successor_owner_id=successor_owner_id,
        dest_collection_id=dest_collection_id,
        title=f"Knowledge Handoff: {departing_owner_id} → {successor_owner_id}",
        status="open",
        agenda_count=len(agenda_topics),
        answered_count=0,
        generated_count=0,
        orphan_count=len(orphan_doc_ids),
        tenant_id=tenant_id,
        classification=classification,
    )

    # Persist session + items.
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO dic_handoff_sessions "
            "(session_id, departing_owner_id, successor_owner_id, dest_collection_id, title, status, "
            "agenda_count, answered_count, generated_count, orphan_count, created_by, created_at, updated_at, tenant_id, classification) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (session_id, departing_owner_id, successor_owner_id, dest_collection_id, session.title,
             "open", len(agenda_topics), 0, 0, len(orphan_doc_ids), created_by, _now_utc(), _now_utc(), tenant_id, classification),
        )
        for i, topic in enumerate(agenda_topics):
            item_id = f"{session_id}_item_{i}"
            cur.execute(
                "INSERT INTO dic_handoff_items "
                "(item_id, session_id, item_kind, topic, prompt, status, created_at, updated_at, tenant_id, classification) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (item_id, session_id, "interview", topic,
                 f"What does {departing_owner_id} know about '{topic}' that {successor_owner_id} must capture?",
                 "pending", _now_utc(), _now_utc(), tenant_id, classification),
            )
        conn.commit()
    except Exception as exc:
        logger.warning("handoff: session persist failed: %s", exc)
    finally:
        conn.close()

    return session


def answer_item(
    item_id: str,
    answer_text: str,
    reviewed_by: str = "system",
) -> dict:
    """Store an interview answer and trigger CoD-verified doc generation."""
    from tools.db.storage import get_connection
    from tools.document_intelligence.doc_generator import generate_document
    from tools.document_intelligence.verifier import verify as _verify

    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT session_id, topic, tenant_id, classification FROM dic_handoff_items WHERE item_id = %s",
            (item_id,),
        )
        row = cur.fetchone()
        if not row:
            return {"error": "item not found"}
        session_id, topic, tenant_id, classification = (
            (row[0], row[1], row[2], row[3]) if hasattr(row, "__getitem__")
            else (row["session_id"], row["topic"], row["tenant_id"], row["classification"])
        )

        # Update answer.
        cur.execute(
            "UPDATE dic_handoff_items SET answer_text = %s, status = %s, updated_at = %s WHERE item_id = %s",
            (answer_text, "answered", _now_utc(), item_id),
        )

        # Update session answered_count.
        cur.execute(
            "UPDATE dic_handoff_sessions SET answered_count = answered_count + 1, updated_at = %s WHERE session_id = %s",
            (_now_utc(), session_id),
        )

        # Generate CoD-verified document from the answer.
        try:
            result = generate_document(
                query=f"Structure the captured knowledge about '{topic}' into a formal document.",
                collection_id=session_id,
                tenant_id=tenant_id,
                classification=classification,
                created_by=reviewed_by,
            )
            doc_id = result.doc_id
            version_id = result.version_id

            # Verify if possible.
            verified = False
            abstained = False
            try:
                if result.sections:
                    vr = _verify(result.sections[0].content, [answer_text])
                    verified = vr.verified and not vr.abstained
                    abstained = vr.abstained
            except Exception:
                pass

            cur.execute(
                "UPDATE dic_handoff_items SET doc_id = %s, version_id = %s, verified = %s, abstained = %s, status = %s, updated_at = %s "
                "WHERE item_id = %s",
                (doc_id, version_id, int(verified), int(abstained), "generated", _now_utc(), item_id),
            )
            cur.execute(
                "UPDATE dic_handoff_sessions SET generated_count = generated_count + 1, updated_at = %s WHERE session_id = %s",
                (_now_utc(), session_id),
            )
        except Exception as exc:
            logger.warning("handoff: generation failed for item %s: %s", item_id, exc)

        conn.commit()
        return {"status": "answered", "item_id": item_id}
    except Exception as exc:
        return {"error": str(exc)}
    finally:
        conn.close()


def close_session(session_id: str, closed_by: str = "system") -> dict:
    """Mark session complete and reassign orphaned docs to successor."""
    from tools.db.storage import get_connection

    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE dic_handoff_sessions SET status = %s, updated_at = %s WHERE session_id = %s",
            ("closed", _now_utc(), session_id),
        )
        # Reassign orphaned doc owners in team_access.
        cur.execute(
            "SELECT successor_owner_id, dest_collection_id, orphan_count FROM dic_handoff_sessions WHERE session_id = %s",
            (session_id,),
        )
        row = cur.fetchone()
        if row:
            successor, dest_cid, orphans = (
                (row[0], row[1], row[2]) if hasattr(row, "__getitem__")
                else (row["successor_owner_id"], row["dest_collection_id"], row["orphan_count"])
            )
            if successor and orphans > 0:
                try:
                    cur.execute(
                        "INSERT INTO dic_team_access (access_id, collection_id, user_id, role, tenant_id) "
                        "VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                        (_hid("access", dest_cid, successor, _now_utc()), dest_cid, successor, "admin", "default"),
                    )
                except Exception as _exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
                    logger.warning(
                        "close_session: best-effort INSERT into dic_team_access failed (non-blocking): %s",
                        _exc,
                    )
        conn.commit()
        return {"status": "closed", "session_id": session_id}
    except Exception as exc:
        return {"error": str(exc)}
    finally:
        conn.close()


def list_sessions(tenant_id: str = "default", limit: int = 50) -> list[dict]:
    from tools.db.storage import get_connection
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM dic_handoff_sessions WHERE tenant_id = %s ORDER BY created_at DESC LIMIT %s",
            (tenant_id, limit),
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        conn.close()
