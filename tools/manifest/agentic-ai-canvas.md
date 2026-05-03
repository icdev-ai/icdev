# Tool Manifest — Agentic AI Design Canvas (AADC)

**Module:** `tools/agentic_ai_canvas/`
**Blueprint route prefix:** `/agentic-ai`
**Feature flag:** `ICDEV_AADC_ENABLED=true`
**DB:** `data/agentic_ai_canvas.db` (SQLite default; `AADC_STORAGE_BACKEND=postgresql` for PG)

---

## Tools

### `tools/agentic_ai_canvas/db/init_db.py`
Initialize AADC database — creates 9 tables, seeds 12 templates and 12 snippets.
```bash
python tools/agentic_ai_canvas/db/init_db.py
```

### `tools/agentic_ai_canvas/agentic_engine.py`
Rule-based compliance assessment engine (no LLM).
- `assess_design(design_id, graph_json, design_meta)` → full result with NIST AI RMF score, OWASP LLM score, autonomy level, MITRE ATLAS threats, findings
- `classify_autonomy(agent_node, nodes, edges)` → int 0–5
- `classify_impact(design_meta)` → (safety_impacting, rights_impacting)
- `check_nist_ai_rmf(nodes, edges, design_meta)` → (findings, score)
- `check_owasp_llm(nodes, edges)` → (findings, score)
- `map_atlas_threats(nodes)` → threat list

### `tools/agentic_ai_canvas/workflow.py`
HITL workflow + loop_engine bridge.
- `seed_hitl_templates()` — inserts AADC HITL templates into `wf_templates`
- `maybe_create_hitl_instance(design_id, safety, rights, project_id)` → wf_instance_id or None
- `get_workflow_status(design_id)` → dict or None
- `is_approved(design_id)` → bool
- `launch_to_kanban(design_id, name, graph_json, project_id)` → {loop_id, task_ids, clusters}

### `tools/agentic_ai_canvas/bus_subscriber.py`
Cross-canvas event bus integration.
- `register()` — subscribes to `sdc.topology_saved` and `odc.source_added`
- `publish_design_saved(design_id, name)` — emits `aadc.design_saved`
- `publish_agent_flagged(design_id, agent_label, level)` — emits `aadc.agent_flagged`

### `tools/agentic_ai_canvas/blueprint.py`
Flask Blueprint (`aadc_bp`) — all routes registered under `/agentic-ai`.

**Page routes:**
| Route | Template |
|-------|----------|
| `GET /agentic-ai/` | `agentic_ai_canvas/index.html` |
| `GET /agentic-ai/canvas/new` | `agentic_ai_canvas/canvas.html` |
| `GET /agentic-ai/canvas/<id>` | `agentic_ai_canvas/canvas.html` |
| `GET /agentic-ai/templates` | `agentic_ai_canvas/templates.html` |
| `GET /agentic-ai/snippets` | `agentic_ai_canvas/snippets.html` |
| `GET /agentic-ai/canvas/<id>/assessments` | `agentic_ai_canvas/assessments.html` |
| `GET /agentic-ai/canvas/<id>/artifacts` | `agentic_ai_canvas/artifacts.html` |

**API routes:**
| Method + Route | Purpose |
|----------------|---------|
| `POST /agentic-ai/api/designs` | Create new design |
| `GET /agentic-ai/api/designs/<id>` | Get design + last assessment |
| `PUT /agentic-ai/api/designs/<id>` | Save design (triggers assess + HITL + event bus) |
| `DELETE /agentic-ai/api/designs/<id>` | Delete design |
| `POST /agentic-ai/api/designs/<id>/assess` | Run rule-based assessment |
| `POST /agentic-ai/api/designs/<id>/launch` | Launch design to Kanban via loop_engine |
| `GET /agentic-ai/api/templates` | List all 12 templates |
| `POST /agentic-ai/api/templates/<tid>/apply/<did>` | Apply template → new design |
| `POST /agentic-ai/api/designs/<id>/save-as-template` | Save current design as custom template |
| `GET /agentic-ai/api/snippets` | List all 12 snippets |
| `POST /agentic-ai/api/designs/<id>/snippets/<sid>` | Insert snippet into design graph |
| `GET /agentic-ai/api/designs/<id>/artifacts` | List generated artifacts |
| `POST /agentic-ai/api/designs/<id>/artifacts` | Generate model_card / system_card / ai_bom |

---

## Database Tables

| Table | Purpose |
|-------|---------|
| `aadc_designs` | Design metadata (name, domain, classification, graph_json) |
| `aadc_assessments` | Per-assessment snapshots (NIST/OWASP scores, findings, ATLAS) |
| `aadc_templates` | 12 built-in + user custom templates with full graph JSON |
| `aadc_snippets` | 12 reusable subgraph patterns |
| `aadc_artifacts` | Generated model cards, system cards, AI BOMs (markdown) |
| `aadc_workflow_links` | Maps design_id → wf_instance_id for HITL tracking |
| `aadc_loop_links` | Maps design_id → loop_id for Kanban/loop_engine tracking |
| `aadc_audit_log` | Append-only audit trail (NIST AU-2) |
| `aadc_design_tags` | Tag associations for designs |

---

## Node Palette (60+ types across 8 categories)

| Category | Example nodes |
|----------|--------------|
| Model | LLM Inference, Foundation Model, Fine-Tuned Model, Embedding Model, VLM, STT, TTS |
| Memory | Vector Store, Chat History, Episodic Memory, Working Memory, KG Memory, Long-Term Memory |
| Agent | Autonomous Agent, HITL Agent, Orchestrator, Subagent, Researcher, Planner, Executor |
| Tool/MCP | MCP Server, Web Search, Code Executor, File System, API Connector, Database Tool |
| Data | Document Corpus, Data Pipeline, RAG Retriever, Training Dataset, Feedback Dataset |
| Safety | Guardrail, Output Filter, PII Redaction, Toxicity Filter, Rate Limiter, Circuit Breaker |
| Governance | Audit Logger, Compliance Monitor, HITL Gate, Policy Engine, Explainability Module |
| Infra | API Gateway, Message Queue, Batch Processor, Cache Layer, LLM Proxy, Telemetry Sink |

---

## Templates (12 built-in)

1. Simple RAG Q&A (L0, NIST 88%)
2. HITL-Gated RAG (L1, NIST 96%)
3. Single Autonomous Agent (L2, OWASP 80%)
4. Multi-Agent Orchestrator (L3, NIST 79%)
5. Agentic Research Pipeline (L3, NIST 82%)
6. Proposal Genesis Pattern (L2, NIST 91%)
7. RAG + Fine-Tuning Loop (L1, NIST 88%)
8. MCP Server Cluster (L2, OWASP 85%)
9. Compliance-First AI (L1, NIST 98%)
10. Digital Twin AI (L3, NIST 75%)
11. Conversational Intake Agent (L1, NIST 93%)
12. AI Security Monitor (L2, NIST 95%)

---

## Snippets (12 reusable patterns)

1. Basic RAG Retrieval (3 nodes)
2. HITL Approval Gate (2 nodes)
3. Safety Guardrail Stack (3 nodes)
4. Confidence + Circuit Breaker (2 nodes)
5. MCP Tool Surface (4 nodes)
6. Agent Memory System (3 nodes)
7. Feedback Loop (3 nodes)
8. Behavioral Baseline (2 nodes)
9. Agent Spawn Pattern (3 nodes)
10. Token Budget Enforcer (2 nodes)
11. Prompt Registry Lookup (2 nodes)
12. AI Audit Trail (2 nodes)

---

## Compliance Frameworks Assessed

- **NIST AI RMF** (Govern/Map/Measure/Manage) — 8 weighted checks, 0–100% score
- **OWASP LLM Top 10** — 10 checks covering prompt injection, data poisoning, etc.
- **OMB M-25-21** — safety-impacting + rights-impacting domain classification
- **MITRE ATLAS** — adversarial ML technique mapping per node type
- **NIST AI 600-1** — high-risk AI system indicators

---

## Static Assets

| File | Purpose |
|------|---------|
| `tools/dashboard/static/css/agentic-canvas.css` | Node styling, palette, props panel, toolbar |

---

## Registration Points (all 7 complete)

1. `tools/agentic_ai_canvas/blueprint.py` — Flask Blueprint `aadc_bp`
2. `tools/dashboard/app.py` `_CANVAS_DEFS` — `("aadc", "ICDEV_AADC_ENABLED", ...)`
3. `tools/dashboard/app.py` `inject_globals` — `"aadc_enabled": _CANVAS_FLAGS.get("aadc", False)`
4. `tools/canvas/orchestrator.py` `VALID_CANVAS_KEYS` — `"aadc"`
5. `tools/canvas/orchestrator.py` `CANVAS_DB_MAP` — `"aadc": ("agentic_ai_canvas.db", ...)`
6. `tools/dashboard/templates/base.html` — nav dropdown under `{% if aadc_enabled %}`
7. `tools/manifest/agentic-ai-canvas.md` — this file
