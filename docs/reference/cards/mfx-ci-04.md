# Editing CLAUDE.md without regenerating the packaged bootstrap is refused at COMMIT (mfx-ci-04)

> Moved verbatim from CLAUDE.md on 2026-09-12 (xrv-docs-02). The prose is the card's own record; only the leading `#` comment markers were stripped and the command runs fenced.

```bash
python tools/installer/prebuild_bootstrap.py                                 # the ONE repair; then `git add icdev/data/claude_bootstrap`
python tools/workflow/coherence_checker.py --check bootstrap_parity --json   # what the hook and CI both run
python tools/testing/pre_commit_check.py                                     # the hook by hand, against what is staged
```

`icdev init` copies icdev/data/claude_bootstrap/CLAUDE.md, NOT this file. PR
#2137 added 74 lines here, never regenerated the payload, and was red for
HOURS on test_payload_rule_is_green_on_the_tree_as_committed with ZERO payload
defects -- check_bootstrap_parity had found one stale packaged file (#960
again). The fix was the one command above. THE COST WAS THE FEEDBACK DELAY: a
red shard, a log dug out, a push, another full run -- and
`.githooks/pre-commit` mentioned the bootstrap ZERO times.
THE HOOK FIRES ONLY WHEN THE COMMIT STAGES A FILE THE BOOTSTRAP SCAFFOLDS: the
`SOURCES` literal of prebuild_bootstrap.py plus the `AI_PLATFORM_FILES` it
extends from, READ OUT OF THE SCRIPTS WITH `ast` -- never a second copy (a
test pins the derived list to the script's runtime SOURCES) and never an
import (the tools/ shim costs ~137 ms on EVERY commit for a list that is a
literal; the ast read is inside the noise of a bare interpreter start). In
scope it runs the SAME `coherence_checker.py --check bootstrap_parity` CI
runs and prints that check's own message plus the command. IT REGENERATES
NOTHING -- a hook that fixes what it checks gates nothing, the same reason the
test-gating hook does not widen its own allowlist -- and a test asserts it
never spawns or imports the script. `--no-verify` skips it; CI stays the
backstop. A fast path, not a second gate.
A SECOND GUARD READS THE INDEX, which the coherence check structurally cannot:
`git add CLAUDE.md` after a regeneration stages the repo file and not the
packaged copy, so the tree on disk is in parity and the COMMIT is not. Two
`git rev-parse :<path>` calls per staged must_match target; the refusal names
the `git add`. A packaged copy the index never held is the check's own `warn`.
MEASURED 2026-09-06 on this host, five runs each, whole hook (pre_commit_check.py):
  docs-only commit, no scaffolded file staged   377 ms -> 386 ms  (357-390 / 369-405: inside each other's range; two ast parses)
  CLAUDE.md + packaged copy staged, in parity   489 ms -> 821 ms  (+332: the coherence shell-out ~190 + two `git rev-parse`)
  CLAUDE.md staged, packaged copy NOT staged    424 ms -> 745 ms  REFUSED, naming `git add CLAUDE.md icdev/data/claude_bootstrap/CLAUDE.md`
RE-MEASURED 2026-09-07 with a scheduler cycle and two genesis daemons working
on the host: 425 => 435 / 549 => 888 / 473 => 806 (REFUSED). Every absolute is
~50-70 ms higher on BOTH sides and the deltas are the same (+10, +339, +333):
quote the delta, the absolute is the host's. So the common commit still pays
0 and a CLAUDE.md commit pays ~0.3 s, against the hours PR #2137 waited for a
full CI run to say the same thing.
SURVEYED BEFORE ARMING, replayed through the SHIPPED predicate against each
commit's OWN prebuild_bootstrap.py, parity re-derived from git blob ids:
  first-parent 200 on origin/main @ d9a8e75a3   54 in scope (27.0%)   0 broken   0 FIRES
  no-merges 500 on the same ref                 120 in scope (24.0%)  1 broken   1 FIRE (0.20%), caused
  PR #2137's own branch, first-parent 12         6 in scope (50.0%)   1 broken   1 FIRE (8.33%), caused
The one fire in both is 4b1978dfa -- THE INCIDENT: CLAUDE.md changed, the
payload not regenerated, parent in parity. Its repair (0acc4f7b9, by hand,
after the red CI run) is why main's first-parent walk carries 0 broken trees:
the defect lives only in the branch population, the one a pre-commit hook
sees and a survey over merges cannot. 0.20% is an eighth of the 1.63% this
file calls refusing routine work, and every one of the other 119 in-scope
branch commits had regenerated first. Read 2026-09-06 against 4f869b771,
before #2137 merged: 48/200 and 114/500 in scope, ZERO fires -- both readings
quoted; one figure off a moving ref is not a measurement. A THIRD instance
arrived while this card was in flight: sibling #2159 (rmf-rail-02, 2026-09-07)
edited CLAUDE.md, not the payload, and went red on the same two tests.
Method, every number, and the replay script in full:
  docs/audits/mfx-ci-04-precommit-bootstrap-parity-survey.md
