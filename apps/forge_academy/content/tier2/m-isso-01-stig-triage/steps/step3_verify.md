---
ontology_id: icdev:mission:m-isso-01-stig-triage:step:3
step_class: icdev:Lesson
---

# Verify: Review the Triage Results

Here is how to read a triage result before you act on it.

## What to look for

**Severity classification**: confirm the agent identified the CAT level correctly against the STIG itself. CAT I findings appear in red and get the shortest remediation window.

**Remediation recommendation** — The agent proposes a specific fix action. For SSH hardening findings, it generates the exact `sshd_config` directive. For most RHEL STIGs, it produces an Ansible task.

**Auto-POA&M flag** — If `auto_poam: true`, the agent can push this directly into your POAM tracking system on the next sync.

**Evidence collected flag** — When `evidence_collected: true`, the agent has captured the system state (before-patch screenshot or config snapshot) needed for your ATO evidence package.

## What a real ISSO does next

1. Review the recommendation for accuracy
2. Assign to system admin team with the Ansible playbook
3. Set the POA&M milestone dates (auto-populated from today plus the window for that severity)
4. Upload to XACTA or eMASS
5. Schedule a verification scan after patching

The agent handles steps 3–4 automatically.
