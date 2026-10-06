---
ontology_id: icdev:mission:m-isso-02-poam:step:2
step_class: icdev:Lesson
---

# Configure POA&M Intelligence

These are the inputs a POA&M Intelligence run takes. This step is a read-through. There is no form to fill in. When you've read what each input does, click **Understood → Continue**.

## Inputs

**System ID** — Your system's identifier in eMASS or XACTA (e.g., `SYS-1042`). Used to pull existing findings if your system is already registered.

**Open Findings (JSON)** — A list of finding objects. Each finding needs:
- `id`: STIG VULN ID or CVE
- `severity`: `CAT I`, `CAT II`, or `CAT III`
- `discovered`: ISO date when the finding was opened

Example:
```json
[
  {"id": "V-230296", "severity": "CAT II", "discovered": "2026-09-01"},
  {"id": "CVE-2024-6387", "severity": "CAT I", "discovered": "2026-09-15"}
]
```

**Output Format**: an eMASS POA&M import file, or an export for XACTA. Choose based on the system of record your authorizing official uses.

## What you get

- A complete POA&M in your chosen format, ready for upload
- Milestone dates calculated from the discovery date and your organization's remediation timelines (a common convention is CAT I: 30 days, CAT II: 90 days, CAT III: 180 days. ICDEV's own generators keep these as per-severity settings, e.g. `tools/compliance/poam_auto_generator.py`)
- Overdue items flagged with escalation recommendation
- A summary memo for your ISSM (auto-generated, plain English)
