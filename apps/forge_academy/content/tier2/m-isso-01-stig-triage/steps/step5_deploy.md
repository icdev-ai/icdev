---
ontology_id: icdev:mission:m-isso-01-stig-triage:step:5
step_class: icdev:Lesson
---

# Deploy: Activate Your STIG Triage Agent

You've configured and reviewed the STIG Triage Agent. Here is what deploying it as a standing workflow means.

## What "Deploy" means

A deployed triage workflow runs on its own instead of waiting for you to start it. From that point:

- The agent runs on a **scheduled cadence** (daily scan by default)
- Any new CAT I STIG findings trigger an **automatic alert** to your configured recipients
- POA&M entries are **auto-drafted** and queued for your approval
- Evidence packages are **auto-collected** during scheduled scans

## After deployment

Running agents are listed on the ICDEV Agents page at `/agents`. For a deployed workflow you would typically:
- Adjust the scan schedule
- Add additional STIG IDs to the watch list
- Connect it to your ticketing system (Jira, ServiceNow)
- Enable auto-remediation for low-risk findings (ISSO approval required)

## In this mission

This step is a read-through. Finishing it does **not** deploy anything to your ICDEV instance. Deploying a scheduled workflow is an administrator action, and it needs ISSO approval for any auto-remediation.

Click **Understood → Continue** to finish the mission.
