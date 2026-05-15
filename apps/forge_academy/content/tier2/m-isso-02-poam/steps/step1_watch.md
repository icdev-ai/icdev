---
ontology_id: icdev:mission:m-isso-02-poam:step:1
step_class: icdev:Lesson
---

# POA&M Intelligence — Watch It Run

The POA&M (Plan of Action and Milestones) is the single most audited artifact in your ATO package. Manual POA&M management typically consumes 4–8 hours per week. Watch ICDEV automate it.

## What the agent just did

For system **ICDEV-Prod** with 12 open findings:

1. **Ingested** all 12 findings from the STIG triage results
2. **Prioritized** by CAT level: 2 CAT I (30-day), 6 CAT II (90-day), 4 CAT III (180-day)
3. **Calculated** milestone dates from the discovery date
4. **Generated** POA&M entries in DoD-standard format (columns: weakness, responsible entity, scheduled completion, milestones, resources required, status)
5. **Flagged** 1 overdue CAT II finding (past 90-day window) — escalation recommended
6. **Produced** an eMASS-ready import CSV

The entire process: **47 seconds**.

## The math without AI

Manual POA&M for 12 findings: cross-reference STIG IDs, look up DoD date calculation rules, fill each row, format for eMASS, get ISSM approval. ~3 hours. Per review cycle.

## Next step

Configure the POA&M Intelligence agent for your system — enter your system ID and open findings, and it will generate your POA&M package.
