# Configure Your STIG Triage Agent

You're going to configure the STIG Triage Agent for your system. Fill out the form below to target a specific STIG finding.

## What each field means

**STIG ID** — The VULN ID from the STIG checklist (e.g., `V-220706`). This tells the agent which control to evaluate.

**Severity Filter** — Which CAT levels to triage:
- **CAT I only** — Critical findings. 30-day remediation window. Start here.
- **CAT I + CAT II** — Adds high findings (90-day window). Common for quarterly reviews.
- **All (CAT I/II/III)** — Full sweep. Use for initial ATO baseline.

**System Name** — The system identifier in your POAM tracker. Used to tag evidence and auto-populate the POA&M entry.

## What happens when you submit

The agent will:
1. Look up the STIG finding in the RHEL 8 STIG database
2. Classify the severity and calculate the remediation deadline (from today)
3. Generate an Ansible task to remediate
4. Draft a POA&M entry template
5. Mark evidence as "ready to collect"

Hit **Apply Configuration** when ready.
