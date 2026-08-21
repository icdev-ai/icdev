# CUI // SP-CTI
"""Is a live process running code that has since been superseded? (autonomy-id-02)

autonomy-id-01 made each process RECORD the commit it booted from. This asks the
next question — has anything it actually executes changed since — and answers it
per process rather than for the fleet.

KEYED ON THE PROCESS'S OWN IMPORT CLOSURE, NEVER ON THE TIP. A daemon is not
stale because an unrelated canvas merged; it is stale because code IT EXECUTES
changed. Measured on this repository, `main` takes several commits an hour, so a
detector keyed on the raw tip would mark every process stale within minutes of
every merge, and the signal would be ignored inside a day — the exact way a
check earns itself a `|| true`. The closure is what makes a `stale` verdict
worth acting on: it names the changed files the process imports.

WHY NOT THE KNOWLEDGE GRAPH, which already holds import edges. `edge_deriver`
derives them and `kg_edges` is populated — probed 2026-08-21, 8,964 nodes and
16,759 edges, so this is not a case of designing against an empty substrate. It
was rejected on FRESHNESS: the awareness reflex re-indexes every three hours
(`args/awareness_config.yaml`), and the index was last written 00:24 while this
was being built at 03:20. A closure that lags reality MISSES a newly added
import, and a missed import means an intersection that comes back empty, which
reports CURRENT. That is the one direction of error this module must not have —
a detector built to refuse false reassurance cannot be founded on an index whose
staleness manufactures it. The walk below is live and costs a few hundred
`ast.parse` calls.

Import parsing itself is NOT re-implemented: `_iter_imported_modules` is
imported from `edge_deriver`, so a change in how this repo recognises an import
lands in both places at once.

THREE VERDICTS, and only one is a finding:

    current       nothing in this process's closure has changed since it booted
    stale         at least one file it imports has changed — the files are named
    unmeasurable  no recorded version, no module, an unknown commit, or no git

`unmeasurable` is NEVER folded into `current`. "Nobody could check" and "checked
and fine" justify opposite actions, and collapsing them is the defect the whole
AUTONOMY card exists to remove.

DIRTINESS IS CARRIED, NEVER MERGED INTO THE VERDICT. A process that booted from
a modified tree is not running the tree its SHA names, so `current` cannot be
PROVEN for it. It is reported as `current` with `dirty: True` beside it rather
than given a fourth state: the verdict answers "has the recorded commit been
superseded", which is a real and separately useful question, and overloading it
with "and was the tree clean" would make both unreadable. A reader acting on
`current` must look at `dirty`.

REPORT ONLY. Restarting a stale daemon is autonomy-act-03's business and belongs
in that card's enumerated `restore` tier, performed by the supervisor with an
audit row. A detector that restarts things is an unaudited actuator.

Usage:
    python tools/awareness/code_staleness.py --json
    python tools/awareness/code_staleness.py
    python tools/awareness/code_staleness.py --module tools.genesis.daemon --since <sha>
"""

from __future__ import annotations

import ast
import subprocess  # nosec B404 — git only, fixed argv, shell=False
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set

_BASE = Path(__file__).resolve().parents[2]
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))

from tools.awareness.edge_deriver import _iter_imported_modules  # noqa: E402

# ── Verdicts ────────────────────────────────────────────────────────────────
#: Nothing this process imports has changed since it booted.
CURRENT = "current"
#: At least one file in its import closure has changed. THE finding.
STALE = "stale"
#: Could not be determined. Never folded into `current`.
UNMEASURABLE = "unmeasurable"

#: Only these roots are walked. A stdlib or third-party import is not part of
#: what a merge to this repository can change, and following it would turn a
#: bounded walk into a crawl of site-packages.
_LOCAL_ROOTS = ("tools", "icdev")

#: A hung git must never block the check.
GIT_TIMEOUT_SECONDS = 20

#: Refuses to walk forever if the graph is pathological. Reported when hit —
#: a truncated closure could miss the changed file and report `current`, so a
#: silent cap here would reintroduce exactly the false reassurance this module
#: is built to refuse.
MAX_CLOSURE_FILES = 4000


def _run_git(args: List[str], root: Path):
    return subprocess.run(  # nosec B603 B607 — fixed argv, shell=False, git only
        ["git", "-C", str(root), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=GIT_TIMEOUT_SECONDS, check=False, shell=False,
    )


def _module_to_paths(dotted: str, root: Path) -> List[Path]:
    """Candidate files for a dotted module: the module, then its package init.

    `tools.x` may be `tools/x.py` or `tools/x/__init__.py`, and `from tools.db
    import storage` yields BOTH `tools.db.storage` and `tools.db` — so each is
    resolved independently and whichever exists contributes.
    """
    parts = dotted.split(".")
    if not parts or parts[0] not in _LOCAL_ROOTS:
        return []
    out = []
    direct = root.joinpath(*parts).with_suffix(".py")
    if direct.is_file():
        out.append(direct)
    pkg = root.joinpath(*parts, "__init__.py")
    if pkg.is_file():
        out.append(pkg)
    return out


def import_closure(entry: str, root: Optional[Path] = None,
                   max_files: int = MAX_CLOSURE_FILES) -> Dict[str, Any]:
    """Repo-relative files reachable by import from *entry*.

    *entry* is a dotted module (``tools.genesis.daemon``) or a repo-relative
    path. Parsed with `ast`, never imported: importing `tools.genesis.daemon` to
    learn what it imports would start a daemon, and importing the Cortex stack to
    inspect it is heaviest on precisely the deployment where something is broken.

    An unparseable file contributes ITSELF and no edges — it is still code the
    process runs, so dropping it would shrink the closure and bias toward
    `current`.
    """
    base = root or _BASE
    start = entry
    if entry.endswith(".py") or "/" in entry or "\\" in entry:
        try:
            rel = Path(entry).resolve().relative_to(base)
            start = ".".join(rel.with_suffix("").parts)
        except (ValueError, OSError):
            return {"files": set(), "truncated": False, "unresolved": True}

    roots = _module_to_paths(start, base)
    if not roots:
        return {"files": set(), "truncated": False, "unresolved": True}

    seen: Set[Path] = set()
    queue: List[Path] = list(roots)
    truncated = False
    unparseable: List[str] = []

    while queue:
        path = queue.pop()
        if path in seen:
            continue
        if len(seen) >= max_files:
            truncated = True
            break
        seen.add(path)
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, SyntaxError, ValueError):
            unparseable.append(str(path))
            continue
        for dotted, _lineno in _iter_imported_modules(tree):
            for nxt in _module_to_paths(dotted, base):
                if nxt not in seen:
                    queue.append(nxt)

    files = set()
    for path in seen:
        try:
            files.add(path.relative_to(base).as_posix())
        except ValueError:
            continue
    return {"files": files, "truncated": truncated, "unresolved": False,
            "unparseable": unparseable}


def changed_files(since: str, until: str = "origin/main",
                  root: Optional[Path] = None, runner=None) -> Optional[Set[str]]:
    """Repo-relative paths changed between two commits, or None if unknowable.

    None means UNMEASURABLE and must never be read as "nothing changed": an
    unknown commit (a shallow clone, a build id from `ICDEV_BUILD_ID`, a branch
    never fetched) is a question we could not answer, not a clean answer.
    """
    base = root or _BASE
    run = runner or _run_git
    try:
        for ref in (since, until):
            probe = run(["rev-parse", "--verify", f"{ref}^{{commit}}"], base)
            if getattr(probe, "returncode", 1) != 0:
                return None
        result = run(["diff", "--name-only", f"{since}..{until}"], base)
    except (OSError, subprocess.SubprocessError):
        return None
    if getattr(result, "returncode", 1) != 0:
        return None
    out = (getattr(result, "stdout", "") or "").strip()
    if not out:
        return set()
    return {line.strip() for line in out.splitlines() if line.strip()}


def assess_process(row: Dict[str, Any], changed: Optional[Set[str]],
                   closure: Optional[Set[str]]) -> Dict[str, Any]:
    """One process's verdict. Pure — takes the facts, returns the reading."""
    version = row.get("code_version")
    module = row.get("module")
    out: Dict[str, Any] = {
        "session_id": row.get("session_id"),
        "module": module,
        "pid": row.get("pid"),
        "code_version": version,
        # Carried, never merged into the verdict — see the module docstring.
        "dirty": True if row.get("code_dirty") == 1 else (
            False if row.get("code_dirty") == 0 else None),
        "changed_in_closure": [],
    }
    if not version:
        out.update(verdict=UNMEASURABLE, reason="no recorded code version")
        return out
    if not module:
        out.update(verdict=UNMEASURABLE,
                   reason="no recorded module — the closure cannot be scoped")
        return out
    if changed is None:
        out.update(verdict=UNMEASURABLE,
                   reason=f"git could not compare {str(version)[:9]} with the tip")
        return out
    if closure is None:
        out.update(verdict=UNMEASURABLE,
                   reason=f"import closure for {module} could not be derived")
        return out

    hits = sorted(closure & changed)
    out["closure_size"] = len(closure)
    if hits:
        out.update(verdict=STALE, changed_in_closure=hits[:25],
                   changed_count=len(hits),
                   reason=f"{len(hits)} file(s) it imports changed since it booted")
    else:
        out.update(verdict=CURRENT, changed_count=0,
                   reason="nothing in its import closure has changed")
    return out


def report(until: str = "origin/main", root: Optional[Path] = None,
           runner=None, processes_fn=None) -> Dict[str, Any]:
    """Assess every live process. Never raises."""
    base = root or _BASE
    if processes_fn is None:
        from tools.coordination.code_identity import processes as processes_fn

    fleet = processes_fn()
    state = fleet.get("state")
    if state in ("unmeasurable", "no_live_processes"):
        # Pass the fleet's own answer through rather than inventing counts. An
        # empty list here is not a clean fleet.
        return {"state": state, "reason": fleet.get("reason"), "processes": [],
                "stale": None, "current": None, "unmeasurable": None}

    rows = fleet.get("processes") or []
    closures: Dict[str, Optional[Set[str]]] = {}
    results = []
    for row in rows:
        version = row.get("code_version")
        module = row.get("module")
        changed = changed_files(version, until, base, runner) if version else None
        if module and module not in closures:
            got = import_closure(module, base)
            closures[module] = None if got.get("unresolved") else got["files"]
        results.append(assess_process(row, changed, closures.get(module)))

    counts = {CURRENT: 0, STALE: 0, UNMEASURABLE: 0}
    for r in results:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
    return {
        "state": "measured",
        "processes": results,
        "stale": counts[STALE],
        "current": counts[CURRENT],
        "unmeasurable": counts[UNMEASURABLE],
        "any_stale": counts[STALE] > 0,
    }


def render(rep: Dict[str, Any]) -> str:
    out = [f"Process code staleness — {rep['state']}"]
    if rep.get("reason"):
        out.append(f"  {rep['reason']}")
    mark = {CURRENT: "  ok  ", STALE: " STALE", UNMEASURABLE: "  ??  "}
    for p in rep.get("processes", []):
        dirty = " +dirty" if p.get("dirty") else (
            " +dirty?" if p.get("dirty") is None else "")
        out.append(f"{mark.get(p['verdict'], '  ?   ')} "
                   f"{str(p.get('module') or p.get('session_id'))[:44]:44}"
                   f"{str(p.get('code_version') or '')[:9]:>10}{dirty}")
        if p["verdict"] != CURRENT:
            out.append(f"        {p.get('reason', '')}")
        for f in p.get("changed_in_closure", [])[:5]:
            out.append(f"          changed: {f}")
        extra = (p.get("changed_count") or 0) - 5
        if p["verdict"] == STALE and extra > 0:
            out.append(f"          … and {extra} more")
    if rep["state"] == "measured":
        out.append("")
        out.append(f"  stale {rep['stale']} · current {rep['current']} · "
                   f"unmeasurable {rep['unmeasurable']}")
        out.append("  (unmeasurable is NOT current — nobody could check those)")
    return "\n".join(out)


def main(argv: Optional[Iterable[str]] = None) -> int:
    import argparse
    import json

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--until", default="origin/main",
                        help="the ref to compare against (default origin/main)")
    parser.add_argument("--module", help="assess ONE module instead of the fleet")
    parser.add_argument("--since", help="the commit that module booted from")
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.module:
        if not args.since:
            parser.error("--module requires --since <commit>")
        got = import_closure(args.module)
        closure = None if got.get("unresolved") else got["files"]
        row = {"session_id": "(cli)", "module": args.module,
               "code_version": args.since, "code_dirty": None}
        result = assess_process(row, changed_files(args.since, args.until), closure)
        rep = {"state": "measured", "processes": [result],
               "stale": 1 if result["verdict"] == STALE else 0,
               "current": 1 if result["verdict"] == CURRENT else 0,
               "unmeasurable": 1 if result["verdict"] == UNMEASURABLE else 0,
               "any_stale": result["verdict"] == STALE}
    else:
        rep = report(until=args.until)

    print(json.dumps(rep, indent=2, default=str) if args.json else render(rep))
    # Report only — no --gate. A survey shipped with a gate earns itself a
    # `|| true` (kpr-fix-03). Exit 2 only when nothing could be produced.
    return 0 if rep.get("state") == "measured" else 2


if __name__ == "__main__":
    raise SystemExit(main())
