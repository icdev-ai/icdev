# SDC Demo Playbook — Prospect Edition (Live Build, 15 minutes)
**Audience**: New prospects — government agencies, defense contractors, SaaS vendors seeking FedRAMP
**Goal**: Design → Threat model → STIG → Terraform → Approved in one session (live build)
**Format**: Single presenter, blank canvas start

---

## Opening Hook (1 minute)

> "How long did your last ATO take? Six months? A year? I'm going to show you the same outcome in 15 minutes — live, on this system, starting from a blank canvas."

Navigate to: `http://localhost:5050/security/`

---

## Live Build — Blank Canvas to Compliant (10 minutes)

### Step 1: Drop 4 nodes (2 minutes)
1. Click **New Design** → name it "Prospect Demo — IL5 Web App"
2. Drag from templates: **WAF** (Perimeter), **Web Application** (App), **App Server** (App), **Database** (Data)
3. Connect: WAF → Web App → App Server → Database
4. Set classification: IL5 on all trust boundaries

### Step 2: Run the 12-Step Workflow (5 minutes)
Click **Run SDC Workflow** on the design panel.

Narrate each phase as it runs:
- *"Step 1-2: The system is now scanning for STRIDE threats and DISA STIG violations — automatically."*
- *"Step 4: ISSO approval gate. In your environment, this goes to your ISSO's email. Today I'll simulate it."* → Click approve
- *"Step 5: Terraform generated. KMS encryption, WAF with OWASP CRS, non-root containers — all NIST 800-53 compliant."*
- *"Step 12: Evidence package ready. This is your ATO artifact."*

### Step 3: Show the After State (3 minutes)
Switch to: `http://localhost:5050/security/demo` → Scenario C

> "This is what your system looks like after SDC runs. 0 CAT1 findings. Posture grade A. 87% NIST 800-53 coverage. cATO ready."

Type IQE query live:
```
foreach t in sdc_demo.threat_summary where t.snapshot_label == "after" where t.cat1_count == 0 select t.design_id, t.posture_grade, t.controls_implemented, t.risk_score
```

---

## Audience-Specific Closes (2 minutes)

### Government Agency (FISMA / ATO)
> "Your ATO package used to take 6–12 months and cost $500K+ in consulting. With SDC, your security engineers generate the evidence package in the same session they design the system. Your ISSO approves within 24 hours. That's not a 10% improvement — that's a different operating model."

**Call to action**: *"Let us run SDC against one of your existing system designs. We'll show you your CAT1 count and a Terraform remediation plan — in 48 hours, no commitment."*

### Defense Contractor (CMMC L2/L3)
> "CMMC assessment prep takes 6–18 months. SDC generates your CMMC L2 evidence package as a byproduct of the security design workflow. 91% practice coverage, automatically crosswalked from your NIST 800-53 controls."

**Call to action**: *"We can model your enclave boundary in SDC and show you your CMMC gap report — same day."*

### FedRAMP SaaS Vendor
> "FedRAMP Moderate requires 325 controls documented, tested, and evidenced. SDC automates 82% of that documentation. Your ISSO uploads the package, FedRAMP PMO reviews — the technical work is done."

**Call to action**: *"Your FedRAMP package starts with a security design. Let's build yours now."*

---

## Full Portfolio Close

> "SDC is one canvas in ICDEV™. Once your designs are compliant, the Network Design Canvas manages your physical infrastructure, and the AADC canvas handles your AI system governance. You get a single platform for design → compliance → operations — from IL4 to IL6."

---

## Backup Plan

If live build fails or network is slow:
1. Use the pre-seeded demo at `/security/demo` directly
2. Run `python tools/sdc/demo_runner.py --audience exec --json` in terminal
3. Show the JSON results as "the same output the UI renders"

Never apologize for using the backup — position it as: *"This is the headless API mode, used for CI/CD pipeline integration."*
