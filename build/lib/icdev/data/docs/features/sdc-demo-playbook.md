# CUI // SP-CTI
# SDC Demo Playbook — Leadership Edition (15 minutes)

**Classification:** CUI // SP-CTI  
**Audience:** DoD/IC Senior Leadership, CISOs, Program Managers  
**Goal:** Show Red Team → SDC Automation → Compliant in 15 minutes  
**Tagline:** "200 hours manual to 4 hours automated. $29,400 saved per engagement."  
**Version:** 1.0 | FY2026

---

## Pre-Demo Checklist (5 minutes before)

```bash
# 1. Verify demo data is seeded and counts are correct
python tools/db/seeds/seed_sdc_demo.py --verify --json
# Expected: {"cat1_findings": 15, "total_findings": 47, "attack_paths": 3, "status": "ok"}

# 2. Confirm dashboard is running and reachable
python -c "import urllib.request; urllib.request.urlopen('http://localhost:5050/health', timeout=3); print('OK')"

# 3. If data is missing, re-seed
python tools/db/seeds/seed_sdc_demo.py --reset --json

# 4. Navigate to SDC Demo Runner
# http://localhost:5050/security/demo
```

**Browser tabs to open before audience enters:**
1. `http://localhost:5050/security/demo` — SDC Demo Runner (primary stage)
2. `http://localhost:5050/security/` — Security Canvas (backup / deep dive)
3. IQE widget ready at bottom of demo page

**Audience selector:** Set to **Executive** before leadership enters.  
**Screen resolution:** 1920×1080 minimum — ensure IQE widget is fully visible.

---

## Act 1 — Red Team: Before State (5 minutes)

**Click:** Scenario A (Red Team button, red border)

**Opening line as results render:**

> "This is the live system — no slides. What you're seeing is an actual security assessment of our IL5 web application platform."

**Key numbers to call out:**

| Metric | Value | What It Means |
|--------|-------|---------------|
| Total findings | **47** | Full STIG + CVE finding inventory — CAT1 through CAT3 |
| CAT1 findings | **15** | DISA Category I — each one is a cATO blocker |
| Live attack paths | **3** | Confirmed adversary routes to sensitive data |
| Risk score | **8.7 / 10** | IL5 threshold is 7.0 — this system is over |
| Posture grade | **F** | ISSO would not approve today |
| Manual estimate | **200 hrs** | At $150/hr — $30,000 and 5 weeks |

**Narration script:**

> "47 findings. 15 of those are Category I — each one is a show-stopper for cATO. Path 1 goes through an unpatched SQLi vulnerability: CVE-confirmed, exploitable from the internet. Path 2 is a privilege escalation through a misconfigured IAM role. Path 3 chains a weak cipher suite to a session hijack vector. An adversary has three confirmed routes to your data today."

**IQE Query to type live:**
```
foreach t in sdc_demo.threat_summary where t.snapshot_label == "before" where t.cat1_count > 0 select t.design_id, t.cat1_count, t.total_findings, t.risk_score, t.posture_grade
```

**Hook question:** *"If you had a major audit tomorrow, how many of your systems would show a report like this?"*

**Transition:** *"Let me show you what happens when the Security Design Canvas takes over."*

---

## Act 2 — SDC 12-Step Workflow (5 minutes)

**Click:** Scenario B (Workflow button, blue border) + enable **Simulate ISSO** checkbox

**Opening line:**

> "Now I'm going to run the Security Design Canvas automated workflow. Watch 12 steps execute — threat scan, STIG check, risk scoring, ISSO approval, IaC generation, Terraform apply. The timer in the corner shows wall-clock hours. I'll narrate as it runs."

**Step-by-step narration:**

| Steps | Name | Narration |
|-------|------|-----------|
| 1–3 | Threat Scan → STIG Check → Risk Scoring | "Automated — 4 minutes. Manually: 2–3 days of assessor time." |
| 4 | **ISSO Approval Gate** | "This is the human-in-the-loop gate. In simulate mode our ISSO is approving now. In production: ISSO gets a notification, reviews the auto-assembled assessment package, clicks approve. We see: `isso-demo@agency.gov` approved at step 4." |
| 5–9 | IaC Gen → Policy Compile → Terraform → Ansible → Config Push | "Fully automated. The system is remediating the 47 findings in real time." |
| 10–12 | Post-Deploy Scan → Crosswalk → Evidence Package | "Verification complete. Evidence assembled. Ready for AO review." |

**Metric to highlight:** *"12 steps. 17 hours wall clock. 4 automated hours. Compare to 5 weeks and 200 manual person-hours."*

**Hook question:** *"Your ISSOs are reviewing 30-page Word documents today. What if their job was a one-click approval on a pre-validated package?"*

**Transition:** *"Now let's look at the same system — same day — after SDC ran."*

---

## Act 3 — After State: Compliant (5 minutes)

**Click:** Scenario C (Compliant button, green border)

**Opening line:**

> "Here's the same system. Same day. Four automated hours later."

**Key numbers to call out:**

| Metric | Before | After |
|--------|--------|-------|
| Total findings | 47 | **0** |
| CAT1 findings | 15 | **0** |
| Attack paths | 3 | **0** |
| Risk score | 8.7 | **2.1** |
| Posture grade | F | **A** |
| NIST 800-53 coverage | 41% | **87%** |
| Manual hours avoided | — | **196 hrs** |
| Cost saved | — | **$29,400** |

**Narration script:**

> "Zero CAT1 findings. Zero attack paths. Posture went from F to A in 17 hours — not 5 weeks. The ISSO can sign. The AO can review a machine-generated evidence package rather than waiting for a manual compilation."

**IQE Query to type live:**
```
foreach t in sdc_demo.threat_summary where t.snapshot_label == "after" where t.cat1_count == 0 select t.design_id, t.posture_grade, t.controls_implemented, t.risk_score, t.cost_saved
```

**Expand Terraform IaC snippet:**

> *"This Terraform was generated by the system — production-ready, NIST-compliant, tagged for GovCloud, ready to push through your CI/CD pipeline. Your engineers didn't write a line of this."*

**Compliance crosswalk callout:**

> "87% NIST 800-53 coverage — automatically crosswalked: FedRAMP Moderate at 82%, CMMC Level 2 at 91%. The same run, three frameworks."

**Close:**

> *"From 47 findings and 3 attack paths to 0 CAT1 and posture A — automated, auditable, repeatable. This design is cATO-ready today."*

---

## Competitive Talking Points

### The Core ROI Story

| Metric | ICDEV™ SDC | Manual Process |
|--------|-----------|----------------|
| Security assessment | 4 minutes (automated) | 2–3 days (assessor) |
| ISSO approval workflow | Built-in HITL gate | Email + SharePoint round-trips |
| IaC generation | Terraform + Ansible auto-generated | Weeks of engineering time |
| Compliance crosswalk | NIST / FedRAMP / CMMC simultaneous | Three separate consultant efforts |
| Evidence package | Auto-assembled at step 12 | Manual compilation, weeks |
| cATO readiness check | Real-time, continuous | Quarterly manual review |
| **Total time** | **4 hours automated** | **200 hours manual** |
| **Cost per engagement** | ~$600 (compute) | **$30,000 (200 hrs × $150/hr)** |
| **Net savings** | — | **$29,400 per engagement** |

### Competitive Differentiators vs. Alternatives

| Capability | ICDEV™ SDC | Tenable / Nessus | Manual STIG |
|-----------|-----------|-----------------|-------------|
| Attack path chaining | Yes — BFS enumeration | No | No |
| ISSO gate (HITL) | Native, auditable | No | Ad hoc |
| IaC auto-generation | Yes — Terraform + Ansible | No | No |
| cATO pipeline integration | Yes — CI/CD hooks | Partial | No |
| Multi-framework crosswalk | NIST + FedRAMP + CMMC | NIST only | Manual |
| Evidence auto-assembly | Yes | No | Manual |

---

## Hook Questions by Audience

**CISO:**
*"How long does your current ATO process take from initial assessment to ISSO sign-off? What if you could compress that from weeks to hours — with a full audit trail?"*

**Program Manager:**
*"How many FTEs do you have dedicated to compliance documentation today? What would you do with 196 hours per engagement back in the team's hands?"*

**CTO / Technical Lead:**
*"Your engineers are writing IaC manually and running STIG checklists in spreadsheets. This generates compliant Terraform from a security design canvas. What's the cost of not having this in your next ATO cycle?"*

**Contracting / Acquisition:**
*"What's the per-engagement cost of your current security assessment workflow? We're seeing $29,400 in savings per run — not annually, per engagement."*

---

## Demo Scenarios Quick Reference

| # | Scenario | Route | IQE | Punchline |
|---|---------|-------|-----|-----------|
| 1 | Red Team: 47 findings, 3 attack paths | `/security/demo` → Scenario A | `sdc_demo.threat_summary` (before) | 15 CAT1, risk 8.7, posture F |
| 2 | ISSO approval gate in action | Scenario B, enable Simulate ISSO | `sdc_demo.workflow_steps` step 4 | HITL gate: `isso-demo@agency.gov` approved |
| 3 | After state: 0 CAT1, posture A | Scenario C | `sdc_demo.threat_summary` (after) | Grade A, $29,400 saved, cATO ready |
| 4 | BFS attack path visualization | `/security/` → Attack Paths tab | `attack.paths` | Path 1: SQLi → data exfil; Path 3: cipher → hijack |
| 5 | Terraform IaC snippet | Scenario C → Expand IaC | `sdc_demo.workflow_steps` step 7 | Production-ready, NIST-tagged, GovCloud |
| 6 | Multi-framework crosswalk | Scenario C → Crosswalk tab | `sdc_demo.workflow_steps` step 11 | NIST 87%, FedRAMP 82%, CMMC 91% |
| 7 | Evidence package assembly | Scenario C → Evidence tab | `sdc_demo.runs` | Auto-assembled PDF, AO-ready |
| 8 | ROI calculator | Scenario C → ROI widget | — | 196 hrs saved, $29,400, 49× ROI multiplier |

---

## Hard Q&A Reference

| Question | 1-Line Answer |
|----------|--------------|
| IL6 / SECRET? | Same workflow runs air-gapped SIPR with NSA Type 1 encryption; Ollama local LLM only |
| Self-certification? | Machine-generates evidence for AO review — same model as SCAP/STIG tooling; AO still approves |
| Why not Tenable? | Tenable finds; SDC remediates, generates IaC, crosswalks, and packages evidence — end-to-end |
| CMMC 2.0 scope? | 91% L2 coverage across AC/AU/CM/IA/IR/SC/SI — not a C3PAO replacement |
| FedRAMP authorized? | FedRAMP-aligned controls; authorization path per agency AO decision |
| Integration with ServiceNow? | Native connector — findings, ISSO workflow, evidence all sync to ITSM tickets |
| GitLab CI/CD gate? | SDC deploy gate is a blocking merge check — 0 CAT1 required to merge |
| What if ISSO rejects? | Gate stays open; findings routed back to Step 3; full audit trail preserved |
| Data residency? | Air-gap = no egress; all processing on-prem; no PII leaves boundary |
| How do attack paths get verified? | BFS enumeration + Caldera adapter validates exploitability against live system graph |
| 47 vs. 15 CAT1? | 47 is total findings (CAT1–CAT3); 15 CAT1 are cATO blockers — all 15 resolved at Act 3 |

---

## Backup Talking Points (If Demo Fails)

- **Dashboard down:** *"The CLI version shows identical results."*
  ```bash
  python tools/sdc/demo_runner.py --scenario A --json
  python tools/sdc/demo_runner.py --scenario C --json
  ```
- **IQE widget blank:** *"Let me pull the numbers directly."*
  ```bash
  python -c "from tools.sdc.demo_runner import run_sdc_demo; import json; print(json.dumps(run_sdc_demo('A'), indent=2))"
  ```
- **ISSO gate hangs:** Uncheck Simulate ISSO → click Manual Approve button in Step 4.
- **If asked about IL6:** *"Same workflow, air-gapped SIPR environment, NSA Type 1 encryption. We can walk through the air-gap runbook after this session."*
- **If asked about integration:** *"Native connectors to ServiceNow, Jira, GitLab CI/CD, and Splunk SIEM — all bidirectional."*
- **If challenged on ROI numbers:** *"196 hours × $150 loaded labor rate — standard DoD OMB A-76 contractor rate. We can customize to your agency's rate card."*

---

*CUI // SP-CTI — Handle per ICDEV™ classification policy.*  
*Pattern: `docs/features/ai-canvas-demo-playbook.md`*
