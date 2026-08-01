# CUI // SP-CTI
"""Does the BOM fund the design that was agreed?

A design everybody signed off on is not just another source. It is the YARDSTICK.
A bill of materials is only defensible if it funds the thing that was approved,
and spends only on what that thing justifies. So the check runs in both
directions:

    a component in the agreed design with no BOM line
        -> UNFUNDED. This is how projects end up eighty percent funded, and
           nobody finds out until the eighty percent has been spent.

    a BOM line with no component in the agreed design
        -> UNJUSTIFIED. Scope creep, or a leftover from an option that died.

And a third question that no amount of reading the documents can answer, because
it is about something that is not in any of them:

    a workstream we SAID we were doing, that appears in no design and no BOM
        -> you cannot detect the absence of something nobody wrote down. Which is
           precisely why it surfaces late, unfunded, in front of the wrong
           audience. So intent is promoted to a checkable source in its own right.

One thing this module will not do. A rack elevation drawing twelve machines is
making a CLAIM — and it is the most persuasive kind of claim there is, because it
looks like a photograph of something that already exists. If the inventory can
only account for two, that is a question, not a verdict. Inventories go stale. A
rack full of real hardware can be missing from a spreadsheet. The engine reports
the disagreement, names both sides, and asks. It never concludes that hardware is
fictional.

Public API::

    components_from(extraction, baseline_label) -> list[Component]
    check_coverage(components, lines, ...) -> list[Finding]
    check_scope(scope_items, components, lines, ...) -> list[Finding]
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field

from tools.bom.extract_grid import GridExtraction
from tools.bom.findings import Evidence, Finding
from tools.bom.matching import best_match, looks_like_part_number, normalize_part, tokens

# A drawing counts things two ways: by drawing them one at a time ("Node #7"), or
# by saying how many ("3x Switch", "Switch ×3"). Both forms turn up in the same
# file, sometimes for the same equipment.
_MULTIPLIER_PREFIX = re.compile(r"(?:^|\s)(\d{1,3})\s*[x×]\s*(?=\S)", re.IGNORECASE)
_MULTIPLIER_SUFFIX = re.compile(r"\s*[x×]\s*(\d{1,3})\s*$", re.IGNORECASE)
_INSTANCE = re.compile(r"#\s*\d+\s*$")

# Above this many unfunded components on one drawing, report the drawing rather
# than each component. Below it, name them individually — a handful of specific
# criticals is exactly what somebody needs to act on.
_AGGREGATE_ABOVE = 5


@dataclass
class Component:
    """One thing the agreed design says should be there."""

    label: str
    diagram: str = ""
    baseline: str = ""
    zone: str = ""
    node_id: str = ""
    # What the drawing claims. NOT what exists.
    claimed_qty: float = 1.0
    model_key: str = ""
    function_slug: str = ""

    @property
    def description(self) -> str:
        return self.label


@dataclass
class Line:
    """The BOM's side of the comparison. A thin view, so this module does not
    depend on how lines happen to be stored."""

    line_id: str
    description: str
    source_document: str = ""
    sheet: str = ""
    locator: str = ""
    part_number: str = ""
    manufacturer: str = ""
    function_slug: str = ""
    qty: float | None = None
    existing_asset: bool = False
    extended_price: float | None = None


@dataclass
class ScopeItem:
    """A capability somebody has declared is in scope, whether or not any document
    mentions it."""

    scope_id: str
    label: str
    capabilities: list[str] = field(default_factory=list)
    wave_label: str = ""


def _strip_instance(label: str) -> str:
    """"R320 — Worker #7" and "R320 — Worker #8" are two of the same thing."""
    return _INSTANCE.sub("", label).strip(" -—–|")


def _model_key(label: str) -> str:
    """The part-number-ish token in a drawn label, if there is one.

    A rack elevation labels a machine by its model. That token is what lets a
    drawing be compared with an inventory and with a BOM — and it is the only part
    of the label that means anything precise.

    Note the split does NOT include the hyphen. A model number is routinely
    hyphenated ("9200-24T"), and splitting on it shreds the very token we came for
    into two halves that each look like noise.
    """
    for word in re.split(r"[\s—–|(),/]+", label):
        word = word.strip("-")
        if looks_like_part_number(word):
            return normalize_part(word)
    return ""


# Things a drawing contains that are not components.
#
# A rack elevation is mostly furniture: U-slot rulers, column headers, blank
# panels, "SPARE / FUTURE". Treating those as things the BOM must fund produces a
# flood of unfunded_component findings about rack units — and a register full of
# nonsense is a register nobody reads, which costs more than reporting nothing.
_NOT_A_COMPONENT = re.compile(
    r"""^(?:
        u\s?\d+(?:\s*[-–—]\s*\d+)?          # U1, U11, U1–2  (rack unit rulers)
        | rack\s*\d*                        # column headers
        | equipment | position | slot | unit
        | spare(?:\s*/?\s*future)?          # a gap in the rack is not a purchase
        | future | reserved | empty | blank(?:\s*panel)?
        | tbd | n/?a | \W*                  # ornaments
    )$""",
    re.IGNORECASE | re.VERBOSE,
)


def _is_component(label: str) -> bool:
    if len(label) < 3:
        return False
    return not _NOT_A_COMPONENT.match(label.strip())


def components_from(
    extraction: GridExtraction,
    baseline_label: str = "",
) -> list[Component]:
    """Turn an agreed drawing into the components it claims.

    Instances are COLLAPSED and COUNTED. A rack elevation that draws twelve
    machines individually is not twelve different components; it is one component
    with a claimed quantity of twelve. Keeping them apart would make the coverage
    check compare a BOM line against twelve separate demands and report eleven
    phantom shortfalls.
    """
    grouped: dict[tuple[str, str], Component] = {}

    for node in extraction.nodes:
        raw = (node.get("label") or "").strip()
        if len(raw) < 3:
            continue

        qty = 1.0
        m = _MULTIPLIER_SUFFIX.search(raw)
        if m:
            qty = float(m.group(1))
            raw = _MULTIPLIER_SUFFIX.sub("", raw).strip()
        else:
            m = _MULTIPLIER_PREFIX.search(raw)
            if m:
                qty = float(m.group(1))
                raw = _MULTIPLIER_PREFIX.sub(" ", raw).strip()

        base = _strip_instance(raw)
        if not _is_component(base):
            continue

        key = (node.get("diagram", ""), base.lower())
        if key in grouped:
            grouped[key].claimed_qty += qty
            continue

        grouped[key] = Component(
            label=base,
            diagram=node.get("diagram", ""),
            baseline=baseline_label or extraction.filename,
            zone=str(node.get("zone") or ""),
            node_id=str(node.get("id") or ""),
            claimed_qty=qty,
            model_key=_model_key(base),
        )

    return list(grouped.values())


def _as_candidates(lines: list[Line]) -> list[dict]:
    return [
        {
            "line": ln,
            "description": ln.description,
            "part_number": ln.part_number,
            "manufacturer": ln.manufacturer,
            "function_slug": ln.function_slug,
        }
        for ln in lines
    ]


def check_coverage(
    components: list[Component],
    lines: list[Line],
    *,
    baseline_label: str,
    threshold: float = 0.55,
) -> list[Finding]:
    """Hold the BOM against the agreed design, both ways."""
    out: list[Finding] = []
    if not components:
        return out

    candidates = _as_candidates(lines)
    matched_lines: set[str] = set()
    unfunded: list[Component] = []

    # ── The design says it exists. Does anybody pay for it? ──────────────────
    for comp in sorted(components, key=lambda c: c.label.lower()):
        cand, m = best_match(
            comp.label, candidates,
            query_part=comp.model_key,
            query_function=comp.function_slug,
            threshold=threshold,
        )
        if cand is not None:
            matched_lines.add(cand["line"].line_id)
            continue
        unfunded.append(comp)

    # Grouped by drawing, and this is not cosmetic.
    #
    # A floor plan calls for seating, tables and rooms; a hardware BOM prices none
    # of them, because the facility fit-out is a separate lump somewhere else. All
    # of that is TRUE and worth saying once. Said eighteen separate times, at
    # CRITICAL, it buries the finding that actually matters — the switch on the
    # rack elevation that nobody costed — under a pile of furniture.
    #
    # A register nobody reads protects nothing, so the noise is a correctness
    # problem and not a presentation one.
    by_diagram: dict[str, list[Component]] = defaultdict(list)
    for comp in unfunded:
        by_diagram[comp.diagram or "design"].append(comp)

    for diagram, comps in sorted(by_diagram.items()):
        if len(comps) > _AGGREGATE_ABOVE:
            names = ", ".join(f'"{c.label}"' for c in comps[:6])
            out.append(Finding(
                finding_type="unfunded_component",
                kind="defect",
                severity="high",
                title=(
                    f"{len(comps)} things on the {diagram} have no line paying for them"
                ),
                detail=(
                    f"The {baseline_label} baseline draws them; no bill of materials "
                    f"carries them: {names}"
                    + (f", and {len(comps) - 6} more." if len(comps) > 6 else ".")
                    + "\n\nWhen a whole drawing goes unpriced like this it usually "
                    "means its scope is funded as a lump somewhere else — a fit-out "
                    "allowance, say. Confirm that lump exists and that it is big "
                    "enough, because right now nothing connects the two."
                ),
                impact_usd=None,
                evidence=[
                    Evidence(
                        source_document=baseline_label, sheet=diagram,
                        locator=c.node_id, raw_text=c.label,
                    )
                    for c in comps[:10]
                ],
                data={"count": len(comps), "diagram": diagram},
            ))
            continue

        for comp in comps:
            out.append(Finding(
                finding_type="unfunded_component",
                kind="defect",
                severity="critical",
                title=f'"{comp.label}" is in the agreed design and nothing pays for it',
                detail=(
                    f"The {baseline_label} baseline places this on the "
                    f"{comp.diagram or 'design'}"
                    + (f" (x{comp.claimed_qty:g})" if comp.claimed_qty > 1 else "")
                    + ". No line in any bill of materials matches it.\n\n"
                    "This is how a project ends up eighty percent funded — and nobody "
                    "finds out until the eighty percent has been spent."
                ),
                impact_usd=None,   # nothing prices it; that is the entire finding
                evidence=[Evidence(
                    source_document=baseline_label,
                    sheet=comp.diagram,
                    locator=comp.node_id,
                    raw_text=comp.label,
                )],
                data={"claimed_qty": comp.claimed_qty, "model_key": comp.model_key},
            ))

    # ── The BOM pays for it. Did anybody agree to it? ────────────────────────
    #
    # Softer than the other direction, and deliberately. A design drawing is not a
    # complete BOM: nobody draws the cabling, the spares or the freight, and
    # flagging every one of those as unjustified would bury the real finding under
    # noise. So this only speaks up about lines that are EXPENSIVE and drawable —
    # a large sum for something the agreed design never asked for.
    priced = [
        ln for ln in lines
        if ln.line_id not in matched_lines
        and ln.extended_price
        and ln.extended_price > 0
    ]
    if priced:
        big = sorted(priced, key=lambda ln: -(ln.extended_price or 0))
        cutoff = (big[0].extended_price or 0) * 0.10   # the top order of magnitude
        for ln in big:
            if (ln.extended_price or 0) < cutoff:
                break
            out.append(Finding(
                finding_type="unjustified_line",
                kind="risk",
                severity="medium",
                title=f'"{ln.description[:60]}" is not in the agreed design',
                detail=(
                    f"The BOM carries this at {ln.extended_price:,.2f}, and nothing "
                    f"in the {baseline_label} baseline calls for it. Either the "
                    f"design has moved on and the drawing has not, or this is a "
                    f"leftover from an option that died. Both are worth knowing "
                    f"before somebody signs for it."
                ),
                impact_usd=ln.extended_price,
                evidence=[Evidence(
                    source_document=ln.source_document,
                    sheet=ln.sheet, locator=ln.locator, raw_text=ln.description,
                    line_id=ln.line_id,
                )],
            ))

    return out


def check_owned_units(
    components: list[Component],
    inventory: dict[str, int],
    *,
    baseline_label: str,
    replacement_prices: dict[str, float] | None = None,
) -> list[Finding]:
    """The design leans on N machines. The inventory can account for M.

    READ THIS BEFORE CHANGING IT.

    A serial number proves a machine EXISTS. The ABSENCE of a serial number proves
    NOTHING AT ALL. Inventories go stale; a rack of real, working hardware can be
    missing from a spreadsheet that nobody has walked around and updated in a year.

    So this never concludes that hardware is fictional. It reports that two sources
    disagree, says which is which, and asks. Getting this wrong is not a small
    error — it is the tool telling a room full of executives, with total
    confidence, that ten machines their engineers are standing next to do not
    exist.

    The count still matters, and matters a lot: a refresh reserve is only as
    correct as the number of units it was sized from. That is why the question gets
    asked, not why it gets answered.
    """
    out: list[Finding] = []
    replacement_prices = replacement_prices or {}

    # Sum WITHIN a drawing; take the MAXIMUM across drawings.
    #
    # A design has several views of one thing. The rack elevation draws twelve
    # machines individually; the logical topology says "×12" for the same twelve.
    # They are twelve machines, not twenty-four — each drawing is a complete view,
    # not an additional order.
    #
    # Summing across views inflates the claim, and an inflated claim would make the
    # engine report a shortfall against the inventory that does not exist. It would
    # then tell somebody to go and buy hardware they already own, which is a more
    # expensive mistake than saying nothing.
    per_diagram: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    labels: dict[str, Component] = {}
    for comp in components:
        if not comp.model_key:
            continue
        per_diagram[comp.model_key][comp.diagram] += comp.claimed_qty
        # Prefer the most descriptive label we saw for this model.
        prev = labels.get(comp.model_key)
        if prev is None or len(comp.label) > len(prev.label):
            labels[comp.model_key] = comp

    claimed: dict[str, float] = {
        key: max(views.values()) for key, views in per_diagram.items()
    }

    for key, claimed_qty in sorted(claimed.items()):
        verified = inventory.get(key)
        comp = labels[key]

        if verified is None:
            out.append(Finding(
                finding_type="unverified_existing_asset",
                kind="risk",
                severity="medium",
                title=f'"{comp.label}" is drawn {claimed_qty:g}x and no inventory covers it',
                detail=(
                    "The design relies on this hardware and no source in this "
                    "corpus enumerates it by serial number. If it is kit we already "
                    "own, the reserve to replace it cannot be sized. If it is kit we "
                    "have to buy, nothing prices it.\n\n"
                    "Silence is not confirmation either way."
                ),
                evidence=[Evidence(
                    source_document=baseline_label, sheet=comp.diagram,
                    locator=comp.node_id, raw_text=comp.label,
                )],
                data={"claimed_qty": claimed_qty},
            ))
            continue

        if verified >= claimed_qty:
            continue

        shortfall = claimed_qty - verified
        unit = replacement_prices.get(key)
        out.append(Finding(
            finding_type="baseline_asset_gap",
            # A DECISION, not a defect. The tool does not know which source is
            # stale, and it must not pretend to.
            kind="decision",
            severity="high",
            title=(
                f'The design draws {claimed_qty:g}x "{comp.label}"; the inventory '
                f"accounts for {verified:g}"
            ),
            detail=(
                f"Two sources disagree about how many of these there are, and the "
                f"engine cannot tell which one is out of date.\n\n"
                f"EITHER the inventory is incomplete — a serial number proves a "
                f"machine exists, but its absence proves nothing, and inventories "
                f"go stale — in which case somebody needs to walk the rack and "
                f"update it.\n\n"
                f"OR the design leans on {shortfall:g} more than we actually have, "
                f"in which case they must be bought or the design must shrink.\n\n"
                f"This is a funding number either way, and it is not a discrepancy "
                f"to argue about. Somebody has to go and look."
                + (
                    f" At {unit:,.2f} each, the shortfall would cost "
                    f"{shortfall * unit:,.2f}."
                    if unit else
                    " Nothing in this corpus prices a replacement, so the cost of "
                    "the shortfall cannot be stated."
                )
            ),
            impact_usd=(shortfall * unit) if unit else None,
            evidence=[Evidence(
                source_document=baseline_label, sheet=comp.diagram,
                locator=comp.node_id, raw_text=comp.label,
            )],
            data={
                "claimed_qty": claimed_qty,
                "verified_qty": verified,
                "shortfall": shortfall,
                "model_key": key,
            },
        ))

    return out


def check_scope(
    scope_items: list[ScopeItem],
    components: list[Component],
    lines: list[Line],
    *,
    weak_sources: set[str] | None = None,
    threshold: float = 0.45,
) -> list[Finding]:
    """What we said we were doing, held against what anybody wrote down.

    This is the question an evidence-only engine cannot ask. You cannot detect the
    absence of something nobody put in a document — and that workstream is exactly
    the one that turns up late, unfunded, in front of the wrong audience.
    """
    out: list[Finding] = []
    weak_sources = weak_sources or set()

    line_cands = _as_candidates(lines)
    comp_cands = [
        {"description": c.label, "part_number": c.model_key,
         "function_slug": c.function_slug, "component": c}
        for c in components
    ]

    for item in scope_items:
        # A scope item is a capability, not a product. Match on everything the
        # customer said about it, not just its title.
        haystack = " ".join([item.label, *item.capabilities])

        matched_lines = [
            c["line"] for c in line_cands
            if _covers(haystack, c["description"], threshold)
        ]
        matched_comps = [
            c["component"] for c in comp_cands
            if _covers(haystack, c["description"], threshold)
        ]

        if not matched_comps and not matched_lines:
            out.append(Finding(
                finding_type="scope_declared_unpriced",
                kind="risk",
                severity="high",
                title=f'"{item.label}" is in scope and appears in nothing',
                detail=(
                    "We have said we are doing this"
                    + (f" ({item.wave_label})" if item.wave_label else "")
                    + ". It is in no agreed design and no bill of materials.\n\n"
                    "A placeholder holds the slot with a NULL price — not a zero, "
                    "which would claim the work is free, and not a guess, which "
                    "would get quoted back at somebody in a budget meeting. A "
                    "budget owner can earmark against a placeholder. They cannot "
                    "earmark against silence."
                ),
                impact_usd=None,
                evidence=[Evidence(
                    source_document="project intent",
                    raw_text=item.label,
                )],
                data={"scope_id": item.scope_id, "capabilities": item.capabilities},
            ))
            continue

        if matched_lines and not matched_comps:
            sources = {ln.source_document for ln in matched_lines}
            only_weak = sources and sources <= weak_sources

            if only_weak:
                out.append(Finding(
                    finding_type="scope_priced_only_by_weak_source",
                    kind="risk",
                    severity="high",
                    title=f'"{item.label}" is priced only by a source we do not trust',
                    detail=(
                        f"The sole pricing for this workstream sits in "
                        f"{', '.join(sorted(sources))} — and nowhere else. It appears "
                        f"in no agreed design and in no authoritative bill of "
                        f"materials.\n\n"
                        f"On a spreadsheet this reads as covered. It is not covered. "
                        f"Credibility, conformance and scope all meet on this one, "
                        f"and it is the kind of gap that is invisible until the money "
                        f"is already committed."
                    ),
                    impact_usd=sum(
                        ln.extended_price or 0 for ln in matched_lines
                    ) or None,
                    evidence=[
                        Evidence(
                            source_document=ln.source_document, sheet=ln.sheet,
                            locator=ln.locator, raw_text=ln.description,
                            line_id=ln.line_id,
                        )
                        for ln in matched_lines[:5]
                    ],
                    data={"scope_id": item.scope_id, "sources": sorted(sources)},
                ))
            else:
                out.append(Finding(
                    finding_type="scope_declared_undesigned",
                    kind="risk",
                    severity="medium",
                    title=f'"{item.label}" is priced but never designed',
                    detail=(
                        "Lines in the BOM pay for this, and no agreed architecture "
                        "shows it. Money without a design is money nobody can defend "
                        "the shape of."
                    ),
                    impact_usd=sum(ln.extended_price or 0 for ln in matched_lines) or None,
                    evidence=[
                        Evidence(
                            source_document=ln.source_document, sheet=ln.sheet,
                            locator=ln.locator, raw_text=ln.description,
                            line_id=ln.line_id,
                        )
                        for ln in matched_lines[:5]
                    ],
                    data={"scope_id": item.scope_id},
                ))

    return out


def _covers(haystack: str, candidate: str, threshold: float) -> bool:
    """Does this line or component belong to that declared capability?

    Containment against the capability text, not symmetric similarity. A scope item
    is broad ("stand up a simulation environment") and a line is narrow ("network
    emulation software"); demanding they look alike as whole strings would match
    nothing.
    """
    hs, cs = tokens(haystack), tokens(candidate)
    if not hs or not cs:
        return False
    return len(hs & cs) / len(cs) >= threshold
