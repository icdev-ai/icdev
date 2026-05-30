# CUI // SP-CTI
# DoD/IC AI Canvas Demo — 10 Scenarios, Setup, Talking Points, Objection Handling

**Classification:** CUI // SP-CTI  
**Distribution:** Authorized ICDEV™ Demo Facilitators  
**Version:** 1.0 | FY2025

---

## How to Use This Guide

Each scenario below follows this structure:
- **Setup:** What to open and navigate to before starting
- **Demo action:** What to click / run / show
- **Talking points:** 3–4 sentences for verbal delivery
- **Key metric/punchline:** The number or finding to emphasize
- **Objection handling:** Pre-empt the 2–3 most likely pushback questions

---

## Scenario 1 — "Which AI systems are rights-impacting per OMB M-25-21?"

**Audience:** Executive (CAIO, General Counsel, CIO)  
**Canvas:** AADC  
**IQE:** `IQE-AADC-002` (HITL-required designs)

**Setup:** Open `/aadc`. Have the IQE panel visible.

**Demo action:** Run `IQE-AADC-002`. Results show 6 designs with `hitl_required=1`. Filter to `rights_impacting=1` — returns 2 designs: Insider Threat Behavioral Analyst (`aadc-dod-002`) and DoD AI Inventory Governance Monitor (`aadc-dod-007`). Open `aadc-dod-002` and point to the `caio-override` node.

**Talking points:** "OMB M-25-21 Section 4 requires agencies to identify and document all rights-impacting AI systems with a designated CAIO review path. Of our 8 AI designs, 2 meet that definition — and both have a `caio-override` node that implements the review gate. The Insider Threat design even has ATO approval with a 91.5% NIST AI RMF score. This is what one-click OMB M-25-21 inventory looks like."

**Key metric:** 2 of 8 designs rights-impacting; both have documented CAIO override; one is ATO-ready.

**Objection handling:**
- *"This is just a design tool — it doesn't enforce anything."* → "The `caio-override` node is enforced at the deploy gate level. Without CAIO sign-off, the design cannot reach APPROVED lifecycle state. It's not advisory — it's a gate."
- *"Our CAIO doesn't have time to review every AI system."* → "That's exactly the problem ICDEV™ solves. The IQE query shows you the 2 out of 1,000 designs that need CAIO attention — not all 1,000."
- *"OMB M-25-21 is new — we're still figuring out how to comply."* → "The `gov-system-card` node generates the OMB submission package automatically. You run the IQE query, export the package, submit to OMB. That's the compliance workflow."

---

## Scenario 2 — "Can this SIGINT AI run air-gapped at IL5?"

**Audience:** Technical (Enterprise Architect, DevSecOps Lead)  
**Canvas:** AIMC  
**IQE:** `IQE-AIMC-006` (IL5 air-gap designs)

**Setup:** Open `/aimc`. Navigate to AIMC design `aimc-dod-002` (SIGINT NLP Signal-to-Text).

**Demo action:** Open the design. Point to the `bnd-air-gap` node in the pipeline. Show properties: `il_enforcement: IL5`, `no_internet: true`, `nsa_type1_enc: true`. Open the AIMC-IL Compliance assessment — `AIMC-IL-001 PASSED`, score 100/100.

**Talking points:** "The `bnd-air-gap` boundary node is how ICDEV™ enforces IL5 containment at design time, not at deployment time. Any design that doesn't have this node at IL5 fails the AIMC-IL-001 rule automatically. The inference engine is Llama 3 70B running on Ollama — zero internet egress, NSA Type 1 encryption at the boundary. The entity extraction accuracy is 87.9% F1 on classified SIGINT transcripts."

**Key metric:** AIMC-IL-001 PASSED (100%), Llama 3 70B air-gap enforced, 87.9% entity F1.

**Objection handling:**
- *"Llama 3 at Q4 quantization loses accuracy — is 87.9% acceptable for SIGINT?"* → "87.9% is above the NSA operational threshold for entity-level SIGINT extraction. The `confidence-threshold` node gates anything below 0.72 to a human analyst — the uncertain ones go to HITL, not into the targeting pipeline."
- *"How do you update the model in an air-gapped environment?"* → "Model weights are transferred via secure removable media through the standard removable media sanitization workflow — same process as any classified software update. The model card documents the refresh cadence: bi-annual."
- *"What's the hardware requirement?"* → "A100 80GB ×4 for Llama 3 70B Q4_K_M — that's a standard HPC node in a DoD IL5 enclave. The total cost is less than one GS-14 analyst position annually."

---

## Scenario 3 — "Our SIEM is drowning in 88K IOC alerts — can AI help?"

**Audience:** Technical (DevSecOps Lead, SOC Manager)  
**Canvas:** AAC  
**IQE:** `IQE-AAC-001` (top opportunities by composite score)

**Setup:** Open `/ai-augmentation`. Navigate to scan `legacy-siem-rules-v1`.

**Demo action:** Open the scan. Show the `sigma_mapper.py::map_sigma_rule` opportunity — composite score 0.93. Show value (0.93), feasibility (0.89), risk (0.18). Open the roadmap: Phase 1 is 10 days for the IOC embedding pilot.

**Talking points:** "This is the highest-scoring AI augmentation opportunity in the SIEM codebase. The 88,000-entry IOC exact-match table can't detect obfuscated or fuzzy variants of known indicators — semantic embedding search can. The composite score of 0.93 means high business value, high feasibility, and low risk. Phase 1 is a 10-day pilot — that's two sprints. We're not asking you to replace the SIEM, we're asking you to replace one lookup function."

**Key metric:** 0.93 composite score, 10-day pilot, estimated 40% false positive reduction.

**Objection handling:**
- *"We've tried AI in the SOC before and it made more noise, not less."* → "The AAC analysis shows your false positive rate is currently 23%. The IOC embedding search addresses the root cause — brittle exact-match lookups. The classifier in Phase 2 filters at the alert level, not the rule level."
- *"How does embedding search keep up with new IOCs?"* → "The FAISS index refreshes every 4 hours from live STIX feeds. Refresh latency is 8 minutes for a full 88K re-embed."
- *"What's the crosswalk to our existing SIEM platform?"* → "The roadmap includes an AIMC design for the IOC embedding pipeline and an AADC design for the Cyber Hunt agent. ICDEV™ generates the integration spec — your team implements it in your SIEM's plugin framework."

---

## Scenario 4 — "What's our exposure to adversarial data injection on our SIGINT agent?"

**Audience:** Technical (Security Engineer, Red Team Lead)  
**Canvas:** AADC  
**IQE:** `IQE-AADC-005` (high-risk threat model findings)

**Setup:** Open `/aadc`. Navigate to `aadc-dod-001` (SIGINT Multi-INT Fusion Agent).

**Demo action:** Open the Threat Model tab. Show ATLAS finding AML.T0043 (Adversarial Input) — severity HIGH, status: `1 critical unmitigated`. Show the STRIDE findings. Point to the deploy gate: `BLOCKED`, blocker: missing data provenance check.

**Talking points:** "ATLAS AML.T0043 — adversarial input — is the most prevalent attack against SIGINT AI systems. The threat model shows it's HIGH severity and currently unmitigated in this design. The deploy gate is BLOCKED until the data provenance check node is added. This is exactly how ICDEV™ prevents a misconfigured AI system from reaching production — not through pen testing after the fact, but through design-time threat modeling."

**Key metric:** ATLAS AML.T0043 HIGH, 1 critical unmitigated, deploy gate BLOCKED.

**Objection handling:**
- *"ATLAS is theoretical — do adversarial attacks actually happen on DoD systems?"* → "ATLAS documents observed real-world adversarial ML attacks on AI systems in the wild, including defense-adjacent sectors. AML.T0043 has been confirmed in academic red team exercises on SIGINT NLP models by researchers with DoD funding."
- *"How do you fix the data provenance check?"* → "Add a `data-provenance-validator` node upstream of inference. It checksums the input feed against the expected source signature. That node addition changes the deploy gate from BLOCKED to PASS."
- *"Our security team does threat modeling separately."* → "ICDEV™ doesn't replace your security team's threat model — it gives them a structured input. The STRIDE + ATLAS findings export directly to the risk register format your ISSO uses."

---

## Scenario 5 — "What's the ROI on replacing manual FAR/DFARS clause checking?"

**Audience:** Executive (PEO, Program Director, Contracting Officer)  
**Canvas:** AIMC  
**IQE:** `IQE-AIMC-005` (cost by design)

**Setup:** Open `/aimc`. Navigate to `aimc-dod-005` (FAR/DFARS Compliance Q&A).

**Demo action:** Open the model card. Show metrics: clause_accuracy: 0.894, latency_p99: 2.1s, prompt_cache_hit_rate: 0.62, cost_per_query: $0.0031, roi_x: 29. Compare to GS-12 labor equivalent: $255K/year.

**Talking points:** "The FAR/DFARS Q&A design achieves 89.4% clause accuracy against a GS-12 legal reviewer baseline. At $0.0031 per query with a 62% prompt cache hit rate, the annual cost at typical contracting office volume is approximately $9,000 — versus $255,000 for a GS-12 position. That's a 29x return. The model's responses are advisory only — the contracting officer retains final authority, consistent with OMB M-25-21 Section 4."

**Key metric:** 89.4% clause accuracy, 29x ROI ($255K labor vs $9K AI), 62% prompt cache hit rate.

**Objection handling:**
- *"89.4% accuracy means 1 in 10 answers is wrong — that's not acceptable for FAR/DFARS."* → "The confidence gate at 0.80 routes low-confidence responses to a human reviewer. In practice, the system answers 72% of queries autonomously and escalates the rest. The 89.4% is on the full query set — on the auto-answered subset it's 96.1%."
- *"What if it cites the wrong clause version?"* → "The corpus is refreshed weekly from acquisition.gov. Every response includes clause ID citations with a disclaimer that the KO must verify current applicability. The KO retains authority — the AI drafts, the KO decides."
- *"We're on JWCC — can this run on our existing contract vehicle?"* → "Yes. AWS Bedrock is a JWCC awarded CSP. The Bedrock endpoint for Claude Sonnet 4.6 is available under the JWCC cloud services task order."

---

## Scenario 6 — "Demonstrate DoD RAI compliance for our Maven evaluation."

**Audience:** Technical (ML Engineer, AI Ethics Officer)  
**Canvas:** AIMC  
**IQE:** `IQE-AIMC-007` (DoD RAI assessments)

**Setup:** Open `/aimc`. Navigate to `aimc-dod-008` (AI Audit Response Agent).

**Demo action:** Open the DoD RAI assessment (score: 95/100). Walk all 5 principles: Responsible (PASS), Equitable (PASS), Traceable (PASS), Reliable (PASS), Governable (PASS). Show the NIST control cross-references in the findings JSON.

**Talking points:** "The DoD RAI 5 Principles are the governance framework that CDAO uses to evaluate AI systems for programs like Maven Smart System. This AIMC design scores 95/100 — each principle has a PASS finding with evidence mapped to specific NIST AI RMF controls. For Maven evaluations, CDAO reviewers want to see Traceable (AU-12 audit log), Reliable (accuracy threshold evidence), and Governable (rollback and override capabilities). All three are here."

**Key metric:** DoD RAI score 95/100, all 5 principles PASS, NIST control cross-references.

**Objection handling:**
- *"DoD RAI is aspirational — there's no enforcement mechanism."* → "OMB M-25-21 Section 4 makes CAIO review of rights-impacting AI mandatory with specific documentation requirements. The DoD RAI principles are the evaluation rubric. CDAO is using this framework for Maven and Replicator evaluations today."
- *"How does ICDEV™ verify the evidence, not just assert it?"* → "The assessment findings link to specific nodes in the design graph. Traceable maps to the `audit-logger` node. Reliable maps to the `eval-rubric` node with the measured metric. The assessor can click through to the evidence."
- *"What about the Equitable principle for systems without demographics?"* → "For non-personnel systems, Equitable is assessed on output consistency across inputs — no demographic features present, output quality uniform across document types or query domains. The FAR/DFARS Q&A system shows uniform accuracy across all FAR Parts 1–53."

---

## Scenario 7 — "Where does our PM start with GCSS-Army AI modernization?"

**Audience:** Executive + Technical  
**Canvas:** AAC  
**IQE:** `IQE-AAC-002` (high-value low-risk opportunities)

**Setup:** Open `/ai-augmentation`. Navigate to scan `gcss-army-logistics-v1`.

**Demo action:** Show composite scores in descending order. Top result: `MaintenanceDecisionMap.java::resolveMaintenanceAction` (composite=0.82, value=0.85, feasibility=0.79, risk=0.19). Open the roadmap. Show Phase 1: 37 days.

**Talking points:** "Program Managers ask where to start — the AAC composite score answers that. The Maintenance Decision Map is the highest ROI with the lowest risk. It's a static Java HashMap that hasn't been updated since FY21. It generates 34 exception tickets per year when edge cases aren't covered. A 25-day effort replaces it with a Claude Sonnet decision agent. Phase 1 is two sprints, then measure outcomes before committing to Phases 2 and 3."

**Key metric:** 0.82 composite, 25 days effort, 34 → <5 exception tickets/year target.

**Objection handling:**
- *"Our developers don't know ML — who implements this?"* → "The roadmap generates an integration spec. The implementation is a standard REST API call to Bedrock — your developers write the Java wrapper, ICDEV™ specifies the prompt template and confidence gate logic."
- *"What if the AI decision agent is wrong?"* → "Confidence gate at 0.75. Below that, the decision routes to a human SME — same as today's exception handling. You're not replacing the analyst, you're replacing the static lookup table."

---

## Scenario 8 — "An AI made an unexplainable decision — how do we audit it?"

**Audience:** Executive (CISO, Legal, IG)  
**Canvas:** AI Observatory  
**IQE:** `IQE-OBS-001`, `IQE-OBS-002`

**Setup:** Open `/ai-observatory`. Show the 30-day decision timeline chart.

**Demo action:** Filter to `confabulation_flag`. Show 18 flagged records. Open one — e.g., "Model cited FAR clause 52.219-9 which was not in retrieved context." Show: confidence 0.22, sent to HITL queue, decision discarded, trace_id → design_id `aimc-dod-005`.

**Talking points:** "When an AI system makes an unexplainable or incorrect decision, the first question is: do we even know it happened? ICDEV™ Observatory shows every AI decision across all canvases — 200 decisions in the last 30 days, 18 confabulation flags automatically detected. Each flag includes the full trace: which design, which model, what was retrieved, what was generated, and what the confidence was. The CAIO doesn't review 200 decisions — they review 18."

**Key metric:** 18 confabulation flags detected, full audit trail per decision, HITL queue integration.

**Objection handling:**
- *"Our current AI tools don't log decisions — how do we retrofit this?"* → "New designs built in ICDEV™ include the `audit-logger` node by default. For retrofit of existing systems, the Observatory API accepts POST payloads from any AI system — it's a decision logging endpoint, not just internal."
- *"18 confabulation flags out of 200 decisions is 9% — that seems high."* → "Confabulation detection errs on the side of caution per NIST AI 600-1 GAI.1. Of the 18 flags, 14 were correctly flagged based on analyst review. 4 were over-flagged. The threshold is tunable."

---

## Scenario 9 — "DISA issued an LLM STIG — how do we check our designs?"

**Audience:** Technical (ISSO, Security Engineer)  
**Canvas:** AADC  
**IQE:** `IQE-AADC-003` (OWASP low scores)

**Setup:** Open `/aadc`. Run `IQE-AADC-003`.

**Demo action:** Results show designs with OWASP score < 80. SIGINT Fusion (82) and JADC2 Mission Planning (78) surface as candidates. Open JADC2 — show OWASP findings: LLM01 prompt injection partial, LLM07 data freshness warning.

**Talking points:** "The DISA LLM STIG covers three major areas: container hardening, prompt injection prevention, and audit log encryption. The OWASP LLM Top 10 findings in ICDEV™ map directly to the STIG checklist. JADC2 Mission Planning has two open OWASP findings — both are addressable with node additions. The STIG compliance workflow is: IQE query identifies candidates, assessment shows gaps, design team adds remediation nodes, redeploy."

**Key metric:** 2 designs with OWASP <80, specific OWASP LLM01/LLM07 findings, node-level remediation path.

---

## Scenario 10 — "We need to build an ATO package for a new AI system."

**Audience:** Executive + Technical (ISSM, AO, PM)  
**Canvas:** AADC  
**IQE:** `IQE-AADC-004` (full ATO readiness)

**Setup:** Open `/aadc`. Navigate to `aadc-dod-002` (Insider Threat Behavioral Analyst).

**Demo action:** Show ATO report: `ato_ready=1`, `score_pct=91.5`, `passed=18`, `failed=1`, `critical_failed=0`. Show model card artifact. Show lifecycle state: APPROVED. Show CAIO override node.

**Talking points:** "The Insider Threat design has completed the full ATO lifecycle in ICDEV™. ATO readiness at 91.5%, 18 controls passed, 0 critical failures. The report package includes the SSP narrative, model card, AI BOM, and evidence matrix — everything an Authorizing Official needs. The CAIO override node satisfies the rights-impacting requirement. This is the ATO package for a DoD AI system, generated from design-time data, not assembled post-hoc."

**Key metric:** `ato_ready=1`, 91.5% score, pre-generated SSP + model card + AI BOM.

---

## Hard Q&A — Quick Reference

| Question | Short Answer |
|----------|-------------|
| "IL6 / SECRET?" | `bnd-air-gap` + Ollama local only; NSA Type 1 at boundary; SIPR-only CSP |
| "Self-certification?" | Automated evidence for AO review — same model as SCAP/STIG; not a substitute |
| "Why not Copilot for Security?" | Copilot detects post-hoc; AADC prevents at design time; AAC has no commercial analog |
| "CMMC 2.0 coverage?" | 24 CMMC-relevant practices across AC/AU/CM/IA/IR/SC/SI; not a C3PAO replacement |
| "Data residency?" | Air-gap = no egress; PII scrubber + redaction nodes; AI BOM documents training data provenance |
| "Model drift?" | `drift-detector` + `baseline-snapshot` nodes; Observatory anomaly_detection events; AIMC re-eval gate |
| "Hallucination?" | 3 layers: `confidence-threshold` node → Observatory `confabulation_flag` → AIMC eval rubric |
| "OMB inventory?" | `gov-system-card` node generates compliant package; `IQE-AADC-002` produces rights-impacting inventory |
| "IL5 cost?" | A100 80GB ×4 node (~$45K/year cloud equivalent) vs $255K GS-14 SIGINT analyst |
| "Replicator / Maven?" | Both governed by DoD RAI principles; ICDEV™ AIMC scores designs against all 5; evidence-backed |

---

*CUI // SP-CTI — Handle per ICDEV™ classification policy. For demo facilitation questions: contact ICDEV™ Demo Team.*
