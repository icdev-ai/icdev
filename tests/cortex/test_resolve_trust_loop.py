# CUI // SP-CTI
"""``cortex.resolve`` closes its TRUST loop — citations + provenance (cef-rsv-03).

Four things are asserted here, and the shape of the first two is the point.

**The registry row is READ, never assumed.** Every existing test of the Cortex
provenance gate patches ``_gate_register_provenance`` with a fake that returns a
canned id and then asserts the gate was *invoked* — including
``test_resolve_facade.py::test_a_resolution_audits_with_a_provenance_id``, which
proves the gate RAN and cannot see whether a row landed. That is the exact shape
that let cxo-trust-01 ship: ``citation_type="cortex"`` was absent from
``CITATION_TYPES``, ``register_citation`` raised before the INSERT, the gate
recorded ``warn``, and 0 of 285 rows were type ``cortex`` while every test
stayed green. So the tests below leave the registry write REAL against a temp
SQLite database and the assertions are ``SELECT``s. ``test_pre_fix_vocabulary_``
``lands_nothing`` restores the broken vocabulary and asserts the same reads flip,
so they are proven to discriminate rather than merely to pass.

**A block is asserted as a REFUSAL, not as a flag.** Each of the three
``BLOCK_*`` causes is asserted with ``pytest.raises`` plus a read of the audit
row showing the ``operation`` gate failed — a test that only checked a
``blocked: True`` field on a returned object would pass against a degrading
implementation, which is the failure this card exists to prevent.

The negative controls matter as much as the positives, because every one of
these findings has a zero that is not a defect: a gap with no citations because
nothing matched is honest, a conflict side with no row id is honest, and a
resolution with no successor claims nothing. Each is asserted to be
DISTINGUISHABLE from the defect it resembles.
"""
from __future__ import annotations

import importlib
import json

import pytest

from tools.cortex import api, governance, resolution_provenance, resolver
from tools.cortex.resolution_provenance import (
    BASIS_EVIDENCE,
    BASIS_NO_EVIDENCE,
    BASIS_RETRIEVAL_FAILED,
    SIDE_NO_ROW_ID,
    STATUS_MISCONFIGURED,
    STATUS_WRITTEN,
    citation_digest,
)
from tools.cortex.resolver import (
    BLOCK_UNATTESTED_FINDING,
    BLOCK_UNATTESTED_REPLACEMENT,
    CortexResolutionBlocked,
)
from tools.cortex.schemas import Citation, CortexContext, CortexSearchResult
from tools.cortex.search_service import BackendResults

registry = importlib.import_module("tools.provenance.registry")
citation_types = importlib.import_module("tools.provenance.citation_types")
cortex_db = importlib.import_module("tools.cortex.db.init_db")


# ---------------------------------------------------------------------------
# Fakes — a scripted DomainPack and scripted fan-out hits
# ---------------------------------------------------------------------------
def make_pack(
    pack_id="fake",
    *,
    matches=True,
    currency_verdict="deprecated",
    rationale="Deprecated by RFC 8996.",
    confidence=1.0,
    evidence_source="rule:tls-11",
    replacement=None,
    replacement_ref=None,
):
    """A DomainPack whose extract/evaluate/recommend are fully scripted.

    ``replacement_ref`` is separable from ``evidence_source`` on purpose: a pack
    that NAMES a successor while pointing at nothing is the case
    ``replacement_attestation`` exists to refuse, and it cannot be expressed if
    the two are the same knob.
    """
    from tools.doc_modernization.base_pack import (
        CandidateEntity,
        DomainPack,
        Replacement,
        Verdict,
    )

    class _FakePack(DomainPack):
        def extract(self, text, chunk_ref):
            if not matches:
                return []
            return [CandidateEntity(
                label=text.strip(),
                entity_type="protocol",
                pack_id=pack_id,
                chunk_ref=chunk_ref,
            )]

        def evaluate(self, entity, conn):
            return Verdict(
                currency_verdict=currency_verdict,
                finding_type="deprecated_tech" if currency_verdict != "current" else None,
                severity="high",
                rationale=rationale,
                confidence=confidence,
                evidence=([{"source": evidence_source, "detail": rationale, "date": ""}]
                          if evidence_source else []),
            )

        def recommend(self, entity, verdict, conn):
            if replacement is None:
                return None
            ref = evidence_source if replacement_ref is None else replacement_ref
            return Replacement(
                label=replacement,
                source="rulebook",
                source_ref=ref,
                detail=f"move to {replacement}",
                evidence=([{"source": ref, "detail": "", "date": ""}] if ref else []),
            )

    return _FakePack(config={"pack_id": pack_id, "label": pack_id,
                             "entity_types": ["protocol"]})


def hit(source_id="rag:1", backend="rag", score=0.9,
        content="TLS 1.1 appears in SOP-12.", advisory=False, metadata=None):
    meta = dict(metadata or {})
    if advisory:
        meta["advisory"] = True
    return CortexSearchResult(
        content=content,
        score=score,
        backend=backend,
        strategy="stub",
        citation=Citation(source_id=source_id, source_type=backend,
                          source_table=f"{backend}_chunks", title="stub", snippet=content),
        metadata=meta,
    )


def currency_hit(source_id="ec:1", verdict="current", others=(), label="TLS 1.1"):
    """What the ``currency`` backend publishes, including its ``others`` lane."""
    return hit(
        source_id=source_id,
        backend="currency",
        content=f"{label} — {verdict} per catalog.",
        metadata={
            "lane": "assertion",
            "entity_label": label,
            "entity_type": "protocol",
            "verdict": verdict,
            "source": "catalog",
            "authoritative": True,
            "conflict": bool(others),
            "others": list(others),
        },
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def packs(monkeypatch):
    def _install(*pack_objs):
        monkeypatch.setattr(resolver, "_load_packs",
                            lambda: {p.pack_id: p for p in pack_objs})
        return pack_objs

    class _Conn:
        def rollback(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(resolver, "_evidence_connection", lambda: _Conn())
    return _install


@pytest.fixture
def evidence(monkeypatch):
    def _install(results=(), errors=()):
        def fake_search(query, **kwargs):
            return BackendResults(results, errors=list(errors))

        monkeypatch.setattr(resolver, "_search_impl", fake_search)

    return _install


@pytest.fixture
def offline_gates(monkeypatch):
    """Governance gates 1-3 and 7 faked; the AUDIT payloads are recorded.

    Gate 8 (provenance + audit) is deliberately left real in the ``registry_db``
    fixture below and faked here, so a test can choose which half it is about.
    """
    record = {"audit": []}
    monkeypatch.setattr(governance, "_gate_check_text",
                        lambda text: {"allowed": True, "warnings": [],
                                      "blocked_reason": None})
    monkeypatch.setattr(governance, "_gate_redact_input", lambda text, cls: (text, 0))
    monkeypatch.setattr(governance, "_gate_redact_output", lambda text: (text, []))
    monkeypatch.setattr(governance, "_gate_register_provenance",
                        lambda out, ctx, op, rid: "scr-gate-1")
    monkeypatch.setattr(governance, "_gate_record_audit",
                        lambda payload: record["audit"].append(payload))
    return record


@pytest.fixture
def no_registry(monkeypatch):
    """Capture the resolver's registry write without a database.

    Returns the list of kwargs it was called with, so the tests that are about
    citations rather than persistence still exercise the real code path.
    """
    calls: list = []

    def _fake(**kwargs):
        calls.append(kwargs)
        return "scr-resolution-1"

    monkeypatch.setattr(resolution_provenance, "_register_citation", _fake)
    return calls


@pytest.fixture
def registry_db(tmp_path, monkeypatch):
    """A temp SQLite DB carrying the two tables the real writes land in.

    Same construction as ``tests/cortex/test_provenance_gate.py``: ``ICDEV_DB_``
    ``PATH`` steers ``get_connection`` (the audit row) and ``registry.DB_PATH``
    steers ``register_citation``, and both must name ONE file or the
    provenance-id join has nothing to join against. The CHECK clause is rendered
    from ``sqlite_check_clause()`` rather than retyped, so the constraint under
    test is the one the migration ships.
    """
    db_path = tmp_path / "icdev.db"
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(db_path))
    monkeypatch.setattr(registry, "DB_PATH", db_path)
    monkeypatch.setattr(cortex_db, "_SCHEMA_ENSURED", False)

    from tools.db.storage import get_connection

    conn = get_connection()
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS source_citation_registry ("
            " id TEXT PRIMARY KEY,"
            " citation_type TEXT NOT NULL "
            + citation_types.sqlite_check_clause()
            + ", source_table TEXT NOT NULL,"
            " source_record_id TEXT NOT NULL,"
            " source_doc TEXT,"
            " source_hash TEXT NOT NULL,"
            " anchor_hash TEXT,"
            " merkle_root TEXT,"
            " blockchain_tx_id TEXT,"
            " classification TEXT DEFAULT 'CUI',"
            " project_id TEXT,"
            " trust_score REAL DEFAULT 0.0,"
            " created_at TEXT DEFAULT CURRENT_TIMESTAMP)"
        )
        conn.commit()
    finally:
        conn.close()

    cortex_db.init_db()
    return db_path


@pytest.fixture
def real_provenance(monkeypatch, registry_db):
    """Gates 1-6 faked, gate 8 and the resolver's own registry write REAL."""
    monkeypatch.setattr(governance, "_gate_check_text",
                        lambda text: {"allowed": True, "warnings": [],
                                      "blocked_reason": None})
    monkeypatch.setattr(governance, "_gate_redact_input", lambda text, cls: (text, 0))
    monkeypatch.setattr(governance, "_gate_redact_output", lambda text: (text, []))
    return registry_db


def _registry_rows(source_table=None, citation_type="cortex"):
    from tools.db.storage import get_connection

    conn = get_connection()
    try:
        cur = conn.cursor()
        sql = (
            "SELECT id, citation_type, source_table, source_record_id, source_doc, "
            "source_hash, classification, project_id FROM source_citation_registry "
            "WHERE citation_type = %s"
        )
        params = [citation_type]
        if source_table is not None:
            sql += " AND source_table = %s"
            params.append(source_table)
        cur.execute(sql, tuple(params))
        cols = ["id", "citation_type", "source_table", "source_record_id",
                "source_doc", "source_hash", "classification", "project_id"]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# AC1 — every resolve writes a source_citation_registry row of type 'cortex'
# ---------------------------------------------------------------------------
class TestProvenanceIsPersisted:
    def test_a_resolution_writes_a_cortex_registry_row(self, real_provenance, packs,
                                                       evidence):
        packs(make_pack())
        evidence(results=[hit()])

        api.resolve("TLS 1.1", ctx=CortexContext(tenant_id="t1"))

        rows = _registry_rows(source_table=resolution_provenance.SOURCE_TABLE)
        assert len(rows) == 1, (
            "a resolution wrote no source_citation_registry row describing its "
            "evidence — the governance gate's row hashes the PROSE and names no "
            "source, so without this the evidence a verdict rests on is "
            "unregistered"
        )
        assert rows[0]["citation_type"] == "cortex"
        assert rows[0]["project_id"] == "t1"
        assert "TLS 1.1" in rows[0]["source_doc"]
        assert "deprecated" in rows[0]["source_doc"]

    def test_the_governance_row_is_still_written_too(self, real_provenance, packs,
                                                     evidence):
        """Two rows, two subjects. Neither replaces the other."""
        packs(make_pack())
        evidence(results=[hit()])

        api.resolve("TLS 1.1", ctx=CortexContext(tenant_id="t1"))

        assert len(_registry_rows(source_table="cortex_governance")) == 1
        assert len(_registry_rows(source_table="cortex_resolution")) == 1

    def test_the_row_hashes_the_evidence_set_it_returned(self, real_provenance, packs,
                                                         evidence):
        """A row that landed but does not describe THIS evidence proves nothing."""
        packs(make_pack())
        evidence(results=[hit(), hit(source_id="dic:9", backend="dic")])

        result = api.resolve("TLS 1.1", ctx=CortexContext(tenant_id="t1"))

        row = _registry_rows(source_table="cortex_resolution")[0]
        assert row["source_hash"] == citation_digest(
            result.entity, result.verdict, result.citations
        ), "the digest is not recomputable from the resolution the caller was handed"

    def test_every_citation_carries_the_registry_id(self, real_provenance, packs,
                                                    evidence):
        packs(make_pack())
        evidence(results=[hit()])

        result = api.resolve("TLS 1.1", ctx=CortexContext(tenant_id="t1"))

        row = _registry_rows(source_table="cortex_resolution")[0]
        assert result.citations, "the resolution returned no citations to stamp"
        assert {c.provenance_id for c in result.citations} == {row["id"]}, (
            "Citation.provenance_id has existed on the schema since Cortex "
            "shipped and was empty on every resolution — the join must run both "
            "ways"
        )
        assert result.metadata["provenance"]["status"] == STATUS_WRITTEN
        assert result.governance.outcomes["provenance"] == "pass"

    def test_a_resolution_with_no_citations_still_registers(self, real_provenance,
                                                            packs, evidence):
        """"Every resolve" includes the one that answered nothing."""
        packs(make_pack(matches=False))
        evidence(results=[], errors=[])

        result = api.resolve("Nortel Passport 8600", ctx=CortexContext())

        assert result.verdict == "unknown"
        rows = _registry_rows(source_table="cortex_resolution")
        assert len(rows) == 1
        assert "0 citation(s)" in rows[0]["source_doc"], (
            "an empty evidence set must be visible in the row, not only inside "
            "the digest"
        )

    def test_the_record_id_is_deterministic(self, real_provenance, packs, evidence):
        """The same entity over the same evidence names the same record."""
        packs(make_pack())
        evidence(results=[hit()])

        api.resolve("TLS 1.1", ctx=CortexContext())
        api.resolve("TLS 1.1", ctx=CortexContext())

        rows = _registry_rows(source_table="cortex_resolution")
        assert len(rows) == 2
        assert len({r["source_record_id"] for r in rows}) == 1, (
            "two resolutions of one entity over one evidence set minted two "
            "uncorrelatable record ids"
        )

    def test_pre_fix_vocabulary_lands_nothing(self, real_provenance, packs, evidence,
                                              monkeypatch):
        """Restore the pre-cxo-trust-01 vocabulary; the reads above must flip.

        And the STATUS must say ``misconfigured`` rather than ``unavailable``:
        the whole reason that bug survived is that a bad vocabulary value was
        recorded as a transient degradation.
        """
        monkeypatch.setattr(
            citation_types, "CITATION_TYPES",
            tuple(t for t in citation_types.CITATION_TYPES if t != "cortex"),
        )
        packs(make_pack())
        evidence(results=[hit()])

        result = api.resolve("TLS 1.1", ctx=CortexContext())

        assert _registry_rows(source_table="cortex_resolution") == []
        assert result.metadata["provenance"]["status"] == STATUS_MISCONFIGURED
        assert result.governance.outcomes["provenance"] == "fail", (
            "'warn' is the cxo-trust-01 signature — a misconfiguration must not "
            "read as a momentary outage"
        )
        assert all(c.provenance_id == "" for c in result.citations)

    def test_a_failed_write_never_breaks_the_resolution(self, offline_gates, packs,
                                                        evidence, monkeypatch):
        """Provenance is fail-open, and says so on the report."""
        def _boom(**kwargs):
            raise RuntimeError("connection refused")

        monkeypatch.setattr(resolution_provenance, "_register_citation", _boom)
        packs(make_pack())
        evidence(results=[hit()])

        result = api.resolve("TLS 1.1", ctx=CortexContext())

        assert result.verdict == "deprecated"
        assert result.metadata["provenance"]["status"] == "unavailable"
        assert result.governance.outcomes["provenance"] == "warn"

    def test_an_empty_id_is_a_failed_write_not_a_written_one(self, offline_gates,
                                                             packs, evidence,
                                                             monkeypatch):
        """``register_citation`` swallows DB errors and returns ``""``."""
        monkeypatch.setattr(resolution_provenance, "_register_citation",
                            lambda **kwargs: "")
        packs(make_pack())
        evidence(results=[hit()])

        result = api.resolve("TLS 1.1", ctx=CortexContext())

        assert result.metadata["provenance"]["status"] == "unavailable"
        assert result.metadata["provenance"]["detail"] == "registry insert returned no id"


# ---------------------------------------------------------------------------
# AC2 — a hallucinated citation BLOCKS; so does an unbacked replacement
# ---------------------------------------------------------------------------
class TestBlocking:
    def test_a_finding_citing_an_unknown_source_blocks(self, offline_gates, packs,
                                                       evidence, no_registry,
                                                       monkeypatch):
        """A conflict side pointing outside the evidence set is refused.

        Injected at the DETECTOR seam because that is where such a side can come
        from: a claim built off a hit whose citation never entered the set.
        """
        def _fake_resolve_entities(hits, **kwargs):
            return {
                "entities": [], "claims": [], "gaps": [], "unresolved": [],
                "backends_consulted": [], "backends_failed": [], "text_claims": False,
                "conflicts": [{
                    "entity_key": "tls 1.1", "entity_label": "TLS 1.1",
                    "kind": "status", "values": ["current", "deprecated"],
                    "backends": ["rag"], "cross_backend": True,
                    "sides": [
                        {"source_id": "rag:1", "source": "sop", "backend": "rag",
                         "status": "deprecated"},
                        {"source_id": "ghost:404", "source": "nowhere",
                         "backend": "rag", "status": "current"},
                    ],
                }],
            }

        monkeypatch.setattr(resolver, "resolve_entities", _fake_resolve_entities)
        packs(make_pack())
        evidence(results=[hit()])

        with pytest.raises(CortexResolutionBlocked) as exc:
            api.resolve("TLS 1.1", ctx=CortexContext())

        assert exc.value.reason == BLOCK_UNATTESTED_FINDING
        assert exc.value.report["hallucinated_citations"] == ["ghost:404"]

    def test_the_finding_block_is_a_refusal_not_a_flag(self, offline_gates, packs,
                                                       evidence, no_registry,
                                                       monkeypatch):
        """The audit row must record the operation as FAILED."""
        monkeypatch.setattr(resolver, "resolve_entities", lambda hits, **kw: {
            "entities": [], "claims": [], "gaps": [], "unresolved": [],
            "backends_consulted": [], "backends_failed": [], "text_claims": False,
            "conflicts": [{
                "entity_key": "tls 1.1", "entity_label": "TLS 1.1", "kind": "status",
                "values": ["current", "deprecated"], "backends": ["rag"],
                "cross_backend": True,
                "sides": [{"source_id": "ghost:404", "source": "nowhere",
                           "backend": "rag", "status": "current"}],
            }],
        })
        packs(make_pack())
        evidence(results=[hit()])

        with pytest.raises(CortexResolutionBlocked):
            api.resolve("TLS 1.1", ctx=CortexContext())

        assert len(offline_gates["audit"]) == 1
        assert offline_gates["audit"][0]["blocked_gate"] == "operation"
        assert no_registry == [], (
            "a refused resolution registered its evidence set — a blocked "
            "answer has no evidence set worth attesting"
        )

    def test_an_unbacked_recommended_replacement_blocks(self, offline_gates, packs,
                                                        evidence, no_registry):
        """The resolve-side analogue of redline_drafter's out-of-candidate block.

        "Recommended replacement: X" is the actionable line in a resolution and
        the one a redline is drafted from, so a pack naming a successor it
        cannot point at is refused rather than rendered.
        """
        packs(make_pack(replacement="TLS 1.3", replacement_ref=""))
        evidence(results=[hit()])

        with pytest.raises(CortexResolutionBlocked) as exc:
            api.resolve("TLS 1.1", ctx=CortexContext())

        assert exc.value.reason == BLOCK_UNATTESTED_REPLACEMENT
        assert exc.value.report["successor"] == "TLS 1.3"

    def test_a_backed_replacement_is_returned(self, offline_gates, packs, evidence,
                                              no_registry):
        """The discriminating control: the SAME pack with a ref resolves."""
        packs(make_pack(replacement="TLS 1.3"))
        evidence(results=[hit()])

        result = api.resolve("TLS 1.1", ctx=CortexContext())

        assert result.verdict == "superseded"
        assert result.metadata["replacement_attestation"] == {
            "claimed": True, "attested": True,
            "successor": "TLS 1.3", "ref": "rule:tls-11",
        }

    def test_a_resolution_claiming_no_successor_is_not_blocked(self, offline_gates,
                                                               packs, evidence,
                                                               no_registry):
        """No claim, nothing to attest. The gate must not manufacture a defect."""
        packs(make_pack(replacement=None))
        evidence(results=[hit()])

        result = api.resolve("TLS 1.1", ctx=CortexContext())

        assert result.metadata["replacement_attestation"]["claimed"] is False
        assert result.metadata["replacement_attestation"]["attested"] is True


# ---------------------------------------------------------------------------
# AC3 — citation validation is REUSED, never re-implemented
# ---------------------------------------------------------------------------
class TestNoSecondParser:
    def test_the_resolve_path_imports_the_shared_validator(self):
        from pathlib import Path

        src = Path(resolver.__file__).read_text(encoding="utf-8")
        assert "from tools.quality.citation_grounding import validate_citations" in src

    @pytest.mark.parametrize("module", [resolver, resolution_provenance])
    def test_no_citation_regex_lives_on_the_resolve_path(self, module):
        """A second parser is how two surfaces start disagreeing about an id.

        Neither module may import ``re`` at all. ``resolver`` EMITS
        ``[source: id]`` tags, which is fine — writing a tag is not reading one.
        Reading is ``citation_grounding``'s, and everything cef-rsv-03 validates
        is a STRUCTURED id a claim already carried, checked by set arithmetic.
        """
        import ast
        from pathlib import Path

        src = Path(module.__file__).read_text(encoding="utf-8")
        imported: set = set()
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Import):
                imported.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add((node.module or "").split(".")[0])
        assert "re" not in imported, (
            "a regex on the resolve path is how a second citation parser starts"
        )
        assert "parse_citations" not in src

    def test_the_two_surfaces_validate_against_one_allowed_set(self, offline_gates,
                                                              packs, evidence,
                                                              no_registry):
        """The prose report and the finding report must agree on availability."""
        packs(make_pack())
        evidence(results=[hit()])

        result = api.resolve("TLS 1.1", ctx=CortexContext())

        prose = result.metadata["citation_report"]
        findings = result.metadata["finding_citation_report"]
        assert prose["available_count"] == findings["available_count"]


# ---------------------------------------------------------------------------
# AC4 — a gap and a conflict each carry citations for their evidence
# ---------------------------------------------------------------------------
class TestFindingsCarryCitations:
    def test_a_gap_cites_the_evidence_that_produced_it(self, offline_gates, packs,
                                                       evidence, no_registry):
        """Sources mentioned the entity and none answered — so cite them."""
        packs(make_pack(matches=False))
        evidence(results=[hit(content="TLS 1.1 is referenced in SOP-12.")])

        result = api.resolve("TLS 1.1", ctx=CortexContext())

        assert result.verdict == "unknown"
        gap = result.gaps[0]
        assert [c["source_id"] for c in gap["citations"]] == ["rag:1"]
        assert gap["citation_basis"] == BASIS_EVIDENCE

    def test_a_gap_with_nothing_retrieved_says_which_zero_it_is(self, offline_gates,
                                                                packs, evidence,
                                                                no_registry):
        """An empty citation list is honest here — and must not read as the same
        thing as an outage."""
        packs(make_pack(matches=False))
        evidence(results=[], errors=[])

        result = api.resolve("Nortel Passport 8600", ctx=CortexContext())

        gap = result.gaps[0]
        assert gap["citations"] == []
        assert gap["citation_basis"] == BASIS_NO_EVIDENCE

    def test_a_gap_from_a_dead_fanout_is_not_a_corpus_statement(self, offline_gates,
                                                                packs, evidence,
                                                                no_registry):
        packs(make_pack(matches=False))
        evidence(results=[], errors=[{"backend": "rag", "stage": "query",
                                      "message": "boom"}])

        result = api.resolve("TLS 1.1", ctx=CortexContext())

        gap = result.gaps[0]
        assert gap["citations"] == []
        assert gap["citation_basis"] == BASIS_RETRIEVAL_FAILED, (
            "an outage was reported with the same basis as an empty corpus"
        )

    def test_a_conflict_cites_the_row_behind_each_side(self, offline_gates, packs,
                                                      evidence, no_registry):
        """Two packs disagreeing: each side's rule is cited."""
        packs(
            make_pack(pack_id="pack_a", currency_verdict="current",
                      evidence_source="catalog:ok"),
            make_pack(pack_id="pack_b", currency_verdict="deprecated",
                      evidence_source="rule:tls-11"),
        )
        evidence(results=[hit()])

        result = api.resolve("TLS 1.1", ctx=CortexContext())

        conflicts = [c for c in result.conflicts if c["kind"] == "status"]
        assert conflicts, "two packs disagreed and no conflict was reported"
        cited = {c["source_id"] for c in conflicts[0]["citations"]}
        assert cited == {"catalog:ok", "rule:tls-11"}
        assert conflicts[0]["uncited_sides"] == []

    def test_a_side_with_no_row_id_is_reported_not_faked(self, offline_gates, packs,
                                                        evidence, no_registry):
        """``entity_currency``'s losing sources name an authority and no row.

        Lending such a side the winning row's citation would attribute one
        source's claim to another source's row, so it is reported instead.
        """
        packs(make_pack(matches=False))
        evidence(results=[currency_hit(
            verdict="current",
            others=[{"source": "eol-feed", "verdict": "deprecated"}],
        )])

        result = api.resolve("TLS 1.1", ctx=CortexContext())

        conflicts = [c for c in result.conflicts if c["kind"] == "status"]
        assert conflicts, "the store's preserved disagreement produced no conflict"
        conflict = conflicts[0]
        assert [c["source_id"] for c in conflict["citations"]] == ["ec:1"]
        assert len(conflict["uncited_sides"]) == 1
        assert conflict["uncited_sides"][0]["source"] == "eol-feed"
        assert conflict["uncited_sides"][0]["reason"] == SIDE_NO_ROW_ID
        assert result.metadata["finding_citation_report"]["uncited_conflict_sides"] == 1

    def test_an_advisory_opinion_never_becomes_a_conflict_side(self, offline_gates,
                                                               packs, evidence,
                                                               no_registry):
        """An ``sme`` hit is not evidence, so it cannot be a side of a finding.

        This is the discriminating case for feeding the detector the EVIDENTIARY
        hits only: before that, the opinion became a claim, the claim became a
        conflict side, and its source id was in no citation — which the finding
        validation now refuses outright. Either way the pre-fix tree does not
        return this resolution.
        """
        packs(make_pack(currency_verdict="deprecated"))
        evidence(results=[
            hit(),
            hit(source_id="sme:1", backend="sme", advisory=True,
                content="TLS 1.1 — current per the domain expert.",
                metadata={"entity_label": "TLS 1.1", "verdict": "current",
                          "source": "sme"}),
        ])

        result = api.resolve("TLS 1.1", ctx=CortexContext())

        assert result.verdict == "deprecated"
        for conflict in result.conflicts:
            sides = {s.get("source_id") for s in conflict["sides"]}
            assert "sme:1" not in sides
        assert "sme:1" not in {c.source_id for c in result.citations}
        assert result.metadata["advisory"], "the opinion must still be surfaced"

    def test_the_findings_travel_over_the_serialized_boundary(self, offline_gates,
                                                              packs, evidence,
                                                              no_registry):
        """MCP and REST hand back ``to_dict()``; the citations must survive it."""
        packs(make_pack(matches=False))
        evidence(results=[hit(content="TLS 1.1 is referenced in SOP-12.")])

        payload = json.loads(json.dumps(
            api.resolve("TLS 1.1", ctx=CortexContext()).to_dict()
        ))

        assert payload["gaps"][0]["citations"][0]["source_id"] == "rag:1"
        assert payload["gaps"][0]["citation_basis"] == BASIS_EVIDENCE
