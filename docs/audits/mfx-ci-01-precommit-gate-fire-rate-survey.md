# CUI // SP-CTI

# mfx-ci-01 — fire-rate and latency survey for three commit-time checks

**Measured** 2026-09-04 on `C:/AI/ICDev` (worktree `.tmp/worktrees/mfx-ci-01`),
against `origin/main` at `ff424c071a04`.

CLAUDE.md's PreToolUse rule requires a fire-rate survey *before* a check is
armed, and states that refusing **1.63%** of routine work is already grounds for
standing a check down. This is that survey for the three checks mfx-ci-01 adds.

Every number below was measured **twice, by two independent sessions**, and
where the two disagree both readings are printed. A figure that only one
observer can reproduce is reported as such rather than averaged away.

## What was armed

| # | Check | Where it runs |
|---|-------|---------------|
| 1 | `npx playwright test --list` — every spec must PARSE | the **required** `Lint` job |
| 2 | mirror parity over the **staged** files | `.githooks/pre-commit` |
| 3 | undeclared-import census `--staged --check` | `.githooks/pre-commit` |

## Method

200 first-parent commits on `origin/main`, each replayed **against its own
tree** — `git ls-tree` blob hashes at that commit, and file contents via
`git show <commit>:<path>` — so the question answered is *"would this commit
have been refused when it was made"*, not *"is it drifted today"*.

The replay imports the **shipped** predicates (`pre_commit_check._mirror_scope`,
`mirror_parity.is_mirror_shim`, `pre_commit_check._mirror_excluded_extensions`,
`undeclared_import_census.build_report`). It is deliberately **not** a second
copy of the rule — a survey that re-implements the predicate measures the
survey, which is the defect this repo has a standing rule against.

Re-derive with the script in [Appendix A](#appendix-a--the-replay-script).

## Result — fire rates

| Check | Commits in scope | Fires | Rate (all 200) | Routine refusals |
|-------|-----------------:|------:|---------------:|-----------------:|
| e2e `--list` | 22 | 2 | 1.00% | **0** |
| mirror parity | 142 | 4 | 2.00% | **0** |
| census | 141 | 0 | 0.00% | **0** |

**Every fire was a real defect.** The rate that matters is refusals of *routine*
work, and it is 0 for all three. All three headline rows were reproduced
independently by a second session.

### e2e — 2 fires, one broken file

Both are `tests/e2e/key_pages_smoke.spec.ts`, `SyntaxError: Unexpected token
(115:0)`: `d47d6f0870` (the #2052 squash) and `e96bdc09a4` (the #2051 merge that
landed on top of it). The hand-fix `a721926530` lists clean at **840 tests in 65
files**. Playwright loads every spec before it runs one, so those two commits
executed **zero** of the 840 tests across all four shards.

### mirror parity — 4 fires, every one later repaired BY HAND

| Commit | File | How it was actually fixed |
|--------|------|---------------------------|
| `4a9f239057` | `db/schema/tables.yaml` | the **rmf-rfp-01 incident**; CI red, a human mirrored 199 files |
| `8a1c0e446d` | `workflow/validated_commit.py` | an **entire follow-up card** — #1986 *"mirror the PYTHONPATH fix into icdev/, where the wheel reads it"* (kpr-rvfy-06) |
| `4ebc87689d` | `genesis/daemon.py` | a later commit (#1976) |
| `22bf9a47b5` | `slides/constants.py` | sat drifted **for weeks**; reconciled by this card |

All four are in parity today — which is the proof that each was genuine
unreconciled drift rather than a deliberate divergence. #1986 is the strongest
case: a whole PR whose only job was the mirror copy this hook would have
demanded at `git commit`.

**Candidate breakdown, and a counting caveat.** The two sessions counted
different units and both were right; the numbers are given with their unit
named, because merging them is how a survey starts lying:

| Bucket | (commit, file) events | Distinct files |
|--------|----------------------:|---------------:|
| raw candidates | 131 | 66 |
| removed by the excluded-extension rule (`.md`) | 124 | 60 |
| removed by the shim rule | 3 | 2 |
| **fires** | **4** | **4** |
| reported as a note, never blocking (`missing_twin`) | 69 | 59 |

A second session replaying from the earlier `survey.json` (a different scoping
pass) counted 58 raw file-fire events over 27 distinct files, with 21 distinct
removed by the extension rule and 2 by the shim rule. **Both arrive at exactly
the same 4 survivors**, which is the number the decision rests on. The
divergence is in what each pass admitted as a raw candidate, not in the finding.

`.md` manifest shards are `merge=union` and diverge transiently **by design**;
without that exclusion the check would refuse constantly.

### census — 0 routine fires, and two apparent fires that are survey artifacts

The census fires correctly on its incident: `ccc7a0e2d` (rmf-wp-02),
`exporter.py:434`, module `markdown`.

An earlier replay reported 2 fires — `cui_marker` and `audit_logger` in
`compliance/poam_generator.py`, `compliance/stig_checker.py`,
`mbse/des_assessor.py`. **These are sandbox artifacts, not findings.** That
replay copied only the changed `.py` files into a temp tree, so first-party
repo modules were unresolvable and read as undeclared third-party. Re-derived
against the real tree:

```
$ python tools/ci/undeclared_import_census.py --changed \
    tools/compliance/poam_generator.py tools/compliance/stig_checker.py \
    tools/mbse/des_assessor.py --check
Undeclared-import census (changed): 0 site(s) seen, 0 registered, 0 unregistered
exit=0
```

Independently confirmed by the second session, which reproduced it a different
way: taking the first-party name set from
`git ls-tree -r --name-only <commit> -- tools icdev/tools` gives **0 fires over
140 in scope**. Reporting those two would have published a 1.00% fire rate for a
check that refuses nothing.

## Result — latency

Medians, **never best-of-N**: a hook costs what it usually costs, not what its
luckiest run did. The bare interpreter floor on this host is ~41 ms, so a
subprocess check can never be cheaper than that.

| Scenario | Mirror | Census (obs. A) | Census (obs. B) | Added total |
|----------|-------:|----------------:|----------------:|------------:|
| nothing staged under `tools/<pkg>/` | 0 ms | 0 ms | 0 ms | **0 ms** — both skipped |
| 1 staged file | 80 / 77 ms | 127 ms | 350 ms | 207–427 ms |
| 3 staged files | 79 / 78 ms | 126 ms | 353 ms | 206–431 ms |
| 60 staged files, one package | 97 ms | 126 ms | 544 ms | 224–641 ms |

**The mirror numbers agree to within 3 ms between observers. The census numbers
do not, and the gap is not explained.** Observer A measured n=7 with a tight
spread (min 123, max 134 ms); observer B measured n=7 at ~2.8x that. The most
likely cause is host load — five agent sessions were live on this machine — but
that was not proven, so both are printed. **The conclusion holds at either
reading**: the worst case (641 ms at 60 staged files) is inside the 1 s budget,
and the common commit pays **0** because the scope walk finds nothing and
neither subprocess is spawned. The hook's own budget note is 155 ms for the
pre-existing checks.

Quote the range. A single figure here did not survive re-measurement once
already (see below).

### Why `--files` and not `--paths <pkg>`

| | median |
|---|---:|
| `mirror_parity.py --files` (3 files) | 78 ms |
| `mirror_parity.py --paths db` (whole package, 864 pairs) | 504–512 ms |

`tools/db` pair counts, verified: live 864, mirror 914, both-sides 864.

**6.4x** — and the ratio is the *smaller* half of the argument. The larger half
is **correctness of scope**: `--paths db` reports the package's **pre-existing**
backlog, which the committing author neither caused nor can fix without
stepping on the PR that owns it. A hook may only block on what the commit
staged.

> An earlier draft quoted **4486 ms** and **"46x"** for `--paths db`. That was a
> single **cold** invocation and does not reproduce; the warm median is
> ~508 ms. Corrected here and in every comment that carried it. The inflated
> ratio was doing argumentative work it should never have been doing — the
> scope argument stands on its own.

### `npx playwright test --list`

| Observer | Warm runs | Median |
|---|---|---:|
| A | 2.61 / 1.94 / 2.04 / 2.19 / 2.15 s | ~2.15 s |
| B | 2.18 / 3.68 / 6.72 s | ~3.68 s |

High variance under load; call it **~2–4 s, up to ~7 s on a busy host**. An
earlier draft quoted "1.8 s warm / 14.6 s cold" — **neither figure reproduces
for either observer, and both are withdrawn.**

`--list` needs **no browser and no dashboard**: `ICDEV_NO_SERVER=1` leaves
`webServer` undefined (`playwright.config.ts:147`) and Playwright skips
`globalSetup` for `--list` (`:80`). Verified live: 840 tests in 65 files,
exit 0, no browser and no dashboard spawned. It is a **parse gate only** —
`forbidOnly` is not applied to `--list` (probed: a `test.only` lists clean under
`CI=1`), so a stray `.only` remains the E2E job's to refuse.

## The shim rule, and the budget ratchet 13 → 9

`args/mirror_parity_gate.yaml`'s marker required the phrase `"re-export"`. Three
of the five real shims say **"Backward-compat shim"** instead —
`tools/billing/tier.py`, `tools/testing/qa_agent_runner.py`,
`tools/testing/selector_healer.py` — so a rule that exists to *exclude* shims
was counting them as drift. Measured both ways on this tree:

| Marker | Matches of the 5 real shims | Drifted `.py` counted |
|--------|---------------------------:|----------------------:|
| old, `"re-export"` only | 2 | 12 |
| new, admits `"shim"` | 5 | 9 |

All five are byte-drifted, so all five would have been counted without the fix.
A shim's two names resolve to **one module object** (xit-decl-02), so there is
no stale half and comparing bytes is meaningless. The predicate now lives once,
in `mirror_parity.is_mirror_shim`; `coherence_checker._is_mirror_shim`
delegates to it, so the hook and the gate cannot disagree about what a shim is.

`budget` therefore ratchets **13 → 9** — a lowering, never a raise —
and `coherence_checker --check mirror_parity` passes at exactly 9. One genuine
reconciliation is in that drop (`icdev/tools/slides/constants.py`).

## Appendix A — the replay script

Run from the repo root. It is reproduced here in full rather than committed as a
tool: it answers a one-off question about history, and a survey with a `--gate`
earns itself a `|| true` (kpr-fix-03).

```python
# Replay the SHIPPED mirror predicate over history. Not a second copy of the
# rule: it calls mirror_parity.is_mirror_shim and reads the gate's own
# excluded_extensions, exactly as the hook does.
import json, re, subprocess, sys, tempfile, os
from pathlib import Path

ROOT = Path.cwd(); os.chdir(ROOT); sys.path.insert(0, str(ROOT))
from tools.dx.mirror_parity import is_mirror_shim
from tools.testing.pre_commit_check import _mirror_excluded_extensions, _mirror_scope

N = int(sys.argv[1]) if len(sys.argv) > 1 else 200
EXCLUDED = _mirror_excluded_extensions()
PKG_RE = re.compile(r"^(?:icdev/)?tools/([^/]+)/(.+)$")
SCRATCH = Path(tempfile.gettempdir()) / "mfx-mirror-replay"; SCRATCH.mkdir(exist_ok=True)

def git(*a):
    return subprocess.run(["git", *a], capture_output=True, text=True,
                          encoding="utf-8", errors="replace").stdout

def blob(commit, path):
    r = subprocess.run(["git", "show", f"{commit}:{path}"], capture_output=True)
    return r.stdout if r.returncode == 0 else None

def shim_at(commit, path, cache):
    key = (commit, path)
    if key not in cache:
        data = blob(commit, path)
        if data is None:
            cache[key] = False
        else:
            tmp = SCRATCH / "probe.py"; tmp.write_bytes(data)
            cache[key] = is_mirror_shim(tmp)
    return cache[key]

def replay(commit, cache):
    parents = git("log", "-1", "--format=%P", commit).split()
    if not parents:
        return None
    rows = []
    for line in git("diff", "--name-status", parents[0], commit).splitlines():
        parts = [p for p in line.split("\t") if p.strip()]
        if len(parts) >= 2:
            rows.append((parts[0], parts[-1]))
    scope = _mirror_scope(rows)                      # THE SHIPPED SCOPE FUNCTION
    if not scope:
        return {"in_scope": False}
    pairs = {}
    for p in scope:
        m = PKG_RE.match(p); pairs[f"{m.group(1)}/{m.group(2)}"] = None
    want = []
    for rel in pairs:
        want += [f"tools/{rel}", f"icdev/tools/{rel}", f"icdev/tools/{rel.split('/')[0]}"]
    tree = {}
    for line in git("ls-tree", "-r", commit, "--", *sorted(set(want))).splitlines():
        meta, _, path = line.partition("\t"); bits = meta.split()
        if len(bits) >= 3:
            tree[path] = bits[2]
    pkgs_with_twin = {p.split("/")[2] for p in tree if p.startswith("icdev/tools/")}
    drift, excluded_ext, shims, not_mirrored, missing = [], [], [], [], []
    for rel in sorted(pairs):
        pkg = rel.split("/")[0]
        live, mirror = tree.get(f"tools/{rel}"), tree.get(f"icdev/tools/{rel}")
        if pkg not in pkgs_with_twin:
            not_mirrored.append(rel); continue
        if Path(rel).suffix in EXCLUDED:
            excluded_ext.append(rel); continue
        if (live and shim_at(commit, f"tools/{rel}", cache)) or \
           (mirror and shim_at(commit, f"icdev/tools/{rel}", cache)):
            shims.append(rel); continue
        if live and mirror and live != mirror:
            drift.append(rel)
        elif live and not mirror:
            missing.append(rel)
    return {"in_scope": True, "fire": bool(drift), "drift": drift,
            "excluded_ext": excluded_ext, "shims": shims,
            "not_mirrored": not_mirrored, "missing_twin": missing}

commits = git("rev-list", "--first-parent", f"-{N}", "origin/main").split()
cache, rows = {}, []
for c in commits:
    r = replay(c, cache)
    if r is None:
        continue
    r["sha"] = c[:10]; r["subject"] = git("log", "-1", "--format=%s", c).strip()[:80]
    rows.append(r)
in_scope = [r for r in rows if r["in_scope"]]
fires = [r for r in in_scope if r["fire"]]
print(json.dumps({
    "in_scope": len(in_scope), "fires": len(fires),
    "fire_rate_pct_all": round(100.0 * len(fires) / len(rows), 2),
    "shim_events": sum(len(r["shims"]) for r in in_scope),
    "excluded_ext_events": sum(len(r["excluded_ext"]) for r in in_scope),
    "fired_on": [{"sha": r["sha"], "drift": r["drift"], "subject": r["subject"]} for r in fires],
}, indent=2))
```

## Standing rules this survey establishes

1. **Never raise** a budget, a timeout or a census ceiling to get a commit
   through. Reconcile with `--fix` and `git add` the `icdev/` copy.
2. **Never edit** `args/mirror_drift_baseline.yaml` to make a commit pass.
3. Both hook checks **fail open** on any error — no `yaml`, an unreadable
   report, a missing tool. CI is the backstop, and a hook that wedges a commit
   gets `--no-verify`'d, which is strictly worse than one that occasionally
   misses.
4. `missing_from_mirror` is a **note, never a block**: it is ungated in the gate
   YAML and in CI, and the backlog is ~300 files (59 distinct in this sample
   alone).
5. **Quote ranges, name your observer, and state the unit.** Three figures in
   the first draft of this card did not survive re-measurement, and every one of
   them was a single run reported as a fact.
