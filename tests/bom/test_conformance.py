# CUI // SP-CTI
"""The BOM held against the agreed design, and against what we said we'd do.

Invented content. ICDEV is a public repo.
"""
from __future__ import annotations

import pytest

from tests.bom import fixtures
from tools.bom.conformance import (
    Line,
    ScopeItem,
    check_coverage,
    check_owned_units,
    check_scope,
    components_from,
)
from tools.bom.extract_grid import extract_grid
from tools.bom.matching import score, token_similarity


def _t(findings, kind):
    return [f for f in findings if f.finding_type == kind]


class TestMatching:
    def test_a_sku_written_two_ways(self):
        """Exact comparison scores ZERO here. Trigrams score about a half.

        This is not a rare case: it is what happens whenever two people type the
        same part number from two different quotes.
        """
        m = score("KVM switch", "KVM over IP", a_part="MPU2-2032DAC-400", b_part="MPU2032DAC")
        assert m.method == "trigram"
        assert m.score > 0.6

    def test_a_summary_matches_the_specification_it_summarises(self):
        """A design label is a summary; a BOM line is the full spec.

        The coverage check has to see through that, or it reports a component as
        unfunded while the line paying for it sits two rows away.

        No single measure gets there, which is the argument for the ladder.
        Symmetric token overlap alone scores this at 0.48, because it punishes the
        BOM for carrying MORE detail — which is exactly backwards. Containment
        helps. The sequence rung carries the rest.
        """
        assert (
            score(
                "Duo Access Control (MFA / Zero Trust)",
                "Duo Access Control, 25-user, 3yr Advantage",
            ).score
            > 0.55
        )

    def test_symmetric_overlap_alone_would_have_missed_it(self):
        """Pinning WHY the ladder exists, so nobody simplifies it back."""
        assert token_similarity(
            "Duo Access Control (MFA / Zero Trust)",
            "Duo Access Control, 25-user, 3yr Advantage",
        ) < 0.55

    def test_a_single_shared_word_is_a_coincidence_not_a_match(self):
        """Otherwise a one-word label scores full containment against everything
        it happens to appear inside."""
        assert token_similarity("Router", "Router blade for chassis, 400G uplink") < 0.4

    def test_same_job_different_product_is_a_choice_not_a_duplicate(self):
        """Two vendors' firewalls may share not one character, and one may cost
        twenty times the other. Noticing they compete for the same slot is often
        the most valuable thing the engine does."""
        m = score(
            "Perimeter firewall", "Next-gen firewall appliance",
            a_part="FPX-2110", b_part="ZQ-4145",
            a_function="firewall", b_function="firewall",
        )
        assert m.method == "function"
        assert "same job" in m.reason


class TestReadingADrawing:
    def test_instances_are_collapsed_and_counted(self, tmp_path):
        """Twelve machines drawn one at a time are ONE component, quantity twelve.

        Keeping them apart makes the coverage check compare a single BOM line
        against twelve separate demands and report eleven shortfalls that do not
        exist.
        """
        p = fixtures.drawio_with_tabs(tmp_path / "arch.drawio")
        comps = components_from(extract_grid(p), "baseline")

        workers = [c for c in comps if "Worker" in c.label]
        assert len(workers) == 1
        assert workers[0].claimed_qty == 12
        assert workers[0].diagram == "Rack Elevation"

    def test_rack_furniture_is_not_a_component(self, tmp_path):
        """A rack elevation is mostly rulers, headers and blank panels.

        Demanding the BOM fund "U11" and "SPARE / FUTURE" produces a register full
        of nonsense — and a register nobody reads protects nothing, so the noise is
        a correctness problem, not a presentation one.
        """
        import xml.etree.ElementTree as ET  # noqa: F401

        xml = """<mxfile><diagram name="Rack"><mxGraphModel><root>
          <mxCell id="0"/><mxCell id="1" parent="0"/>
          <mxCell id="a" value="U11" vertex="1" parent="1"/>
          <mxCell id="b" value="SPARE / FUTURE" vertex="1" parent="1"/>
          <mxCell id="c" value="EQUIPMENT" vertex="1" parent="1"/>
          <mxCell id="d" value="Core Switch CS-9500" vertex="1" parent="1"/>
        </root></mxGraphModel></diagram></mxfile>"""
        p = tmp_path / "rack.drawio"
        p.write_text(xml, encoding="utf-8")

        comps = components_from(extract_grid(p), "baseline")
        assert [c.label for c in comps] == ["Core Switch CS-9500"]

    def test_a_model_number_is_not_split_on_its_hyphen(self, tmp_path):
        """"9200-24T" is one token. Splitting it shreds the only precise part of
        the label into two halves that each look like noise."""
        xml = """<mxfile><diagram name="Net"><mxGraphModel><root>
          <mxCell id="0"/><mxCell id="1" parent="0"/>
          <mxCell id="a" value="Access Switch 9200-24T" vertex="1" parent="1"/>
        </root></mxGraphModel></diagram></mxfile>"""
        p = tmp_path / "n.drawio"
        p.write_text(xml, encoding="utf-8")
        assert components_from(extract_grid(p), "b")[0].model_key == "920024t"


class TestCoverage:
    def test_a_component_nobody_pays_for(self, tmp_path):
        p = fixtures.drawio_with_tabs(tmp_path / "a.drawio")
        comps = components_from(extract_grid(p), "baseline")
        found = check_coverage(comps, [], baseline_label="baseline")

        unfunded = _t(found, "unfunded_component")
        assert unfunded
        # Nothing prices it. That IS the finding — inventing a figure for it would
        # be worse than leaving it blank.
        assert all(f.impact_usd is None for f in unfunded)

    def test_many_unfunded_on_one_drawing_are_reported_once(self, tmp_path):
        """A floor plan calls for seating, tables and rooms; a hardware BOM prices
        none of them because the fit-out is a lump somewhere else. True, and worth
        saying ONCE. Said eighteen times at CRITICAL it buries the switch on the
        rack elevation that nobody costed."""
        p = fixtures.drawio_with_tabs(tmp_path / "a.drawio")
        comps = components_from(extract_grid(p), "baseline")
        # 12 workers collapse to one component, so pad the drawing out.
        for i in range(8):
            comps.append(type(comps[0])(
                label=f"Furnishing {i}", diagram="Floor Plan", baseline="b",
            ))

        found = _t(check_coverage(comps, [], baseline_label="b"), "unfunded_component")
        floor = [f for f in found if f.evidence[0].sheet == "Floor Plan"]
        assert len(floor) == 1
        assert "have no line paying for them" in floor[0].title


class TestOwnedHardwareIsAQuestionNotAVerdict:
    """The single most dangerous thing this engine could get wrong.

    A serial number proves a machine EXISTS. Its ABSENCE proves NOTHING — an
    inventory nobody has walked around in a year is simply out of date. Concluding
    that hardware is fictional means telling a room full of executives, with total
    confidence, that machines their engineers are standing next to do not exist.
    """

    @pytest.fixture
    def gap(self, tmp_path):
        p = fixtures.drawio_with_tabs(tmp_path / "a.drawio")
        comps = components_from(extract_grid(p), "baseline")
        # The drawing claims 12. The inventory can account for 2.
        return check_owned_units(
            comps, {"nx100": 2}, baseline_label="baseline",
        )

    def test_the_disagreement_is_reported(self, gap):
        found = _t(gap, "baseline_asset_gap")
        assert len(found) == 1
        assert found[0].data["claimed_qty"] == 12
        assert found[0].data["verified_qty"] == 2
        assert found[0].data["shortfall"] == 10

    def test_it_is_a_decision_and_not_a_defect(self, gap):
        f = _t(gap, "baseline_asset_gap")[0]
        assert f.kind == "decision"

    def test_it_names_both_possibilities_and_picks_neither(self, gap):
        f = _t(gap, "baseline_asset_gap")[0]
        assert "EITHER the inventory is incomplete" in f.detail
        assert "OR the design leans on" in f.detail
        assert "Somebody has to go and look" in f.detail
        # It must never assert the hardware is fictional.
        assert "do not exist" not in f.detail

    def test_it_refuses_to_price_the_shortfall_it_cannot_price(self, gap):
        f = _t(gap, "baseline_asset_gap")[0]
        assert f.impact_usd is None
        assert "cannot be stated" in f.detail

    def test_it_prices_the_shortfall_when_the_corpus_prices_a_replacement(self, tmp_path):
        p = fixtures.drawio_with_tabs(tmp_path / "a.drawio")
        comps = components_from(extract_grid(p), "baseline")
        found = check_owned_units(
            comps, {"nx100": 2}, baseline_label="b",
            replacement_prices={"nx100": 4000.0},
        )
        f = _t(found, "baseline_asset_gap")[0]
        assert f.impact_usd == 40000.0   # 10 short x 4000


class TestOneDesignManyViews:
    def test_the_same_machines_drawn_twice_are_not_twice_as_many(self, tmp_path):
        """A rack elevation draws twelve machines; the topology says "x12" for the
        SAME twelve. That is twelve machines, not twenty-four.

        Summing across views inflates the claim, and an inflated claim invents a
        shortfall — which would send somebody out to buy hardware they already own.
        A more expensive mistake than saying nothing.
        """
        xml = """<mxfile>
          <diagram name="Rack"><mxGraphModel><root>
            <mxCell id="0"/><mxCell id="1" parent="0"/>
            <mxCell id="r1" value="NX-100 — Worker #1" vertex="1" parent="1"/>
            <mxCell id="r2" value="NX-100 — Worker #2" vertex="1" parent="1"/>
            <mxCell id="r3" value="NX-100 — Worker #3" vertex="1" parent="1"/>
          </root></mxGraphModel></diagram>
          <diagram name="Topology"><mxGraphModel><root>
            <mxCell id="0"/><mxCell id="1" parent="0"/>
            <mxCell id="t1" value="NX-100 x3" vertex="1" parent="1"/>
          </root></mxGraphModel></diagram>
        </mxfile>"""
        p = tmp_path / "two_views.drawio"
        p.write_text(xml, encoding="utf-8")

        comps = components_from(extract_grid(p), "b")
        found = _t(check_owned_units(comps, {"nx100": 1}, baseline_label="b"),
                   "baseline_asset_gap")
        assert len(found) == 1
        assert found[0].data["claimed_qty"] == 3   # not 6


class TestDeclaredScope:
    """You cannot detect the absence of something nobody wrote down."""

    def test_a_workstream_in_no_document_at_all(self):
        item = ScopeItem("sc1", "Simulation Environment",
                         ["network emulation", "cloud emulation"], "Wave 3")
        found = _t(check_scope([item], [], []), "scope_declared_unpriced")

        assert len(found) == 1
        # NULL, not zero. Zero claims the work is free; a guess gets quoted back at
        # somebody in a budget meeting.
        assert found[0].impact_usd is None
        assert "cannot earmark against silence" in found[0].detail

    def test_a_workstream_priced_only_by_a_source_nobody_trusts(self):
        """It reads as covered on a spreadsheet. It is not covered.

        Credibility, conformance and scope all meet here, and it is the kind of gap
        that stays invisible until the money is already committed.
        """
        item = ScopeItem("sc1", "Simulation Environment",
                         ["network emulation software"], "Wave 3")
        lines = [Line(
            line_id="l1",
            description="Network emulation software, enterprise",
            source_document="rough_draft.xlsx",
            extended_price=85000.0,
        )]
        found = check_scope(
            [item], [], lines, weak_sources={"rough_draft.xlsx"},
        )
        weak = _t(found, "scope_priced_only_by_weak_source")
        assert len(weak) == 1
        assert weak[0].impact_usd == 85000.0
        assert "rough_draft.xlsx" in weak[0].detail

    def test_a_workstream_priced_by_a_trusted_source_is_only_undesigned(self):
        item = ScopeItem("sc1", "Simulation Environment", ["network emulation software"])
        lines = [Line(
            line_id="l1",
            description="Network emulation software, enterprise",
            source_document="approved_bom.xlsx",
            extended_price=85000.0,
        )]
        found = check_scope([item], [], lines, weak_sources={"rough_draft.xlsx"})
        assert _t(found, "scope_declared_undesigned")
        assert not _t(found, "scope_priced_only_by_weak_source")
