# CUI // SP-CTI
"""Cell-grid extraction.

Run against documents built by tests/bom/fixtures.py, which reproduce the SHAPES
of the failures real documents have without borrowing anyone's content. A formula
multiplying a quantity by an empty cell yields zero whether the item is a network
switch or a marine gearbox; an extractor that catches it here catches it in files
nobody on this project has ever seen.
"""
from __future__ import annotations

import pytest

from tests.bom import fixtures
from tools.bom.extract_grid import _reconstruct_grid, _to_num, extract_grid


class TestNumberParsing:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            (1234, 1234.0),
            (1234.5, 1234.5),
            ("$155,000", 155000.0),
            ("$ 1,234.00", 1234.0),
            ("(1,200)", -1200.0),         # accountants bracket their negatives
            ("", None),
            (None, None),
            ("see below", None),
            ("TBD", None),
            ("$150,000–$250,000", None),  # a RANGE is not a number. Choosing an
                                          # end of it would invent a budget.
            (True, None),                 # bool is an int in Python. A checkbox
                                          # is not a quantity.
        ],
    )
    def test_parse(self, raw, expected):
        assert _to_num(raw) == expected


class TestGridReconstruction:
    """Tables that are not tables.

    A grid of loose text boxes has has_table=False on every shape, so a
    table-aware extractor reads the money slide as scattered words and loses the
    total without saying anything.
    """

    def test_aligned_text_boxes_become_a_grid(self):
        # (left, top, width, height, text)
        shapes = [
            (100, 100, 400, 50, "Category"), (600, 100, 300, 50, "Amount"),
            (100, 200, 400, 50, "Alpha"),    (600, 202, 300, 50, "$1,000"),
            (100, 300, 400, 50, "Beta"),     (600, 299, 300, 50, "$2,500"),
        ]
        grid = _reconstruct_grid(shapes)
        assert (1, 1, "Category") in grid
        assert (2, 2, "$1,000") in grid
        # Rows 2 and 3 are a couple of units out of true, as real decks always
        # are. A clusterer demanding exactness would be useless.
        assert (3, 2, "$2,500") in grid

    def test_a_bulleted_list_is_not_promoted_to_a_table(self):
        """One column is a list. Inventing a second would fabricate structure."""
        shapes = [(100, 100 * i, 400, 50, f"item {i}") for i in range(1, 5)]
        assert _reconstruct_grid(shapes) == []

    def test_too_few_shapes_is_not_a_table(self):
        assert _reconstruct_grid([(0, 0, 10, 10, "x")]) == []


class TestDegradesRatherThanExplodes:
    """One unreadable file in a corpus of twenty degrades that file, not the run."""

    def test_missing_file_warns(self, tmp_path):
        g = extract_grid(tmp_path / "nope.xlsx")
        assert g.cells == []
        assert any("not found" in w for w in g.warnings)

    def test_unsupported_format_says_why(self, tmp_path):
        p = tmp_path / "notes.txt"
        p.write_text("just prose", encoding="utf-8")
        g = extract_grid(p)
        assert g.cells == []
        assert any("no grid extractor" in w for w in g.warnings)

    def test_corrupt_file_warns(self, tmp_path):
        p = tmp_path / "broken.xlsx"
        p.write_bytes(b"this is not a workbook")
        g = extract_grid(p)
        assert g.cells == []
        assert g.warnings


class TestWorkbooks:
    def test_formulas_survive_the_double_load(self, tmp_path):
        """openpyxl returns values OR formulas, never both. So we load twice.

        Without both halves, every downstream detector that needs to know which
        SUM() consumes which cell is dead on arrival.
        """
        p = fixtures.workbook_with_a_zeroed_line(tmp_path / "bom.xlsx")
        g = extract_grid(p)

        assert g.representation == "xlsx_formulas"
        assert g.has_formulas

        by_loc = {(c.sheet, c.locator): c for c in g.cells}
        d2 = by_loc[("BOM", "D2")]
        assert d2.formula == "=B2*C2"   # the formula
        assert d2.value_num == 200.0    # AND the value it produced

    def test_a_line_that_looks_costed_and_costs_nothing(self, tmp_path):
        """Quantity 1, unit price never entered, formula quietly yields zero.

        The item sits inside the total contributing nothing, and the spreadsheet
        never mentions it. A values-only extractor sees a cell holding 0 and has
        no way to know that is wrong: the evidence is the formula, and the
        absence of the cell it multiplies by.
        """
        p = fixtures.workbook_with_a_zeroed_line(tmp_path / "bom.xlsx")
        g = extract_grid(p)
        by_loc = {(c.sheet, c.locator): c for c in g.cells}

        assert by_loc[("BOM", "B3")].value_num == 1.0    # quantity 1
        assert ("BOM", "C3") not in by_loc               # unit price: absent
        d3 = by_loc[("BOM", "D3")]
        assert d3.formula == "=B3*C3"
        assert d3.value_num == 0.0                       # silently zero

    def test_a_script_written_workbook_has_formulas_but_no_values(self, tmp_path):
        """openpyxl never RUNS a formula, so it caches nothing.

        Load such a file with data_only=True and every formula cell comes back
        None. Excel-saved workbooks carry both halves; script-written ones carry
        only the formula — and a script-written estimate is exactly the kind whose
        arithmetic most wants checking.

        So the detector downstream must NOT depend on seeing a cached zero. The
        durable signal is a formula multiplying by a cell that does not exist,
        which is present either way.
        """
        p = fixtures.workbook_with_a_zeroed_line(tmp_path / "gen.xlsx", cached=False)
        g = extract_grid(p)
        by_loc = {(c.sheet, c.locator): c for c in g.cells}

        d3 = by_loc[("BOM", "D3")]
        assert d3.formula == "=B3*C3"   # the formula survives
        assert d3.value_num is None     # ...but there is no value to read
        assert ("BOM", "C3") not in by_loc   # and this is still the real evidence

    def test_a_workbook_with_formulas_outranks_one_without(self, tmp_path):
        import openpyxl

        rich = fixtures.workbook_with_a_zeroed_line(tmp_path / "rich.xlsx")
        wb = openpyxl.Workbook()
        wb.active["A1"] = "flat"
        plain = tmp_path / "plain.xlsx"
        wb.save(plain)

        g_rich, g_plain = extract_grid(rich), extract_grid(plain)
        assert g_rich.representation == "xlsx_formulas"
        assert g_plain.representation == "xlsx"
        # Fidelity decides which copy wins when the same document turns up twice.
        # Formulas are where the errors hide, so they are worth more.
        assert g_rich.fidelity > g_plain.fidelity


class TestDecks:
    def test_a_grid_of_text_boxes_is_read_as_a_table(self, tmp_path):
        p = fixtures.deck_with_a_table_that_is_not_a_table(tmp_path / "deck.pptx")
        g = extract_grid(p)

        assert g.representation == "pptx_tables"
        cells = {(c.row, c.col): c.value_text for c in g.cells if c.sheet == "slide1"}
        assert cells[(1, 1)] == "Category"
        assert cells[(2, 2)] == "$1,000"
        assert cells[(4, 1)] == "Total"
        assert cells[(4, 2)] == "$3,500"

    def test_money_is_parsed_out_of_the_reconstructed_grid(self, tmp_path):
        p = fixtures.deck_with_a_table_that_is_not_a_table(tmp_path / "deck.pptx")
        g = extract_grid(p)
        cells = {(c.row, c.col): c for c in g.cells if c.sheet == "slide1"}
        assert cells[(4, 2)].value_num == 3500.0


class TestDiagrams:
    def test_every_tab_is_read_and_kept_apart(self, tmp_path):
        """A .drawio file is <mxfile>/<diagram>/<mxGraphModel>/<root>.

        The shared parser looked only at direct children and so returned zero
        nodes for any real draw.io file — an empty architecture, reported as
        success. Tabs stay separate because the rack elevation is the only one
        that says how many, and the floor plan is not.
        """
        p = fixtures.drawio_with_tabs(tmp_path / "arch.drawio")
        g = extract_grid(p)

        assert not g.warnings, g.warnings
        assert g.metadata["diagrams"] == ["Floor Plan", "Rack Elevation"]

        per_tab: dict[str, int] = {}
        for n in g.nodes:
            per_tab[n["diagram"]] = per_tab.get(n["diagram"], 0) + 1
        assert per_tab["Rack Elevation"] == 12
        assert per_tab["Floor Plan"] == 1

    def test_a_diagram_claims_units_but_prices_nothing(self, tmp_path):
        """A drawing is the most persuasive kind of claim, because it looks like
        a photograph of something that already exists. It is still a claim."""
        p = fixtures.drawio_with_tabs(tmp_path / "arch.drawio")
        g = extract_grid(p)

        rack = [n for n in g.nodes if n["diagram"] == "Rack Elevation"]
        assert len(rack) == 12
        # Nodes never land in `cells`. A diagram does not price anything, and
        # letting it become a line item is how a drawing turns into a budget.
        assert g.cells == []
