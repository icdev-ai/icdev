#!/usr/bin/env python3
# CUI // SP-CTI
"""Resolve the merge conflicts that are provably not disagreements.

Measured across 2026-08-09: of roughly ten conflicts a human resolved by hand,
the same two shapes accounted for six, and BOTH were resolved the same way every
single time — keep both sides.

  * **Additive doc blocks.** Two branches each append an independent block to a
    shared reference file (`docs/reference/commands.md` gained a `--merge` block
    and a `--requeue` block on the same day). Neither replaces the other; the
    resolution is to drop the markers and keep both.
  * **Allocation-number collisions.** Two branches each take "the next free
    number" for a numbered section — `### Gap 52` was allocated twice, then
    `### Gap 56` was too. The file's own Gap 50 note records this happening
    before. The content does not conflict at all; only the label does.

WHAT THIS DELIBERATELY WILL NOT DO. It never touches code, never resolves a
conflict where either side DELETED a line, and never runs outside an allowlist of
files whose format it understands. A conflict in Python is a disagreement about
behaviour and belongs to a person. The value here is removing the mechanical
repetition, not automating judgement — a resolver that guesses would be worse
than the ten minutes it saves, because a wrong merge is discovered much later
than an unresolved one.

Returns None when it cannot prove a case is safe, so the caller falls through to
the existing "abort and escalate" path unchanged.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import List, Optional, Tuple

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.logging.icdev_logger import get_logger  # noqa: E402

logger = get_logger(__name__)

#: Files whose conflict shape this module understands. An allowlist, not a
#: pattern: "looks like markdown" is not evidence that keeping both sides is
#: correct, and the cost of being wrong is a silent bad merge.
RESOLVABLE_PATHS = (
    "docs/reference/commands.md",
    "docs/security/sandbox-coverage.md",
    "tools/manifest/",          # union-merged by convention already
)

#: CRLF-tolerant on purpose. The file is read with ``newline=""`` so its on-disk
#: endings survive, and git writes conflict markers using the file's own endings
#: — so on Windows the separator is "\r\n=======\r\n". A pattern hard-coded to
#: "\n" matched nothing there: every unit test written with LF strings passed
#: while the resolver silently declined every REAL conflict. Caught only by
#: running it against an actual `git rebase`, which is why that test exists.
_CONFLICT_RE = re.compile(
    r"<<<<<<< [^\r\n]*\r?\n(?P<ours>.*?)\r?\n?=======\r?\n"
    r"(?P<theirs>.*?)>>>>>>> [^\r\n]*\r?\n",
    re.S,
)

#: `### Gap 57 — Title`. The number is an allocation, not an identity.
#: Spelled out because this file is edited through shells and heredocs that
#: have mangled a bare escape more than once.
NEWLINE = chr(10)

_NUMBERED_HEADING_RE = re.compile(r"^(?P<hashes>#{2,4}) Gap (?P<num>\d+) ", re.M)


def is_resolvable_path(path: str) -> bool:
    p = (path or "").replace("\\", "/")
    return any(marker in p for marker in RESOLVABLE_PATHS)


#: A line must recur at least this many times in the document before it counts as
#: template rather than content. Two occurrences is just the conflict itself —
#: the two sides of an unresolved hunk — so the threshold has to clear that.
_BOILERPLATE_MIN_OCCURRENCES = 4


def _boilerplate_lines(text: str) -> set:
    """Lines this document repeats so often they carry no identity.

    The purity test below asks whether both sides touched the SAME text. A shared
    line normally answers yes — but in a document with a fixed per-entry template
    it answers nothing at all, because every entry has those lines by
    construction.

    Measured on the real conflict that motivated this (docs/security/
    sandbox-coverage.md, two independently-added `### Gap 57` blocks): the two
    sides shared exactly two lines,

        - **Decision:** **bypass-documented**
        - **Guardrails:**

    both of which appear in nearly every Gap entry in the file. The resolver
    declined, so the one document it was written for was the one it could never
    resolve.

    Frequency is the evidence, not a hand-written pattern list. A list of
    "known boilerplate" would go stale the moment the template changed and would
    have to be maintained per document; recurrence is measured from the file
    itself and adapts on its own. A line unique to these two sides still blocks
    the resolution, which is the property that matters.
    """
    counts = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(("<<<<<<<", "=======", ">>>>>>>")):
            continue
        counts[line] = counts.get(line, 0) + 1
    return {ln for ln, n in counts.items() if n >= _BOILERPLATE_MIN_OCCURRENCES}


def _is_purely_additive(ours: str, theirs: str, boilerplate: set = frozenset()) -> bool:
    """True when neither side removed a line the other kept.

    Both sides ADDING different things is the case this module exists for. If
    either side dropped content, someone made an editorial decision and it is not
    ours to re-make.
    """
    ours_lines = {ln.strip() for ln in ours.splitlines() if ln.strip()}
    theirs_lines = {ln.strip() for ln in theirs.splitlines() if ln.strip()}
    if not ours_lines or not theirs_lines:
        # One side empty means the other side's block was deleted, not added.
        return False
    # A shared line means the two sides edited the SAME text — a rewrite, not two
    # independent additions. Template lines are excluded: they are shared because
    # the document's format says so, not because the two sides met.
    shared = (ours_lines & theirs_lines) - boilerplate
    if not shared:
        # ...but boilerplate alone must not carry a hunk. If NOTHING here is
        # distinguishable content, there is nothing to prove the two sides are
        # independent additions rather than one rewrite of a template block.
        return bool(ours_lines - boilerplate) and bool(theirs_lines - boilerplate)
    return False


def _renumber_collisions(merged: str, spans: list) -> tuple:
    """Renumber allocations THIS resolution duplicated — and nothing else.

    `spans` are the (start, end) offsets of the text this module just merged.
    Only headings inside those spans are candidates.

    That restriction is the whole correctness of this function. Scanning the
    whole document renumbers every duplicate it finds, and this document has many
    that predate the conflict and are nobody's business here: on the real
    sandbox-coverage.md conflict an unrestricted pass rewrote TWELVE headings,
    turning `### Gap 13 — DataBridge Secret Resolvers` into Gap 59 six hundred
    lines from anything being merged. Cross-references break silently and the
    diff is unreviewable — far worse than the conflict it set out to fix. That
    bug sat here unnoticed because the purity test always declined this file
    first; widening the test is what exposed it.

    Only ever moves a number UP, and only for a collision: the occurrence already
    in the document keeps its number, because something already references it.
    Renumbering the one that arrived second is the choice a human made all three
    times this happened.
    """
    notes = []
    used = {int(m.group("num")) for m in _NUMBERED_HEADING_RE.finditer(merged)}
    next_free = (max(used) + 1) if used else 1

    def _inside(pos):
        return any(lo <= pos < hi for lo, hi in spans)

    # Numbers claimed OUTSIDE the merged text. A heading inside a span collides
    # only if one of these already holds its number; two headings that both
    # arrived in this same merge are caught by `claimed` as we walk.
    claimed = {int(m.group("num")) for m in _NUMBERED_HEADING_RE.finditer(merged)
               if not _inside(m.start())}

    edits = []
    for m in _NUMBERED_HEADING_RE.finditer(merged):
        if not _inside(m.start()):
            continue
        num = int(m.group("num"))
        if num not in claimed:
            claimed.add(num)
            continue
        replacement = m.group(0).replace("Gap %d " % num, "Gap %d " % next_free, 1)
        edits.append((m.start(), m.end(), replacement))
        notes.append("Gap %d -> Gap %d (duplicate allocation)" % (num, next_free))
        claimed.add(next_free)
        next_free += 1

    if not edits:
        return merged, notes
    pieces, cursor = [], 0
    for start, end, text in edits:
        pieces.append(merged[cursor:start])
        pieces.append(text)
        cursor = end
    pieces.append(merged[cursor:])
    return "".join(pieces), notes


def resolve_text(path: str, text: str) -> Optional[Tuple[str, List[str]]]:
    """Resolve a conflicted file, or None when it is not provably safe.

    Returns ``(resolved_text, notes)``. `notes` is what the caller should put in
    the commit message — an automatic resolution that does not say what it did is
    indistinguishable from a bad merge when someone reads the history later.
    """
    if not is_resolvable_path(path):
        return None
    hunks = list(_CONFLICT_RE.finditer(text))
    if not hunks:
        return None

    boilerplate = _boilerplate_lines(text)
    for h in hunks:
        if not _is_purely_additive(h.group("ours"), h.group("theirs"), boilerplate):
            logger.info("conflict_resolvers: %s has a non-additive hunk — leaving "
                        "it for a human", path)
            return None

    notes = [f"{len(hunks)} additive conflict(s) in {path}: kept both sides"]
    # Order: theirs (already on the base) first, then ours, so the file reads
    # in the order the entries landed. The spans are recorded as we build:
    # renumbering must be confined to what this resolution actually merged.
    spans, pieces, cursor = [], [], 0
    for h in hunks:
        replacement = (h.group("theirs").rstrip(NEWLINE) + NEWLINE
                       + h.group("ours").rstrip(NEWLINE) + NEWLINE)
        pieces.append(text[cursor:h.start()])
        begin = sum(len(x) for x in pieces)
        pieces.append(replacement)
        spans.append((begin, begin + len(replacement)))
        cursor = h.end()
    pieces.append(text[cursor:])
    merged = "".join(pieces)
    if "<<<<<<<" in merged or ">>>>>>>" in merged:
        return None

    merged, renum = _renumber_collisions(merged, spans)
    notes.extend(renum)
    return merged, notes


def resolve_file(path: Path) -> Optional[List[str]]:
    """Resolve one conflicted file in place. None when it was left alone."""
    try:
        with open(path, encoding="utf-8", newline="") as fh:
            text = fh.read()
    except OSError as exc:
        logger.debug("conflict_resolvers: cannot read %s: %s", path, exc)
        return None
    rel = str(path).replace("\\", "/")
    outcome = resolve_text(rel, text)
    if outcome is None:
        return None
    resolved, notes = outcome
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(resolved)
    logger.info("conflict_resolvers: resolved %s (%s)", path, "; ".join(notes))
    return notes
