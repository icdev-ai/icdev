# CUI // SP-CTI
"""Categories, without asking a model.

The point of this module is that the answer is usually already in the document. A
workbook with sheets called Compute, Networking and Software has been categorised
by the person who owns the scope, in the words their organisation uses — and
replacing that with a taxonomy of our own is swapping a right answer for a
defensible one.

Invented content. ICDEV is a public repo.
"""
from __future__ import annotations

import pytest

from tools.bom.categorize import categorize, category_of
from tools.bom.lines import ExtractedLine


def _line(desc, sheet="", part="", uom=""):
    return ExtractedLine(
        line_id=f"l-{desc}-{sheet}", line_hash="h", source_document="d.xlsx",
        source_sheet=sheet, source_locator="A1", raw_text=desc,
        description=desc, part_number=part, uom=uom,
    )


class TestTheDocumentAlreadyKnows:
    def test_the_sheet_name_is_the_category(self):
        assert category_of(_line("Core switch", "Networking")) == "Networking"

    def test_a_numbered_section_loses_only_its_number(self):
        """"2.1 Core Lab Switching" is a name with an ordinal on it. The ordinal
        sorts the tabs into an order that means nothing once sections are merged
        across four documents."""
        got = category_of(_line("Switch", "2.1 Core Lab Switching & Routing"))
        assert got == "Core Lab Switching & Routing"

    def test_their_words_are_not_rewritten_into_ours(self):
        assert category_of(_line("x", "Physical Infra")) == "Physical Infra"


class TestWhenTheSheetNameSaysNothing:
    @pytest.mark.parametrize("sheet", ["Sheet1", "BOM", "Draft", "v0.2", "Summary",
                                       "Copy of Sheet1", ""])
    def test_a_meaningless_sheet_name_falls_through_to_the_content(self, sheet):
        """A sheet called "Sheet1" is not a category; it is the absence of one, and
        treating it as one produces a workbook with a tab called Sheet1 in it."""
        got = category_of(_line("Dell PowerEdge R320 server", sheet))
        assert got not in ("Sheet1", "BOM", "Draft", "v0.2", "Summary", "")

    def test_it_never_returns_blank(self):
        """A line with no category still has a price. One that quietly disappears
        from a category sheet is a line nobody argues about again."""
        assert category_of(_line("", "")).strip()


class TestNoLineIsLost:
    def test_every_line_gets_exactly_one_category(self):
        lines = [
            _line("Core switch", "Networking"),
            _line("Rack server", "Compute"),
            _line("Mystery item", "Sheet1"),
        ]
        cats = categorize(lines)
        assert len(cats) == 3
        assert all(v.strip() for v in cats.values())

    def test_it_is_keyed_by_line_id_for_build_dataset(self):
        lines = [_line("Core switch", "Networking"), _line("Server", "Compute")]
        assert categorize(lines)[lines[0].line_id] == "Networking"


class TestATaxonomyWithOneBucketIsNotATaxonomy:
    """A single-sheet workbook has a sheet NAME, not a category scheme."""

    def test_one_sheet_falls_through_to_the_content(self):
        """Taking the sheet name at its word produces a "categorised" workbook with
        exactly one tab, which tells the reader nothing and cannot be pivoted."""
        lines = [
            _line("Cisco FPR4145 firewall", "Network - Hardware BOM"),
            _line("HPE DL380 Gen12 server", "Network - Hardware BOM"),
            _line("VMware vSphere licence", "Network - Hardware BOM"),
        ]
        cats = set(categorize(lines).values())
        assert cats != {"Network - Hardware BOM"}
        assert len(cats) >= 2, cats

    def test_two_real_sections_are_left_alone(self):
        """The rule must not fire when the document DID partition its lines — that
        would throw away the author's own taxonomy to prove a point."""
        lines = [
            _line("Core switch", "Networking"),
            _line("Rack server", "Compute"),
        ]
        assert set(categorize(lines).values()) == {"Networking", "Compute"}

    def test_no_lines_is_an_empty_map_not_a_crash(self):
        assert categorize([]) == {}
