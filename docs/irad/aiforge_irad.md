# IRAD: AI-Forge — Autonomous AI Code Synthesis for DoD Legacy Modernization

**Submitter:** Sovanna Chuon  
**Sector:** Defense / IC — Mission System Modernization

---

## Problem / Opportunity Addressed

DoD has 10,000+ legacy systems with 3–5 yr modernization backlogs. AI-ify identifies AI-ready code patterns — but stops at a report. No tool closes the detect → implement loop in an air-gapped IL environment. AI-Forge is that missing layer.

> **AI-ify finds it. AI-Forge fixes it.**

---

## Technical Goals

1. Auto-generate AI replacement code from scan output
2. Test suite validation; auto-rollback on failure
3. Agentic loop: detect → synthesize → test → HITL → commit
4. CUI-marked outputs + ATO evidence (NIST AI RMF)
5. Air-gap (Ollama) + GovCloud (Bedrock)

---

## Technical Approach

1. **Signal** — AI-ify scores API → priority queue
2. **Synthesis** — 8 pattern templates + IL-aware LLM → candidate code
3. **Validation** — Test suite run; auto-rollback on regression
4. **Integration** — HITL diff review → Git PR + ATO evidence

---

## Schedule & TRL

- **Schedule:** 07/01/2026 → 06/30/2027 (12 months)
- **TRL Start:** TRL 3 (concept; AI-ify detection foundation exists)
- **TRL End:** TRL 6 (demo with real legacy repo + HITL gate + ATO evidence package)

---

## Financial Estimates

- **Total Cost:** $480K
  - **Labor:** $380K (2.5 FTEs — ML engineer, full-stack, DevSecOps)
  - **Non-Labor:** $100K (GovCloud Bedrock credits, air-gap compute, tooling)

---

## IP / Patents / Trade Secrets

**Yes.** Trade secrets: pattern-specific synthesis template library; IL-aware model routing algorithm; composite-score-to-code-strategy mapping table. Patent candidate: closed-loop detect→synthesize→validate pipeline for IL-constrained environments.

---

## Major Milestones

| Quarter | Milestone |
|---------|-----------|
| Q1 | 8 pattern templates + IL model routing |
| Q2 | Auto-test harness + rollback; 2-repo pilot |
| Q3 | HITL dashboard; PR/kanban integration |
| Q4 | Air-gap hardening; PMO field pilot; TRL 6 |

---

## Solution Concept Diagram (OV-1)

**See:** [`aiforge_diagram.drawio`](aiforge_diagram.drawio) — open in [draw.io](https://app.diagrams.net) or VS Code draw.io extension.

Two swim lanes:
- **Row 1 (Detect and Synthesize):** AI-ify Scan → Pattern Templates → IL-aware LLM → Tests Pass? *(no loops back)*
- **Row 2 (Validate and Ship):** Bedrock or Ollama → HITL Review → Git PR → ATO Evidence

---

## Solution Concept Summary

AI-Forge closes the loop AI-ify leaves open. Where AI-ify stops at a ranked recommendation report, AI-Forge picks up — autonomously converting each high-scoring opportunity into production-ready, tested AI replacement code.

The synthesis layer holds 8 pattern-specific code templates, one for each AI-ify pattern type (nested conditionals, rule tables, keyword search, cron jobs, and more). For each opportunity scoring ≥ 0.70 composite, AI-Forge injects the original code block into the matching template and calls an IL-appropriate LLM — AWS Bedrock for IL4/IL5 GovCloud, Ollama for IL5/IL6 air-gap environments. The result is a candidate replacement plus a generated unit test skeleton.

That candidate is immediately validated against the project's existing test suite. If any test regresses, AI-Forge rolls back automatically and re-queues for a revised attempt. If tests pass, the diff lands in a HITL review dashboard where an engineer inspects the change, optionally edits it, and approves. Approval triggers a Git PR, four linked kanban tasks (Design / Implement / Test / Review), and a CUI-marked ATO evidence artifact mapped to NIST AI RMF MANAGE-1.

An agentic reflex loop continuously monitors new AI-ify scans and queues fresh opportunities — with circuit breakers capping autonomous actions at five per hour per project to ensure human oversight remains meaningful.

**Differentiator:** Commercial tools like Copilot and Tabnine are generic autocomplete. They have no pattern intelligence, no IL model routing, and no compliance trail. AI-Forge is purpose-built for the DoD modernization mission: pattern-aware synthesis, IL-constrained execution, and an unbroken audit chain from scan to ATO evidence.

**Scan Monday. Ship tested AI upgrades Friday.**

---

## Target Markets

| Target Adopter | Customer Champion | Potential Value | Sector BD Champion |
|---|---|---|---|
| DoD ACAT II/III PMOs (C2, logistics, supply chain) | DISA, Army PEO EIS | $50–200M/yr modernization cost avoidance | Defense/IT services BU |
| NAVAIR / AFLCMC sustainment programs | PEO AVN, AFLCMC/HB | $20–80M/yr per program | Defense BU |
| IC mission system owners (NRO, NGA, NSA) | IC CTO / CISO offices | Classified — high strategic value | Intelligence BU |
| Civilian agencies (IRS, SSA legacy COBOL) | GSA, OMB tech leads | $30–100M/yr | Federal civilian BU |
