---
ontology_id: icdev:mission:m-isso-01-stig-triage:step:2
step_class: icdev:Lesson
---

# Configure Your STIG Triage Agent

These are the settings that target the STIG Triage Agent at a specific finding. This step is a read-through. There is no form to fill in. When you've read what each setting does, click **Understood → Continue**.

## What each field means

**STIG ID**: the Vuln ID from the STIG checklist (e.g., `V-230296`). This tells the agent which rule to evaluate.

**Severity Filter** — Which CAT levels to triage:
- **CAT I only**: the highest-severity findings, with the shortest remediation window. Start here.
- **CAT I + CAT II**: adds medium-severity findings. Common for quarterly reviews.
- **All (CAT I/II/III)** — Full sweep. Use for initial ATO baseline.

**System Name** — The system identifier in your POAM tracker. Used to tag evidence and auto-populate the POA&M entry.

## What a run does

The agent will:
1. Look up the STIG finding in the RHEL 8 STIG database
2. Classify the severity and calculate the remediation deadline (from today)
3. Generate an Ansible task to remediate
4. Draft a POA&M entry template
5. Mark evidence as "ready to collect"
