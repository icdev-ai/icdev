# CUI // SP-CTI
# Phase 7 — AADC Adversarial Security & Accreditation

**Canvas:** Agentic AI Design Canvas (AADC)  
**Epic key:** aadc-p7  
**Shipped:** 2026-05-04  
**Classification:** CUI // SP-CTI

---

## Summary

Phase 7 adds offensive security analysis and packaging tooling to the AADC: a 12-scenario AI red team engine mapped to MITRE ATLAS TTPs, an automated design linter with canvas overlays, and an accreditation package builder that assembles all governance artifacts into a downloadable ZIP.

---

## Features Shipped

### 1. AI Red Team Engine (`/agentic-ai/red-team/<id>`)
- 12 adversarial attack scenarios covering: prompt injection, indirect injection, jailbreak, data poisoning, model extraction, membership inference, system prompt leakage, goal hijacking, PII exfiltration, supply chain attack, adversarial perturbation, unsandboxed code execution
- Each scenario: MITRE ATLAS technique ID, exploitability score (0-10), mitigated/exposed status, missing mitigation node suggestions
- Attack surface summary: LLM/agent/tools/memory exposure flags, unsandboxed exec, missing guards
- Overall risk rating: CRITICAL / HIGH / MEDIUM / LOW
- Red Team button on every design card on index page

### 2. Design Linter (`Canvas → AADC → 💡 Lint Design`)
- 13 structural lint rules (LNT-01..13) covering Safety, Privacy, Governance, Compliance, Reliability, Observability, Supply Chain
- Per-node warnings: yellow outline + badge count on canvas nodes with issues
- Floating lint banner: score %, total issues, critical count (click to dismiss)
- Recommendations shown in simulation panel: severity, message, suggested node to add
- Results persisted in `aadc_lint_reports` table

### 3. Accreditation Package Builder (`📦 Accred` button / API)
- `GET /api/designs/<id>/accred-package` → ZIP download
- Package contents:
  - `oscal-component-<id>.json` — OSCAL 1.1 Component Definition
  - `threat-model-<id>.json` — STRIDE + ATLAS
  - `risk-register-<id>.json` — all risk items
  - `ato-checklist-<id>.json` — ATO readiness
  - `regulatory-gaps-<id>.json` — EU AI Act / DoD / OMB analysis
  - `red-team-report-<id>.json` — adversarial analysis
  - `exec-summary-<id>.json` — executive brief
  - `assessment-<id>.json` — latest NIST/OWASP assessment
  - `README.md` — cover sheet with posture, critical gaps, recommended actions

---

## New Files

| File | Purpose |
|------|---------|
| `tools/agentic_ai_canvas/red_team.py` | 12-scenario red team engine (MITRE ATLAS TTPs) |
| `tools/agentic_ai_canvas/auto_recommend.py` | Design linter (13 rules, per-node warnings) |
| `tools/agentic_ai_canvas/accred_package.py` | Accreditation ZIP builder |
| `tools/dashboard/templates/agentic_ai_canvas/red_team.html` | Red team report page |
| `tools/db/migrations/109_aadc_phase7.sql` | DDL for aadc_red_team_reports + aadc_lint_reports |

---

## New DB Tables

| Table | Purpose |
|-------|---------|
| `aadc_red_team_reports` | Red team report snapshots per design |
| `aadc_lint_reports` | Design lint report snapshots per design |

---

## New API Routes

| Method + Route | Purpose |
|----------------|---------|
| `GET /agentic-ai/red-team/<id>` | Red team report page |
| `GET /agentic-ai/api/designs/<id>/red-team` | Red team JSON |
| `GET /agentic-ai/api/designs/<id>/lint` | Design lint JSON |
| `GET /agentic-ai/api/designs/<id>/accred-package` | Accreditation ZIP download |

---

## MITRE ATLAS Coverage

| Tactic | Scenarios |
|--------|-----------|
| ML Attack Staging | RT-01 (Direct Injection), RT-02 (Indirect Injection), RT-04 (Data Poisoning) |
| ML Evasion | RT-03 (Jailbreak), RT-11 (Adversarial Perturbation) |
| Reconnaissance | RT-05 (Model Extraction), RT-06 (Membership Inference) |
| Exfiltration | RT-07 (System Prompt Leak), RT-09 (PII Exfiltration) |
| Impact | RT-08 (Goal Hijacking), RT-12 (Unsandboxed Exec) |
| Persistence | RT-10 (Supply Chain Attack) |

---

*CUI // SP-CTI — ICDEV™ AADC Phase 7*
