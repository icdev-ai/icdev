#!/usr/bin/env python3
# CUI // SP-CTI
"""Union-resolve a REAL rebase conflict on a DECLARED append-shaped file (mfx-sib-03).

`tools/kanban/conflict_resolvers.py` resolves the doc-only conflicts it can prove
additive and refuses everything else, so `pr_watcher` escalates every REAL
conflict on the files a card series appends to in lockstep -- a canvas
blueprint gaining one route block per card, the `request.path in [...]` list in
base.html gaining one token per card, a coverage table gaining one row per card,
a Playwright spec gaining one test per card. Between 2026-09-03 and 09-04 an
operator resolved exactly that shape TEN times by hand, and every resolution was
the same rule: the union. Twice a WRONG rule shipped a broken file -- a
quoted-list rule applied to a TypeScript object literal, and "keep both blocks"
applied where the hunk boundary had cut a test mid-body -- and only `ruff` or a
later Playwright collect caught it.

THREE DECISIONS, and each one is the whole design:

  * **Rules are chosen BY FILE, never by content.** `union_resolver.files` in
    args/pr_watcher_config.yaml maps a path pattern to an ORDERED rule list. A
    file no entry matches REFUSES -- the resolver never guesses what a hunk
    "looks like", because "looks like a quoted list" is exactly how the object
    literal got broken. An empty side always resolves to the other, on any
    declared file, before any rule is consulted.

  * **The merge is a WHOLE-FILE three-way over the index stages**, not a parse
    of conflict markers. `:1` is the base, `:2` the side already on the branch
    being rebased onto (main), `:3` the commit being replayed (the card).
    Overlapping edits form a CLUSTER; a cluster that only one side changed is
    taken from that side; one both sides changed identically is taken once;
    anything else goes to the declared rules. Because each side's text comes
    from that side's actual file, "the hunk boundary cut a test mid-body" cannot
    happen here -- a side's segment is a contiguous run of a real file.

  * **VERIFY before anything leaves the scratch worktree, and any failure
    refuses.** Python: `ast.parse` then `ruff`. TypeScript: the typescript
    compiler's syntactic diagnostics when a `typescript` module is resolvable,
    else a string/comment-aware delimiter balance (the truncation class), and
    the verdict RECORDS which one ran. Jinja/HTML: a Jinja parse. YAML / JSON:
    a load. Every file: no conflict marker, then `git diff --cached --check`.
    Declared pytest targets run ONCE on the completed rebase before the push.
    A verifier that cannot run (no ruff, no node) is `unmeasured` and refuses:
    an unverified resolution is not a resolution.

RULES (declare them per path; `other_side_when_empty` is universal):
  keep_both_blocks   both sides INSERTED at the same point: main's block, then
                     the card's. Route blocks in a blueprint, tests in a spec.
  table_rows         keep_both_blocks restricted to `| ... |` rows, duplicates
                     dropped. Markdown coverage tables.
  quoted_list_line   ONE line on all three sides, a run of quoted tokens with
                     identical prefix / suffix / separator: main's tokens plus
                     the card's NEW tokens, each placed after its predecessor.
                     The `request.path in [...]` list, the `Pages:` line.
  adjacent_edits     two edits of DIFFERENT base lines that merely abut are
                     merged line-by-line instead of being one conflict. A nav
                     table where main migrated row A and the card migrated the
                     row beneath it. An insertion at the seam stays a conflict.

The rung runs INSIDE `rebase_recovery.rebase_and_push`, after the doc resolver
declines and before the abort, so it sits under the same per-base-era rebase
budget `pr_watcher._maybe_rebase` already enforces; the watcher writes
`pr_watcher.union_resolved` / `pr_watcher.union_refused` audit rows naming the
rules used. Mirror paths (`icdev/tools/...`, `icdev/data/args/...`) are
matched by their canonical name, so one declaration covers both copies.

CLI (a human mid-rebase, or a dry run over a conflicted worktree):
    python -m tools.kanban.union_resolver --worktree <path> --dry-run --json
    python -m tools.kanban.union_resolver --worktree <path> --mode merge
    python -m tools.kanban.union_resolver --list-rules
"""
from __future__ import annotations

import argparse
import ast
import difflib
import glob
import json
import pathlib
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

# sys.path bootstrap: run by path, sys.path[0] is this file's own directory.
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.logging.icdev_logger import get_logger  # noqa: E402

logger = get_logger(__name__)

RULE_OTHER_SIDE_WHEN_EMPTY = "other_side_when_empty"
RULE_KEEP_BOTH_BLOCKS = "keep_both_blocks"
RULE_TABLE_ROWS = "table_rows"
RULE_QUOTED_LIST_LINE = "quoted_list_line"
RULE_ADJACENT_EDITS = "adjacent_edits"

#: The rules a config entry may name. `other_side_when_empty` is not here on
#: purpose: it applies to every declared file and cannot be switched off.
DECLARABLE_RULES = frozenset({
    RULE_KEEP_BOTH_BLOCKS, RULE_TABLE_ROWS, RULE_QUOTED_LIST_LINE, RULE_ADJACENT_EDITS,
})

RULE_DESCRIPTIONS = {
    RULE_OTHER_SIDE_WHEN_EMPTY: "universal: a side with nothing in the hunk yields to the other",
    RULE_KEEP_BOTH_BLOCKS: "both inserted at one point: main's block then the card's",
    RULE_TABLE_ROWS: "keep_both_blocks restricted to | rows, duplicates dropped",
    RULE_QUOTED_LIST_LINE: "one quoted-token line: main's tokens plus the card's new ones",
    RULE_ADJACENT_EDITS: "edits of different base lines that merely abut merge line-by-line",
}

#: Mirror prefixes fold onto the canonical path so ONE declaration covers the
#: packaged copy. The conflict on `icdev/tools/x/blueprint.py` is the same
#: conflict as on `tools/x/blueprint.py`, byte for byte.
_MIRROR_PREFIXES = (
    ("icdev/tools/", "tools/"),
    ("icdev/data/args/", "args/"),
    ("icdev/data/docs/", "docs/"),
    ("icdev/docs/", "docs/"),
    ("icdev/data/context/", "context/"),
)

DEFAULT_GIT_TIMEOUT = 60
DEFAULT_VERIFY_TIMEOUT = 180
DEFAULT_PYTEST_TIMEOUT = 900

_MARKER_RE = re.compile(r"^(?:<{7}|={7}|>{7}|\|{7})(?: |$)", re.M)


class UnionRefused(Exception):
    """The resolver could not PROVE a resolution. The caller aborts."""


@dataclass
class UnionOutcome:
    outcome: str                       # resolved | refused | not_applicable
    files: List[str] = field(default_factory=list)
    rules_used: List[str] = field(default_factory=list)   # "<file>:<rule>@<line>"
    verifiers: List[str] = field(default_factory=list)    # "<file>:<verifier>"
    tests: List[str] = field(default_factory=list)        # declared pytest targets
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ── configuration ────────────────────────────────────────────────────────────
def config_path() -> pathlib.Path:
    """`args/pr_watcher_config.yaml` in the checkout this module belongs to."""
    try:
        from icdev.core.paths import repo_root
        root = pathlib.Path(repo_root(__file__))
    except Exception:  # noqa: BLE001 -- an installed kernel without the core resolver
        root = _REPO_ROOT
    return root / "args" / "pr_watcher_config.yaml"


def load_declared_rules(path: Optional[pathlib.Path] = None) -> Dict[str, Any]:
    """The `union_resolver` block. Empty when the file or the block is absent."""
    p = path or config_path()
    try:
        import yaml
        with open(p, "r", encoding="utf-8") as fh:
            cfg = yaml.safe_load(fh) or {}
    except Exception as exc:  # noqa: BLE001
        logger.debug("union_resolver: cannot read %s: %s", p, exc)
        return {}
    block = cfg.get("union_resolver") or {}
    return block if isinstance(block, dict) else {}


def canonical_path(path: str) -> str:
    p = (path or "").replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    for mirror, canon in _MIRROR_PREFIXES:
        if p.startswith(mirror):
            return canon + p[len(mirror):]
    return p


def _glob_to_regex(pattern: str) -> "re.Pattern[str]":
    """`*` within one path segment, `**` across segments. fnmatch lets `*`
    cross `/`, which would make `tools/*/blueprint.py` claim every blueprint at
    any depth -- a declaration must mean what it says."""
    out, i = "", 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out += "(?:.*/)?"
            i += 3
        elif pattern.startswith("**", i):
            out += ".*"
            i += 2
        elif pattern[i] == "*":
            out += "[^/]*"
            i += 1
        elif pattern[i] == "?":
            out += "[^/]"
            i += 1
        else:
            out += re.escape(pattern[i])
            i += 1
    return re.compile("^" + out + "$")


def match_declaration(path: str, declarations: Sequence[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The FIRST declaration whose pattern matches the canonical path, or None."""
    canon = canonical_path(path)
    for decl in declarations or ():
        if not isinstance(decl, dict):
            continue
        pat = decl.get("path")
        if not pat:
            continue
        if _glob_to_regex(canonical_path(str(pat))).match(canon):
            return decl
    return None


def _declared_rules(decl: Dict[str, Any], rel: str) -> List[str]:
    rules = decl.get("rules") or []
    if isinstance(rules, str):
        rules = [rules]
    unknown = [r for r in rules if r not in DECLARABLE_RULES]
    if unknown:
        raise UnionRefused(f"{rel}: unknown rule(s) declared: {unknown}")
    if not rules:
        raise UnionRefused(f"{rel}: declaration names no rules")
    return list(rules)


# ── the three-way engine ─────────────────────────────────────────────────────
_Op = Tuple[str, int, int, int, int]


@dataclass(frozen=True)
class _Change:
    side: str      # "main" | "card"
    op: _Op

    @property
    def i1(self) -> int:
        return self.op[1]

    @property
    def i2(self) -> int:
        return self.op[2]

    @property
    def is_insert(self) -> bool:
        return self.op[1] == self.op[2]


def _opcodes(base: Sequence[str], side: Sequence[str]) -> List[_Op]:
    return difflib.SequenceMatcher(None, base, side, autojunk=False).get_opcodes()


def _touches_at_seam(cluster: List[_Change], hi: int, nxt: _Change, adjacent_ok: bool) -> bool:
    """Does a change starting exactly where the cluster ends join it?

    Two non-empty edits of different base lines that merely abut are the
    `adjacent_edits` case and stay apart when that rule is declared. An
    INSERTION at the seam is ambiguous -- nothing says whether it goes before
    or after the other side's edit -- so it always joins. A diff's own opcodes
    are separated by equal runs, so same-side changes never abut each other.
    """
    if not adjacent_ok:
        return True
    if nxt.is_insert:
        return True
    ending_here = [c for c in cluster if c.i2 == hi and c.side != nxt.side]
    return any(c.is_insert for c in ending_here)


def _clusters(changes: List[_Change], adjacent_ok: bool) -> List[List[_Change]]:
    ordered = sorted(changes, key=lambda c: (c.i1, c.i2, c.side))
    out: List[List[_Change]] = []
    for c in ordered:
        if out:
            cur = out[-1]
            hi = max(m.i2 for m in cur)
            if c.i1 < hi or (c.i1 == hi and _touches_at_seam(cur, hi, c, adjacent_ok)):
                cur.append(c)
                continue
        out.append([c])
    return out


def _map_index(ops: List[_Op], b: int, members: set, at_end: bool, side_len: int) -> int:
    """The side index that corresponds to base index `b`.

    A member insertion at `b` is INCLUDED (its lines belong to this cluster); a
    non-member one is excluded. A non-equal op that starts exactly at a cluster
    END belongs to the next cluster and is excluded too.
    """
    for op in ops:
        tag, i1, i2, j1, j2 = op
        if i1 == i2 == b:
            member = op in members
            if at_end:
                return j2 if member else j1
            return j1 if member else j2
        if i1 <= b < i2:
            if tag == "equal":
                return j1 + (b - i1)
            if at_end:
                return j1 if i1 == b else j2
            return j1
    return side_len


def _rule_keep_both_blocks(base_seg, main_seg, card_seg):
    if base_seg:
        return None          # both REWROTE existing lines -- not two appends
    return list(main_seg) + list(card_seg)


_TABLE_ROW_RE = re.compile(r"^\s*\|.*\|\s*$")


def _rule_table_rows(base_seg, main_seg, card_seg):
    if base_seg:
        return None
    content = [ln for ln in list(main_seg) + list(card_seg) if ln.strip()]
    if not content or not all(_TABLE_ROW_RE.match(ln) for ln in content):
        return None
    seen, out = set(), []
    for ln in list(main_seg) + list(card_seg):
        key = ln.strip()
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        out.append(ln)
    return out


_QUOTE_KINDS = ("'", '"', "`")


def _tokenise(line: str, q: str) -> List[str]:
    """The line as alternating text chunks and `q`-quoted tokens, joinable back."""
    parts = re.split("(" + re.escape(q) + "[^" + re.escape(q) + "]*" + re.escape(q) + ")", line)
    return [p for p in parts if p != ""]


def _is_token(item: str, q: str) -> bool:
    return len(item) >= 2 and item[0] == q and item[-1] == q


def _union_quoted(base_line: str, main_line: str, card_line: str, q: str) -> Optional[str]:
    """The union of one quoted-token list under quote kind `q`, or None.

    The line is tokenised and the SAME three-way engine runs over the token
    sequence: each side may only INSERT (a token and its separator) -- a side
    that rewrote or removed an item is not editing a list, which is exactly
    what the outer `class="..."` attribute of base.html looks like under the
    double-quote kind, or what a nav row looks like under any kind. Extra
    quoted tokens elsewhere on the line (`request.path.startswith('/security')`
    after the list) are ordinary unchanged items and cost nothing.
    """
    items = [_tokenise(ln, q) for ln in (base_line, main_line, card_line)]
    if not any(_is_token(it, q) for it in items[0]):
        return None
    base_items, main_items, card_items = items
    inserted = False
    for side in (main_items, card_items):
        for tag, _i1, _i2, _j1, _j2 in _opcodes(base_items, side):
            if tag == "equal":
                continue
            if tag != "insert":
                return None
            inserted = True
    if not inserted:
        return None
    try:
        merged, _notes = merge_three_way(base_items, main_items, card_items, [RULE_KEEP_BOTH_BLOCKS])
    except UnionRefused:
        return None
    line = "".join(merged)
    kept = {it for it in merged if _is_token(it, q)}
    wanted = {it for it in main_items + card_items if _is_token(it, q)}
    if not wanted <= kept:
        return None
    return line


def _rule_quoted_list_line(base_seg, main_seg, card_seg):
    """ONE line on all three sides. Every quote kind is tried; the union must
    be the SAME under every kind that parses, or the line is ambiguous."""
    if not (len(base_seg) == len(main_seg) == len(card_seg) == 1):
        return None
    candidates = {
        _union_quoted(base_seg[0], main_seg[0], card_seg[0], q) for q in _QUOTE_KINDS
    } - {None}
    if len(candidates) != 1:
        return None
    return [candidates.pop()]


_CLUSTER_RULES: Dict[str, Callable] = {
    RULE_KEEP_BOTH_BLOCKS: _rule_keep_both_blocks,
    RULE_TABLE_ROWS: _rule_table_rows,
    RULE_QUOTED_LIST_LINE: _rule_quoted_list_line,
}


def _resolve_cluster(base_seg, main_seg, card_seg, rules: Sequence[str], lo: int):
    if not main_seg and card_seg:
        return list(card_seg), RULE_OTHER_SIDE_WHEN_EMPTY
    if not card_seg and main_seg:
        return list(main_seg), RULE_OTHER_SIDE_WHEN_EMPTY
    tried = []
    for rule in rules:
        fn = _CLUSTER_RULES.get(rule)
        if fn is None:
            continue
        tried.append(rule)
        got = fn(base_seg, main_seg, card_seg)
        if got is not None:
            return got, rule
    raise UnionRefused(
        f"no declared rule resolves the hunk at line {lo + 1} "
        f"(base {len(base_seg)} line(s), main {len(main_seg)}, card {len(card_seg)}; "
        f"tried {tried or 'nothing'})"
    )


def merge_three_way(base: Sequence[str], main: Sequence[str], card: Sequence[str],
                    rules: Sequence[str]) -> Tuple[List[str], List[str]]:
    """Merge three line lists. Returns (merged_lines, notes); raises UnionRefused.

    `notes` names every rule that decided a cluster, as `<rule>@<base line>`,
    so the audit row can say what was done rather than that something was.
    """
    ops_m = _opcodes(base, main)
    ops_c = _opcodes(base, card)
    changes = [_Change("main", op) for op in ops_m if op[0] != "equal"]
    changes += [_Change("card", op) for op in ops_c if op[0] != "equal"]
    adjacent_ok = RULE_ADJACENT_EDITS in rules

    out: List[str] = []
    notes: List[str] = []
    cursor = 0
    prev: Optional[Tuple[int, str]] = None      # (hi, side) of the previous cluster
    for cluster in _clusters(changes, adjacent_ok):
        lo = min(c.i1 for c in cluster)
        hi = max(c.i2 for c in cluster)
        mem_m = {c.op for c in cluster if c.side == "main"}
        mem_c = {c.op for c in cluster if c.side == "card"}
        m1 = _map_index(ops_m, lo, mem_m, False, len(main))
        m2 = _map_index(ops_m, hi, mem_m, True, len(main))
        c1 = _map_index(ops_c, lo, mem_c, False, len(card))
        c2 = _map_index(ops_c, hi, mem_c, True, len(card))
        base_seg = list(base[lo:hi])
        main_seg = list(main[m1:m2])
        card_seg = list(card[c1:c2])

        out.extend(base[cursor:lo])
        cursor = hi
        sides = {c.side for c in cluster}
        if prev is not None and prev[0] == lo and len(sides) == 1 and prev[1] != next(iter(sides)):
            notes.append(f"{RULE_ADJACENT_EDITS}@{lo + 1}")
        prev = (hi, next(iter(sides)) if len(sides) == 1 else "both")

        if main_seg == base_seg:
            out.extend(card_seg)
        elif card_seg == base_seg:
            out.extend(main_seg)
        elif main_seg == card_seg:
            out.extend(main_seg)
        else:
            resolved, rule = _resolve_cluster(base_seg, main_seg, card_seg, rules, lo)
            out.extend(resolved)
            notes.append(f"{rule}@{lo + 1}")
    out.extend(base[cursor:])
    return out, notes


# ── git plumbing ─────────────────────────────────────────────────────────────
def _run(cmd: List[str], *, cwd: str, runner: Optional[Callable], timeout: int,
         binary: bool = False):
    kwargs: Dict[str, Any] = {"capture_output": True, "cwd": cwd, "timeout": timeout}
    if not binary:
        kwargs.update(text=True, encoding="utf-8", errors="replace")
    return (runner or subprocess.run)(cmd, **kwargs)


def _unmerged_files(cwd: str, runner) -> List[str]:
    proc = _run(["git", "diff", "--name-only", "--diff-filter=U"], cwd=cwd, runner=runner,
                timeout=DEFAULT_GIT_TIMEOUT)
    if getattr(proc, "returncode", 1) != 0:
        return []
    return [ln.strip() for ln in (getattr(proc, "stdout", "") or "").splitlines() if ln.strip()]


def _stage_bytes(cwd: str, rel: str, stage: int, runner) -> Optional[bytes]:
    proc = _run(["git", "show", f":{stage}:{rel}"], cwd=cwd, runner=runner,
                timeout=DEFAULT_GIT_TIMEOUT, binary=True)
    if getattr(proc, "returncode", 1) != 0:
        return None
    out = getattr(proc, "stdout", b"") or b""
    return out.encode("utf-8") if isinstance(out, str) else out


def _read_stages(cwd: str, rel: str, runner, mode: str) -> Tuple[List[str], List[str], List[str]]:
    """(base, main, card) as line lists with their endings preserved.

    In a REBASE stage 2 is HEAD -- the branch being rebased onto -- and stage 3
    the commit being replayed. In a `git merge main` from the card's branch it
    is the other way round, so `mode` says which.
    """
    raw = {n: _stage_bytes(cwd, rel, n, runner) for n in (1, 2, 3)}
    if raw[1] is None:
        raise UnionRefused(f"{rel}: no base stage (add/add or delete conflict) -- nothing to union against")
    if raw[2] is None or raw[3] is None:
        raise UnionRefused(f"{rel}: a side is missing from the index -- one side deleted the file")
    try:
        texts = {n: raw[n].decode("utf-8") for n in (1, 2, 3)}
    except UnicodeDecodeError as exc:
        raise UnionRefused(f"{rel}: not UTF-8 text ({exc})") from exc
    base = texts[1].splitlines(keepends=True)
    if mode == "merge":
        main, card = texts[3].splitlines(keepends=True), texts[2].splitlines(keepends=True)
    else:
        main, card = texts[2].splitlines(keepends=True), texts[3].splitlines(keepends=True)
    return base, main, card


# ── verification ─────────────────────────────────────────────────────────────
def _verify_markers(rel: str, text: str) -> None:
    if _MARKER_RE.search(text):
        raise UnionRefused(f"{rel}: conflict marker left in the resolved text")


def _verify_python(rel: str, text: str, cwd: str, verify_runner) -> List[str]:
    try:
        ast.parse(text, filename=rel)
    except SyntaxError as exc:
        raise UnionRefused(f"{rel}: does not parse after resolution -- {exc}") from exc
    proc = _run([sys.executable, "-m", "ruff", "check", "--no-cache", rel],
                cwd=cwd, runner=verify_runner, timeout=DEFAULT_VERIFY_TIMEOUT)
    rc = getattr(proc, "returncode", 2)
    err = (getattr(proc, "stderr", "") or "")
    if rc == 0:
        return ["py_ast", "ruff"]
    if "No module named" in err or rc >= 2:
        raise UnionRefused(f"{rel}: ruff could not run (unmeasured, rc={rc}): {err[:200]}")
    out = (getattr(proc, "stdout", "") or "") + err
    raise UnionRefused(f"{rel}: ruff refused the resolution: {out[-600:]}")


def _find_typescript(cwd: str) -> Optional[pathlib.Path]:
    candidates = [
        pathlib.Path(cwd) / "node_modules" / "typescript",
        pathlib.Path(cwd) / "frontend" / "node_modules" / "typescript",
    ]
    for c in candidates:
        if (c / "lib" / "typescript.js").exists():
            return c
    npm = shutil.which("npm")
    if npm:
        try:
            proc = subprocess.run([npm, "root", "-g"], capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", timeout=30)
            root = (proc.stdout or "").strip()
            if root and (pathlib.Path(root) / "typescript" / "lib" / "typescript.js").exists():
                return pathlib.Path(root) / "typescript"
        except Exception:  # noqa: BLE001
            pass
    return None


_TS_CHECK_SCRIPT = (
    "const ts=require(process.argv[1]);const fs=require('fs');"
    "const src=fs.readFileSync(process.argv[2],'utf8');"
    "const r=ts.transpileModule(src,{reportDiagnostics:true,"
    "compilerOptions:{target:ts.ScriptTarget.ES2020,module:ts.ModuleKind.ESNext}});"
    "const d=r.diagnostics||[];"
    "if(d.length){for(const x of d){console.error(ts.flattenDiagnosticMessageText(x.messageText,'\\n'))}"
    "process.exit(1)}"
)


def delimiter_balance(text: str) -> Optional[str]:
    """Unbalanced bracket in TS/JS source, skipping strings, templates and comments.

    Returns a description of the first fault, or None when balanced. The weaker
    verifier: it cannot see a type error, but it does see the shape that
    actually shipped -- a test whose closing `});` was cut off.
    """
    pairs = {")": "(", "]": "[", "}": "{"}
    stack: List[Tuple[str, int]] = []
    i, n, line = 0, len(text), 1
    while i < n:
        ch = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        if ch == "\n":
            line += 1
            i += 1
            continue
        if ch == "/" and nxt == "/":
            j = text.find("\n", i)
            i = n if j < 0 else j
            continue
        if ch == "/" and nxt == "*":
            j = text.find("*/", i + 2)
            if j < 0:
                return f"unterminated block comment opened on line {line}"
            line += text.count("\n", i, j)
            i = j + 2
            continue
        if ch in ("'", '"', "`"):
            q, j = ch, i + 1
            while j < n:
                if text[j] == "\\":
                    j += 2
                    continue
                if text[j] == q:
                    break
                if text[j] == "\n":
                    if q != "`":
                        return f"unterminated string on line {line}"
                    line += 1
                j += 1
            if j >= n:
                return f"unterminated string opened on line {line}"
            i = j + 1
            continue
        if ch in "([{":
            stack.append((ch, line))
        elif ch in ")]}":
            if not stack or stack[-1][0] != pairs[ch]:
                return f"unexpected '{ch}' on line {line}"
            stack.pop()
        i += 1
    if stack:
        ch, at = stack[-1]
        return f"'{ch}' opened on line {at} is never closed"
    return None


def _verify_typescript(rel: str, text: str, cwd: str, verify_runner) -> List[str]:
    ts_lib = _find_typescript(cwd)
    node = shutil.which("node")
    if ts_lib is not None and node:
        proc = _run([node, "-e", _TS_CHECK_SCRIPT, str(ts_lib), rel],
                    cwd=cwd, runner=verify_runner, timeout=DEFAULT_VERIFY_TIMEOUT)
        if getattr(proc, "returncode", 1) != 0:
            raise UnionRefused(f"{rel}: typescript syntax diagnostics: "
                               f"{(getattr(proc, 'stderr', '') or '')[-600:]}")
        return ["ts_syntax"]
    fault = delimiter_balance(text)
    if fault:
        raise UnionRefused(f"{rel}: {fault}")
    return ["ts_delimiter_balance"]


def _verify_jinja(rel: str, text: str) -> List[str]:
    try:
        import jinja2
    except ImportError as exc:  # pragma: no cover - jinja2 is a hard dependency here
        raise UnionRefused(f"{rel}: jinja2 unavailable (unmeasured)") from exc
    try:
        jinja2.Environment().parse(text, name=rel)
    except jinja2.TemplateSyntaxError as exc:
        raise UnionRefused(f"{rel}: Jinja does not parse after resolution -- line {exc.lineno}: {exc.message}") from exc
    return ["jinja_parse"]


def _verify_yaml(rel: str, text: str) -> List[str]:
    import yaml
    try:
        yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise UnionRefused(f"{rel}: YAML does not load after resolution -- {exc}") from exc
    return ["yaml_load"]


def _verify_json(rel: str, text: str) -> List[str]:
    try:
        json.loads(text)
    except ValueError as exc:
        raise UnionRefused(f"{rel}: JSON does not load after resolution -- {exc}") from exc
    return ["json_load"]


def verify_file(rel: str, text: str, cwd: str, verify_runner=None) -> List[str]:
    """Run every verifier this file's extension earns. Raises UnionRefused."""
    _verify_markers(rel, text)
    ext = pathlib.PurePosixPath(rel.replace("\\", "/")).suffix.lower()
    if ext == ".py":
        return ["markers"] + _verify_python(rel, text, cwd, verify_runner)
    if ext in (".ts", ".tsx", ".js", ".mjs"):
        return ["markers"] + _verify_typescript(rel, text, cwd, verify_runner)
    if ext in (".html", ".jinja", ".j2"):
        return ["markers"] + _verify_jinja(rel, text)
    if ext in (".yaml", ".yml"):
        return ["markers"] + _verify_yaml(rel, text)
    if ext == ".json":
        return ["markers"] + _verify_json(rel, text)
    return ["markers"]


def _diff_check(cwd: str, files: List[str], runner) -> None:
    proc = _run(["git", "diff", "--cached", "--check", "--", *files], cwd=cwd, runner=runner,
                timeout=DEFAULT_GIT_TIMEOUT)
    if getattr(proc, "returncode", 1) != 0:
        out = (getattr(proc, "stdout", "") or "") + (getattr(proc, "stderr", "") or "")
        raise UnionRefused(f"git diff --check refused the resolution: {out[-600:]}")


def _expand_tests(decl: Dict[str, Any], rel: str, cwd: str) -> List[str]:
    """Declared pytest targets for this file, expanded against the scratch tree.

    `{parent}` is the conflicted file's parent directory name and `{stem}` its
    stem, so one declaration for `tools/*/blueprint.py` can point at that
    canvas's page tests. A target matching nothing is dropped, not invented.
    """
    p = pathlib.PurePosixPath(canonical_path(rel))
    fmt = {"parent": p.parent.name, "stem": p.stem}
    out: List[str] = []
    for pat in decl.get("tests") or []:
        try:
            spec = str(pat).format(**fmt)
        except (KeyError, IndexError):
            spec = str(pat)
        for hit in sorted(glob.glob(str(pathlib.Path(cwd) / spec))):
            relhit = str(pathlib.Path(hit).relative_to(cwd)).replace("\\", "/")
            if relhit not in out:
                out.append(relhit)
    return out


def run_declared_tests(cwd: str, targets: Sequence[str], verify_runner=None,
                       timeout: int = DEFAULT_PYTEST_TIMEOUT) -> Tuple[bool, str]:
    """Run the declared pytest targets ONCE, on the completed tree. (ok, detail)."""
    targets = [t for t in targets if t]
    if not targets:
        return True, "no declared tests"
    cmd = [sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", *targets]
    try:
        proc = _run(cmd, cwd=cwd, runner=verify_runner, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, f"pytest timed out after {timeout}s on {targets}"
    rc = getattr(proc, "returncode", 1)
    if rc == 0:
        return True, f"pytest passed: {targets}"
    if rc == 5:
        return True, f"pytest collected nothing in {targets}"
    tail = "\n".join(((getattr(proc, "stdout", "") or "") + (getattr(proc, "stderr", "") or "")).splitlines()[-30:])
    return False, f"pytest rc={rc} on {targets}: {tail}"


# ── the rung ─────────────────────────────────────────────────────────────────
def resolve_index_conflicts(cwd: str, rules_cfg: Optional[Dict[str, Any]] = None, *,
                            runner: Optional[Callable] = None,
                            verify_runner: Optional[Callable] = None,
                            mode: str = "rebase", dry_run: bool = False) -> UnionOutcome:
    """Resolve every unmerged file in `cwd` by its declared rules, then verify.

    ALL-OR-NOTHING: every unmerged file must be declared and must resolve
    before anything is written, and every verifier must pass before the
    outcome reads `resolved`. On a verification failure the written files are
    put back to their conflicted state (`git checkout --merge`) so a human sees
    the conflict, not a half-resolution.
    """
    cfg = rules_cfg if rules_cfg is not None else load_declared_rules()
    if not cfg or not cfg.get("enabled", True):
        return UnionOutcome("not_applicable", reason="union_resolver disabled or undeclared")
    decls = cfg.get("files") or []
    files = _unmerged_files(cwd, runner)
    if not files:
        return UnionOutcome("not_applicable", reason="no unmerged files")

    plans: List[Tuple[str, Dict[str, Any], str, List[str]]] = []
    try:
        for rel in files:
            decl = match_declaration(rel, decls)
            if decl is None:
                raise UnionRefused(f"undeclared: {rel} matches no union_resolver.files entry")
            rules = _declared_rules(decl, rel)
            base, main, card = _read_stages(cwd, rel, runner, mode)
            merged, notes = merge_three_way(base, main, card, rules)
            plans.append((rel, decl, "".join(merged), notes))
    except UnionRefused as exc:
        logger.info("union_resolver: refused -- %s", exc)
        return UnionOutcome("refused", files=files, reason=str(exc))

    rules_used = [f"{rel}:{n}" for rel, _d, _t, notes in plans for n in notes]
    tests: List[str] = []
    for rel, decl, _t, _n in plans:
        for t in _expand_tests(decl, rel, cwd):
            if t not in tests:
                tests.append(t)
    if dry_run:
        return UnionOutcome("resolved", files=files, rules_used=rules_used, tests=tests,
                            reason="dry-run: nothing written")

    written: List[str] = []
    verifiers: List[str] = []
    try:
        for rel, _d, text, _n in plans:
            target = pathlib.Path(cwd) / rel
            with open(target, "w", encoding="utf-8", newline="") as fh:
                fh.write(text)
            written.append(rel)
            add = _run(["git", "add", "--", rel], cwd=cwd, runner=runner, timeout=DEFAULT_GIT_TIMEOUT)
            if getattr(add, "returncode", 1) != 0:
                raise UnionRefused(f"{rel}: git add failed: {(getattr(add, 'stderr', '') or '')[:200]}")
        for rel, _d, text, _n in plans:
            verifiers.extend(f"{rel}:{v}" for v in verify_file(rel, text, cwd, verify_runner))
        _diff_check(cwd, files, runner)
        verifiers.append("diff_check")
    except UnionRefused as exc:
        logger.info("union_resolver: verification refused -- %s", exc)
        for rel in written:
            _run(["git", "checkout", "--merge", "--", rel], cwd=cwd, runner=runner,
                 timeout=DEFAULT_GIT_TIMEOUT)
        return UnionOutcome("refused", files=files, rules_used=rules_used,
                            verifiers=verifiers, reason=str(exc))
    logger.info("union_resolver: resolved %s (%s)", files, "; ".join(rules_used))
    return UnionOutcome("resolved", files=files, rules_used=rules_used,
                        verifiers=verifiers, tests=tests)


# ── CLI ──────────────────────────────────────────────────────────────────────
def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--worktree", help="a checkout whose index holds unmerged files")
    ap.add_argument("--mode", choices=("rebase", "merge"), default="rebase",
                    help="which side stage :2 is (rebase: the base branch; merge: the card)")
    ap.add_argument("--dry-run", action="store_true", help="resolve in memory, write nothing")
    ap.add_argument("--list-rules", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if args.list_rules:
        cfg = load_declared_rules()
        payload = {"rules": RULE_DESCRIPTIONS, "declared_files": cfg.get("files") or [],
                   "enabled": bool(cfg.get("enabled", True)) if cfg else False}
        print(json.dumps(payload, indent=2) if args.json else
              "\n".join(f"{k:24} {v}" for k, v in RULE_DESCRIPTIONS.items())
              + "\n\ndeclared:\n" + "\n".join(
                  f"  {d.get('path')}: {d.get('rules')}" for d in payload["declared_files"]))
        return 0
    if not args.worktree:
        ap.error("--worktree is required unless --list-rules")
    outcome = resolve_index_conflicts(args.worktree, mode=args.mode, dry_run=args.dry_run)
    if args.json:
        print(json.dumps(outcome.to_dict(), indent=2))
    else:
        print(f"{outcome.outcome}: {outcome.reason or ', '.join(outcome.rules_used) or 'nothing to do'}")
        for f in outcome.files:
            print(f"  {f}")
    return 0 if outcome.outcome == "resolved" else 1


if __name__ == "__main__":
    sys.exit(main())
