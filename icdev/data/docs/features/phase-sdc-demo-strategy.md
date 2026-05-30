# Phase: SDC Demo Strategy
<!-- CUI // SP-CTI -->

## Summary

Transforms the Security Design Canvas (SDC) from a powerful-but-invisible tool into a
15-minute executive demo that shows the complete arc from a vulnerable system to cATO-ready
compliance — all driven by ICDEV automation.

**Demo story**: Red Team finds 47 STRIDE findings (15 CAT1, F-grade). SDC runs 12-step
automated workflow (threat scan → STIG check → risk scoring → ISSO approval → IaC gen →
Terraform → Ansible → post-deploy scan → crosswalk → evidence). Result: 0 CAT1, grade A,
87% NIST 800-53, $29,400 saved, cATO ready.

---

## Shipped Components

### Phase 2 — Synthetic Data (SDC-06 through SDC-10)
| File | Purpose |
|------|---------|
| `tools/db/seeds/seed_sdc_demo.py` | 8 designs, 47 threats, 3 attack snapshots, 5 SOPs, before/after compliance timeline, 12 ISSO workflow steps, ROI metrics |
| `tools/db/migrations/161_sdc_compliance_timeline/up.py` | `sdc_compliance_timeline` + `sdc_roi_metrics` tables in main PG DB |
| `tools/db/seeds/seed_ai_canvases_all.py` | Updated to include T6 SDC demo step |

Seed entry point:
```bash
python tools/db/seeds/seed_sdc_demo.py --all [--reset] [--json]
python tools/db/seeds/seed_sdc_demo.py --verify --json
```

### Phase 3 — SDC Demo Runner (SDC-11 through SDC-18)
| File | Purpose |
|------|---------|
| `tools/sdc/demo_runner.py` | 3 scenarios (A=Red Team, B=Workflow, C=After State), `run_sdc_demo()` public API |
| `tools/iqe/adapters/sdc_demo.py` | 4 IQE collections: `sdc_demo.runs`, `sdc_demo.scenarios`, `sdc_demo.threat_summary`, `sdc_demo.workflow_steps` |
| `tools/dashboard/templates/security_canvas/demo.html` | 3-scenario control panel with progress bar and IQE query widget |
| `tools/security_canvas/blueprint.py` | Routes: `GET /security/demo`, `POST /security/api/sdc-demo-run` |
| `tools/dashboard/app.py` | `_CANVAS_MAP` entry for `sdc_demo` |

Demo runner entry point:
```bash
python tools/sdc/demo_runner.py --scenario A --audience exec --json
python tools/sdc/demo_runner.py --scenario B --simulate --json
python tools/sdc/demo_runner.py --scenario C --json
```

### Phase 4 — Playbooks + IQE Queries (SDC-19 through SDC-24)
| File | Purpose |
|------|---------|
| `docs/features/sdc-demo-playbook.md` | Leadership 15-min playbook (Red Team → Compliant arc) |
| `docs/features/sdc-demo-customer-playbook.md` | Existing customer 20-min deep-dive |
| `docs/features/sdc-demo-prospect-playbook.md` | Prospect live-build walkthrough |
| `context/iqe/queries/security/stig_cat1_open.iqe` | Operational: open CAT1 findings |
| `context/iqe/queries/security/il5_ready_designs.iqe` | Operational: IL5-ready designs |
| `context/iqe/queries/security/control_coverage_gaps.iqe` | Operational: control gaps |
| `context/iqe/queries/security/high_risk_designs.iqe` | Operational: high-risk designs |
| `context/iqe/queries/security/sop_workflow_approvals.iqe` | Operational: SOP approvals |
| `context/iqe/queries/security/workflow_completion_status.iqe` | Operational: workflow status |
| `context/iqe/queries/security/remediation_roi.iqe` | Executive: ROI by design |
| `context/iqe/queries/security/posture_grade_trend.iqe` | Executive: posture trend |
| `context/iqe/queries/security/cat1_reduction_summary.iqe` | Executive: CAT1 reduction |
| `context/iqe/queries/security/cato_readiness.iqe` | Executive: cATO-ready designs |
| `context/iqe/queries/security/demo_run_history.iqe` | Executive: demo run history |
| `context/iqe/queries/security/scenario_catalog.iqe` | Executive: scenario catalog |

### Phase 5 — WOW Factor Enhancements (SDC-25 through SDC-29)
| File | Purpose |
|------|---------|
| `tools/dashboard/templates/security_canvas/_compliance_timeline.html` | Before/after timeline widget |
| `tools/sdc/roi_calculator.py` | `compute_roi(design_id)` — hours saved, cost saved, ROI multiplier |
| `tools/dashboard/templates/security_canvas/attackpath.html` | MITRE ATT&CK tactic sidebar overlay |
| `tools/sdc/isso_gate.py` | `approve_demo()` — ISSO gate simulation with audit trail |
| `tools/security_canvas/blueprint.py` | Routes: `/security/api/compliance-timeline/<id>`, `/security/api/roi/<id>`, `/security/api/isso-approve`, `/security/api/attack-ttp-coverage/<id>` |
| `tests/test_sdc_demo_runner.py` | 19 pytest tests — all pass |

---

## Demo Narrative (3 Acts, 15 Minutes)

### Act 1 — Before State (5 min)
- Navigate to `/security/demo` and click **Scenario A (Red Team)**
- Show: 47 STRIDE findings, 15 CAT1, risk score 8.7, posture grade F
- Show: 3 live attack paths on the attack path digital twin (`/security/designs/demo-design-001/attack-paths`)
- Hook for leadership: *"This is what an adversary sees today"*

### Act 2 — 12-Step Workflow (5 min)
- Click **Scenario B (12-Step Workflow)**
- Watch 12-step progress bar animate: Threat Scan → STIG Check → Risk Scoring → ISSO Approval Gate → IaC Generation → Security Policy → Terraform Plan → Terraform Apply → Ansible Remediation → Post-Deploy Scan → Compliance Crosswalk → Evidence Package
- Step 4: Live ISSO approval gate — approver clicks in real time
- Hook: *"200 manual hours → 17 automated hours"*

### Act 3 — After State (5 min)
- Click **Scenario C (After State)**
- Show: 0 CAT1, risk score 1.2, posture grade A, 87% NIST 800-53 coverage
- Show: Terraform IaC snippet (AC-2, SC-8, AU-9, IA-5, CM-7, SI-10, SC-28, IR-4)
- Show: ROI widget — $29,400 saved, 50x ROI, 1.23 FTEs avoided
- Hook: *"Design to cATO-ready in one session"*

---

## Key Data Points for Demos

| Metric | Before | After |
|--------|--------|-------|
| CAT1 findings | 15 | 0 |
| Risk score | 8.7 | 1.2 |
| Posture grade | F | A |
| NIST 800-53 coverage | 0% | 87% |
| Manual hours | 200h | 4h |
| Cost saved | — | $29,400 |
| ROI | — | 50x |
| Attack paths | 3 | 0 |

---

## Verification

```bash
# 1. Seed demo data
python tools/db/seeds/seed_sdc_demo.py --all --reset
python tools/db/seeds/seed_sdc_demo.py --verify --json

# 2. Run all 3 scenarios headlessly
python tools/sdc/demo_runner.py --scenario A --json
python tools/sdc/demo_runner.py --scenario B --simulate --json
python tools/sdc/demo_runner.py --scenario C --json

# 3. ROI check
python tools/sdc/roi_calculator.py --design demo-design-001 --json

# 4. Run tests (19 tests, all pass)
pytest tests/test_sdc_demo_runner.py -v --tb=short
```

---

## Phase 1 — Knowledge Base (Background, Not Blocking Demo)

Files to create (pending SDC-01 through SDC-05):
- `tools/sdc/ingest_knowledge_base.py` — batch ingest orchestrator
- SOURCE_REGISTRY entries for NIST 800-53 Rev 5, DISA STIGs, MITRE ATT&CK STIX, NIST CSF 2.0, NIST 800-207 ZTA, NIST 800-218 SSDF, DoD DevSecOps Guide

These tasks are tagged `medium` priority in Kanban and do not block the demo.
