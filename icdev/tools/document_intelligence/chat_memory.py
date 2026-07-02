# CUI // SP-CTI
"""Conversational, KG-grounded session memory for DIC document chat.

DIC chat is otherwise stateless per query, so a follow-up like
"and its retention period?" loses the subject established by the previous
turn. This module adds grounded, citable session memory that resolves such
follow-up references *without* re-stating context.

Design (adapted from getzep/graphiti, mem0, "Memory is Reconstructed, Not
Retrieved", and "Training-Free Lexical-Dense Fusion for Conversational-Memory
Retrieval" — dossier rdoss-546ae06f9cd2):

* **Grounded subject only.** A turn's remembered "subject" is derived strictly
  from the prior turn's *grounded citations* (the top-scoring result's document
  title) — never invented. If a turn produced no grounded evidence, it records
  no subject, so memory can never fabricate one.
* **Reconstructive resolution.** A follow-up query is detected lexically
  (dangling pronoun / leading conjunction) and *reconstructed* by prepending the
  prior grounded subject, so the existing grounded retrieval + citation
  machinery resolves it like any other query. Memory adds context to the query;
  it never substitutes for retrieval and never short-circuits citations.
* **RLS-scoped.** ``dic_chat_memory`` carries ``tenant_id`` + ``classification``
  and uses ``get_connection()`` so RLS is enforced. Reads are also explicitly
  scoped to ``session_id`` + ``tenant_id`` belt-and-suspenders.
* **Toggleable.** ``memory_enabled()`` honours the ``ICDEV_DIC_CHAT_MEMORY``
  env var and a per-request ``memory`` flag; off restores stateless behaviour.
"""
from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

# Referring tokens that signal a turn depends on a prior subject. Matched as
# whole words so "item" / "their_field" never trip "it" / "their".
_FOLLOWUP_PRONOUNS = frozenset({
    "it", "its", "it's", "they", "them", "their", "theirs",
    "that", "this", "those", "these", "the same", "such",
    "he", "him", "his", "she", "her", "hers",
})

# Conjunctions / discourse openers that signal a continuation of the prior turn.
_LEADING_CONJUNCTIONS = (
    "and ", "also ", "what about", "how about", "plus ", "& ",
)

_PRONOUN_RE = re.compile(
    r"\b(" + "|".join(re.escape(p) for p in sorted(_FOLLOWUP_PRONOUNS, key=len, reverse=True)) + r")\b"
)


@dataclass
class ResolvedQuery:
    """Result of attempting to resolve a follow-up against session memory."""

    original_query: str = ""
    resolved_query: str = ""
    subject: str = ""
    is_followup: bool = False
    prior_turn_id: str = ""

    def to_dict(self) -> dict:
        return {
            "original_query": self.original_query,
            "resolved_query": self.resolved_query,
            "subject": self.subject,
            "is_followup": self.is_followup,
            "prior_turn_id": self.prior_turn_id,
        }


@dataclass
class ChatTurn:
    """A persisted conversation turn recovered from session memory."""

    turn_id: str = ""
    session_id: str = ""
    collection_id: str = ""
    turn_index: int = 0
    query: str = ""
    answer: str = ""
    subject: str = ""
    subject_doc_id: str = ""
    entities: list[str] = field(default_factory=list)
    doc_ids: list[str] = field(default_factory=list)
    citations: list[dict] = field(default_factory=list)
    mode: str = "grounded"


# --------------------------------------------------------------------------- #
# Toggle
# --------------------------------------------------------------------------- #

def memory_enabled(data: dict | None = None) -> bool:
    """Return True when conversational memory is active for this request.

    Off restores fully stateless behaviour. Precedence:
    1. ``ICDEV_DIC_CHAT_MEMORY`` env var (a hard global kill-switch when falsey),
    2. an explicit per-request ``memory`` flag in the chat payload,
    3. default on.
    """
    env = os.environ.get("ICDEV_DIC_CHAT_MEMORY", "1").strip().lower()
    if env in ("0", "false", "off", "no"):
        return False
    if data is not None and "memory" in data:
        return bool(data.get("memory"))
    return True


# --------------------------------------------------------------------------- #
# Follow-up detection
# --------------------------------------------------------------------------- #

def is_followup(query: str) -> bool:
    """Heuristically detect a query that depends on a prior turn's subject.

    A query is a follow-up when it opens with a continuation conjunction or
    carries a dangling referring pronoun (it/its/that/their/…). This is purely
    lexical and air-gap safe — no LLM.
    """
    q = (query or "").strip().lower()
    if not q:
        return False
    if any(q.startswith(c) for c in _LEADING_CONJUNCTIONS):
        return True
    return bool(_PRONOUN_RE.search(q))


# --------------------------------------------------------------------------- #
# Grounded subject extraction
# --------------------------------------------------------------------------- #

def extract_subject(results: list, query: str = "") -> tuple[str, str, list[str], list[str]]:
    """Derive a *grounded* subject from search results.

    The subject is the top-scoring grounded result's document title — a value
    that exists in the corpus and is backed by a citation. Never invents text.

    Returns ``(subject, subject_doc_id, doc_ids, entities)`` where ``entities``
    is the de-duplicated list of cited document titles (the grounded "nodes" the
    turn touched). Empty subject when there is no grounded evidence.
    """
    if not results:
        return "", "", [], []
    top = results[0]
    subject = (getattr(top, "doc_title", "") or "").strip()
    subject_doc_id = (getattr(top, "doc_id", "") or "").strip()

    doc_ids: list[str] = []
    entities: list[str] = []
    for r in results:
        did = (getattr(r, "doc_id", "") or "").strip()
        if did and did not in doc_ids:
            doc_ids.append(did)
        title = (getattr(r, "doc_title", "") or "").strip()
        if title and title not in entities:
            entities.append(title)
    return subject, subject_doc_id, doc_ids, entities


# --------------------------------------------------------------------------- #
# Persistence
# --------------------------------------------------------------------------- #

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def record_turn(
    session_id: str,
    query: str,
    answer: str,
    results: list,
    tenant_id: str = "default",
    classification: str = "CUI",
    collection_id: str = "",
    mode: str = "grounded",
) -> str | None:
    """Persist a conversation turn with its grounded subject. Returns turn_id.

    Only the grounded *subject* + citations are stored — derived from
    ``results`` — so recalled memory stays grounded. No-ops (returns None) when
    there is no ``session_id`` or no grounded evidence to anchor the turn.
    """
    session_id = (session_id or "").strip()
    if not session_id or not results:
        return None

    subject, subject_doc_id, doc_ids, entities = extract_subject(results, query)
    if not subject:
        # Nothing grounded to remember — refuse rather than fabricate a subject.
        return None

    citations = []
    for r in results[:5]:
        cit = getattr(r, "citation", None)
        if cit is not None and hasattr(cit, "to_dict"):
            citations.append(cit.to_dict())

    turn_id = uuid.uuid4().hex
    try:
        from tools.db.storage import get_connection

        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT COUNT(*) FROM dic_chat_memory WHERE session_id = ? AND tenant_id = ?",
                (session_id, tenant_id),
            )
            row = cur.fetchone()
            turn_index = int(row[0]) if row else 0

            cur.execute(
                """
                INSERT INTO dic_chat_memory
                    (turn_id, session_id, collection_id, turn_index, query, answer,
                     subject, subject_doc_id, entities_json, doc_ids_json,
                     citations_json, mode, created_at, tenant_id, classification)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    turn_id, session_id, collection_id or "", turn_index, query or "",
                    (answer or "")[:4000], subject, subject_doc_id,
                    json.dumps(entities), json.dumps(doc_ids),
                    json.dumps(citations), mode, _now(), tenant_id, classification,
                ),
            )
            conn.commit()
        finally:
            conn.close()
        return turn_id
    except Exception as exc:
        logger.warning("dic: chat memory record error: %s", exc)
        return None


def recall_last_turn(session_id: str, tenant_id: str = "default") -> ChatTurn | None:
    """Return the most recent grounded turn for a session, or None.

    Scoped to ``session_id`` + ``tenant_id`` (on top of storage-layer RLS) so a
    tenant can never recall another tenant's conversation.
    """
    session_id = (session_id or "").strip()
    if not session_id:
        return None
    try:
        from tools.db.storage import get_connection

        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT turn_id, session_id, collection_id, turn_index, query, answer,
                       subject, subject_doc_id, entities_json, doc_ids_json,
                       citations_json, mode
                FROM dic_chat_memory
                WHERE session_id = ? AND tenant_id = ?
                ORDER BY turn_index DESC
                LIMIT 1
                """,
                (session_id, tenant_id),
            )
            row = cur.fetchone()
        finally:
            conn.close()
        if not row:
            return None
        return ChatTurn(
            turn_id=row[0],
            session_id=row[1],
            collection_id=row[2] or "",
            turn_index=int(row[3] or 0),
            query=row[4] or "",
            answer=row[5] or "",
            subject=row[6] or "",
            subject_doc_id=row[7] or "",
            entities=_loads(row[8]),
            doc_ids=_loads(row[9]),
            citations=_loads(row[10]),
            mode=row[11] or "grounded",
        )
    except Exception as exc:
        logger.warning("dic: chat memory recall error: %s", exc)
        return None


def _loads(raw) -> list:
    try:
        val = json.loads(raw or "[]")
        return val if isinstance(val, list) else []
    except Exception:
        return []


# --------------------------------------------------------------------------- #
# Resolution
# --------------------------------------------------------------------------- #

def resolve_followup(session_id: str, query: str, tenant_id: str = "default") -> ResolvedQuery:
    """Reconstruct a follow-up query using the prior turn's grounded subject.

    When ``query`` looks like a follow-up and a prior grounded subject exists,
    the subject is prepended so downstream grounded retrieval resolves the
    reference. Otherwise the query is returned unchanged. The subject is always
    a grounded document title from a previous turn — never fabricated.
    """
    rq = ResolvedQuery(original_query=query, resolved_query=query)
    if not is_followup(query):
        return rq
    prior = recall_last_turn(session_id, tenant_id)
    if not prior or not prior.subject:
        return rq
    # Don't double-anchor if the subject is already named in the query.
    if prior.subject.lower() in (query or "").lower():
        rq.is_followup = True
        rq.subject = prior.subject
        rq.prior_turn_id = prior.turn_id
        return rq
    rq.is_followup = True
    rq.subject = prior.subject
    rq.prior_turn_id = prior.turn_id
    rq.resolved_query = f"{prior.subject} {query}".strip()
    return rq
