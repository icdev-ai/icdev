---
ontology_id: icdev:mission:m-isso-02-poam:step:2
step_class: icdev:Lesson
---

# Configure POA&M Intelligence

Enter your system details to generate a POA&M package.

## Fields

**System ID** — Your system's identifier in eMASS or XACTA (e.g., `SYS-1042`). Used to pull existing findings if your system is already registered.

**Open Findings (JSON)** — A list of finding objects. Each finding needs:
- `id`: STIG VULN ID or CVE
- `severity`: `CAT I`, `CAT II`, or `CAT III`
- `discovered`: ISO date when the finding was opened

Example:
```json
[
  {"id": "V-220706", "severity": "CAT I", "discovered": "2026-04-01"},
  {"id": "V-220707", "severity": "CAT II", "discovered": "2026-03-15"}
]
```

**Output Format** — `eMASS CSV` or `XACTA XML`. Choose based on your IATT/ATO system.

## What you get

- A complete POA&M in your chosen format, ready for upload
- Milestone dates calculated per DoD policy (CAT I: 30 days, CAT II: 90 days, CAT III: 180 days)
- Overdue items flagged with escalation recommendation
- A summary memo for your ISSM (auto-generated, plain English)
