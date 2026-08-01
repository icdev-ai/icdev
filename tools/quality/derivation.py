#!/usr/bin/env python3
# CUI // SP-CTI
"""Derivation disclosure — tell the user when a span is quoted, synthesized, or computed.

WHY THIS EXISTS

A cited answer today presents three very different things identically:

  * a **quotation** — the source says exactly this;
  * a **paraphrase** — the model restated it, and the restatement may drift; and
  * a **computed figure** — a number that appears in NO source, that the model
    produced by doing arithmetic.

The third is the dangerous one. "Total obligated value is $4.15M [source: 3]"
reads as a quotation, carries a well-formed citation, and passes citation
validation — because the cited chunk exists. But the number is not in the chunk.
It was derived, possibly correctly, possibly not, and nothing in the output
distinguishes it from a figure lifted verbatim off the page.

This module classifies every claim into one of three provenance classes and,
for computed figures, recovers the arithmetic that produced them so the user
sees the formula, each operand's value, and where each operand came from.

DESIGN

Classification is **deterministic**, per the D391 deterministic-picker rule: the
model is never asked "did you quote or compute this?" — a model that fabricated
a number will equally happily report that it quoted one. Instead:

  * `verbatim`        the claim's text occurs contiguously in a cited source;
  * `derived-numeric` the claim carries a number that appears in no source, and
                      arithmetic over source numbers reproduces it;
  * `derived-text`    everything else — restated, summarized, or fused.

The formula search is a bounded exhaustive sweep over the numbers actually
present in the cited sources (sums/differences/products/ratios/percentages of
up to three operands). It answers "is there an arithmetic path from the sources
to this number", which is evidence the figure was computed rather than invented.

**A recovered formula is not a proof of correctness.** It is a disclosure: here
is a derivation consistent with your sources. `derived-numeric` with NO
recoverable formula is the loud case — a number grounded in nothing, which is
what an invented figure looks like.

CLI::

    python tools/quality/derivation.py --claim "Total is 45" --source "20 and 25"
"""
from __future__ import annotations

import argparse
import itertools
import json
import re
import sys
from dataclasses import dataclass, field

from tools.quality.citation_grounding import bind_claim_span, strip_citations

#: The three provenance classes surfaced to the user.
DERIV_VERBATIM = "verbatim"
DERIV_TEXT = "derived-text"
DERIV_NUMERIC = "derived-numeric"

DERIVATION_CLASSES = (DERIV_VERBATIM, DERIV_TEXT, DERIV_NUMERIC)

#: Human-readable label per class, for UI and audit records.
DERIVATION_LABELS = {
    DERIV_VERBATIM: "Quoted from source",
    DERIV_TEXT: "Synthesized from source",
    DERIV_NUMERIC: "Computed from source values",
}

# A number, optionally with thousands separators, decimals, a leading currency
# symbol and a trailing scale word or percent sign. Scale words matter: sources
# routinely say "$4.2 million" where the answer says "4,200,000".
_SCALES = {
    "hundred": 100.0,
    "thousand": 1_000.0,
    "k": 1_000.0,
    "million": 1_000_000.0,
    "m": 1_000_000.0,
    "mm": 1_000_000.0,
    "billion": 1_000_000_000.0,
    "bn": 1_000_000_000.0,
    "b": 1_000_000_000.0,
    "trillion": 1_000_000_000_000.0,
}

_NUM_RE = re.compile(
    r"(?<![\w.])"                                   # not mid-identifier
    r"(?P<neg>-)?"
    r"[$€£]?\s*"
    r"(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"\s*(?P<scale>hundred|thousand|million|billion|trillion|k|mm|m|bn|b)?"
    r"(?P<pct>\s*%|\s*percent)?"
    r"(?![\w])",
    re.IGNORECASE,
)

#: Ceiling on how many distinct source numbers enter the formula search.
#: C(24,3) is ~2k combinations x a handful of operators — bounded and fast — and
#: a claim needing operands beyond the 24 most prominent is not one a recovered
#: formula would credibly explain anyway.
_MAX_OPERANDS = 24

#: Formula search is capped at three operands. Beyond that, a "match" is
#: numerology: with enough numbers and operators something will always hit the
#: target by coincidence, and a coincidence disclosed as a derivation is worse
#: than an honest "computed, derivation not recoverable".
_MAX_TERMS = 3


@dataclass
class Operand:
    """One value that fed a computed figure, with where it came from."""

    value: float
    literal: str = ""
    source_id: str = ""
    quote: str = ""

    def to_dict(self) -> dict:
        return {
            "value": self.value,
            "literal": self.literal,
            "source_id": self.source_id,
            "quote": self.quote,
        }


@dataclass
class Derivation:
    """How one claim relates to the sources it cites.

    ``kind`` is the disclosure. ``formula``/``operands`` are populated only for
    ``derived-numeric`` and only when a derivation was actually recovered —
    absence is meaningful and must not be rendered as "no derivation needed".
    """

    kind: str = DERIV_TEXT
    quote: str = ""
    source_ids: list = field(default_factory=list)
    score: float = 0.0
    formula: str = ""
    operands: list = field(default_factory=list)
    value: float | None = None
    unexplained: list = field(default_factory=list)

    @property
    def is_derived(self) -> bool:
        return self.kind != DERIV_VERBATIM

    @property
    def label(self) -> str:
        return DERIVATION_LABELS.get(self.kind, self.kind)

    def describe(self) -> str:
        """One user-facing sentence. This is the actual disclosure text."""
        if self.kind == DERIV_VERBATIM:
            return "Quoted verbatim from the cited source."
        if self.kind == DERIV_NUMERIC:
            if self.formula:
                ops = ", ".join(
                    f"{o.literal or o.value}" + (f" [{o.source_id}]" if o.source_id else "")
                    for o in self.operands
                )
                return f"Computed: {self.formula}. Operands: {ops}."
            nums = ", ".join(str(n) for n in self.unexplained)
            return (
                f"Contains value(s) not present in the cited sources ({nums}) and no "
                "derivation from source values was recoverable."
            )
        return "Synthesized from the cited source — restated, not quoted."

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "label": self.label,
            "quote": self.quote,
            "source_ids": list(self.source_ids),
            "score": self.score,
            "formula": self.formula,
            "operands": [o.to_dict() for o in self.operands],
            "value": self.value,
            "unexplained": list(self.unexplained),
            "description": self.describe(),
        }


# --------------------------------------------------------------------------- #
# Numeric extraction
# --------------------------------------------------------------------------- #


def extract_numbers(text: str) -> list:
    """Every numeric literal in ``text`` as ``(value, literal)``.

    Scale words and thousands separators are normalized so "$4.2 million" and
    "4,200,000" compare equal — without that, every unit-differing restatement
    would misclassify as an unexplained computed figure.
    """
    out = []
    for m in _NUM_RE.finditer(text or ""):
        raw = m.group("num").replace(",", "")
        try:
            val = float(raw)
        except ValueError:  # pragma: no cover - regex guarantees numeric
            continue
        if m.group("neg"):
            val = -val
        scale = (m.group("scale") or "").lower()
        if scale:
            val *= _SCALES.get(scale, 1.0)
        out.append((val, m.group(0).strip()))
    return out


def _num_key(value: float) -> str:
    """Comparison key tolerant of float noise (1e-9 relative)."""
    return f"{value:.9g}"


def _matches(candidate: float, target: float) -> bool:
    """True when ``candidate`` reproduces ``target`` at the target's precision.

    A source total of 45.0 legitimately surfaces as "45"; 4.157 legitimately
    surfaces as "4.16". Comparing exactly would reject every correctly-rounded
    figure, so the comparison is made at the precision the claim actually
    asserts.
    """
    if candidate is None:
        return False
    s = f"{target:.10g}"
    dp = len(s.split(".")[1]) if "." in s else 0
    try:
        return round(candidate, dp) == round(target, dp)
    except (ValueError, OverflowError):  # pragma: no cover
        return False


# --------------------------------------------------------------------------- #
# Formula recovery
# --------------------------------------------------------------------------- #


def _candidate_formulas(values: list):
    """Yield ``(expression_template, result, operand_tuple)`` over small subsets.

    Deliberately a small, legible operator set. Every operator here corresponds
    to something a document-analysis answer plausibly does — totals, deltas,
    rates, shares — and each renders as a formula a reader can check by hand.
    """
    # Pairs: the overwhelming majority of real derivations.
    for a, b in itertools.permutations(values, 2):
        av, bv = a[0], b[0]
        yield (f"{a[1]} + {b[1]}", av + bv, (a, b))
        yield (f"{a[1]} - {b[1]}", av - bv, (a, b))
        yield (f"{a[1]} x {b[1]}", av * bv, (a, b))
        if bv:
            yield (f"{a[1]} / {b[1]}", av / bv, (a, b))
            yield (f"({a[1]} / {b[1]}) x 100", av / bv * 100.0, (a, b))
            yield (f"({a[1]} - {b[1]}) / {b[1]} x 100", (av - bv) / bv * 100.0, (a, b))
        yield (f"{a[1]} x {b[1]} / 100", av * bv / 100.0, (a, b))

    # Triples: sums and sum-minus only. Unrestricted 3-operand search over all
    # operators is where coincidental matches start dominating real ones.
    for combo in itertools.combinations(values, 3):
        expr = " + ".join(c[1] for c in combo)
        yield (expr, sum(c[0] for c in combo), combo)


def derive_formula(target: float, sources: dict, *, max_operands: int = _MAX_OPERANDS):
    """Recover an arithmetic path from source numbers to ``target``.

    ``sources`` maps ``source_id -> text``. Returns ``(formula, [Operand])`` or
    ``(None, [])`` when nothing reproduces the target.

    Single-source-value identity is checked first: if the target simply IS a
    number in a source, that is not a derivation at all and the caller should
    have classified it verbatim.
    """
    pool = []
    seen = set()
    for sid, text in (sources or {}).items():
        for val, literal in extract_numbers(text or ""):
            key = _num_key(val)
            if key in seen:
                continue
            seen.add(key)
            pool.append((val, literal, sid))
            if len(pool) >= max_operands:
                break
        if len(pool) >= max_operands:
            break

    if len(pool) < 2:
        return None, []

    values = [(v, lit) for v, lit, _sid in pool]
    owner = {_num_key(v): (lit, sid) for v, lit, sid in pool}

    best = None
    for expr, result, operands in _candidate_formulas(values):
        if not _matches(result, target):
            continue
        # Prefer the shortest derivation: a 2-operand explanation is far more
        # likely to be the real one than a 3-operand coincidence hitting the
        # same value.
        if best is None or len(operands) < len(best[1]):
            best = (expr, operands)
            if len(operands) == 2:
                break

    if best is None:
        return None, []

    expr, operands = best
    built = []
    for val, literal in operands:
        lit, sid = owner.get(_num_key(val), (literal, ""))
        built.append(Operand(value=val, literal=lit, source_id=sid))
    return expr, built


# --------------------------------------------------------------------------- #
# Classification
# --------------------------------------------------------------------------- #


def _normalize(text: str) -> str:
    """Collapse whitespace and case for contiguity testing."""
    return re.sub(r"\s+", " ", (text or "")).strip().lower()


def classify_claim(claim: str, sources: dict, *, cited_ids=None) -> Derivation:
    """Classify one claim as verbatim, derived-text or derived-numeric.

    ``sources`` maps ``source_id -> text``. ``cited_ids`` restricts the check to
    the sources the claim actually cites; when omitted every source is
    considered.

    The order matters. Verbatim is tested first and wins outright: a claim
    lifted word-for-word off the page needs no derivation disclosure even if it
    happens to contain numbers.
    """
    bare = strip_citations(claim or "").strip()
    if not bare:
        return Derivation(kind=DERIV_TEXT)

    pool = dict(sources or {})
    if cited_ids:
        scoped = {k: v for k, v in pool.items() if k in set(cited_ids)}
        # Fall back to the full pool rather than classifying against nothing —
        # a citation naming an unknown id is a citation defect, reported by
        # `citation_gate`, not a derivation question.
        pool = scoped or pool

    norm_claim = _normalize(bare)

    # 1. Verbatim — the claim occurs contiguously in some source.
    for sid, text in pool.items():
        if norm_claim and norm_claim in _normalize(text):
            span = bind_claim_span(bare, text or "", sid) or {}
            return Derivation(
                kind=DERIV_VERBATIM,
                quote=span.get("quote", bare),
                source_ids=[sid],
                score=span.get("score", 1.0),
            )

    # Best supporting span across the pool — the evidence shown for either
    # derived class.
    best_span, best_sid = None, ""
    for sid, text in pool.items():
        span = bind_claim_span(bare, text or "", sid)
        if span and (best_span is None or span["score"] > best_span["score"]):
            best_span, best_sid = span, sid

    # 2. Numbers in the claim that appear in NO source are computed figures.
    source_values = set()
    for text in pool.values():
        for val, _lit in extract_numbers(text or ""):
            source_values.add(_num_key(val))

    claim_numbers = extract_numbers(bare)
    unexplained = [
        literal for val, literal in claim_numbers if _num_key(val) not in source_values
    ]

    if unexplained:
        # Recover a derivation for the first unexplained value; that is the
        # figure the reader most needs accounted for.
        target = next(
            val for val, literal in claim_numbers if literal == unexplained[0]
        )
        formula, operands = derive_formula(target, pool)
        return Derivation(
            kind=DERIV_NUMERIC,
            quote=(best_span or {}).get("quote", ""),
            source_ids=[best_sid] if best_sid else list(pool),
            score=(best_span or {}).get("score", 0.0),
            formula=formula or "",
            operands=operands,
            value=target,
            unexplained=unexplained,
        )

    # 3. Everything else is synthesis.
    return Derivation(
        kind=DERIV_TEXT,
        quote=(best_span or {}).get("quote", ""),
        source_ids=[best_sid] if best_sid else [],
        score=(best_span or {}).get("score", 0.0),
    )


def disclose_derivations(text: str, sources: dict) -> dict:
    """Per-claim derivation report for a whole answer.

    Returns ``{claims, counts, has_derived, has_unexplained_numeric}``. The last
    flag is the one worth surfacing loudly: a computed figure with no
    recoverable derivation is what an invented number looks like.
    """
    from tools.quality.citation_grounding import decompose_claims, parse_citations

    claims = []
    counts = {DERIV_VERBATIM: 0, DERIV_TEXT: 0, DERIV_NUMERIC: 0}
    for sentence, start, end in decompose_claims(text or ""):
        # A fragment that is nothing but a citation marker asserts nothing.
        # Counting it as synthesis made every fully-quoted answer report itself
        # as containing derived content, which would train users to ignore the
        # badge — the one outcome that defeats the whole disclosure.
        if not strip_citations(sentence).strip():
            continue
        cited = parse_citations(sentence)
        deriv = classify_claim(sentence, sources, cited_ids=cited or None)
        counts[deriv.kind] = counts.get(deriv.kind, 0) + 1
        claims.append({
            "claim": sentence,
            "start": start,
            "end": end,
            "cited": cited,
            "derivation": deriv.to_dict(),
        })

    unexplained = [
        c for c in claims
        if c["derivation"]["kind"] == DERIV_NUMERIC and not c["derivation"]["formula"]
    ]
    return {
        "claims": claims,
        "counts": counts,
        "has_derived": counts[DERIV_TEXT] + counts[DERIV_NUMERIC] > 0,
        "has_unexplained_numeric": bool(unexplained),
        "unexplained_numeric_count": len(unexplained),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Classify claim derivation against sources.")
    ap.add_argument("--claim", required=True)
    ap.add_argument("--source", action="append", default=[],
                    help="Source text; repeatable. Ids are assigned positionally.")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    sources = {str(i + 1): s for i, s in enumerate(args.source)}
    deriv = classify_claim(args.claim, sources)
    if args.json:
        print(json.dumps(deriv.to_dict(), indent=2))
    else:
        print(f"{deriv.kind}: {deriv.describe()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
