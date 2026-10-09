# RMF cycle time: TWO clocks that are never merged (rmf-cyc-01)

> Moved verbatim from CLAUDE.md on 2026-09-12 (xrv-docs-02). The prose is the card's own record; only the leading `#` comment markers were stripped and the command runs fenced.

```bash
python -m tools.compliance.rmf_cycle_time                       # human report
python -m tools.compliance.rmf_cycle_time --json --window-days 30
python -m tools.compliance.rmf_cycle_time --project-id "proj-123"
python -m tools.compliance.rmf_stage_recorder --project-id "proj-123" --actor human:pm --submit --evidence pkg:v1
python -m tools.compliance.rmf_stage_recorder --project-id "proj-123" --actor human:ao --decision authorized
```

automation_time is OURS -- the 72h claim. decision_latency is the AUTHORIZING
OFFICIAL's queue. NEVER ADDED, and that is the whole design: halve the
automation and a single end-to-end figure can get WORSE because an AO took
leave, so a change in it attributes to neither party. Each carries its own
denominator; each is None -- never 0.0, which reads as "instant" -- when
unmeasured. Asserted by walking EVERY numeric value in the payload and
refusing any that equals the sum; a check on key NAMES would pass a blend
spelled under an innocent name.

THE WRITES ARE A CONSEQUENCE OF AN ARTIFACT, never a step anyone remembers.
rmf_workflow_stages held ZERO rows on the live board (measured 2026-09-02 --
so did ssp_documents, poam_items, stig_findings, oscal_artifacts and
cato_evidence) because it was a hand-maintained board, and hand-maintained
boards do not get maintained. The ATO dashboard papered over it: with no rows
it returned six synthetic `not_started` stages, so an estate nobody had ever
assessed rendered identically to one at the start of its lifecycle. Five
producers now write through tools/compliance/rmf_stage_recorder.py, the ONE
writer, mapping an artifact to the step it is EVIDENCE OF (SP 800-37 Rev 2):
  ssp_generator      -> select      S-4/S-5, the security plan
  poam_generator     -> assess      A-6
  stig_checker       -> assess      A-4, assessment reports
  oscal_generator    -> select | implement | assess, by WHAT the artifact IS
                                    (an OSCAL SSP is still an SSP; `catalog`
                                    and `profile` map to nothing -- reference
                                    material is not one system's progress)
  cato_monitor.collect_evidence -> monitor   Step 7, Task M-2
`categorize` and `authorize` have NO automated producer and are named on every
run under stages_without_producer: a clock that silently began at whichever
step happened to be wired measures a SHORTER job than the one being claimed.
started_at is stamped ONCE -- an SSP regenerated on day 3 must not reset the
clock to day 3 -- and a stage already `complete` is not reopened by a
regenerated artifact. An actor is automated | human | UNKNOWN, three values:
an unrecognised actor inflates the number we make a claim about if counted as
automation, and is the control group if counted as manual, so it is neither.

A LOWER BOUND CANNOT MEET A TARGET, and this was found by running the real
generators rather than by reasoning. A package with no recorded submission is
bounded by its latest artifact, so its span can only GROW: two artifacts 16ms
apart give 0.0 hours and score TRUE against 72h -- a perfect result for a
package nobody finished assembling, the empty-denominator defect wearing a
duration. `meets_target` is judged on SUBMITTED packages only and is None
otherwise; `is_lower_bound` says when the headline median is a floor.
A RESUBMISSION overwrites submitted_at and CLEARS the decision, so
decision_latency is the most recent submit->decide pair and never a sum across
rework -- the gap between a rejection and a resubmission is OURS. A decision
with no recorded submission is UNMEASURABLE, never zero. `denied` is `blocked`,
never `complete`. A NEGATIVE span (a decision dated before its submission) is
a data defect reported as unmeasurable, never abs()'d into a small number.

baseline_source carries TWO derivations that share no code: the DECLARED one
in args/rmf_cycle_baseline.yaml (with its `kind` -- `claimed` is never
presented as evidence) and a `measured_here` one re-derived from this
deployment's own human:* rows, which withholds its median below
`min_projects` because one project is an anecdote wearing a statistic's name.
THE SHIPPED BASELINE REFUSES ITS OWN COMPARISON, twice:
  baseline_unquantified              "months" has no number, scope, source or date
  baseline_includes_decision_latency an anecdotal ATO duration is wall-clock to
                                     the signed authorization and so CONTAINS
                                     the AO's queue; dividing it by an
                                     automation-only clock is the blend wearing
                                     a percentage
Fill in a quantified, AO-queue-free figure before publishing any "months ->
72h" ratio. Do NOT flip the refusal off in the config. No cited_external
baseline ships, on purpose -- inventing a citation to make the ratio render is
the fabrication the file guards against.
Migration 20260902233931 (started_at/actor/evidence_ref/submitted_at). It is
a Python migration because the table exists on PG and on NOTHING on SQLite,
so it probes the catalogue and adds exactly the missing columns rather than
guessing which of three populations it faces.
