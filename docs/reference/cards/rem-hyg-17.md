# Does the surface's CLAIM survive an INDEPENDENT re-derivation? (rem-hyg-17)

> Moved verbatim from CLAUDE.md on 2026-09-12 (xrv-docs-02). The prose is the card's own record; only the leading `#` comment markers were stripped and the command runs fenced.

```bash
python tools/awareness/claim_verifier.py --json
python tools/awareness/claim_verifier.py --list
python tools/awareness/claim_verifier.py --claim posture_score_needs_evidence
```

THE DEFECT IT EXISTS FOR. On 2026-08-20 eight defects were found by hand in one
session and they were ONE defect wearing eight faces: a surface asserting
something whose supporting evidence nothing ever re-derived. Posture scored
100.0 for canvases never assessed; `unlogged` was
`backend.startswith("postgres")`, a constant wearing the name of a
measurement; the recovery panel called 331 retry ATTEMPTS "auto-recovered"
when 46 recovered; the idle log asserted "the usual cause is an open PR" for
an hour with ZERO open PRs. THE DATA WAS RARELY WRONG -- THE REDUCTION WAS.
WHY WATCHING OUTPUTS CANNOT FIND THESE, and why nothing here learns from the
system's own reported history: a bad reduction produces a beautifully stable
series. `odc_gap_scores` holds 91 rows spanning a month carrying ONE distinct
value for ONE subject -- a single stuck writer that any row-counting
confidence model rates as extremely well corroborated. Five
`pr_watcher.resume` rows for one task are ONE failure, not five recoveries.
    REPETITION IS NOT CORROBORATION.
`independent_observations` therefore counts distinct (subject, value) pairs,
NEVER rows.
Each Claim carries TWO callables that MUST NOT SHARE CODE -- what the system
REPORTS and an INDEPENDENT derivation of the same fact from primary data. If
the verifier calls what the surface calls it proves the function is
deterministic, which was never in question; every one of the eight defects
survived because one computation was trusted twice.
THREE VERDICTS: agrees | disagrees | unmeasurable. TWO EMPTY SIDES ARE
UNMEASURABLE, never agreement -- this module nearly shipped reporting `agrees`
for `[] == []` against an empty database, committing the exact defect it was
built to catch. A scalar `False`/`0` is a real answer and is NOT empty.
CORRECTIVE ACTION IS TIERED BY REVERSIBILITY: report | restore (a mechanical,
individually verifiable act -- reap a provably dead lease) | propose (seed a
card; a human decides). There is deliberately NO tier that edits a claim,
threshold or assertion so the surface agrees -- a verifier that may rewrite
what it verifies can always make itself green, which is the same move as a
test quietly weakened to match code that broke.
Claims are seeded ONLY from defects that actually happened, so "it reports
clean" can never be confused with "it does nothing" -- and today's fixes gain
a LIVE regression guard their fixture-based unit tests cannot provide.
Report only, no --gate (kpr-fix-03). Registry: tools/awareness/claims.py
IT RUNS ON ITS OWN, and its own inertness goes red (autonomy-act-01). It was
consumed by NOBODY for its first day -- no reflex, scheduler or daemon imported
it. Now the `claim_verifier_reflex` genesis reflex (6h, green; registered in
BOTH daemon.REFLEX_NAMES and args/genesis_config.yaml) acts by the claim's own
tier -- report states, propose seeds ONE card carrying both derivations,
restore is DEFERRED to autonomy-act-03 and named, never dropped -- and a cycle
measuring zero claims reports `unmeasurable`, never ok.
```bash
python tools/genesis/daemon.py --reflex claim_verifier_reflex --json     # one cycle, through the daemon
python tools/awareness/capability_consumption.py --class verified_claim --json
```

Consumption is a MEASURED verdict (agrees|disagrees) on a daemon-dispatched
run, read from the per-claim `verdicts` map on genesis_audit.details -- no new
table. `unmeasurable` is reported under extra.unmeasurable_events and never
counted; a human running the CLI records nothing, on purpose. Budget 0 in
args/liveness_gate.yaml: a verifier nobody runs FAILS capability_liveness.
