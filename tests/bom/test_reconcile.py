# CUI // SP-CTI
"""Reconciliation: clustering, winner selection, and the adjudicator's cage.

Invented content. ICDEV is a public repo.
"""
from __future__ import annotations

import pytest

from tools.bom.lines import ExtractedLine
from tools.bom.matching import discriminators, score
from tools.bom.reconcile import (
    Decision,
    Source,
    pair_key,
    reconcile,
    validate_adjudication,
)


def _line(lid, desc, *, part="", unit=None, qty=1, source="a.xlsx", basis="msrp"):
    return ExtractedLine(
        line_id=lid,
        line_hash=f"h-{lid}",
        source_document=source,
        source_sheet="S",
        source_locator="A1",
        raw_text=f"{desc} {part} {unit}",
        description=desc,
        part_number=part,
        qty=qty,
        unit_price=unit,
        price_basis=basis,
    )


class TestAPlaceholderIsNotAPartNumber:
    def test_generic_is_not_a_sku(self):
        """The part column is routinely filled with "Generic" or "Various".

        Comparing those as if they were SKUs merged nine unrelated accessories
        into one cluster with total confidence — and the "identical part number"
        justification read exactly like a correct answer. A real part number
        contains a digit.
        """
        m = score("Cable manager", "Blank panel", a_part="Generic", b_part="Generic")
        assert m.method != "exact_part"
        assert m.score < 0.5

    def test_a_real_sku_still_matches_itself(self):
        m = score("Switch", "Core switch", a_part="CS-9500-16X", b_part="CS-9500-16X")
        assert m.method == "exact_part"
        assert m.score == 1.0


class TestDifferentPartNumbersMeanDifferentProducts:
    def test_a_substituted_digit_is_a_different_model(self):
        """"C9300-24T" and "C9200-24T" are the same length and differ in one
        character. They are two different switches, and no similarity score gets
        to say otherwise."""
        m = score(
            "Catalyst 9300-24T Access Switch",
            "Catalyst 9200-24T Access Switch",
            a_part="C9300-24T-A", b_part="C9200-24T-A",
        )
        assert m.score == 0.0
        assert "substituted digit" in m.reason

    def test_a_truncated_sku_is_the_same_product(self):
        """Different LENGTHS, because one is the other with the suffix dropped —
        which is what happens when two people copy a SKU off two different
        quotes."""
        m = score(
            "KVM over IP switch", "Enterprise KVM switch",
            a_part="MPU2-2032DAC-400", b_part="MPU2032DAC",
        )
        assert m.score > 0.6

    def test_two_specific_and_different_parts_never_fall_through_to_the_description(self):
        """A description is a summary and is allowed to be vague. A part number is
        a COMMITMENT. When both sides made that commitment and they disagree, the
        commitment wins — whatever the prose says."""
        m = score(
            "Cisco 93180YC-FXP switch", "Cisco 9348GC-FXP switch",
            a_part="93180YC-FXP", b_part="9348GC-FXP",
        )
        assert m.score == 0.0
        assert "not the same item" in m.reason


class TestTheMeasurementIsTheProduct:
    @pytest.mark.parametrize(
        ("a", "b"),
        [
            ("OM4 LC-LC Duplex Fibre 3m", "OM4 LC-LC Duplex Fibre 10m"),
            ("Cat6A Patch Cable 3ft", "Cat6A Patch Cable 10ft"),
            ("SFP+ DAC 1m Twinax", "SFP+ DAC 3m Twinax"),
        ],
    )
    def test_two_lengths_are_two_purchases(self, a, b):
        """Ninety percent the same string, and a buyer needs BOTH.

        A similarity score reads the difference as noise — one token out of eight —
        when it is the only token that matters. Merging them prices two purchases as
        one, and the shortfall does not appear until somebody is standing in a data
        centre holding a cable that does not reach.
        """
        assert score(a, b).score == 0.0

    def test_the_same_item_described_twice_still_matches(self):
        assert score(
            "Cat6A Patch Cable 3ft",
            "Patch cable, Cat6A, 3ft",
        ).score > 0.55

    def test_discriminators_ignore_years(self):
        assert "m2026" not in discriminators("Licence renewal 2026")


class TestShortDescriptionsLieToTheSequenceMatcher:
    def test_pdu_is_not_ups(self):
        """They share nine of twelve characters, so the sequence matcher calls them
        0.83 alike — and they are a power distribution unit and an uninterruptible
        power supply, which are not remotely the same purchase.

        On a short string the shared characters are the packaging; the three that
        differ are the entire product.
        """
        m = score("PDU (Rack-Mount)", "UPS (Rack-Mount)")
        assert m.score < 0.70   # below the threshold that would cluster them


class TestTheAdjudicatorsCage:
    """Every gate here is CODE. None of it is a request in a prompt, because a
    prompt is advice and this is not a matter on which advice is enough."""

    @pytest.fixture
    def pair(self):
        return (
            _line("a", "Enterprise KVM switch", part="MPU2-2032DAC-400", unit=6400),
            _line("b", "KVM over IP", part="MPU2032DAC", unit=3500, source="b.xlsx"),
        )

    def test_a_good_verdict_is_accepted(self, pair):
        a, b = pair
        out = validate_adjudication(
            {"relation": "same_item", "confidence": 0.8,
             "reason": "both name a KVM switch from the same range",
             "evidence_spans": ["KVM"]},
            a, b,
        )
        assert out == ("same_item", 0.8, "both name a KVM switch from the same range")

    def test_a_currency_figure_in_the_prose_voids_the_whole_response(self, pair):
        """The model was never SHOWN a price. If one appears in its answer it
        either invented it or inferred it, and both are disqualifying."""
        a, b = pair
        assert validate_adjudication(
            {"relation": "same_item", "confidence": 0.9,
             "reason": "the same switch; the $6,400 listing is the retail one"},
            a, b,
        ) is None

    def test_a_bare_thousands_figure_also_voids_it(self, pair):
        a, b = pair
        assert validate_adjudication(
            {"relation": "same_item", "confidence": 0.9,
             "reason": "identical; one is listed at 6,400 and the other lower"},
            a, b,
        ) is None

    def test_an_invented_evidence_span_is_refused(self, pair):
        """An invented justification is worse than none, because it is persuasive."""
        a, b = pair
        assert validate_adjudication(
            {"relation": "same_item", "confidence": 0.9,
             "reason": "same product",
             "evidence_spans": ["identical serial numbers"]},   # nowhere in either line
            a, b,
        ) is None

    def test_a_verdict_outside_the_vocabulary_is_refused(self, pair):
        a, b = pair
        assert validate_adjudication(
            {"relation": "probably_the_same", "confidence": 0.9, "reason": "x"}, a, b
        ) is None

    def test_a_discarded_verdict_is_not_a_merge(self):
        """When a gate throws the model's answer out, the pair goes to a HUMAN —
        unmerged. Falling back to "well, it probably meant yes" would make every
        gate decorative.

        Note the pair: it has to land in the AMBIGUOUS band (0.45–0.70), because
        that is the only place a model is ever consulted. A strong deterministic
        match never reaches one, and a weak one is discarded without asking.
        """
        a = _line("a", "Managed network switch", unit=5000)
        b = _line("b", "Ethernet switch, managed", unit=5200, source="b.xlsx")

        def bad_model(_prompt):
            return {"relation": "same_item", "confidence": 0.99,
                    "reason": "these both cost about $5,000"}

        r = reconcile([a, b], {}, adjudicator=bad_model)
        assert r.llm_calls == 1

        # Nothing merged on a voided verdict: two clusters of one.
        assert all(len(c.members) == 1 for c in r.clusters)
        assert len(r.clusters) == 2

    def test_the_model_is_never_shown_a_price(self):
        """It is being asked whether two things are the same thing. The money is
        irrelevant to that question — and extremely relevant to the temptation to
        reason backwards from it."""
        a = _line("a", "Managed network switch", unit=5000)
        b = _line("b", "Ethernet switch, managed", unit=200000, source="b.xlsx")

        seen: list[dict] = []

        def spy(prompt):
            seen.append(prompt)
            return None

        reconcile([a, b], {}, adjudicator=spy)

        assert seen
        blob = str(seen[0])
        assert "5000" not in blob and "200000" not in blob
        assert "unit_price" not in blob and "extended" not in blob


class TestRefusingIsAnAnswer:
    def test_a_wide_price_spread_forces_a_human(self):
        """Two products doing the same job at wildly different prices are a CHOICE.

        Averaging them produces a number that is not a compromise but a fiction,
        and it goes into a budget with our name on it.
        """
        a = _line("a", "Perimeter firewall appliance", part="FW-2110", unit=10500)
        b = _line("b", "Perimeter firewall appliance", part="FW-2110",
                  unit=200000, source="b.xlsx")
        r = reconcile([a, b], {})

        cluster = next(c for c in r.clusters if len(c.members) == 2)
        assert cluster.status == "pending_review"
        assert not cluster.committed
        # Contributes ZERO until somebody chooses. Not the cheapest branch, not the
        # mean — both of those would be inventions.
        assert r.committed_total == 0
        assert any(f.finding_type == "price_spread" for f in r.findings)

    def test_two_authoritative_sources_are_never_auto_resolved(self):
        """A real dispute between two things the customer VOUCHED for. Quietly
        picking one would be the worst possible use of the authority they gave us."""
        a = _line("a", "Core switch", part="CS-9500", unit=21000, source="a.xlsx")
        b = _line("b", "Core switch", part="CS-9500", unit=25000, source="b.xlsx")
        sources = {
            "a.xlsx": Source("a", credibility_tier="authoritative"),
            "b.xlsx": Source("b", credibility_tier="authoritative"),
        }
        r = reconcile([a, b], sources)
        assert any(f.finding_type == "authoritative_conflict" for f in r.findings)
        assert r.committed_total == 0

    def test_a_copy_of_a_document_can_never_win(self):
        a = _line("a", "Core switch", part="CS-9500", unit=21000, source="real.xlsx")
        b = _line("b", "Core switch", part="CS-9500", unit=21000, source="print.pdf")
        sources = {
            "real.xlsx": Source("a", credibility_tier="authoritative"),
            "print.pdf": Source("b", credibility_tier="derived", role="derived"),
        }
        r = reconcile([a, b], sources)
        cluster = next(c for c in r.clusters if len(c.members) == 2)
        assert cluster.winner_line_id == "a"

    def test_agreement_does_not_go_to_a_review_queue(self):
        """A queue full of items that require no thought trains people to click
        through it — and then the one that mattered gets clicked through too."""
        a = _line("a", "Core switch", part="CS-9500", unit=21000, source="a.xlsx")
        b = _line("b", "Core switch", part="CS-9500", unit=21000, source="b.xlsx")
        r = reconcile([a, b], {})
        cluster = next(c for c in r.clusters if len(c.members) == 2)
        assert cluster.status == "accepted"
        assert r.committed_total == 21000


class TestCredibilityDecidesTheWinner:
    def test_a_draft_never_overrules_a_source_you_vouched_for(self):
        a = _line("a", "Core switch", part="CS-9500", unit=21000, source="trusted.xlsx")
        b = _line("b", "Core switch", part="CS-9500", unit=19000, source="draft.xlsx")
        sources = {
            "trusted.xlsx": Source("a", credibility_tier="authoritative"),
            "draft.xlsx": Source("b", credibility_tier="draft"),
        }
        r = reconcile([a, b], sources)
        cluster = next(c for c in r.clusters if len(c.members) == 2)
        assert cluster.winner_line_id == "a"
        assert "authoritative" in cluster.rationale


class TestDecisionsOutliveEverything:
    def test_a_pair_key_is_order_independent(self):
        assert pair_key("h1", "h2") == pair_key("h2", "h1")

    def test_a_human_verdict_is_replayed_and_never_re_asked(self):
        """Clusters are recomputed on every run. Key a customer's approvals to them
        and the next upload renumbers everything and silently orphans every decision
        they ever made — the classic entity-resolution re-run bug, which destroys
        weeks of work without raising a single error.
        """
        a = _line("a", "KVM switch", part="MPU2-2032DAC-400", unit=6400)
        b = _line("b", "KVM over IP", part="MPU2032DAC", unit=3500, source="b.xlsx")

        approved = Decision(
            pair_key=pair_key(a.line_hash, b.line_hash),
            a_line_hash=a.line_hash, b_line_hash=b.line_hash,
            verdict="same_item", confidence=1.0, decided_by="human",
            reason="checked the invoice; same unit",
        )

        calls = []

        def model(prompt):
            calls.append(prompt)
            return {"relation": "different", "confidence": 0.9, "reason": "x"}

        r = reconcile([a, b], {}, decisions=[approved], adjudicator=model)

        # Never re-asked. Their answer does not quietly change between runs.
        assert calls == []
        assert r.llm_calls == 0
        assert any(len(c.members) == 2 for c in r.clusters)
