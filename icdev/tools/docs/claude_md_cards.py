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
COMMANDS_MD = BASE_DIR / "docs" / "reference" / "commands.md"

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


def _is_marking(line: str) -> bool:
    """A classification banner, e.g. `# CUI // SP-CTI` -- a marking, not a title."""
    body = line.lstrip("#").strip()
    return "//" in body and len(body) <= 40


def _parse_record_header(path: Path):
    """(ids, title, command) from a record, or None when it has no heading.

    THE FILENAME IS THE CARD ID. That is a fact, not a guess -- every test keys
    on `cards/<stem>.md` -- so the id is never parsed out of the heading and a
    record cannot be unregisterable merely for writing its heading differently.
    Two shapes are in the wild and BOTH must read:

        # <title> (<ids>)     the canonical form, 84 of 86 records
        # <id> -- <title>     what two recent workers wrote

    Only the canonical form carries EXTRA ids (a record for several cards), so
    it is tried first and its id list is preferred; otherwise the stem stands
    alone. A record with no `# ` heading at all is still None -- that one this
    tool genuinely cannot read.
    """
    try:
        head = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None

    heading = None
    for ln in head[:6]:
        if not ln.strip() or _is_marking(ln):
            continue
        if ln.startswith("# "):
            heading = ln
        break
    if heading is None:
        return None

    ids: List[str] = []
    m = ESSAY_HEADER.match(heading)
    if m:
        ids = [t.strip() for t in m.group("ids").split(",") if t.strip()]
        title = m.group("title").strip()
    else:
        title = heading[2:].strip()
        # `# <id> -- <title>`: drop the id, it is the stem and is already known.
        for dash in (EMDASH, "--"):
            prefix = path.stem + " " + dash + " "
            if title.startswith(prefix):
                title = title[len(prefix):].strip()
                break
    if not ids:
        ids = [path.stem]

    cmd, fenced = "", False
    for ln in head:
        if ln.startswith("```"):
            if fenced:
                break
            fenced = True
            continue
        if fenced and ln.strip():
            # Drop a trailing inline comment: the index cites the ENTRY POINT,
            # and the trimmed form is still a substring of the record, which is
            # what test_every_indexed_command_appears_in_its_own_record checks.
            cmd = ln.strip().split("  #", 1)[0].rstrip()
            break
    return ids, title, cmd


def _record_index_line(path: Path) -> Optional[str]:
    """An index line for a record ALREADY on disk, from the shared parser."""
    parsed = _parse_record_header(path)
    if parsed is None:
        return None
    ids, title, cmd = parsed
    rest = title if not cmd else f"{title} {EMDASH} `{cmd}`"
    return f"- `{', '.join(ids)}` {EMDASH} {rest}"


def _index_line(essay: Dict[str, Any]) -> str:
    """One line per card. The command is optional -- some cards are libraries."""
    cmd = next((c.strip() for c in essay["body"]
                if c.strip() and not c.startswith("#")), "")
    rest = f"{essay['title']} {EMDASH} `docs/reference/cards/{essay['slug']}.md`"
    if cmd:
        rest = f"{essay['title']} {EMDASH} `{cmd}` {EMDASH} `docs/reference/cards/{essay['slug']}.md`"
    return f"- `{', '.join(essay['ids'])}` {EMDASH} {rest}"


#: A Cards-table row: ``| `ids` | title | `cmd` | [stem.md](cards/stem.md) |``
COMMANDS_ROW = "| `{ids}` | {title} | {cmd} | [{stem}.md](cards/{stem}.md) |"


def _commands_rows_present() -> Optional[set]:
    """Record stems already linked from commands.md, or None if unreadable.

    The test's predicate is a SUBSTRING -- `cards/<stem>.md` appearing anywhere
    in the file -- so that is what is asked here rather than a second opinion
    about what a table row looks like.
    """
    try:
        body = COMMANDS_MD.read_text(encoding="utf-8")
    except OSError:
        return None
    return {p.stem for p in CARDS_DIR.glob("*.md")
            if f"cards/{p.stem}.md" in body} if CARDS_DIR.exists() else set()


def _commands_row(path: Path) -> Optional[str]:
    """A Cards-table row for a record, from the SAME header the index uses."""
    parsed = _parse_record_header(path)
    if parsed is None:
        return None
    ids, title, cmd = parsed
    return COMMANDS_ROW.format(
        ids=", ".join(ids), title=title,
        cmd=f"`{cmd}`" if cmd else "*(no CLI; see the record)*",
        stem=path.stem)


def _append_commands_rows(stems: List[str]) -> List[str]:
    """Append a row per stem AFTER the table's last row. Returns what landed.

    Anchored on the LAST line that is already a table row, so the row joins the
    table rather than the end of the file -- and a commands.md with no Cards
    table at all is left alone and reported, never guessed at.
    """
    if not stems:
        return []
    try:
        lines = COMMANDS_MD.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    last = None
    for i, ln in enumerate(lines):
        if ln.startswith("| `") and "](cards/" in ln:
            last = i
    if last is None:
        return []
    added, rows = [], []
    for stem in stems:
        row = _commands_row(CARDS_DIR / f"{stem}.md")
        if row is None:
            continue
        rows.append(row)
        added.append(stem)
    if not rows:
        return []
    out = lines[:last + 1] + rows + lines[last + 1:]
    COMMANDS_MD.write_text("\n".join(out).rstrip() + "\n",
                           encoding="utf-8", newline="\n")
    return added


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
        # Records on disk carrying no index line. `--check` must see these or a
        # card that ships its record directly drifts silently until CI says so.
        "unindexed_records": sorted(on_disk - indexed),
        # The SECOND registration. `test_commands_md_lists_every_card`
        # asserts it, and it was the manual half that reddened three
        # consecutive PRs. None -- never [] -- when commands.md is
        # unreadable: absence is not emptiness.
        "unlisted_in_commands": (None if (_c := _commands_rows_present()) is None
                                 else sorted(on_disk - _c)),
        "has_index_heading": idx is not None,
    }


def apply(dry_run: bool = False) -> Dict[str, Any]:
    """Extract, index, then trim. Returns what changed."""
    before = survey()
    if before["state"] != "measured":
        return before
    lines = CLAUDE_MD.read_text(encoding="utf-8").splitlines()
    prelude, essays = parse_essays(lines)
    # "Nothing inline" is NOT "nothing to do": a card that shipped its record
    # directly still needs its index line, and returning here is why the first
    # `--apply` after PR #2277 reported `changed: false` over a record it could
    # see was unindexed.
    if (not essays and not before.get("unindexed_records")
            and not before.get("unlisted_in_commands")):
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
    unindexable: List[str] = []
    if idx:
        have = {slug(m.group("ids").split(",")[0])
                for ln in lines[idx[0]:idx[1]] if (m := INDEX_LINE.match(ln))}
        new_index = [_index_line(e) for e in safe if e["slug"] not in have]
        # A RECORD WRITTEN DIRECTLY IS STILL A CARD. Since xrv-docs-02 a new
        # card ships its record rather than an inline essay, so indexing only
        # what this run EXTRACTED leaves every such card unindexed and reddens
        # test_every_card_record_is_indexed_from_claude_md (PR #2277). Orphans
        # are indexed from their own header; one that cannot be parsed is left
        # alone and named in `unindexable`.
        seen = set(have) | {e["slug"] for e in safe}
        for rec in sorted(CARDS_DIR.glob("*.md")) if CARDS_DIR.exists() else []:
            if rec.stem in seen:
                continue
            line = _record_index_line(rec)
            if line is None:
                unindexable.append(rec.stem)
                continue
            new_index.append(line)
            seen.add(rec.stem)

    lo, hi = _fence_bounds(lines)
    unsafe = {k["slug"] for k in kept_inline}
    body_keep = list(prelude)
    for e in essays:
        if e["slug"] in unsafe:
            body_keep += [f"# {e['title']} ({', '.join(e['ids'])})"] + e["body"]
    out = lines[:lo] + body_keep + lines[hi:]

    listed = _commands_rows_present()
    rows_added = ([] if listed is None
                  else _append_commands_rows(
                      sorted({p.stem for p in CARDS_DIR.glob("*.md")} - listed)
                      if CARDS_DIR.exists() else []))

    if new_index:
        idx2 = _index_bounds(out)
        if idx2:
            out = out[:idx2[1]] + new_index + [""] + out[idx2[1]:]

    CLAUDE_MD.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8", newline="\n")
    after = survey()
    return {"changed": True, "records_written": wrote, "trimmed": len(safe),
            "kept_inline": kept_inline, "index_lines_added": len(new_index),
            "unindexable_records": unindexable,
            "commands_rows_added": rows_added,
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
