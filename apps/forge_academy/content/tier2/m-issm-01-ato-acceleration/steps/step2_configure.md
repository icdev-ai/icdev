---
ontology_id: icdev:mission:m-issm-01-ato-acceleration:step:2
step_class: icdev:Lesson
---

# Configure ATO Acceleration

These are the inputs an ATO acceleration run takes. This step is a read-through. There is no form to fill in. When you've read what each input does, click **Understood → Continue**.

## Inputs

**System Name** — The system's formal name as it appears in eMASS/XACTA.

**Impact Level** — Determines the applicable control baseline:
- **IL2**: public or non-critical mission information. FedRAMP Moderate baseline (323 controls in Rev 5).
- **IL4**: Controlled Unclassified Information (CUI). FedRAMP Moderate plus the DoD Cloud Computing SRG's additional (FedRAMP+) controls.
- **IL5**: higher-sensitivity CUI and National Security Systems. FedRAMP High baseline (410 controls in Rev 5) plus FedRAMP+ controls.

`python tools/compliance/crosswalk_engine.py --impact-level IL4` prints the controls ICDEV puts in scope for a level.

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
