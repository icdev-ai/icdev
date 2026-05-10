# Verify: ATO Timeline and Gap Analysis

Review your system's ATO acceleration report.

## Reading the output

**Control coverage** — The percentage of controls with evidence already mapped. Anything above 80% is excellent for a mature system; below 60% suggests significant rework.

**Gap prioritization** — The 3 columns that matter:
- `gap_control`: The NIST 800-53 control ID missing evidence
- `estimated_hours`: How long to collect the evidence manually
- `auto_collectable`: Whether ICDEV can auto-collect this evidence from existing telemetry

**Auto-collectable gaps** — These require zero manual effort. The agent pulls evidence from Nessus results, Ansible playbooks, Terraform state, or CloudTrail logs automatically.

**Manual gaps** — These require human action: interview the system owner, pull a screenshot, or get a policy document signed. The agent generates the exact request to send.

**Estimated ATO date** — Calculated as: today + sum of effort for manual gaps (assuming 4 hours/day of ISSM effort). The date shown assumes you start collection tomorrow.

## Common interpretation

- If `estimated_days > 60`: Consider requesting an IATT (Interim ATO) while evidence collection proceeds
- If `critical_gaps > 5`: Escalate to CISO before proceeding — may require architecture change, not just paperwork
- If `auto_collectable_pct > 70%`: You're in good shape — most of the work is automated

Click **Confirm** to save this report to your evidence folder.
