# CUI // SP-CTI
"""The deck.

Built on ICDEV's existing slides engine — nine themes, native PPTX tables,
speaker notes — so none of that is reinvented here. What IS here is the argument:
which slides, in what order, saying what.

The trust model is borrowed from Compass's monthly status report, and it is the
only reason a deck like this can be allowed to exist. **The figures come from a
frozen snapshot. A model may write the prose around them and may not touch them.**
Every number on every slide is rendered from the reconciled dataset, and the
narrative is fitted to the numbers rather than the other way round.

The order is the argument:

  1. THE ASK. What we want, and — when the sources still disagree — the honest
     statement that there is not yet a number.
  2. WHAT YOU ALREADY OWN. The best news in the pack, and a cost-sorted table
     buries it. Hardware already in the building costs nothing and is frequently
     the reason a team can start now instead of waiting a year for a facility.
  3. WHAT WE FOUND. Sorted by money. This is the slide that earns the room's
     trust, because it is the one that admits things.
  4. WHERE IT GOES. The pivot.
  5. WHEN. Phasing — because an all-or-nothing request gets deferred and a phased
     one gets approved.
  6. WHO SAID SO. The credibility ladder, so nobody has to take the total on faith.

Public API::

    build_deck(dataset, findings, sources, ...) -> list[dict]     # slide specs
    render(slides, theme=..., title=...) -> str                    # a .pptx path
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Iterable

from tools.bom import constants as C
from tools.bom.findings import Finding
from tools.bom.pivot import Dataset, Pivot

# Audience, and it is not decoration — it is which QUESTION the deck answers.
#
#   working      — "what did we find, and can we trust it?"  The workgroup's deck.
#                  Findings, provenance, competing claims, the cell references.
#   leadership   — "what are we asking for, and what do we get?"  Nothing about how
#                  the number was arrived at. A room of executives is not being
#                  asked to audit the reconciliation; they are being asked to fund
#                  an outcome, and showing them the working reads as either
#                  hedging or as an invitation to relitigate it.
#
# The engineering rigour does not disappear from the leadership deck. It is what
# makes the leadership deck ALLOWED TO EXIST — see the refusal in build_deck().
AUDIENCES = ("leadership", "working")


class NotReadyForLeadership(RuntimeError):
    """A leadership deck was asked for while the evidence still disagrees.

    This is the one place the tool refuses rather than degrades. A polished deck
    is a deck that states a number with confidence, and there is no honest way to
    do that over four documents that price the same project differently. The fix
    is not a caveat in six-point type; it is a human nominating a source of record
    — which takes minutes, because the reconciliation is already done.
    """


@dataclass
class Snapshot:
    """The figures, frozen.

    Once this exists, the numbers in the deck cannot move — not through an LLM,
    not through a re-run, not through somebody re-opening the workbook. The deck
    cites its hash in the footer, so a figure quoted back at you six weeks later
    can be traced to the exact state of the evidence that produced it.
    """

    committed_total: float
    open_total: float
    open_count: int
    line_count: int
    competing_claims: list[str] = field(default_factory=list)
    by_category: dict[str, float] = field(default_factory=dict)
    findings_by_severity: dict[str, int] = field(default_factory=dict)
    top_findings: list[dict[str, Any]] = field(default_factory=list)
    owned_value: float = 0.0
    owned_note: str = ""

    @property
    def sha(self) -> str:
        blob = json.dumps(self.__dict__, sort_keys=True, default=str)
        return hashlib.sha256(blob.encode()).hexdigest()[:12]

    @property
    def is_a_total(self) -> bool:
        return not self.competing_claims


def freeze(
    dataset: Dataset,
    findings: Iterable[Finding],
    *,
    owned_value: float = 0.0,
    owned_note: str = "",
) -> Snapshot:
    findings = list(findings)

    by_cat: dict[str, float] = {}
    for r in dataset.rows:
        if not r.committed:
            continue
        cat = r.dims.get("category") or "(uncategorised)"
        by_cat[cat] = by_cat.get(cat, 0.0) + r.extended_price

    sev: dict[str, int] = {}
    for f in findings:
        sev[f.severity] = sev.get(f.severity, 0) + 1

    top = sorted(
        findings,
        key=lambda f: (C.SEVERITY_RANK[f.severity], -(f.impact_usd or 0.0)),
    )[:6]

    return Snapshot(
        committed_total=dataset.committed_total,
        open_total=dataset.open_total,
        open_count=sum(1 for r in dataset.rows if not r.committed),
        line_count=len(dataset.rows),
        competing_claims=sorted(dataset.claim_sources) if dataset.competing_claims else [],
        by_category=by_cat,
        findings_by_severity=sev,
        top_findings=[
            {
                "severity": f.severity,
                "kind": f.kind,
                "impact": f.impact_usd,
                "title": f.title,
                "where": (
                    f"{f.evidence[0].source_document}!{f.evidence[0].sheet}"
                    f"!{f.evidence[0].locator}"
                    if f.evidence else ""
                ),
            }
            for f in top
        ],
        owned_value=owned_value,
        owned_note=owned_note,
    )


def _money(v: float | None) -> str:
    return f"${v:,.0f}" if v else "—"


# ── The slides ───────────────────────────────────────────────────────────────

def _title_slide(snap: Snapshot, project: str, *, audience: str = "leadership") -> dict:
    if audience == "leadership":
        # What we want and what it buys. How we got here is the speaker's job, and
        # only if asked — a title slide that opens with methodology has spent the
        # room's attention before the ask arrives.
        bullets = [_money(snap.committed_total) + " — investment request"]
        if snap.owned_value:
            bullets.append(
                f"{_money(snap.owned_value)} of capability already owned, working "
                f"from day one"
            )
        if snap.open_count:
            bullets.append(f"{snap.open_count} items still being priced")
    else:
        bullets = [
            "Reconciled from every document we were given",
            f"Snapshot {snap.sha} — these figures are frozen",
        ]

    return {
        "slide_type": "title",
        "title": project,
        "bullets": bullets,
        "speaker_notes": (
            "Every number in this deck traces to a cell in a source document. "
            "Nothing here was estimated by the tool that produced it."
        ),
    }


def _the_ask(snap: Snapshot) -> dict:
    if not snap.is_a_total:
        # We do not print a total we do not have. This slide is the product.
        return {
            "slide_type": "content",
            "title": "There is not yet a number — and that is the finding",
            "bullets": [
                f"{len(snap.competing_claims)} documents each claim to price this "
                f"project.",
                "Adding them together adds competing estimates of the same project. "
                "That is the arithmetic that produced the spread in the first place.",
                "Nominate a source of record for each area of scope and this becomes "
                "a number in minutes — the reconciliation is already done.",
                *[f"· {name}" for name in snap.competing_claims],
            ],
            "speaker_notes": (
                "Do not soften this. The room's instinct will be to ask for 'the "
                "number', and the honest answer is that four documents disagree and "
                "somebody has to say which one governs. That decision takes minutes; "
                "pretending it has already been made costs the project."
            ),
        }

    bullets = [
        f"{_money(snap.committed_total)} — agreed, and every line traces to a source cell.",
    ]
    if snap.open_count:
        bullets.append(
            f"{_money(snap.open_total)} across {snap.open_count} item(s) is still "
            f"disputed and is NOT in that figure — not at its cheapest, not averaged."
        )
    if snap.owned_value:
        bullets.append(
            f"{_money(snap.owned_value)} of hardware we already own is carrying part "
            f"of this at zero cost."
        )
    return {
        "slide_type": "content",
        "title": "The ask",
        "bullets": bullets,
        "speaker_notes": (
            "The committed figure excludes everything still in dispute. If asked "
            "'is that the whole number?', the answer is: it is the part we can "
            "defend line by line, and here is exactly what is still open."
        ),
    }


def _what_you_own(snap: Snapshot) -> dict | None:
    """The best news in the pack, and a cost-sorted table buries it.

    Hardware already in the building costs nothing and is frequently the reason a
    team can start building on Monday instead of waiting a year for a facility. On
    a table sorted by price it is the last row.
    """
    if not snap.owned_value and not snap.owned_note:
        return None
    return {
        "slide_type": "content",
        "title": "What we already own",
        "bullets": [
            f"{_money(snap.owned_value)} of hardware is already in the building.",
            snap.owned_note or "It is being repurposed rather than replaced.",
            "This is avoided capital expenditure, and it is why work can start "
            "before the buildout finishes.",
            "It is out of warranty, so the ask includes a reserve to replace it as "
            "it fails — earmarked now rather than discovered later.",
        ],
        "speaker_notes": (
            "Lead with this if the room is cold. It is the only slide that gives "
            "them something, and it reframes the conversation from 'what will this "
            "cost' to 'what are we already getting for free'."
        ),
    }


def _findings_slide(snap: Snapshot) -> dict:
    rows = [["Severity", "Impact", "What we found"]]
    for f in snap.top_findings:
        rows.append([
            f["severity"].upper(),
            _money(f["impact"]),
            f["title"][:78],
        ])

    counts = ", ".join(
        f"{n} {sev}" for sev, n in sorted(
            snap.findings_by_severity.items(),
            key=lambda kv: C.SEVERITY_RANK[kv[0]],
        )
    )
    return {
        "slide_type": "table",
        "title": "What we found in the evidence",
        # The builder takes its table as bullets={headers, rows, footer}. Passing a
        # bare list here rendered the slide as the words "No table data." — a deck
        # that looked fine until somebody opened it.
        "bullets": {
            "headers": rows[0],
            "rows": rows[1:],
            "footer": [counts, "", ""] if counts else [],
        },
        "speaker_notes": (
            "This is the slide that earns the room. It is the one that admits "
            "things — a double-counted licence, a chassis with no price, a subtotal "
            "that stopped tracking its own inputs. Every one cites the exact cell. "
            "A pack with no findings is a pack nobody checked."
        ),
    }


def _where_it_goes(snap: Snapshot, p: Pivot | None) -> dict | None:
    if p is None or not snap.by_category:
        return None
    rows = [["", *p.cols, "Total"]]
    for r in p.rows:
        rows.append([
            r,
            *[_money(p.cell(r, c)) for c in p.cols],
            _money(p.row_totals.get(r, 0)),
        ])
    return {
        "slide_type": "table",
        "title": "Where the money goes",
        "bullets": {"headers": rows[0], "rows": rows[1:], "footer": []},
        "speaker_notes": p.reconciliation_note,
    }


def _who_said_so(sources: dict[str, Any]) -> dict:
    rows = [["Document", "How much its word is worth"]]
    for name, src in sorted(
        sources.items(),
        key=lambda kv: C.CREDIBILITY_RANK.get(
            getattr(kv[1], "credibility_tier", "unknown"), 99
        ),
    )[:10]:
        rows.append([name[:52], getattr(src, "credibility_tier", "unknown")])
    return {
        "slide_type": "table",
        "title": "Who said so",
        "bullets": {"headers": rows[0], "rows": rows[1:], "footer": []},
        "speaker_notes": (
            "Nobody should take the total on faith. Where two documents disagreed, "
            "the one higher up this list won — and where two you both marked "
            "authoritative disagreed, neither won and the item is still open."
        ),
    }


def build_deck(
    dataset: Dataset,
    findings: Iterable[Finding] = (),
    sources: dict[str, Any] | None = None,
    *,
    project: str = "Bill of Materials",
    pivot: Pivot | None = None,
    owned_value: float = 0.0,
    owned_note: str = "",
    audience: str = "leadership",
) -> tuple[list[dict], Snapshot]:
    """The slide specs, and the frozen figures they were rendered from.

    ``audience`` decides which question the deck answers — see AUDIENCES. It is
    the difference between a pack that shows its working and a pack that states a
    conclusion, and mixing them produces a deck that does neither job.
    """
    if audience not in AUDIENCES:
        raise ValueError(f"unknown audience: {audience}")

    snap = freeze(dataset, findings, owned_value=owned_value, owned_note=owned_note)
    sources = sources or {}

    if audience == "leadership" and not snap.is_a_total:
        raise NotReadyForLeadership(
            f"{len(snap.competing_claims)} sources still price this project "
            f"differently, so there is no number to present. Nominate a source of "
            f"record for each area of scope, then build the leadership deck. "
            f"(The working deck presents this honestly and is what the workgroup "
            f"should be looking at first.)"
        )

    if audience == "leadership":
        # What we are asking for, what it buys, and when. Nothing about how the
        # figure was arrived at: the room is being asked to fund an outcome, not to
        # audit a reconciliation, and showing them the working reads as hedging.
        #
        # The findings and the provenance are not hidden — they are the WORKING
        # deck, and they are the reason this deck is allowed to state a number at
        # all. Leadership gets the conclusion because somebody did the work.
        slides: list[dict | None] = [
            _title_slide(snap, project, audience=audience),
            _the_ask(snap),
            _what_you_own(snap),
            _where_it_goes(snap, pivot),
        ]
    else:
        slides = [
            _title_slide(snap, project, audience=audience),
            _the_ask(snap),
            _what_you_own(snap),
            _findings_slide(snap),
            _where_it_goes(snap, pivot),
            _who_said_so(sources),
            {
                "slide_type": "content",
                "title": "How to check this",
                "bullets": [
                    "Every figure traces to a document, a sheet and a cell — see "
                    "the workbook.",
                    "Findings marked 'deterministic' are arithmetic; nothing "
                    "inferred them.",
                    "Anything still disputed contributes zero here and is listed "
                    "in full.",
                    f"Snapshot {snap.sha} — quote it and these numbers can be "
                    f"reproduced exactly.",
                ],
                "speaker_notes": (
                    "Invite the check. A workgroup that cannot reproduce a number "
                    "will not defend it in front of anyone else, and a figure "
                    "nobody will defend is a figure that gets cut."
                ),
            },
        ]

    out = [s for s in slides if s is not None]

    # The builder renders the LAST slide as an outro, whatever type it claims to
    # be. Without an explicit one it quietly eats a real slide — "Who said so" was
    # being turned into a thank-you card, and nothing said so.
    out.append({
        "slide_type": "outro",
        "title": "Every figure traces to a cell",
        "bullets": [
            f"Snapshot {snap.sha} — quote it and these numbers reproduce exactly.",
            "The workbook has the provenance for every line.",
        ],
        "speaker_notes": (
            "If they want to check, let them. That is the point of the pack."
        ),
    })

    # The footer is not decoration. A figure quoted back at you in six weeks can be
    # traced to the exact state of the evidence that produced it.
    #
    # The slide builder's contract is a list of {title, url} dicts, not strings —
    # worth honouring rather than working around, because that footer is the same
    # mechanism every other ICDEV deck uses to say where its numbers came from.
    for s in out:
        s.setdefault("citations", []).append({
            "title": f"reconciled from source documents · snapshot {snap.sha}",
            "url": "",
        })

    return out, snap


def render(slides: list[dict], *, theme: str = "", title: str = "Bill of Materials") -> str:
    """Hand the specs to ICDEV's slide builder. Returns a .pptx path."""
    from tools.slides.constants import DEFAULT_THEME, THEMES
    from tools.slides.pptx_builder import build

    theme = theme or DEFAULT_THEME
    if theme not in THEMES:
        raise ValueError(f"unknown theme '{theme}' (have: {', '.join(THEMES)})")

    return build(slides, theme=theme, title=title)
