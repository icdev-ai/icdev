# An incident becomes a standing claim with two independent derivations (autonomy-lrn-01)

**Card:** AUTONOMY / LRN · **Task:** `autonomy-lrn-01` · **Date:** 2026-08-21

## The defect

Every defect this week was fixed, tested and documented — and the test was a
fixture-based unit test pinning ONE function. When the same defect exists at a
second site the test still passes, the card is marked done, and the bug keeps
firing under a green test.

The Studio approval park proved it. `hgx-park-01` made
`workflow_runner._park_for_approval` commit the gate row and the run row in one
transaction, and pinned that with structural tests that read *that function's*
source. `mcp_executor.open_approval_gate` had the identical two-commit defect,
kept failing `assert 'running' == 'awaiting_approval'` on the Windows runner,
and was read as flake until `rem-hyg-19` — weeks later.

The claim registry (`rem-hyg-17`) is the one place a fix can gain a LIVE guard
that does not have that blind spot, because a claim is asserted against the
PRIMARY DATA, not against a function. But the conversion from incident to
claim was manual, and mostly did not happen: 4 claims, against a week that
produced dozens of confirmed defects.

## What shipped

### 1. Every `Claim` cites its `Incident`

`tools/awareness/claim_verifier.py` gains an `Incident` dataclass
(`task_ids`, `observed_on`, `fixed_by`) and `Claim.incident`. All five
registered claims cite the card(s) they were learned from;
`tests/awareness/test_incident_claims.py` refuses a claim that cites nothing,
a malformed id, or a non-ISO date.

### 2. A citation is a VERIFIED FACT, or it is not verified

`tools/awareness/incident_claims.py::verify_incident` checks every cited id is
`done` on the board **and** landed on the default branch — through
`tools.kanban.landed_check`, the one implementation of "is it on main". The
verdict is `True | False | None`:

| situation | verified |
|---|---|
| every id done and on main | `True` |
| an id still `pr_opened` (the fix has not happened yet) | `False` |
| done on the board but nothing on main | `False` |
| board unreadable, or git could not answer (shallow clone) | `None` — never folded into `True` |

### 3. The measurement: which fixed incidents have a claim?

`coverage_report` NAMES the window's done `fix` cards with and without a
standing claim. Names, not a count — a count can be held constant while the set
churns.

```
python tools/awareness/claim_verifier.py --incidents
python tools/awareness/claim_verifier.py --incidents --window-days 30 --json
python tools/awareness/claim_verifier.py --list        # each claim <- its card(s)
```

Measured on the live board 2026-08-21:

```
Incident -> claim coverage — last 7 day(s)  [measured]
  claims 5 citing 6 distinct incident(s)
  fixed 58 · guarded 5 · UNGUARDED 53
```

All six cited incidents verify. A board with no done fix in the window reports
`unmeasurable` with `None` counts — never "0 unguarded".

**Distinct ids, never rows.** An incident cited by two claims is ONE incident;
one claim citing two sites (`hgx-park-01` + `rem-hyg-19`) guards TWO. This is
the same rule `independent_observations` applies to evidence: repetition is not
corroboration.

### 4. The learned claim: `approval_park_is_whole`

| side | derivation |
|---|---|
| reported | `workflow_runner.get_pending_approvals()` — what the HITL surface lists as awaiting a decision |
| derived | raw SQL from the RUN table: pending gates under parked runs, plus a `run-without-gate:<run_id>` sentinel for every parked run with no pending gate |
| agree | set equality — a gate the surface shows under an unparked run is the first half of a two-commit park; a sentinel is the other order |

Whichever site parks, the half-commit is the same finding. The two callables
share no code (asserted by `__code__` identity and by source inspection).

On this board no gate has ever been parked (121 step rows, none awaiting), so
the claim reads `unmeasurable` today — never `agrees`.

## What it deliberately does not do

- **Seeds nothing automatically.** A claim needs two derivations that share no
  code; that is authored by whoever fixed the defect, with the incident cited.
  A generated claim would be the surface's own computation trusted twice.
- **No `--gate`.** Report only (kpr-fix-03: a survey with a gate earns itself a
  `|| true`).
- **No new detector.** The path reuses `claim_verifier`, `landed_check` and the
  board; the only new CLI surface is a flag on the existing verifier.

## Files

- `tools/awareness/claim_verifier.py` — `Incident`, `Claim.incident`, `--incidents`, `--list` shows provenance
- `tools/awareness/incident_claims.py` — the path and the measurement (library)
- `tools/awareness/claims.py` — five claims cite their incidents; `approval_park_is_whole` added
- `tests/awareness/test_incident_claims.py` — gated via `args/ci_test_files/core.d/autonomy-lrn-01.txt`
- Mirrored to `icdev/tools/awareness/`
