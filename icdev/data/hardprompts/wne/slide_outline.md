# WNE Slide Outline Template
# 10-slide structure derived from workflow phases
---
## Slide 1 — Title & Classification
**{{ program_name }}**
{{ org_name }} | {{ classification }}
Prepared: {{ timeframe_months }}-Month Execution Plan

---
## Slide 2 — Strategic Purpose
**Why {{ program_name }}?**
{{ purpose }}

{% if parameters.roi_estimate %}Projected ROI: {{ parameters.roi_estimate }}{% endif %}

---
## Slide 3 — Workflow Overview
**{{ phases | length }} Phases | {{ decision_points | length }} Decision Points | {{ approval_gates | length }} Approval Gates**

| Phase | Type | Steps |
|-------|------|-------|
{% for phase in phases %}| {{ phase.name }} | {{ phase.phase_type }} | {{ phase.nodes | length }} |
{% endfor %}

---
## Slide 4 — Phase 1: Initiation
{% if phases | length > 0 %}
**{{ phases[0].name }}** ({{ phases[0].phase_type }})
Steps: {{ phases[0].nodes | join(', ') }}
{% else %}
No phases defined.
{% endif %}

---
## Slide 5 — Core Execution Phases
{% set mid_phases = phases[1:-1] if phases | length > 2 else phases %}
{% for phase in mid_phases %}
**{{ loop.index + 1 }}. {{ phase.name }}** ({{ phase.phase_type }})
Steps: {{ phase.nodes | join(', ') }}

{% endfor %}

---
## Slide 6 — Decision Points & Human-in-the-Loop
{% if decision_points %}
{% for dp in decision_points %}
- **{{ dp.name }}** — {{ dp.role }}{% if dp.doc_template %} | {{ dp.doc_template }}{% endif %}

{% endfor %}
{% else %}
Fully automated — no human decision steps required.
{% endif %}

---
## Slide 7 — Policy & Approval Gates
{% if approval_gates %}
{% for gate in approval_gates %}
- **{{ gate.name }}** — {{ gate.policy }} ({{ gate.role }})
{% endfor %}
{% else %}
No formal approval gates; phase transitions are system-enforced.
{% endif %}

---
## Slide 8 — Risk & Compliance Posture
- Classification: **{{ classification }}**
- Approval gates enforcing policy: **{{ approval_gates | length }}**
- Human oversight checkpoints: **{{ decision_points | length }}**
{% if parameters.risk_reduction %}
- Risk reduction estimate: **{{ parameters.risk_reduction }}**
{% endif %}

---
## Slide 9 — Timeline & Resources
**{{ timeframe_months }}-Month Delivery Plan**
{% if parameters.funding_ask %}
Funding ask: **{{ parameters.funding_ask }}**
{% endif %}

| Milestone | Phase | Gating |
|-----------|-------|--------|
{% for phase in phases %}| {{ phase.name }} | {{ loop.index }}/{{ phases | length }} | {% if phase.phase_type == 'approval' %}Policy gate{% elif phase.phase_type == 'human' %}HITL review{% else %}Automated{% endif %} |
{% endfor %}

---
## Slide 10 — Recommendation & Next Steps
**Recommended Action:** Authorize {{ program_name }} execution.

{% if audience == 'leadership' %}
- Approve funding: {% if parameters.funding_ask %}{{ parameters.funding_ask }}{% else %}TBD{% endif %}

- Direct program office to initiate Phase 1
- Schedule {{ decision_points | length }} executive review(s)
{% elif audience == 'compliance' %}
- Accept risk posture with {{ approval_gates | length }} gate(s)
- Assign compliance owners per decision point
- Schedule ATO evidence collection milestones
{% elif audience == 'technical' %}
- Stand up toolchain integrations
- Configure {{ phases | length }} phase automation pipelines
- Wire approval gate notifications
{% elif audience == 'board' %}
- Authorize {{ timeframe_months }}-month program
- Track ROI against baseline at each phase gate
- Quarterly board update at Phase {{ (phases | length / 2) | round(0) | int }}
{% else %}
- Initiate Phase 1 per workflow definition
- Engage stakeholders at each decision point
- Report status at phase boundaries
{% endif %}
