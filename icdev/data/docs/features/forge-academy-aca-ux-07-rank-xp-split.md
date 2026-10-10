# CUI // SP-CTI

# aca-ux-07 — the earned-XP rank change does not get a one-time learner notice

**Status:** decided, implemented
**Date:** 2026-08-01
**Supersedes nothing. Closes:** aca-ux-07
**Context:** aca-int-07 (PRs #1042 / #1044 / #1045, migrations 315–316, applied to the
live database 2026-07-30)

## The question

aca-int-07 moved rank off the running XP total and onto **earned** XP —
`SUM(xp_delta) WHERE is_attendance = 0` in `fa_xp_ledger`. Attendance still
accumulates and still shows in the total; it just no longer buys a rank.

Verified in the browser after a dashboard restart: the live learner went from
*Operative* to *Recruit — 250 XP to Operative* while their displayed total stayed
1715. That is correct — 1465 of those points were 41 daily logins — but it is a
**visible demotion** for anyone who already held a rank.

Does that warrant a one-time notice explaining why the rank changed?

## Measured state (live DB, 2026-08-01)

```
fa_users                    1 row   — id=1, xp=1815, level='recruit'
fa_xp_ledger  is_attendance=1      43 rows   1565 XP   (daily logins)
fa_xp_ledger  is_attendance=0       3 rows    250 XP   (graded work)
```

The population that could have been demoted is **one row**, and it is the
`guest@system.local` fallback account (`email` is empty), not a person with a
training record to defend.

## Decision

**No one-time demotion notice.** Instead, a **standing label** of the split, shipped
on the three surfaces that print a rank next to an XP figure.

## Why not the one-time notice

1. **Its audience is closed and already empty.** Only a learner who held a rank
   *before* 2026-07-30 could experience the demotion. Exactly one account qualifies,
   and it is the system fallback user. Everyone enrolled after that date was
   earned-XP-ranked from their first login and was never demoted — there is nothing
   to explain to them. The notice would ship with an audience of zero and stay that
   way.

2. **It would need dismissal state we would then carry forever.** A one-time notice
   that cannot be dismissed is a permanent banner; one that can be dismissed needs a
   new persisted flag per user, plus a "was this account ranked before migration 315"
   predicate that is only answerable during a window that has already closed. That is
   a schema addition and a dated conditional, both permanent, to apologise once.

3. **An apology is the wrong frame for a correct change.** The rank did not break —
   it started being true. `fa_users.xp` had accumulated 1715 points that no record
   explained, and 85% of the rank was attendance. Leading with "sorry your rank
   changed" reads as though the old number was the right one.

## What the actual defect was

The demotion is a one-off. The thing that made it *look* like a bug is permanent, and
it is not the demotion at all — it is that **two different XP numbers appear side by
side with neither one labelled**:

| Surface | Printed | Bar filled by |
|---|---|---|
| `page.html` hub | `fa_user.xp` = 1815 | `level_ctx.pct`, from earned = 250 |
| `mission.html` mini-bar | `fa_user.xp` = 1815 | `level_ctx.pct`, from earned = 250 |
| `profile.html` | `fa_user.xp` = 1815, labelled **"Total Earned"** | — |

A learner five logins into a fresh account sees "125 XP" beside "250 XP to Operative"
and reads broken arithmetic. That happens to **every** learner who ever collects a
daily login, forever, with or without a migration — so it cannot be covered by a
one-time notice.

`profile.html` was the sharpest case: it labelled the running total **"Total
Earned"**, and since aca-int-07 "earned" has a specific meaning — the 250 that buys
rank. The label asserted the opposite of the truth.

## What shipped instead

- `apps/forge_academy/blueprint.py::_level_ctx` now returns `total_xp`, `earned_xp`
  and `attendance_xp` alongside the existing rank progress. `attendance_xp` is derived
  by subtraction, not by a second `SUM` over `is_attendance = 1`, so it cannot
  disagree with the total printed beside it; it is clamped at zero so the UI never
  asserts a nonsense split if the ledger over-counts `fa_users.xp`.
- **Hub** (`page.html`) — the bar is labelled *"Rank progress — earned XP"* and prints
  the earned figure, not the total. Below it, when attendance is non-zero, a standing
  `role="status"` line states the real split: *"Total 1815 XP, of which 1565 is
  daily-login attendance. Attendance still counts toward your total — rank is earned
  from graded work only, so showing up does not buy a promotion."*
- **Mission mini-bar** (`mission.html`) — prints `earned XP`, matching the bar it sits
  above.
- **Profile** (`profile.html`) — *"Total Earned"* corrected to *"Total XP"*, with
  *"Earned — sets rank"* added beside it.

`role="status"` follows `faEnrolNotice` in `mission.html`, the accessible pattern the
XP live region was modelled on (aca-trn-06), so the line is announced when the hub
re-renders after the daily-login award. It is standing text rather than a dismissible
toast, because the condition it describes is permanent.

## Do not re-litigate

If someone proposes a demotion notice again: the demotion affected one system account,
on one date that has passed, and the confusion it appeared to cause was the unlabelled
total-vs-earned split — which is now labelled on all three surfaces. Re-open only if a
future change moves *already-earned* XP out of the rank basis for accounts with real
learners on them, which is a different event with a live audience.

## Tests

`tests/test_aca_rank_xp_split.py` — the split is exposed, sums to the total, survives
a missing ledger, and the templates print the earned figure rather than the total
beside the rank bar.
