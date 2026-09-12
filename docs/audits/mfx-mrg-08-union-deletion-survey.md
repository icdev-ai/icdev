# The empty-side rung discards a DELETION — survey (mfx-mrg-08)

> CUI // SP-CTI
> Measured 2026-09-12 against the live PostgreSQL board (`icdev`) and
> `origin/main` at `ace4d4d9b`. Re-derive with
> `python -m tools.kanban.union_deletion_survey --json`.

## The defect

`tools/kanban/union_resolver.py::_resolve_cluster` opened with two lines that
ran before any declared rule and are deliberately not switchable off
(`other_side_when_empty` is absent from `DECLARABLE_RULES` — "it applies to
every declared file and cannot be switched off"):

```python
if not main_seg and card_seg:
    return list(card_seg), RULE_OTHER_SIDE_WHEN_EMPTY
if not card_seg and main_seg:
    return list(main_seg), RULE_OTHER_SIDE_WHEN_EMPTY
```

Neither looks at `base_seg`. **An empty side is read as "this branch had
nothing to say here", and that is also exactly what "this branch DELETED these
lines" looks like.** `_rule_keep_both_blocks` and `_rule_table_rows` both open
`if base_seg: return None` — "both REWROTE existing lines, not two appends" —
and the universal rung, which runs FIRST and refuses nothing, did not.

## Two populations, never merged

They answer different questions and only one of them has ground truth.

| | population | ground truth | answers |
|---|---|---|---|
| `--recorded` | every `pr_watcher.union_resolved` / `union_refused` audit row, replayed against the branch head THAT ROW ACTUALLY HAD (the kpr-watch-15 remote-tracking-reflog reconstruction, never today's head) | none — a refused row produced no file | how many hunks of each shape the resolver has met |
| `--landed` | every conflicting merge commit on the default branch touching a DECLARED file (the kpr-watch-14 population) | **what the human landed** | was restoring the lines ever right? |

Four hunk shapes, and `decided_by` says which branch of `merge_three_way`
disposed of each, because three equality short-circuits run BEFORE
`_resolve_cluster` and counting shapes that never reach the rung would
overstate the exposure.

---

## 1. The recorded rows

Read at **2026-09-12T13:14:59Z** (74 rows: 73 `union_refused`, **1**
`union_resolved`). The board is live and moved under this survey: the same
query returned 72 rows at 12:41Z, 73 at 12:52Z and 74 at 12:59Z. One figure
off a moving board is not a measurement, so every reading here is quoted with
its instant.

```
74 rows; 64 replayed, 10 UNMEASURABLE {no_reflog: 10}
604 hunks over the declared files:
    329  both_sides_present          (50 reach _resolve_cluster)
    253  base_empty_side_empty       ( 0 reach _resolve_cluster)
     14  base_nonempty_main_empty    ( 2 reach _resolve_cluster)
      8  base_nonempty_card_empty    ( 0 reach _resolve_cluster)
```

(The 12:52Z reading was 73 rows / 590 hunks / 321-249-13-7 with 48-0-1-0
reaching — the same shape, four `xrv-cost-05` retries later.)

`UNMEASURABLE` is its own bucket and is never folded into either other: a
branch whose remote ref was deleted after its PR merged has no reflog and
cannot be asked. Ten rows (all `mfx-own-04`) are in that state.

**THE INTENDED CASE NEVER REACHES THE RUNG, STRUCTURALLY.** All 253
`base_empty_side_empty` hunks were disposed of earlier, and they always will be:
`_resolve_cluster` is called only when `main_seg != base_seg`, so `main_seg`
empty implies `base_seg` non-empty, and likewise for `card_seg`. Every firing
of `other_side_when_empty` inside `_resolve_cluster` is therefore a
deletion-discard by construction. The narrowing keeps the rung (the pure-
insertion behaviour is unchanged and asserted in both directions) and makes its
one reachable path refuse.

### The replay UNDERSTATES, and the resolver's own notes are authoritative

The replay reconstructs a whole-file three-way from
`(merge-base(head, base_sha), base_sha, head)`. The resolver reads **index
stages from an in-flight rebase**, which replays commit by commit, so stage `:1`
is a per-commit parent tree and not the branch merge base. The replay therefore
reproduces only 2 of the 3 firings the audit rows themselves record.

So the authoritative count is the resolver's OWN `rules=[...]` note, which
records every rule that decided a cluster. Across all 74 rows
(read 2026-09-12T13:14:59Z), `other_side_when_empty` appears **three times**:

| when (UTC) | action | task | note |
|---|---|---|---|
| 2026-09-12 12:13:22 | **`union_resolved`** | `xrv-docs-02` | `CLAUDE.md:other_side_when_empty@85` |
| 2026-09-12 12:51:59 | `union_refused` | `xrv-cost-05` | `CLAUDE.md:other_side_when_empty@86` |
| 2026-09-12 12:54:53 | `union_refused` | `xrv-cost-05` | `CLAUDE.md:other_side_when_empty@86` |

**Three firings in the resolver's whole recorded history, and three of three
were deletion-discards. Zero were the intended case.**

### The one that landed

`xrv-docs-02` is the branch whose entire change is the 7,968-line CLAUDE.md
trim. Its remote-tracking reflog, with CLAUDE.md's line count at each push:

```
33bb0b36c  2026-09-12 07:25:13 -0400   473 lines   the trim, as the card wrote it
693ee8964  2026-09-12 08:13:18 -0400  4458 lines   <- the union resolver's rebase
871e08782  2026-09-12 08:31:42 -0400   476 lines   <- a human, by hand
```

`git diff --stat 33bb0b36c 693ee8964 -- CLAUDE.md` is **`3985 insertions(+)`**.
The `union_resolved` row at 12:13:22Z is that push: the rung took main's side
over the card's empty one, restored 3,985 lines the card had deliberately
removed, force-pushed the branch and reported success. Sixteen minutes later
`871e08782` — *"fix(docs): regenerate the CLAUDE.md card trim, and make it
re-appliable"* — removed them again, by hand.

The other two firings are the mirror image: rebasing `xrv-cost-05` onto the
merged trim, MAIN is the empty side (`base 3905 lines, main 0, card 3965`) and
the rung would have discarded main's trim instead. Nothing was pushed there
only because a *different* file in the same run refused
(`tests/e2e/cache_savings_spend_panel.spec.ts`) and the whole rebase aborted.

---

## 2. What actually landed — was restoring ever right?

Whole history, `origin/main` @ `ace4d4d9b`, **not truncated**:

```
1686 merge(s) examined, 3172 touch a declared file, 123 where BOTH sides changed it
empty-side-over-non-empty-base hunks reaching the rung: 8
  restoring was RIGHT (every restored line is in what landed): 2
  restoring was WRONG (a restored line is NOT in what landed): 6
```

Each hunk is counted once per parent ORIENTATION (a merge commit does not
record which side was "main"), so the 8 are **4 distinct hunks**: 3 wrong, 1
right.

| merge | file | base | main | card | restored | in what landed | right? |
|---|---|---|---|---|---|---|---|
| `bcff07b1d` | `tools/network/blueprint.py` | 16 | 18 | 0 | 17 | 1 | **no** |
| `bcff07b1d` | `tools/network/blueprint.py` | 3756 | 3758 | 0 | 3432 | 8 | **no** |
| `bcff07b1d` | `tools/network/blueprint.py` | 3290 | 3289 | 0 | 2974 | 9 | **no** |
| `5059969cc` | `tools/dashboard/app.py` | 1 | 34 | 0 | 31 | 31 | yes |

The three wrong ones are one merge of a blueprint that was largely rewritten:
the rung would have put back 3,432 lines of which **8** survive into the file
that merged. The one right one is an ordinary block expansion (one base line
replaced by 34).

`restore_was_right` is a ONE-SIDED test — "is every restored non-blank line
somewhere in the landed file". A `False` is conclusive (a restored line that is
not in what landed was wrongly restored); a `True` means *consistent with*, not
*equal to*, the human's resolution. It is reported as `None`, never `0`, when
nothing was measured.

---

## 3. Fire rate

What the narrowing REFUSES that the old rung resolved. Three denominators,
because they are three different questions and merging them would hide both
the cost and the benefit:

| denominator | fires | rate |
|---|---|---|
| recorded `union_resolved` rows (all time) | 1 of 1 | **100%** — and that one was wrong |
| all recorded union rows (74) whose verdict CHANGES | 1 of 74 | **1.35%** |
| conflicting merges of declared files (123) | 1 extra escalation | **0.81%** |
| all merges on `origin/main` (1,686) | 1 extra escalation | **0.06%** |

The second row is 1 and not 3 because the other two firings were inside runs
that already refused on a different file — the narrowing changes nothing there.

**The cost is one case and it is named**: `5059969cc` / `tools/dashboard/app.py`
is the single historical hunk the old rung resolved correctly and the narrowed
one escalates to a human. Against it stand three provably wrong restorations in
the landed population and one that actually shipped to a branch.

Every rate is below the **1.63%** this repo already calls grounds for refusing
routine work, and the refusals it adds are not routine work: **zero** of the
three recorded firings were the intended case.

---

## Limitations, stated

* The recorded replay is a WHOLE-FILE three-way and the resolver reads REBASE
  INDEX STAGES. It reproduces 2 of the 3 recorded firings, so it is a LOWER
  bound on the shape counts; the audit rows' own `rules=[...]` notes are what
  the fire-rate claim rests on.
* Ten recorded rows are `unmeasurable_no_reflog` — the branch ref was deleted
  after merge. That is not "no deletion was found there".
* `restore_was_right` is one-sided, as above.
* The landed population uses `--diff-merges=first-parent` to name the files a
  merge brought in. `--name-only` prints NOTHING for a merge commit by default,
  which reads as "this merge touched no declared file" for all 1,686 of them —
  the first version of this survey reported exactly that, a clean bill of
  health produced by a flag.

## Repair

`_resolve_cluster` asks `base_seg` before the rung, exactly as
`keep_both_blocks` and `table_rows` do, and the hunk falls through to the
declared rules — all of which decline it — so the conflict reaches a human.
The refusal names which side deleted. The four declared rule functions and the
three-way engine are byte-identical, pinned by source hash in
`tests/kanban/test_union_deletion_rung.py`.

`--list-rules` still lists the same five rules and the same declarations; the
`other_side_when_empty` DESCRIPTION changed, because a rung described as
"universal" that is no longer universal is the drift this repo refuses.

### One function changed, re-derived rather than asserted

```
$ python - <<'PY'
import ast, subprocess
old = subprocess.run(["git","show","origin/main:tools/kanban/union_resolver.py"],
                     capture_output=True, text=True, encoding="utf-8").stdout
new = open("tools/kanban/union_resolver.py", encoding="utf-8").read()
def segs(src):
    tree = ast.parse(src)
    return {n.name: ast.get_source_segment(src, n)
            for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef))}
a, b = segs(old), segs(new)
assert set(a) == set(b)
print("top-level defs/classes:", len(a),
      "| CHANGED:", sorted(k for k in a if a[k] != b[k]))
PY
top-level defs/classes: 43 | CHANGED: ['_resolve_cluster']
```

Nothing was added or removed from the module's top level, and of its 43
definitions exactly one differs. The only other bytes in the diff are two
PROSE corrections — the module docstring's "an empty side always resolves to
the other" bullet and the `RULE_DESCRIPTIONS` value — both of which the
narrowing makes false, and leaving a false description is the drift this repo
refuses. The rule functions themselves are pinned by hash in
`tests/kanban/test_union_deletion_rung.py`, which is where the claim is
checked on every CI run; this re-derivation is the whole-file version of the
same claim and is recorded here because the gated shards cannot run it.
