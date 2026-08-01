# CUI // SP-CTI
"""Which cells feed which totals.

``=SUM(F4:F26)`` says "these cells roll up into this one". Knowing that is the
entire difference between two claims that look identical in a spreadsheet:

    "this licence has been counted twice"      — the money is double-booked
    "we are buying two of them"                — the money is correct

The same item, appearing on two sheets at the same price, is *both* of those
depending on which subtotals consume it. If the two occurrences feed **different**
rollups that both flow into the grand total, the money lands in the total twice.
If they feed the **same** rollup, it is a genuine quantity of two.

No amount of reading the rendered values recovers that distinction. It exists only
in the formulas, which is why the extractor keeps them and why this module exists.

Public API::

    build_graph(cells) -> FormulaGraph
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from tools.bom.extract_grid import Cell

# Functions that aggregate other cells. A cell computed with one of these is a
# subtotal, and the cells inside its arguments are what it is a subtotal *of*.
_ROLLUP_FUNCS = ("SUM", "SUBTOTAL", "SUMIF", "SUMIFS", "SUMPRODUCT", "AGGREGATE")

_FUNC_RE = re.compile(r"\b(" + "|".join(_ROLLUP_FUNCS) + r")\s*\(", re.IGNORECASE)

# A1, $A$1, Sheet1!A1, 'Sheet Name'!A1:B10 — and the range forms of each.
_REF_RE = re.compile(
    r"""
    (?: (?: '(?P<qsheet>[^']+)' | (?P<sheet>[A-Za-z0-9_.\- ]+) ) ! )?   # optional sheet
    \$?(?P<c1>[A-Z]{1,3})\$?(?P<r1>\d{1,7})                              # first cell
    (?: \s*:\s* \$?(?P<c2>[A-Z]{1,3})\$?(?P<r2>\d{1,7}) )?               # optional range end
    """,
    re.VERBOSE,
)

# Text inside quotes is a criterion, not a reference. SUMIF(F3:F26,"<>—") has a
# "—" that must never be mistaken for a cell.
_STRING_RE = re.compile(r'"[^"]*"')

_MAX_RANGE_CELLS = 50_000   # a runaway A:A reference must not hang the run


def _col_to_num(col: str) -> int:
    n = 0
    for ch in col.upper():
        n = n * 26 + (ord(ch) - 64)
    return n


def _num_to_col(n: int) -> str:
    out = ""
    while n > 0:
        n, rem = divmod(n - 1, 26)
        out = chr(65 + rem) + out
    return out


@dataclass
class Ref:
    sheet: str          # "" means "the sheet the formula lives on"
    start: str          # "F4"
    end: str = ""       # "F26" for a range, "" for a single cell

    def expand(self, default_sheet: str) -> list[tuple[str, str]]:
        """(sheet, locator) for every cell this reference covers."""
        sheet = self.sheet or default_sheet
        if not self.end:
            return [(sheet, self.start)]

        m1 = re.fullmatch(r"([A-Z]{1,3})(\d+)", self.start)
        m2 = re.fullmatch(r"([A-Z]{1,3})(\d+)", self.end)
        if not m1 or not m2:
            return []

        c1, r1 = _col_to_num(m1.group(1)), int(m1.group(2))
        c2, r2 = _col_to_num(m2.group(1)), int(m2.group(2))
        c1, c2 = min(c1, c2), max(c1, c2)
        r1, r2 = min(r1, r2), max(r1, r2)

        if (c2 - c1 + 1) * (r2 - r1 + 1) > _MAX_RANGE_CELLS:
            return []

        return [
            (sheet, f"{_num_to_col(c)}{r}")
            for c in range(c1, c2 + 1)
            for r in range(r1, r2 + 1)
        ]


def parse_refs(formula: str) -> list[Ref]:
    """Every cell reference in a formula.

    Quoted strings are stripped first: a SUMIF criterion is an argument, not an
    address, and reading one as a reference would wire the graph to a cell that
    does not exist.
    """
    body = _STRING_RE.sub('""', formula.lstrip("="))
    refs: list[Ref] = []
    for m in _REF_RE.finditer(body):
        sheet = m.group("qsheet") or m.group("sheet") or ""
        # A bare function name can look like a column ("SUM" is not A1 notation,
        # but a stray token could be). Requiring a row number already filters
        # most of it; guard the rest by rejecting anything that is immediately
        # followed by "(".
        end = m.end()
        if end < len(body) and body[end] == "(":
            continue
        refs.append(
            Ref(
                sheet=sheet.strip(),
                start=f"{m.group('c1').upper()}{m.group('r1')}",
                end=f"{m.group('c2').upper()}{m.group('r2')}" if m.group("c2") else "",
            )
        )
    return refs


def is_rollup(formula: str) -> bool:
    return bool(_FUNC_RE.search(formula or ""))


@dataclass
class FormulaGraph:
    # (sheet, locator) -> the cells it consumes
    consumes: dict[tuple[str, str], set[tuple[str, str]]] = field(default_factory=dict)
    # (sheet, locator) -> the rollup cells that consume IT
    consumed_by: dict[tuple[str, str], set[tuple[str, str]]] = field(default_factory=dict)
    # Every cell computed with an aggregating function.
    rollups: set[tuple[str, str]] = field(default_factory=set)
    cells: dict[tuple[str, str], Cell] = field(default_factory=dict)

    def ancestors(self, key: tuple[str, str]) -> set[tuple[str, str]]:
        """Every rollup that this cell's money eventually flows into.

        Walks up transitively, because a grand total is usually a sum of
        subtotals rather than of line items. Cycle-safe: a circular reference is
        an Excel error, not a reason to hang.
        """
        seen: set[tuple[str, str]] = set()
        stack = list(self.consumed_by.get(key, ()))
        while stack:
            node = stack.pop()
            if node in seen:
                continue
            seen.add(node)
            stack.extend(self.consumed_by.get(node, ()))
        return seen

    def is_hardcoded_sibling(self, key: tuple[str, str]) -> bool:
        """A typed-in number sitting where its neighbours hold formulas.

        In a summary block, a cell that is a literal while the cells above and
        below it are computed will not move when the sheets it claims to total
        are edited. The number goes stale and the workbook keeps presenting it
        with total confidence.
        """
        cell = self.cells.get(key)
        if cell is None or cell.is_formula or cell.value_num is None:
            return False

        sheet, loc = key
        m = re.fullmatch(r"([A-Z]{1,3})(\d+)", loc)
        if not m:
            return False
        col, row = m.group(1), int(m.group(2))

        neighbours = [
            self.cells.get((sheet, f"{col}{row + delta}"))
            for delta in (-2, -1, 1, 2)
        ]
        # ANY formula neighbour counts, not just SUM(). Real summary blocks are
        # full of plain addition — "=Compute!E9+Compute!E16" — and requiring an
        # aggregating function here misses every one of them. The tell is not
        # which function was used; it is that the cells around this one are
        # COMPUTED and this one is typed.
        formula_neighbours = [
            n for n in neighbours
            if n is not None and n.is_formula and self.consumes.get((n.sheet, n.locator))
        ]
        # Two computed siblings is enough to call it a block. One could be
        # coincidence; none means this is just a column of data.
        return len(formula_neighbours) >= 2


def build_graph(cells: list[Cell]) -> FormulaGraph:
    g = FormulaGraph()
    g.cells = {(c.sheet, c.locator): c for c in cells}

    for cell in cells:
        if not cell.is_formula:
            continue
        key = (cell.sheet, cell.locator)
        rollup = is_rollup(cell.formula)
        if rollup:
            g.rollups.add(key)

        targets: set[tuple[str, str]] = set()
        for ref in parse_refs(cell.formula):
            for sheet, loc in ref.expand(cell.sheet):
                targets.add((sheet, loc))
        targets.discard(key)   # a self-reference is an Excel error, not an edge

        if targets:
            g.consumes[key] = targets
            for t in targets:
                g.consumed_by.setdefault(t, set()).add(key)

    return g


def missing_operands(graph: FormulaGraph, key: tuple[str, str]) -> list[tuple[str, str]]:
    """Cells a formula multiplies by that do not exist.

    This is the durable signal behind a line that looks costed and costs nothing.
    It survives where the cached value does not: a workbook written by a script
    has formulas but has never evaluated them, so ``data_only=True`` yields None
    and there is no zero to notice. The formula referencing an empty cell is
    present either way.
    """
    return [
        ref for ref in graph.consumes.get(key, ())
        if ref not in graph.cells
    ]
