# CUI // SP-CTI
"""Deciding whether two descriptions of a thing are the same thing.

Used twice, and the second use is the one that justifies the design: conformance
matches a component drawn on a diagram against a line in a bill of materials, and
reconciliation matches a line in one BOM against a line in another. The diagram
says "Catalyst 9500". The BOM says "C9500-16X-A". A human sees those are the same
switch instantly and no exact-match algorithm ever will.

Everything here is deterministic. ``difflib`` is stdlib, so this works air-gapped;
there is no ``rapidfuzz`` in requirements and the embedding path is network-bound,
which makes it useless in the environments this has to run in.

The scoring is a LADDER, not a single number, because no single measure survives
the real cases:

  exact part number   — the strong signal, when it exists at all
  trigram similarity  — the same SKU written two ways: hyphens moved, a suffix
                        dropped, a truncated catalogue number. Exact match scores
                        ZERO on these; trigrams score around 0.5.
  description overlap — when one source has no part numbers whatsoever, which is
                        routine
  function            — a firewall is a firewall even when two vendors' products
                        share not one character. This is the rung that notices two
                        products competing for the same job at wildly different
                        prices, which is frequently the most valuable thing the
                        engine will ever tell somebody.

Nothing here decides anything on its own. It returns a score and the reason for
it, and the caller decides whether that is enough — or whether it needs a person.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

# Words that appear in every line of every BOM and therefore distinguish nothing.
_NOISE_WORDS = frozenset(
    "the a an and or of for to with in on at by is are be per each new used "
    "unit units item items qty quantity total cost price ea pcs pack set kit "
    "inc incl including support licence license software hardware system "
    "assembly module series model type approx est estimated".split()
)

_ALNUM = re.compile(r"[^a-z0-9]+")
_WORD = re.compile(r"[a-z0-9]+")

# A part number is specific in a way a description is not: letters AND digits,
# usually with punctuation, and never a plain English word.
_PART_LIKE = re.compile(r"^(?=.*\d)(?=.*[a-z])[a-z0-9][a-z0-9\-/.]{3,}$", re.IGNORECASE)


def normalize_part(text: str) -> str:
    """Strip a part number to its comparable core.

    Vendors, resellers and spreadsheets punctuate the same SKU differently, and
    the punctuation carries no meaning. What matters is the sequence of
    alphanumerics.
    """
    return _ALNUM.sub("", (text or "").lower())


def tokens(text: str) -> set[str]:
    """Meaningful words. The ones every BOM shares are dropped."""
    return {
        w for w in _WORD.findall((text or "").lower())
        if len(w) > 2 and w not in _NOISE_WORDS
    }


def looks_like_part_number(text: str) -> bool:
    return bool(_PART_LIKE.match((text or "").strip()))


# The tokens that make two otherwise-identical descriptions describe different
# things: a length, a port count, a capacity, a model number.
_DISCRIMINATOR = re.compile(
    r"\b(\d+(?:\.\d+)?)\s*(m|cm|mm|ft|in|u|g|gb|tb|gbe|ge|w|kw|va|kva|port|ports|"
    r"pack|node|nodes|seat|seats|core|cores|user|users|year|yr|month|mo)\b",
    re.IGNORECASE,
)
# A bare model number inside a description: "Catalyst 9300", "DL380 Gen12".
_MODEL_IN_TEXT = re.compile(r"\b(\d{3,5})(?:[-\s]?[a-z]{1,3}\d*)?\b", re.IGNORECASE)


def discriminators(text: str) -> frozenset[str]:
    """What makes this description different from a nearly identical one.

    In a bill of materials the measurement IS the product. "Fibre 3m" and "fibre
    10m" are ninety percent the same string and are two cables a buyer needs both
    of; a similarity score reads the difference as noise, because it is one token
    out of eight, when it is the only token that matters.
    """
    out: set[str] = set()
    for value, unit in _DISCRIMINATOR.findall(text or ""):
        out.add(f"{float(value):g}{unit.lower()}")
    for model in _MODEL_IN_TEXT.findall(text or ""):
        # Years are not model numbers. Neither are quantities already captured
        # above.
        if 1900 <= int(model) <= 2100:
            continue
        out.add(f"m{model}")
    return frozenset(out)


def trigrams(text: str) -> set[str]:
    s = normalize_part(text)
    if len(s) < 3:
        return {s} if s else set()
    return {s[i:i + 3] for i in range(len(s) - 2)}


def trigram_similarity(a: str, b: str) -> float:
    """Jaccard over character 3-grams.

    This is the rung that catches a SKU written two ways — a hyphen moved, a
    suffix dropped, a catalogue number truncated. Exact comparison scores ZERO on
    those; trigrams score around one half. A single-matcher design fails this case
    every time, and the case is not rare: it is what happens whenever two people
    type the same part number from two different quotes.
    """
    ta, tb = trigrams(a), trigrams(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def token_similarity(a: str, b: str) -> float:
    """How much these two descriptions agree.

    Jaccard AND containment, whichever is kinder, because the two sides of a real
    comparison are rarely the same length and Jaccard punishes exactly the wrong
    one for it.

    A design label is a summary — "Cisco Duo (MFA / Zero Trust)". The BOM line is
    the full specification — "Cisco Duo MFA, 25-user, 3yr Advantage". They share
    every word the label has, and Jaccard scores that 0.33 because the BOM carried
    more detail. Penalising a bill of materials for being SPECIFIC is precisely
    backwards, and it made the coverage check report a component as unfunded while
    the line paying for it sat two rows away.

    Containment is discounted slightly so that a one-word label cannot match
    everything it happens to appear inside.
    """
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return 0.0
    shared = len(ta & tb)
    jaccard = shared / len(ta | tb)

    if shared < 2:
        # One word in common is a coincidence, not a match. Without this guard a
        # one-word label would score full containment against everything it
        # happens to appear inside.
        return jaccard

    # Lightly discounted, so a genuine symmetric match still ranks above a
    # merely-contained one when both are available.
    containment = (shared / min(len(ta), len(tb))) * 0.95
    return max(jaccard, containment)


def sequence_similarity(a: str, b: str) -> float:
    """stdlib. Deliberately: this has to run air-gapped."""
    return SequenceMatcher(None, normalize_part(a), normalize_part(b)).ratio()


@dataclass
class Match:
    score: float
    method: str          # one of constants.MATCH_METHODS
    reason: str

    @property
    def is_confident(self) -> bool:
        return self.score >= 0.92 and self.method == "exact_part"


def score(
    a_desc: str,
    b_desc: str,
    *,
    a_part: str = "",
    b_part: str = "",
    a_mfr: str = "",
    b_mfr: str = "",
    a_function: str = "",
    b_function: str = "",
) -> Match:
    """How alike are these two things, and why.

    Returns the strongest rung that fires, with its reason attached, because a
    reviewer asking "why did you merge these?" is entitled to an answer better
    than a number.
    """
    # ── B1: the part numbers agree exactly ───────────────────────────────────
    #
    # But only if they ARE part numbers. A BOM routinely fills that column with a
    # placeholder — "Generic", "Various", "TBD", "Misc" — meaning "no specific part
    # was chosen". Comparing those as if they were SKUs merges every accessory in
    # the document into one cluster with total confidence, and the resulting
    # "identical part number" reason reads exactly like a correct answer.
    #
    # A real part number contains a digit. That single requirement is what stands
    # between an exact-match rule and nine unrelated items agreeing they are the
    # same thing.
    pa = normalize_part(a_part) if looks_like_part_number(a_part) else ""
    pb = normalize_part(b_part) if looks_like_part_number(b_part) else ""
    if pa and pb and pa == pb:
        return Match(1.0, "exact_part", f"identical part number ({a_part})")

    mfr_ok = (
        not a_mfr or not b_mfr
        or normalize_part(a_mfr) == normalize_part(b_mfr)
        or token_similarity(a_mfr, b_mfr) > 0.5
    )

    # ── B2: the same SKU, written differently ────────────────────────────────
    if pa and pb:
        # A digit SUBSTITUTION is a different product; a TRUNCATION is the same one
        # written shorter.
        #
        # "C9300-24T" and "C9200-24T" are the same length and differ in one
        # character — they are two different switches, and no similarity score
        # should be allowed to say otherwise. Whereas "MPU2-2032DAC-400" and
        # "MPU2032DAC" have different lengths because one is the other with the
        # suffix dropped, which is what happens when two people copy a SKU off two
        # different quotes.
        #
        # Length is a crude discriminator and it is the right one here: a model
        # number that has been shortened loses characters; a model number that
        # names a different model swaps them.
        if len(pa) == len(pb):
            return Match(
                0.0, "trigram",
                f"different part numbers of the same shape ({a_part} / {b_part}) — "
                f"a substituted digit is a different model, not a different spelling",
            )

        tri = trigram_similarity(a_part, b_part)
        if tri >= 0.45 and mfr_ok:
            return Match(
                min(0.90, 0.55 + tri * 0.4), "trigram",
                f"part numbers are near-identical ({a_part} / {b_part}, "
                f"{tri:.0%} character overlap)"
                + ("" if not a_mfr else f" and the manufacturer agrees ({a_mfr})"),
            )

        # BOTH lines name a specific part, and the parts do not match. That is a
        # DISQUALIFIER, and it must not fall through to the description rungs.
        #
        # Two rows can be described almost identically — "Cisco 93180YC-FXP" and
        # "Cisco 9348GC-FXP", "OM4 fibre 3m" and "OM4 fibre 10m" — while naming
        # different products. The description is a summary and it is allowed to be
        # vague. The part number is a commitment. When both sides made that
        # commitment and they disagree, the commitment wins.
        if a_function and b_function and a_function == b_function:
            return Match(
                0.5, "function",
                f"different products ({a_part} / {b_part}) doing the same job — "
                f"a choice, not a duplicate",
            )
        return Match(
            0.0, "trigram",
            f"different part numbers ({a_part} / {b_part}): whatever the "
            f"descriptions say, these are not the same item",
        )

    # ── B3: descriptions agree ───────────────────────────────────────────────
    #
    # Before believing two descriptions, check the thing that DISTINGUISHES them.
    #
    # "OM4 fibre 3m" and "OM4 fibre 10m" are ninety percent the same string and
    # are two different cables. "Catalyst 9300-24T" and "Catalyst 9200-24T" are two
    # different switches. "Patch cable 3ft" and "patch cable 10ft" are two line
    # items a buyer needs both of.
    #
    # In a bill of materials the measurement IS the product. A similarity score
    # reads that difference as noise — it is one token out of eight — when it is
    # in fact the only token that matters. Merging them prices two purchases as
    # one, and the shortfall does not appear until somebody is standing in a data
    # centre holding a cable that does not reach.
    da, db = discriminators(a_desc), discriminators(b_desc)
    if da and db and da != db:
        return Match(
            0.0, "trigram",
            f"the descriptions differ where it counts ({'/'.join(sorted(da))} vs "
            f"{'/'.join(sorted(db))}) — in a bill of materials the measurement is "
            f"the product",
        )

    tok = token_similarity(a_desc, b_desc)
    seq = sequence_similarity(a_desc, b_desc)

    # Character similarity is discounted hard on SHORT descriptions, where it lies.
    #
    # "PDU (Rack-Mount)" and "UPS (Rack-Mount)" share nine of twelve characters, so
    # the sequence matcher calls them 0.83 alike — and they are a power distribution
    # unit and an uninterruptible power supply, which are not remotely the same
    # purchase. On a short string the shared characters are the packaging; the three
    # that differ are the entire product.
    #
    # Long descriptions are the opposite: there, character overlap is real evidence,
    # because there is enough of it to be more than a coincidence.
    span = min(len(a_desc or ""), len(b_desc or ""))
    desc = max(tok, seq * (0.9 if span >= 25 else 0.7))
    if desc >= 0.55:
        return Match(
            min(0.88, desc), "trigram",
            f"descriptions agree ({desc:.0%}): \"{a_desc[:40]}\" / \"{b_desc[:40]}\"",
        )

    # ── B4: same job, different product ──────────────────────────────────────
    #
    # Two vendors' firewalls may share not one character, and one of them may cost
    # twenty times the other. Noticing that they are competing for the same slot is
    # often the single most valuable thing this engine does — and it is the ONLY
    # rung that works when a whole source has no part numbers at all, which is
    # entirely routine.
    if a_function and b_function and a_function == b_function:
        return Match(
            0.5 + desc * 0.3, "function",
            f"both are a {a_function.replace('-', ' ')} — different products, "
            f"same job. This is a choice, not a duplicate.",
        )

    return Match(desc, "trigram", "no meaningful similarity")


def best_match(
    query_desc: str,
    candidates: list[dict],
    *,
    query_part: str = "",
    query_mfr: str = "",
    query_function: str = "",
    threshold: float = 0.55,
) -> tuple[dict | None, Match]:
    """The closest candidate, if any clears the bar.

    Candidates are dicts with at least ``description``; optionally ``part_number``,
    ``manufacturer``, ``function_slug``.
    """
    best: tuple[dict, Match] | None = None
    for cand in candidates:
        m = score(
            query_desc, cand.get("description", ""),
            a_part=query_part, b_part=cand.get("part_number", ""),
            a_mfr=query_mfr, b_mfr=cand.get("manufacturer", ""),
            a_function=query_function, b_function=cand.get("function_slug", ""),
        )
        if best is None or m.score > best[1].score:
            best = (cand, m)

    if best is None or best[1].score < threshold:
        return None, Match(best[1].score if best else 0.0, "trigram", "nothing close enough")
    return best
