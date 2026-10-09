# CUI // SP-CTI
"""How much a document's word is worth.

Users already encode this. They rename the file, they put it in a folder, they
say "use this one" in an email. That judgement is real, it is load-bearing, and
it lives nowhere the software can see — so when four BOMs disagree, the tool
treats them as equals and averages a lie.

The job here is to CAPTURE that judgement as data, not to guess it from a
filename in Python. The vocabulary therefore lives in ``args/bom_credibility.yaml``
where a customer can add their own words; the code only knows how to score.

Two rules that are not negotiable:

**AI proposes; only a human's designation binds.** The engine emits a tier, a
confidence and a written rationale. It never silently promotes a source to
authoritative and then quietly resolves a conflict in its favour.

**Silence is never confirmation.** A source nobody has ruled on stays ``unknown``,
and ``unknown`` never outranks anything.

The structural signals are the interesting half — they let the engine reason
about a file with a completely uninformative name. Live formulas mean the
arithmetic is reproducible and can be argued with. Part numbers mean somebody
specified a thing that can actually be bought. Serial numbers mean a document is
enumerating physical units rather than asserting what should exist — which is how
ground truth gets recognised without hardcoding anyone's filename.

Public API::

    assess(extraction, forensics=None, derivative_of=None) -> Assessment
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from tools.bom import constants as C
from tools.bom.extract_grid import Cell, GridExtraction

_CONFIG_PATH = Path(__file__).resolve().parents[2] / "args" / "bom_credibility.yaml"
_CONFIG: dict[str, Any] | None = None

# A serial number: mostly alphanumeric, few vowels, not a word. Deliberately
# loose — the discriminating power comes from the column being nearly all
# distinct AND the sheet having no prices, not from this pattern alone.
_SERIALISH = re.compile(r"^[A-Z0-9][A-Z0-9\-]{4,}$", re.IGNORECASE)
_VOWELS = set("aeiou")

# A part number is specific in a way a description is not: digits and letters
# mixed, usually with punctuation.
_PARTISH = re.compile(r"^(?=.*\d)(?=.*[A-Z])[A-Z0-9][A-Z0-9\-/.]{3,}$", re.IGNORECASE)


def load_config(path: Path | None = None) -> dict[str, Any]:
    global _CONFIG
    if path is not None:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    if _CONFIG is None:
        _CONFIG = yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8"))
    return _CONFIG


@dataclass
class Signal:
    name: str
    weight: float
    why: str


@dataclass
class Assessment:
    tier: str = C.DEFAULT_CREDIBILITY
    score: float = 0.0
    role: str = "bom_claim"
    role_confidence: float = 0.0
    signals: list[Signal] = field(default_factory=list)

    # Never binding. A human's setting overrides this, and until one exists the
    # source is a proposal, not a verdict.
    set_by: str = "ai_proposed"

    @property
    def rationale(self) -> str:
        if not self.signals:
            return "No signal either way — this source is unranked until somebody says otherwise."
        parts = [
            f"{'+' if s.weight > 0 else ''}{s.weight:g} {s.why}"
            for s in sorted(self.signals, key=lambda s: -abs(s.weight))
        ]
        return "; ".join(parts)

    def as_dict(self) -> dict[str, Any]:
        return {
            "credibility_tier": self.tier,
            "authority_rank": C.CREDIBILITY_RANK.get(self.tier, 99),
            "score": round(self.score, 2),
            "role": self.role,
            "role_confidence": round(self.role_confidence, 2),
            "credibility_set_by": self.set_by,
            "credibility_rationale": self.rationale,
            "credibility_signals": [
                {"name": s.name, "weight": s.weight, "why": s.why} for s in self.signals
            ],
        }


# ── Column shape ─────────────────────────────────────────────────────────────

def _columns(cells: list[Cell]) -> dict[tuple[str, int], list[Cell]]:
    cols: dict[tuple[str, int], list[Cell]] = {}
    for c in cells:
        cols.setdefault((c.sheet, c.col), []).append(c)
    return cols


def _header_of(col_cells: list[Cell]) -> str:
    top = min(col_cells, key=lambda c: c.row)
    return (top.value_text or "").strip().lower()


def _looks_serial(values: list[str]) -> bool:
    """Mostly-distinct, mostly-consonant, alphanumeric tokens.

    A serial number is what an inventory has and a bill of materials does not. It
    is the signature of a document that enumerates units that physically exist,
    rather than one that asserts what ought to.
    """
    real = [v for v in values if v]
    if len(real) < 3:
        return False
    if len(set(real)) / len(real) < 0.8:
        return False

    hits = 0
    for v in real:
        if not _SERIALISH.match(v):
            continue
        letters = [ch for ch in v.lower() if ch.isalpha()]
        # Real words are ~40% vowels. Serials are not words.
        if letters and sum(ch in _VOWELS for ch in letters) / len(letters) > 0.4:
            continue
        hits += 1
    return hits / len(real) >= 0.6


def _has_price_columns(cells: list[Cell], cfg: dict) -> bool:
    wanted = [w.lower() for w in cfg["price_headers"]]
    for col_cells in _columns(cells).values():
        header = _header_of(col_cells)
        if header and any(w in header for w in wanted):
            return True
    return False


def _has_part_numbers(cells: list[Cell], cfg: dict) -> bool:
    wanted = [w.lower() for w in cfg["part_number_headers"]]
    for col_cells in _columns(cells).values():
        header = _header_of(col_cells)
        if header and any(w in header for w in wanted):
            return True

    # No helpful header? Look for a column that is mostly part-number-shaped. A
    # BOM with real SKUs in it has been specified by somebody who knows what they
    # are buying, and that is worth something regardless of how they labelled the
    # column.
    for col_cells in _columns(cells).values():
        values = [c.value_text for c in col_cells if c.value_text and c.value_num is None]
        if len(values) >= 4 and sum(bool(_PARTISH.match(v)) for v in values) / len(values) >= 0.6:
            return True
    return False


# ── Role ─────────────────────────────────────────────────────────────────────

def propose_role(extraction: GridExtraction, cfg: dict) -> tuple[str, float, list[Signal]]:
    """What kind of document this is.

    The distinction the whole engine turns on: **a BOM claims things; an inventory
    identifies individual physical units.** Serial numbers are the signature of
    the second, and that is how ground truth gets recognised without hardcoding
    anybody's filename — it generalises to any corpus in any industry.

    A serial proves a machine EXISTS. Its absence proves NOTHING. This function
    never licenses the conclusion that hardware is fictional; it only says which
    document is in a position to verify a count.
    """
    signals: list[Signal] = []

    if extraction.nodes and not extraction.cells:
        # A drawing. Whether it is *the agreed* drawing is a human's call — that
        # is the difference between a sketch and a yardstick.
        return "diagram", 0.9, [
            Signal("is_diagram", 0.0, "a drawing: it claims components, it prices none")
        ]

    cells = extraction.cells
    if not cells:
        return "narrative", 0.5, [
            Signal("no_line_items", 0.0, "no tabular content: context, not a bill of materials")
        ]

    inv = cfg["roles"]["inventory_truth"]
    hits = 0

    serial_col = any(
        _looks_serial([c.value_text for c in col if c.value_text])
        for col in _columns(cells).values()
    )
    if serial_col:
        hits += 1
        signals.append(Signal(
            "serial_column", 0.0,
            "a column of serial numbers: this document enumerates units that exist",
        ))

    headers = " ".join(_header_of(col) for col in _columns(cells).values())
    if any(k in headers for k in inv["header_keywords"]):
        hits += 1
        signals.append(Signal(
            "inventory_headers", 0.0,
            "headers name serials, tags or warranties, not prices",
        ))

    priced = _has_price_columns(cells, cfg)
    if not priced:
        hits += 1
        signals.append(Signal(
            "no_price_columns", 0.0,
            "nothing here is priced: it is a record of what is, not a plan for what to buy",
        ))

    if hits >= 2:
        return "inventory_truth", min(0.5 + 0.2 * hits, 0.95), signals

    quote_kw = cfg["roles"]["quote"]["header_keywords"]
    if any(k in headers for k in quote_kw):
        return "quote", 0.7, [
            Signal("quote_headers", 0.0, "reads as a vendor's priced offer")
        ]

    return "bom_claim", 0.6, signals


# ── Credibility ──────────────────────────────────────────────────────────────

def _vocabulary_signals(text: str, cfg: dict) -> list[Signal]:
    out: list[Signal] = []
    lowered = text.lower()
    for direction in ("upgrade", "downgrade"):
        for word, weight in cfg["vocabulary"][direction].items():
            # Word-boundary matched, so "solid" does not fire on "solidarity" and
            # "old" does not fire on "gold".
            if re.search(rf"(?<![a-z0-9]){re.escape(str(word).lower())}(?![a-z0-9])", lowered):
                out.append(Signal(
                    f"vocab:{word}", float(weight),
                    f'the document says "{word}"',
                ))
    return out


def assess(
    extraction: GridExtraction,
    forensics: Any = None,
    *,
    derivative_of: str = "",
    config_path: Path | None = None,
) -> Assessment:
    """Propose a credibility tier and a role, with a written rationale.

    Never binding. The rationale exists so that a human can disagree with a
    specific reason rather than with a number.
    """
    cfg = load_config(config_path)
    signals: list[Signal] = []

    role, role_conf, role_signals = propose_role(extraction, cfg)

    # A copy cannot be more authoritative than the thing it copies, and it has
    # lost the formulas on the way. Short-circuit: no amount of confident
    # vocabulary rescues a PDF print of a spreadsheet.
    if derivative_of:
        return Assessment(
            tier="derived",
            score=cfg["signals"]["is_derivative"],
            role="derived",
            role_confidence=0.95,
            signals=[Signal(
                "is_derivative", cfg["signals"]["is_derivative"],
                f"a re-representation of {derivative_of}: same money, fewer facts",
            )],
        )

    # The filename is a signal, not an oracle. It is scored alongside everything
    # else precisely so that a confidently-named file with no formulas and no part
    # numbers does not outrank a plain one that has both.
    text_for_vocab = extraction.filename + " " + " ".join(extraction.sheets)
    header_text = " ".join(
        c.value_text for c in extraction.cells[:400] if c.value_text
    )
    signals += _vocabulary_signals(text_for_vocab + " " + header_text[:2000], cfg)

    S = cfg["signals"]

    if extraction.has_formulas:
        signals.append(Signal(
            "has_formulas", S["has_formulas"],
            "live formulas: the arithmetic is reproducible and can be argued with",
        ))

    if extraction.cells and _has_part_numbers(extraction.cells, cfg):
        signals.append(Signal(
            "has_part_numbers", S["has_part_numbers"],
            "real part numbers: somebody specified a thing that can be bought",
        ))

    meta = extraction.metadata
    creator = str(meta.get("creator", "")) + " " + str(meta.get("last_modified_by", ""))
    machine = any(
        m in creator.lower() for m in ("openpyxl", "python-pptx", "reportlab")
    )

    if machine:
        signals.append(Signal(
            "machine_generated", S["machine_generated"],
            "written by a script, not a person",
        ))
    elif creator.strip():
        signals.append(Signal(
            "named_author", S["named_author"],
            f"authored by {creator.strip()}",
        ))

    try:
        if int(meta.get("revision") or 0) >= 5:
            signals.append(Signal(
                "many_revisions", S["many_revisions"],
                "revised repeatedly: it has been through review",
            ))
    except (TypeError, ValueError):
        pass

    blob = (header_text + " " + str(meta.get("comments", ""))).lower()
    if any(k in blob for k in ("generated by ai", "ai-generated", "prepared by ai", "powered by")):
        signals.append(Signal(
            "self_declared_ai", S["self_declared_ai"],
            "the document says a model produced it",
        ))

    if forensics is not None:
        placeholders = len(forensics.of_kind("placeholder"))
        if placeholders:
            signals.append(Signal(
                "has_placeholders", S["has_placeholders"],
                f"{placeholders} unfinished values in a document being costed",
            ))
        if placeholders > 5:
            signals.append(Signal(
                "many_placeholders", S["many_placeholders"],
                "unfinished throughout",
            ))

    score = sum(s.weight for s in signals)

    tier = C.DEFAULT_CREDIBILITY
    for name in ("authoritative", "corroborated", "working", "draft"):
        if score >= cfg["tiers"][name]:
            tier = name
            break
    else:
        # A document that scored heavily NEGATIVE is not "unknown" — we know
        # rather a lot about it, none of it reassuring. 'unknown' means we have no
        # signal at all, and conflating the two would let a source we have every
        # reason to distrust sit in the same bucket as one we simply have not
        # looked at.
        if signals:
            tier = "draft"

    # An inventory that verifies units is authoritative ABOUT COUNTS whatever its
    # filename says — that is what it is for. It is still not authoritative about
    # prices, which is why role and tier are two different columns.
    if role == "inventory_truth" and tier in ("unknown", "draft"):
        tier = "working"
        signals.append(Signal(
            "inventory_floor", 0.0,
            "an inventory of real units is worth listening to about counts, "
            "whatever it is called",
        ))

    return Assessment(
        tier=tier,
        score=score,
        role=role,
        role_confidence=role_conf,
        signals=signals + role_signals,
    )


def main() -> int:  # pragma: no cover
    import argparse
    import json
    import os

    from tools.bom.extract_grid import extract_grid
    from tools.bom.forensics import analyze as forensics_analyze

    ap = argparse.ArgumentParser(description="Propose a credibility tier for a document.")
    ap.add_argument("path", nargs="+")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    rows = []
    for p in args.path:
        g = extract_grid(p)
        f = forensics_analyze(p)
        a = assess(g, f)
        rows.append((os.path.basename(p), a))

    if args.json:
        print(json.dumps(
            {name: a.as_dict() for name, a in rows}, indent=2, default=str
        ))
        return 0

    rows.sort(key=lambda r: C.CREDIBILITY_RANK.get(r[1].tier, 99))
    for name, a in rows:
        print(f"{a.tier:<14} {a.score:>6.1f}  {a.role:<20} {name}")
        print(f"               {a.rationale[:150]}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
