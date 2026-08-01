# CUI // SP-CTI
"""Slicing the reconciled bill of materials.

ICDEV can already group by one dimension. What it could not do is a cross-tab —
rows by columns by measure — which is the difference between a report and a tool
somebody can actually think with. "Spend by category" is a report. "Spend by
category BY WAVE, and now flip the architecture baseline and watch it move" is a
different conversation.

Two rules, and they are the whole reason to be careful here.

**The pivot must agree with the deck.** If a table in the appendix adds to a
different number than the headline, every figure in the pack is now suspect —
including the ones that were right. So both come from the same committed set, and
a cluster that has not been decided contributes ZERO to both. Never its cheapest
branch, never its mean.

**Excluded money is SHOWN, never silently dropped.** A total that quietly leaves
out the disputed lines is a lie of omission, and it is the most dangerous kind
here because it looks tidy. Every pivot carries a reconciliation footer: what is
committed, what is still open, and what the open items could cost.

Public API::

    build_dataset(clusters, lines, sources, ...) -> Dataset
    pivot(dataset, rows=..., cols=..., measure=..., agg=...) -> Pivot
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from tools.bom.lines import ExtractedLine
from tools.bom.reconcile import Cluster, Source

# What a row can be sliced BY. Free-form beyond these — a customer's own taxonomy
# lands here too — but these are the ones the engine can always supply.
DIMENSIONS = (
    "category",          # from the approved taxonomy
    "wave",              # when it is needed
    "cost_type",         # capex / opex
    "price_basis",       # msrp / street / rom / quoted / unknown
    "source",            # which document
    "credibility",       # how much that document's word is worth
    "manufacturer",
    "option_group",      # which mutually exclusive choice it belongs to
    "status",            # committed / open / excluded
    "sheet",
)

MEASURES = ("extended_price", "qty", "unit_price", "count")

AGGREGATIONS: dict[str, Callable[[list[float]], float]] = {
    "sum": lambda v: sum(v),
    "avg": lambda v: (sum(v) / len(v)) if v else 0.0,
    "min": lambda v: min(v) if v else 0.0,
    "max": lambda v: max(v) if v else 0.0,
    "count": lambda v: float(len(v)),
}

_BLANK = "(none)"


@dataclass
class Row:
    """One reconciled item, flattened for slicing."""

    line_id: str
    cluster_id: str
    description: str
    dims: dict[str, str] = field(default_factory=dict)
    extended_price: float = 0.0
    qty: float = 0.0
    unit_price: float = 0.0
    # Does this money count toward the committed total?
    committed: bool = False
    # Why not, when it does not. Shown to the user rather than hidden from them.
    excluded_reason: str = ""


@dataclass
class Dataset:
    rows: list[Row] = field(default_factory=list)
    # The documents that each claim to price this project.
    claim_sources: set[str] = field(default_factory=set)

    @property
    def competing_claims(self) -> bool:
        """More than one document claiming to be the bill of materials.

        This is the single most dangerous state the engine can be in, and it is
        also the NORMAL one — it is why the product exists.

        Item-level reconciliation merges what it can MATCH. Everything it cannot
        match — because four documents describe overlapping scope in different
        words — survives as separate clusters, and a naive total then ADDS four
        competing estimates of the same project together.

        That total is not large. It is meaningless. And it is meaningless in
        precisely the way the customer's own spreadsheets already were, which
        would make this engine an expensive way to reproduce their problem with
        more decimal places.

        So while more than one source is claiming the same scope, there IS no
        total, and the engine says so instead of printing one.
        """
        return len(self.claim_sources) > 1

    @property
    def committed_total(self) -> float:
        return sum(r.extended_price for r in self.rows if r.committed)

    @property
    def open_total(self) -> float:
        """What the undecided items COULD cost, at the top of their range.

        Not added to anything. Stated, so nobody mistakes a clean committed total
        for the whole story.
        """
        return sum(r.extended_price for r in self.rows if not r.committed)

    def dimensions(self) -> list[str]:
        seen: set[str] = set()
        for r in self.rows:
            seen.update(r.dims)
        return sorted(seen)


@dataclass
class Pivot:
    rows: list[str]
    cols: list[str]
    cells: dict[tuple[str, str], float]
    row_totals: dict[str, float]
    col_totals: dict[str, float]
    grand_total: float
    measure: str
    agg: str
    # The honesty footer. A total that quietly omits the disputed lines is a lie
    # of omission, and it is the dangerous kind because it looks tidy.
    committed_total: float = 0.0
    open_total: float = 0.0
    open_count: int = 0
    competing_claims: list[str] = field(default_factory=list)

    def cell(self, row: str, col: str) -> float:
        return self.cells.get((row, col), 0.0)

    def as_table(self) -> list[list[Any]]:
        head = ["", *self.cols, "Total"]
        body = [
            [r, *(self.cell(r, c) for c in self.cols), self.row_totals.get(r, 0.0)]
            for r in self.rows
        ]
        foot = ["Total", *(self.col_totals.get(c, 0.0) for c in self.cols), self.grand_total]
        return [head, *body, foot]

    @property
    def is_a_total(self) -> bool:
        """Is the grand total a NUMBER, or just a sum?

        While several documents each claim to price the same project, adding them
        gives a sum — and a sum of competing estimates is not a total. Saying so is
        the difference between this engine and the spreadsheet it replaced.
        """
        return not self.competing_claims

    @property
    def reconciliation_note(self) -> str:
        if self.competing_claims:
            names = ", ".join(sorted(self.competing_claims))
            return (
                f"THIS FIGURE IS NOT A TOTAL. {len(self.competing_claims)} documents "
                f"each claim to price this project ({names}), and reconciliation could "
                f"only merge the lines it could MATCH. Everything described differently "
                f"in different documents survives separately — so adding these up adds "
                f"COMPETING ESTIMATES OF THE SAME PROJECT together, which is exactly "
                f"the arithmetic that produced the spread you are here to resolve.\n\n"
                f"Nominate a source of record for each area of scope, and this becomes "
                f"a number. Until then it is a sum, and a sum of estimates is not an "
                f"estimate of anything."
            )
        if not self.open_count:
            return "Every line here has been reconciled and accepted."
        return (
            f"This table shows the {self.committed_total:,.2f} that has been agreed. "
            f"A further {self.open_count} item(s), worth up to {self.open_total:,.2f}, "
            f"are still undecided and contribute NOTHING to these figures — not "
            f"their cheapest option, not an average. They are listed in the findings "
            f"register, and until somebody chooses, this total is an understatement "
            f"rather than an estimate."
        )


def build_dataset(
    clusters: Iterable[Cluster],
    lines: Iterable[ExtractedLine],
    sources: dict[str, Source] | None = None,
    *,
    categories: dict[str, str] | None = None,
    waves: dict[str, str] | None = None,
    option_groups: dict[str, str] | None = None,
) -> Dataset:
    """Flatten the reconciled clusters into sliceable rows.

    ONE row per cluster, taken from its winner — never one row per source line.
    Four documents describing the same switch is one switch, and a pivot built
    from the raw lines would show it four times and total it four times, which is
    exactly the arithmetic this engine exists to prevent.
    """
    sources = sources or {}
    by_id = {ln.line_id: ln for ln in lines}
    ds = Dataset()

    # Which documents are asserting a price for this project? A derived copy is
    # not a competing claim — it is the same claim, reprinted. Nor is an inventory
    # or a diagram, which price nothing.
    for name, src in sources.items():
        if src.role in ("bom_claim", "quote") and any(
            ln.source_document == name for ln in by_id.values()
        ):
            ds.claim_sources.add(name)

    for cluster in clusters:
        winner_id = cluster.winner_line_id or (
            cluster.members[0] if cluster.members else ""
        )
        winner = by_id.get(winner_id)
        if winner is None:
            continue

        src = sources.get(winner.source_document) or Source(winner.source_document)

        qty = cluster.resolved_qty if cluster.resolved_qty is not None else (winner.qty or 0)
        unit = (
            cluster.resolved_unit_price
            if cluster.resolved_unit_price is not None
            else (winner.unit_price or 0)
        )
        extended = winner.extended_price
        if extended is None:
            extended = (qty or 0) * (unit or 0)

        if cluster.committed:
            reason = ""
        elif len(cluster.members) > 1:
            reason = (
                f"{len(cluster.members)} sources describe this and they do not agree"
            )
        else:
            reason = "not yet accepted"

        ds.rows.append(Row(
            line_id=winner.line_id,
            cluster_id=cluster.cluster_id,
            description=winner.description,
            dims={
                "category": (categories or {}).get(winner.line_id, _BLANK),
                "wave": (waves or {}).get(winner.line_id, _BLANK),
                "cost_type": "opex" if _looks_recurring(winner) else "capex",
                "price_basis": cluster.resolved_price_basis or winner.price_basis,
                "source": winner.source_document,
                "credibility": src.credibility_tier,
                "manufacturer": winner.manufacturer or _BLANK,
                "option_group": (option_groups or {}).get(winner.line_id, _BLANK),
                "status": "committed" if cluster.committed else "open",
                "sheet": winner.source_sheet or _BLANK,
            },
            extended_price=float(extended or 0),
            qty=float(qty or 0),
            unit_price=float(unit or 0),
            committed=cluster.committed,
            excluded_reason=reason,
        ))

    return ds


def _looks_recurring(line: ExtractedLine) -> bool:
    blob = f"{line.description} {line.notes} {line.uom}".lower()
    return any(
        k in blob for k in ("/mo", "per month", "monthly", "recurring", "/yr", "per year")
    )


def pivot(
    dataset: Dataset,
    *,
    rows: str,
    cols: str = "",
    measure: str = "extended_price",
    agg: str = "sum",
    committed_only: bool = True,
) -> Pivot:
    """A cross-tab: rows by columns by measure.

    ``committed_only`` defaults to True and that default is the point. A total that
    silently includes disputed lines would tell a budget owner that a number is
    settled when it is not — and the whole product exists to stop exactly that.
    Open items are counted in the footer, where they can be seen.
    """
    if measure not in MEASURES:
        raise ValueError(f"unknown measure: {measure}")
    if agg not in AGGREGATIONS:
        raise ValueError(f"unknown aggregation: {agg}")

    considered = [r for r in dataset.rows if r.committed or not committed_only]

    def value(r: Row) -> float:
        if measure == "count":
            return 1.0
        return float(getattr(r, measure, 0.0) or 0.0)

    buckets: dict[tuple[str, str], list[float]] = {}
    for r in considered:
        rk = r.dims.get(rows, _BLANK) or _BLANK
        ck = r.dims.get(cols, _BLANK) if cols else "Total"
        buckets.setdefault((rk, ck), []).append(value(r))

    fn = AGGREGATIONS[agg]
    cells = {k: fn(v) for k, v in buckets.items()}

    row_keys = sorted({k[0] for k in cells})
    col_keys = sorted({k[1] for k in cells})

    # Totals are re-aggregated from the underlying values, never summed from the
    # cells. For 'sum' the two agree; for 'avg' they do NOT, and an average of
    # averages is a number with no meaning that looks exactly like one with meaning.
    row_totals = {
        rk: fn([v for r, vals in buckets.items() if r[0] == rk for v in vals])
        for rk in row_keys
    }
    col_totals = {
        ck: fn([v for c, vals in buckets.items() if c[1] == ck for v in vals])
        for ck in col_keys
    }
    grand = fn([v for vals in buckets.values() for v in vals])

    open_rows = [r for r in dataset.rows if not r.committed]

    return Pivot(
        rows=row_keys,
        cols=col_keys,
        cells=cells,
        row_totals=row_totals,
        col_totals=col_totals,
        grand_total=grand,
        measure=measure,
        agg=agg,
        committed_total=dataset.committed_total,
        open_total=dataset.open_total,
        open_count=len(open_rows),
        competing_claims=sorted(dataset.claim_sources) if dataset.competing_claims else [],
    )


def suggest_pivots(dataset: Dataset, intent: str = "") -> list[dict[str, str]]:
    """The slices worth showing, given what is actually in the data.

    Deterministic. A dimension with one value in it explains nothing, and a
    dimension nobody populated explains less — so neither is offered, however
    reasonable it sounds in the abstract.
    """
    def spread(dim: str) -> int:
        return len({r.dims.get(dim, _BLANK) for r in dataset.rows} - {_BLANK})

    out: list[dict[str, str]] = []

    if spread("category"):
        out.append({
            "rows": "category", "cols": "cost_type",
            "measure": "extended_price", "agg": "sum",
            "title": "Where the money goes, and whether it is a purchase or a commitment",
        })
    if spread("wave") > 1:
        out.append({
            "rows": "wave", "cols": "category",
            "measure": "extended_price", "agg": "sum",
            "title": "What we need, and when — an all-or-nothing ask gets deferred",
        })
    if spread("credibility") > 1:
        out.append({
            "rows": "category", "cols": "credibility",
            "measure": "extended_price", "agg": "sum",
            "title": "How much of this number rests on sources we trust",
        })
    if spread("price_basis") > 1:
        out.append({
            "rows": "price_basis", "cols": "",
            "measure": "extended_price", "agg": "sum",
            "title": "What KIND of prices these are — list, street, or somebody's estimate",
        })
    if spread("option_group"):
        out.append({
            "rows": "option_group", "cols": "",
            "measure": "extended_price", "agg": "sum",
            "title": "The choices nobody has made yet",
        })

    return out
