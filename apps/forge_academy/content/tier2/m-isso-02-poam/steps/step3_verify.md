# Verify: Review Your POA&M Package

Review the generated POA&M package below before approving it.

## Quality checks to perform

**Date math** — Confirm the Scheduled Completion Date for each CAT I finding is exactly 30 days from the Discovered date. CAT II: 90 days. CAT III: 180 days. These are DoD policy requirements — any deviation requires ISSM justification.

**Overdue flags** — Any finding where today's date exceeds the Scheduled Completion Date must have a status of `Ongoing` and include a milestone extension justification. The agent flags these automatically.

**Responsible entity** — The agent uses the system owner from the SSP by default. Change this if a specific team owns the remediation.

**Resource requirements** — For CAT I findings, the agent estimates remediation hours. Review for accuracy — this feeds into your risk acceptance memo.

## eMASS import

The generated CSV follows the eMASS POA&M import template exactly. Download it and import directly via:

`eMASS → POA&M → Import → Upload CSV`

No manual reformatting required.

## Approval flow

Once you're satisfied: mark this step complete. The POA&M package is saved to your evidence folder and a draft notification is queued for your ISSM.
