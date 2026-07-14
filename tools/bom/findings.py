# CUI // SP-CTI
"""The deterministic detectors. No model runs here.

Everything in this module is arithmetic, and that is the point. These findings
are the ones you can put in front of a hostile reviewer and defend line by line,
because each is a fact about the document rather than an opinion about it. They
are also, on real evidence, the ones worth the most money.

The engine ships a ``--no-llm`` mode that produces every finding below and
nothing else. That mode is the honest demo: it cannot hallucinate, because there
is nothing in it that could.

Each finding cites the exact cell it came from. A finding that cannot say where
it came from is a rumour, and a reviewer is right to ignore it.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

from tools.bom import constants as C
from tools.bom.extract_grid import Cell, GridExtraction
from tools.bom.formula_graph import (
    FormulaGraph,
    build_graph,
    is_rollup,
    missing_operands,
)

# A cross-reference note is the difference between a critical finding and a
# merely suspicious one. When a human has written "shared with the Networking
# sheet" next to a line, they have told you the money appears elsewhere — and if
# both subtotals still count it, they told you and it happened anyway.
_CROSSREF_RE = re.compile(
    r"\b(?:shared\s+with|also\s+(?:in|on)|see\s+also|counted\s+(?:in|on)|duplicate\s+of|per)\b",
    re.IGNORECASE,
)

# Recurring cost language — deliberately NARROW.
#
# A charge stated PER PERIOD ("3,000/mo") booked once in a capital table
# understates the commitment by however many months nobody multiplied by. That is
# the finding, and it is a real one.
#
# What this must NOT match is a multi-year TERM inside a product name — a licence
# sold as a three-year SKU is a single purchase that happens to cover three years.
# An earlier version keyed on "3-yr" and produced twenty-nine confident
# accusations about lines that were perfectly correct. A detector that cries wolf
# gets switched off, and then it protects nothing at all.
#
# So: explicit per-period pricing only. Precision over recall, every time — the
# recall we lose here is picked up later by is_recurring on the normalized line,
# where we know which column is the price.
_RECURRING_RE = re.compile(
    r"(?:\d\s*/\s*(?:mo|month|yr|year)\b"
    r"|\bper\s+(?:month|year|annum)\b"
    r"|\b(?:monthly|recurring)\b"
    r"|\bsubscription\s+(?:fee|cost|charge)\b)",
    re.IGNORECASE,
)

_PLACEHOLDER_RE = re.compile(
    r"(?:\b(?:TBD|TBC|FIXME|XXX|pending|to\s+be\s+(?:determined|confirmed)"
    r"|to\s+work)\b|\?{3,})",
    re.IGNORECASE,
)


@dataclass
class Evidence:
    source_document: str
    sheet: str = ""
    locator: str = ""
    raw_text: str = ""
    line_id: str = ""

    def as_dict(self) -> dict[str, str]:
        return {
            "source_document": self.source_document,
            "sheet": self.sheet,
            "locator": self.locator,
            "raw_text": self.raw_text,
            "line_id": self.line_id,
        }


@dataclass
class Finding:
    finding_type: str
    title: str
    detail: str = ""
    kind: str = C.DEFAULT_FINDING_KIND
    severity: str = "medium"
    # NULL is allowed and is often the honest answer. A finding worth reporting
    # with no defensible dollar figure keeps None and says why — a plausible
    # invented number gets quoted back at somebody in a budget meeting.
    impact_usd: float | None = None
    detector: str = "deterministic"
    evidence: list[Evidence] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)

    @property
    def fingerprint(self) -> str:
        """Stable across re-runs, so a disposition survives the next upload."""
        parts = [self.finding_type] + [
            f"{e.source_document}|{e.sheet}|{e.locator}" for e in self.evidence
        ]
        return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()[:16]

    def __post_init__(self) -> None:
        if self.finding_type not in C.FINDING_TYPES:
            raise ValueError(f"unknown finding_type: {self.finding_type}")
        if self.severity not in C.SEVERITIES:
            raise ValueError(f"unknown severity: {self.severity}")
        if self.kind not in C.FINDING_KINDS:
            raise ValueError(f"unknown kind: {self.kind}")


def _ev(doc: str, cell: Cell) -> Evidence:
    return Evidence(
        source_document=doc,
        sheet=cell.sheet,
        locator=cell.locator,
        raw_text=cell.formula or cell.value_text,
    )


def _row_text(cells: list[Cell], sheet: str, row: int) -> str:
    return " ".join(
        c.value_text for c in cells
        if c.sheet == sheet and c.row == row and c.value_text
    )


def _row_label(cells: list[Cell], sheet: str, row: int) -> str:
    """The item's name: the leftmost cell on the row that is text, not a number.

    Deliberately NOT the whole row. Bucketing duplicates on the full row text
    fails on precisely the cases that matter most, because the second occurrence
    of a shared item is the one carrying a note — "shared with the Networking
    sheet" — and that note makes the two rows look different.

    The annotation that PROVES it is a duplicate was the thing stopping it from
    being recognised as one.
    """
    candidates = [
        c for c in cells
        if c.sheet == sheet and c.row == row and c.value_text and c.value_num is None
    ]
    if not candidates:
        return ""
    return min(candidates, key=lambda c: c.col).value_text.strip()


# ── Detectors ────────────────────────────────────────────────────────────────

def detect_unpriced_zeroed(doc: str, g: FormulaGraph) -> list[Finding]:
    """A line that looks costed and costs nothing.

    Quantity present, unit price never entered, and the formula multiplies
    politely to zero. It sits inside the total contributing nothing, and the
    spreadsheet will never mention it.

    The test is the FORMULA, not the value. A workbook a script wrote has never
    evaluated its own formulas — ``data_only=True`` returns None and there is no
    zero to notice — so keying on the cached result would find these in
    hand-saved files and miss them in generated ones. Generated estimates are
    exactly the kind whose arithmetic most wants checking.
    """
    out: list[Finding] = []
    for key in sorted(g.consumes):
        cell = g.cells.get(key)
        if cell is None or is_rollup(cell.formula):
            continue

        missing = missing_operands(g, key)
        if not missing:
            continue

        # Only care when the other operands are real: a formula referencing
        # nothing at all is an empty template row, not a costed line.
        present = [
            g.cells[r] for r in g.consumes[key]
            if r in g.cells and g.cells[r].value_num is not None
        ]
        if not present:
            continue

        computed = cell.value_num
        if computed not in (None, 0.0):
            continue   # it produced a real number; the operand is elsewhere

        gone = ", ".join(f"{s}!{loc}" for s, loc in sorted(missing))
        out.append(Finding(
            finding_type="unpriced_line_zeroed",
            kind="defect",
            severity="high",
            title=f"{cell.sheet}!{cell.locator} computes to zero — an operand was never filled in",
            detail=(
                f"`{cell.formula}` multiplies by {gone}, which is empty. The line "
                f"carries a real quantity and contributes nothing to the total. "
                f"The total is therefore understated by an amount this document "
                f"does not contain — we will not guess it."
            ),
            impact_usd=None,   # deliberately: the missing price is missing
            evidence=[_ev(doc, cell)],
            data={"missing_operands": gone, "formula": cell.formula},
        ))
    return out


def detect_hardcoded_rollup(doc: str, g: FormulaGraph) -> list[Finding]:
    """A typed-in number sitting where its neighbours are computed.

    Edit the sheets it claims to total and it will not move. The workbook keeps
    presenting a stale figure with complete confidence, and nothing in the
    rendered view distinguishes it from a live one.
    """
    out: list[Finding] = []
    for key in sorted(g.cells):
        if not g.is_hardcoded_sibling(key):
            continue
        cell = g.cells[key]
        out.append(Finding(
            finding_type="hardcoded_rollup",
            kind="defect",
            severity="high",
            title=f"{cell.sheet}!{cell.locator} is a typed-in number in a block of formulas",
            detail=(
                "Its neighbours are computed; this one is a literal. Editing the "
                "underlying sheets will not change it, and nothing on screen says "
                "so. A subtotal that silently stops tracking its own inputs is the "
                "quietest way for a total to go wrong."
            ),
            impact_usd=cell.value_num,
            evidence=[_ev(doc, cell)],
            data={"value": cell.value_num},
        ))
    return out


def detect_stale_rollup(doc: str, g: FormulaGraph, tolerance: float = 0.01) -> list[Finding]:
    """A subtotal that disagrees with the cells it sums.

    Only checkable when the workbook carries cached values — a script-written one
    has none, and we say nothing rather than guessing.
    """
    out: list[Finding] = []
    for key in sorted(g.rollups):
        cell = g.cells.get(key)
        if cell is None or cell.value_num is None:
            continue
        operands = [
            g.cells[r].value_num
            for r in g.consumes.get(key, ())
            if r in g.cells and g.cells[r].value_num is not None
        ]
        if not operands:
            continue
        if not cell.formula.upper().lstrip("=").startswith("SUM("):
            continue   # only plain SUM is safely recomputable without an engine

        expected = sum(operands)
        if abs(expected - cell.value_num) <= tolerance:
            continue

        out.append(Finding(
            finding_type="stale_rollup",
            kind="defect",
            severity="high",
            title=f"{cell.sheet}!{cell.locator} disagrees with the cells it sums",
            detail=(
                f"The cell holds {cell.value_num:,.2f}; its own inputs add to "
                f"{expected:,.2f}. The workbook was edited after it was last "
                f"recalculated, and the figure on screen is the old one."
            ),
            impact_usd=abs(expected - cell.value_num),
            evidence=[_ev(doc, cell)],
            data={"stored": cell.value_num, "recomputed": expected},
        ))
    return out


def detect_intra_doc_double_count(
    doc: str, cells: list[Cell], g: FormulaGraph
) -> list[Finding]:
    """The same money reaching the grand total by two different routes.

    This is NOT deduplication. An item can legitimately appear on two sheets as a
    cross-reference; the bug is when *both* sheet subtotals include it.

    The whole determination is: do the two occurrences feed the SAME rollup, or
    DIFFERENT ones that both flow into the total?

        same rollup      -> a genuine quantity of two
        different ones   -> the money is in the total twice

    That distinction is invisible in the rendered values and recoverable only
    from the formula graph. It is the single most valuable thing this module
    does.
    """
    out: list[Finding] = []

    # Anchor on the LITERAL amounts a human typed, never on computed cells.
    #
    # A subtotal is the *result* of a set of lines, and two subtotals coinciding
    # is unremarkable. Bucketing on them finds the sums rather than the items —
    # which is a confidently wrong answer, and those are the expensive kind.
    buckets: dict[tuple[float, str], list[Cell]] = {}
    for cell in cells:
        if cell.is_formula:
            continue
        if cell.value_num is None or cell.value_num <= 0:
            continue
        label = _row_label(cells, cell.sheet, cell.row).lower()
        desc_key = re.sub(r"[^a-z0-9 ]", " ", label)
        desc_key = re.sub(r"\s+", " ", desc_key).strip()[:80]
        if not desc_key:
            continue
        buckets.setdefault((round(cell.value_num, 2), desc_key), []).append(cell)

    for (amount, desc_key), members in sorted(buckets.items()):
        sheets = {c.sheet for c in members}
        if len(sheets) < 2:
            continue   # the same figure twice on one sheet is usually a column pair

        # Which grand-total-bound rollups does each occurrence feed?
        routes: dict[str, set[tuple[str, str]]] = {}
        for c in members:
            routes[f"{c.sheet}!{c.locator}"] = g.ancestors((c.sheet, c.locator))

        feeding = {k: v for k, v in routes.items() if v}
        if len(feeding) < 2:
            continue   # at most one of them actually reaches a total

        route_sets = list(feeding.values())
        # If every occurrence flows into exactly the same set of rollups, the
        # money is counted once and the quantity is simply two.
        if all(rs == route_sets[0] for rs in route_sets[1:]):
            continue

        note = ""
        for c in members:
            row = _row_text(cells, c.sheet, c.row)
            if _CROSSREF_RE.search(row):
                note = row
                break

        redundant = amount * (len(feeding) - 1)
        out.append(Finding(
            finding_type="intra_doc_double_count",
            kind="defect",
            severity="critical" if note else "high",
            title=(
                f"{amount:,.2f} reaches the total from {len(feeding)} places "
                f"({', '.join(sorted(sheets))})"
            ),
            detail=(
                "The same amount is consumed by different subtotals that both flow "
                "into the grand total, so the money is counted more than once. "
                + (
                    f'A note on the line says: "{note.strip()}" — somebody already '
                    "knew this figure appears elsewhere, and both subtotals still "
                    "include it."
                    if note else
                    "Confirm whether this is one item cross-referenced twice, or two "
                    "items that happen to cost the same."
                )
            ),
            impact_usd=redundant,
            evidence=[_ev(doc, c) for c in members],
            data={"amount": amount, "occurrences": len(feeding), "note": note},
        ))
    return out


_STOPWORDS = frozenset(
    "a an and or the of for to with in on at by is are be per each new used "
    "support license licence software hardware system unit units item items".split()
)


def _tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", text.lower())
    return {w for w in words if len(w) > 2 and w not in _STOPWORDS and not w.isdigit()}


def detect_crossref_double_count(
    doc: str, cells: list[Cell], g: FormulaGraph
) -> list[Finding]:
    """Somebody wrote "shared with the other sheet" — and both sheets counted it.

    The label-matched detector cannot see this one, and the reason is worth
    stating: the same item is frequently RENAMED between sheets. A licence is
    "Network Simulation Software" on one and the vendor's product name on
    another, so nothing about the two rows looks alike except the money.

    What IS reliable is the note. A human who writes "shared with the Test Bench"
    has told you, in plain language, that this money appears somewhere else. That
    is a deterministic anchor and a very strong one — it is a confession.

    So: take every line carrying a cross-reference note, look for other lines at
    the same amount whose description genuinely overlaps, and check whether the
    two reach the grand total by different routes. If they do, the money is in
    the total twice and the person who wrote the note already suspected it.
    """
    out: list[Finding] = []

    # Reason about ROWS, not cells. A line item spans several numeric cells —
    # quantity, unit price, extended — and treating each as a candidate reports
    # the same duplicate three times and, worse, announces that "1.00 appears on
    # both sheets", which is the quantity column and is meaningless.
    #
    # The row's money is its LARGEST figure: the extended price.
    def _row_money(sheet: str, row: int) -> Cell | None:
        nums = [
            c for c in cells
            if c.sheet == sheet and c.row == row
            and not c.is_formula and c.value_num is not None and c.value_num > 0
        ]
        return max(nums, key=lambda c: c.value_num) if nums else None

    rows = sorted({(c.sheet, c.row) for c in cells if c.value_num is not None})
    priced_rows = [
        (sheet, row, money)
        for sheet, row in rows
        if (money := _row_money(sheet, row)) is not None
    ]

    for sheet, row, cell in priced_rows:
        row_text = _row_text(cells, sheet, row)
        if not _CROSSREF_RE.search(row_text):
            continue

        mine = g.ancestors((cell.sheet, cell.locator))

        # NOTE: `mine` may legitimately be empty, and staying silent when it is
        # would be the worst possible behaviour.
        #
        # On real evidence the sheets carrying a shared item frequently have NO
        # formula subtotals at all — the subtotal is a number somebody typed,
        # which is exactly why it also shows up as a hardcoded_rollup. The money
        # is untraceable *because the workbook is broken in a second way*, and an
        # engine that requires a clean formula graph before it will speak up goes
        # quiet on precisely the documents that need it most.
        #
        # So we still report, and we say plainly that we cannot verify the routing
        # and why. That is a true statement, it is useful, and it hands the human
        # the thread to pull.

        my_tokens = _tokens(row_text)
        if len(my_tokens) < 3:
            continue

        best: tuple[float, Cell, bool] | None = None
        for other_sheet, other_row, other in priced_rows:
            if other_sheet == sheet:
                continue
            if round(other.value_num, 2) != round(cell.value_num, 2):
                continue

            theirs = g.ancestors((other.sheet, other.locator))
            traceable = bool(mine and theirs)
            if traceable and theirs == mine:
                continue   # same route: one item, counted once. Not a bug.

            other_tokens = _tokens(_row_text(cells, other_sheet, other_row))
            if not other_tokens:
                continue
            overlap = len(my_tokens & other_tokens) / len(my_tokens | other_tokens)
            # The amount alone is far too weak — a round figure recurs all over a
            # real BOM. The description has to corroborate.
            if overlap < 0.12:
                continue
            if best is None or overlap > best[0]:
                best = (overlap, other, traceable)

        if best is None:
            continue

        overlap, partner, traceable = best

        if traceable:
            severity, kind = "critical", "defect"
            detail = (
                f'The line on {cell.sheet} says: "{row_text.strip()[:150]}". '
                f"The same amount appears on {partner.sheet} under a different "
                f"name, and the two feed different subtotals that both flow into "
                f"the grand total. Somebody already knew this money was shared, "
                f"and it was counted anyway."
            )
        else:
            # Untraceable, and we say so rather than either staying quiet or
            # asserting a double-count we cannot demonstrate.
            severity, kind = "high", "decision"
            detail = (
                f'The line on {cell.sheet} says: "{row_text.strip()[:150]}". '
                f"The same amount appears on {partner.sheet} under a different "
                f"name.\n\n"
                f"We cannot prove whether it reaches the grand total once or twice, "
                f"because the subtotals on these sheets are typed-in numbers rather "
                f"than formulas — see the hardcoded_rollup findings. The workbook "
                f"cannot answer this question about itself. Somebody has to check "
                f"whether both category totals include this figure."
            )

        out.append(Finding(
            finding_type="intra_doc_double_count",
            kind=kind,
            severity=severity,
            title=(
                f"{cell.value_num:,.2f} appears on both {cell.sheet} and "
                f"{partner.sheet} — and a note says it is shared"
            ),
            detail=detail,
            impact_usd=cell.value_num,
            evidence=[_ev(doc, cell), _ev(doc, partner)],
            data={
                "amount": cell.value_num,
                "description_overlap": round(overlap, 3),
                "routing_traceable": traceable,
            },
        ))

    return out


def detect_arithmetic_mismatch(doc: str, cells: list[Cell], g: FormulaGraph) -> list[Finding]:
    """extended != qty * unit, where all three are literals typed by a human."""
    out: list[Finding] = []
    by_row: dict[tuple[str, int], list[Cell]] = {}
    for c in cells:
        if c.value_num is not None and not c.is_formula:
            by_row.setdefault((c.sheet, c.row), []).append(c)

    for (sheet, row), row_cells in sorted(by_row.items()):
        nums = sorted(row_cells, key=lambda c: c.col)
        if len(nums) < 3:
            continue
        for i in range(len(nums) - 2):
            qty, unit, ext = nums[i], nums[i + 1], nums[i + 2]
            if qty.value_num is None or unit.value_num is None or ext.value_num is None:
                continue
            if qty.value_num <= 0 or unit.value_num <= 0 or ext.value_num <= 0:
                continue
            expected = qty.value_num * unit.value_num
            if expected == 0 or abs(expected - ext.value_num) <= 0.01:
                continue
            # Only flag when it *nearly* works — a coincidental triple of unrelated
            # numbers should not produce noise.
            if not (0.5 <= ext.value_num / expected <= 2.0):
                continue
            out.append(Finding(
                finding_type="arithmetic_mismatch",
                kind="defect",
                severity="medium",
                title=f"{sheet} row {row}: extended price does not equal qty x unit",
                detail=(
                    f"{qty.value_num:g} x {unit.value_num:,.2f} = {expected:,.2f}, "
                    f"but the row carries {ext.value_num:,.2f}. All three are typed "
                    f"literals, so nothing will ever recompute this."
                ),
                impact_usd=abs(expected - ext.value_num),
                evidence=[_ev(doc, ext)],
                data={"qty": qty.value_num, "unit": unit.value_num,
                      "stated": ext.value_num, "expected": expected},
            ))
            break
    return out


def detect_capex_opex_conflation(doc: str, cells: list[Cell]) -> list[Finding]:
    """A recurring cost sitting in a one-time table.

    A monthly line booked once understates the multi-year commitment by however
    many months nobody multiplied by — and it is the kind of error that only
    surfaces after the money has been approved.
    """
    out: list[Finding] = []
    for cell in cells:
        if cell.value_num is None or cell.value_num <= 0:
            continue
        row = _row_text(cells, cell.sheet, cell.row)
        if not _RECURRING_RE.search(row):
            continue
        out.append(Finding(
            finding_type="capex_opex_conflation",
            kind="risk",
            severity="high",
            title=f"{cell.sheet} row {cell.row}: a recurring cost in a capital table",
            detail=(
                "This line describes a repeating charge but is carried as a single "
                "amount. Confirm the term it should be multiplied by before this "
                "total is presented as the cost of ownership."
            ),
            impact_usd=None,   # we do not know the term; inventing one is worse
            evidence=[Evidence(
                source_document=doc, sheet=cell.sheet,
                locator=cell.locator, raw_text=row[:200],
            )],
            data={"amount": cell.value_num},
        ))
        break_row = (cell.sheet, cell.row)
        cells = [c for c in cells if (c.sheet, c.row) != break_row]
    return out


def detect_unresolved_placeholders(doc: str, cells: list[Cell]) -> list[Finding]:
    out: list[Finding] = []
    for cell in cells:
        if not cell.value_text or not _PLACEHOLDER_RE.search(cell.value_text):
            continue
        out.append(Finding(
            finding_type="unresolved_placeholder",
            kind="risk",
            severity="medium",
            title=f"{cell.sheet}!{cell.locator} is still unfinished",
            detail=(
                f'"{cell.value_text.strip()[:80]}" — left in a document that is '
                "being costed. Somebody intended to come back to it."
            ),
            evidence=[_ev(doc, cell)],
        ))
    return out


# ── Entry point ──────────────────────────────────────────────────────────────

def analyze_document(extraction: GridExtraction) -> list[Finding]:
    """Every deterministic finding in one document. No model runs."""
    doc = extraction.filename
    cells = extraction.cells
    if not cells:
        return []

    g = build_graph(cells)

    findings: list[Finding] = []
    findings += detect_unpriced_zeroed(doc, g)
    findings += detect_hardcoded_rollup(doc, g)
    findings += detect_stale_rollup(doc, g)
    findings += detect_intra_doc_double_count(doc, cells, g)
    findings += detect_crossref_double_count(doc, cells, g)
    findings += detect_capex_opex_conflation(doc, list(cells))
    findings += detect_unresolved_placeholders(doc, cells)

    # detect_arithmetic_mismatch is deliberately NOT wired in yet.
    #
    # It has to know which column is quantity, which is unit price and which is
    # extended — and until header detection lands it is guessing from the position
    # of numbers in a row. On real evidence that guess produced sixteen confident
    # accusations about arithmetic that was perfectly correct.
    #
    # A detector that is right sixteen times and wrong sixteen times is not half a
    # detector; it is a liability, because the reviewer now has to check our work
    # as well as theirs. It comes back when the extractor can tell it what the
    # columns MEAN (bom-extract-01, header mapping).

    # Same figure, two occurrences, but reached by two different detectors: keep
    # the more specific one. The cross-reference version quotes a human admitting
    # the money is shared, which is a strictly better thing to show a reviewer.
    seen_pairs: set[frozenset[tuple[str, str]]] = set()
    deduped: list[Finding] = []
    for f in findings:
        if f.finding_type != "intra_doc_double_count":
            deduped.append(f)
            continue
        pair = frozenset((e.sheet, e.locator) for e in f.evidence)
        if pair in seen_pairs:
            continue
        seen_pairs.add(pair)
        deduped.append(f)
    findings = deduped

    # Most severe first, then by money. An executive reads until they stop, so
    # the order is part of the product.
    findings.sort(
        key=lambda f: (C.SEVERITY_RANK[f.severity], -(f.impact_usd or 0.0))
    )
    return findings


def main() -> int:  # pragma: no cover
    import argparse
    import json

    from tools.bom.extract_grid import extract_grid

    ap = argparse.ArgumentParser(description="Deterministic BOM findings. No LLM.")
    ap.add_argument("path")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    findings = analyze_document(extract_grid(args.path))

    if args.json:
        print(json.dumps([
            {
                "type": f.finding_type, "kind": f.kind, "severity": f.severity,
                "title": f.title, "detail": f.detail, "impact_usd": f.impact_usd,
                "detector": f.detector, "fingerprint": f.fingerprint,
                "evidence": [e.as_dict() for e in f.evidence],
            }
            for f in findings
        ], indent=2))
        return 0

    for f in findings:
        money = f"${f.impact_usd:,.0f}" if f.impact_usd else "—"
        print(f"[{f.severity:<8}] {money:>12}  {f.title}")
        print(f"             {f.detail}")
        for e in f.evidence:
            print(f"             -> {e.source_document}!{e.sheet}!{e.locator}")
        print()
    print(f"{len(findings)} findings, all deterministic.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
