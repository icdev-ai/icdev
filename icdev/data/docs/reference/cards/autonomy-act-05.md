# ONE statement of which pr_watcher actions are recovery evidence (autonomy-act-05)

> Moved verbatim from CLAUDE.md on 2026-09-12 (xrv-docs-02). The prose is the card's own record; only the leading `#` comment markers were stripped and the command runs fenced.

```bash
python -m tools.kanban.recovery_action_survey                 # replay BOTH row sets over history
python -m tools.kanban.recovery_action_survey --json --detail
python -m tools.kanban.recovery_action_survey --at 2026-09-09T02:35:00Z   # ONE instant
python -m pytest tests/kanban/test_recovery_action_vocabulary.py -q
```

THREE readers answer "did pr_watcher recover this PR" from audit_trail and they
did NOT fetch the same rows. `recovery_summary.AUDIT_ACTIONS` (8 names) was
EXPORTED so "the SQL in app.py and the classifier here cannot drift apart
again" -- its own comment -- and exactly ONE of the three took it. The Home
panel read the constant; `detector_findings.recovery_rows` and
`claims._recovery_rows`, THE TWO THAT FILE AND CLEAR CARDS, each kept the
pre-rmf-disc-01 FOUR-value literal. So rmf-disc-01's widening reached the
surface a human READS and never reached the readers that ACT.
WIDENING IS NOT PURELY ADDITIVE, which is why it was surveyed before arming --
three effects in three directions:
  ADDS ATTEMPTS   rebase_failed / ci_retrigger raise `attempts` and move the
                  newest counted attempt FORWARD, pushing earliest_clear_at LATER.
  SUBTRACTS       resume_refund / rebase_refund DECREMENT it. The narrow set
                  fetched neither, so it counted attempts the watcher's own
                  accounting had already withdrawn.
  ADDS SUBJECTS   summarize_recovery DROPS a task with zero attempts, so a task
                  attempted only by a kind the reader does not FETCH draws NO
                  CARD AT ALL.
MEASURED on the live PG board 2026-09-09, 56,910 rows / 157 replay instants at
the reflex's own 6h cadence with its 24h window (detector_runs is a ROLLUP, not
a run log, so there is no recorded per-run instant to anchor on):
  19 subjects ADDED, 0 REMOVED
  102 card titles changed -- 90 understated, 12 OVERSTATED (the refunds)
  57 earliest_clear_at values changed, ALL LATER, 0 earlier; median 1.44h, max 19.71h
The starkest case: `sbx-fld-01` at 2026-08-09 carried 177 `escalate` rows and 2
`rebase_failed` attempts -- the narrow reader saw ZERO attempts and filed
nothing while the panel showed it. `sbx-cov-02` identical (179 / 2).
VERDICT: ONE constant, and the SHIPPED set is the one. 0 removals means
widening can silence no existing finding; earliest_clear_at moves only LATER,
so the #2057 hold can only file FEWER spurious -r2 cards, never more. Narrowing
the panel instead was REJECTED (it restores the blind spot on the one surface
that sees it). NO threshold and NO window value was changed -- window_hours 24,
max_cards_per_run 6 and the rule that `escalate` outranks a later `merge` are
all untouched.
A FOURTH QUESTION KEEPS ITS OWN NAME. `watcher_outcome_rows` asks which of TWO
rows is NEWER (autonomy-act-04's ordering) and an attempt row is neither an
escalation nor a merge, so it reads a DISTINCT `OUTCOME_ACTIONS` carrying that
reason -- the card's second acceptable answer. sibling_overlap.CONFLICT_ACTIONS
and protected_conflict_survey.LADDER_ACTIONS already had theirs.
THE CLAIM'S DERIVED SIDE MAY SHARE THE VOCABULARY, NEVER THE REDUCTION.
claim_verifier's rule is about the COLLAPSE, and `_derived_recoveries` still
re-implements that itself (AST-pinned: no summarize_recovery call). Which action
IS an attempt is a fact about pr_watcher's WRITER, and a derivation over a
DIFFERENT population is not an independent check of the same claim -- it is a
different claim, and it reported `disagrees` for a task the watcher only
rebase_failed.
THE TEST READS THE SOURCE, because a behavioural one structurally cannot: the
narrow set is a STRICT SUBSET, so every assertion over a fixture carrying only
`resume` rows passes for both. Three discriminations, each measured:
SUBSTRING not equality (the incident was ONE string, `"WHERE action IN
('pr_watcher.rebase','pr_watcher.resume',"`, equal to no vocabulary member --
the first version of the test used equality and REPORTED THE INCIDENT CLEAN,
caught by the red-first replay against main); PROSE IS NOT A LITERAL (the
model_id_gate precedent -- detector_findings carries four reason strings naming
these actions); and A NAMED MODULE-LEVEL CONSTANT IS NOT ONE EITHER. Surveyed
over both trees: ZERO modules fire beyond the declarer, the writer, the three
readers and the three declared exceptions. Positive controls ship with it -- a
scanner that stopped scanning also reports clean.
Survey: docs/audits/autonomy-act-05-recovery-row-set-survey.md
