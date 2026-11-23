---
ontology_id: icdev:mission:m-assessor-01-objective-evidence:step:3
step_class: icdev:Lesson
---

# The Evidence Binder and the SPRS Score

You will learn what ICDEV[OS]'s evidence binder holds, how its SPRS score is computed, and what ICDEV[OS] never claims on your behalf.

> Facts in this lesson are current as of November 2026. CMMC Phase 2 was suspended on 2026-07-13 pending a review; ICDEV[OS] shows that as a fact and does not guess past it.

## The binder

`GET /api/v1/cmmc/{system_id}/binder` gives a generated, digest-pinned binder. It holds the objective results as OSCAL assessment results, and the POA&M, each validated against NIST's schema. The same evidence gives the same digest, so you can tell whether what you were handed is what was generated.

## The score

The SPRS score uses the DoD Assessment Methodology's values: 110 down to -203. Every deduction is listed, with any partial credit the methodology allows. An unverified practice is deducted as not met.

## Every verdict replays

The twin records each CMMC verdict with its observation. Replaying it from that observation must give the same verdict. A verdict that does not replay is not evidence.

## What ICDEV[OS] never does

ICDEV[OS] records a self-assessment and its affirmation. It never certifies. A certification assessment is a C3PAO's (Level 2) or DCMA DIBCAC's (Level 3). Choosing the C3PAO is the customer's.
