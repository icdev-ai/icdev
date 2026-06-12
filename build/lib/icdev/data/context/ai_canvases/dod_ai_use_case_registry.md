# CUI // SP-CTI
# DoD AI Use Case Registry — Mission Domain Reference

**Classification:** CUI // SP-CTI  
**Distribution:** Authorized ICDEV™ Users  
**Version:** 1.0 | FY2025

---

## Overview

This registry catalogs 25 operational AI use cases across DoD mission domains. Each entry maps to ICDEV™ canvas designs and AAC scan records to enable rapid demonstration of AI-first solutions for program managers, contracting officers, and mission owners.

---

## Domain 1 — Intelligence, Surveillance, and Reconnaissance (ISR)

**UC-ISR-001: SIGINT Multi-INT Fusion**  
Automates fusion of SIGINT, OSINT, and HUMINT feeds to produce prioritized target packages. Reduces analyst processing time by 65%. Maps to AADC design `aadc-dod-001`.  
*IL: IL5 | Organization: NSA, INSCOM | Pattern: multi-agent orchestration*

**UC-ISR-002: EO/SAR Change Detection**  
Automated change detection over SAR and electro-optical imagery using multi-modal vision models. Detects facility construction, vehicle movement, and infrastructure changes. Maps to AIMC design `aimc-dod-007`.  
*IL: IL5 | Organization: NGA, USAF A2 | Pattern: fine-tuned vision model*

**UC-ISR-003: Threat Actor Attribution**  
Semantic scoring of adversary TTPs against MITRE ATT&CK and STIX 2.1 bundles. Produces ranked attribution hypotheses for SOC analysts. Maps to AIMC design `aimc-dod-004`.  
*IL: IL4 | Organization: CISA, DISA SOC | Pattern: RAG over STIX bundles*

**UC-ISR-004: RF Signal Classification**  
Waveform classification for electronic warfare deconfliction. Embeds signal characteristics in sub-2ms inference for tactical radio systems. Maps to AAC scan `jtrs-radio-firmware-v1`.  
*IL: IL5 | Organization: PEO C3T, USAF A2 | Pattern: embedded ML classifier*

---

## Domain 2 — Command, Control, and Communications (C3)

**UC-C3-001: JADC2 Mission Planning Assistant**  
Agentic AI assistant providing multi-domain course-of-action (COA) generation, deconfliction, and adaptive replanning under EMCON constraints. Maps to AADC design `aadc-dod-003`.  
*IL: IL5 | Organization: Joint Staff J6, JAIC/CDAO | Pattern: multi-agent with HITL gate*

**UC-C3-002: Communications Mesh Routing Optimization**  
RL-based routing agent for degraded communications environments. Replaces 64-entry static rule table with adaptive decision agent. Maps to AAC scan `jtrs-radio-firmware-v1`.  
*IL: IL5 | Organization: PEO C3T | Pattern: decision agent*

**UC-C3-003: Link Health Anomaly Detection**  
Real-time anomaly detection on JTRS link health metrics (packet loss, jitter, RSSI). Replaces hardcoded threshold triggers with adaptive baselines.  
*IL: IL4 | Organization: DISA, Army Signal | Pattern: anomaly detection*

---

## Domain 3 — Cyber Operations and Defense

**UC-CYBER-001: Autonomous Cyber Hunt — SIEM Integration**  
AI-driven threat hunting agent integrating with SIEM for autonomous IOC correlation, alert triage, and hunt campaign orchestration. Maps to AADC design `aadc-dod-006`.  
*IL: IL4 | Organization: DISA SOC, CISA | Pattern: multi-agent cyber hunt*

**UC-CYBER-002: IOC Semantic Matching**  
Embedding-based IOC matching replacing 88K-entry exact-match rule table. Supports fuzzy matching, semantic similarity, and temporal decay. Maps to AAC scan `legacy-siem-rules-v1`.  
*IL: IL4 | Organization: DISA SOC | Pattern: embedding search*

**UC-CYBER-003: Insider Threat Behavioral Analysis**  
Behavioral pattern detection for insider threat identification using UEBA signals. ATO-ready with CAIO override for rights-impacting decisions. Maps to AADC design `aadc-dod-002`.  
*IL: IL5 | Organization: DCSA, NSA | Pattern: ML classifier + HITL*

**UC-CYBER-004: AI-Powered Incident Narration**  
LLM-generated incident response reports in CISA IR format (TLP:WHITE) from structured SIEM data. Reduces report drafting time from 4 hours to 15 minutes.  
*IL: IL4 | Organization: DISA SOC, CISA | Pattern: LLM generation*

---

## Domain 4 — Acquisition and Contracting

**UC-ACQ-001: FAR/DFARS Compliance Q&A**  
Claude Sonnet-powered RAG assistant for contracting officers. 89.4% clause accuracy. 29x ROI vs GS-12 manual review. Maps to AIMC design `aimc-dod-005` and AADC design `aadc-dod-004`.  
*IL: IL4 | Organization: DCSA, PEOs, AFARS offices | Pattern: RAG*

**UC-ACQ-002: Acquisition Document Classification**  
Automated classification of DD-250, SF-1449, and RFP documents for routing and compliance review. 94.1% accuracy. Maps to AIMC design `aimc-dod-001`.  
*IL: IL4 | Organization: Army Contracting Command, DLA | Pattern: fine-tuned classifier*

**UC-ACQ-003: Supply Chain AI Risk Assessment**  
Agentic AI design for automated supply chain AI component risk assessment, provenance verification, and SBOM analysis. Maps to AADC design `aadc-dod-008`.  
*IL: IL4 | Organization: DLA, USD(A&S) | Pattern: multi-agent*

---

## Domain 5 — Logistics and Sustainment

**UC-LOG-001: Predictive Maintenance — GCSS-Army**  
Equipment failure prediction for Army vehicles and systems. 34% reduction in unplanned maintenance. Maps to AIMC design `aimc-dod-003` and AAC scan `gcss-army-logistics-v1`.  
*IL: IL4 | Organization: Army G-4, TACOM | Pattern: fine-tuned tabular model*

**UC-LOG-002: NSN Semantic Search**  
Semantic search over 6M-entry NSN catalog replacing brittle keyword list matching. Sub-100ms retrieval for supply technicians. Maps to AAC scan `gcss-army-logistics-v1`.  
*IL: IL4 | Organization: DLA, Army G-4 | Pattern: embedding search*

**UC-LOG-003: Parts Demand Forecasting**  
ML-based demand forecasting for critical spare parts, replacing 12 hardcoded reorder thresholds with adaptive anomaly detection.  
*IL: IL4 | Organization: DLA, Army Sustainment Command | Pattern: anomaly detection*

---

## Domain 6 — Personnel and Readiness

**UC-HR-001: Personnel Readiness Forecast**  
30/60/90-day personnel readiness scoring for commanders. RIGHTS-IMPACTING — requires CAIO review per OMB M-25-21. Maps to AIMC design `aimc-dod-006`.  
*IL: IL4 | Organization: Army G-1, DCS Personnel | Pattern: fine-tuned + LLM judge*

**UC-HR-002: Training Eligibility Classifier**  
ML classifier for automated training eligibility determination. Rights-impacting — HITL mandatory for decisions affecting career progression. Maps to AAC scan `hr-readiness-portal-v1`.  
*IL: IL4 | Organization: Army G-1, ATC | Pattern: ML classifier*

**UC-HR-003: Personnel Skill Semantic Search**  
Semantic search over 124K personnel records for skill-based assignment matching. Replaces brittle 3,400-entry keyword list. Maps to AAC scan `hr-readiness-portal-v1`.  
*IL: IL4 | Organization: Army G-1, AFPC | Pattern: embedding search*

---

## Domain 7 — Finance and Audit

**UC-FIN-001: Payment Anomaly Detection**  
ML anomaly detection for improper payment identification in DFAS financial systems. Replaces 78-deep nested conditional classifier from COBOL-converted Java. Maps to AAC scan `dfs-financial-system-v1`.  
*IL: IL4 | Organization: DFAS, DoD IG | Pattern: ML classifier*

**UC-FIN-002: Financial Narrative Generation**  
LLM generation of A-136 compliant financial narrative from structured DFAS data. Reduces reporting time from 3 days to 4 hours per cycle.  
*IL: IL4 | Organization: DFAS, OUSD Comptroller | Pattern: LLM generation*

---

## Domain 8 — AI Governance and Compliance

**UC-GOV-001: DoD AI Inventory Governance Monitor**  
Agentic design for automated AI system inventory monitoring, OMB M-25-21 compliance tracking, and CAIO review orchestration. Maps to AADC design `aadc-dod-007`.  
*IL: IL4 | Organization: CDAO, DoD CIO | Pattern: multi-agent governance*

**UC-GOV-002: AI Audit Response Agent**  
RAG agent generating DoD AI audit response packages covering NIST AI RMF, DoD RAI, OMB M-25-21. DoD RAI score 95/100. Maps to AIMC design `aimc-dod-008`.  
*IL: IL4 | Organization: CDAO, DoD IG | Pattern: RAG*

**UC-GOV-003: AI Red Team Automation**  
Automated red team assessment workflow for AI systems, generating ATLAS-mapped threat findings and STRIDE diagrams. Integrates with AADC deploy gate.  
*IL: IL4 | Organization: NIST, CDAO, NSA | Pattern: multi-agent*

---

## Canvas Cross-Reference Matrix

| Use Case | AADC | AIMC | AAC | Observatory |
|----------|------|------|-----|-------------|
| UC-ISR-001 | aadc-dod-001 | — | — | aadc canvas |
| UC-ISR-002 | — | aimc-dod-007 | — | aimc canvas |
| UC-ISR-003 | — | aimc-dod-004 | — | aimc canvas |
| UC-C3-001  | aadc-dod-003 | — | — | aadc canvas |
| UC-CYBER-001 | aadc-dod-006 | — | legacy-siem | sdc canvas |
| UC-CYBER-003 | aadc-dod-002 | — | — | aadc canvas |
| UC-ACQ-001 | aadc-dod-004 | aimc-dod-005 | — | aadc+aimc |
| UC-LOG-001 | — | aimc-dod-003 | gcss-army | aimc canvas |
| UC-HR-001 | — | aimc-dod-006 | hr-readiness | aimc canvas |
| UC-GOV-001 | aadc-dod-007 | — | — | aadc canvas |
| UC-GOV-002 | — | aimc-dod-008 | — | aimc canvas |

---

*CUI // SP-CTI — Handle per ICDEV™ classification policy. See tools/classification_manager.py.*
