---
ontology_id: icdev:mission:m-isso-02-poam:step:1
step_class: icdev:Lesson
---

# POA&M Intelligence — Watch It Run

The POA&M (Plan of Action and Milestones) is the single most audited artifact in your ATO package. Manual POA&M management typically consumes 4–8 hours per week. This walkthrough shows ICDEV automating it, as an illustrative run.

## What the agent just did

For system **ICDEV-Prod** with 12 open findings:

1. **Ingested** all 12 findings from the STIG triage results
2. **Prioritized** by CAT level: 2 CAT I, 6 CAT II, 4 CAT III, each with its remediation window (this organization uses 30 / 90 / 180 days)
3. **Calculated** milestone dates from the discovery date
4. **Generated** POA&M entries in DoD-standard format (columns: weakness, responsible entity, scheduled completion, milestones, resources required, status)
5. **Flagged** 1 overdue CAT II finding (past 90-day window) — escalation recommended
6. **Produced** an eMASS-ready import CSV

The entire process: **47 seconds**.

## The math without AI

Manual POA&M for 12 findings: cross-reference STIG IDs, look up the organization's remediation timelines, fill each row, format for eMASS, get ISSM approval. ~3 hours. Per review cycle.

## Next step

The next step walks through the inputs a POA&M run takes: your system ID, your open findings and the output format.
