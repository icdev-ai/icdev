# CUI // SP-CTI
"""evidence_currency pack — has the source a document was built from changed?

The only pack needing no domain knowledge, so it is the one that makes DocDrift
work for ANY document type. baseline = the cited chunk's hash at link time
(dic_chunk_links.chunk_hash, migration 267); current = that chunk's hash now.

The property that matters most: a document with no anchors must be reported
UNVERIFIABLE, never silently skipped — in an SSP chain, "nothing could check
this" rendering identically to "checked and current" is the dangerous outcome.

No network, no LLM.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.doc_modernization.base_pack import ChunkRef  # noqa: E402
from tools.doc_modernization.packs.evidence_currency import (  # noqa: E402
    NO_ANCHOR_LABEL,
    EvidenceCurrencyPack,
)

_CFG = {"pack_id": "evidence_currency", "label": "Evidence Currency"}


class _Cur:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _Conn:
    def __init__(self, *result_sets):
        self._queue = list(result_sets)
        self.seen = []

    def execute(self, sql, params=()):
        self.seen.append((sql, params))
        return _Cur(self._queue.pop(0) if self._queue else [])

    def rollback(self):
        pass


def _ref(link_id="ver-1_link_0", doc_id="doc-1"):
    return ChunkRef(doc_id=doc_id, version_id="ver-1", chunk_link_id=link_id,
                    page=None, section=None)


def _entity(pack, link_id="ver-1_link_0"):
    return pack.extract("some text", _ref(link_id))[0]


class TestExtractIsAboutCitationsNotProse:
    def test_one_entity_per_cited_chunk(self):
        p = EvidenceCurrencyPack(config=_CFG)
        ents = p.extract("anything at all", _ref("link-7"))
        assert len(ents) == 1
        assert ents[0].label == "link-7"          # the anchor, not a word in the text
        assert ents[0].entity_type == "evidence_anchor"

    def test_prose_content_is_irrelevant(self):
        """Unlike every other pack, the text is not the subject."""
        p = EvidenceCurrencyPack(config=_CFG)
        a = p.extract("TLS 1.1 and Catalyst 6500", _ref("link-1"))
        b = p.extract("completely different prose", _ref("link-1"))
        assert a[0].label == b[0].label == "link-1"

    def test_no_chunk_link_yields_the_unverifiable_anchor(self):
        p = EvidenceCurrencyPack(config=_CFG)
        ents = p.extract("text", _ref(link_id=None))
        assert len(ents) == 1 and ents[0].label == NO_ANCHOR_LABEL


class TestUnverifiableIsReportedNotSkipped:
    def test_document_without_anchors_raises_an_info_finding(self):
        """'Could not be checked' must not render as 'checked and current'."""
        p = EvidenceCurrencyPack(config=_CFG)
        e = p.extract("t", _ref(link_id=None))[0]
        v = p.evaluate(e, _Conn())
        assert v.is_finding is True
        assert v.finding_type == "unverifiable_evidence"
        assert v.currency_verdict == "unknown"
        assert v.severity == "info"
        assert "could not be checked" in v.rationale.lower()

    def test_one_unverifiable_finding_per_document_not_per_chunk(self):
        """dedupe_key is doc|pack|label|type, so the label must be constant."""
        p = EvidenceCurrencyPack(config=_CFG)
        a = p.extract("chunk one", _ref(link_id=None))[0]
        b = p.extract("chunk two", _ref(link_id=None))[0]
        assert a.label == b.label == NO_ANCHOR_LABEL


class TestVerdicts:
    def test_unchanged_evidence_is_current(self):
        p = EvidenceCurrencyPack(config=_CFG)
        conn = _Conn([{"chunk_hash": "abc", "rag_chunk_id": "rc-1", "content_hash": "abc"}])
        v = p.evaluate(_entity(p), conn)
        assert v.currency_verdict == "current" and v.is_finding is False

    def test_changed_evidence_is_divergent(self):
        p = EvidenceCurrencyPack(config=_CFG)
        conn = _Conn([{"chunk_hash": "abc123", "rag_chunk_id": "rc-1", "content_hash": "def456"}])
        v = p.evaluate(_entity(p), conn)
        assert v.currency_verdict == "divergent"
        assert v.finding_type == "stale_reference"
        assert v.severity == "medium"
        assert "abc123"[:12] in v.evidence[0]["detail"]

    def test_deleted_evidence_is_retired_and_worse(self):
        """A claim that can no longer be traced at all is more serious than one
        whose source merely moved."""
        p = EvidenceCurrencyPack(config=_CFG)
        conn = _Conn([{"chunk_hash": "abc", "rag_chunk_id": "rc-1", "content_hash": None}])
        v = p.evaluate(_entity(p), conn)
        assert v.currency_verdict == "retired"
        assert v.severity == "high"
        assert "no longer exists" in v.rationale

    def test_missing_baseline_but_live_chunk_is_unknown_not_divergent(self):
        """A pre-migration link has no baseline — we must not claim drift we
        never had the means to detect."""
        p = EvidenceCurrencyPack(config=_CFG)
        conn = _Conn([{"chunk_hash": None, "rag_chunk_id": "rc-1", "content_hash": "def"}])
        v = p.evaluate(_entity(p), conn)
        assert v.currency_verdict == "unknown" and v.is_finding is False

    def test_dangling_citation_is_retired_even_without_a_baseline(self):
        """A dangling citation is provable without a baseline: the chunk simply
        isn't there. 88 of 168 links in the live corpus are in exactly this
        state, and checking the baseline first would have silently swallowed
        every one of them as 'unknown'.
        """
        p = EvidenceCurrencyPack(config=_CFG)
        conn = _Conn([{"chunk_hash": None, "rag_chunk_id": "rc-gone", "content_hash": None}])
        v = p.evaluate(_entity(p), conn)
        assert v.currency_verdict == "retired"
        assert v.is_finding is True
        assert "dangling" in v.evidence[0]["detail"]

    def test_unknown_anchor_is_not_a_finding(self):
        p = EvidenceCurrencyPack(config=_CFG)
        v = p.evaluate(_entity(p), _Conn([]))
        assert v.currency_verdict == "unknown" and v.is_finding is False

    def test_severities_are_configurable(self):
        p = EvidenceCurrencyPack(config={**_CFG, "changed_severity": "critical"})
        conn = _Conn([{"chunk_hash": "a", "rag_chunk_id": "rc", "content_hash": "b"}])
        assert p.evaluate(_entity(p), conn).severity == "critical"


class TestResilience:
    def test_failing_query_rolls_back_and_degrades(self):
        p = EvidenceCurrencyPack(config=_CFG)

        class _Boom:
            rolled_back = False

            def execute(self, *a, **k):
                raise RuntimeError("no such column: chunk_hash")

            def rollback(self):
                _Boom.rolled_back = True

        v = p.evaluate(_entity(p), _Boom())
        assert v.is_finding is False
        assert _Boom.rolled_back is True


class TestEvidenceSnapshot:
    def test_a_changed_chunk_moves_the_snapshot(self):
        """Default snapshot hashes static config only, which would leave this
        pack inert — the entire point is reacting when a chunk changes."""
        p = EvidenceCurrencyPack(config=_CFG)
        a = p.evidence_snapshot(_Conn([{"link_id": "l1", "content_hash": "h1"}]))
        b = p.evidence_snapshot(_Conn([{"link_id": "l1", "content_hash": "h2"}]))
        assert a != b

    def test_stable_corpus_stable_snapshot(self):
        p = EvidenceCurrencyPack(config=_CFG)
        rows = [{"link_id": "l1", "content_hash": "h1"}]
        assert p.evidence_snapshot(_Conn(list(rows))) == p.evidence_snapshot(_Conn(list(rows)))


class TestNoAutomatedFix:
    def test_recommend_never_proposes_a_replacement(self):
        """Only a human decides whether a document should follow its source."""
        p = EvidenceCurrencyPack(config=_CFG)
        conn = _Conn([{"chunk_hash": "a", "rag_chunk_id": "rc", "content_hash": "b"}])
        v = p.evaluate(_entity(p), conn)
        assert p.recommend(_entity(p), v, conn) is None


class TestVocabulary:
    def test_types_are_declared(self):
        from tools.doc_modernization.constants import FINDING_TYPES, KG_ENTITY_TYPES

        assert "unverifiable_evidence" in FINDING_TYPES
        assert "evidence_anchor" in KG_ENTITY_TYPES
