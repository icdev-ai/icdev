---
ontology_id: icdev:mission:m-isso-01-stig-triage:step:3
step_class: icdev:Lesson
---

# Verify: Review the Triage Results

The STIG Triage Agent has processed your configuration. Review the output below.

## What to look for

**Severity classification** — Confirm the agent correctly identified the CAT level. CAT I findings appear in red; they require a POA&M entry within 30 days of discovery.

**Remediation recommendation** — The agent proposes a specific fix action. For SSH hardening findings, it generates the exact `sshd_config` directive. For most RHEL STIGs, it produces an Ansible task.

**Auto-POA&M flag** — If `auto_poam: true`, the agent can push this directly into your POAM tracking system on the next sync.

**Evidence collected flag** — When `evidence_collected: true`, the agent has captured the system state (before-patch screenshot or config snapshot) needed for your ATO evidence package.

## What a real ISSO does next

1. Review the recommendation for accuracy
2. Assign to system admin team with the Ansible playbook
3. Set the POA&M milestone dates (auto-populated from today + 30 days for CAT I)
4. Upload to XACTA or eMASS
5. Schedule a verification scan after patching

The agent handles steps 3–4 automatically.
