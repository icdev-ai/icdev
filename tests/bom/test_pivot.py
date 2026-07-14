# CUI // SP-CTI
"""Slicing the reconciled bill of materials.

Invented content. ICDEV is a public repo.
"""
from __future__ import annotations

import pytest

from tools.bom.lines import ExtractedLine
from tools.bom.pivot import Dataset, Row, build_dataset, pivot, suggest_pivots
from tools.bom.reconcile import Cluster, Source


def _row(rid, *, price, committed=True, **dims):
    return Row(
        line_id=rid, cluster_id=f"c-{rid}", description=rid,
        dims=dims, extended_price=price, qty=1, unit_price=price,
        committed=committed,
        excluded_reason="" if committed else "sources disagree",
    )


class TestCrossTab:
    @pytest.fixture
    def ds(self):
        return Dataset(rows=[
            _row("a", price=100, category="compute", wave="now"),
            _row("b", price=200, category="compute", wave="later"),
            _row("c", price=50, category="network", wave="now"),
        ])

    def test_rows_by_columns(self, ds):
        p = pivot(ds, rows="category", cols="wave")
        assert p.cell("compute", "now") == 100
        assert p.cell("compute", "later") == 200
        assert p.cell("network", "now") == 50
        assert p.cell("network", "later") == 0

    def test_margins(self, ds):
        p = pivot(ds, rows="category", cols="wave")
        assert p.row_totals["compute"] == 300
        assert p.col_totals["now"] == 150
        assert p.grand_total == 350

    def test_totals_are_re_aggregated_not_summed_from_the_cells(self):
        """For 'sum' the two agree; for 'avg' they do NOT.

        An average of averages is a number with no meaning that looks exactly like
        one with meaning — which is the most expensive kind of number there is.
        """
        ds = Dataset(rows=[
            _row("a", price=100, g="x"),
            _row("b", price=200, g="x"),
            _row("c", price=300, g="y"),
        ])
        p = pivot(ds, rows="g", measure="extended_price", agg="avg")
        # Mean of (150, 300) would be 225. The mean of the DATA is 200.
        assert p.grand_total == 200

    def test_one_row_per_cluster_not_per_source_line(self):
        """Four documents describing the same switch is ONE switch.

        A pivot built from the raw lines would show it four times and total it four
        times — which is precisely the arithmetic this engine exists to prevent.
        """
        lines = [
            ExtractedLine(
                line_id=f"l{i}", line_hash=f"h{i}", source_document=f"d{i}.xlsx",
                source_sheet="S", source_locator="A1", raw_text="switch",
                description="Core switch", unit_price=21000, qty=1,
                extended_price=21000,
            )
            for i in range(4)
        ]
        cluster = Cluster(
            cluster_id="c1", members=[ln.line_id for ln in lines],
            winner_line_id="l0", resolved_qty=1, resolved_unit_price=21000,
            status="accepted",
        )
        ds = build_dataset([cluster], lines, {})
        assert len(ds.rows) == 1
        assert ds.committed_total == 21000


class TestUndecidedMoneyContributesNothing:
    def test_an_open_cluster_is_not_in_the_total(self):
        ds = Dataset(rows=[
            _row("a", price=100, category="compute"),
            _row("b", price=999, category="compute", committed=False),
        ])
        p = pivot(ds, rows="category")
        assert p.grand_total == 100
        assert p.committed_total == 100

    def test_but_it_is_SHOWN(self):
        """A total that quietly leaves out the disputed lines is a lie of omission,
        and it is the dangerous kind because it looks tidy."""
        ds = Dataset(rows=[
            _row("a", price=100, category="compute"),
            _row("b", price=999, category="compute", committed=False),
        ])
        p = pivot(ds, rows="category")
        assert p.open_count == 1
        assert p.open_total == 999
        assert "contribute NOTHING" in p.reconciliation_note
        assert "understatement rather than an estimate" in p.reconciliation_note


class TestASumOfEstimatesIsNotAnEstimate:
    """The single most dangerous state the engine can be in — and the normal one.

    Item-level reconciliation merges what it can MATCH. Everything it cannot match,
    because several documents describe overlapping scope in different words,
    survives as separate clusters. A naive total then ADDS COMPETING ESTIMATES OF
    THE SAME PROJECT together — reproducing the customer's own problem with more
    decimal places, which would make this engine an expensive way to achieve
    nothing.
    """

    def _two_claims(self):
        lines = [
            ExtractedLine(
                line_id="a", line_hash="ha", source_document="bom_one.xlsx",
                source_sheet="S", source_locator="A1", raw_text="x",
                description="Core switch", unit_price=21000, qty=1,
                extended_price=21000,
            ),
            ExtractedLine(
                line_id="b", line_hash="hb", source_document="bom_two.xlsx",
                source_sheet="S", source_locator="A1", raw_text="y",
                description="Perimeter firewall", unit_price=10500, qty=1,
                extended_price=10500,
            ),
        ]
        clusters = [
            Cluster(cluster_id="c1", members=["a"], winner_line_id="a",
                    resolved_qty=1, resolved_unit_price=21000, status="accepted"),
            Cluster(cluster_id="c2", members=["b"], winner_line_id="b",
                    resolved_qty=1, resolved_unit_price=10500, status="accepted"),
        ]
        sources = {
            "bom_one.xlsx": Source("1", role="bom_claim"),
            "bom_two.xlsx": Source("2", role="bom_claim"),
        }
        return clusters, lines, sources

    def test_two_documents_claiming_the_same_project_is_not_a_total(self):
        clusters, lines, sources = self._two_claims()
        p = pivot(build_dataset(clusters, lines, sources), rows="source")

        assert p.is_a_total is False
        assert "NOT A TOTAL" in p.reconciliation_note
        assert "COMPETING ESTIMATES" in p.reconciliation_note
        assert "source of record" in p.reconciliation_note

    def test_one_source_of_record_makes_it_a_number(self):
        clusters, lines, sources = self._two_claims()
        # Nominate one. The other is now out of scope for this figure.
        clusters = clusters[:1]
        lines = lines[:1]
        sources = {"bom_one.xlsx": sources["bom_one.xlsx"]}

        p = pivot(build_dataset(clusters, lines, sources), rows="source")
        assert p.is_a_total is True
        assert p.grand_total == 21000
        assert "NOT A TOTAL" not in p.reconciliation_note

    def test_a_printed_copy_is_not_a_competing_claim(self):
        """It is the same claim, reprinted. Nor is an inventory or a diagram, which
        price nothing at all."""
        clusters, lines, sources = self._two_claims()
        sources["bom_two.xlsx"] = Source("2", role="derived")

        ds = build_dataset(clusters, lines, sources)
        assert ds.competing_claims is False


class TestSuggestedPivots:
    def test_a_dimension_with_one_value_explains_nothing(self):
        """However reasonable it sounds in the abstract."""
        ds = Dataset(rows=[
            _row("a", price=100, category="compute", wave="now"),
            _row("b", price=200, category="network", wave="now"),
        ])
        suggested = suggest_pivots(ds)
        # Every row is in the same wave, so slicing by wave shows a single column.
        assert not any(s["rows"] == "wave" for s in suggested)
        assert any(s["rows"] == "category" for s in suggested)


class TestBadInputIsRefused:
    def test_an_unknown_measure(self):
        with pytest.raises(ValueError, match="unknown measure"):
            pivot(Dataset(), rows="category", measure="vibes")

    def test_an_unknown_aggregation(self):
        with pytest.raises(ValueError, match="unknown aggregation"):
            pivot(Dataset(), rows="category", agg="guess")
