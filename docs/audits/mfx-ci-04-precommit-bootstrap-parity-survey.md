# mfx-ci-04 — pre-commit bootstrap parity: fire-rate survey and latency measurement

Date: 2026-09-06 (build, latency, first replay) and 2026-09-07 (replay re-run).
Tree: this branch on top of origin/main @ 4f869b771; the survey below is replayed
against origin/main @ d9a8e75a3 (2026-09-07), sixteen commits later, and the
2026-09-06 reading against 4f869b771 is quoted beside it.

## The defect

`icdev init` copies `icdev/data/claude_bootstrap/CLAUDE.md`, not the repo's
`CLAUDE.md`. PR #2137 added 74 lines to CLAUDE.md, never ran
`python tools/installer/prebuild_bootstrap.py`, and was red for hours on
`test_payload_rule_is_green_on_the_tree_as_committed` with zero payload defects:
`check_bootstrap_parity` had found one stale packaged file (#960 again). The fix
was one documented command. The cost was the feedback delay, because
`.githooks/pre-commit` mentioned the bootstrap zero times and the first thing to
notice was a full CI run.

## What ships

`tools/testing/pre_commit_check.py` (mirrored byte-identical to `icdev/`) gains
a bootstrap-parity gate that:

* fires only when the commit stages a path `prebuild_bootstrap.py` scaffolds —
  the script's `SOURCES` literal plus the `AI_PLATFORM_FILES` literal it extends
  from, read with `ast` (never a second copy, never an import: the tools/ shim
  costs ~137 ms on every commit for a list that is a literal);
* in scope, runs the same `coherence_checker.py --check bootstrap_parity` CI
  runs and prints that check's own message plus the regeneration command;
* regenerates nothing (a test asserts it never spawns or imports the script);
* additionally compares the INDEX blobs of each staged must-match target and
  its packaged copy, because the coherence check reads the working tree and
  `git add CLAUDE.md` after a regeneration leaves the commit stale while the
  tree on disk is in parity.

## Fire-rate survey

Replayed through the SHIPPED predicates — `_scaffolded_sources` with a
`git show <commit>:<path>` reader so each commit is judged by its own
`prebuild_bootstrap.py` / `ai_platforms.py`, and `_bootstrap_scope` — with the
parity verdict re-derived from git objects: the `must_match` pairs of that
commit's own `args/bootstrap_parity.yaml` compared by blob id, plus the payload
rule (every `PAYLOAD_MODULES` entry of every packaged hook present in the
payload and equal to `tools/hooks/<module>`). Blob equality is byte equality,
which is the comparison `check_bootstrap_parity` makes; it is read from the
object store because the check itself is bound to one checkout's working tree.

A FIRE is: the commit changes (vs its first parent) a scaffolded path AND the
tree as committed fails parity. `caused` means the parent was in parity and this
commit broke it; `inherited` means the parent was already broken.

Two populations, because a hook sees branch commits while `main` sees merges:

| population (replayed 2026-09-07) | commits | in scope | broken as committed | FIRES | caused / inherited |
|---|---|---|---|---|---|
| `--first-parent 200` on origin/main @ d9a8e75a3 (what main CARRIES: merges and direct commits) | 200 | 54 (27.00%) | 0 | 0 (0.00%) | 0 / 0 |
| `--no-merges 500` on the same ref (the BRANCH commits a pre-commit hook actually sees) | 500 | 120 (24.00%) | 1 | 1 (0.20%) | 1 / 0 |
| PR #2137's own branch, `--first-parent 12` from its head e10feac3f | 12 | 6 (50.00%) | 1 | 1 (8.33%) | 1 / 0 |

In-scope paths by root, no-merges 500: CLAUDE.md 85, .claude/commands 29,
.claude/hooks 6, .env.example 5, tools (shared_checks.py) 2, .env.sample 2.
First-parent 200: CLAUDE.md 28, .claude/commands 23, .env.example 3,
.claude/hooks 3, tools 2. `sources_unreadable` 0 and `declaration absent` 0 in
every population: every commit replayed carried a parseable
prebuild_bootstrap.py, ai_platforms.py and args/bootstrap_parity.yaml.

The SAME replay on 2026-09-06 against origin/main @ 4f869b771, before PR #2137
had merged: first-parent 200 read 48 in scope (24.0%), 0 broken, 0 fires;
no-merges 500 read 114 in scope (22.8%), 0 broken, 0 fires. Both readings are
quoted because one figure off a moving ref is not a measurement; the difference
between them is one merged PR, and it is the PR the card was written about.

The one fire, in both the no-merges population and the PR's own branch:

    FIRE 4b1978dfa fix(e2e): the documented throwaway-database recipe actually
                   isolates, and it is MEASURED (qa-fail-6a87916931be3793)
         CLAUDE.md != icdev/data/claude_bootstrap/CLAUDE.md
         staged in scope: ['CLAUDE.md']     parent in parity: caused

That is THE INCIDENT: the PR #2137 commit that added the CLAUDE.md lines and
did not regenerate the payload. Its repair is the branch's next merge commit,
0acc4f7b9 "Merge origin/main: take main's landed RLS fix, and regenerate the
packaged bootstrap CLAUDE.md" -- made by hand, after the red CI run -- which is
why the first-parent walk of main carries 0 broken trees: the defect lives
only in the branch population, which is exactly the population a pre-commit
hook sees and a survey over merges cannot.

Reading: the gate fires on 1 of 500 branch commits (0.20%) and on 0 of
200 main commits, and the one fire IS the incident. It refuses no routine
work: the other 119 in-scope branch commits, and all 54 in-scope first-parent
commits, had regenerated the payload before committing, so the hook would have
printed `Bootstrap parity: OK` and let them through. 0.20% is an eighth of the
1.63% CLAUDE.md already calls grounds for standing a check down. The cost side
is the in-scope rate, not the fire rate: roughly a quarter of commits stage a
scaffolded path (CLAUDE.md is the most-edited file in the tree), and each of
those pays the ~0.3 s shell-out measured below; the other three quarters pay
two `ast` parses inside the interpreter's own noise.

A THIRD LIVE INSTANCE, while this card was in flight. Sibling PR #2159
(kanban/rmf-rail-02, opened 2026-09-07 20:22Z, one day after the card was
written) changed CLAUDE.md and not icdev/data/claude_bootstrap/CLAUDE.md, and its
ICDEV CI run 34159165593 is red on exactly the two tests the incident named:
`tests/test_bootstrap_hook_payload.py::test_payload_rule_is_green_on_the_tree_as_committed`
(shard 3) and
`tests/test_init_goals_and_selective.py::test_packaged_claude_md_is_not_a_stripped_template`
(shard 4). Its author committed without this hook. With it, the commit is
refused in ~0.8 s naming `python tools/installer/prebuild_bootstrap.py`.

## Latency

Whole hook (`python tools/testing/pre_commit_check.py`) against a real staged
index on this host, five runs each, median and range, before (HEAD's hook) and
after (this branch's hook):

| staged shape | before (HEAD hook) | after (this hook) | verdict after |
|---|---|---|---|
| docs-only, no scaffolded file | 377 ms (357–390) | 386 ms (369–405) | pass; the two `ast` parses, inside the before-range |
| CLAUDE.md + its packaged copy, in parity | 489 ms (478–490) | 821 ms (796–864) | pass; +332 ms = coherence shell-out ~190 ms + two `git rev-parse` |
| CLAUDE.md staged, packaged copy regenerated but NOT staged | 424 ms (390–449) | 745 ms (731–753) | REFUSED, naming `git add CLAUDE.md icdev/data/claude_bootstrap/CLAUDE.md` |

Re-measured 2026-09-07 with the same driver while a kanban scheduler cycle and
two genesis daemons were working on the host (the 2026-09-06 run had a quieter
machine):

| staged shape | before (HEAD hook) | after (this hook) | delta |
|---|---|---|---|
| docs-only, no scaffolded file | 425 ms (395–442) | 435 ms (409–455) | +10, inside the before-range |
| CLAUDE.md + its packaged copy, in parity | 549 ms (524–580) | 888 ms (831–1263) | +339 |
| CLAUDE.md staged, packaged copy NOT staged | 473 ms (442–493) | 806 ms (759–860), REFUSED | +333 |

Every absolute figure is ~50–70 ms higher on BOTH sides and the deltas are the
same within noise (+9/+332/+321 yesterday, +10/+339/+333 today). The delta is
the hook's cost; the absolute is the host's. Both readings are quoted because
one figure from one run does not survive re-measurement.

The "before" figures differ between shapes because the domain-leak gate, which
runs on every commit, regex-scans a 250 KB CLAUDE.md when it is staged; that
cost is pre-existing and identical on both sides. Driver, run from the repo root
with the branch's `tools/testing/pre_commit_check.py` as `AFTER` and HEAD's copy
(`git show HEAD:tools/testing/pre_commit_check.py`) written three directories
below the root as `BEFORE`, so both resolve the same `BASE_DIR`:

```python
import statistics, subprocess, sys, time
ROOT = "."
BEFORE = ".tmp/mfx-ci-04/pre_commit_check_before.py"
AFTER = "tools/testing/pre_commit_check.py"
def git(*a): subprocess.run(["git", *a], cwd=ROOT, check=True, capture_output=True)
def measure(script):
    ts = []; rc = None
    for _ in range(5):
        t = time.perf_counter()
        p = subprocess.run([sys.executable, script], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
        ts.append((time.perf_counter() - t) * 1000); rc = p.returncode
    return statistics.median(ts), min(ts), max(ts), rc, p.stdout.strip().splitlines()
SHAPES = {
    "docs-only (no scaffolded file staged)": ["docs/audits/mfx-ci-04-precommit-bootstrap-parity-survey.md"],
    "CLAUDE.md + its packaged copy staged": ["CLAUDE.md", "icdev/data/claude_bootstrap/CLAUDE.md"],
    "CLAUDE.md staged, packaged copy NOT staged": ["CLAUDE.md"],
}
git("reset", "-q")
for name, files in SHAPES.items():
    git("reset", "-q"); git("add", *files)
    for label, script in (("before", BEFORE), ("after", AFTER)):
        med, lo, hi, rc, out = measure(script)
        print(f"{name:45s} {label:6s} median {med:6.0f} ms  range {lo:.0f}-{hi:.0f}  exit {rc}")
        if label == "after":
            for line in out:
                if "Bootstrap" in line or "BLOCKED" in line or "git add" in line: print("      " + line)
git("reset", "-q")
```

Components, five runs each (from `.tmp` scratch, reproduced by the snippet
below): bare interpreter 42 ms (41–45); `import tools.dx.ai_platforms` through
the shim 179 ms (177–195); `ast.parse` of both declaration files 42 ms (41–47),
i.e. inside the interpreter's own noise; `coherence_checker.py --check
bootstrap_parity --json` 191 ms (181–199).

```python
import statistics, subprocess, sys, time
CASES = {
  "bare python": [sys.executable, "-c", "pass"],
  "import tools.dx.ai_platforms (shim)": [sys.executable, "-c", "import tools.dx.ai_platforms"],
  "ast-read prebuild+ai_platforms": [sys.executable, "-c",
     "import ast,pathlib;ast.parse(pathlib.Path('tools/installer/prebuild_bootstrap.py').read_text(encoding='utf-8'));ast.parse(pathlib.Path('tools/dx/ai_platforms.py').read_text(encoding='utf-8'))"],
  "coherence --check bootstrap_parity": [sys.executable, "tools/workflow/coherence_checker.py", "--check", "bootstrap_parity", "--json"],
}
for name, cmd in CASES.items():
    ts = []
    for _ in range(5):
        t = time.perf_counter(); subprocess.run(cmd, capture_output=True); ts.append((time.perf_counter()-t)*1000)
    print(f"{name:40s} median {statistics.median(ts):7.0f} ms  range {min(ts):.0f}-{max(ts):.0f}")
```

## Replay script, in full

Run from the repo root as `python survey.py --first-parent 200 --no-merges 500
--json out.json`. It imports the shipped `tools.testing.pre_commit_check`.

```python
#!/usr/bin/env python3
"""mfx-ci-04 fire-rate survey: would the pre-commit bootstrap-parity gate have fired?

Replays history through the SHIPPED predicates -- pre_commit_check._scaffolded_sources
(with a `git show <commit>:<path>` reader, so each commit is judged by ITS OWN
prebuild_bootstrap.py / ai_platforms.py) and pre_commit_check._bootstrap_scope --
and re-derives the parity verdict at each commit from git objects: the must_match
pairs in that commit's own args/bootstrap_parity.yaml compared by BLOB ID, plus the
payload rule (every PAYLOAD_MODULES entry of every packaged hook must exist in the
payload and equal tools/hooks/<module>). Blob equality is byte equality, which is
the comparison check_bootstrap_parity makes; it is read from the object store
because the check itself is bound to the working tree of one checkout.

A FIRE is: the commit stages (changes vs its first parent) a scaffolded path AND the
tree as committed fails parity. `caused` means the parent was in parity and this
commit broke it; `inherited` means the parent was already broken.

Usage: python .tmp/mfx-ci-04/survey.py [--first-parent N] [--no-merges N] [--json out]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import yaml  # noqa: E402

from tools.testing import pre_commit_check as pcc  # noqa: E402

HOOKS_DIR = "icdev/data/claude_bootstrap/claude/hooks"


def git(*args: str) -> str | None:
    p = subprocess.run(["git", *args], capture_output=True, text=True, cwd=str(ROOT),
                       encoding="utf-8", errors="replace")
    return p.stdout if p.returncode == 0 else None


def show(commit: str, path) -> str | None:
    return git("show", f"{commit}:{str(path).replace(chr(92), '/')}")


def blob(commit: str, path: str) -> str | None:
    out = git("rev-parse", "-q", "--verify", f"{commit}:{path}")
    return out.strip() if out else None


def changed(commit: str) -> list[tuple[str, str]]:
    """(status, path) vs the FIRST parent -- what the branch/PR staged, in aggregate."""
    out = git("diff", "--name-status", f"{commit}^1", commit)
    if out is None:  # root commit
        out = git("show", "--name-status", "--format=", commit) or ""
    rows: list[tuple[str, str]] = []
    for line in out.splitlines():
        parts = [p for p in line.split("\t") if p.strip()]
        if len(parts) >= 2:
            rows.append((parts[0].strip(), parts[-1].strip().replace("\\", "/")))
    return rows


def parity(commit: str) -> dict:
    """{declared, drift[], payload_defects[]} for the tree AS COMMITTED."""
    text = show(commit, "args/bootstrap_parity.yaml")
    pairs = []
    if text:
        try:
            data = yaml.safe_load(text) or {}
            pairs = [(str(p["target"]), "icdev/" + str(p["source"]))
                     for p in (data.get("must_match") or [])
                     if isinstance(p, dict) and p.get("target") and p.get("source")]
        except Exception:
            pairs = []
    drift = []
    for target, packaged in pairs:
        a, b = blob(commit, target), blob(commit, packaged)
        if a and b and a != b:
            drift.append(f"{target} != {packaged}")
    payload = []
    listing = git("ls-tree", "--name-only", commit, HOOKS_DIR + "/") or ""
    for hook in listing.splitlines():
        if not hook.endswith(".py"):
            continue
        modules = pcc._literal_in_source(show(commit, hook), "PAYLOAD_MODULES")
        for module in modules or ():
            packaged = f"{HOOKS_DIR}/{module}"
            b = blob(commit, packaged)
            if not b:
                payload.append(f"{hook} loads {module}: not in payload")
                continue
            a = blob(commit, f"tools/hooks/{module}")
            if a and a != b:
                payload.append(f"{hook} loads {module}: stale")
    return {"declared": bool(pairs), "drift": drift, "payload_defects": payload}


def replay(commits: list[str]) -> dict:
    rows = []
    for c in commits:
        sources = pcc._scaffolded_sources(read=lambda rel, c=c: show(c, rel))
        scope = pcc._bootstrap_scope(changed(c), sources)
        here = parity(c)
        broken = bool(here["drift"] or here["payload_defects"])
        parent = parity(f"{c}^1") if git("rev-parse", "-q", "--verify", f"{c}^1") else {"drift": [], "payload_defects": []}
        parent_broken = bool(parent["drift"] or parent["payload_defects"])
        subject = (git("log", "-1", "--format=%s", c) or "").strip()
        rows.append({
            "commit": c[:9], "subject": subject[:90], "in_scope": scope,
            "sources_readable": bool(sources), "declared": here["declared"],
            "broken": broken, "parent_broken": parent_broken,
            "defects": here["drift"] + here["payload_defects"],
            "fire": bool(scope) and broken,
        })
    n = len(rows)
    in_scope = [r for r in rows if r["in_scope"]]
    fires = [r for r in rows if r["fire"]]
    caused = [r for r in fires if not r["parent_broken"]]
    return {
        "commits": n,
        "sources_unreadable": sum(1 for r in rows if not r["sources_readable"]),
        "undeclared": sum(1 for r in rows if not r["declared"]),
        "in_scope": len(in_scope),
        "in_scope_pct": round(100 * len(in_scope) / n, 2) if n else None,
        "broken_anywhere": sum(1 for r in rows if r["broken"]),
        "fires": len(fires),
        "fire_pct": round(100 * len(fires) / n, 4) if n else None,
        "fires_caused": len(caused),
        "fires_inherited": len(fires) - len(caused),
        "fire_rows": fires,
        "in_scope_paths_top": _top_paths(in_scope),
        "rows": rows,
    }


def _top_paths(rows: list[dict], k: int = 12) -> list[tuple[str, int]]:
    """In-scope paths bucketed by root: a dot-dir keeps two components."""
    counts: dict[str, int] = {}
    for r in rows:
        for p in r["in_scope"]:
            parts = p.split("/")
            key = "/".join(parts[:2]) if p.startswith(".") and len(parts) > 1 else parts[0]
            counts[key] = counts.get(key, 0) + 1
    return sorted(counts.items(), key=lambda kv: -kv[1])[:k]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--first-parent", type=int, default=200)
    ap.add_argument("--no-merges", type=int, default=500)
    ap.add_argument("--ref", default="origin/main")
    ap.add_argument("--json")
    a = ap.parse_args()
    fp = (git("rev-list", "--first-parent", "-n", str(a.first_parent), a.ref) or "").split()
    nm = (git("rev-list", "--no-merges", "-n", str(a.no_merges), a.ref) or "").split()
    result = {"first_parent": replay(fp), "no_merges": replay(nm)}
    for name, r in result.items():
        print(f"== {name}: {r['commits']} commits ==")
        print(f"  in scope (stage a scaffolded path): {r['in_scope']} ({r['in_scope_pct']}%)")
        print(f"  parity broken as committed:         {r['broken_anywhere']}")
        print(f"  FIRES (in scope AND broken):        {r['fires']} ({r['fire_pct']}%)"
              f"  caused {r['fires_caused']} / inherited {r['fires_inherited']}")
        print(f"  sources unreadable: {r['sources_unreadable']}   declaration absent: {r['undeclared']}")
        print(f"  in-scope paths by root: {r['in_scope_paths_top']}")
        for row in r["fire_rows"]:
            print(f"    FIRE {row['commit']} {row['subject']}")
            for d in row["defects"]:
                print(f"         {d}")
            print(f"         staged in scope: {row['in_scope'][:6]}")
    if a.json:
        Path(a.json).write_text(json.dumps(result, indent=2, default=list), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```
