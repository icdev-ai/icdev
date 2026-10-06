---
ontology_id: icdev:mission:m-isso-01-stig-triage:step:1
step_class: icdev:Lesson
---

# STIG Triage Agent — Watch It Run

Before you configure your own STIG triage agent, see how ICDEV's AI handles a real STIG finding. The walkthrough below is an illustrative run.

## What just happened?

The STIG Triage Agent received **RHEL 8 STIG V-230296 (RHEL-08-010550)**, a CAT II finding that requires SSH `PermitRootLogin` to be disabled. In under 2 seconds, it:

1. **Classified** the finding: CAT II (medium), so it goes on the POA&M with the remediation window your organization sets for CAT II
2. **Located** the fix: `/etc/ssh/sshd_config` → set `PermitRootLogin no`
3. **Generated** an Ansible remediation task (`lineinfile` on `PermitRootLogin no`, the same task ICDEV's `tools/infra/ansible_generator.py` emits)
4. **Drafted** a POA&M entry with timeline, responsible party, and milestone dates
5. **Collected evidence**: an `sshd_config` snapshot before and after the patch

## Why this matters

Manual STIG triage for a 500-finding RHEL baseline takes 3–5 days. The STIG Triage Agent processes all 500 in under 10 minutes — with auto-generated evidence packages ready for your ATO package.

## Next: Configure your own

The next step walks through the settings that target the agent at your system: which STIG finding to triage, which severities to include, and which system the evidence belongs to.
