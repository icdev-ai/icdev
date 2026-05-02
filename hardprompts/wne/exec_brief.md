# WNE Executive Brief Template
# Classification: {{ classification }}
# Program: {{ program_name }} | Org: {{ org_name }}
---
{% if audience == 'leadership' %}
## Course of Action (COA)

{{ purpose }}

### Recommended COA: COA B — Balanced (Hybrid Approach)
Implement **{{ program_name }}** via the **COA B — Balanced (Hybrid)** approach over {{ timeframe_months }} month(s) across {{ phases | length }} phase(s).
The hybrid model combines structured training, lab standup, and on-the-job tasks for optimal risk-cost-speed tradeoff.
The workflow navigates {{ decision_points | length }} decision point(s) and {{ approval_gates | length }} approval gate(s).

### Return on Investment
{% if parameters.roi_estimate %}
Projected ROI: **{{ parameters.roi_estimate }}**
{% else %}
Estimated cost avoidance and risk reduction through structured {{ program_name }} execution.
{% endif %}

### Funding Ask
{% if parameters.funding_ask %}
Requested funding: **{{ parameters.funding_ask }}** over {{ timeframe_months }} months.
{% else %}
Budget authorization required to resource {{ phases | length }} execution phase(s).
{% endif %}

### Phase Summary
{% for phase in phases %}
- **{{ phase.name }}** ({{ phase.phase_type }}): {{ phase.nodes | length }} step(s)
{% endfor %}

### Key Approval Gates
{% if approval_gates %}
{% for gate in approval_gates %}
- {{ gate.name }} — Policy: {{ gate.policy }} ({{ gate.role }})
{% endfor %}
{% else %}
No formal approval gates defined; leadership concurrence assumed at phase boundaries.
{% endif %}

{% elif audience == 'compliance' %}
## Control Coverage & Risk Reduction

**Program:** {{ program_name }}
**Classification:** {{ classification }}
**Purpose:** {{ purpose }}

### Compliance Posture
This workflow addresses {{ phases | length }} execution phase(s) with {{ approval_gates | length }} policy-enforced gate(s)
and {{ decision_points | length }} human decision point(s) ensuring auditability.

### Phases & Control Mapping
{% for phase in phases %}
- **{{ phase.name }}** ({{ phase.phase_type }}): {{ phase.nodes | length }} node(s)
{% endfor %}

### Decision Points (Audit Evidence)
{% if decision_points %}
{% for dp in decision_points %}
- **{{ dp.name }}** — Role: {{ dp.role }}{% if dp.doc_template %} | Doc: {{ dp.doc_template }}{% endif %}

{% endfor %}
{% else %}
No human decision points; workflow is fully automated.
{% endif %}

### Risk Reduction
Structured phase-gate execution reduces residual risk by enforcing policy compliance at each transition.
{% if parameters.risk_reduction %}
Estimated risk reduction: **{{ parameters.risk_reduction }}**
{% endif %}

{% elif audience == 'technical' %}
## Toolchain & Integration Steps

**Program:** {{ program_name }}
**Timeframe:** {{ timeframe_months }} month(s)

### Execution Phases
{% for phase in phases %}
#### {{ phase.name }} ({{ phase.phase_type }})
Steps: {{ phase.nodes | join(', ') }}

{% endfor %}

### Integration Points
{% if parameters.integrations %}
{% for integration in parameters.integrations %}
- {{ integration }}
{% endfor %}
{% else %}
Refer to workflow YAML for per-step integration configuration.
{% endif %}

### Decision Points
{% if decision_points %}
{% for dp in decision_points %}
- **{{ dp.name }}** (node: `{{ dp.node_id }}`) — Role: {{ dp.role }}
{% endfor %}
{% else %}
No human-in-the-loop steps; all transitions are automated.
{% endif %}

### Approval Gates
{% if approval_gates %}
{% for gate in approval_gates %}
- **{{ gate.name }}** (node: `{{ gate.node_id }}`) — Policy: {{ gate.policy }} | Role: {{ gate.role }}
{% endfor %}
{% else %}
No approval gates defined.
{% endif %}

{% elif audience == 'board' %}
## Business Value & Competitive Positioning

**Program:** {{ program_name }}
**Strategic Purpose:** {{ purpose }}

### Business Value
{{ program_name }} delivers structured workflow execution across {{ phases | length }} phase(s),
reducing operational risk and accelerating time-to-outcome by {{ timeframe_months }} months.

{% if parameters.business_value %}
{{ parameters.business_value }}
{% endif %}

### Competitive Positioning
{% if parameters.competitive_positioning %}
{{ parameters.competitive_positioning }}
{% else %}
Adoption of {{ program_name }} positions {{ org_name }} ahead of peers in process maturity
and compliance readiness.
{% endif %}

### Investment Summary
- Phases: {{ phases | length }}
- Timeframe: {{ timeframe_months }} months
- Decision gates: {{ decision_points | length }}
- Approval gates: {{ approval_gates | length }}
{% if parameters.funding_ask %}
- Funding ask: {{ parameters.funding_ask }}
{% endif %}

{% else %}
## Executive Summary

**Program:** {{ program_name }}
**Organization:** {{ org_name }}
**Classification:** {{ classification }}
**Purpose:** {{ purpose }}
**Timeframe:** {{ timeframe_months }} month(s)

### Workflow Overview
{{ phases | length }} phase(s) | {{ decision_points | length }} decision point(s) | {{ approval_gates | length }} approval gate(s)

### Phases
{% for phase in phases %}
- **{{ phase.name }}** ({{ phase.phase_type }}): {{ phase.nodes | length }} step(s)
{% endfor %}
{% endif %}
