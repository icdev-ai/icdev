# CUI // SP-CTI
"""Four bills of materials, one defensible number.

The pipeline: block, score, replay what a human already decided, adjudicate only
what is genuinely ambiguous, cluster, pick a winner, and refuse — loudly — to
resolve the things that are not ours to resolve.

Three properties are load-bearing, and each one exists because its absence is a
specific disaster:

**A model never touches a number.** The adjudicator is given descriptions and part
numbers and NOT prices, so it cannot anchor on money. Its response schema has no
numeric field but ``confidence``. Every string it returns must appear verbatim in
the source or the verdict is discarded. If its prose contains a currency figure,
the whole response is VOIDED. These are code-level gates, not requests in a
prompt: a prompt is advice, and this is not a matter on which advice is enough.

**Human decisions are keyed on line hashes, never on cluster ids.** Clusters are
recomputed on every run. Key a customer's approvals to them and the next upload
renumbers everything and silently orphans every decision they ever made. That is
the classic entity-resolution re-run bug, and it destroys weeks of work without
raising a single error.

**Refusing is an answer.** Two products doing the same job at wildly different
prices are a CHOICE somebody has to make out loud. Averaging them produces a
number that is not a compromise but a fiction, and it goes into a budget with our
name on it. Such a cluster contributes ZERO to the committed total, carries the
range, and waits for a person.

Public API::

    reconcile(lines, sources, decisions=(), adjudicator=None) -> Reconciliation
"""
from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from tools.bom import constants as C
from tools.bom.findings import Evidence, Finding
from tools.bom.lines import ExtractedLine
from tools.bom.matching import (
    Match,
    looks_like_part_number,
    normalize_part,
    score as match_score,
    tokens,
)

# Any currency figure in a model's prose voids the entire response. Broad on
# purpose: a false void costs one adjudication, and a hallucinated number in front
# of a budget owner costs the product.
_MONEY_IN_PROSE = re.compile(r"[$£€]\s?\d|\b\d[\d,]*\.\d{2}\b|\b\d{1,3}(?:,\d{3})+\b")


# Strong enough to put two lines side by side and show a human the range between
# them. NOT strong enough to merge them silently — that still requires an exact
# part number. The gap between these two thresholds is where the product's
# honesty lives.
_PROVISIONAL_SCORE = 0.70


def pair_key(a_hash: str, b_hash: str) -> str:
    """Stable identity for a pair of lines, order-independent.

    Keyed on the LINE HASHES — which are hashes of the bytes as they arrived, not
    of anything a parser produced. So a human's verdict survives an upload, a
    re-cluster, and an improvement to the parser itself.
    """
    lo, hi = sorted((a_hash, b_hash))
    return hashlib.sha256(f"{lo}\x1f{hi}".encode()).hexdigest()[:24]


@dataclass
class Decision:
    """What a human (or a model, or arithmetic) concluded about one pair."""

    pair_key: str
    a_line_hash: str
    b_line_hash: str
    verdict: str                 # C.MATCH_VERDICTS
    confidence: float = 0.0
    decided_by: str = "deterministic"   # C.DECISION_ACTORS
    reason: str = ""

    @property
    def is_binding(self) -> bool:
        """Only a person settles a pair for good.

        A model's verdict is a proposal that lands in a review queue; it is
        replayed so we do not pay to ask twice, but it never counts as settled.
        """
        return self.decided_by == "human"


@dataclass
class Source:
    """What we know about where a line came from."""

    source_id: str
    filename: str = ""
    credibility_tier: str = C.DEFAULT_CREDIBILITY
    authority_rank: int = 0
    role: str = "bom_claim"
    as_of: str = ""

    @property
    def rank(self) -> int:
        return C.CREDIBILITY_RANK.get(self.credibility_tier, 99)


@dataclass
class Cluster:
    cluster_id: str
    members: list[str] = field(default_factory=list)     # line_id
    winner_line_id: str = ""
    function_slug: str = ""
    resolved_qty: float | None = None
    resolved_unit_price: float | None = None
    resolved_price_basis: str = C.DEFAULT_PRICE_BASIS
    price_min: float | None = None
    price_max: float | None = None
    status: str = "pending_review"
    match_confidence: float = 0.0
    rationale: str = ""

    @property
    def committed(self) -> bool:
        """Does this cluster's money count toward the total?

        Only when a person has accepted it, or when nothing about it was ever in
        doubt. Anything still under review contributes zero — never its cheapest
        branch, never its mean.
        """
        return self.status == "accepted"


@dataclass
class Reconciliation:
    clusters: list[Cluster] = field(default_factory=list)
    pending: list[Decision] = field(default_factory=list)   # proposals awaiting a human
    findings: list[Finding] = field(default_factory=list)
    llm_calls: int = 0

    @property
    def committed_total(self) -> float:
        return sum(
            (c.resolved_qty or 0) * (c.resolved_unit_price or 0)
            for c in self.clusters if c.committed
        )


# ── Blocking ─────────────────────────────────────────────────────────────────

def _blocks(line: ExtractedLine) -> set[str]:
    """The buckets a line belongs to. A pair is a candidate on ANY collision.

    A ladder, because no single key survives real data. Part numbers are the strong
    signal and one source in four does not have them at all. Descriptions catch the
    rest. Function catches two vendors' products that share not one character and
    compete for the same slot — which is frequently the most valuable thing the
    engine ever notices.
    """
    keys: set[str] = set()

    # Only a REAL part number blocks on part. The column is routinely filled with
    # "Generic" or "Various", meaning no specific part was chosen — and bucketing
    # on that word puts every accessory in the document into one bucket.
    part = normalize_part(line.part_number) if looks_like_part_number(line.part_number) else ""
    if len(part) >= 4:
        keys.add(f"part:{part}")

        # Character trigrams, not a prefix.
        #
        # A prefix bucket assumes the two spellings of a SKU agree at the START,
        # and the interesting ones do not: "MPU2-2032DAC-400" and "MPU2032DAC"
        # normalize to strings whose first six characters already differ. So the
        # pair the whole matching ladder was BUILT to catch was never even offered
        # to it — blocking silently discarded it before scoring ever ran.
        #
        # Trigrams collide wherever the two strings agree anywhere, which is what
        # near-identical part numbers actually do. Cheap, and the oversized-bucket
        # guard below stops a common fragment from becoming a bottleneck.
        for i in range(len(part) - 2):
            keys.add(f"tri:{part[i:i + 3]}")

    # Three characters, not five.
    #
    # "KVM over IP" contains no word of five characters, so a five-character
    # minimum gave that line NO description bucket at all — it could never be
    # compared with anything. Acronyms are exactly the short words that identify a
    # product, and excluding them excludes the products they name.
    for tok in tokens(line.description):
        if len(tok) >= 3:
            keys.add(f"tok:{tok}")

    fn = getattr(line, "function_slug", "")
    if fn:
        keys.add(f"fn:{fn}")

    return keys


def candidate_pairs(lines: list[ExtractedLine]) -> set[tuple[int, int]]:
    buckets: dict[str, list[int]] = defaultdict(list)
    for i, line in enumerate(lines):
        for key in _blocks(line):
            buckets[key].append(i)

    pairs: set[tuple[int, int]] = set()
    for members in buckets.values():
        # A bucket that half the corpus falls into is not a signal. Skipping it
        # costs nothing: anything genuinely alike will also collide on a narrower
        # key.
        if len(members) > 60:
            continue
        for i, a in enumerate(members):
            for b in members[i + 1:]:
                pairs.add((min(a, b), max(a, b)))
    return pairs


# ── The adjudicator's cage ───────────────────────────────────────────────────

# The ONLY shape a model may answer in. Note what is absent: there is no numeric
# property here except a confidence. Prices are referenced by line id, so the
# model never sees the money during identity adjudication and cannot anchor on it.
ADJUDICATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["relation", "confidence", "reason"],
    "properties": {
        "relation": {"enum": list(C.MATCH_VERDICTS)},
        "canonical": {"enum": ["a", "b", None]},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "reason": {"type": "string", "maxLength": 240},
        "evidence_spans": {
            "type": "array",
            "items": {"type": "string", "maxLength": 120},
            "maxItems": 4,
        },
    },
}


def _ground_token(value: str, haystack: str) -> bool:
    """Did this string actually come from the source?

    Case-folded, alphanumerics only. A model that returns a phrase which does not
    appear in either line has invented it, and an invented justification is worse
    than no justification: it is persuasive.
    """
    core = re.sub(r"[^a-z0-9]", "", (value or "").lower())
    if not core:
        return False
    hay = re.sub(r"[^a-z0-9]", "", (haystack or "").lower())
    return core in hay


def adjudication_prompt(a: ExtractedLine, b: ExtractedLine) -> dict[str, Any]:
    """What the model is allowed to see.

    Descriptions, part numbers, manufacturers. NOT prices, NOT quantities, NOT
    totals. It is being asked whether two things are the same thing, and the money
    is irrelevant to that question — while being extremely relevant to the model's
    temptation to reason backwards from it.
    """
    return {
        "a": {
            "id": "a",
            "description": a.description,
            "part_number": a.part_number,
            "manufacturer": a.manufacturer,
            "source": f"{a.source_document}!{a.source_sheet}",
        },
        "b": {
            "id": "b",
            "description": b.description,
            "part_number": b.part_number,
            "manufacturer": b.manufacturer,
            "source": f"{b.source_document}!{b.source_sheet}",
        },
        "question": (
            "Are these the same item, two different items that do the same job, "
            "alternatives, or unrelated? Do not mention prices."
        ),
    }


def validate_adjudication(
    raw: dict[str, Any] | None, a: ExtractedLine, b: ExtractedLine
) -> tuple[str, float, str] | None:
    """Accept a model's verdict, or throw it away.

    Every gate here is code. None of it is a request in a prompt, because a prompt
    is advice and this is not a matter on which advice is enough.
    """
    if not isinstance(raw, dict):
        return None

    relation = raw.get("relation")
    if relation not in C.MATCH_VERDICTS:
        return None

    try:
        confidence = float(raw.get("confidence", 0.0))
    except (TypeError, ValueError):
        return None
    if not 0.0 <= confidence <= 1.0:
        return None

    reason = str(raw.get("reason") or "").strip()

    # A currency figure in the prose VOIDS the whole response. The model was never
    # shown a price; if one appears in its answer it either invented it or inferred
    # it, and both are disqualifying.
    if _MONEY_IN_PROSE.search(reason):
        return None

    # Every span it claims to have read must actually be there. An invented
    # justification is worse than none, because it is persuasive.
    haystack = " ".join((
        a.description, a.part_number, a.manufacturer, a.raw_text,
        b.description, b.part_number, b.manufacturer, b.raw_text,
    ))
    for span in raw.get("evidence_spans") or []:
        if not _ground_token(str(span), haystack):
            return None

    return relation, confidence, reason


# ── Winner selection ─────────────────────────────────────────────────────────

def _basis_rank(basis: str) -> int:
    return C.PRICE_BASIS_RANK.get(basis, 99)


def _negated(as_of: str) -> str:
    """Sort dates descending inside an otherwise-ascending key.

    An unknown date sorts LAST, not first: a source that never said when it was
    written does not get to outrank one that did.
    """
    if not as_of:
        return ""
    return "".join(chr(0x10FFFF - ord(ch)) if ord(ch) < 0x10FFFF else ch for ch in as_of)


def choose_winner(
    members: list[ExtractedLine],
    sources: dict[str, Source],
) -> tuple[ExtractedLine | None, str, bool]:
    """Which line's numbers we take. Returns (winner, why, needs_human).

    The order is a stored policy in a real deployment; this is the default. What is
    NOT negotiable are the refusals below.
    """
    if not members:
        return None, "", False
    if len(members) == 1:
        return members[0], "the only line for this item", False

    def src(ln: ExtractedLine) -> Source:
        return sources.get(ln.source_document) or Source(source_id=ln.source_document)

    # A copy of a document can never win, and never contributes. It is the same
    # money, and it has already lost the formulas.
    live = [ln for ln in members if src(ln).role != "derived"]
    if not live:
        return None, "every line here comes from a copy of another document", False

    # ── Refusal 1: two sources the customer VOUCHED for, disagreeing. ────────
    #
    # This is a real dispute between two things they told us to trust. The tool
    # does not get to pick a side, and quietly picking one would be the worst
    # possible use of the authority they gave us.
    authoritative = [ln for ln in live if src(ln).credibility_tier in C.NEVER_AUTO_RESOLVE_TIERS]
    prices = {round(ln.unit_price, 2) for ln in authoritative if ln.unit_price}
    if len(prices) > 1:
        return None, (
            "two sources you marked authoritative disagree about this. That is a "
            "dispute between two things you vouched for, and it is not ours to settle."
        ), True

    # ── Refusal 2: a price spread too wide to be a rounding error. ───────────
    #
    # Two products doing the same job at wildly different prices are a CHOICE.
    # Averaging them produces a fiction, and the fiction goes into a budget with
    # our name on it.
    priced = [ln for ln in live if ln.unit_price]
    if priced:
        lo = min(ln.unit_price for ln in priced)
        hi = max(ln.unit_price for ln in priced)
        if lo > 0 and hi / lo > C.FORCED_REVIEW_PRICE_RATIO:
            return None, (
                f"the prices here differ by {hi / lo:.1f}x. That is not a rounding "
                f"error, it is a decision — and it is frequently the most valuable "
                f"thing in the whole bill of materials. Somebody has to choose out loud."
            ), True

    # Credibility FIRST, and that ordering is the point of the whole module. A
    # working draft never overrules a source the customer marked authoritative,
    # however specific the draft happens to be.
    def _rank(ln: ExtractedLine) -> tuple:
        s = src(ln)
        return (
            s.rank,                              # how much this source's word is worth
            s.authority_rank,
            0 if ln.part_number else 1,          # a real SKU beats a prose description
            _basis_rank(ln.price_basis),         # quoted > street > list > rom > unknown
            _negated(s.as_of),                   # newest, where we know
            ln.line_id,                          # deterministic, so runs are repeatable
        )

    winner = sorted(live, key=_rank)[0]
    s = src(winner)
    why = (
        f"{winner.source_document} is the most credible source carrying this "
        f"({s.credibility_tier})"
        + (", and it has a part number" if winner.part_number else "")
        + (f"; its price is a {winner.price_basis} figure"
           if winner.price_basis != "unknown" else
           "; its price basis is unknown, which is itself worth checking")
    )
    return winner, why, False


# ── The pipeline ─────────────────────────────────────────────────────────────

def reconcile(
    lines: list[ExtractedLine],
    sources: dict[str, Source] | None = None,
    decisions: Iterable[Decision] = (),
    adjudicator: Callable[[dict], dict | None] | None = None,
) -> Reconciliation:
    """Cluster the lines, pick winners, and refuse what is not ours to settle.

    ``adjudicator`` is injected. Without one the engine runs fully — every
    deterministic verdict stands, and the ambiguous band lands in the review queue
    instead of being guessed at. That is the ``--no-llm`` mode, and it cannot
    hallucinate because there is nothing in it that could.
    """
    sources = sources or {}
    out = Reconciliation()
    if not lines:
        return out

    by_hash = {ln.line_hash: ln for ln in lines}

    # Replay FIRST. A pair a human has ruled on is pinned, and is never re-sent to
    # a model — that is both a cost saving and, much more importantly, a stability
    # guarantee: their answer does not quietly change between runs.
    known: dict[str, Decision] = {}
    for d in decisions:
        if d.a_line_hash in by_hash and d.b_line_hash in by_hash:
            known[d.pair_key] = d

    # TWO edge sets, and the distinction is the whole point.
    #
    # `accepted` is certainty: identical part numbers. `provisional` is a strong
    # but inexact match — the same SKU written two ways, the same product
    # described differently.
    #
    # Provisional edges STILL FORM CLUSTERS, and that is not a technicality. If a
    # near-match never clustered, the two prices would never sit in the same place,
    # the spread rule could never fire, and the engine would quietly discover the
    # most valuable disagreement in the bill of materials and then throw it away.
    # A cluster holding any provisional edge is marked pending_review: it exists so
    # the RANGE is visible, not so the merge is assumed.
    accepted: list[tuple[int, int]] = []
    provisional: list[tuple[int, int]] = []

    for i, j in sorted(candidate_pairs(lines)):
        a, b = lines[i], lines[j]
        key = pair_key(a.line_hash, b.line_hash)

        prior = known.get(key)
        if prior is not None:
            if prior.verdict == "same_item" and prior.is_binding:
                accepted.append((i, j))
            elif prior.verdict == "same_item":
                out.pending.append(prior)
            continue

        m: Match = match_score(
            a.description, b.description,
            a_part=a.part_number, b_part=b.part_number,
            a_mfr=a.manufacturer, b_mfr=b.manufacturer,
        )

        if m.score >= C.AUTO_CLUSTER_SCORE and m.method == "exact_part":
            accepted.append((i, j))
            continue
        if m.score <= C.DISCARD_SCORE:
            continue

        if m.score >= _PROVISIONAL_SCORE:
            provisional.append((i, j))
            out.pending.append(Decision(
                pair_key=key,
                a_line_hash=a.line_hash, b_line_hash=b.line_hash,
                verdict="same_item", confidence=m.score,
                decided_by="deterministic", reason=m.reason,
            ))
            continue

        # The ambiguous band — the ONLY place a model is consulted.
        if adjudicator is not None:
            out.llm_calls += 1
            verdict = validate_adjudication(
                adjudicator(adjudication_prompt(a, b)), a, b
            )
            if verdict is not None:
                relation, confidence, reason = verdict
                if relation in ("different", "insufficient_evidence"):
                    continue
                out.pending.append(Decision(
                    pair_key=key,
                    a_line_hash=a.line_hash, b_line_hash=b.line_hash,
                    verdict=relation, confidence=confidence,
                    decided_by="llm", reason=reason,
                ))
                continue
            # The model's answer was thrown out by a gate. Fall through: the pair
            # goes to a human, unmerged. A discarded verdict is not a merge.

        out.pending.append(Decision(
            pair_key=key,
            a_line_hash=a.line_hash, b_line_hash=b.line_hash,
            verdict="same_item", confidence=m.score,
            decided_by="deterministic", reason=m.reason,
        ))

    # ── Union-Find over ACCEPTED edges only ─────────────────────────────────
    parent = list(range(len(lines)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i, j in accepted + provisional:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[rj] = ri

    provisional_nodes = {i for pair in provisional for i in pair}

    groups: dict[int, list[int]] = defaultdict(list)
    for i in range(len(lines)):
        groups[find(i)].append(i)

    for root, idxs in sorted(groups.items()):
        members = [lines[i] for i in idxs]
        cid = f"c{root:04d}"

        winner, why, needs_human = choose_winner(members, sources)
        priced = [ln.unit_price for ln in members if ln.unit_price]

        cluster = Cluster(
            cluster_id=cid,
            members=[ln.line_id for ln in members],
            winner_line_id=winner.line_id if winner else "",
            price_min=min(priced) if priced else None,
            price_max=max(priced) if priced else None,
            rationale=why,
        )

        if winner is not None and not needs_human:
            # Numbers are COPIED. Never averaged, never adjusted, never blended.
            cluster.resolved_qty = winner.qty
            cluster.resolved_unit_price = winner.unit_price
            cluster.resolved_price_basis = winner.price_basis

            # A cluster where the sources AGREE needs no human.
            #
            # Sending it to review anyway is not merely cautious, it is corrosive:
            # a queue full of items that require no thought trains people to click
            # through it, and then the one that mattered gets clicked through too.
            # Ask only about disagreement.
            #
            # Copies do not count as agreement or as disagreement — a print of a
            # workbook restating the workbook's own figure is not a second opinion.
            live_prices = {
                round(ln.unit_price, 2)
                for ln in members
                if ln.unit_price
                and (sources.get(ln.source_document) or Source("")).role != "derived"
            }
            settled = len(live_prices) <= 1
            # A cluster resting on an inexact match is a PROPOSAL, however neatly
            # its prices happen to line up. "These two look like the same thing"
            # is a judgement, and judgements get confirmed.
            is_provisional = any(i in provisional_nodes for i in idxs)
            cluster.status = (
                "accepted" if (settled and not is_provisional) else "pending_review"
            )
            cluster.match_confidence = 1.0 if len(members) == 1 else 0.9
        else:
            cluster.status = "pending_review"

        if needs_human:
            out.findings.append(Finding(
                finding_type=(
                    "authoritative_conflict"
                    if "authoritative" in why else "price_spread"
                ),
                kind="decision",
                severity="high",
                title=(
                    f'"{members[0].description[:56]}" — {len(members)} sources, '
                    f"and they do not agree"
                ),
                detail=(
                    why
                    + f"\n\nThe range is {cluster.price_min:,.2f} to "
                      f"{cluster.price_max:,.2f}. Until somebody chooses, this "
                      f"contributes ZERO to the committed total — not the cheapest "
                      f"branch, not the mean. Both of those would be inventions."
                    if cluster.price_min and cluster.price_max else why
                ),
                impact_usd=(
                    (cluster.price_max - cluster.price_min)
                    if (cluster.price_min and cluster.price_max) else None
                ),
                evidence=[
                    Evidence(
                        source_document=ln.source_document, sheet=ln.source_sheet,
                        locator=ln.source_locator, raw_text=ln.description,
                        line_id=ln.line_id,
                    )
                    for ln in members
                ],
                data={"cluster_id": cid, "members": len(members)},
            ))

        out.clusters.append(cluster)

    return out
