# CUI // SP-CTI

# kpr-watch-22 — nothing consumed `union_refused`: the rung had resolved 2 conflicts in its lifetime

```bash
python -m tools.kanban.union_candidates                  # 30 days, each candidate with its survey
python -m tools.kanban.union_candidates --attribute-only # split the corpus; no git, no survey
```

**THE DEFECT IS NOT THE RULE, IT IS THAT NOTHING READS THE TELEMETRY.**
Measured on 2026-09-12:

```
pr_watcher.union_resolved   2        <- LIFETIME
pr_watcher.union_refused    37       <- in TWELVE HOURS
pr_watcher.rebase_failed    39       <- same window
```

33 of the 37 read `undeclared:`. The rung is built, declared, reached roughly
forty times a day, and had resolved two conflicts in its entire existence.
`pr_watcher.union_refused` had EXACTLY ONE mention outside its writer and it
was a docstring (`tools/kanban/union_resolver.py:77`) — no reflex, detector,
survey or gate consumed it. So the only discovery path an undeclared
append-shaped file ever had was a human noticing a stalled PR and reading audit
rows by hand, which is what happened that night, five hours after the stall
began. kpr-watch-14 measured the identical shape (65 refusals, 0 resolutions)
and fixed it by declaring ONE file pair; kpr-watch-20 did the same for a second
pair. Both were correct and **neither is a mechanism** — each bought a single
epic. This is that defect — declared-but-unconsumed — reaching the telemetry of
the platform's own merge automation.

**ATTRIBUTION IS TO THE UNDECLARED MEMBER, NEVER TO THE SET.** A refusal names
the WHOLE conflict set and ONE undeclared file refuses the set, so a file
declared since kpr-watch-14 appears in refusals it did not cause. Over the 30
days to 2026-09-12 `CLAUDE.md` is named in **51** refusal rows and caused
**none** of them; ranking files by refusal count would have proposed declaring
a file that has been declared for a week. Candidacy is therefore re-asked of
the SHIPPED `match_declaration`, per file: a file it accepts is COLLATERAL and
can never be a candidate. The resolver's own message is not trusted either — it
raises at the FIRST undeclared file it reaches, so a set with two undeclared
members names one and costs both, and the set is re-walked instead.

**A HUNK THAT WAS `refused` OR `unanchorable` COMPARED NOTHING**, and
`lost_content` counts it as 0. That zero is the `None`-never-0 hazard one level
down: `tools/canvas_compliance/posture.py` surveys to ONE hunk, `refused`, and
so to `lost_content=0` — a source module reading as clean because nothing was
ever compared. The denominator is the DECISIVE hunks, and a candidate with none
of them is `unmeasurable` and files NOTHING. A proposal without evidence is
what this card exists to replace.

**THE RECOMMENDATION IS CALIBRATED, and the calibration is the three files a
human correctly declared** (measured on `origin/main`, 2026-09-12):

```
tests/airgap/test_artifact_freshness.py   4 decisive hunks  0 base-touching  lost 0
args/pinned_artifacts.yaml                1 decisive hunk   1 base-touching  lost 0
CLAUDE.md                                14 decisive hunks  1 base-touching  lost 0
tools/canvas_compliance/posture.py        0 decisive hunks  1 base-touching  UNMEASURABLE
```

Two of the three correct declarations contain a hunk BOTH SIDES REWROTE. So
"append-shaped or nothing" would have declined two of three and the rung would
still have resolved two conflicts in its lifetime. `union_lost_content` — text
that landed, came from a side, and the union drops — is the only verdict that
is a defect, and it is the number both earlier cards actually decided on.

**SHAPE IS REPORTED, NOT GUESSED, AND NEVER READ OFF THE PATH.**
`append_shaped` means every measured hunk is a pure insertion on both sides
with the base region EMPTY; anything else is `rewrites_base`, carried **with
its count**, because 1-of-15 (CLAUDE.md, correctly declared) and 1-of-1 (a
source module) are the same flag and different facts. It is the reviewer's
decisive input and deliberately not a veto.

**MEASURED AFTER ARMING**, 30 days to 2026-09-12: **118** refusal rows, **9**
undeclared files named, **0** of them already declared, **9** declined
`unmeasurable` (8 source modules / a migration `up.py` / a `docs/audits` file,
none with a comparable hunk), **0** proposed — the two files that were
proposable had been declared by hand hours earlier. Re-run with kpr-watch-14's
and kpr-watch-20's entries stripped, it proposes **exactly the 4 files the
human declared by hand** and still declines all 9.

**THE CARD PROPOSES, THE HUMAN DECLARES.** One `suggested` card per measurable
candidate, carrying the survey, the refusal count, the shape verdict and the
exact one-line declaration. Nothing writes `union_resolver.files`, pinned over
the AST — an actuator that edits its own guardrail is the tier `restore_acts`
deliberately does not have. And nothing auto-declares: kpr-watch-14 records
that "declaring one to quieten a refusal is how the wrong rule shipped a broken
file twice on 2026-09-03".

**DELIBERATELY NOT REOPENED:** partial resolution of a mixed set.
kpr-watch-14 measured it and it buys ZERO — `rebase_recovery` aborts and
deletes any outcome that is not fully resolved, so a half-resolution never
reaches a human.

Consumed as the SIXTH detector on `detector_findings_reflex` (6h, green). No
new table and no new writer: the rows already existed.
