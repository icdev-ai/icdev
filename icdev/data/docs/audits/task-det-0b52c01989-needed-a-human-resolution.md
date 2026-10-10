<!-- CUI // SP-CTI -->
# task-det-0b52c01989 — `needed_a_human` finding for kpr-stale-05, resolved

- **Task:** task-det-0b52c01989 (filed by `detector_findings_reflex`, detector
  `recovery` / rem-hyg-16, finding `0b52c01989ef675d`, fingerprint
  `needed_a_human`)
- **Subject:** kpr-stale-05 — PR #2049, resumed 5x by pr_watcher, escalated
- **Date measured:** 2026-09-05, against the live PG board and the forge

## Verdict

Nothing is left to land. Both acceptance criteria were already true on the live
board when this card was dispatched, and the **systemic** cause was fixed and
pinned nine hours before that.

| Criterion | Measured 2026-09-05 |
|---|---|
| the derivation no longer reports `kpr-stale-05` | `[]` (89 `pr_watcher.*` rows in the 24h window, none for this subject) |
| `detector_findings.0b52c01989ef675d` reads `cleared` | `status=cleared`, `cleared_at=2026-09-05 06:03:51` |

## The actual cause

**#2049 had no defect in it. Its work was already on main, inside a sibling.**

`kanban/kpr-stale-06` was branched from `kanban/kpr-stale-05` and merged
`origin/kanban/kpr-stale-05` into itself twice. #2053's own body says so —
*"Builds on #2049 (kpr-stale-05); this PR contains that branch. Once #2049
merges, the diff here collapses."* The author expected #2049 to land **first**.
It did not: #2053 merged at 00:28:18Z carrying both of #2049's commits, and from
that instant #2049 was a PR whose entire diff was upstream. Re-derived from the
forge, not from a report:

| | |
|---|---|
| #2049 commits | `9e26d2b79353`, `52b4b2e8dbcf` |
| #2053 commits | `9e26d2b79353`, `52b4b2e8dbcf`, + 4 of its own |
| family | `named_branch` — #2053's body names `#2049` and the branch |

`tools/kanban/orphan_requeue.py` is on `origin/main` carrying kpr-stale-05's own
docstring. The deliverable landed; only the PR was redundant.

### The resume ladder, and where it stopped being fixable

| When (UTC) | Event |
|---|---|
| 09-03 23:32:29 | `resume` 1 — `ci_failed` |
| 09-03 23:45:08 | `resume` 2 — `ci_failed` |
| **09-04 00:28:18** | **#2053 merges, absorbing both of #2049's commits** |
| 09-04 00:30:40 | `resume` 3 — `merge_conflict` |
| 09-04 00:41:18 | `resume` 4 — `merge_conflict` |
| 09-04 00:52:08 | `resume` 5 — `merge_conflict` — the card's evidence row |
| 09-04 00:53:12 | `escalate` — "resume cap reached (5/5) — manual intervention required" |
| 09-04 01:05:23 | human forces `done`: *"work landed on main via kpr-stale-06's squash-merge #2053 … #2049 closed as superseded"* |

**Three of the five resumes — every one classified `merge_conflict` — were spent
after the branch had become redundant.** The classification flip at 00:30 is the
boundary: resumes 1–2 chased a real CI failure, resumes 3–5 asked an LLM to
repair a branch whose only remaining content was a conflict against its own
merged copy. That is the class the detector exists to separate from `recovered`,
and no resume budget could have reached it.

The forced `done` is correctly **not** counted as a recovery — it follows the
escalation, so `summarize_recovery` keeps the outcome `needed_a_human`. That is
the rem-hyg-16 rule working, not a defect.

## The systemic fix already landed — and names this incident

`mfx-mrg-02` (#2102, merged to main 2026-09-05 08:05:36Z, commit `39986a0bf`)
arms exactly this predicate: a PR is `superseded` when a **merged** PR in the
same task family carries our head sha and every commit we can see. Its survey
lists **#2049 by name** as one of two recall cases
(`docs/audits/mfx-mrg-02-superseded-pr-survey.md`, population 2), and the
incident is pinned as a fixture — `tests/ci/test_pr_superseded.py` builds
#2049's two commit shas absorbed by #2053 and asserts the close comment cites
`#2053`.

Had that check been armed on 2026-09-04, #2049 would have been closed with a
citation at the first poll after 00:28:18Z, and resumes 3–5 and the escalation
would never have been spent. Nothing further is required of this card, and this
record deliberately proposes **no** change to the detector, its threshold or its
window — an actuator never edits what it verifies.

## One observation on the card itself, recorded not acted on

The card's title and evidence quote `attempts: 5`; the `detector_findings` row
now reads `attempts: 3`, `seen_count: 3`. Both are correct. The count is
re-derived on every 6h cycle over a **rolling 24h window**, so it falls as the
earliest resume rows age out. The fingerprint is `needed_a_human` — the
**outcome**, never the count — which is why the falling count re-upserted one
row instead of filing three cards. Worth knowing when reading an aged card: the
attempt count on a `needed_a_human` card is a lower bound by the time you read
it.

## Re-derive

```
python - <<'EOF'
from tools.awareness.claims import _recovery_rows
from tools.dashboard.recovery_summary import summarize_recovery
print([e for e in summarize_recovery(_recovery_rows(), limit=10_000) if e['task_id'] == 'kpr-stale-05'])
EOF
python -m tools.kanban.detector_findings --list --status cleared --detector recovery
gh pr view 2049 --json state,commits --jq '.state, [.commits[].oid]'
gh pr view 2053 --json mergedAt,commits --jq '.mergedAt, [.commits[].oid]'
git cat-file -e origin/main:tools/kanban/orphan_requeue.py && echo deliverable-on-main
```
