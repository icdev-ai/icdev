# CUI // SP-CTI
# Legacy System AI Modernization Playbook — DoD/IC Reference

**Classification:** CUI // SP-CTI  
**Distribution:** Authorized ICDEV™ Users  
**Version:** 1.0 | FY2025

---

## Overview

This playbook provides prescriptive modernization guidance for the five legacy DoD systems scanned in the ICDEV™ AI Augmentation Canvas (AAC). Each section maps detected code patterns to AI paradigms, provides effort estimates, and defines success criteria for a Program Manager.

---

## 1 — GCSS-Army Logistics Core (287K LOC, Java)

**System owner:** Army G-4 / GCSS-Army PEO  
**Composite AI Augmentation Score:** 0.77  
**Recommended start:** Phase 1 Quick Wins (37 days)

### Pattern Analysis Summary

The GCSS-Army codebase contains six AI augmentation opportunities across 1,842 files. The highest-priority pattern is a 187-key maintenance decision map (`MaintenanceDecisionMap.java::resolveMaintenanceAction`, composite=0.82) — a static Java HashMap last updated in FY21Q3 that generates 34 exception tickets per year when edge cases are not covered. This is a textbook `large_rule_table → decision_agent` replacement.

### Phase 1 — Quick Wins (37 days, ~$185K)

**1a. MaintenanceDecisionMap — Decision Agent (25 days)**  
Replace the 187-key static HashMap with a Claude Sonnet decision agent backed by a RAG corpus of Army maintenance doctrine (TM manuals, DA PAM 750-series). The agent handles novel cases that the static map cannot, with HITL escalation for confidence <0.75.  
*Success criteria: Exception ticket rate <5/year, analyst satisfaction ≥4.2/5*

**1b. SupplyKeywordSearch — Embedding Search (12 days)**  
Replace the 14,200-entry NSN keyword list with a FAISS embedding index over the 6M NSN catalog. Cosine similarity retrieval returns semantic matches that exact-match cannot find (e.g., "hydraulic pump" matches "fluid power actuator").  
*Success criteria: Search recall@10 ≥ 92%, retrieval latency <100ms*

### Phase 2 — Classifier + Anomaly Detection (72 days, ~$360K)

**2a. EquipmentClassifier — ML Classifier (42 days)**  
The 43-condition, 7-level nested conditional in `classifyEquipmentStatus` was converted from PMCS paper forms in FY18. Train an XGBoost classifier on 142K labeled GCSS-Army maintenance records. SHAP explanations required for auditor review.  
*Success criteria: F1 ≥ 0.88, recall on Class 1 (failure) ≥ 0.91*

**2b. PartAvailabilityService — Anomaly Detection (30 days)**  
Replace 12 hardcoded reorder thresholds with an LSTM-based demand forecasting model trained on NSN part consumption history. Adaptive thresholds adjust for seasonal demand and supply chain disruptions.  
*Success criteria: Stockout reduction 20%, overstock reduction 15%*

### Phase 3 — LLM + Agentic (40 days, ~$200K)

**3a. ReadinessNarrativeBuilder — LLM Generation (18 days)**  
Generate AR 220-1 readiness narrative sections from structured GCSS data. Model generates FRAGO-style language; human S4 reviews before submission.  
*Success criteria: Narrative generation time <3 min vs 45 min manual*

**3b. MaintenanceAlertScheduler — Agentic Trigger (22 days)**  
Replace cron-based scheduler with an event-driven agentic trigger. The agent evaluates alert severity before firing, reducing false positive rate from 23% to <8%.  
*Success criteria: Alert false positive rate <8%, missed alert rate <1%*

---

## 2 — Legacy SIEM Rules Engine (63K LOC, Python)

**System owner:** DISA SOC  
**Composite AI Augmentation Score:** 0.85 (highest of all 5 scans)  
**Recommended start:** IOC Embedding Pilot (10 days)

### Pattern Analysis Summary

The SIEM Rules Engine is the highest-priority modernization target due to its 88K-entry IOC exact-match rule table (`sigma_mapper.py::map_sigma_rule`, composite=0.93) and 23% analyst false positive burden. The primary opportunity is replacing the static IOC list with semantic embedding search, which enables fuzzy matching, typosquatting detection, and temporal relevance weighting.

### Phase 1 — IOC Embedding Pilot (18 days, ~$90K)

**1a. sigma_mapper — Embedding Search (10 days)**  
Embed the 88K IOC list into a FAISS index. Semantic search returns top-8 matches with cosine similarity scores. This single change reduces false positives from 23% to an estimated 8% (based on DISA SOC pilot data) and enables detection of obfuscated IOCs.  
*Success criteria: False positive reduction ≥ 40%, detection of 3 known evasion patterns in regression test*

**1b. incident_narrator — LLM Generation (8 days)**  
Replace Jinja2 template-based incident report generation with Claude Sonnet. Output conforms to CISA IR format (TLP:WHITE). Reduces report drafting from 4 hours to 15 minutes.  
*Success criteria: CISA format compliance 100%, analyst review time <20 min*

### Phase 2 — Alert Classifier + Threshold Baselines (63 days, ~$315K)

**2a. pattern_classifier — ML Classifier (35 days)**  
Train a 3-class classifier (true_positive / false_positive / escalate) on 18 months of SIEM alert logs with SOC analyst verdicts. 120K alerts/day volume requires batched inference via SageMaker endpoint.  
*Success criteria: Precision ≥ 0.85 on true_positive class, escalate recall ≥ 0.92*

**2b. threshold_engine — Anomaly Detection (28 days)**  
Replace 34 hardcoded login/port-scan thresholds with unsupervised anomaly detection baselines trained on FY23–FY24 network telemetry. Dynamic thresholds adapt to business hours, maintenance windows, and user behavior patterns.  
*Success criteria: Threat detection rate +15% vs hardcoded baselines on historical test set*

---

## 3 — Defense Financial System (1.24M LOC, Java/COBOL-converted)

**System owner:** DFAS  
**Composite AI Augmentation Score:** 0.68 (highest risk of all 5 scans)  
**Recommended start:** Low-risk narrative generation first (22 days)

### Pattern Analysis Summary

The DFAS system carries the highest modernization risk due to its COBOL-converted Java codebase (test coverage 38%) and 5.2M payment audit records requiring FISCAM-compliant explanations. The `PaymentAuditClassifier.java` (78-deep nested conditionals, composite=0.82) is the highest-value opportunity but also the highest risk — recommend phased approach starting with narrative generation.

### Risk Assessment

**STOP:** Do not attempt to replace `PaymentAuditClassifier.java` in Phase 1. The COBOL-converted code has undocumented business rules embedded in deeply nested conditions. Any ML replacement requires:
1. Complete regression test suite (currently 38% coverage — must reach 80% before ML deployment)
2. 12 months of parallel run with human review of all classifier disagreements
3. FISCAM auditor sign-off on explainability (SHAP required)

### Phased Approach

**Phase 1 — Safe Wins (36 days):** `FinancialNarrativeBuilder` LLM generation (22 days) + `VendorSearchService` embedding search (14 days). Zero risk to financial processing logic.

**Phase 2 — Anomaly + Classifier (93 days):** After test coverage reaches 80% (parallel effort). `ThresholdAuditTrigger` anomaly detection then `PaymentAuditClassifier` ML replacement with 6-month parallel run.

---

## 4 — JTRS Radio Firmware (189K LOC, Rust)

**System owner:** PEO C3T  
**Composite AI Augmentation Score:** 0.74  
**Recommended start:** Non-safety-critical opportunities first

### Safety-Critical Constraint

`channel_optimizer.rs::select_optimal_channel` and `waveform_classifier.rs::classify_waveform_type` are safety-critical — they operate on classified RF spectrum with <2ms latency requirements. Any ML replacement requires:
- Hardware validation on the specific JTRS radio model (not simulation)
- Formal safety analysis (MIL-STD-882E compliant)
- NSA review of model weights before IL5 enclave deployment
- HITL mandatory for all channel selection decisions affecting joint operations

### Recommended Sequence

**Phase 1 (43 days):** Link health anomaly detection (18 days) + mesh route optimization (25 days). Neither is on the safety-critical path.

**Phase 2 (75 days):** Safety-critical classifiers after Phase 1 validates the ML pipeline. Requires formal safety gate before deployment.

---

## 5 — HR Readiness Portal (41K LOC, TypeScript/Python)

**System owner:** Army G-1  
**Composite AI Augmentation Score:** 0.78  
**Recommended start:** Phase 1 — no rights-impacting opportunities (25 days)

### Rights-Impacting Alert

`TrainingEligibilityChecker.ts::checkTrainingEligibility` (composite=0.84) is rights-impacting under OMB M-25-21 — decisions affect career progression. CAIO review and demographic parity audit are **mandatory before deployment**. Do not include this in Phase 1.

### Phase 1 — Safe (25 days, ~$125K)

Personnel skill semantic search (10 days) and readiness narrative LLM generation (15 days). Both operate on aggregated data, no individual personnel decisions.

### Phase 2 — Rights-Impacting (66 days) — CAIO Gate Required

Training eligibility classifier and eligibility rule decision agent. Both require: (1) CAIO review package, (2) demographic parity analysis, (3) HITL gate for all career-impacting decisions.

---

## Summary — Modernization Priority Matrix

| System | Score | Risk | Start Action | Phase 1 Days | ROI Signal |
|--------|-------|------|-------------|-------------|-----------|
| SIEM Rules Engine | 0.85 | LOW | IOC embedding pilot | 18 | 40% false positive reduction |
| HR Readiness Portal | 0.78 | LOW | Skill semantic search | 25 | 8,200 searches/day improved |
| GCSS-Army | 0.77 | MEDIUM | Decision map agent | 37 | 34 → <5 exception tickets/year |
| JTRS Firmware | 0.74 | MEDIUM-HIGH | Link anomaly detection | 43 | 50ms → adaptive latency budget |
| DFAS Financial | 0.68 | HIGH | Narrative generation only | 36 | 3 days → 4 hours per report cycle |

---

*CUI // SP-CTI — Handle per ICDEV™ classification policy.*
