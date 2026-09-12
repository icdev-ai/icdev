# A REPARK id extends a task id at the FRONT -- the matcher no longer binds it (mfx-own-05)

> Moved verbatim from CLAUDE.md on 2026-09-12 (xrv-docs-02). The prose is the card's own record; only the leading `#` comment markers were stripped and the command runs fenced.

```bash
python -m tools.kanban.branch_match_survey --env-file C:/AI/ICDev/.env       # legacy vs shipped rule, every drop NAMED
python -m tools.kanban.branch_match_survey --env-file C:/AI/ICDev/.env --include-terminal --json
```

`_branches_for_task` (the done-gate's "does a branch for this task hold
unmerged work", stranded_audit, artifact_evidence, both orphan_requeue
proofs) matched `(^|[/_-])<id>` -- a child's SUFFIX extension was the only
direction its docstring anticipated, and the alternative also admitted a
PREFIX extension, which is exactly what a repark card wears
(`kph-repark-<id>`, then `kph-repark-kph-repark-<id>`). MEASURED 2026-09-06:
`mfx-ci-04` sat in `validating` 12:49 -> ~18:00 refused every cycle with
`branch_not_ancestor:kanban/kph-repark-kph-repark-mfx-ci-04` -- a THIRD
card's branch, which built PR #2146; `kph-repark-mfx-ci-04` was refused on
the same foreign branch. Now the id must START a path segment:
`(^|/)<id>([/_.-]|$)`. Fail-open on git error is UNCHANGED.
SURVEYED on the live listing (6,286 refs) before narrowing, and the narrowed
set is a SUBSET by construction (added: 0 both runs):
  11 non-terminal ids   21 -> 13 pairs, 8 dropped, ALL repark, every task
                        keeps >= 1 ref (10 -> 10)
  3,966 ids (terminal)  4,260 -> 4,229, 31 dropped: 13 repark + 18 OTHER,
                        all 18 hand-named `icdev-<id>` / `feature-<id>-...`
                        branches of `done` tasks from July/August, named in
                        docs/audits/mfx-own-05-branch-matcher-prefix-extension-survey.md
That `other` shape (a `-`-joined prefix that is not a repark) is the one
thing the narrowing gives up: it is indistinguishable from the repark shape
structurally, zero non-terminal tasks carry one today, and the worker
convention is `kanban/<id>`. Re-run the survey before widening it back.
The old rule lives ONLY in the survey (`legacy_matches`), labelled history;
"today" is asked of the shipped predicate, never a copy.
NOT built here, and named: the refusal itself was `branch_not_ancestor` --
`git cherry` 0 unmerged while `merge-base --is-ancestor` fails, the SQUASH
signature. A merged PR is knowable from the forge, but that is a forge
round-trip inside a proof that is pure git today; its own card if the
matcher fix alone does not clear the population.
