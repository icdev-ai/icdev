# CUI // SP-CTI
"""What category is this line in?

Deterministic, and it answers in that order:

  1. **The document's own section.** A workbook with sheets called Compute,
     Networking, Software has already been categorised — by the person who owns the
     scope, in the words their organisation uses. Overriding that with a taxonomy of
     our own would be replacing a right answer with a defensible one, and it would
     rename the categories out from under everybody who has to read the result.

  2. **A keyword classifier**, when the section name carries no information
     ("Sheet1", "BOM", "Draft v2"). Reuses `govcon.bom_generator`, which is
     already the deterministic backstop everywhere else in ICDEV.

  3. **"Other"** — never blank, never dropped. A line with no category still has a
     price, and a line that quietly disappears from a category sheet is a line
     nobody argues about again.

No LLM. This is the path that keeps `--no-llm` mode honest: it produces a real
taxonomy from a corpus nobody has seen, without a model ever being asked.
"""
from __future__ import annotations

import re
from typing import Iterable

from tools.bom.lines import ExtractedLine

# Sheet names that tell you nothing about scope. A sheet called "Sheet1" is not a
# category; it is the absence of one, and treating it as a category produces a
# workbook with a sheet called Sheet1 in it.
_GENERIC = re.compile(
    r"^(sheet\d*|tab\d*|bom|bill of materials|draft|final|data|list|items?|"
    r"summary|table\d*|v?\d+(\.\d+)*|new|temp|copy( of .*)?)$",
    re.I,
)

# The keyword classifier's slugs, in the words a reader expects on a tab.
_LABELS = {
    "hardware": "Hardware",
    "software": "Software",
    "labor": "Services & Labour",
    "services": "Services & Labour",
    "travel": "Travel",
    "materials": "Materials",
    "odc": "Other Direct Costs",
    "other": "Other",
}


def _from_keywords(text: str, uom: str = "") -> str:
    try:
        from tools.govcon.bom_generator import classify_equipment_category
    except Exception:          # pragma: no cover - govcon is optional
        return "Other"

    slug = (classify_equipment_category(text or "", uom or "") or "other").lower()
    return _LABELS.get(slug, slug.replace("_", " ").title() or "Other")


def _clean(name: str) -> str:
    """A section name, made presentable without being rewritten.

    Their "2.1 Core Lab Switching & Routing" becomes "Core Lab Switching & Routing"
    — the number is an ordinal, not part of the name, and it would sort the tabs
    into an order that means nothing once the sections are merged across documents.
    """
    s = re.sub(r"^\s*\d+(\.\d+)*[\.\)\-–:]?\s*", "", str(name or "")).strip()
    return s[:60]


def category_of(line: ExtractedLine) -> str:
    sheet = _clean(line.source_sheet)
    if sheet and not _GENERIC.match(sheet):
        return sheet
    return _from_keywords(
        f"{line.description} {line.part_number} {line.manufacturer}".strip(),
        line.uom or "",
    )


def categorize(lines: Iterable[ExtractedLine], *, min_buckets: int = 2) -> dict[str, str]:
    """{line_id: category}, ready for ``pivot.build_dataset(categories=...)``.

    **A taxonomy with one bucket is not a taxonomy.** A single-sheet workbook has
    a sheet name, not a category scheme — and taking it at its word produces a
    "categorised" workbook with exactly one category tab, which tells the reader
    nothing and cannot be pivoted at all. When the documents' own sections do not
    partition the lines, fall through to the content.
    """
    lines = list(lines)
    if not lines:
        return {}

    by_sheet = {ln.line_id: category_of(ln) for ln in lines}
    if len(set(by_sheet.values())) >= min_buckets:
        return by_sheet

    return {
        ln.line_id: _from_keywords(
            f"{ln.description} {ln.part_number} {ln.manufacturer}".strip(),
            ln.uom or "",
        )
        for ln in lines
    }


__all__ = ["categorize", "category_of"]
