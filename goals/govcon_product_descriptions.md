# GovCon Proposal Writing — Product-Level Descriptions

## CUI // SP-CTI

## Overview

The GovCon proposal writing engine (Phase 59) now describes **two complete products** in proposal responses — not just individual tools or capabilities:

1. **ICDEV** — The Intelligent Coding Development Platform (the system that builds systems)
2. **Contract Performance Management Portal (CPMP)** — The post-award delivery tracking system

This document describes how the proposal engine writes about each product, what content it includes, and how the system detects when a whole-product response is appropriate.

---

## Product 1: ICDEV — The Platform

### What the Proposal Engine Writes

When an RFP requirement mentions integrated platforms, complete solutions, SDLC automation, or end-to-end development, the response drafter describes ICDEV as a **complete product delivered on-premises to the customer**:

| Aspect | What the Proposal Says |
|--------|----------------------|
| **Identity** | "ICDEV (Intelligent Coding Development) platform — a complete autonomous software development system" |
| **Architecture** | 15 specialized AI agents, 6-layer GOTCHA framework, ATLAS workflow methodology |
| **Scale** | 42 compliance frameworks, 500+ deterministic tools, 6 first-class programming languages |
| **Compliance** | Dual-hub crosswalk (NIST 800-53 + ISO 27001) — implement once, cascade everywhere |
| **Deployment** | 6 CSPs (AWS GovCloud, Azure Gov, GCP Assured, OCI Gov, IBM IC4G, local/air-gapped) |
| **Delivery Model** | Deployed on-premises to the customer — the platform IS the delivery mechanism |
| **Customer Value** | ATO in weeks not months, eliminates compliance drift, one platform replaces 10+ tools |

### Key Capabilities Mentioned in Proposals

- 42 compliance frameworks with dual-hub crosswalk
- 500+ deterministic tools for reproducible, auditable execution
- 6 programming languages with 9-step testing pipeline and TDD/BDD
- Multi-cloud IaC generation for 6 CSPs with STIG-hardened containers
- OSCAL-native compliance output with 3-layer deep validation
- Zero Trust Architecture with 7-pillar maturity scoring
- AI governance (NIST AI RMF, OMB M-25-21/M-26-04, EU AI Act, GAO)
- RICOAS requirements intake with 7-dimension readiness scoring
- Digital Program Twin with Monte Carlo simulation
- Innovation Engine for autonomous self-improvement
- GovCon Intelligence for SAM.gov capture

### Customer Value Statements

- Reduces ATO timeline from 12-18 months to weeks
- Eliminates compliance drift with cATO continuous monitoring
- Accelerates delivery with true TDD/BDD across 6 languages
- Complete audit trail (append-only, NIST AU compliant)
- Supports air-gapped IL6/SIPR with no internet dependency
- One platform replaces 10+ point solutions

---

## Product 2: Contract Performance Management Portal (CPMP)

### What the Proposal Engine Writes

When an RFP requirement mentions contract management, CDRL tracking, deliverable management, CPARS, or post-award administration, the response drafter describes the CPMP as a **complete delivery tracking product**:

| Aspect | What the Proposal Says |
|--------|----------------------|
| **Identity** | "Contract Performance Management Portal (CPMP) — a post-award delivery tracking system" |
| **CDRL Tracking** | Automatic import from Section H (DD Form 1423), frequency-based due date calculation |
| **SOW Compliance** | Extracts shall/must/will from Sections C/F/H, tracks compliance status |
| **CPARS Risk** | 4-factor weighted formula (35% overdue + 25% rejected + 25% non-compliant + 15% late) |
| **Reminders** | 30/14/7/1 day intervals with severity escalation, auto-marks overdue |
| **Dashboard** | Contracts list with CPARS color coding, contract detail with compliance bars and risk gauges |
| **Traceability** | Proposal → Contract → Deliverables — full Shipley lifecycle closure |

### Key Capabilities Mentioned in Proposals

- Automatic CDRL import from DD Form 1423 with frequency tracking
- SOW obligation extraction from Sections C, F, and H
- CPARS risk scoring (0.0-1.0) with 4 risk levels (Low/Moderate/High/Critical)
- Proactive reminders at 30/14/7/1 day intervals
- Contract lifecycle management with COR/CO contacts and POP dates
- Dashboard with compliance bars, CPARS gauges, and upcoming timelines
- Knowledge base feedback loop — delivery performance informs future proposals

### Customer Value Statements

- Eliminates manual CDRL tracking spreadsheets
- Reduces CPARS risk through proactive early warning
- Provides government CORs with real-time contractor performance visibility
- Closes Shipley lifecycle — connects proposal promises to delivery outcomes
- Improves future win rates through delivery performance feedback
- Supports FAR 42.302 contract administration

---

## How Product Detection Works

### Trigger Keywords

The response drafter detects when a shall statement needs a product-level response (instead of individual capability response) using keyword matching:

**ICDEV Platform triggers:**
- "integrated platform", "complete solution", "end-to-end", "full lifecycle"
- "software factory", "autonomous development", "unified platform"
- "development platform", "SDLC platform", "comprehensive platform"

**CPMP triggers:**
- "contract management", "CDRL", "contract data requirements", "CPARS"
- "post-award", "deliverable tracking", "contract performance"
- "obligation tracking", "DD Form 1423", "COR visibility"

### Detection Rules

1. If 2+ product trigger keywords match → use product-level template
2. If 1+ keyword matches AND domain is "management" → use product-level template
3. Otherwise → use component-level capability template

### Response Pipeline (Updated)

```
SHALL statement extracted from RFP
        │
        ▼
Product keyword detection ──── 2+ matches? ──── YES ──▶ Product template
        │                                                       │
        NO                                                      │
        │                                                       ▼
Domain-specific template                             LLM gets product context
        │                                                       │
        ▼                                                       ▼
Capability catalog match                             Draft references WHOLE PRODUCT
        │                                            (not individual capabilities)
        ▼
Knowledge base search ──── includes product_overview blocks when detected
        │
        ▼
Two-tier LLM draft (qwen3 → Claude)
        │
        ▼
Stored in proposal_section_drafts (status='draft')
        │
        ▼
Human reviews → approves → flows to proposal_sections
```

---

## Knowledge Base Content Structure

### New Categories (3 added)

| Category | Purpose | Volume Type |
|----------|---------|-------------|
| `product_overview` | Whole-product description (ICDEV, CPMP) | technical |
| `integrated_solution` | How products work end-to-end as unified systems | technical |
| `customer_value` | Benefits and ROI for government customers | management |

### Seeded Content Blocks per Product (3 each)

For each product in the catalog, `seed_from_catalog()` creates:
1. **Product overview** — full description + key capabilities + controls + evidence
2. **Customer value** — bulleted list of customer benefits
3. **Integrated solution** — how capabilities unify into a single platform

### Total Knowledge Base Content

| Source | Blocks Created |
|--------|---------------|
| 2 products × 3 blocks each | 6 product-level blocks |
| 30 capabilities × 2 blocks each | 60 capability-level blocks |
| **Total** | **66 auto-seeded blocks** |

---

## Files Modified

| File | Change |
|------|--------|
| `context/govcon/icdev_capability_catalog.json` | Added `products` array with 2 product entries (ICDEV, CPMP) |
| `tools/govcon/knowledge_base.py` | Added 3 categories; updated `seed_from_catalog()` to seed product blocks |
| `tools/govcon/response_drafter.py` | Added product templates, detection logic, LLM product context injection |
| `args/govcon_config.yaml` | Added product detection config and knowledge base categories |

---

## Management Volume Content Guidance

Per project memory, Management Volume responses **must** reference:

1. **ICDEV platform** — as the delivery mechanism (the product IS the compliance evidence)
2. **Contract Management Portal** — for post-award requirements and CDRL tracking
3. **The feedback loop** — how delivery performance informs future proposals

The response drafter now handles this automatically when Management Volume requirements trigger product-level detection.

---

## Architecture Decisions

| ID | Decision | Rationale |
|----|----------|-----------|
| D363.1 | Product entries in catalog are separate from capability entries (`products` vs `capabilities` arrays) | Products describe whole solutions; capabilities describe components. Different search/match logic. |
| D365.1 | Product detection uses keyword threshold (≥2 matches) | Prevents false positives on single-keyword matches (e.g., "cloud" alone shouldn't trigger ICDEV platform) |
| D368.1 | Three new knowledge base categories: product_overview, integrated_solution, customer_value | Distinct from capability_description — product blocks describe integrated platforms, not individual tools |
| D368.2 | Product KB blocks prepended to search results (higher priority) | When a requirement maps to a product, the whole-product context should appear first in the draft |
