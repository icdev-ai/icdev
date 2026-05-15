---
ontology_id: icdev:mission:m-isso-01-stig-triage:step:5
step_class: icdev:Lesson
---

# Deploy: Activate Your STIG Triage Agent

You've configured and reviewed the STIG Triage Agent. Now deploy it as a live pattern in ICDEV.

## What "Deploy" means

Clicking **Deploy Agent** registers the `stig-triage` pattern in your ICDEV instance. From this point:

- The agent runs on a **scheduled cadence** (daily scan by default)
- Any new CAT I STIG findings trigger an **automatic alert** to your configured recipients
- POA&M entries are **auto-drafted** and queued for your approval
- Evidence packages are **auto-collected** during scheduled scans

## After deployment

Your STIG Triage Agent will appear in the ICDEV Agents dashboard at `/agents/active`. You can:
- Adjust the scan schedule (`Tools → Scheduler`)
- Add additional STIG IDs to the watch list
- Connect it to your ticketing system (Jira, ServiceNow)
- Enable auto-remediation for low-risk findings (ISSO approval required)

## This is real

This isn't a simulation — the pattern is deployed to your actual ICDEV instance. Your ATO timeline just got shorter.

Click **Deploy Agent** below to activate.
