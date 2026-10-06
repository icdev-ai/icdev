---
ontology_id: icdev:mission:m-isso-02-poam:step:3
step_class: icdev:Lesson
---

# Verify: Review Your POA&M Package

Review the generated POA&M package below before approving it.

## Quality checks to perform

**Date math**: confirm the Scheduled Completion Date for each finding is the Discovered date plus your organization's window for that severity (30 / 90 / 180 days for CAT I / II / III in this scenario). Any deviation needs ISSM justification.

**Overdue flags** — Any finding where today's date exceeds the Scheduled Completion Date must have a status of `Ongoing` and include a milestone extension justification. The agent flags these automatically.

**Responsible entity** — The agent uses the system owner from the SSP by default. Change this if a specific team owns the remediation.

**Resource requirements** — For CAT I findings, the agent estimates remediation hours. Review for accuracy — this feeds into your risk acceptance memo.

## eMASS import

The generated file is laid out for eMASS's POA&M import. Check it against the import template your eMASS instance currently publishes before you upload it. Templates change between eMASS releases.

## Approval flow

Once you're satisfied, click **Understood → Continue** to finish the mission. In a live run, the next action is to route the package to your ISSM for approval.
