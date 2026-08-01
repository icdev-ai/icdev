# CUI // SP-CTI
"""The same document, twice, in two formats.

Somebody emails the workbook and the PDF they printed from it. Both land in the
folder. Ingest both naively and every figure in that BOM is counted twice — not
approximately, exactly: the whole document, doubled, with no warning and nothing
on screen to suggest anything is wrong. It is the easiest way there is to be
catastrophically and confidently wrong about a budget.

Detection is by CONTENT, never by filename. "Report.pdf" and "Report Final
v2.pdf" may be unrelated; "q3.xlsx" and "budget_print.pdf" may be the same
document. What settles it is whether the rows say the same things.

Two things make this harder than it sounds, and both were learned the hard way:

**Numbers must be compared as numbers.** A workbook stores ``6400``; its PDF
renders ``$6,400.00``. Normalising the printed string gives ``640000``, which
matches nothing, and two copies of the same document share exactly zero rows.

**Rows do not survive printing intact.** A PDF table extractor merges and drops
cells, so a printed row is a SUBSET of the row it came from — never an exact copy
of it. Matching on equality finds nothing. Rows have to be matched by containment.

When two copies really are the same document, keep the one that can still be
argued with. A workbook has its formulas; a PDF of that workbook has already had
the arithmetic flattened out of it — which is exactly where the errors were
hiding. The loser is kept for audit and EXCLUDED from every rollup: it is the same
money, not deprioritised money.

Public API::

    find_derivatives(extractions) -> list[Derivation]
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from tools.bom.constants import DERIVATIVE_OVERLAP, REPRESENTATION_FIDELITY
from tools.bom.extract_grid import GridExtraction

_NOISE = re.compile(r"[^a-z0-9]+")

# A row has to carry enough to be worth comparing. A lone number tells you
# nothing about whether two documents are related.
_MIN_ROW_TOKENS = 2
_MIN_SHARED_ROWS = 3

# How much of a printed row must survive in its original for the two to be the
# same row. Generous, because printing loses cells.
_ROW_MATCH = 0.6

# A row that matches only on short numeric tokens ("1.00", "2.00") is noise —
# every table has those. At least one token has to be distinctive.
_DISTINCTIVE = 6


@dataclass
class Derivation:
    """One document is a re-representation of another."""

    derived: str
    original: str
    overlap: float
    shared_rows: int
    reason: str
    # True when more than one document could equally be the original. It does not
    # change the safe action — a copy is excluded from the rollups either way —
    # but the PROVENANCE is then a guess, and a guess must be labelled as one.
    ambiguous: bool = False
    candidates: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "derived": self.derived,
            "original": self.original,
            "overlap": round(self.overlap, 3),
            "shared_rows": self.shared_rows,
            "reason": self.reason,
            "ambiguous": self.ambiguous,
            "candidates": self.candidates,
        }


def _token(cell) -> str:
    """One cell, normalized so it survives a trip through a printer.

    Numbers go through ``value_num``, NOT their rendered text — otherwise a
    workbook's ``6400`` and its PDF's ``$6,400.00`` are different strings and the
    duplicate sails through undetected. Compare what the cell MEANS, not how it
    was printed.
    """
    if cell.value_num is not None:
        return f"{cell.value_num:.2f}"
    return _NOISE.sub("", (cell.value_text or "").lower())


def _rows(extraction: GridExtraction) -> list[frozenset[str]]:
    """Each row of content as a set of normalized tokens.

    Rows with nothing distinctive in them are dropped entirely — not just skipped
    when matching, but excluded from the denominator too. A header row
    ("Item | Qty | Cost") is identical in every BOM ever written: it can never
    prove two documents are related, so it is not evidence FOR a match. And
    because it can never match, leaving it in the denominator makes it evidence
    AGAINST one — which is worse than useless. It is evidence of nothing, and it
    is counted as nothing.
    """
    grouped: dict[tuple[str, int], set[str]] = {}
    for c in extraction.cells:
        if not c.value_text and c.value_num is None:
            continue
        tok = _token(c)
        if tok:
            grouped.setdefault((c.sheet, c.row), set()).add(tok)

    rows = [
        frozenset(v) for v in grouped.values()
        if len(v) >= _MIN_ROW_TOKENS and any(len(t) >= _DISTINCTIVE for t in v)
    ]

    # A diagram carries no cells — its content is node labels. Without this, a
    # .drawio and the PDF somebody exported from it look like two entirely
    # unrelated documents.
    labels = {
        _NOISE.sub("", (n.get("label") or "").lower())
        for n in extraction.nodes
    }
    labels = {label for label in labels if len(label) >= _DISTINCTIVE}
    if labels:
        rows += [frozenset({label}) for label in labels]

    return rows


def _row_matches(a: frozenset[str], b: frozenset[str]) -> bool:
    """Is this the same row, seen through two different renderers?

    Containment, not equality. A PDF table extractor merges and drops cells, so a
    printed row is a subset of the row it came from and never an exact copy of it.
    Demanding equality finds nothing at all — the two copies of a document that IS
    a copy share zero identical rows.
    """
    shared = a & b
    if not shared:
        return False
    if len(shared) / min(len(a), len(b)) < _ROW_MATCH:
        return False
    # At least one token has to be distinctive. Rows that agree only on "1.00"
    # and "2.00" agree on nothing: every table in the world contains those.
    return any(len(t) >= _DISTINCTIVE for t in shared)


def _containment(small: list[frozenset[str]], large: list[frozenset[str]]) -> tuple[float, int]:
    """How much of the smaller document already exists inside the larger one.

    Containment, not symmetric Jaccard. A print of two sheets out of a ten-sheet
    workbook shares a small fraction of the workbook's rows, so Jaccard is low and
    the pair is missed — while the print is nonetheless entirely CONTAINED in the
    workbook, which is exactly what "derived" means.
    """
    if not small:
        return 0.0, 0
    hits = sum(1 for row in small if any(_row_matches(row, other) for other in large))
    return hits / len(small), hits


def _fidelity(extraction: GridExtraction) -> int:
    return REPRESENTATION_FIDELITY.get(extraction.representation, 0)


def _name_affinity(a: str, b: str) -> float:
    """Shared leading characters of two filenames, normalized.

    A TIEBREAK ONLY, and it earns its place.

    When two originals are equally good matches — which happens when the corpus
    holds two near-identical variants of the same design — content cannot tell
    them apart, because content is what makes them near-identical. Attributing a
    printed copy to the wrong variant would put it in the wrong option group, and
    an option group is a thing a human is going to make a funding decision from.

    Filenames are a weak signal and are never allowed to DECIDE whether something
    is a copy. But between two candidates the content has already declared
    indistinguishable, the fact that one shares a name with the copy is real
    evidence, and refusing to use it would be fastidiousness at the customer's
    expense.
    """
    x = _NOISE.sub("", a.lower())
    y = _NOISE.sub("", b.lower())
    if not x or not y:
        return 0.0
    n = 0
    for ca, cb in zip(x, y):
        if ca != cb:
            break
        n += 1
    return n / min(len(x), len(y))


def find_derivatives(extractions: list[GridExtraction]) -> list[Derivation]:
    """Which of these documents are copies of which others.

    At most one Derivation per copy. A document cannot be derived from two things
    at once, and emitting several attributions for the same file leaves whoever
    reads the register to pick one at random.
    """
    candidates = _all_pairs(extractions)

    best: dict[str, Derivation] = {}
    for d in candidates:
        current = best.get(d.derived)
        if current is None or (d.overlap, d.shared_rows) > (current.overlap, current.shared_rows):
            best[d.derived] = d

    # Where several originals matched a copy equally well, say so. It does not
    # change the safe action — the copy stays out of the rollups regardless — but
    # the provenance is a guess and gets labelled as one.
    by_derived: dict[str, list[Derivation]] = {}
    for d in candidates:
        by_derived.setdefault(d.derived, []).append(d)

    out: list[Derivation] = []
    for name, chosen in best.items():
        peers = by_derived[name]
        tied = [
            p for p in peers
            if abs(p.overlap - chosen.overlap) < 0.02 and p.original != chosen.original
        ]
        if tied:
            pool = [chosen, *tied]
            # Content has already said these are indistinguishable. Fall back to
            # the name — as a tiebreak, and only here.
            pool.sort(key=lambda p: -_name_affinity(name, p.original))
            chosen = pool[0]
            chosen.ambiguous = True
            chosen.candidates = sorted(p.original for p in pool)
            chosen.reason += (
                f"\n\nProvenance is AMBIGUOUS: {len(pool)} documents in this corpus "
                f"match it equally well ({', '.join(chosen.candidates)}) — they are "
                f"near-identical variants of one another, so the content cannot tell "
                f"them apart. Attribution here is a best guess from the filename. "
                f"Excluding the copy from the rollups is correct either way; if the "
                f"originals turn out to be competing OPTIONS, which one this copy "
                f"belongs to is a decision for a human."
            )
        out.append(chosen)

    return sorted(out, key=lambda d: d.derived)


def _all_pairs(extractions: list[GridExtraction]) -> list[Derivation]:
    rows = {e.filename: _rows(e) for e in extractions}
    out: list[Derivation] = []

    for i, a in enumerate(extractions):
        for b in extractions[i + 1:]:
            ra, rb = rows[a.filename], rows[b.filename]
            if not ra or not rb:
                continue

            fa, fb = _fidelity(a), _fidelity(b)

            # SAME FORMAT IS NOT A DUPLICATE. This guard is the most important
            # line in the module.
            #
            # Two same-format documents that are 99% identical are almost never
            # one copied from the other. They are VARIANTS — the same design
            # priced two ways, the same diagram with one technology swapped for
            # another. Treating one as a copy and excluding it from the rollups
            # would silently DELETE AN OPTION the customer is still choosing
            # between, and the deck would reach leadership missing an entire
            # alternative with nothing to indicate it ever existed.
            #
            # A derivative is a change of REPRESENTATION: the same content, worse
            # structure. That always shows up as a difference in fidelity. Same
            # fidelity plus near-identical content is a DECISION for a human — an
            # option group — not something for us to quietly resolve.
            if fa == fb:
                continue

            original, derived = (a, b) if fa > fb else (b, a)
            small, large = rows[derived.filename], rows[original.filename]

            overlap, hits = _containment(small, large)
            if hits < _MIN_SHARED_ROWS or overlap < DERIVATIVE_OVERLAP:
                continue

            out.append(Derivation(
                derived=derived.filename,
                original=original.filename,
                overlap=overlap,
                shared_rows=hits,
                reason=(
                    f"{hits} of its {len(small)} content rows already exist in "
                    f"{original.filename} ({overlap:.0%}). {original.filename} is a "
                    f"{original.representation} and keeps "
                    f"{'its formulas' if original.has_formulas else 'more structure'}; "
                    f"{derived.filename} is a {derived.representation} and has already "
                    f"had the arithmetic flattened out of it. Counting both would "
                    f"double every figure in this document."
                ),
            ))

    return out


def main() -> int:  # pragma: no cover
    import argparse
    import json
    import os

    from tools.bom.extract_grid import extract_grid

    ap = argparse.ArgumentParser(description="Find documents that are copies of each other.")
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    files: list[str] = []
    for p in args.paths:
        if os.path.isdir(p):
            files += [os.path.join(p, f) for f in sorted(os.listdir(p))]
        else:
            files.append(p)

    derivations = find_derivatives([extract_grid(f) for f in files])

    if args.json:
        print(json.dumps([d.as_dict() for d in derivations], indent=2))
        return 0

    if not derivations:
        print("No duplicate representations found.")
        return 0

    for d in derivations:
        print(f"{d.derived}")
        print(f"  is a copy of {d.original}  ({d.overlap:.0%} of its rows, {d.shared_rows} matched)")
        print("  -> excluded from rollups. Counting both would double every figure.")
        print()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
