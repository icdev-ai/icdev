---
ontology_id: icdev:mission:m-isso-07-turnkey-first-sprs:step:3
step_class: icdev:Lesson
---

# Read the First SPRS Score

You will learn how ICDEV[OS] computes your system's SPRS score, why it may be lower than you expect, and what you may and may not do with it.

> Facts in this lesson are current as of November 2026. The scoring values are the DoD Assessment Methodology's, as ICDEV[OS] pins them; CMMC Phase 2 was suspended on 2026-07-13 pending a review, and Level 2 still rests on SP 800-171 Rev. 2.

## The score

The **CMMC** tab of the **Systems** page shows the score. It starts at 110, one point per SP 800-171 Rev. 2 requirement, and deducts each requirement's weight (1, 3 or 5) when it is not met. The values sum to 313, so the score runs from 110 down to -203.

A requirement is met only when every one of its objectives is met, inherited or not applicable. An **unverified** objective counts as **not met**. Your first score is often low for exactly that reason: it reflects only what the evidence shows.

## The POA&M

Some unmet requirements may go on a POA&M; others may not. 32 CFR 170.21 sets the rules ICDEV[OS] enforces when you add an item:

- some requirements can never be on a POA&M;
- a Conditional status needs a score of at least 88 of 110;
- the POA&M must close within 180 days.

## What you never do

You do not affirm. A frozen assessment becomes a `cmmc-affirmation` item, the ISSM reviews it, and the **senior affirming official** signs it. Entering the score in the DoD's SPRS is your organisation's own act, outside ICDEV[OS]. Nothing here certifies a system: a certification assessment belongs to a C3PAO or to DCMA DIBCAC.
