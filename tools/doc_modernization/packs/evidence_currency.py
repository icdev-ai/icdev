# CUI // SP-CTI
"""Evidence-currency pack — has the source this document was built from changed?

Every other pack needs domain knowledge: a catalog of EOL hardware, a crypto
rulebook, a change corpus. This one needs none, which is exactly why it works
for ANY document type — an HR policy, an SSP, a runbook — with no per-domain
authoring at all:

    baseline = the hash of each chunk this version CITED, at link time
    current  = that chunk's hash now
    drift    = they differ (the source moved), or the chunk is gone (evidence deleted)

It asks the narrowest honest question available: not "is the world still like
this?" but "is what this document was built from still what it says?". That is a
pure hash comparison — deterministic, no LLM, no catalog (TRUST rule 1).

Documents with no evidence anchors at all are reported as UNVERIFIABLE rather
than skipped. A document nothing can check must never render identically to one
that was checked and found current — in an SSP chain that silence is the
dangerous outcome.
"""
from __future__ import annotations

import hashlib
from typing import Any

from tools.doc_modernization.base_pack import (
    CandidateEntity,
    ChunkRef,
    DomainPack,
    Verdict,
)
from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

# One constant label for the "this document has no anchors" case. dedupe_key is
# doc_id|pack_id|entity_label|finding_type, so a constant label collapses to
# exactly ONE unverifiable finding per document instead of one per chunk.
NO_ANCHOR_LABEL = "(no evidence anchors)"


def _rows(conn, sql: str, params: tuple) -> list[dict]:
    """Query tolerantly; roll back on failure (a failed statement poisons the
    whole PostgreSQL transaction)."""
    try:
        cur = conn.execute(sql, params)
        return [dict(r) if hasattr(r, "keys") else r for r in cur.fetchall()]
    except Exception as exc:
        logger.warning("evidence_currency: query failed: %s", exc)
        try:
            conn.rollback()
        except Exception:
            pass
        return []


class EvidenceCurrencyPack(DomainPack):
    """Flags documents whose cited evidence has changed or disappeared."""

    pack_id = "evidence_currency"
    label = "Evidence Currency"
    entity_types = ["evidence_anchor"]

    def extract(self, text: str, chunk_ref: ChunkRef) -> list[CandidateEntity]:
        """One entity per cited chunk — the anchor itself, not anything in the text.

        Unlike every other pack this ignores ``text`` entirely: the subject is the
        citation, not the prose. When the scanner had no chunk links and fell back
        to dic_sections, chunk_link_id is None and the document is unverifiable.
        """
        if not chunk_ref.chunk_link_id:
            return [CandidateEntity(
                label=NO_ANCHOR_LABEL,
                entity_type="evidence_anchor",
                pack_id=self.pack_id,
                chunk_ref=chunk_ref,
                raw_match="",
                context="",
            )]
        return [CandidateEntity(
            label=chunk_ref.chunk_link_id,
            entity_type="evidence_anchor",
            pack_id=self.pack_id,
            chunk_ref=chunk_ref,
            raw_match="",
            context=(text or "")[:160],
        )]

    def evaluate(self, entity: CandidateEntity, conn) -> Verdict:
        if entity.label == NO_ANCHOR_LABEL:
            return Verdict(
                currency_verdict="unknown",
                finding_type="unverifiable_evidence",
                severity=str((self.config or {}).get("unverifiable_severity", "info")),
                rationale=(
                    "This document has no evidence anchors (no chunk links), so its "
                    "currency cannot be verified. It was not checked and found "
                    "current — it could not be checked at all. Re-ingest it so its "
                    "sources are recorded."
                ),
                confidence=1.0,
                evidence=[{
                    "source": f"dic_document:{entity.chunk_ref.doc_id}",
                    "detail": "no dic_chunk_links rows for the approved version",
                    "date": "",
                }],
            )

        rows = _rows(
            conn,
            "SELECT l.chunk_hash, l.rag_chunk_id, r.content_hash "
            "FROM dic_chunk_links l "
            "LEFT JOIN rag_chunks r ON r.id = l.rag_chunk_id "
            "WHERE l.link_id = %s",
            (entity.label,),
        )
        if not rows:
            return Verdict(
                currency_verdict="unknown",
                finding_type=None,
                rationale="Evidence anchor not found.",
                confidence=0.0,
            )

        row = rows[0]
        baseline = row.get("chunk_hash")
        current = row.get("content_hash")

        # Checked BEFORE the baseline: a dangling citation is provable without
        # one. The link names a chunk that does not exist, so the claim cannot be
        # traced to anything — that is true whether or not we ever recorded what
        # the chunk used to say. (Checking the baseline first would have reported
        # these as "unknown" and silently swallowed every broken citation that
        # predates migration 267.)
        if current is None:
            return Verdict(
                currency_verdict="retired",
                finding_type="stale_reference",
                severity=str((self.config or {}).get("deleted_severity", "high")),
                rationale=(
                    "The evidence this passage cites no longer exists — the source "
                    "chunk has been deleted. The claim can no longer be traced."
                ),
                confidence=1.0,
                evidence=[{
                    "source": f"rag_chunk:{row.get('rag_chunk_id')}",
                    "detail": "cited chunk is gone (dangling citation)",
                    "date": "",
                }],
            )

        if not baseline:
            # The chunk still exists but we never recorded what it said at link
            # time (pre-migration link). Unknown, not divergent: we must not claim
            # drift we never had the means to detect.
            return Verdict(
                currency_verdict="unknown",
                finding_type=None,
                rationale="No baseline hash recorded for this citation (pre-migration link).",
                confidence=0.0,
            )

        if current != baseline:
            return Verdict(
                currency_verdict="divergent",
                finding_type="stale_reference",
                severity=str((self.config or {}).get("changed_severity", "medium")),
                rationale=(
                    "The evidence this passage cites has changed since the document "
                    "was built from it. The document may no longer reflect its source."
                ),
                confidence=1.0,
                evidence=[{
                    "source": f"rag_chunk:{row.get('rag_chunk_id')}",
                    "detail": f"hash {str(baseline)[:12]} -> {str(current)[:12]}",
                    "date": "",
                }],
            )

        return Verdict(
            currency_verdict="current",
            finding_type=None,
            rationale="Cited evidence is unchanged since this document was built.",
            confidence=1.0,
        )

    def evidence_snapshot(self, conn) -> str:
        """Hash the corpus's live chunk hashes.

        The base implementation only hashes static config, which would make this
        pack inert — the whole point is to react when a source chunk changes.
        Any change to any cited chunk moves this digest and forces a re-scan.
        """
        rows = _rows(
            conn,
            "SELECT l.link_id, r.content_hash FROM dic_chunk_links l "
            "LEFT JOIN rag_chunks r ON r.id = l.rag_chunk_id "
            "ORDER BY l.link_id",
            (),
        )
        payload = "|".join(f"{r.get('link_id')}:{r.get('content_hash')}" for r in rows)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def recommend(self, entity: CandidateEntity, verdict: Verdict, conn) -> Any:
        """No automated replacement: only a human can decide whether a document
        should follow its source's change. Regeneration is proposed through the
        DocDrift HITL queue, never applied here."""
        return None
