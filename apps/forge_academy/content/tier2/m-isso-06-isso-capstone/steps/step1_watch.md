---
ontology_id: icdev:mission:m-isso-06-isso-capstone:step:1
step_class: icdev:Lesson
---

# ISSO Capstone — Deploy a Complete STIG Remediation Workflow

You've configured individual ICDEV capabilities. Now you'll chain them into a complete, repeating workflow — from STIG scan to remediation to evidence to SSP update. This is your operational ISSO toolkit.

## What You'll See

An illustrative run of a full STIG remediation workflow for ICDEV-Prod:

**Phase 1: Scan (automated, nightly at 0200)**
Nessus STIG scan initiates → 1,847 checks evaluated → 3 new findings vs baseline:
- CAT II: Privileged accounts can log in without multifactor authentication (IA-2)
- CAT III: Audit log retention shorter than the organization's policy (AU-11)
- CAT III: System clock not synchronized to an authoritative time source (AU-8)

**Phase 2: Triage + Assign (automated)**
Each finding classified → CAT II finding auto-assigned to the sysadmin team with its 90-day remediation deadline. CAT III findings scheduled for next sprint. POA&M entries created with risk-adjusted due dates.

**Phase 3: Remediation Artifact Generation**
Ansible playbooks generated for CAT III findings (automated remediation). CAT II playbook generated but requires sysadmin approval before execution.

**Phase 4: Evidence Collection + SSP Update**
Post-remediation evidence collected automatically. Control IA-2 re-evaluated → compliant. The IA-2 narrative in the SSP updated with the new evidence date and control status.

**Workflow runtime:** 4 hours total (23 minutes active ICDEV processing, rest waiting on approvals).

Your remediation velocity: 2.3× industry average. Your evidence completeness: 97%.

You're now an ISSO with an AI force multiplier.
