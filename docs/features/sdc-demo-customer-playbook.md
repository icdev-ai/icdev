# SDC Demo Playbook — Existing Customer Edition (20 minutes)
**Audience**: Existing ICDEV™ customers — security engineers, ISSOs, DevSecOps leads
**Goal**: Deep-dive on new SDC capabilities: ISSO gate, IaC generation, IQE natural-language queries
**Format**: 2-person team (driver + narrator), 20 minutes

---

## Pre-Demo Setup

```bash
python tools/db/seeds/seed_sdc_demo.py --verify --json
# All 8 checks must show "ok": true
```

**Audience selector**: Set to **Technical** for this playbook.

---

## Section 1 — SDC Overview + Before State (5 minutes)

Open `http://localhost:5050/security/demo`, click **Scenario A**.

Narrate:
- 47 STRIDE threats decomposed by CAT1/2/3
- 3 attack paths with TTP mapping (T1190 SQLi → T1552 cred dump → T1071 exfil)
- Risk score 8.7 — above ISSO approval threshold

**IQE Queries to demonstrate** (type in the IQE widget):

1. `foreach t in sdc_demo.threat_summary where t.snapshot_label == "before" where t.cat1_count > 0 select t.design_id, t.cat1_count, t.risk_score, t.posture_grade`
2. `foreach s in sdc_demo.workflow_steps where s.approved_by != null select s.step_name, s.approved_by, s.approved_at`

---

## Section 2 — 12-Step Workflow Deep Dive (8 minutes)

Click **Scenario B**, enable **Simulate ISSO**.

Walk through each step category:

**Automated steps (1–3, 5–8, 10–12)**:
- Step 1: STRIDE threat scan → 47 findings in 45 seconds (vs 2-day manual)
- Step 2: STIG checker → maps to DISA STIG V-IDs + NIST control families
- Step 5: IaC generator → Terraform HCL with SSE-KMS, WAF, non-root containers

**ISSO Gate (Step 4)**:
- Show `isso-demo@agency.gov` approval in the workflow panel
- Explain: in production, ISSO receives email with assessment package link; one-click approval writes to sc_audit (append-only, NIST AU-6 compliant)
- Point out: audit trail immutable — even admins cannot modify approval records

**Post-Deploy Validation (Step 10)**:
- Automated re-scan confirms 0 CAT1 remaining before evidence package assembles

**IQE Query**:
```
foreach s in sdc_demo.workflow_steps where s.status == "completed" select s.step_id, s.step_name, s.approved_by
```

---

## Section 3 — After State + IQE Power Queries (7 minutes)

Click **Scenario C**.

Walk through:
- NIST 800-53 87% / FedRAMP 82% / CMMC L2 91% crosswalk (generated automatically)
- Expand Terraform IaC snippet — show the actual `module "sdc_security_baseline"` output
- ROI: 196 hours saved, $29,400 cost savings, 50x ROI multiplier

**Advanced IQE Queries** (show these sequentially):

3. `foreach t in sdc_demo.threat_summary where t.design_id == "demo-design-001" select t.snapshot_label, t.posture_grade, t.risk_score, t.controls_implemented`

4. `foreach t in sdc_demo.threat_summary where t.snapshot_label == "after" where t.cat1_count == 0 select t.design_id, t.posture_grade, t.controls_implemented, t.controls_total`

5. `foreach t in sdc_demo.threat_summary where t.snapshot_label == "before" select t.design_id, t.remediation_hours, t.cat1_count, t.posture_grade`

6. `foreach s in sdc_demo.scenarios select s.scenario_id, s.title, s.audience, s.hook`

---

## Existing 5 IQE Queries (from Attack Path module)

Show these from the Security Canvas → Attack Path page:

7. Data exfiltration paths: `foreach e in attack.edges where e.risk_score >= 8 where e.encrypted == false select e.snapshot_id, e.source, e.target, e.risk_score`
8. Cross-boundary traversals: (per `cross_boundary_paths.iqe`)
9. Lateral movement to IL5: (per `lateral_to_il5.iqe`)
10. Privilege escalation paths: (per `priv_escal_paths.iqe`)
11. MTTR critical paths: (per `mttr_critical_paths.iqe`)

---

## Q&A Anticipation

**"Can we connect this to our existing SIEM?"**
→ Yes. sc_audit writes to immutable append-only log; Splunk/QRadar connectors in `tools/sdc/`. CloudTrail integration shown in IaC snippet.

**"What about our existing STIG findings database?"**
→ SDC ingests from DISA STIG Viewer XML via `tools/sdc/stig_checker.py`. Existing POA&M items import via `tools/compliance/`.

**"Does the ISSO gate integrate with our approval workflow?"**
→ `tools/sdc/isso_gate.py` exposes `/security/api/isso-approve`. Wrap with ServiceNow webhook for enterprise ITSM integration.
