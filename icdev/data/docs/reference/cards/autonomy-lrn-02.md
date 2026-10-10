# Is intervention actually FALLING? The AUTONOMY card held to its own standard (autonomy-lrn-02)

> Moved verbatim from CLAUDE.md on 2026-09-12 (xrv-docs-02). The prose is the card's own record; only the leading `#` comment markers were stripped and the command runs fenced.

```bash
python -m tools.awareness.autonomy_loop                  # human report, 7-day window
python -m tools.awareness.autonomy_loop --json --window-days 30
python -m tools.awareness.autonomy_loop --no-live-verify # skip running the claim verifier
python -m tools.awareness.autonomy_loop --no-forge       # no gh call; PR-based measures read unmeasurable
```

FIVE MEASURES, each with its denominator stated: claims registered and how
many name a REAL incident (the registry's tag is DECLARED, the board's row is
VERIFIED — two numbers, never one); disagreements caught LIVE before a human;
duplicate-dispatch rate against the recorded 11.6% baseline (27/232,
2026-08-20); admission refusals split RIGHT/WRONG by replaying history
through the gate's OWN `classify` (never a second copy of the rule); stale
daemons over ASSESSED processes, each with `stale_for`.
EVERY RATE IS None, NEVER 0.0 OR 100.0, WHEN NOTHING WAS MEASURED. `_rate` is
the one place a percentage is computed; `pct if total else 100.0` here would
breach args/perfect_score_gate.yaml, ratcheted to 0 by rem-hyg-13.
THREE ABSENCES, measured on the live board 2026-08-21 and reported BY NAME:
 * the admission gate in `report` mode LOGS its verdict and persists nothing,
   so `recorded_refusals` counts ENFORCED refusals only; what it WOULD have
   said is recovered by replay, lifetime AND window, because the baseline was
   lifetime and a 7-day number against it is not like for like;
 * nothing persists a claim-verifier run (it runs when a human types the
   command — autonomy-act-01 schedules it), so a disagreement cannot be DATED
   and `caught_live` is None. The current verdicts ride along as
   `snapshot_now`, labelled: 0 disagreements today is not 0 caught live;
 * `agent_sessions` lacked the code-identity columns (migration
   20260821024132 unapplied), so all 5 live processes read "no recorded code
   version" and the stale count was UNKNOWN — named, not 0.
The lifetime admission replay DRIFTS DOWN over time: the forge reader returns
the newest PRs only, so a dispatch whose PRs aged out replays as allow (190
fires today against the baseline's 195, with more dispatches). Stated on the
result as `sample_note`. The windowed duplicate rate is a LOWER BOUND (a
branch that opened yesterday has had less time to draw a second PR) — stated
as `censoring`.
Baselines are carried AS RECORDED and dated, never recomputed. A delta is None
when either side is missing, and the headline cannot read `falling` while any
section is unmeasured: `falling_where_measured` names the holes beside it.
Report only, no --gate: it measures the BOARD and the FLEET, not a diff.
