# An INCIDENT becomes a STANDING CLAIM, and the claim cites it (autonomy-lrn-01)

> Moved verbatim from CLAUDE.md on 2026-09-12 (xrv-docs-02). The prose is the card's own record; only the leading `#` comment markers were stripped and the command runs fenced.

```bash
python tools/awareness/claim_verifier.py --incidents              # which fixed incidents have NO claim
python tools/awareness/claim_verifier.py --incidents --window-days 30 --json
python tools/awareness/claim_verifier.py --list                   # each claim <- the card(s) it was learned from
```

WHY. Every defect gets a fixture-based unit test pinning ONE function, and when the
same defect exists at a second site that test still passes. hgx-park-01 made
workflow_runner._park_for_approval atomic and its structural tests read THAT
function's source; mcp_executor.open_approval_gate kept the identical two-commit
park for weeks, read as Windows flake, until rem-hyg-19. A claim over the DATA
(`approval_park_is_whole`: a gate awaiting a decision under a run that is not
parked) is the same finding whichever site wrote it. Measured 2026-08-21: 58
done `fix` cards in 7 days, 5 guarded, 53 UNGUARDED — the conversion was
manual and mostly did not happen.
Every Claim carries an `Incident` (task ids + the date OBSERVED). It is a
VERIFIED FACT only when every cited card is `done` on the board AND landed on
the default branch (tools/kanban/landed_check, the one "is it on main"); a card
in `pr_opened` is a fix that has not happened yet. True | False | None — an
unreadable board or a shallow clone is None, never verified. Distinct ids, never
rows: an incident cited by two claims is ONE incident; one claim citing two
sites guards TWO. A board with no done fix in the window is UNMEASURABLE with
None counts, never "0 unguarded". Nothing seeds a claim automatically — the two
derivations that share no code are authored by whoever fixed the defect, with
the incident cited. Report only. Library: tools/awareness/incident_claims.py
