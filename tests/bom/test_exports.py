# CUI // SP-CTI
"""The workbook and the deck.

Invented content. ICDEV is a public repo.
"""
from __future__ import annotations

import io

import openpyxl
import pytest
from pptx import Presentation

from tools.bom.deck import build_deck, freeze, render
from tools.bom.export_xlsx import export
from tools.bom.findings import Evidence, Finding
from tools.bom.lines import ExtractedLine
from tools.bom.pivot import Dataset, Row, pivot
from tools.bom.reconcile import Source


def _line(lid, desc, price, doc="bom.xlsx", cell="A1"):
    return ExtractedLine(
        line_id=lid, line_hash=f"h{lid}", source_document=doc, source_sheet="Sheet1",
        source_locator=cell, raw_text=desc, description=desc,
        part_number="SW-9500", manufacturer="Acme",
        qty=1, unit_price=price, extended_price=price,
    )


def _row(lid, price, *, committed=True, **dims):
    return Row(
        line_id=lid, cluster_id=f"c{lid}", description=f"item {lid}",
        dims=dims or {"manufacturer": "Acme", "price_basis": "msrp"},
        extended_price=price, qty=1, unit_price=price, committed=committed,
        excluded_reason="" if committed else "sources disagree",
    )


@pytest.fixture
def scenario():
    ds = Dataset(
        rows=[_row("a", 21000), _row("b", 9999, committed=False)],
        claim_sources={"bom.xlsx"},
    )
    lines = [_line("a", "Core switch", 21000), _line("b", "Firewall", 9999)]
    findings = [
        Finding(
            finding_type="unpriced_line_zeroed", kind="defect", severity="high",
            title="A line computes to zero", detail="An operand was never filled in.",
            impact_usd=None,
            evidence=[Evidence("bom.xlsx", "Sheet1", "E9", "chassis")],
        ),
        Finding(
            finding_type="hardcoded_rollup", kind="defect", severity="high",
            title="A typed number among formulas", impact_usd=192000.0,
            evidence=[Evidence("bom.xlsx", "Summary", "C15", "192000")],
        ),
    ]
    sources = {
        "bom.xlsx": Source("s1", credibility_tier="authoritative"),
        "print.pdf": Source("s2", credibility_tier="derived", role="derived"),
    }
    return ds, lines, findings, sources


class TestTheWorkbook:
    def test_it_opens(self, scenario):
        ds, lines, findings, sources = scenario
        wb = openpyxl.load_workbook(io.BytesIO(export(ds, findings, sources, lines)))
        assert wb.sheetnames == ["The Ask", "Bill of Materials", "Findings", "Sources"]

    def test_every_line_carries_its_provenance(self, scenario):
        """A reconciled BOM is only worth anything if a sceptic can take any number,
        follow it back to the cell it came from, and satisfy themselves it is real."""
        ds, lines, findings, sources = scenario
        wb = openpyxl.load_workbook(io.BytesIO(export(ds, findings, sources, lines)))
        ws = wb["Bill of Materials"]

        header = [c.value for c in ws[5]]
        assert "Source document" in header
        assert "Source cell" in header

        doc_col = header.index("Source document") + 1
        cell_col = header.index("Source cell") + 1
        assert ws.cell(6, doc_col).value == "bom.xlsx"
        assert ws.cell(6, cell_col).value == "Sheet1!A1"

    def test_findings_are_sorted_by_money(self, scenario):
        """The only ordering anybody with a budget has ever used."""
        ds, lines, findings, sources = scenario
        wb = openpyxl.load_workbook(io.BytesIO(export(ds, findings, sources, lines)))
        ws = wb["Findings"]
        impacts = [ws.cell(r, 3).value for r in range(6, 8)]
        assert impacts[0] == 192000.0     # priced first
        assert impacts[1] is None         # the one we refused to price

    def test_the_ask_states_what_is_excluded(self, scenario):
        ds, lines, findings, sources = scenario
        wb = openpyxl.load_workbook(io.BytesIO(export(ds, findings, sources, lines)))
        ws = wb["The Ask"]
        labels = [ws.cell(r, 1).value for r in range(1, 15)]
        blob = " ".join(str(x) for x in labels if x)
        assert "Committed" in blob
        assert "Open" in blob
        assert "NOTHING" in blob


class TestTheSnapshotIsFrozen:
    def test_the_same_evidence_gives_the_same_hash(self, scenario):
        """A figure quoted back at you six weeks later must be traceable to the
        exact state of the evidence that produced it."""
        ds, _lines, findings, _sources = scenario
        assert freeze(ds, findings).sha == freeze(ds, findings).sha

    def test_different_evidence_gives_a_different_hash(self, scenario):
        ds, _lines, findings, _sources = scenario
        moved = Dataset(rows=[_row("a", 22000)], claim_sources={"bom.xlsx"})
        assert freeze(ds, findings).sha != freeze(moved, findings).sha


class TestTheDeck:
    def test_it_renders_real_tables_and_a_talk_track(self, scenario, tmp_path):
        """The customer's own leadership deck had ZERO speaker notes across fifteen
        slides — we flagged it. Shipping a deck with the same hole would be
        embarrassing, and it nearly happened: the builder takes its table as
        bullets={headers, rows, footer}, and passing a bare list rendered the slide
        as the words "No table data.\""""
        ds, lines, findings, sources = scenario
        p = pivot(ds, rows="manufacturer", cols="price_basis")
        slides, _snap = build_deck(ds, findings, sources, pivot=p, project="Lab")

        prs = Presentation(render(slides, theme="investment_deck", title="Lab"))
        tables = sum(
            1 for s in prs.slides for sh in s.shapes if getattr(sh, "has_table", False)
        )
        notes = sum(
            1 for s in prs.slides
            if s.has_notes_slide and s.notes_slide.notes_text_frame.text.strip()
        )
        assert tables >= 2
        assert notes >= len(prs.slides) - 1

    def test_the_last_slide_is_an_explicit_outro(self, scenario):
        """The builder renders the LAST slide as an outro whatever type it claims to
        be. Without an explicit one it quietly eats a real slide — "Who said so" was
        being turned into a thank-you card, and nothing said so."""
        ds, _lines, findings, sources = scenario
        slides, _ = build_deck(ds, findings, sources, project="Lab")
        assert slides[-1]["slide_type"] == "outro"

    def test_every_slide_cites_the_snapshot(self, scenario):
        ds, _lines, findings, sources = scenario
        slides, snap = build_deck(ds, findings, sources, project="Lab")
        for s in slides:
            assert any(snap.sha in c["title"] for c in s["citations"])

    def test_an_unknown_theme_is_refused(self, scenario):
        ds, _lines, findings, sources = scenario
        slides, _ = build_deck(ds, findings, sources, project="Lab")
        with pytest.raises(ValueError, match="unknown theme"):
            render(slides, theme="hot_pink")


class TestTheDeckRefusesToPrintATotalItDoesNotHave:
    def test_competing_claims_replace_the_ask_with_the_truth(self):
        """The room's instinct is to ask for 'the number'. The honest answer is that
        several documents disagree and somebody has to say which one governs."""
        ds = Dataset(
            rows=[_row("a", 21000)],
            claim_sources={"bom_one.xlsx", "bom_two.xlsx"},
        )
        slides, snap = build_deck(ds, [], {}, project="Lab")

        assert snap.is_a_total is False
        ask = slides[1]
        assert "not yet a number" in ask["title"]
        assert any("competing estimates" in b.lower() for b in ask["bullets"])
        # And it names them, so nobody has to guess which documents are fighting.
        assert any("bom_one.xlsx" in b for b in ask["bullets"])

    def test_a_settled_dataset_states_the_figure(self):
        ds = Dataset(rows=[_row("a", 21000)], claim_sources={"bom.xlsx"})
        slides, snap = build_deck(ds, [], {}, project="Lab")
        assert snap.is_a_total is True
        assert slides[1]["title"] == "The ask"
        assert any("21,000" in b for b in slides[1]["bullets"])


class TestWhatYouAlreadyOwn:
    def test_it_is_the_best_news_and_gets_its_own_slide(self):
        """Hardware already in the building costs nothing and is frequently the
        reason a team can start now instead of waiting a year. On a cost-sorted
        table it is the last row — which is a product failure, not a formatting one.
        """
        ds = Dataset(rows=[_row("a", 21000)], claim_sources={"bom.xlsx"})
        slides, _ = build_deck(
            ds, [], {}, project="Lab",
            owned_value=95000,
            owned_note="Nineteen machines, being repurposed rather than replaced.",
        )
        titles = [s["title"] for s in slides]
        assert "What we already own" in titles

    def test_it_is_omitted_when_there_is_nothing_to_say(self):
        ds = Dataset(rows=[_row("a", 21000)], claim_sources={"bom.xlsx"})
        slides, _ = build_deck(ds, [], {}, project="Lab")
        assert "What we already own" not in [s["title"] for s in slides]
