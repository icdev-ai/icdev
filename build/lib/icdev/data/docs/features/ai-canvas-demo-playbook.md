# CUI // SP-CTI
# AI Canvas Demo Playbook — DoD/IC and Federal Government

**Classification:** CUI // SP-CTI  
**Audience:** ICDEV™ Demo Facilitators, Account Executives, Solutions Architects  
**Version:** 1.0 | FY2025

---

## Pre-Demo Checklist

Before any demo session:

```bash
# 1. Seed all canvas data (one-time setup)
python tools/db/seeds/seed_ai_canvases_all.py --json

# 2. Verify record counts
python -c "
from tools.agentic_ai_canvas.db.init_db import get_connection, init_db
init_db(); conn = get_connection(); cur = conn.cursor()
cur.execute('SELECT COUNT(*) FROM aadc_designs'); print('AADC designs:', cur.fetchone()[0])
cur.execute('SELECT COUNT(*) FROM aadc_assessments'); print('AADC assessments:', cur.fetchone()[0])
"

# 3. Start the dashboard
# python tools/dashboard/app.py --port 5050

# 4. Verify routes
# /aadc            → 8 designs populated
# /aimc            → 8 model designs populated
# /ai-augmentation → 5 scans with opportunities + roadmaps
# /ai-observatory  → 200 decisions, 30-day time-series charts
```

---

## Executive Demo (40 min)
**Target audience:** CIO, CISO, PEO, Program Director, CAIO

### Act 1 — The Governance Problem (5 min) | Canvas: AI Observatory

**Open:** `/ai-observatory`

**Show:** 200 AI decisions over 30 days. Point to the 18 confabulation flags in the decision type distribution. Show the confidence band chart — 18 decisions below 0.30 in the HITL queue.

**Say:** "Your agency is operating AI systems today. The question is: do you know what decisions they're making, which ones they got wrong, and which ones affect citizens' rights? This is what AI governance visibility looks like. Not 10,000 log entries — 18 decisions that need a human."

**IQE to run:** `IQE-OBS-002` — confabulation flags
**Hook question:** "How many AI systems is your org operating, and which are rights-impacting under OMB M-25-21?"

---

### Act 2 — Designing AI for Compliance (10 min) | Canvas: AADC

**Open:** `/aadc`, navigate to JADC2 Mission Planning (aadc-dod-003)

**Show:** Design graph with HITL gate, audit logger, circuit breaker nodes. Assessment panel: nist_rmf=85, owasp=78. ATO report: 2 blockers.

**Then open:** Insider Threat design (aadc-dod-002). Show `ato_ready=1`, `caio-override` node satisfying OMB M-25-21 §4.

**IQE to run:** `IQE-AADC-002` — HITL-required designs (returns 6 of 8)
**Hook question:** "Does your governance process check for rights-impacting classifications before deployment?"

---

### Act 3 — Model Selection for Classified Environments (8 min) | Canvas: AIMC

**Open:** `/aimc`, navigate to SIGINT NLP (aimc-dod-002)

**Show:** `bnd-air-gap` node + IL5 boundary. AIMC-IL-001 PASSED (100%).

**Then open:** FAR/DFARS Q&A (aimc-dod-005). Model card: 89.4% clause accuracy, 29x ROI.

**Demo:** IL filter — select IL5 → only Ollama local models remain.
**Hook question:** "When your teams evaluate AI models, does that include an IL suitability check and DoD RAI assessment?"

---

### Act 4 — Modernizing Legacy Systems (8 min) | Canvas: AAC

**Open:** `/ai-augmentation`, navigate to GCSS-Army scan

**Show:** Composite score 0.77. Navigate to `nested_conditionals` in EquipmentClassifier.java.

**Then open:** SIEM scan (composite=0.85): IOC matcher with 88K-entry list → embedding_search pilot, 10 days.

**IQE to run:** `IQE-AAC-001` — top opportunities ranked
**Hook question:** "Has anyone done a systematic analysis of where AI can replace brittle rule-based logic in your highest-cost system?"

---

### Act 5 — Unified Governance Picture (9 min) | Observatory + cross-canvas IQE

**Run:** `IQE-OBS-003` — low-confidence queue, 18 records
**Say:** "This is what your CAIO reviews — not 10K outputs, just the uncertain ones."

**Show:** Rights-impacting designs (AADC Records 2, 7 + AIMC Record 6). "One-click OMB M-25-21 inventory."

**Close:** "Your architects can design and assess AI systems this week. Your CAIO can sign off on the right ones, with confidence."

---

## Technical Demo (60 min)
**Target audience:** Enterprise Architect, DevSecOps Lead, ML Engineer

### Act 1 — AADC Architecture Deep Dive (15 min)

**Open:** `/aadc`, navigate to Threat Intel Fusion (aadc-dod-005 — 18 nodes)

**Walk:** STRIDE + ATLAS findings. Deploy gate: BLOCKED, 2 critical.
**Walk:** Lifecycle state transitions: DRAFT → REVIEW requires deploy gate pass.

**Live IQE:** `IQE-AADC-004`, `IQE-AADC-006`, `IQE-AADC-005`
**Technical hook:** "How does the `agent-isolation-boundary` node map to your current zero-trust architecture?"

---

### Act 2 — AIMC IL Compliance Engine (12 min)

**Open:** SIGINT NLP (aimc-dod-002) — `bnd-air-gap` enforcement, AIMC-IL-001 rule firing.

**Open:** FAR/DFARS Q&A (aimc-dod-005) — RAG pipeline → OpenSearch GovCloud → Bedrock. Prompt caching: 62% token reduction.

**Live IQE:** `IQE-AIMC-006`, `IQE-AIMC-007`
**Show:** AI Audit Responder (aimc-dod-008) — DoD RAI score 95/100, all 5 principles with NIST control cross-references.

---

### Act 3 — AAC Code-Level Pattern Analysis (10 min)

**Open:** SIEM scan — `large_rule_table` in `sigma_mapper.py`. Scoring breakdown: value=0.93, feasibility=0.89, risk=0.18.

**Show:** Roadmap cross-links — AIMC design for IOC embedding + AADC design for semantic search pipeline must pre-exist.

**Open:** JTRS scan — il_recommended=`qwen3-local` because IL5.
**Technical hook:** "How do you regression test a system where ground truth is encoded in the old logic?"

---

### Act 4 — RAG + KG Integration (10 min)

**Walk:** Source registry → ingestion manager → chunker → vector store factory → RAG to KG ingester.

**Show:** DoD/IC KG entity graph (38 nodes). Query: "Which AI designs governed by NIST AI RMF operate at IL5?"

**Explain:** `rag_chunks.kg_node_ids` backref. ATLAS PDF → ATLAS standard node in KG.
**Technical hook:** "For SIGINT terminology, how do you maintain a controlled vocabulary so entity extraction doesn't fragment canonical terms?"

---

### Act 5 — CI/CD Security Gates + Headless ANVIL (13 min)

**Show:** Pre-commit hook — APPEND_ONLY_TABLES, canvas AI decision audit enforcement.

**Run:**
```bash
python tools/anvil/status.py --json
python tools/dx/companion.py --sync --write --json
```

**Technical hook:** "For GitLab CI/CD, how would you integrate the AADC deploy gate as a blocking merge check?"

---

## Seed Script Run Order

```bash
# Full seed (all canvases)
python tools/db/seeds/seed_ai_canvases_all.py --json

# Individual canvases (if needed)
python tools/db/seeds/seed_ai_canvases_aadc.py        # AADC 8 designs
python tools/db/seeds/seed_ai_canvases_aimc.py        # AIMC 8 designs
python tools/db/seeds/seed_ai_canvases_aac.py         # AAC 5 scans
python tools/db/seeds/seed_ai_canvases_observatory.py # 200 decisions
python tools/db/seeds/seed_ai_canvases_kg.py          # KG 38 nodes

# Reset and reseed (full demo reset)
python tools/db/seeds/seed_ai_canvases_all.py --reset-all --json
```

---

## 10 Demo Scenarios Quick Reference

| # | Scenario | Canvas | IQE | Punchline |
|---|---------|--------|-----|-----------|
| 1 | Rights-impacting per OMB M-25-21? | AADC | IQE-AADC-002 | 2 of 8 designs, CAIO override present |
| 2 | SIGINT AI at IL5 air-gapped? | AIMC | IQE-AIMC-006 | bnd-air-gap, AIMC-IL-001 PASS, 100% |
| 3 | SIEM 88K IOC alert overload? | AAC | IQE-AAC-001 | 0.93 composite, 10-day pilot |
| 4 | Adversarial data injection exposure? | AADC | IQE-AADC-005 | ATLAS AML.T0043 HIGH, gate BLOCKED |
| 5 | ROI on FAR/DFARS AI? | AIMC | IQE-AIMC-005 | 89.4% accuracy, 29x ROI |
| 6 | DoD RAI compliance for Maven? | AIMC | IQE-AIMC-007 | DoD RAI score 95, all 5 PASS |
| 7 | GCSS-Army modernization start point? | AAC | IQE-AAC-002 | 0.82 composite, 25 days Phase 1 |
| 8 | Audit an unexplainable AI decision? | Observatory | IQE-OBS-001/002 | Full trace: design → node → confidence → flag |
| 9 | DISA LLM STIG check? | AADC | IQE-AADC-003 | 2 designs OWASP <80, specific LLM01/LLM07 |
| 10 | Build ATO package for new AI? | AADC | IQE-AADC-004 | ato_ready=1, pre-generated SSP + AI BOM |

See `context/ai_canvases/dod_ai_demo_scenarios_and_objections.md` for full scenario scripts and objection handling.

---

## Hard Q&A Reference

| Question | 1-Line Answer |
|----------|--------------|
| IL6 / SECRET? | bnd-air-gap + Ollama local; NSA Type 1 enc; SIPR only |
| Self-certification? | Automated evidence for AO review — same model as SCAP/STIG |
| Why not Copilot for Security? | Copilot detects post-hoc; AADC prevents at design time |
| CMMC 2.0 coverage? | 24 practices across AC/AU/CM/IA/IR/SC/SI; not C3PAO replacement |
| Data residency? | Air-gap = no egress; PII scrubber nodes; AI BOM documents provenance |
| Model drift? | drift-detector node + Observatory anomaly_detection + AIMC re-eval gate |
| Hallucination? | 3 layers: confidence-threshold → confabulation_flag → eval-rubric |
| OMB M-25-21 inventory? | gov-system-card node generates package; IQE-AADC-002 produces inventory |

---

*CUI // SP-CTI — Handle per ICDEV™ classification policy.*
