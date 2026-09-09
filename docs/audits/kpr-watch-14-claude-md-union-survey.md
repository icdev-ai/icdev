# CUI // SP-CTI

# kpr-watch-14 — the union rung had resolved nothing, and CLAUDE.md was 55.6% of why

Measured **2026-09-08** on the live PostgreSQL board (`icdev`) and against
`origin/main` at `c31218fcf`. Every figure below is re-derivable with the two
commands named beside it; a figure read off a live board is quoted with its
instant, because both sides move.

---

## 1. The rung was inert, not merely noisy

```sql
SELECT COUNT(*) FROM audit_trail WHERE action = 'pr_watcher.union_resolved';  -- 0
SELECT COUNT(*) FROM audit_trail WHERE action = 'pr_watcher.union_refused';   -- 65
```

`mfx-sib-03` shipped the union rung, `pr_watcher._maybe_rebase` has written one
row per attempt since, and across **9 distinct tasks it had resolved zero
conflicts and refused 65 times**. Reflex-level liveness green — the rung ran —
while the ACT it exists to perform reached nothing. That is
`reflex-level-liveness-does-not-prove-act-level-liveness` at the merge ladder.

63 of the 65 refuse on `undeclared:`. The other 2 (`dwr-ev-02`) refuse later,
inside a set whose first undeclared file is a canvas template.

## 2. What the refusals actually name

Replayed through the **shipped** `union_resolver.match_declaration` against the
live `union_resolver.files` — never a second copy of the matcher:

| split | rows | share |
|---|---:|---:|
| every file in the set undeclared | 53 | 84.1% |
| MIXED — a DECLARED file refused because an undeclared sibling shared the set | 10 | 15.9% |

The mixed 10 are `qa-fail-6a87916931be3793` (7) and `dwr-ev-03` (3). The card's
own survey recorded all 10 under the first task; both readings are quoted
because the board gained rows between the two runs.

Undeclared paths, counting **every** occurrence rather than only the first the
loop reached:

| path | sets |
|---|---:|
| `CLAUDE.md` | 35 |
| `icdev/data/claude_bootstrap/CLAUDE.md` | 35 |
| `tools/canvas_compliance/posture.py` (+ mirror) | 14 |
| `tools/security/row_security.py` (+ mirror) | 7 |
| `tools/doc_modernization/redline_drafter.py` (+ mirror) | 3 |
| `tools/document_intelligence/{ingest_orchestrator,suggestion_store}.py` | 2 each |

`CLAUDE.md` is 35 of 63 = **55.6%**, the single largest cause.

### A theory already tested and false

Every refusal message names the `icdev/` copy while every declaration is
spelled `tools/…`, which reads as a mirror-declaration asymmetry. It is not:
`match_declaration` canonicalises through `_MIRROR_PREFIXES` first, verified
against three declared pairs (`dashboard/app.py`,
`dashboard/templates/base.html`, `security_canvas/blueprint.py`). The `icdev/`
name is a **sort-order artifact** — `_unmerged_files` returns git's sorted
`--diff-filter=U` output and `icdev/` precedes `tools/` lexically, so the
mirror is simply the first undeclared file the loop reaches. Do not re-derive
this.

## 3. Would a union of CLAUDE.md have been RIGHT?

The card's constraint: *a wrong union on THIS file is a silent edit to the
operating rules.* So the declaration is made on a measurement, not on the shape.

```bash
python -m tools.kanban.claude_md_union_survey
```

**Ground truth is what actually landed.** For every merge commit on
`origin/main` touching the file, the survey rebuilds the three sides from git
(base = `merge-base` of the two parents), runs the **shipped**
`merge_three_way`, and compares against the file the merge commit recorded.
Asking the resolver whether it agrees with itself would prove only that it is
deterministic, which was never in question.

**The population is the conflicting merges, and GIT decides which those are.**
`git merge-file` separates the merges git resolved by itself: the rung never
sees those, and counting them would inflate the agreement rate with cases that
were never in question.

| | |
|---|---:|
| merge commits touching `CLAUDE.md` | 28 |
| git merged cleanly (never reaches the rung) | 17 |
| **CONFLICTED in git — the population** | **11** |
| exact — byte-for-byte what the human landed | 7 |
| blank_lines_only — same lines, one or two blank lines at a block seam | 4 |
| **union_lost_content** | **0** |
| union_kept_more | 0 |
| refused | 0 |

Zero content lost **in either parent orientation**. A merge commit does not
record which side was "main", and for an append-shaped file the orientation
decides only the ORDER of the two inserted blocks; an orientation that changed
the CONTENT would itself be a finding. The survey reports the best orientation
with the other beside it.

An earlier pass of this survey read "8 differ" — that was the orientation
artifact plus blank lines, before the comparison was made at set level. The
raw diff is misleading here and the set-level comparison is what the finding
rests on.

### End to end, on a real historical conflict

Merge `29dac7848` (`dwr-fid-03`, second pass) — the same two files that appear
in all 35 refusals — replayed by really merging its two parents in a scratch
worktree and running `resolve_index_conflicts` with the **shipped** declaration:

```
unmerged files: ['CLAUDE.md', 'icdev/data/claude_bootstrap/CLAUDE.md']
outcome: resolved
rules_used: ['CLAUDE.md:keep_both_blocks@626',
             'icdev/data/claude_bootstrap/CLAUDE.md:derived_from:CLAUDE.md']
verifiers:  [... 'icdev/data/claude_bootstrap/CLAUDE.md:derivation_parity', 'diff_check']
PARITY: True (327,665 bytes)
byte-identical to what LANDED: True
```

`981844beb` likewise resolves with parity and **0 non-blank lines lost or
gained** — the `blank_lines_only` verdict, confirmed on the real file.
`418eb1528` and `3e848b498` still REFUSE, on `.github/workflows/icdev-ci.yml`:
their conflict sets were never CLAUDE.md-only, and declaring CLAUDE.md does not
claim otherwise.

## 4. The generated copy is DERIVED, not unioned

All 35 CLAUDE.md sets also carried `icdev/data/claude_bootstrap/CLAUDE.md`, so
**declaring CLAUDE.md alone would have resolved none of them.** mfx-ci-04
requires that payload to be regenerated whenever CLAUDE.md changes, so the two
conflict together on every card.

It is declared `derived_from: CLAUDE.md`, which takes no rules and no three-way
merge: after the source resolves, its bytes are written to the copy, and the
copy is **re-read** to confirm (`derivation_parity`) rather than trusted from
the write. Two independent unions of two files that must be byte-identical is
how a branch lands out of `bootstrap_parity` — the union is only approximately
order-stable, and blank-line placement alone (see §3) would break it.

Proven equivalent to the regeneration rather than asserted:
`prebuild_bootstrap.SOURCES` maps `("CLAUDE.md", "CLAUDE.md", "file")` and
`_copy_file` is a bare `shutil.copy2` for it (its only transform is keyed on
`settings.json.template`). The test runs **that module's own copier** over CRLF
input, so if the payload ever stops being a byte copy, copying stops being the
right repair and the test says so.

Running `prebuild_bootstrap.py` itself was **rejected**: it copies nine other
trees, so a rebase would write files the card never touched — the same
objection that rules out repairing anything outside the conflict set.

### The one shape it refuses to repair

A declared copy of a resolved source that is **not in the conflict set and is
already stale**. Reachable only from a branch that was out of parity already
(two sides that each regenerated would both have changed it, so it would be
unmerged too). Writing it would put a change into the replayed commit that
neither side made; leaving it would land a resolved CLAUDE.md beside a stale
copy. So it refuses and names the repair. A `derived_from` declaration must
name **one literal path** — a copy has exactly one source, and a glob would
silently claim several.

## 5. What the declaration buys

All 63 undeclared-cause refusals replayed through the new declaration list:

| | rows | share |
|---|---:|---:|
| now fully declared — the rung would run | **34** | **54.0%** |
| still refused | 29 | 46.0% |

Four whole tasks clear entirely: `mfx-own-04` (10), `dwr-ev-03` (9),
`dwr-fid-03` (8), `dwr-fid-02` (7) — every one of their refusals was this pair
alone.

The remaining 29 name **ordinary source modules** and are **not candidates**: a
source module is not append-shaped, and declaring one to quieten a refusal is
exactly how the wrong rule shipped a broken file twice on 2026-09-03.

## 6. Partial resolution: measured, and refused

The 10 mixed refusals look like the case for resolving the declared subset and
leaving the rest conflicted. **It buys zero.**

`rebase_recovery.rebase_and_push` runs the whole rebase in a
`tempfile.mkdtemp` worktree and calls `git rebase --abort` followed by
`_cleanup` on any outcome other than `resolved`
(`tools/kanban/rebase_recovery.py:344,397-409,520`). So:

* a half-resolution is **deleted seconds later** and no human ever sees it —
  the human's own checkout is never touched;
* the undeclared files stay unmerged, so `rebase --continue` cannot proceed
  either, and **no additional rebase completes**.

Zero rebases completed, zero human effort saved, in exchange for the invariant
`resolve_index_conflicts` exists to hold: *"on a verification failure the
written files are put back to their conflicted state … so a human sees the
conflict, not a half-resolution."* Pinned by
`test_one_undeclared_sibling_still_refuses_the_whole_set_and_writes_nothing`.

## 7. Not done, and named rather than implied

* `icdev/data/claude_bootstrap/.claude/commands/start.md` is a second bootstrap
  copy of a declared file (`.claude/commands/start.md`). It has appeared in
  **no** refusal — the `Pages:` line is derived by `nav_paths.py --write` now,
  so cards no longer hand-append to it. Declare it when it is measured.
* The 29 remaining refusals need a different answer than a declaration.
* `args/pr_watcher_config.yaml` and its `icdev/data/args/` copy already
  diverged before this change; `args/` is outside `mirror_parity`'s scope and
  the resolver reads the repo-root copy through `config_path()`. Not touched
  here.

## Re-derive

```bash
python -m tools.kanban.claude_md_union_survey --json
python -m tools.kanban.union_resolver --list-rules
python -m pytest tests/kanban/test_union_derived_from.py tests/kanban/test_union_resolver.py -q
# and the refusal corpus, against the live board:
#   SELECT details FROM audit_trail WHERE action = 'pr_watcher.union_refused'
#   replayed through tools.kanban.union_resolver.match_declaration
```
