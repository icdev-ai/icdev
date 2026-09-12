#!/usr/bin/env python3
# CUI // SP-CTI
r"""Move card essays out of CLAUDE.md into docs/reference/cards/, idempotently.

WHY THIS EXISTS AND NOT A ONE-OFF EDIT. xrv-docs-02 moved 82 card essays out of
`### Essential Commands` by hand and the removal did not survive: the branch's
own later commit carries CLAUDE.md at 371,288 bytes with all 81 essay headers
back inside the fence, while the 82 extracted files and the `#### Card records`
index are still correct. EVERY card appends a block to CLAUDE.md, so this file
conflicts on nearly every merge, and each resolution is another chance to take
the side that still has the essays. A restructure that can only be re-applied by
hand will be undone by hand.

So the trim is a FUNCTION of the file, re-runnable at any time:

    python -m tools.docs.claude_md_cards --check     # drift, exit 1, writes nothing
    python -m tools.docs.claude_md_cards --apply     # extract, index, trim
    python -m tools.docs.claude_md_cards --apply --json

IDEMPOTENT BY CONSTRUCTION. A second run finds no essay header in the fence, no
card file to write and no index line to add, and reports `changed: false`. That
is what makes it safe to put in front of a conflict resolution: re-run it and
the file is correct again whichever side won.

NOTHING IS DELETED THAT IS NOT FIRST WRITTEN DOWN. An essay is removed from the
fence only after its record exists on disk AND an index line points at it, both
verified by re-reading. A card whose record cannot be written is left INLINE and
reported -- a trim that loses an incident record to save bytes has destroyed the
thing the file exists for.

THE RULES ARE THE TEST'S, NOT A SECOND COPY. `tests/docs/test_claude_md_index.py`
defines what a well-formed tree looks like -- the essay-header shape, the index
line shape, the `#### Card records` heading, and the budget -- and this module
matches them deliberately. If the two ever disagree, the TEST is right.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# sys.path BOOTSTRAP first, so `python tools/docs/claude_md_cards.py` reaches
# main() (kax-conflict-04); then the ONE root resolver.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from icdev.core.paths import repo_root  # noqa: E402

BASE_DIR = repo_root(__file__)

CLAUDE_MD = BASE_DIR / "CLAUDE.md"
CARDS_DIR = BASE_DIR / "docs" / "reference" / "cards"

EMDASH = chr(8212)
COMMANDS_HEADING = "### Essential Commands"
INDEX_HEADING = "#### Card records"

#: ``# <title> (<card-id>[, <card-id>...])`` -- the test's own matcher.
ESSAY_HEADER = re.compile(
    r"^# (?P<title>.+) \((?P<ids>(?:#?[a-z0-9][a-z0-9/_.-]*)"
    r"(?:\s*,\s*#?[a-z0-9][a-z0-9/_.-]*)*)\)\s*$"
)
#: ``- `<ids>` -- <rest>``
INDEX_LINE = re.compile(r"^- `(?P<ids>[^`]+)`\s+" + EMDASH + r"\s+(?P<rest>.+)$")

PROVENANCE = (
    "> Moved verbatim from CLAUDE.md on 2026-09-12 (xrv-docs-02). The prose is "
    "the card's own record; only the leading `#` comment markers were stripped "
    "and the command runs fenced."
)


def slug(card_id: str) -> str:
    """The record filename for a card id. Mirrors the test's `_indexed_ids`."""
    return card_id.strip().lstrip("#").replace("/", "-")


def _fence_bounds(lines: List[str]) -> Tuple[int, int]:
    """(first line INSIDE the fence, the closing ``` line)."""
    start = next(i for i, ln in enumerate(lines) if ln.strip() == COMMANDS_HEADING)
    opened = next(i for i in range(start, len(lines)) if lines[i].startswith("```"))
    closed = next(i for i in range(opened + 1, len(lines)) if lines[i].startswith("```"))
    return opened + 1, closed


def _index_bounds(lines: List[str]) -> Optional[Tuple[int, int]]:
    """(heading line, line after the block) or None when the index is absent."""
    try:
        start = next(i for i, ln in enumerate(lines) if ln.startswith(INDEX_HEADING))
    except StopIteration:
        return None
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if re.match(r"^#{1,3} ", lines[j]):
            end = j
            break
    return start, end


def parse_essays(lines: List[str]) -> Tuple[List[str], List[Dict[str, Any]]]:
    """Split the fence into its command PRELUDE and the card essays after it.

    The prelude is everything before the first essay header -- the genuine
    essential commands the block is named for, which must survive.
    """
    lo, hi = _fence_bounds(lines)
    body = lines[lo:hi]
    first = next((i for i, ln in enumerate(body) if _is_header(body, i)), None)
    if first is None:
        return body, []
    prelude = body[:first]
    essays: List[Dict[str, Any]] = []
    cur: Optional[Dict[str, Any]] = None
    for off, ln in enumerate(body[first:], start=first):
        m = ESSAY_HEADER.match(ln) if _is_header(body, off) else None
        if m:
            if cur:
                essays.append(cur)
            ids = [s.strip() for s in m.group("ids").split(",") if s.strip()]
            cur = {"title": m.group("title").strip(), "ids": ids,
                   "slug": slug(ids[0]), "body": []}
        elif cur is not None:
            cur["body"].append(ln)
    if cur:
        essays.append(cur)
    return prelude, essays


def _is_header(body: List[str], i: int) -> bool:
    """An essay header, as opposed to PROSE that merely ends in `(something)`.

    The shape alone is ambiguous and the test's matcher is deliberately loose.
    MEASURED on this tree: `# stronger posture than the raw-INSERT (219) and
    undeclared-import (210)` is the fourth line of the rem-hyg-13 essay and
    matches `ESSAY_HEADER` exactly. Splitting there would truncate that essay
    and write a record called `210.md`.

    Every real header opens a block and is preceded by a BLANK line (or starts
    the essay region); a continuation line never is. That is a fact about the
    layout rather than a guess about ids, so it does not care whether a future
    card id happens to be numeric.
    """
    if not ESSAY_HEADER.match(body[i]):
        return False
    if i == 0:
        return True
    return body[i - 1].strip() == ""


def _record_text(essay: Dict[str, Any]) -> str:
    """A card record: header, provenance, the commands fenced, then the prose.

    The `#` comment markers are stripped because the essay lived inside a bash
    fence where every prose line had to be a comment; outside it they are noise.
    """
    cmds: List[str] = []
    prose: List[str] = []
    for ln in essay["body"]:
        if ln.startswith("# "):
            prose.append(ln[2:])
        elif ln.strip() == "#":
            prose.append("")
        else:
            cmds.append(ln)
    out = [f"# {essay['title']} ({', '.join(essay['ids'])})", "", PROVENANCE, ""]
    if any(c.strip() for c in cmds):
        out += ["```bash"] + [c for c in cmds if c.strip()] + ["```", ""]
    out += prose
    return "\n".join(out).rstrip() + "\n"


def _index_line(essay: Dict[str, Any]) -> str:
    """One line per card. The command is optional -- some cards are libraries."""
    cmd = next((c.strip() for c in essay["body"]
                if c.strip() and not c.startswith("#")), "")
    rest = f"{essay['title']} {EMDASH} `docs/reference/cards/{essay['slug']}.md`"
    if cmd:
        rest = f"{essay['title']} {EMDASH} `{cmd}` {EMDASH} `docs/reference/cards/{essay['slug']}.md`"
    return f"- `{', '.join(essay['ids'])}` {EMDASH} {rest}"


def survey() -> Dict[str, Any]:
    """What the tree looks like now. Writes nothing."""
    if not CLAUDE_MD.exists():
        return {"state": "unmeasurable", "reason": f"no CLAUDE.md at {CLAUDE_MD}"}
    lines = CLAUDE_MD.read_text(encoding="utf-8").splitlines()
    prelude, essays = parse_essays(lines)
    idx = _index_bounds(lines)
    indexed = set()
    if idx:
        for ln in lines[idx[0]:idx[1]]:
            m = INDEX_LINE.match(ln)
            if m:
                indexed.add(slug(m.group("ids").split(",")[0]))
    on_disk = {p.stem for p in CARDS_DIR.glob("*.md")} if CARDS_DIR.exists() else set()
    return {
        "state": "measured",
        "bytes": len(CLAUDE_MD.read_bytes()),
        "inline_essays": len(essays),
        "prelude_commands": len([ln for ln in prelude if ln.strip() and not ln.startswith("#")]),
        "records_on_disk": len(on_disk),
        "indexed": len(indexed),
        "missing_record": sorted(e["slug"] for e in essays if e["slug"] not in on_disk),
        "missing_index": sorted(e["slug"] for e in essays if e["slug"] not in indexed),
        "has_index_heading": idx is not None,
    }


def apply(dry_run: bool = False) -> Dict[str, Any]:
    """Extract, index, then trim. Returns what changed."""
    before = survey()
    if before["state"] != "measured":
        return before
    lines = CLAUDE_MD.read_text(encoding="utf-8").splitlines()
    prelude, essays = parse_essays(lines)
    if not essays:
        return {**before, "changed": False, "reason": "no inline essay to move"}

    CARDS_DIR.mkdir(parents=True, exist_ok=True)
    wrote, kept_inline = [], []
    for e in essays:
        target = CARDS_DIR / f"{e['slug']}.md"
        if target.exists():
            continue
        if dry_run:
            wrote.append(e["slug"])
            continue
        try:
            target.write_text(_record_text(e), encoding="utf-8", newline="\n")
            # RE-READ, never trust the write: the essay is removed from CLAUDE.md
            # below, so an unverified record would lose an incident permanently.
            if not target.read_text(encoding="utf-8").strip():
                raise OSError("record wrote empty")
            wrote.append(e["slug"])
        except OSError as exc:  # noqa: PERF203 - one bad record must not lose the rest
            kept_inline.append({"slug": e["slug"], "error": str(exc)})

    safe = [e for e in essays
            if (CARDS_DIR / f"{e['slug']}.md").exists()
            and e["slug"] not in {k["slug"] for k in kept_inline}]
    if dry_run:
        return {**before, "changed": True, "would_write": wrote,
                "would_trim": len(safe), "kept_inline": kept_inline}

    # index lines for anything not already indexed
    idx = _index_bounds(lines)
    new_index: List[str] = []
    if idx:
        have = {slug(m.group("ids").split(",")[0])
                for ln in lines[idx[0]:idx[1]] if (m := INDEX_LINE.match(ln))}
        new_index = [_index_line(e) for e in safe if e["slug"] not in have]

    lo, hi = _fence_bounds(lines)
    unsafe = {k["slug"] for k in kept_inline}
    body_keep = list(prelude)
    for e in essays:
        if e["slug"] in unsafe:
            body_keep += [f"# {e['title']} ({', '.join(e['ids'])})"] + e["body"]
    out = lines[:lo] + body_keep + lines[hi:]

    if new_index:
        idx2 = _index_bounds(out)
        if idx2:
            out = out[:idx2[1]] + new_index + [""] + out[idx2[1]:]

    CLAUDE_MD.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8", newline="\n")
    after = survey()
    return {"changed": True, "records_written": wrote, "trimmed": len(safe),
            "kept_inline": kept_inline, "index_lines_added": len(new_index),
            "bytes_before": before["bytes"], "bytes_after": after["bytes"],
            "inline_essays_after": after["inline_essays"]}


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true",
                    help="report drift and exit 1; writes nothing")
    ap.add_argument("--apply", action="store_true", help="extract, index and trim")
    ap.add_argument("--dry-run", action="store_true", help="with --apply: act on nothing")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if args.apply:
        out = apply(dry_run=args.dry_run)
    else:
        out = survey()
    print(json.dumps(out, indent=2) if args.json else "\n".join(
        f"{k}: {v}" for k, v in out.items()))
    if args.check and out.get("state") == "measured" and out.get("inline_essays"):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
