# CUI // SP-CTI

# kpr-watch-20 — the union rung refused 24 times on two append-shaped files nobody declared

Measured **2026-09-12** on the live PostgreSQL board (`icdev`) and against
`origin/main` at `12b116c1c`. Every figure is re-derivable with the command
named beside it; a figure read off a live board is quoted with its instant,
because both sides move.

```bash
python -m tools.kanban.artifact_pin_union_survey            # per hunk, ground truth = what landed
python -m tools.kanban.artifact_pin_union_survey --delta     # what THESE two entries buy
python -m tools.kanban.union_resolver --list-rules
python -m pytest tests/kanban/test_artifact_pin_union_declaration.py -q
```

---

## 1. The rung was reached, and it refused

Three artifact-freshness PRs (#2267 valkey, #2269 rds-mysql, #2270 opensearch)
sat CONFLICTING for 3–5 hours with zero failing checks and then escalated to a
human, who resolved all three by hand. Per PR, from `audit_trail`:

```
pr_watcher.wait            217
pr_watcher.rebase_failed     8
pr_watcher.union_refused     8   <- the finding
pr_watcher.resume            1
pr_watcher.escalate          1
```

and every refusal reads, verbatim:

```
refused: files=['args/pinned_artifacts.yaml',
                'tests/airgap/test_artifact_freshness.py']
         rules=[] verifiers=[]
         -- undeclared: args/pinned_artifacts.yaml matches no
            union_resolver.files entry
```

That is kpr-watch-14's population exactly — it measured 63 of 65 refusals on
`undeclared:` — one epic later, on two files that card did not reach.

**Three things that are NOT broken, and were not touched.** kpr-watch-13's
early escalation worked (one resume, not five: the watcher saw the injection
was undelivered and escalated rather than spending the budget). mfx-mrg-08's
`base_seg` guard worked, and is why a union here is safe to declare at all.
The escalation itself was CORRECT — a conflict no declared rule resolves is
exactly what a human should see, and §4 shows one of these still escalates.

## 2. The two files are append-shaped for the same reason CLAUDE.md is

Every artifact-freshness card appends one measurement comment block to
`args/pinned_artifacts.yaml` and one pinning test function to
`tests/airgap/test_artifact_freshness.py`. Four cards landed in one epic (ec2,
valkey, mysql, opensearch) and each collided with every sibling that landed
before it.

## 3. The survey is HUNK-level, and that is the difference from kpr-watch-14

kpr-watch-14 measured CLAUDE.md **whole-file**, which is enough when every
conflict in a file has one shape. It is not enough here. Run whole-file through
the existing `claude_md_union_survey`, `args/pinned_artifacts.yaml` reports
`1 refused, 1 mixed_difference` and reads like a file the union cannot handle.
Per hunk it reports something different and actionable.

* **Ground truth is what landed.** Three sides rebuilt from git (base =
  `merge-base` of the parents), the SHIPPED `merge_three_way` run, the result
  compared against the file the merge commit recorded.
* **Git decides the population** — `git merge-file`, because a merge git
  resolved alone is one the rung never sees.
* **Git also decides the hunks** — the conflict regions come out of
  `git merge-file --diff3`. A hunk is a thing git refused to merge, never a
  thing this survey chose to call one.
* Each region is located in the landed file and in the union output by the
  unchanged context that bounds it, the anchor grown until unique. A region not
  anchorable uniquely in **both** is `unanchorable` and is never counted as
  agreement.
* `p2_as_main` is named as the **rung's** orientation for these merges: the
  human ran `git merge main` from the card branch, so p2 is main and the rung
  rebases the card onto it. The other orientation is reported beside it.

### One verdict kpr-watch-14 did not have: `human_also_edited`

A line that landed, is absent from the union, and appears on **no side** was
written by the human *during* the resolution. The union could not have produced
it, and a rebase resolution is not supposed to invent prose. Folding that into
`union_lost_content` would file editorial work as a union defect. Provenance is
a per-hunk question — the three sides of *this* hunk — which is why a
whole-file comparison cannot ask it.

## 4. The six hunks

| file | merge | hunk | verdict | base/main/card lines |
|---|---|---|---|---|
| `args/pinned_artifacts.yaml` | `cea6f65b4` (#2270, 2nd pass) | 1 | **refused** | 9 / 44 / 24 |
| `args/pinned_artifacts.yaml` | `570103d5f` (#2267 valkey) | 1 | human_also_edited | 0 / 25 / 7 |
| `tests/airgap/test_artifact_freshness.py` | `cea6f65b4` | 1 | **exact** | 0 / 8 / 10 |
| `tests/airgap/test_artifact_freshness.py` | `8f5a5d314` (#2270 opensearch) | 1 | union_kept_more | 0 / 13 / 49 |
| `tests/airgap/test_artifact_freshness.py` | `362e8e9bb` (#2269 mysql) | 1 | union_kept_more | 0 / 11 / 49 |
| `tests/airgap/test_artifact_freshness.py` | `570103d5f` (#2267 valkey) | 1 | union_kept_more | 0 / 36 / 16 |

```
HUNKS: 6   agreeing=1   lost_content=0
   3  union_kept_more
   1  refused
   1  human_also_edited
   1  exact
```

**`union_lost_content` = 0.** The card's expectation — "the union would have
reproduced the human resolution on 6 of 6 hunks" — is **stated, not assumed,
and it is not what was measured.** Byte-for-byte agreement is 1 of 6. The other
five differ, and each difference is the human doing something the union did not:

### 4a. The refusal is correct, and stays

`cea6f65b4` is the one hunk both sides **REWROTE**: the "NOT CONVERTED HERE"
enumeration paragraph, replaced on the opensearch side by a 44-line block and
on main by a 24-line one, over a 9-line base. `keep_both_blocks` opens
`if base_seg: return None`, so it declines — and it should. Two contradictory
enumerations of which pins are behind ("five … `mysql`, `registry`,
`amazonlinux`, `valkey`, `opensearch`" against "four … `registry`,
`amazonlinux`, `valkey`, `opensearch`") is a question about what is TRUE, and a
human answers it. Declaring this pair does not claim otherwise: after the
declaration, this merge still escalates.

### 4b. `human_also_edited` is editorial work, not lost content

On `570103d5f` the union keeps both inserted comment blocks verbatim; the human
additionally rewrote three lines of main's block ("Four remain: mysql,
registry, valkey, opensearch" → "VALKEY followed, and is measured in the block
below. Three remain: mysql, registry, opensearch"). Those three lines exist on
no side. `invented=3, lost=0`.

### 4c. `union_kept_more` is the finding, and it points at the HUMAN resolutions

Three of the six hunks are `union_kept_more` with `extra=2`, and it is the same
two lines every time:

```
    assert entry["decided_by"] == AF.DECIDED_BY_CONSUMER
    assert entry["consumer"] == "floci"
```

Each hand resolution dropped one such pair from the test above the one being
inserted — the two appended test functions end with identical lines, so a
by-hand read of the conflict markers merges the wrong bodies. **Six assertion
lines were lost this way and they are missing from `origin/main` today:**

```
$ git show origin/main:tests/airgap/test_artifact_freshness.py | ...
test_the_shipped_ec2_pin_is_decided_by_floci_...          pinned=1 decided_by=0 consumer=0
test_the_shipped_mysql_pin_is_decided_by_floci_...        pinned=1 decided_by=0 consumer=0
test_the_shipped_valkey_pin_is_decided_by_floci_...       pinned=1 decided_by=0 consumer=0
test_the_shipped_opensearch_pin_is_decided_by_floci_...   pinned=1 decided_by=1 consumer=1
test_the_shipped_postgres_pin_is_decided_by_floci_...     pinned=1 decided_by=1 consumer=1
```

Three of the five `test_the_shipped_*_pin_is_decided_by_floci` tests no longer
assert `decided_by` at all — the one thing each exists to pin. They are green,
gated (`args/ci_test_files/core.d/xrv-pin-01.txt`) and weaker than their names.
**The union would have kept all six lines.** So `union_kept_more` is not a
blemish on the union here; it is the argument for it.

**Not repaired in this card, and named rather than implied.** Restoring those
six lines is a one-hunk edit, and `red_first_gate` would correctly reject it
from this PR: the assertions PASS against the merge base, because
`args/pinned_artifacts.yaml` already carries the right data — the defect is a
missing assertion, not a wrong value, and the gate has no `not_applicable` for
that shape. It is a separate card with a written reason, not a drive-by.

## 5. What the declaration buys

```bash
python -m tools.kanban.artifact_pin_union_survey --delta
```

Replayed over the whole recorded corpus through the **shipped**
`match_declaration` — never a second copy of the matcher. A refusal becomes a
candidate only when EVERY file in its set is declared, because kpr-watch-14
measured that a partial resolution buys zero (`rebase_recovery` aborts and
deletes the scratch worktree on any outcome that is not `resolved`).

| | rows |
|---|---:|
| recorded `pr_watcher.union_refused` (2026-09-12, 22:0x UTC) | 118 |
| resolvable WITHOUT these two entries | 56 |
| resolvable WITH them | 86 (72.9%) |
| **DELTA — what declaring this pair buys** | **30** |
| still refused | 32 |

Three whole tasks clear entirely, every one of their refusals being this pair
alone: `artifact-fresh-a7486018c7` (12), `artifact-fresh-da63da118f` (10),
`artifact-fresh-ee3339893b` (8). The card recorded 8 per PR = 24 on the day it
was written; the board has since gained 6 more, which is why both instants are
quoted.

The 32 that remain name **ordinary source modules** and are **not candidates** —
kpr-watch-14's rule that a source module is not append-shaped is unchanged:

| path | occurrences |
|---|---:|
| `tools/canvas_compliance/posture.py` | 28 |
| `tools/security/row_security.py` | 14 |
| `tools/document_intelligence/suggestion_store.py` | 6 |
| `tools/doc_modernization/redline_drafter.py` | 6 |
| `tools/document_intelligence/ingest_orchestrator.py` | 4 |
| `docs/audits/task-det-7e346f293f-needed-a-human-resolution.md` | 3 |
| `tools/document_intelligence/redraft.py` | 2 |
| `tests/test_dwr_redraft.py` | 1 |
| `tools/db/migrations/20260908071432_.../up.py` | 1 |

## 6. The deletion case is ASSERTED, not inherited

mfx-mrg-08 made the empty-side rung consult `base_seg`.
`test_an_empty_side_over_a_non_empty_base_still_refuses_on_these_files` proves
it **on these two files**: one side deletes a real pin entry
(`  - name: opensearch`) or a real pinning test
(`def test_the_shipped_postgres_pin…`), the other edits the same lines, and the
merge refuses in both orientations —

```
no declared rule resolves the hunk at line 344 (base 6 line(s), main 0, card 6;
tried ['keep_both_blocks']) -- the base branch DELETED these lines and the card
rewrote them
```

With the `base_seg` guard patched out the same merge resolves
(`keep_both_blocks@344`) and writes the card's version of lines the other side
deleted — the deletion silently discarded. A control test asserts that two
sides *appending* to the same files still union cleanly, so the refusal is the
deletion and not a file the rules cannot handle.

## 7. `icdev/data/args/floci_runtime_images.yaml` — considered, and NOT declared

It conflicted on #2269 and is a candidate by proximity. Two measured reasons
against, either one sufficient:

**(a) A union there ships a duplicate YAML key past the verifier.** Its one
conflict is an add/add of the SAME `note:` key under the SAME entry — both
branches re-added a note the wheel mirror had never received. Replayed through
the shipped resolver, `keep_both_blocks` keeps both and the output carries two
`note:` keys in one mapping; `yaml.safe_load` accepts a duplicate key silently
(last wins), so the rung's YAML verifier **passes** and a declaration of record
ships carrying two contradictory notes. That is how the wrong rule shipped a
broken file twice on 2026-09-03, in a form no verifier catches. Pinned by
`test_unioning_two_inserts_of_the_same_yaml_key_ships_a_duplicate_key`.

**(b) It is not append-shaped; it is DERIVED, and it conflicted because it was
STALE.** `tools/installer/sync_package_tree.py` produces it from
`args/floci_runtime_images.yaml` — which merged **clean in 4 of 4** of these
merges. `ffce8d75a` records the drift: *"mirror_parity only ever compares
tools/ ↔ icdev/tools/, so this file drifts with nothing watching."* Both
branches independently re-added the same missing note; once the mirror is in
sync its conflicts are its source's conflicts.

**`derived_from` is not the answer either.** kpr-watch-14 records the one shape
the derivation rung refuses to repair: *a declared copy of a resolved source
that is NOT in the conflict set*. That is exactly this case — the source merged
clean and was never unmerged, so there are no resolved bytes to copy.

What regenerates it instead: `python tools/installer/sync_package_tree.py`. The
real gap it exposes — nothing watches `args/` → `icdev/data/args/` drift, so a
wheel can ship a declaration of record that disagrees with the repository's —
is **its own card**, not this one. `args/pr_watcher_config.yaml` and its
`icdev/data/args/` copy are in the same position and had already diverged
before kpr-watch-14; the resolver reads the repo-root copy through
`config_path()`, and neither is touched here.

## 8. Not done, and named rather than implied

* The six dropped `decided_by` / `consumer` assertions on `origin/main` (§4c).
  A separate card — `red_first_gate` would correctly reject the repair from
  this PR.
* Nothing watches `args/` → `icdev/data/args/` mirror drift (§7).
* The historical replay is **not** a gated assertion. `test-shard` checks out
  at depth 1, so a test replaying merge history would report UNMEASURABLE on
  every CI run and pass — a perfect score over an empty denominator
  (rem-hyg-13). What IS gated is the declaration and the shipped resolver's
  behaviour on the real bytes of these two files.

## Re-derive

```bash
python -m tools.kanban.artifact_pin_union_survey --json
python -m tools.kanban.artifact_pin_union_survey --delta
python -m tools.kanban.union_resolver --list-rules
python -m pytest tests/kanban/test_artifact_pin_union_declaration.py -q
# and the refusal corpus, against the live board:
#   SELECT details FROM audit_trail WHERE action = 'pr_watcher.union_refused'
```
