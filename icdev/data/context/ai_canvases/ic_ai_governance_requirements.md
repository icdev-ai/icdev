# CUI // SP-CTI
# IC AI Governance Requirements — Beyond OMB M-25-21

**Classification:** CUI // SP-CTI  
**Distribution:** Authorized ICDEV™ Users  
**Version:** 1.0 | FY2025

---

## Overview

Intelligence Community (IC) agencies operate under AI governance requirements that extend beyond OMB M-25-21. This document consolidates IC Directive 205 (AI Governance), ODNI AI Principles, SIGINT-specific HITL requirements, and NSA cybersecurity integration mandates for AI systems operating at IL4–IL6.

ICDEV™ canvas designs that process IC-controlled information must satisfy these requirements in addition to the base OMB M-25-21 checklist.

---

## 1 — ODNI AI Principles (ICD 205 Alignment)

The ODNI issued Intelligence Community AI Principles in 2021, aligned with the DoD AI Ethics Principles but with IC-specific extensions:

**Principle 1 — Mission Value:** AI systems must demonstrably support IC mission objectives. ICDEV™ AADC designs must include a `primary_use_case` tag and a mission-value rationale in the assessment panel.

**Principle 2 — Analytic Standards:** AI-generated analysis must meet IC Analytic Standards (ICD 203) — clear sourcing, appropriate uncertainty language, and distinguishable from human-authored product. Observable in AIMC model card: `output_format` field must include disclaimer language.

**Principle 3 — Human Review:** For AI systems with decision authority over human-reviewable intelligence, a HITL gate is mandatory regardless of confidence level. AADC designs must include `hitl_required=1` and a `hitl-gate` node in the graph.

**Principle 4 — Oversight and Accountability:** IC Component AI Officers (AIOs) must maintain an AI inventory consistent with IC Directive 205. ICDEV™ Observatory provides the inventory view via `IQE-OBS-002` (rights-impacting designs).

**Principle 5 — Privacy and Civil Liberties:** AI systems processing domestic communications metadata require Privacy and Civil Liberties Officer (PCLO) review before deployment. AADC `rights_impacting=1` flag triggers this review path.

---

## 2 — ICD 205 Implementation Requirements

ICD 205 (Intelligence Community AI Governance) establishes five implementation requirements for IC AI systems:

**Req 2.1 — AI Inventory Submission:** Each IC element must maintain and submit an AI inventory to ODNI quarterly. ICDEV™ Observatory `IQE-OBS-002` generates the rights-impacting inventory view. The `gov-system-card` node in AADC produces the submission package.

**Req 2.2 — Risk Assessment:** IC AI systems require a risk assessment addressing: analytic accuracy, bias potential, adversarial robustness, and supply chain provenance. AADC assessment panel covers all four areas across NIST AI RMF + OWASP + DoD RAI frameworks.

**Req 2.3 — Testing and Evaluation:** AI systems at IL4+ require Independent Verification and Validation (IV&V) before deployment to production. AADC deploy gate enforces IV&V sign-off as a blocker condition.

**Req 2.4 — Incident Reporting:** AI system failures (confabulation, incorrect decisions, adversarial incidents) must be reported through the IC AI incident reporting mechanism within 72 hours. Observatory confabulation flags (18 in demo corpus) feed this pipeline.

**Req 2.5 — Continuous Monitoring:** AI systems in production require quarterly performance reviews. AIMC model cards document refresh cadence. Observatory time-series charts show 30-day decision drift.

---

## 3 — SIGINT-Specific HITL Requirements

SIGINT AI systems have the most stringent HITL requirements in the IC due to statutory restrictions under 50 U.S.C. § 1881 (Section 702, FISA) and Executive Order 12333:

**SIGINT-HITL-001:** Any AI system that generates targeting packages from SIGINT data requires human analyst review before dissemination, regardless of confidence score. The `hitl-gate` node in AADC design `aadc-dod-001` implements this requirement.

**SIGINT-HITL-002:** Automated SIGINT collection task orders require two-person review. AI systems may recommend but not execute collection without NSA AO sign-off. AADC `autonomy_max=2` (Level 2 — recommends, does not execute) enforces this.

**SIGINT-HITL-003:** AI systems processing US Person data under FISA authorities require a `PCLO-review` node in the AADC design, documenting the minimization procedure applied. Missing this node is an ATO blocker.

**SIGINT-HITL-004:** Confidence gates for SIGINT NLP tasks must be set at ≥0.72 minimum before human review is triggered. Lower confidence thresholds create analyst review burden without meaningful quality bar.

**Design implication:** AIMC design `aimc-dod-002` (SIGINT NLP) implements these requirements through the `confidence-threshold` node (0.72) and `bnd-air-gap` boundary enforcement. AADC design `aadc-dod-001` (SIGINT Fusion) carries `hitl_required=1` and `autonomy_max=2`.

---

## 4 — NSA Cybersecurity AI Integration Mandates

NSA's Cybersecurity Directorate has issued AI integration guidance for IC systems:

**NSA-AI-001 — Model Provenance:** AI models deployed in IC enclaves must have documented provenance (training data source, base model lineage, fine-tuning procedures). The AIMC model card `training_data` and `base_model` fields satisfy this requirement.

**NSA-AI-002 — Supply Chain Vetting:** Third-party AI models used in classified environments require NSA Technology Transfer Program (TTP) review. Commercial cloud models (Bedrock, Azure OpenAI) require FedRAMP High + DoD IL4/IL5 accreditation. AIMC `il_suitability` and `approved_environments` fields document compliance.

**NSA-AI-003 — Air-Gap Requirement (IL5+):** AI inference for IL5+ classified data must not traverse unclassified network paths. The `bnd-air-gap` node in AADC/AIMC designs enforces this. Any design at IL5+ without `bnd-air-gap` fails the AIMC-IL-001 compliance check.

**NSA-AI-004 — Adversarial Robustness:** AI systems processing SIGINT, GEOINT, or MASINT data require adversarial robustness testing per ATLAS framework. AADC threat model must include ATLAS findings; zero unmitigated critical findings required for ATO.

**NSA-AI-005 — Encryption at Rest and Transit:** AI model weights and training data stored in IC enclaves must use NSA Type 1 encryption or approved commercial crypto at IL6. The `bnd-air-gap` node properties document encryption enforcement.

---

## 5 — IL4 vs IL5 vs IL6 AI System Decision Matrix

| Criteria | IL4 (CUI) | IL5 (CUI/Nat Sec) | IL6 (SECRET) |
|----------|-----------|-------------------|--------------|
| Cloud deployment | FedRAMP High CSP | DoD-authorized CSP only | SIPR only |
| Model inference | AWS Bedrock / Azure OAI GovCloud | Ollama air-gap preferred | NSA Type 1 only |
| HITL requirement | Confidence-based | Mandatory for dissemination | Always |
| Audit retention | 365 days | 7 years | 25 years |
| Red team requirement | Optional (recommended) | Required before APPROVED | Required; NSA review |
| Rights-impacting review | CAIO sign-off | CAIO + AIO sign-off | CAIO + AIO + GC |
| Air-gap node | Not required | Strongly recommended | Mandatory |
| Training data provenance | Model card | NSA-AI-001 documented | NSA TTP review |

---

## 6 — IC AI Governance Checklist for AADC Designs

Before an AADC design targeting IC missions can reach APPROVED state, verify:

- [ ] `hitl_required=1` if processing human-reviewable intelligence
- [ ] `autonomy_max ≤ 2` for SIGINT collection systems
- [ ] `bnd-air-gap` node present for IL5+ designs
- [ ] ATLAS threat model complete with no unmitigated CRITICAL findings
- [ ] `caio-override` node present if `rights_impacting=1`
- [ ] NIST AI RMF score ≥ 80 for production deployment
- [ ] DoD RAI Equitable principle score ≥ 70 (CAIO HOLD if <70)
- [ ] Model card documents training data provenance and IL suitability
- [ ] ATO report shows `ato_ready=1` before APPROVED lifecycle state

---

*CUI // SP-CTI — Handle per ICDEV™ classification policy. Reference: ICD 205, ODNI AI Principles 2021, NSA Cybersecurity AI Guidance.*
