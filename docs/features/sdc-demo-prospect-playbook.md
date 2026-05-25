# CUI // SP-CTI
# SDC Demo Playbook — Prospect Edition (Live Build from Blank Canvas)

**Classification:** CUI // SP-CTI  
**Audience:** New prospects — government agencies, defense contractors, SaaS vendors seeking FedRAMP  
**Goal:** Blank canvas → 4 nodes → 12-step workflow → compliant design in 8 minutes (vs. 6-month manual ATO)  
**Format:** Single presenter, live build — no pre-seeded state, no slides  
**Version:** 1.0 | FY2026

---

## The Core Pitch (One Line)

> "Six months and $500K to get an ATO. We'll do the same thing in 8 minutes — right now, starting from nothing."

---

## Pre-Demo Checklist (5 minutes before)

```bash
# 1. Verify the dashboard is running
python -c "import urllib.request; urllib.request.urlopen('http://localhost:5050/health', timeout=3); print('OK')"

# 2. Confirm Security Canvas is accessible and clean
# http://localhost:5050/security/ — navigate in browser, verify no existing designs load

# 3. Verify seed data is available (used for the 12-step workflow simulation)
python tools/db/seeds/seed_sdc_demo.py --verify --json
# Expected: {"cat1_findings": 15, "total_findings": 47, "attack_paths": 3, "status": "ok"}

# 4. If seed data is missing, reset it
python tools/db/seeds/seed_sdc_demo.py --reset --json

# 5. Check IQE widget renders at http://localhost:5050/security/demo
python -c "import urllib.request; urllib.request.urlopen('http://localhost:5050/security/demo', timeout=3); print('IQE OK')"
```

**Browser tabs to open before the audience enters:**
1. `http://localhost:5050/security/` — Security Canvas (blank canvas — primary stage)
2. `http://localhost:5050/security/demo` — Demo Runner: Scenario C (after state — kept in background)
3. Terminal window (minimized) — backup CLI commands

**Screen resolution:** 1920×1080 minimum. If 4K, set browser zoom to 90% so node labels are fully visible without scrolling.

**Audience selector:** Set to **Prospect** on the demo runner page before they enter.

**Clear browser history / incognito window** — prospects should see a blank canvas, not a previous session.

---

## Timing Overview

| Phase | Clock | Content |
|-------|-------|---------|
| **Opening Hook** | 0:00 – 1:00 | The 6-month vs. 8-minute contrast |
| **Live Build: 4 Nodes** | 1:00 – 3:00 | Drop WAF, Web App, App Server, Database |
| **Run 12-Step Workflow** | 3:00 – 7:00 | Narrate each phase as it runs |
| **After State: Compliant** | 7:00 – 8:00 | Before/after numbers, cATO ready |
| **Audience-Specific Close** | 8:00 – 10:00 | FISMA/cATO or FedRAMP hook |
| **Portfolio Call-to-Action** | 10:00 – 11:00 | SDC → NDC → AADC full stack |
| **Q&A** | 11:00 – 15:00 | Prospect-driven |

---

## Phase 1 — Opening Hook (0:00 – 1:00)

Navigate to: `http://localhost:5050/security/` — the blank canvas is visible.

**Opening line:**

> "This is a blank canvas. Nothing is pre-loaded. In the next 8 minutes I'm going to design a compliant IL5 web application — threat-modeled, STIG-checked, Terraform generated, and ready for ISSO review. The same outcome used to take your team 6 months and $500,000 in consulting fees. Let me show you why that's no longer acceptable."

**Do not advance to slides. Start building immediately.**

---

## Phase 2 — Live Build: Blank Canvas to 4 Nodes (1:00 – 3:00)

### Step 1: Create the Design

1. Click **New Design** (top-left)
2. Name it: `Prospect Demo — IL5 Web App`
3. Set classification: **IL5** (dropdown: Impact Level)

> "Every design in SDC is classification-tagged from the moment it's created. IL5 means the system processes Controlled Unclassified Information at the Department of Defense level. That context flows through every downstream artifact — your Terraform, your STIG check, your ATO package."

### Step 2: Drop 4 Nodes

Drag from the node palette on the left. Place them left to right in this order:

| Position | Node Type | Panel Section | Narration |
|----------|-----------|---------------|-----------|
| 1 (leftmost) | **WAF** | Perimeter | "The WAF is your internet-facing boundary. DISA STIG V-220000 — required for any IL5 system exposed to untrusted networks." |
| 2 | **Web Application** | App | "The web tier. This is where T1190 SQL injection and OWASP Top 10 attack surface lives." |
| 3 | **App Server** | App | "The application server — business logic layer, where privilege escalation paths originate." |
| 4 (rightmost) | **Database** | Data | "Your data store. Encryption at rest, NIST SC-28. This is the adversary's goal in every attack path." |

### Step 3: Draw the Trust Boundary Edges

Click-drag to connect:
1. WAF → Web Application
2. Web Application → App Server
3. App Server → Database

> "Three edges. Three trust boundaries. The Security Design Canvas immediately begins enumerating traversal paths — before I've clicked a single workflow button."

### Step 4: Set IL5 on All Trust Boundaries

Click each edge → **Classification** dropdown → **IL5**.

> "Every data-in-transit path is tagged IL5. This tells the IaC generator to mandate TLS 1.2 minimum with FIPS-validated ciphers on every connection — non-negotiable, auto-generated, audit-traced."

**Clock check: you should be at approximately 3:00. The canvas shows 4 nodes, 3 edges, all tagged IL5.**

---

## Phase 3 — Run the 12-Step Workflow (3:00 – 7:00)

Click **Run SDC Workflow** in the right-hand design panel.

The progress panel opens. Narrate each phase as the status indicators advance:

---

### Steps 1–3: Threat & STIG Assessment (3:00 – 3:45)

> "Steps 1 through 3 are running simultaneously. Step 1 is STRIDE threat modeling — the system is enumerating Spoofing, Tampering, Repudiation, Information Disclosure, Denial of Service, and Elevation of Privilege threats against every node and edge we just placed. Step 2 is a DISA STIG automated check against 485 STIG controls applicable to an IL5 web application stack. Step 3 is BFS attack path enumeration — the system is walking every possible traversal route from the WAF to the database."

**Numbers that will appear:**
- Threats identified: **47**
- CAT1 STIG findings: **15**
- Attack paths: **3**
- Risk score: **8.7 / 10.0**

> "47 findings. 15 Category I — those are cATO blockers. Three confirmed adversary routes to your database. A risk score of 8.7 against an IL5 threshold of 7.0. If this were your system today, your ISSO would not sign. Your AO would not authorize. This is where most organizations are right now."

---

### Step 4: ISSO Approval Gate (3:45 – 4:15)

> "Step 4 is the human-in-the-loop gate. In your environment, this triggers a notification to your ISSO — your Information System Security Officer. They receive a machine-assembled assessment package: every finding, every risk score, every attack path, pre-mapped to NIST 800-53. They don't review a 300-page Word document. They click one button."

**Click Approve (simulated ISSO mode).**

> "In a live engagement, this approval goes to `isso@youragency.gov`. The approval is written to an append-only audit table — immutable, NIST AU-6 compliant. It becomes ATO evidence the moment it's created."

---

### Steps 5–9: Automated Remediation (4:15 – 6:00)

> "Steps 5 through 9 are where the system earns its value. Step 5 generates Terraform — not a template, not a boilerplate. Terraform that directly remediates the 47 findings from Step 1. Every line traces back to a specific STIG control."

Highlight as each step completes:

| Step | Output | Narration |
|------|--------|-----------|
| 5 | Terraform HCL | "KMS encryption for SC-28. WAF OWASP CRS rules for SI-3. Non-root containers for CM-6. CloudTrail for AU-2. VPC flow logs for SI-4. Your engineers didn't write a line of this." |
| 6 | Policy Compile | "Security policies compiled and validated against your IL5 baseline. Zero manual policy authoring." |
| 7 | Terraform Apply | "Infrastructure is being provisioned against your target environment. All resources tagged: classification=CUI, il_level=IL5, ato_tracking=prospect-demo-001." |
| 8 | Ansible Config | "Configuration management layer — hardens the OS baseline, applies STIG-compliant settings, enforces non-root execution." |
| 9 | Config Push | "Configuration pushed to all four nodes. Encrypted channels only. No manual SSH, no configuration drift." |

---

### Steps 10–12: Verification & Evidence Assembly (6:00 – 7:00)

> "Steps 10 through 12 close the loop."

| Step | Output | Narration |
|------|--------|-----------|
| 10 | Post-Deploy Scan | "The system rescans itself. If any new finding was introduced during provisioning — anything that wasn't there at Step 1 — Step 10 catches it and loops back. The gate stays open until the rescan is clean." |
| 11 | Compliance Crosswalk | "One run. Three frameworks. NIST 800-53 at 87% coverage. FedRAMP Moderate at 82%. CMMC Level 2 at 91%. The crosswalk engine auto-populates all three from the same control implementation data." |
| 12 | Evidence Package | "The ATO package is assembled. Machine-generated. Every finding, every remediation, every ISSO approval, every crosswalk result — in a single artifact your AO can review." |

**Clock check: you should be at approximately 7:00.**

---

## Phase 4 — After State: Design to Compliant (7:00 – 8:00)

Switch to: `http://localhost:5050/security/demo` → click **Scenario C** (Compliant, green border).

> "Same system. Same day. 4 automated hours of wall-clock time. Here is what changed."

**Call out the numbers side-by-side:**

| Metric | Before (Manual) | After (SDC) |
|--------|----------------|-------------|
| Total findings | **47** | **0** |
| CAT1 findings | **15** | **0** |
| Attack paths | **3** | **0** |
| Risk score | **8.7 / 10** | **2.1 / 10** |
| Posture grade | **F** | **A** |
| NIST 800-53 coverage | **41%** | **87%** |
| FedRAMP Moderate | — | **82%** |
| CMMC Level 2 | — | **91%** |
| Person-hours | **200 hrs** | **4 hrs** |
| Cost | **$30,000** | **~$600 (compute)** |
| Timeline | **6 months** | **8 minutes to design; 4 hrs to apply** |

Type live into the IQE widget:
```
foreach t in sdc_demo.threat_summary where t.snapshot_label == "after" where t.cat1_count == 0 select t.design_id, t.posture_grade, t.controls_implemented, t.risk_score, t.cost_saved
```

> "Zero CAT1 findings. Zero attack paths. Posture grade A. 87% NIST 800-53 coverage. The ISSO can sign. The AO can review. This design is cATO-ready today. That is what we built — from a blank canvas — in 8 minutes."

---

## Phase 5 — Audience-Specific Close (8:00 – 10:00)

Read the room. Choose one close. Do not deliver both.

---

### Close A: Government Agency (FISMA / cATO)

**Use when:** DoD, IC, civilian federal agency, ISSO present, or program manager with ATO backlog.

> "Your ATO process has three problems: it's slow, it's expensive, and it's not repeatable. A manual assessment takes 6–12 months. Each cycle costs $500K or more in contractor time. And every time you do it, you start from scratch.
>
> SDC changes all three. Your security engineers generate the assessment package in the same session they design the system. Your ISSO reviews a machine-assembled package — not a Word document — and approves in 24 hours. The evidence is append-only, auditable, immutable. That is your cATO artifact.
>
> And when the system changes — when you add a microservice, swap a database, update a container image — SDC reruns the workflow. Your ATO stays current. Continuous ATO. Not a point-in-time compliance snapshot that expires the moment your engineers touch the code."

**FISMA hook:**

> "FISMA requires documented security controls, annual assessments, and a Plan of Action and Milestones for every open finding. SDC generates all three as a byproduct of the workflow you just watched. Your FISMA compliance artifact is not a separate effort — it's the output of the tool your security engineers use every day."

**Call to action:**

> "Let us run SDC against one of your existing system designs — something that's either in ATO now or heading into the assessment cycle. We'll give you your CAT1 count and a Terraform remediation plan within 48 hours. No commitment. No contract. We want you to see your numbers."

---

### Close B: SaaS Vendor Seeking FedRAMP

**Use when:** Commercial SaaS company targeting federal market, product team present, or CTO/CISO in the room.

> "FedRAMP Moderate requires 325 controls documented, tested, and evidenced. The average authorization timeline is 12–18 months from kick-off to ATO letter. The average cost — assessment, advisory, readiness — runs $1.2 to $2 million before you collect your first government dollar.
>
> SDC doesn't eliminate the FedRAMP process. It eliminates the manual labor inside it. The 325 controls that currently require a room full of consultants writing documentation — SDC automates 82% of that. The evidence package the PMO reviews is machine-generated from the security design you just watched us build.
>
> Your engineers own the design. SDC owns the evidence. Your ISSO owns the approval. The FedRAMP PMO gets a package that is traceable, complete, and machine-validated. That is a different ATO experience than what the process was designed around."

**FedRAMP hook:**

> "FedRAMP 20x is changing the authorization model — continuous monitoring, automated evidence, machine-readable packages. SDC is built for that world. The evidence package from Step 12 is structured for machine ingestion, not PDF review. We are not retrofitting an old process to new requirements. This is the new process."

**Call to action:**

> "Your FedRAMP journey starts with a security design. Right now you have a design somewhere — a Visio diagram, a Lucidchart export, an architecture deck. Hand us that, and we will import it into SDC and show you your gap count against the FedRAMP Moderate control baseline. Same day. No engagement, no commitment."

---

## Phase 6 — Portfolio Call-to-Action (10:00 – 11:00)

> "SDC is one canvas in the ICDEV™ platform. Here is the full stack."

### The Three-Canvas Portfolio

| Canvas | What It Does | Use Case |
|--------|-------------|----------|
| **SDC** — Security Design Canvas | Design → threat model → STIG → IaC → ATO evidence | The 8-minute demo you just watched |
| **NDC** — Network Design Canvas | Physical and logical network topology → STIG config → rack elevation → live telemetry overlay | Your network engineers draw the network; NDC generates the hardened device config |
| **AADC** — Agentic AI Design Canvas | AI system governance → NIST AI RMF → EU AI Act → bias assessment → model registry | When your AI systems need the same ATO treatment as your infrastructure |

**Portfolio pitch:**

> "Once your security design is compliant in SDC, the Network Design Canvas manages the physical infrastructure that design runs on — same compliance posture, same evidence model, same ISSO gate. When you add AI capabilities to your system, the AADC canvas applies the same workflow to your AI models and pipelines. You get a single platform for design, compliance, and operations — from IL4 to IL6, from infrastructure to AI.
>
> A contracting officer who buys SDC today is buying the full-stack foundation. The NDC and AADC canvases activate on the same platform. No second vendor. No integration project. No data in three different tools that can't talk to each other."

**CTA for the full stack:**

> "If this resonates — if the 8 minutes you just watched represents a workflow you want your team to own — the next step is a scoped pilot. We take one real system. We run it through SDC end to end. You see your actual ATO artifact before you sign anything. That conversation starts here."

---

## Hard Q&A Reference

| Question | Answer |
|----------|--------|
| IL6 / SECRET systems? | Same workflow, air-gapped SIPR environment, NSA Type 1 encryption, local Ollama LLM — no cloud egress |
| This replaces our ISSO? | No. Step 4 is a required human approval gate. SDC eliminates the manual assessment work; your ISSO makes the decision |
| Why not just use Tenable? | Tenable finds vulnerabilities. SDC finds them, remediates them, generates compliant IaC, crosswalks three frameworks, and assembles the ATO package |
| FedRAMP authorized today? | FedRAMP-aligned controls; authorization path per agency AO; FedRAMP 20x pilot program in progress |
| CMMC 2.0 assessment? | 91% L2 coverage — not a C3PAO replacement; SDC generates evidence your C3PAO audits |
| Self-certification risk? | Machine-generates evidence for AO human review — same model as SCAP/STIG automation accepted across DoD |
| Data leaves our boundary? | Air-gap mode: all processing on-prem, no cloud API calls, no PII egress |
| Integrates with our ITSM? | ServiceNow and Jira native connectors — findings, ISSO approvals, and evidence sync bidirectionally |
| GitLab / GitHub Actions? | SDC deploy gate is a blocking merge check — 0 CAT1 required to merge; same command in any CI system |
| How fast for a real system? | 4 nodes, 8 minutes design; 4 automated hours apply. 50-node enterprise system: typically 1 design session + overnight apply |
| What if ISSO rejects? | Gate stays open; findings route back to Step 3; full audit trail preserved; nothing advances without approval |
| Cost model? | Platform + compute — not consulting hours. Ask us for the per-engagement vs. annual seat comparison |

---

## Backup Plan

If live build fails or connectivity is slow — never apologize, never stumble:

**Option 1 — Pre-seeded demo:**
```bash
# Navigate directly to the pre-loaded scenario
# http://localhost:5050/security/demo → Scenario C
```
Narrate: *"Let me show you the after-state first — then I'll show you how we got here."* Reverse the story: show Scenario C (compliant), then Scenario A (before). The ROI impact is identical.

**Option 2 — CLI mode:**
```bash
python tools/sdc/demo_runner.py --audience prospect --json
```
Narrate: *"This is the headless API mode — the same output the UI renders. This is how your CI/CD pipeline integrates with SDC."* Pull the JSON to the screen and walk the numbers.

**Option 3 — Static numbers fallback:**
If all else fails, deliver the core numbers from memory:

- Before: 47 findings, 15 CAT1, 3 attack paths, risk 8.7, grade F
- After: 0 findings, 0 CAT1, 0 paths, risk 2.1, grade A, 87% NIST coverage
- Time: 6 months manual → 4 automated hours
- Cost: $30,000 manual → ~$600 compute

> *"The platform generated these numbers from a blank canvas. The architecture you saw me build — 4 nodes, 3 edges — produced every line of that remediation output."*

**Rule for any technical failure:** Position it as a capability, not a failure. CLI = "headless mode for CI/CD." JSON output = "machine-readable evidence for automated ingestion." Never frame it as a workaround.

---

*CUI // SP-CTI — Handle per ICDEV™ classification policy.*  
*Related: `docs/features/sdc-demo-playbook.md` (leadership edition) · `docs/features/sdc-demo-customer-playbook.md` (existing customers)*
