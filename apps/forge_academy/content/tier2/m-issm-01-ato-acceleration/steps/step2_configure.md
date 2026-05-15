---
ontology_id: icdev:mission:m-issm-01-ato-acceleration:step:2
step_class: icdev:Lesson
---

# Configure ATO Acceleration

Set up the ATO Acceleration agent for your system.

## Configuration Fields

**System Name** — The system's formal name as it appears in eMASS/XACTA.

**Impact Level** — Determines the applicable control baseline:
- **IL2** — CUI Unclassified, FedRAMP Moderate baseline (325 controls)
- **IL4** — CUI / DoD, FedRAMP High + DoD SRG baseline (421 controls)
- **IL5** — CUI Sensitive / National Security, IL4 + additional DoD controls (450+ controls)

**Compliance Framework** — Primary framework driving the ATO:
- **RMF** — DoD Risk Management Framework (most common for internal systems)
- **FedRAMP** — For cloud service offerings seeking JAB or Agency authorization
- **CMMC Level 2** — For defense contractors handling CUI

**Evidence Sources** — Check all that apply:
- Nessus/Tenable scan results (uploaded or live API)
- Ansible playbook inventory (auto-maps configuration controls)
- Terraform state files (auto-maps infrastructure controls)
- Existing SSP from prior ATO (extracts inherited controls)

## What you get

- A prioritized evidence gap list with estimated collection effort per control
- Auto-generated narrative drafts for controls with sufficient evidence
- An ATO timeline Gantt chart (days to package complete)
- A risk acceptance memo for controls that cannot be fully evidenced
