# CUI // SP-CTI
"""Cross-backend semantic entity resolution (cef-rsv-02).

The card's acceptance criteria are FOUR OUTCOMES that must stay distinguishable,
and the reason they need four separate tests rather than one is that three of
them used to render identically — an empty ``conflicts``, an empty ``gaps`` and
a dead fan-out all produced the same clean-looking resolution:

``agreement``     two backends claim the same thing. No conflict, and the
                  entity reads as ANSWERED.
``conflict``      two backends claim incompatible things. Both sides are
                  returned WITH their provenance, and nothing picks between
                  them — asserted structurally, by checking the shape carries no
                  winner field at all, not just that this one case did not set
                  one.
``gap``           nothing answered for the entity. A visible finding, and
                  distinguishable from "answered: current".
``dead backend``  retrieval DIED. A ``backend_error`` and an ``unresolved``
                  record — never a gap, because a gap is a statement about the
                  corpus and an outage is not.

``TestNoSilentWinner`` is the one that matters most: the temptation in every
conflict detector is to resolve the conflict, and the whole value of this one is
that it refuses to.
"""
from __future__ import annotations

import importlib

import pytest

from tools.cortex import entity_resolution as er
from tools.cortex import resolver
from tools.cortex.schemas import (
    RESOLVE_VERDICTS,
    Citation,
    CortexSearchResult,
    EntityAssessment,
    EntityClaim,
    EntityConflict,
)


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------
def currency_hit(
    *,
    label="TLS 1.1",
    verdict="deprecated",
    source="curated_catalog",
    source_id="ec-1",
    superseded_by="",
    eol_date="",
    authoritative=True,
    others=(),
    version="",
):
    """A ``currency`` backend hit shaped exactly as cef-bck-01 emits one."""
    content = f"{label} — {verdict} per {source}."
    return CortexSearchResult(
        content=content,
        score=0.9,
        backend="currency",
        strategy="assertion",
        citation=Citation(
            source_id=source_id,
            source_type="currency_assertion",
            source_table="entity_currency",
            title=label,
            snippet=content,
        ),
        metadata={
            "lane": "assertion",
            "entity_label": label,
            "entity_key": label.casefold(),
            "entity_type": "protocol",
            "entity_version": version,
            "verdict": verdict,
            "superseded_by": superseded_by,
            "eol_date": eol_date,
            "source": source,
            "authoritative": authoritative,
            "as_of": "2026-01-01",
            "confidence": 0.9,
            "conflict": bool(others),
            "others": list(others),
        },
    )


def doc_hit(content, *, backend="rag", source_id="rag:1", title="SOP-12"):
    """A prose hit — the shape rag/dic/kb return, with no typed currency field."""
    return CortexSearchResult(
        content=content,
        score=0.7,
        backend=backend,
        strategy="hybrid",
        citation=Citation(
            source_id=source_id,
            source_type=f"{backend}_chunk",
            source_table=f"{backend}_chunks",
            title=title,
            snippet=content[:80],
        ),
        metadata={},
    )


def assessment(label="TLS 1.1", verdict="deprecated", pack_id="crypto_protocols",
               superseded_by="", confidence=1.0):
    return EntityAssessment(
        entity=label,
        entity_type="protocol",
        pack_id=pack_id,
        verdict=verdict,
        pack_verdict=verdict,
        confidence=confidence,
        rationale="Deprecated by RFC 8996.",
        superseded_by=superseded_by,
        evidence=[{"source": "rule:tls-11", "detail": "", "date": ""}],
    )


def report(hits=(), **kwargs):
    kwargs.setdefault("entities", ["TLS 1.1"])
    kwargs.setdefault("backends", ["currency", "rag"])
    return er.resolve_entities(list(hits), **kwargs)


# ---------------------------------------------------------------------------
# OUTCOME 1 — agreement
# ---------------------------------------------------------------------------
class TestAgreement:
    def test_two_backends_saying_the_same_thing_produce_no_conflict(self):
        out = report([
            currency_hit(verdict="deprecated"),
            doc_hit("TLS 1.1 is deprecated for all new systems."),
        ])

        assert out["conflicts"] == []
        assert out["gaps"] == []
        assert out["unresolved"] == []
        assert [c["backend"] for c in out["claims"]] == ["currency", "rag"]
        assert {c["status"] for c in out["claims"]} == {"deprecated"}

    def test_an_agreed_entity_reads_as_answered(self):
        out = report([currency_hit(verdict="current")])

        row = out["entities"][0]
        assert row["answered"] is True
        assert row["statuses"] == ["current"]

    def test_deprecated_and_superseded_are_the_same_finding_not_a_conflict(self):
        """A pack saying ``deprecated`` and a catalog saying ``superseded``.

        Superseded IS deprecated plus a named successor — ``map_pack_verdict``
        already promotes one to the other on exactly that basis. Reporting it
        would put a permanent false conflict on every properly-resolved entity
        and bury the real ones.
        """
        out = report(
            [currency_hit(verdict="superseded", superseded_by="TLS 1.3")],
            assessments=[assessment(verdict="deprecated")],
        )

        assert out["conflicts"] == []

    def test_a_source_with_no_opinion_cannot_contradict_one_with_an_opinion(self):
        out = report([
            currency_hit(verdict="deprecated"),
            currency_hit(verdict="unknown", source="thin_feed", source_id="ec-2"),
        ])

        assert out["conflicts"] == []

    def test_one_source_seen_through_two_backends_is_one_claim(self):
        """``fusion_ident`` — the SAME predicate RRF fusion uses.

        A document retrieved by rag and by dic is one source. Counted twice it
        would corroborate itself against the catalog, and a conflict's apparent
        weight of evidence would depend on how many rungs happened to index it.
        """
        prose = "TLS 1.1 is deprecated for all new systems."
        out = report(
            [doc_hit(prose, backend="rag", source_id="doc-7"),
             doc_hit(prose, backend="dic", source_id="doc-7")],
            backends=["rag", "dic"],
        )

        assert len(out["claims"]) == 1


# ---------------------------------------------------------------------------
# OUTCOME 2 — conflict
# ---------------------------------------------------------------------------
class TestConflict:
    def test_rag_and_the_curated_catalog_disagreeing_produces_a_conflict(self):
        """The motivating case. Two backends, incompatible claims, one entity."""
        out = report([
            currency_hit(verdict="deprecated", source="curated_catalog"),
            doc_hit("TLS 1.1 remains approved for legacy interconnects."),
        ])

        assert len(out["conflicts"]) == 1
        conflict = out["conflicts"][0]
        assert conflict["kind"] == "status"
        assert conflict["values"] == ["current", "deprecated"]
        assert conflict["cross_backend"] is True
        assert sorted(conflict["backends"]) == ["currency", "rag"]

    def test_both_sides_carry_their_own_provenance(self):
        out = report([
            currency_hit(verdict="deprecated", source="curated_catalog",
                         source_id="ec-42"),
            doc_hit("TLS 1.1 remains approved for legacy interconnects.",
                    source_id="rag:99"),
        ])

        sides = {s["backend"]: s for s in out["conflicts"][0]["sides"]}
        assert sides["currency"]["source"] == "curated_catalog"
        assert sides["currency"]["source_id"] == "ec-42"
        assert sides["currency"]["source_table"] == "entity_currency"
        assert sides["currency"]["as_of"] == "2026-01-01"
        assert sides["currency"]["authoritative"] is True
        assert sides["currency"]["extraction"] == "structured"

        assert sides["rag"]["source_id"] == "rag:99"
        assert sides["rag"]["source_table"] == "rag_chunks"
        assert sides["rag"]["extraction"] == "text_pattern"
        assert "remains approved" in sides["rag"]["snippet"]

    def test_two_different_successors_are_a_conflict_of_their_own_kind(self):
        """Agreeing an entity is superseded and disagreeing about BY WHAT.

        A separate ``kind`` because the two findings drive different work: the
        status drives whether to migrate, the successor drives to what.
        """
        out = report([
            currency_hit(verdict="superseded", superseded_by="TLS 1.3"),
            doc_hit("TLS 1.1 is superseded by TLS 1.2 in this baseline."),
        ])

        kinds = {c["kind"]: c for c in out["conflicts"]}
        assert "superseded_by" in kinds
        assert kinds["superseded_by"]["values"] == ["tls 1.2", "tls 1.3"]

    def test_two_different_eol_dates_are_a_conflict(self):
        out = report(
            [currency_hit(verdict="deprecated", eol_date="2027-03-31"),
             doc_hit("Catalyst 6500 end-of-support is 2029-01-31 per the bulletin.")],
            entities=["TLS 1.1", "Catalyst 6500"],
        )
        # Two entities, and the DATE conflict must attach to the one the sources
        # were talking about — not to whichever entity was asked about.
        out2 = report([
            currency_hit(label="Catalyst 6500", verdict="deprecated",
                         eol_date="2027-03-31"),
            doc_hit("Catalyst 6500 end-of-support is 2029-01-31 per the bulletin."),
        ], entities=["Catalyst 6500"])

        assert all(c["kind"] != "eol_date" for c in out["conflicts"])
        eol = [c for c in out2["conflicts"] if c["kind"] == "eol_date"]
        assert len(eol) == 1
        assert eol[0]["values"] == ["2027-03-31", "2029-01-31"]

    def test_three_disagreeing_sources_are_one_conflict_with_three_sides(self):
        out = report([
            currency_hit(verdict="deprecated", source="curated_catalog"),
            currency_hit(verdict="current", source="eol_feed", source_id="ec-2"),
            doc_hit("TLS 1.1 is obsolete and prohibited."),
        ])

        status = [c for c in out["conflicts"] if c["kind"] == "status"]
        assert len(status) == 1
        assert len(status[0]["sides"]) == 3

    def test_two_packs_disagreeing_is_no_longer_silent(self):
        """``reduce_assessments`` picks the higher-ranked verdict and says
        nothing about the loser. Promoting each assessment to a claim is what
        makes that reduction auditable."""
        out = report(
            [],
            assessments=[assessment(verdict="deprecated", pack_id="crypto_protocols"),
                         assessment(verdict="current", pack_id="network_hw")],
        )

        assert len(out["conflicts"]) == 1
        assert sorted(out["conflicts"][0]["backends"]) == [
            "pack:crypto_protocols", "pack:network_hw",
        ]

    def test_the_store_s_own_disagreeing_sources_become_first_class_sides(self):
        """``entity_currency`` already preserved disagreement under ``others``
        and Cortex already carried it as a boolean nothing acted on."""
        out = report([currency_hit(
            verdict="deprecated",
            source="curated_catalog",
            others=[{"source": "vendor_feed", "verdict": "current",
                     "confidence": 0.4, "as_of": "2025-06-01"}],
        )])

        conflict = out["conflicts"][0]
        assert sorted(s["source"] for s in conflict["sides"]) == [
            "curated_catalog", "vendor_feed",
        ]
        # One backend, two sources: a real finding, reported the same way and
        # not filtered out for failing to span rungs.
        assert conflict["cross_backend"] is False

    def test_a_losing_source_is_not_given_the_winning_row_s_id(self):
        out = report([currency_hit(
            source_id="ec-winner",
            others=[{"source": "vendor_feed", "verdict": "current"}],
        )])

        loser = [s for s in out["conflicts"][0]["sides"]
                 if s["source"] == "vendor_feed"][0]
        assert loser["source_id"] == ""
        assert loser["source_table"] == "entity_currency"


# ---------------------------------------------------------------------------
# The guarantee: nothing resolves the disagreement
# ---------------------------------------------------------------------------
class TestNoSilentWinner:
    #: Every way a "winner" could sneak onto the shape. Checked against the
    #: dataclass rather than one serialized instance: a field that is merely
    #: unset in this test's data would pass an instance check and ship.
    _BANNED = (
        "winner", "winning_side", "resolved", "resolved_value", "resolution",
        "chosen", "selected", "verdict", "consensus", "merged", "average",
        "mean", "score", "rank", "preferred", "authority",
    )

    def test_the_conflict_shape_has_no_field_that_could_hold_a_winner(self):
        fields = set(EntityConflict().to_dict())
        assert fields & set(self._BANNED) == set()

    def test_no_side_is_dropped_when_one_is_authoritative(self):
        """Authority is real and it is recorded ON the sides. It is not applied.

        ``entity_currency`` resolves authority at READ time to answer "what is
        the best available answer"; that is a different question from "do my
        sources agree", and answering the second one with the first deletes the
        finding.
        """
        out = report([
            currency_hit(verdict="deprecated", source="curated_catalog",
                         authoritative=True),
            currency_hit(verdict="current", source="scraped_feed",
                         source_id="ec-2", authoritative=False),
        ])

        sides = out["conflicts"][0]["sides"]
        assert len(sides) == 2
        assert {s["authoritative"] for s in sides} == {True, False}

    def test_a_conflict_does_not_change_the_verdict(self, resolution):
        """The verdict comes from the packs. A disagreement between two
        EVIDENCE sources is not a vote, and must not become one."""
        result = resolution(
            packs=[assessment(verdict="deprecated")],
            hits=[currency_hit(verdict="current", source="scraped_feed"),
                  doc_hit("TLS 1.1 remains approved for legacy interconnects.")],
        )

        assert result.verdict == "deprecated"
        assert result.verdict_source == "pack_evaluate"
        assert result.conflicts

    def test_the_prose_names_both_sides_and_says_no_winner_was_picked(self):
        line = resolver._conflict_line({
            "kind": "status",
            "entity_label": "TLS 1.1",
            "sides": [
                {"source": "curated_catalog", "status": "deprecated",
                 "backend": "currency"},
                {"source": "", "status": "current", "backend": "rag"},
            ],
        })

        assert "curated_catalog says deprecated" in line
        assert "rag says current" in line
        assert "no winner is picked" in line.casefold()


# ---------------------------------------------------------------------------
# OUTCOME 3 — gap
# ---------------------------------------------------------------------------
class TestGap:
    def test_an_entity_no_backend_answered_for_produces_a_gap(self):
        out = report([], entities=["Nortel Passport 8600"])

        assert out["gaps"] == [{
            "entity": "Nortel Passport 8600",
            "entity_key": "nortel passport 8600",
            "reasons": [er.GAP_NO_EVIDENCE],
            "backends_consulted": ["currency", "rag"],
            "backends_failed": [],
        }]
        assert out["unresolved"] == []

    def test_a_gap_is_distinguishable_from_answered_current(self):
        """The card's own wording: silence and a clean bill of health must stop
        looking identical."""
        answered = report([currency_hit(verdict="current")])
        silent = report([], entities=["Nortel Passport 8600"])

        assert answered["gaps"] == []
        assert answered["entities"][0]["answered"] is True
        assert silent["gaps"] and silent["entities"][0]["answered"] is False

    def test_mentioned_without_a_claim_is_no_claim_not_no_evidence(self):
        """Different zeroes, different fixes: ``no_evidence`` is an ingestion
        problem, ``no_claim`` is a corpus-content one."""
        out = report([doc_hit("TLS 1.1 appears in the SOP-12 configuration table.")])

        assert out["gaps"][0]["reasons"] == [er.GAP_NO_CLAIM]

    def test_a_claim_that_asserts_nothing_is_not_an_answer(self):
        """A row exists and its verdict is ``unknown`` with no date and no
        successor. Counting that as answered is how "nobody knows" started
        rendering the same as "current"."""
        out = report([currency_hit(verdict="unknown", eol_date="")])

        assert out["entities"][0]["answered"] is False
        assert out["gaps"][0]["reasons"] == [er.GAP_NO_CLAIM]

    def test_an_entity_only_the_evidence_knew_about_is_still_resolved(self):
        """The caller asked about one entity; a currency row named another."""
        out = report(
            [currency_hit(label="Catalyst 6500", verdict="deprecated")],
            entities=["TLS 1.1"],
        )

        keys = {row["entity_key"] for row in out["entities"]}
        assert keys == {"tls 1.1", "catalyst 6500"}
        assert [g["entity"] for g in out["gaps"]] == ["TLS 1.1"]


# ---------------------------------------------------------------------------
# OUTCOME 4 — a backend that DIED
# ---------------------------------------------------------------------------
class TestDeadBackend:
    ERRORS = [{"backend": "rag", "stage": "timeout", "message": "timed out"},
              {"backend": "currency", "stage": "store", "message": "no such table"}]

    def test_a_dead_fan_out_produces_a_backend_error_not_a_gap(self):
        out = report([], backend_errors=self.ERRORS)

        assert out["gaps"] == []
        assert out["unresolved"] == [{
            "entity": "TLS 1.1",
            "entity_key": "tls 1.1",
            "reason": er.GAP_BACKENDS_FAILED,
            "backends_consulted": ["currency", "rag"],
            "backends_failed": ["currency", "rag"],
        }]

    def test_a_dead_backend_is_not_an_empty_corpus(self):
        """The two are byte-identical at the hit list and must not be at the
        report. This is the pair the whole distinction rests on."""
        died = report([], backend_errors=self.ERRORS)
        empty = report([], backend_errors=[])

        assert died["gaps"] == [] and died["unresolved"]
        assert empty["gaps"] and empty["unresolved"] == []

    def test_a_whole_fan_out_that_raised_counts_every_backend_as_failed(self):
        out = report([], backend_errors=[
            {"backend": "search", "stage": "fanout", "message": "pool exploded"},
        ])

        assert out["gaps"] == []
        assert out["unresolved"][0]["backends_failed"] == ["currency", "rag"]

    def test_a_partial_outage_still_reports_a_real_gap(self):
        """One rung died and another answered. Something DID look, and it did
        not cover this entity — so the gap is real, and the outage rides on the
        gap's own field rather than being smuggled into its reasons."""
        out = report([], backend_errors=[self.ERRORS[0]])

        assert out["unresolved"] == []
        assert out["gaps"][0]["reasons"] == [er.GAP_NO_EVIDENCE]
        assert out["gaps"][0]["backends_failed"] == ["rag"]

    def test_a_pack_that_raised_is_not_a_dead_retrieval_rung(self):
        """A pack error arrives on the same list as a backend error and must
        not suppress a corpus gap — a pack is not a retrieval rung."""
        out = report([], backend_errors=[
            {"backend": "pack:broken", "stage": "evaluate", "message": "boom"},
        ])

        assert out["unresolved"] == []
        assert out["gaps"][0]["reasons"] == [er.GAP_NO_EVIDENCE]
        assert out["gaps"][0]["backends_failed"] == []


# ---------------------------------------------------------------------------
# Identity + vocabulary
# ---------------------------------------------------------------------------
class TestIdentity:
    def test_the_join_key_is_the_currency_store_s_own_normalizer(self):
        store = importlib.import_module("tools.currency.entity_currency")

        assert er.entity_ident("  TLS   1.1 ") == store.normalize_key("TLS 1.1")

    def test_two_spellings_of_one_entity_join(self):
        assert er.entity_ident("TLS 1.1") == er.entity_ident("tls  1.1")

    def test_two_versions_of_one_product_do_not_join(self):
        assert er.entity_ident("TLS 1.1") != er.entity_ident("TLS 1.2")
        assert er.entity_ident("Catalyst 6500", "12.2") != er.entity_ident(
            "Catalyst 6500", "15.1"
        )

    def test_a_version_already_in_the_label_is_not_appended_twice(self):
        assert er.entity_ident("TLS 1.1", "1.1") == er.entity_ident("TLS 1.1")

    def test_fusion_identity_is_one_function_not_two(self):
        from tools.cortex import search_service

        assert er.fusion_ident is search_service.fusion_ident
        assert search_service._fusion_ident is search_service.fusion_ident


class TestStatusVocabulary:
    @pytest.mark.parametrize("raw,expected", [
        ("current", "current"), ("Approved", "current"),
        ("eol", "deprecated"), ("retired", "deprecated"),
        ("EOL", "deprecated"), ("end-of-life", "deprecated"),
        ("superseded", "superseded"),
        ("divergent", "unknown"), ("", "unknown"),
    ])
    def test_known_words_map(self, raw, expected):
        assert er.normalize_status(raw) == expected

    def test_an_unknown_word_is_unknown_never_guessed(self):
        assert er.normalize_status("mostly fine") == "unknown"

    def test_every_status_is_in_the_declared_resolve_vocabulary(self):
        assert set(er.STATUS_ALIASES.values()) <= set(RESOLVE_VERDICTS)

    def test_the_two_maps_cannot_drift_into_contradicting_each_other(self):
        """``PACK_VERDICT_MAP`` and ``STATUS_ALIASES`` translate overlapping
        vocabularies. This one is WIDER on purpose — it has to read an EOL
        feed's and an English sentence's words too — but where both know a word
        they must agree, or one entity's pack verdict and the same entity's
        claim would be different statuses and conflict with themselves."""
        for word, mapped in resolver.PACK_VERDICT_MAP.items():
            assert word in er.STATUS_ALIASES, word
            assert er.STATUS_ALIASES[word] == mapped, word

    @pytest.mark.parametrize("a,b,expect", [
        ("current", "deprecated", True),
        ("current", "superseded", True),
        ("deprecated", "superseded", False),
        ("unknown", "deprecated", False),
        ("current", "current", False),
    ])
    def test_incompatibility_is_symmetric_and_explicit(self, a, b, expect):
        assert er.statuses_conflict(a, b) is expect
        assert er.statuses_conflict(b, a) is expect


# ---------------------------------------------------------------------------
# The text lane — anchored, directional, disableable
# ---------------------------------------------------------------------------
class TestTextLane:
    def test_a_document_can_disagree_with_the_catalog_at_all(self):
        """No RAG/DIC/KB hit carries a typed currency field, so without this
        lane the card's motivating case cannot be detected."""
        claims = er.text_claims("TLS 1.1 remains approved for now.", "TLS 1.1")

        assert [c["status"] for c in claims] == ["current"]

    def test_the_direction_of_a_supersession_sentence_is_read(self):
        """"TLS 1.2 supersedes TLS 1.1" says nothing about TLS 1.2's currency.
        An unanchored keyword scan reads it as evidence that TLS 1.2 is
        superseded and then fabricates a conflict against the catalog."""
        sentence = "TLS 1.2 supersedes TLS 1.1 in every profile."

        assert er.text_claims(sentence, "TLS 1.2") == []
        older = er.text_claims(sentence, "TLS 1.1")
        assert older[0]["status"] == "superseded"
        assert older[0]["superseded_by"] == "TLS 1.2"

    def test_a_negation_is_not_a_deprecation(self):
        assert er.text_claims("TLS 1.1 is not deprecated here.", "TLS 1.1") == []

    def test_a_dotted_successor_survives_the_capture(self):
        """``[^.]`` truncates "TLS 1.3" to "TLS 1", which then reads as a
        DIFFERENT successor from the catalog's and invents a conflict out of two
        sources that agreed."""
        claims = er.text_claims("TLS 1.1 is superseded by TLS 1.3 per RFC 8996.",
                                "TLS 1.1")

        assert claims[0]["superseded_by"] == "TLS 1.3"

    def test_the_lane_never_invents_an_entity(self):
        """It only ever claims about entities already in the resolved set."""
        out = report(
            [doc_hit("SSLv3 is deprecated and prohibited.")],
            entities=["TLS 1.1"],
        )

        assert {row["entity_key"] for row in out["entities"]} == {"tls 1.1"}
        assert out["claims"] == []

    def test_the_lane_is_disableable_and_the_toggle_reaches_the_result(self):
        hits = [currency_hit(verdict="deprecated"),
                doc_hit("TLS 1.1 remains approved for legacy interconnects.")]

        on = report(hits, config={"resolve": {"text_claims": True}})
        off = report(hits, config={"resolve": {"text_claims": False}})

        assert on["conflicts"] and on["text_claims"] is True
        assert off["conflicts"] == [] and off["text_claims"] is False
        # Off does not mean blind: the structured claim is still there, and the
        # entity still reads as answered rather than as a gap.
        assert len(off["claims"]) == 1 and off["gaps"] == []

    def test_absent_config_leaves_the_lane_on(self):
        assert er.text_claims_enabled(None) is True
        assert er.text_claims_enabled({}) is True

    def test_every_text_claim_is_stamped_so_a_reader_can_discount_it(self):
        out = report([doc_hit("TLS 1.1 remains approved for legacy interconnects.")])

        assert {c["extraction"] for c in out["claims"]} == {"text_pattern"}


# ---------------------------------------------------------------------------
# Determinism — the same evidence must always give the same report
# ---------------------------------------------------------------------------
class TestDeterminism:
    def test_hit_order_does_not_change_the_conflict(self):
        hits = [currency_hit(verdict="deprecated"),
                doc_hit("TLS 1.1 remains approved for legacy interconnects.")]

        forward = report(list(hits))
        backward = report(list(reversed(hits)))

        assert forward["conflicts"] == backward["conflicts"]
        assert forward["gaps"] == backward["gaps"]

    def test_the_report_is_json_safe(self):
        import json

        out = report([currency_hit(),
                      doc_hit("TLS 1.1 remains approved for legacy interconnects.")])

        assert json.loads(json.dumps(out))["conflicts"]


class TestClaimDedupe:
    def test_backends_are_unioned_rather_than_the_claim_duplicated(self):
        claim = EntityClaim(entity_key="tls 1.1", source_id="doc-7",
                            status="deprecated", backend="rag", backends=["rag"])
        twin = EntityClaim(entity_key="tls 1.1", source_id="doc-7",
                           status="deprecated", backend="dic", backends=["dic"])

        merged = er.dedupe_claims([claim, twin])

        assert len(merged) == 1
        assert merged[0].backends == ["rag", "dic"]

    def test_the_same_source_disagreeing_with_itself_is_kept_apart(self):
        """Same source id, different claimed status — that is two claims, and
        merging them would silently drop one side of a real conflict."""
        merged = er.dedupe_claims([
            EntityClaim(entity_key="tls 1.1", source_id="doc-7", status="current"),
            EntityClaim(entity_key="tls 1.1", source_id="doc-7", status="deprecated"),
        ])

        assert len(merged) == 2


# ---------------------------------------------------------------------------
# End to end, through resolve()
# ---------------------------------------------------------------------------
@pytest.fixture
def resolution(monkeypatch):
    """Drive ``resolver.resolve`` with a scripted pack set and fan-out.

    The RAW implementation, not the governed facade: the governance chain is
    cef-rsv-01's contract and is covered by ``test_resolve_facade``. What is
    under test here is what the resolution CARRIES.
    """

    class _Conn:
        def rollback(self):
            pass

        def close(self):
            pass

    def _run(packs=(), hits=(), errors=(), entity="TLS 1.1"):
        assessments = list(packs)
        monkeypatch.setattr(resolver, "_evidence_connection", lambda: _Conn())
        monkeypatch.setattr(
            resolver, "assess", lambda _entity: (assessments, [], [])
        )
        monkeypatch.setattr(
            resolver, "_search_impl",
            lambda query, **kw: __import__(
                "tools.cortex.search_service", fromlist=["BackendResults"]
            ).BackendResults(list(hits), errors=list(errors)),
        )
        return resolver.resolve(entity)

    return _run


class TestResolveCarriesTheFinding:
    def test_conflicts_reach_the_resolution(self, resolution):
        result = resolution(
            packs=[assessment(verdict="deprecated")],
            hits=[doc_hit("TLS 1.1 remains approved for legacy interconnects.")],
        )

        assert len(result.conflicts) == 1
        assert result.conflicts[0]["kind"] == "status"
        assert "no winner is picked" in result.text.casefold()

    def test_agreement_leaves_conflicts_empty_and_that_now_means_something(
        self, resolution
    ):
        result = resolution(
            packs=[assessment(verdict="deprecated")],
            hits=[doc_hit("TLS 1.1 is deprecated for all new systems.")],
        )

        assert result.conflicts == []
        assert result.metadata["entity_resolution"]["claims"]

    def test_a_second_entity_the_evidence_named_gets_its_own_gap(self, resolution):
        result = resolution(
            packs=[assessment(verdict="deprecated")],
            hits=[currency_hit(label="Catalyst 6500", verdict="unknown")],
        )

        assert [g["entity"] for g in result.gaps] == ["Catalyst 6500"]
        assert result.gaps[0]["reasons"] == [er.GAP_NO_CLAIM]

    def test_the_subject_s_gap_is_reported_once_by_the_owning_layer(self, resolution):
        """``_gaps`` answers "why is the verdict unknown"; entity resolution
        answers "did anything answer". Both would otherwise file a finding about
        the same entity, with different reasons, in one list."""
        result = resolution(packs=[], hits=[])

        assert [g["entity"] for g in result.gaps] == ["TLS 1.1"]
        assert resolver.GAP_NO_PACK in result.gaps[0]["reasons"]

    def test_a_dead_backend_reaches_the_resolution_as_an_error(self, resolution):
        result = resolution(
            packs=[assessment(verdict="deprecated")],
            hits=[],
            errors=[{"backend": "rag", "stage": "timeout", "message": "timed out"}],
        )

        assert result.backend_errors[0]["stage"] == "timeout"
        unresolved = result.metadata["entity_resolution"]["unresolved"]
        assert all(u["reason"] == er.GAP_BACKENDS_FAILED for u in unresolved)

    def test_the_resolution_still_serializes(self, resolution):
        result = resolution(
            packs=[assessment(verdict="deprecated")],
            hits=[doc_hit("TLS 1.1 remains approved for legacy interconnects.")],
        )
        body = result.to_dict()

        assert body["conflicts"][0]["sides"]
        assert body["verdict"] == "deprecated"


class TestBothNamespaces:
    @pytest.mark.parametrize("namespace", ["tools", "icdev.tools"])
    def test_the_module_exists_and_agrees_in_both_trees(self, namespace):
        module = importlib.import_module(f"{namespace}.cortex.entity_resolution")

        assert module.STATUS_ALIASES == er.STATUS_ALIASES
        assert module.entity_ident("TLS 1.1") == "tls 1.1"
