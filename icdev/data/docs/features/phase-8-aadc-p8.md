# CUI // SP-CTI
# Phase 8 — AADC Design Intelligence & Analytics

**Canvas:** Agentic AI Design Canvas (AADC)  
**Epic key:** aadc-p8  
**Shipped:** 2026-05-03  
**Classification:** CUI // SP-CTI

---

## Summary

Phase 8 adds design intelligence and cross-portfolio analytics to the AADC: an 8-pattern architectural recognizer that classifies designs against named AI system archetypes, a cascade impact analyzer that quantifies blast radius and single points of failure per node, and a portfolio analytics dashboard that surfaces score trends, compliance drift, and pattern distribution across all designs.

---

## Features Shipped

### 1. Architectural Pattern Detector (`/agentic-ai/patterns/<id>`)
- 8 named patterns: BASIC_RAG, AGENTIC_RAG, AUTONOMOUS_AGENT, HITL_SUPERVISED, MULTI_AGENT_ORCHESTRATOR, SAFETY_FIRST, PIPELINE_CHAIN, COGNITIVE_ARCHITECTURE
- Per-pattern confidence score (0-100) with structural flag matching
- Required / bonus / penalty flag logic per pattern
- Missing-node suggestions to strengthen the dominant pattern's safety posture
- 🔍 Patterns button on every design card on index page + canvas AADC menu

### 2. Cascade Impact Analyzer (`/agentic-ai/impact/<id>`)
- Per-node analysis: blast radius (transitive downstream count), vulnerability score (1-8 by type), resilience reduction %, is_spof flag
- Single-point-of-failure detection: checks whether removing a node disconnects any predecessor from any successor
- Summary: resilience score (0-100), SPOF list, top critical nodes, overall risk level (CRITICAL/HIGH/MEDIUM/LOW)
- SPOF alert banner when SPOFs detected
- 💥 Impact button on every design card on index page + canvas AADC menu

### 3. Portfolio Analytics Dashboard (`/agentic-ai/analytics`)
- Cross-design metrics: avg score, P90 score, ATO readiness rate, improved/degraded counts (30d)
- 8-week score trend (weekly buckets by assessment timestamp)
- Dominant pattern distribution across all analyzed designs
- Red team risk level distribution (CRITICAL/HIGH/MEDIUM/LOW counts)
- Lint score distribution (Excellent ≥90 / Good ≥70 / Fair ≥50 / Poor <50)
- Risk density by domain (total risks, critical risks, risks/design ratio)
- Compliance drift table (top 10 designs by 30-day score delta)
- Top 5 designs by open critical risks
- 📈 Analytics link in index page header

---

## New Files

| File | Purpose |
|------|---------|
| `tools/agentic_ai_canvas/pattern_detector.py` | 8-pattern architectural recognizer |
| `tools/agentic_ai_canvas/impact_analyzer.py` | Cascade impact + SPOF + resilience |
| `tools/agentic_ai_canvas/analytics_engine.py` | Cross-design portfolio analytics engine |
| `tools/dashboard/templates/agentic_ai_canvas/pattern_analysis.html` | Pattern analysis page |
| `tools/dashboard/templates/agentic_ai_canvas/impact_analysis.html` | Impact analysis page |
| `tools/dashboard/templates/agentic_ai_canvas/analytics.html` | Portfolio analytics dashboard |
| `tools/db/migrations/110_aadc_phase8.sql` | DDL for aadc_pattern_reports + aadc_impact_reports |

---

## New DB Tables

| Table | Purpose |
|-------|---------|
| `aadc_pattern_reports` | Pattern detection snapshots per design |
| `aadc_impact_reports` | Cascade impact analysis snapshots per design |

---

## New API Routes

| Method + Route | Purpose |
|----------------|---------|
| `GET /agentic-ai/patterns/<id>` | Pattern analysis page |
| `GET /agentic-ai/api/designs/<id>/patterns` | Pattern detection JSON |
| `GET /agentic-ai/impact/<id>` | Impact analysis page |
| `GET /agentic-ai/api/designs/<id>/impact` | Cascade impact JSON |
| `GET /agentic-ai/analytics` | Portfolio analytics dashboard |
| `GET /agentic-ai/api/analytics` | Portfolio analytics JSON |

---

## Pattern Logic

| Pattern | Required Flags | Penalized By |
|---------|---------------|-------------|
| BASIC_RAG | llm_present + rag_present | has_agent |
| AGENTIC_RAG | llm_present + rag_present + has_agent | — |
| AUTONOMOUS_AGENT | has_agent + has_tool + high_autonomy | has_hitl |
| HITL_SUPERVISED | has_agent + has_hitl | — |
| MULTI_AGENT_ORCHESTRATOR | multi_agent + has_orchestrator | — |
| SAFETY_FIRST | safety_dominant | — |
| PIPELINE_CHAIN | llm_present + chain_present | has_agent |
| COGNITIVE_ARCHITECTURE | has_agent + has_memory + has_planning | — |

---

## Resilience Score Formula

`resilience_score = 100 - min(40, SPOF_count × 12) - min(30, high_vuln_unguarded × 8)`

Vulnerability scores by node type: HIGH_RISK (LLMs, agents, orchestrators) = 8; MEDIUM_RISK (tools, RAG stores) = 5; SAFE (guards, governance, observability) = 1; other = 3.

---

*CUI // SP-CTI — ICDEV™ AADC Phase 8*
