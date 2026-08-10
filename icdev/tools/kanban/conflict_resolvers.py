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
_NUMBERED_HEADING_RE = re.compile(r"^(?P<hashes>#{2,4}) Gap (?P<num>\d+) ", re.M)


def is_resolvable_path(path: str) -> bool:
    p = (path or "").replace("\\", "/")
    return any(marker in p for marker in RESOLVABLE_PATHS)


def _is_purely_additive(ours: str, theirs: str) -> bool:
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
    # independent additions.
    return not (ours_lines & theirs_lines)


def _renumber_collisions(merged: str) -> Tuple[str, List[str]]:
    """Give every duplicated allocation number the next free one.

    Only ever moves a number UP, and only for a duplicate: the first occurrence
    keeps its number because something already references it (a merged PR, a
    cross-link). Renumbering the one that arrived second is the same choice a
    human made all three times this happened.
    """
    notes: List[str] = []
    seen = set()
    used = {int(m.group("num")) for m in _NUMBERED_HEADING_RE.finditer(merged)}
    next_free = (max(used) + 1) if used else 1

    def _sub(m):
        nonlocal next_free
        num = int(m.group("num"))
        if num not in seen:
            seen.add(num)
            return m.group(0)
        replacement = m.group(0).replace(f"Gap {num} ", f"Gap {next_free} ", 1)
        notes.append(f"Gap {num} -> Gap {next_free} (duplicate allocation)")
        next_free += 1
        return replacement

    return _NUMBERED_HEADING_RE.sub(_sub, merged), notes


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

    for h in hunks:
        if not _is_purely_additive(h.group("ours"), h.group("theirs")):
            logger.info("conflict_resolvers: %s has a non-additive hunk — leaving "
                        "it for a human", path)
            return None

    notes = [f"{len(hunks)} additive conflict(s) in {path}: kept both sides"]
    # Order: theirs (already on the base) first, then ours, so the file reads in
    # the order the entries landed.
    merged = _CONFLICT_RE.sub(
        lambda m: m.group("theirs").rstrip("\n") + "\n" + m.group("ours").rstrip("\n") + "\n",
        text,
    )
    if "<<<<<<<" in merged or ">>>>>>>" in merged:
        return None

    merged, renum = _renumber_collisions(merged)
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
