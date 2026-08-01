# CUI // SP-CTI
"""Turning a grid of cells into line items that mean something.

The engine has been able to read every cell in a workbook for a while. What it
could not do is say which column was the QUANTITY and which was the PRICE — and
without that, checking arithmetic is reduced to guessing from where numbers
happen to sit in a row. That guess produces confident accusations about
arithmetic that was perfectly correct, which is worse than saying nothing at all.

So: find the header row, work out what each column MEANS, and only then read the
rows beneath it as line items.

Two rules run through this whole module.

**A sheet with no recognisable header yields no lines.** Not "best effort" lines,
not "probably the second column is the price" lines. None. A schema this engine
invented is a schema nobody can check, and every downstream number would inherit
the invention.

**price_basis is resolved from evidence, never assumed.** The order is: what a
human declared, then what the FORMULAS prove (a column computed as ``=F4*0.6``
under a "street" heading is telling you, in the workbook's own hand, that its
input column is a list price), then the header wording, then ``unknown`` — and
``unknown`` is a finding rather than a default, because averaging a list price
against somebody's rough estimate produces a number that is not so much wrong as
meaningless.

Public API::

    extract_lines(extraction, source_id=..., ...) -> list[ExtractedLine]
"""
from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from tools.bom import constants as C
from tools.bom.extract_grid import Cell, GridExtraction
from tools.bom.formula_graph import FormulaGraph, is_rollup
from tools.bom.matching import looks_like_part_number

_CONFIG_PATH = Path(__file__).resolve().parents[2] / "args" / "bom_columns.yaml"
_CONFIG: dict[str, Any] | None = None

_NORM = re.compile(r"[^a-z0-9 /#.]+")
_WS = re.compile(r"\s+")

# "=F4*0.6" — a column that is another column times a constant under one. The
# workbook is telling you that the input is a list price and this is the discount.
_DISCOUNT = re.compile(r"^=\s*\$?[A-Z]{1,3}\$?\d+\s*\*\s*(0?\.\d+)\s*$", re.IGNORECASE)

# A total row is not a line item. Summing it back in double-counts everything
# above it.
_TOTAL_ROW = re.compile(
    # "Subtotal" has NO word boundary before "total" — b-t is inside a word — so
    # `\btotal\b` can never see it. A row labelled "2.2 Test Bench — Subtotal"
    # sailed straight through into the line items, where its money was counted a
    # second time. The bug hides in plain sight because the row LOOKS like a
    # heading rather than a total.
    r"\bsub[\s-]?total\b|\bgrand\s+total\b|\btotal\s*$|^\s*total\b",
    re.IGNORECASE,
)


def load_config(path: Path | None = None) -> dict[str, Any]:
    global _CONFIG
    if path is not None:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    if _CONFIG is None:
        _CONFIG = yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8"))
    return _CONFIG


def _norm(text: str) -> str:
    return _WS.sub(" ", _NORM.sub(" ", (text or "").lower())).strip()


@dataclass
class Header:
    """Which column is which, on one sheet."""

    row: int
    columns: dict[str, int] = field(default_factory=dict)   # role -> col index
    score: int = 0

    def col(self, role: str) -> int | None:
        return self.columns.get(role)


@dataclass
class ExtractedLine:
    line_id: str
    line_hash: str
    source_document: str
    source_sheet: str
    source_locator: str
    raw_text: str

    description: str = ""
    part_number: str = ""
    manufacturer: str = ""
    notes: str = ""

    qty: float | None = None
    uom: str = ""
    unit_price: float | None = None
    extended_price: float | None = None
    computed_extended: float | None = None
    discount_pct: float | None = None

    price_basis: str = C.DEFAULT_PRICE_BASIS
    price_basis_reason: str = ""
    price_missing: bool = False

    source_formula: str = ""
    value_is_formula: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()}


def _match_role(header_text: str, cfg: dict) -> str | None:
    """Which role does this header name?

    Longest synonym first, so "unit price" beats "price" and "extended price"
    beats both. Ordering matters more than it looks: a BOM routinely has three
    columns whose names all contain the word "price", and picking the wrong one
    silently swaps the unit cost for the line total.
    """
    text = _norm(header_text)
    if not text:
        return None

    best: tuple[int, str] | None = None
    for role, synonyms in cfg["columns"].items():
        for syn in synonyms:
            s = _norm(syn)
            if not s:
                continue
            if text == s or s in text:
                if best is None or len(s) > best[0]:
                    best = (len(s), role)
    return best[1] if best else None


def find_header(cells: list[Cell], sheet: str, cfg: dict) -> Header | None:
    """The row that names the columns.

    Real workbooks put a title, a logo and a revision block above the header, so
    this scans down rather than assuming row one. It returns None when nothing on
    the sheet looks like a header — and that is a real answer. Guessing a schema
    would make every number downstream inherit the guess.
    """
    by_row: dict[int, list[Cell]] = defaultdict(list)
    for c in cells:
        if c.sheet == sheet and c.value_text:
            by_row[c.row].append(c)

    best: Header | None = None
    for row in sorted(by_row)[: cfg["max_header_scan_rows"]]:
        columns: dict[str, int] = {}
        for cell in by_row[row]:
            # A header is text. A row of numbers is data, however suggestive.
            if cell.value_num is not None:
                continue
            role = _match_role(cell.value_text, cfg)
            if role and role not in columns:
                columns[role] = cell.col

        # A sheet is a bill of materials only if it can both NAME a thing and PRICE
        # it. Both halves are required, and the requirement is what keeps this from
        # reading every table in the workbook as a BOM.
        #
        # A power schedule ("QTY | AMP | Connection | Max Watt | Total Usable kW")
        # has a quantity and something called a total, and an earlier version duly
        # turned its rows into line items — inventing priced components out of an
        # electrical calculation. Those lines then went looking for a price they
        # never had and reported themselves as costing nothing.
        #
        # Every table has numbers. Only a BOM says what you are buying.
        names_a_thing = any(
            r in columns for r in ("description", "part_number", "manufacturer")
        )
        prices_it = any(r in columns for r in ("unit_price", "extended_price"))
        if not (names_a_thing and prices_it):
            continue

        _disambiguate_by_content(columns, cells, sheet, row)

        score = len(columns)
        if "description" in columns:
            score += 1

        if score >= cfg["min_header_score"] and (best is None or score > best.score):
            best = Header(row=row, columns=columns, score=score)

    return best


def _disambiguate_by_content(
    columns: dict[str, int], cells: list[Cell], sheet: str, header_row: int
) -> None:
    """Some headers cannot be resolved by their wording. Look at what is under them.

    "Unit" is the case that forced this. It is a perfectly good name for the unit
    OF MEASURE ("each", "hour", "seat") and an equally good name for the unit
    PRICE, and no amount of staring at the word decides which. Resolving it by the
    order the synonyms happen to sit in a config file is not a decision, it is a
    coin toss — and when it landed wrong the price column went unmapped and EVERY
    row reported itself as having no price at all.

    The column's contents settle it in a way the header never can: a column of
    numbers under "Unit" is money; a column of words is a unit of measure.
    """
    uom_col = columns.get("unit_of_measure")
    if uom_col is None or "unit_price" in columns:
        return

    values = [
        c for c in cells
        if c.sheet == sheet and c.col == uom_col and c.row > header_row
        and (c.value_text or c.value_num is not None)
    ]
    if not values:
        return

    numeric = sum(1 for c in values if c.value_num is not None)
    if numeric / len(values) >= 0.6:
        columns["unit_price"] = uom_col
        del columns["unit_of_measure"]


def _resolve_price_basis(
    header: Header,
    cells_by_col: dict[int, list[Cell]],
    cfg: dict,
    declared: str = "",
) -> tuple[str, str]:
    """What KIND of price is in the price column.

    Evidence first, wording second, and never a convenient default.
    """
    if declared and declared in C.PRICE_BASES:
        return declared, "declared at upload"

    # ── Formula evidence. The workbook's own hand. ──────────────────────────
    #
    # A column computed as "=F4*0.6" under a heading like "street" is stating,
    # unambiguously, that column F is a LIST price and this one is that list with
    # a discount applied. Nothing a header says is stronger than the arithmetic
    # somebody actually wrote.
    street_col = header.col("street_price")
    if street_col is not None:
        for cell in cells_by_col.get(street_col, []):
            m = _DISCOUNT.match(cell.formula or "")
            if m:
                pct = float(m.group(1))
                return "msrp", (
                    f"the street column is computed as list x {pct:g} "
                    f"({cell.formula}), so the price column is a list price"
                )

    # ── Header wording ──────────────────────────────────────────────────────
    for basis, words in cfg["price_basis_headers"].items():
        for role in ("unit_price", "extended_price"):
            col = header.col(role)
            if col is None:
                continue
            head = next(
                (c.value_text for c in cells_by_col.get(col, []) if c.row == header.row),
                "",
            )
            if any(_norm(w) in _norm(head) for w in words):
                return basis, f'the column is headed "{head.strip()}"'

    # ── Nothing. And that is a finding, not a default. ──────────────────────
    return "unknown", (
        "nothing in this sheet says what kind of price this is — refusing to "
        "assume one, because averaging a list price against a rough estimate "
        "produces a number that is not so much wrong as meaningless"
    )


def _cell_at(cells_by_row: dict[int, dict[int, Cell]], row: int, col: int | None):
    if col is None:
        return None
    return cells_by_row.get(row, {}).get(col)


def _text(cell: Cell | None) -> str:
    return (cell.value_text or "").strip() if cell else ""


def _num(cell: Cell | None) -> float | None:
    return cell.value_num if cell else None


def extract_lines(
    extraction: GridExtraction,
    *,
    source_id: str = "",
    declared_price_basis: str = "",
    config_path: Path | None = None,
    graph: FormulaGraph | None = None,
) -> list[ExtractedLine]:
    """Read a document's line items, once we know what its columns mean."""
    cfg = load_config(config_path)
    cells = extraction.cells
    if not cells:
        return []

    # The graph is not needed for reading lines — is_rollup() answers the only
    # question we ask of a formula here. Accepted as a parameter so a caller that
    # has already built one does not pay for it twice.
    del graph
    out: list[ExtractedLine] = []
    source_id = source_id or extraction.filename

    for sheet in dict.fromkeys(c.sheet for c in cells):
        sheet_cells = [c for c in cells if c.sheet == sheet]
        header = find_header(sheet_cells, sheet, cfg)
        if header is None:
            # No recognisable header. This sheet yields NOTHING, on purpose: a
            # schema we invented is a schema nobody can check.
            continue

        cells_by_col: dict[int, list[Cell]] = defaultdict(list)
        cells_by_row: dict[int, dict[int, Cell]] = defaultdict(dict)
        for c in sheet_cells:
            cells_by_col[c.col].append(c)
            cells_by_row[c.row][c.col] = c

        basis, basis_reason = _resolve_price_basis(
            header, cells_by_col, cfg, declared_price_basis
        )

        desc_col = header.col("description")
        for row in sorted(r for r in cells_by_row if r > header.row):
            row_cells = cells_by_row[row]

            desc = _text(_cell_at(cells_by_row, row, desc_col))
            if not desc:
                # No description column at all is COMMON and perfectly valid — a
                # BOM headed "Manufacturer | Model | QTY | Price | Total" names its
                # items across two columns and never uses the word "description".
                # An earlier version only fell back when a description column
                # existed but was empty, so those sheets yielded zero lines: an
                # entire authoritative BOM read as blank, silently.
                texts = [
                    c for c in sorted(row_cells.values(), key=lambda c: c.col)
                    if c.value_text and c.value_num is None
                ]
                desc = " ".join(t.value_text.strip() for t in texts[:2]).strip()
            if len(desc) < 3:
                continue

            # A total row is not a line item. Reading it as one and adding it back
            # in double-counts everything above it — and the mistake is invisible,
            # because the number it produces looks exactly like the number it
            # should have produced, only twice.
            if _TOTAL_ROW.search(desc):
                continue

            qty = _num(_cell_at(cells_by_row, row, header.col("quantity")))
            unit = _num(_cell_at(cells_by_row, row, header.col("unit_price")))
            ext_cell = _cell_at(cells_by_row, row, header.col("extended_price"))
            ext = _num(ext_cell)

            # The other kind of total row, and the one that gets past a text check:
            # a section subtotal whose label is a heading ("1.1 Compute Hosts")
            # rather than the word "total". Its price cell is a SUM, and that is a
            # fact rather than a guess — a rollup is not a line item, whatever it
            # is called.
            if ext_cell is not None and is_rollup(ext_cell.formula):
                continue

            # Nothing numeric anywhere on the row: a section heading, not an item.
            if qty is None and unit is None and ext is None:
                continue

            part = _text(_cell_at(cells_by_row, row, header.col("part_number")))
            if not part:
                part = next(
                    (
                        c.value_text.strip()
                        for c in sorted(row_cells.values(), key=lambda c: c.col)
                        if c.value_text and looks_like_part_number(c.value_text)
                    ),
                    "",
                )

            computed = qty * unit if (qty is not None and unit is not None) else None

            # A quantity, a formula, and no price. The line looks costed and costs
            # nothing — and the total is understated by however much the item is
            # actually worth.
            missing = bool(
                qty
                and unit is None
                and ext_cell is not None
                and ext_cell.is_formula
                and not is_rollup(ext_cell.formula)
            )

            raw = " | ".join(
                c.value_text for c in sorted(row_cells.values(), key=lambda c: c.col)
                if c.value_text
            )
            locator = (
                ext_cell.locator if ext_cell
                else next(iter(sorted(row_cells)), "") and
                row_cells[min(row_cells)].locator
            )

            # Hashed from the bytes as they ARRIVED — never from a parsed field.
            # A human's merge approval is keyed on this, and if improving the
            # parser rewrote the hash we would silently orphan every decision the
            # customer ever made.
            line_hash = hashlib.sha256(
                "\x1f".join((source_id, f"{sheet}!{locator}", raw)).encode()
            ).hexdigest()

            out.append(ExtractedLine(
                line_id=f"{source_id}:{sheet}:{row}",
                line_hash=line_hash,
                source_document=extraction.filename,
                source_sheet=sheet,
                source_locator=locator,
                raw_text=raw,
                description=desc,
                part_number=part,
                manufacturer=_text(_cell_at(cells_by_row, row, header.col("manufacturer"))),
                notes=_text(_cell_at(cells_by_row, row, header.col("notes"))),
                qty=qty,
                uom=_text(_cell_at(cells_by_row, row, header.col("unit_of_measure"))),
                unit_price=unit,
                extended_price=ext,
                computed_extended=computed,
                price_basis=basis,
                price_basis_reason=basis_reason,
                price_missing=missing,
                source_formula=(ext_cell.formula if ext_cell else ""),
                value_is_formula=bool(ext_cell and ext_cell.is_formula),
            ))

    return out


def main() -> int:  # pragma: no cover
    import argparse
    import json

    from tools.bom.extract_grid import extract_grid

    ap = argparse.ArgumentParser(description="Read a document's line items.")
    ap.add_argument("path")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    g = extract_grid(args.path)
    lines = extract_lines(g)

    if args.json:
        print(json.dumps([ln.as_dict() for ln in lines], indent=2, default=str))
        return 0

    print(f"{g.filename}: {len(lines)} line items")
    for ln in lines[:40]:
        money = f"{ln.extended_price:,.2f}" if ln.extended_price else "—"
        print(f"  {ln.source_sheet}!{ln.source_locator:<6} {ln.description[:44]:<46} "
              f"{money:>12}  [{ln.price_basis}]")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
