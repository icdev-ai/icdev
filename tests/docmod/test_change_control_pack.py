# CUI // SP-CTI
"""change_control pack — approved change documents supersede older documents.

CRs / ERB / ARB decisions arrive as documents, so the question is
document-to-document: is there an approved change document, NEWER than this
document's approved version, naming a system this document describes?

The verdict must be a SQL fact, never a judgement (TRUST rule 1), and the pack
must be inert — not falsely reassuring — when no change corpus is ingested.

No network, no LLM. Fake conn objects; the SQL shape is asserted where it matters.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.doc_modernization.base_pack import CandidateEntity, ChunkRef  # noqa: E402
from tools.doc_modernization.packs.change_control import ChangeControlPack  # noqa: E402

_CFG = {
    "pack_id": "change_control",
    "label": "Change Control",
    "change_collections": ["change-records"],
    "extraction": {"patterns": [r"\b[A-Z][A-Z0-9]{1,7}(?:-[A-Z0-9]{1,8}){1,3}\b"]},
}


class _Cur:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _Conn:
    """Returns queued result sets in order; records the SQL it saw."""

    def __init__(self, *result_sets):
        self._queue = list(result_sets)
        self.seen: list[tuple] = []

    def execute(self, sql, params=()):
        self.seen.append((sql, params))
        return _Cur(self._queue.pop(0) if self._queue else [])

    def rollback(self):
        pass


def _entity(label="CORE-RTR-01", doc_id="doc-target", version_id="ver-1"):
    return CandidateEntity(
        label=label, entity_type="system_reference", pack_id="change_control",
        chunk_ref=ChunkRef(doc_id=doc_id, version_id=version_id, chunk_link_id="l1",
                           page=None, section=None),
        raw_match=label, context="",
    )


class TestExtract:
    def test_finds_system_references(self):
        p = ChangeControlPack(config=_CFG)
        ents = p.extract("Failover is handled by CORE-RTR-01 and EDGE-MX304-02.", _entity().chunk_ref)
        assert {e.label for e in ents} == {"CORE-RTR-01", "EDGE-MX304-02"}
        assert all(e.entity_type == "system_reference" for e in ents)

    def test_dedupes_within_a_chunk(self):
        p = ChangeControlPack(config=_CFG)
        ents = p.extract("CORE-RTR-01 ... core-rtr-01 ... CORE-RTR-01", _entity().chunk_ref)
        assert len(ents) == 1

    def test_prose_is_not_a_system_reference(self):
        p = ChangeControlPack(config=_CFG)
        assert p.extract("The network is fine and stable.", _entity().chunk_ref) == []


class TestInertWithoutAChangeCorpus:
    def test_no_configured_collection_reports_unknown_not_current(self):
        """Absence of a change corpus must never read as 'checked and current'."""
        p = ChangeControlPack(config={**_CFG, "change_collections": []})
        v = p.evaluate(_entity(), _Conn())
        assert v.currency_verdict == "unknown"
        assert v.finding_type is None and v.is_finding is False
        assert "no change-record collection" in v.rationale.lower()

    def test_collection_configured_but_not_ingested_is_also_unknown(self):
        p = ChangeControlPack(config=_CFG)
        v = p.evaluate(_entity(), _Conn([]))  # collection lookup -> nothing
        assert v.currency_verdict == "unknown"
        assert v.is_finding is False


class TestVerdicts:
    def test_newer_change_doc_makes_the_document_divergent(self):
        p = ChangeControlPack(config=_CFG)
        conn = _Conn(
            [{"collection_id": "c1", "name": "change-records"}],          # collections
            [{"created_at": "2026-01-01T00:00:00Z"}],                     # approved_at
            [{"doc_id": "cr-9", "title": "CR-1042 core switch refresh",
              "filename": "cr.pdf", "created_at": "2026-06-01T00:00:00Z"}],
        )
        v = p.evaluate(_entity(), conn)
        assert v.currency_verdict == "divergent"
        assert v.finding_type == "unreflected_change"
        assert v.severity == "high"
        assert v.confidence == 1.0
        # the change document itself is the citation
        assert v.evidence[0]["source"] == "change_doc:cr-9"
        assert "CR-1042" in v.evidence[0]["detail"]

    def test_change_predating_the_document_is_not_drift(self):
        """A change approved BEFORE the doc's version is already reflected."""
        p = ChangeControlPack(config=_CFG)
        conn = _Conn(
            [{"collection_id": "c1"}],
            [{"created_at": "2026-06-01T00:00:00Z"}],                     # doc approved later
            [{"doc_id": "cr-1", "title": "old CR", "filename": "x.pdf",
              "created_at": "2026-01-01T00:00:00Z"}],
        )
        v = p.evaluate(_entity(), conn)
        assert v.currency_verdict == "current"
        assert v.is_finding is False

    def test_no_mentioning_change_doc_is_current(self):
        p = ChangeControlPack(config=_CFG)
        conn = _Conn([{"collection_id": "c1"}], [{"created_at": "2026-01-01T00:00:00Z"}], [])
        v = p.evaluate(_entity(), conn)
        assert v.currency_verdict == "current" and v.is_finding is False

    def test_unreadable_approved_timestamp_is_unknown(self):
        p = ChangeControlPack(config=_CFG)
        conn = _Conn([{"collection_id": "c1"}], [{"created_at": "not-a-date"}])
        v = p.evaluate(_entity(), conn)
        assert v.currency_verdict == "unknown" and v.is_finding is False

    def test_newest_change_is_cited_first_and_capped(self):
        p = ChangeControlPack(config={**_CFG, "max_evidence_docs": 2})
        conn = _Conn(
            [{"collection_id": "c1"}],
            [{"created_at": "2026-01-01T00:00:00Z"}],
            [
                {"doc_id": "cr-a", "title": "A", "filename": "a.pdf", "created_at": "2026-03-01T00:00:00Z"},
                {"doc_id": "cr-b", "title": "B", "filename": "b.pdf", "created_at": "2026-09-01T00:00:00Z"},
                {"doc_id": "cr-c", "title": "C", "filename": "c.pdf", "created_at": "2026-05-01T00:00:00Z"},
            ],
        )
        v = p.evaluate(_entity(), conn)
        assert len(v.evidence) == 2                      # capped
        assert v.evidence[0]["source"] == "change_doc:cr-b"   # newest first
        assert "B" in v.rationale


class TestQueryShape:
    def test_the_document_cannot_flag_itself(self):
        """A CR living in the change collection must not flag itself."""
        p = ChangeControlPack(config=_CFG)
        conn = _Conn([{"collection_id": "c1"}], [{"created_at": "2026-01-01T00:00:00Z"}], [])
        p.evaluate(_entity(doc_id="doc-self"), conn)
        sql, params = conn.seen[-1]
        assert "d.doc_id <> %s" in sql
        assert "doc-self" in params

    def test_chunkless_change_docs_stay_eligible(self):
        """A freshly ingested CR may not be chunk-linked yet; its title still
        names the system. A LEFT JOIN keeps it visible."""
        p = ChangeControlPack(config=_CFG)
        conn = _Conn([{"collection_id": "c1"}], [{"created_at": "2026-01-01T00:00:00Z"}], [])
        p.evaluate(_entity(), conn)
        sql, _ = conn.seen[-1]
        assert "LEFT JOIN dic_chunk_links" in sql
        assert "LOWER(COALESCE(d.title, '')) LIKE" in sql

    def test_a_failing_query_degrades_and_rolls_back(self):
        """One failed statement poisons the whole PG transaction."""
        p = ChangeControlPack(config=_CFG)

        class _Boom:
            rolled_back = False

            def execute(self, *a, **k):
                raise RuntimeError("no such table")

            def rollback(self):
                _Boom.rolled_back = True

        v = p.evaluate(_entity(), _Boom())
        assert v.currency_verdict == "unknown" and v.is_finding is False
        assert _Boom.rolled_back is True


class TestEvidenceSnapshot:
    def test_a_new_change_document_changes_the_snapshot(self):
        """Default evidence_snapshot only hashes static config, which would make
        the pack inert — a newly ingested CR must force a re-scan."""
        p = ChangeControlPack(config=_CFG)
        a = p.evidence_snapshot(_Conn([{"collection_id": "c1"}],
                                      [{"doc_id": "cr-1", "created_at": "2026-01-01"}]))
        b = p.evidence_snapshot(_Conn([{"collection_id": "c1"}],
                                      [{"doc_id": "cr-1", "created_at": "2026-01-01"},
                                       {"doc_id": "cr-2", "created_at": "2026-02-01"}]))
        assert a != b

    def test_unchanged_corpus_keeps_a_stable_snapshot(self):
        p = ChangeControlPack(config=_CFG)
        rows = [{"doc_id": "cr-1", "created_at": "2026-01-01"}]
        a = p.evidence_snapshot(_Conn([{"collection_id": "c1"}], list(rows)))
        b = p.evidence_snapshot(_Conn([{"collection_id": "c1"}], list(rows)))
        assert a == b


class TestVocabulary:
    def test_finding_type_and_entity_type_are_declared(self):
        from tools.doc_modernization.constants import FINDING_TYPES, KG_ENTITY_TYPES

        assert "unreflected_change" in FINDING_TYPES
        assert "system_reference" in KG_ENTITY_TYPES
