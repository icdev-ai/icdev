# CUI // SP-CTI
"""Change-control pack — approved changes are evidence a document went stale.

In practice CRs, ERB and ARB decisions arrive as *documents* (docx/pdf exported
or pulled out of SharePoint), not as rows in a change system. So the question is
document-to-document, and both sides already live in DIC:

    is there an APPROVED CHANGE DOCUMENT, newer than this document's approved
    version, that names a system this document describes?

If yes, the document is describing a world that has since been changed. That is
the strongest cATO signal available and it needs no config parsing, no vendor
knowledge and no external connector — it works for an HR policy naming a system
just as well as for a network runbook.

Why not nc_change_requests / ServiceNow / Jira: in this environment approved
changes land as SharePoint documents. `nc_change_requests` also has no
`approved_at` (only `submitted_at`/`updated_at`), seeds zero
`nc_change_request_items`, and its only writing route inserts columns the DDL
does not have. Reading the change *documents* avoids all of that and matches how
the evidence actually flows. Those tables remain available as a future
ChangeSourceProvider if a program tracks changes natively.

Deterministic end to end (TRUST rule 1): the verdict is "a newer approved change
document mentions this entity" — a SQL fact, never an LLM judgement. The change
document itself is the citation.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Any

from tools.doc_modernization.base_pack import (
    CandidateEntity,
    ChunkRef,
    DomainPack,
    Verdict,
)
from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

DOMAIN = "change_control"

_CONTEXT_WINDOW = 60


def _parse_dt(value: Any) -> datetime | None:
    """Parse a timestamp that may arrive as datetime or ISO/space-separated text.

    dic_documents.created_at is timestamptz on PostgreSQL but text on SQLite, and
    dic_versions.created_at is written as an ISO string. Comparing those in SQL
    would be a dialect trap, so the comparison is done in Python (CLAUDE.md:
    compute in Python rather than lean on translate_sql).
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    text = text.replace("Z", "+00:00")
    if " " in text and "T" not in text:
        text = text.replace(" ", "T", 1)
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _rows(conn, sql: str, params: tuple) -> list[dict]:
    """Query tolerantly — a missing table must degrade, not kill the sweep.

    Rolls back on failure: one failed statement poisons the whole PostgreSQL
    transaction, so every optional evidence read must clean up after itself
    (same pattern as packs/network_hardware.py).
    """
    try:
        cur = conn.execute(sql, params)
        out = []
        for r in cur.fetchall():
            out.append(dict(r) if hasattr(r, "keys") else r)
        return out
    except Exception as exc:
        logger.warning("change_control: query failed: %s", exc)
        try:
            conn.rollback()
        except Exception:
            pass
        return []


class ChangeControlPack(DomainPack):
    """Flags documents superseded by a newer approved change document."""

    pack_id = "change_control"
    label = "Change Control"
    entity_types = ["system_reference"]

    # ── config ────────────────────────────────────────────────────────────
    def _collection_names(self) -> list[str]:
        raw = (self.config or {}).get("change_collections") or []
        return [str(c).strip() for c in raw if str(c).strip()]

    def _patterns(self) -> list[re.Pattern]:
        if getattr(self, "_compiled", None) is not None:
            return self._compiled
        pats = []
        for p in ((self.config or {}).get("extraction", {}) or {}).get("patterns", []) or []:
            try:
                pats.append(re.compile(p))
            except re.error as exc:
                logger.warning("change_control: bad pattern %r: %s", p, exc)
        self._compiled = pats
        return pats

    def _collection_ids(self, conn) -> list[str]:
        """Resolve configured collection NAMES to ids.

        dic_collections.collection_id is an opaque hash; the human label lives in
        `name`. Config names the collection ("change-records"), so accept either
        a name or a literal id.
        """
        names = self._collection_names()
        if not names:
            return []
        placeholders = ",".join(["%s"] * len(names))
        rows = _rows(
            conn,
            f"SELECT collection_id, name FROM dic_collections "  # noqa: S608 - placeholders only
            f"WHERE name IN ({placeholders}) OR collection_id IN ({placeholders})",
            tuple(names) * 2,
        )
        return [r["collection_id"] for r in rows if r.get("collection_id")]

    # ── contract ──────────────────────────────────────────────────────────
    def extract(self, text: str, chunk_ref: ChunkRef) -> list[CandidateEntity]:
        """System/CI references the document makes claims about."""
        found: list[CandidateEntity] = []
        seen: set[str] = set()
        for pat in self._patterns():
            for m in pat.finditer(text or ""):
                label = (m.group(0) or "").strip()
                key = label.lower()
                if not label or key in seen:
                    continue
                seen.add(key)
                start = max(0, m.start() - _CONTEXT_WINDOW)
                found.append(CandidateEntity(
                    label=label,
                    entity_type="system_reference",
                    pack_id=self.pack_id,
                    chunk_ref=chunk_ref,
                    raw_match=label,
                    context=(text or "")[start:m.end() + _CONTEXT_WINDOW],
                ))
        return found

    def evaluate(self, entity: CandidateEntity, conn) -> Verdict:
        collection_ids = self._collection_ids(conn)
        if not collection_ids:
            # No change corpus configured/ingested: say so rather than imply the
            # document was checked and found current.
            return Verdict(
                currency_verdict="unknown",
                finding_type=None,
                rationale="No change-record collection configured or ingested.",
                confidence=0.0,
            )

        approved_at = self._approved_at(conn, entity.chunk_ref)
        if approved_at is None:
            return Verdict(
                currency_verdict="unknown",
                finding_type=None,
                rationale="Document's approved version has no readable timestamp.",
                confidence=0.0,
            )

        changes = self._changes_mentioning(conn, collection_ids, entity, approved_at)
        if not changes:
            return Verdict(
                currency_verdict="current",
                finding_type=None,
                rationale=(
                    f"No approved change document newer than this version "
                    f"({approved_at.date().isoformat()}) mentions {entity.label!r}."
                ),
                confidence=1.0,
            )

        newest = changes[0]
        limit = int((self.config or {}).get("max_evidence_docs", 5) or 5)
        evidence = [
            {
                "source": f"change_doc:{c['doc_id']}",
                "detail": f"{c.get('title') or c.get('filename') or c['doc_id']} mentions {entity.label}",
                "date": str(c.get("created_at") or ""),
            }
            for c in changes[:limit]
        ]
        return Verdict(
            currency_verdict="divergent",
            finding_type="unreflected_change",
            severity=str((self.config or {}).get("severity", "high")),
            rationale=(
                f"{len(changes)} approved change document(s) newer than this version "
                f"mention {entity.label!r}; newest is "
                f"{newest.get('title') or newest.get('filename') or newest['doc_id']}. "
                f"This document may not reflect that change."
            ),
            confidence=1.0,
            evidence=evidence,
        )

    def _approved_at(self, conn, chunk_ref: ChunkRef) -> datetime | None:
        """When this document's scanned (approved) version was created.

        The scanner only scans the latest approved version, so chunk_ref.version_id
        IS that version. NOTE: dic_versions has no `approved_at` column — created_at
        is a documented PROXY for the approval moment, not the approval event.
        """
        rows = _rows(
            conn,
            "SELECT created_at FROM dic_versions WHERE version_id = %s",
            (chunk_ref.version_id,),
        )
        return _parse_dt(rows[0].get("created_at")) if rows else None

    def _changes_mentioning(
        self, conn, collection_ids: list[str], entity: CandidateEntity,
        approved_at: datetime,
    ) -> list[dict]:
        """Approved change docs newer than approved_at that name this entity.

        Matches chunk text OR title OR filename: a freshly ingested CR may not be
        chunk-linked yet, and a CR's title usually names the system anyway. The
        LEFT JOIN keeps chunk-less documents eligible.
        """
        ids = ",".join(["%s"] * len(collection_ids))
        like = f"%{entity.label.lower()}%"
        rows = _rows(
            conn,
            f"""
            SELECT DISTINCT d.doc_id, d.title, d.filename, d.created_at
            FROM dic_documents d
            LEFT JOIN dic_chunk_links l ON l.doc_id = d.doc_id
            LEFT JOIN rag_chunks r ON r.id = l.rag_chunk_id
            WHERE d.collection_id IN ({ids})
              AND d.doc_id <> %s
              AND (
                    LOWER(COALESCE(r.content, '')) LIKE %s
                 OR LOWER(COALESCE(d.title, '')) LIKE %s
                 OR LOWER(COALESCE(d.filename, '')) LIKE %s
              )
            """,  # noqa: S608 - placeholders only
            tuple(collection_ids) + (entity.chunk_ref.doc_id, like, like, like),
        )
        # Date filtering in Python: created_at is timestamptz on PG and text on
        # SQLite, so comparing it in SQL would be a dialect trap.
        newer = []
        for r in rows:
            dt = _parse_dt(r.get("created_at"))
            if dt is not None and dt > approved_at:
                r["_dt"] = dt
                newer.append(r)
        newer.sort(key=lambda r: r["_dt"], reverse=True)
        return newer

    def evidence_snapshot(self, conn) -> str:
        """Hash the change corpus so a NEW change document re-scans documents.

        The default implementation only hashes static config, which would mean a
        newly-ingested CR never triggers a re-scan — the pack would be inert.
        """
        collection_ids = self._collection_ids(conn)
        if not collection_ids:
            return hashlib.sha256(b"change_control:no-collections").hexdigest()
        ids = ",".join(["%s"] * len(collection_ids))
        rows = _rows(
            conn,
            f"SELECT doc_id, created_at FROM dic_documents "  # noqa: S608 - placeholders only
            f"WHERE collection_id IN ({ids}) ORDER BY doc_id",
            tuple(collection_ids),
        )
        payload = "|".join(f"{r.get('doc_id')}:{r.get('created_at')}" for r in rows)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()
