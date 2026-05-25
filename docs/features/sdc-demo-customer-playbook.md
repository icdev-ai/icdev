# CUI // SP-CTI
# SDC Demo Playbook — Existing Customer Edition (20 minutes)

**Classification:** CUI // SP-CTI
**Audience:** Existing ICDEV™ customers — ISSOs, Security Engineers, DevSecOps Leads
**Goal:** Deep-dive on three new SDC capabilities — ISSO approval gate, IaC generation, Attack Path IQE queries
**Format:** 2-person team — **Driver** (keyboard) + **Narrator** (story)
**Prerequisite:** Customer has completed the 15-min leadership demo and understands the before/after arc
**Version:** 1.0 | FY2026

---

## Pre-Demo Setup (5 minutes before)

```bash
# 1. Verify seed data is loaded and counts match expected
python tools/db/seeds/seed_sdc_demo.py --verify --json
# Expected: {"cat1_findings": 15, "total_findings": 47, "attack_paths": 3, "status": "ok"}

# 2. Confirm dashboard reachable
python -c "import urllib.request; urllib.request.urlopen('http://localhost:5050/health', timeout=3); print('OK')"

# 3. If data is missing
python tools/db/seeds/seed_sdc_demo.py --reset --json
```

**Audience selector:** Set to **Technical** before the session begins.

**Browser tabs to open:**
1. `http://localhost:5050/security/demo` — SDC Demo Runner (primary stage)
2. `http://localhost:5050/security/` — Security Canvas → Attack Paths tab (Section 3)
3. IQE widget visible at bottom of both pages — confirm it loads before the audience enters

**Screen check:** IQE widget must be fully visible without scrolling on 1920×1080. If not, zoom to 90%.

---

## Timing Overview

| Section | Time | Focus |
|---------|------|-------|
| **1 — Recap + ISSO Gate Deep Dive** | 0:00 – 7:00 | Step 4 mechanics, audit trail, production integration |
| **2 — IaC Generation Deep Dive** | 7:00 – 13:00 | Terraform HCL walkthrough, GovCloud tagging, CI/CD gate |
| **3 — Attack Path IQE Power Queries** | 13:00 – 20:00 | 5 existing + 6 new NL queries, live attack graph exploration |

---

## Section 1 — ISSO Approval Gate Deep Dive (0:00 – 7:00)

**Driver:** Open Scenario B, enable **Simulate ISSO**.

**Narrator opening:**
> "You've seen the before-and-after. Today we're going inside the three pieces your team will actually own: the ISSO gate, the IaC output, and the IQE query surface. Let's start at Step 4."

### Step 4 Walk-Through

Click **Step 4** in the workflow panel. Point to:

1. **Approval request payload** — system assembles the assessment package (STRIDE findings, STIG mapping, risk score) and sends it to `isso-demo@agency.gov`. In production, this is your ISSO's actual email.
2. **One-click approve link** — ISSO reviews in browser; no SharePoint, no PDF attachment. The link expires in 48 hours.
3. **Audit write** — on approval, a row is written to `sc_audit` (append-only, NIST AU-6). Even system admins cannot modify or delete it.

**Narrator:**
> "The key point for your ISSO: they're not approving a Word doc — they're approving a machine-assembled package that already cross-checked against your CAT1 list. If anything is open, the gate stays closed. There is no way to advance past Step 4 with a CAT1 finding outstanding."

### IQE Queries — Type These Live

**Existing Query 1 — ISSO Approval Audit Trail:**
> *NL: "Show me every workflow step that required ISSO sign-off."*

Type into the IQE widget:
```
foreach s in sdc_demo.workflow_steps where s.approved_by != null select s.design_id, s.step_name, s.approved_by, s.approved_at, s.status
```

Expected result: rows showing `isso-demo@agency.gov` approved Step 4 at a specific timestamp. Point out that `approved_at` is immutable — this is the audit evidence your AO will review.

**New Query 1 — Open CAT1 STIG Blockers (Gate Pre-Check):**
> *NL: "Which designs still have CAT1 findings that would block ISSO approval?"*

Type into the IQE widget:
```
foreach t in sdc_demo.threat_summary where t.snapshot_label == "before" where t.cat1_count > 0 select t.design_id, t.cat1_count, t.cat2_count, t.cat3_count, t.risk_score, t.posture_grade
```

Expected result: `demo-design-001` with 15 CAT1, risk 8.7, grade F. **Narrator:** "This is the query your ISSO would run before opening the approval link — instant confirmation of why the gate is closed."

**Hook question:** *"Does your current ISSO approval process give them a single query they can run to see exactly what's blocking sign-off? This is that query."*

---

## Section 2 — IaC Generation Deep Dive (7:00 – 13:00)

**Driver:** Click Scenario C → expand **IaC Output** panel.

**Narrator:**
> "Step 5 is where the system earns its keep. The Terraform output you're looking at was generated from your STRIDE graph — not a template, not a boilerplate. It reflects the specific findings from your design."

### Terraform HCL Walkthrough

Point to the expanded `module "sdc_security_baseline"` snippet:

| Terraform Block | Why It's There | NIST Control |
|-----------------|---------------|--------------|
| `server_side_encryption = "aws:kms"` | Unencrypted edges flagged at Step 1 | SC-28 |
| `waf_enabled = true` | T1190 SQLi detected in attack paths | SI-3 |
| `container_non_root = true` | Privilege escalation path identified | CM-6 |
| `cloudtrail_enabled = true` | Audit evidence requirement | AU-2 |
| `vpc_flow_logs = true` | Lateral movement detection | SI-4 |

**Narrator:**
> "Every line in this Terraform traces back to a specific finding. It's not aspirational — it's a direct remediation of the 47 items from Scenario A."

### GovCloud Tagging

Scroll down to the `tags` block:
```hcl
tags = {
  classification = "CUI"
  il_level       = "IL5"
  nist_baseline  = "moderate"
  ato_tracking   = "demo-design-001"
}
```

**Narrator:**
> "Every resource is tagged with classification level and IL level. Your cloud team can write a policy that blocks any untagged resource from deploying. The tagging is non-negotiable — it's baked into the IaC generator at `tools/sdc/iac_generator.py`."

### CI/CD Gate Integration

**Narrator:**
> "The Terraform doesn't go anywhere without clearing the security gate. The GitLab CI step is a blocking merge check — 0 CAT1 required. If a finding is introduced between design and deploy, the pipeline stops."

Show: `tools/sdc/deploy_gate.py` — the merge check that SDC injects into GitLab CI.

### IQE Queries — Type These Live

**New Query 2 — IL5-Ready Designs (cATO Gate Check):**
> *NL: "Show me every design that is cleared for IL5 authorization — zero CAT1, grade A."*

Type into the IQE widget:
```
foreach t in sdc_demo.threat_summary where t.snapshot_label == "after" where t.cat1_count == 0 where t.posture_grade == "A" select t.design_id, t.posture_grade, t.controls_implemented, t.controls_total, t.risk_score
```

Expected: `demo-design-001` showing grade A, 74/85 controls implemented, risk 2.1. **Narrator:** "This is your cATO dashboard query — any design appearing here is cleared for AO review."

**New Query 3 — Remediation ROI (Before vs. After Hours):**
> *NL: "Compare remediation hours before and after — show me the automation ROI."*

Type into the IQE widget:
```
foreach t in sdc_demo.threat_summary where t.design_id == "demo-design-001" select t.snapshot_label, t.remediation_hours, t.cat1_count, t.cat2_count, t.posture_grade
```

Expected: before row (200h, 15 CAT1, grade F) vs. after row (4h, 0 CAT1, grade A). **Narrator:** "196 hours saved in a single run. At your agency's loaded labor rate, this is the number your acquisition team needs to justify the platform investment."

**Hook question:** *"What does your current Terraform review cycle look like — is your ISSO seeing this output before or after it hits production?"*

---

## Section 3 — Attack Path IQE Power Queries (13:00 – 20:00)

**Driver:** Click Security Canvas → **Attack Paths** tab.

**Narrator:**
> "This is the piece that's new since Phase 1 shipped. The attack graph is queryable in natural language — you don't need to write SQL or know the schema. Let me show you all 11 queries your team should have in their toolkit."

---

### The 5 Existing Attack Path Queries

These are the seed queries shipped with the Attack Path Twin. Type each one, pause for the result, then explain.

---

**Existing Query 2 — Data Exfiltration Paths:**
> *NL: "Find all critical-risk unencrypted edges that could be used to exfiltrate data."*

```
foreach e in attack.edges where e.risk_score >= 8 where e.encrypted == false select e.snapshot_id, e.component_id, e.source, e.target, e.risk_score, e.encrypted
```

**What to say:** "Risk score 8+ with no encryption — these are the edges where data exits the boundary without protection. In the demo graph: `internet → web_app` (score 8.2) and `web_app → db` (score 9.1). Path 1 in the attack path overlay corresponds to this result."

---

**Existing Query 3 — Lateral Movement to IL5:**
> *NL: "Show me every edge that reaches an IL5-classified component — any lateral movement crossing the trust boundary."*

```
foreach e in attack.edges where e.target_il_level >= 5 select e.snapshot_id, e.component_id, e.source, e.target, e.risk_score, e.target_il_level
```

**What to say:** "If any row comes back here, an adversary can reach your IL5 asset. Your security gate blocks deploy until this query returns zero rows."

---

**Existing Query 4 — Privilege Escalation Exposure:**
> *NL: "Which service nodes are running as root and are reachable through the attack graph?"*

```
foreach n in attack.nodes where n.node_type == "service" where n.privilege == "root" select n.snapshot_id, n.component_id, n.id, n.label, n.node_type, n.privilege
```

**What to say:** "Root-privilege services in the attack graph are T1068 candidates — privilege escalation from user space. The Terraform output from Section 2 sets `container_non_root = true` specifically to eliminate these nodes."

---

**Existing Query 5 — Cross-Boundary Traversal (Unencrypted + Unauthenticated):**
> *NL: "Find all edges that cross a classification boundary with neither encryption nor authentication."*

```
foreach e in attack.edges where e.encrypted == false where e.authenticated == false select e.snapshot_id, e.component_id, e.source, e.target, e.encrypted, e.authenticated, e.risk_score
```

**What to say:** "No encryption AND no authentication in a single hop — this is the worst-case boundary crossing. In the demo: `internet → web_app` qualifies. NIST SC-8 and SC-17 are directly implicated."

---

**Existing Query 6 — MTTR Critical Paths:**
> *NL: "What is the shortest multi-hop attack chain from the internet to the primary database? How many stages must an adversary compromise?"*

```
foreach p in attack.paths("internet", "db_server") where p.hops > 1 select p.src, p.goal, p.hops
```

**What to say:** "Hops > 1 means a compound technique chain — harder to detect, longer to remediate. A 2-hop chain takes roughly 2× longer to remediate than a direct path. Your blue team uses this to prioritize detection rule coverage."

---

### The 6 New Deep-Dive Queries

These go beyond the seed queries for existing customers who want to build their own detection and reporting workflows.

---

**New Query 4 — All High-Risk Edges (Risk ≥ 7.0):**
> *NL: "Give me everything above the ISSO approval threshold — not just critical, but all high-risk traversal paths."*

```
foreach e in attack.edges where e.risk_score >= 7.0 select e.snapshot_id, e.component_id, e.source, e.target, e.risk_score, e.encrypted, e.authenticated
```

**What to say:** "The seed data exfil query uses 8.0 (critical only). This drops to 7.0 — the ISSO approval threshold. Any edge at 7.0+ keeps the gate closed. Use this as your pre-approval sweep before calling the ISSO."

---

**New Query 5 — CAT1 Reduction Across All States:**
> *NL: "Compare CAT1 counts and posture grades across before and after states for all designs — show the full remediation arc."*

```
foreach t in sdc_demo.threat_summary where t.cat1_count >= 0 select t.design_id, t.snapshot_label, t.cat1_count, t.posture_grade, t.risk_score
```

**What to say:** "This is the executive reporting query — before vs. after in a single result set. You can export this directly into your quarterly security posture report. Four columns, full story."

---

**New Query 6 — Control Coverage Gaps (Below IL5 Threshold):**
> *NL: "Which designs have less than 50% of required IL5 controls implemented — flag everything that would block cATO authorization?"*

```
foreach t in sdc_demo.threat_summary where t.snapshot_label == "before" where t.controls_implemented <= 42 select t.design_id, t.controls_implemented, t.controls_total, t.cat1_count, t.posture_grade
```

**What to say:** "42 is the 50% threshold for an 85-control IL5 baseline. Any design below 42 controls implemented cannot achieve cATO. This is your early-warning query — run it weekly to catch drift before it becomes a gate failure."

---

**New Query 7 — Posture Grade Trend (Full Arc):**
> *NL: "Show me the compliance story arc from before to after — grade, risk score, and controls implemented on a single design."*

```
foreach t in sdc_demo.threat_summary where t.design_id == "demo-design-001" select t.snapshot_label, t.posture_grade, t.risk_score, t.controls_implemented
```

**What to say:** "Two rows: before (F, 8.7, 34 controls) and after (A, 2.1, 74 controls). This is the chart your program manager wants — F to A in a single automated run. Export and drop into your PIR."

---

**New Query 8 — ISSO Workflow Approval Audit (Full Context):**
> *NL: "Pull the complete ISSO approval audit trail — who approved what step, on which design, at what time."*

```
foreach s in sdc_demo.workflow_steps where s.approved_by != null select s.design_id, s.step_name, s.approved_by, s.approved_at, s.status
```

**What to say:** "This is the query your auditor runs during the ATO evidence review. Every approval — `isso-demo@agency.gov`, Step 4, timestamp — is append-only in `sc_audit`. It cannot be altered. You can hand this result set directly to your AO as machine-generated evidence."

---

**New Query 9 — Compliance Crosswalk Snapshot:**
> *NL: "Show me the before and after NIST control implementation counts side by side — how much coverage did the automated workflow add?"*

```
foreach t in sdc_demo.threat_summary where t.design_id == "demo-design-001" select t.snapshot_label, t.controls_implemented, t.controls_total, t.posture_grade, t.risk_score
```

**What to say:** "34 controls before. 74 after. 40 controls implemented automatically in a single SDC run. The crosswalk engine simultaneously maps those 74 controls to FedRAMP Moderate and CMMC Level 2 — one run, three frameworks. That's the number your acquisition team takes to the table."

---

## IQE Quick-Reference Card

Cut and keep — the exact text to type for each query during the demo.

| # | Query Name | NL Prompt | First word |
|---|-----------|-----------|------------|
| **Existing 1** | ISSO Approval Audit | "ISSO sign-offs with timestamp" | `foreach s in sdc_demo.workflow_steps where s.approved_by != null...` |
| **Existing 2** | Data Exfiltration Paths | "Critical unencrypted edges risk ≥ 8" | `foreach e in attack.edges where e.risk_score >= 8 where e.encrypted == false...` |
| **Existing 3** | Lateral to IL5 | "Edges reaching IL5 targets" | `foreach e in attack.edges where e.target_il_level >= 5...` |
| **Existing 4** | Privilege Escalation | "Root-privilege service nodes in graph" | `foreach n in attack.nodes where n.node_type == "service" where n.privilege == "root"...` |
| **Existing 5** | Cross-Boundary Traversal | "Unencrypted + unauthenticated boundary hops" | `foreach e in attack.edges where e.encrypted == false where e.authenticated == false...` |
| **Existing 6** | MTTR Critical Paths | "Multi-hop internet-to-db chains" | `foreach p in attack.paths("internet", "db_server") where p.hops > 1...` |
| **New 1** | Open CAT1 Blockers | "CAT1 findings blocking ISSO gate" | `foreach t in sdc_demo.threat_summary where t.snapshot_label == "before" where t.cat1_count > 0...` |
| **New 2** | IL5-Ready Designs | "Designs cleared for IL5 authorization" | `foreach t in sdc_demo.threat_summary where t.snapshot_label == "after" where t.cat1_count == 0 where t.posture_grade == "A"...` |
| **New 3** | Remediation ROI | "Automation hours saved before vs after" | `foreach t in sdc_demo.threat_summary where t.design_id == "demo-design-001" select t.snapshot_label, t.remediation_hours...` |
| **New 4** | All High-Risk Edges | "All edges above ISSO threshold risk ≥ 7" | `foreach e in attack.edges where e.risk_score >= 7.0...` |
| **New 5** | CAT1 Reduction Arc | "CAT1 counts across all states" | `foreach t in sdc_demo.threat_summary where t.cat1_count >= 0...` |
| **New 6** | Control Coverage Gaps | "Designs below 50% IL5 control threshold" | `foreach t in sdc_demo.threat_summary where t.snapshot_label == "before" where t.controls_implemented <= 42...` |
| **New 7** | Posture Grade Trend | "Full compliance arc grade + risk + controls" | `foreach t in sdc_demo.threat_summary where t.design_id == "demo-design-001" select t.snapshot_label, t.posture_grade...` |
| **New 8** | ISSO Audit Full Context | "Complete ISSO audit trail by design" | `foreach s in sdc_demo.workflow_steps where s.approved_by != null select s.design_id, s.step_name...` |
| **New 9** | Crosswalk Snapshot | "Controls implemented before vs after" | `foreach t in sdc_demo.threat_summary where t.design_id == "demo-design-001" select t.snapshot_label, t.controls_implemented, t.controls_total...` |

---

## Q&A Anticipation — Technical Audience

**"Can we write our own IQE queries for custom collections?"**
> Yes. Define a collection adapter in `tools/iqe/adapters/<your_canvas>.py`, register it in `tools/iqe/executor.py`, add seed queries to `context/iqe/queries/<your_canvas>/`. The parser and executor are the same — only the collection source changes.

**"How does the IQE widget handle large attack graphs — say 500+ nodes?"**
> BFS runs in-memory on the API server; the widget streams results. Graphs ≥ 200 nodes trigger automatic path pruning (`max_paths=50`) to keep response time under 5 seconds. Configure at `args/iqe_config.yaml`: `attack_paths.max_paths`.

**"The ISSO gate — is that a webhook or does it need a running server?"**
> `tools/sdc/isso_gate.py` exposes `POST /security/api/isso-approve`. You can wrap it with a ServiceNow webhook, a GitLab CI trigger, or call it directly. The approval writes to `sc_audit` regardless of which path triggers it — all three produce the same immutable audit row.

**"Can the attack path query filter by a specific design, not just all snapshots?"**
> Yes. Add `where e.component_id == "your-design-id"` to any `attack.edges` or `attack.nodes` query. The `component_id` column is the SDC design ID.

**"Does the IaC generator support OpenTofu / Terragrunt?"**
> Terraform HCL is the current output. OpenTofu is drop-in compatible (same HCL). Terragrunt wrapper generation is on the Phase 2 backlog — `args/iac_config.yaml` has the feature flag (`terragrunt_wrapper: false`).

**"What happens if Caldera is offline during a demo?"**
> `CalderaAdapter` degrades gracefully — attack paths still enumerate via BFS; ATT&CK technique IDs are omitted from the overlay. The five seed IQE queries are unaffected (they query the local DB, not Caldera).

**"How do I export IQE results to CSV for the evidence package?"**
> IQE widget has an **Export** button (top-right of result table). CLI: `python -c "from tools.iqe.executor import Executor; ..." | python -m csv`. The evidence assembler at Step 12 auto-includes all IQE results run during that workflow session.

---

## Backup Commands (If Demo Fails)

```bash
# Dashboard down — run scenarios from CLI
python tools/sdc/demo_runner.py --scenario A --json
python tools/sdc/demo_runner.py --scenario C --json

# IQE widget blank — pull attack path results directly
python -c "
from tools.iqe.executor import Executor
from tools.iqe.parser import parse
from tools.iqe.adapters.security import edges_adapter, nodes_adapter
from tools.db.storage import get_connection
ex = Executor()
ex.register_collection('attack.edges', edges_adapter)
ex.register_collection('attack.nodes', nodes_adapter)
q = parse(open('context/iqe/queries/security/data_exfil_paths.iqe').read())
with get_connection() as conn:
    import json; print(json.dumps(ex.run(q, conn), indent=2))
"

# ISSO gate hangs — uncheck Simulate ISSO, click Manual Approve in Step 4 panel

# Caldera offline — attack paths still show; narrate 'ATT&CK enrichment requires live Caldera'
python -c "
from tools.security_canvas.caldera_adapter import CalderaAdapter
a = CalderaAdapter('http://localhost:8888', api_key='ADMIN123')
print(a.health())
"
```

---

*CUI // SP-CTI — Handle per ICDEV™ classification policy.*
*Pattern: `docs/features/phase-sdc-attackpath.md`*
