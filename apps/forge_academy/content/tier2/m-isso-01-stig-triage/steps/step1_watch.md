---
ontology_id: icdev:mission:m-isso-01-stig-triage:step:1
step_class: icdev:Lesson
---

# STIG Triage Agent — Watch It Run

Before you configure your own STIG triage agent, watch how ICDEV's AI handles a real STIG finding.

## What just happened?

The STIG Triage Agent received **RHEL 8 STIG V-220706** — a CAT I finding requiring that SSH `PermitRootLogin` be disabled. In under 2 seconds, it:

1. **Classified** the finding: CAT I (Critical) — must remediate within 30 days per DoD POA&M policy
2. **Located** the fix: `/etc/ssh/sshd_config` → set `PermitRootLogin no`
3. **Generated** an Ansible remediation playbook
4. **Drafted** a POA&M entry with timeline, responsible party, and milestone dates
5. **Collected evidence**: screenshot of sshd_config before/after patch

## Why this matters

Manual STIG triage for a 500-finding RHEL baseline takes 3–5 days. The STIG Triage Agent processes all 500 in under 10 minutes — with auto-generated evidence packages ready for your ATO package.

## Next: Configure your own

In the next step, you'll configure the agent for your specific system. You'll choose which STIG ID to triage, set severity filter thresholds, and watch the agent produce a real remediation recommendation.
