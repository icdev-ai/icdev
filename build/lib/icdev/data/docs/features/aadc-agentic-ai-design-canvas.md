# CUI // SP-CTI
# AADC — Agentic AI Design Canvas
**Reference Document — Recovery Guide**
_Created: 2026-05-03 | Branch: kanban/aisg-a1-05 → aadc/main_

---

## What Is AADC?

The **Agentic AI Design Canvas (AADC)** is the 10th ICDEV™ design canvas. It is a purpose-built visual design surface for architecting, governing, and assessing **Agentic AI systems** — LLM pipelines, RAG architectures, multi-agent orchestrators, MCP server clusters, and any system where AI models take autonomous or semi-autonomous action.

**Key differentiator vs. the 9 existing canvases:** Every other canvas owns a technical domain (network topology, security posture, CI/CD pipeline, etc.). AADC is the first canvas that models the **AI system layer itself** — the design, governance, and compliance of how AI components connect and act. It fills a mandatory gap created by OMB M-25-21, OMB M-26-04, NIST AI RMF, and DoD AI Ethics requirements.

**AADC is not:**
- A code editor (that's ANVIL workflow)
- A monitoring dashboard (that's ODC + tools/monitor/)
- A fine-tuning interface (that's the finetune dashboard)
- An agent runner (that's Genesis v2 + Kanban executor)
- The canvas is a **design surface with compliance assessment**, not an operational control plane

---

## File Structure

```
tools/agentic_ai_canvas/
  __init__.py                   # Module init
  constants.py                  # Node palette (60+ types), compliance rules, NIST/OWASP maps
  agentic_engine.py             # Rule-based assessment engine (no LLM)
  workflow.py                   # HITL + loop_engine bridge
  bus_subscriber.py             # Cross-canvas event bus subscriber
  blueprint.py                  # Flask Blueprint — all routes + API

  db/
    __init__.py
    init_db.py                  # Schema DDL + seed templates + snippets

tools/dashboard/templates/agentic_ai_canvas/
  index.html                    # Landing page — design list
  canvas.html                   # Main visual editor (JointJS)
  templates.html                # Template gallery (12 built-in)
  snippets.html                 # Snippet library (12 built-in)
  assessments.html              # Assessment results detail
  remediation.html              # Remediation recommendations
  artifacts.html                # Generated artifacts (model cards, system cards, AI BOM)

tools/dashboard/static/js/
  agentic-canvas.js             # JointJS canvas editor, node rendering, save/load

tools/dashboard/static/css/
  agentic-canvas.css            # AADC-specific dark theme, node icons

data/
  agentic_ai_canvas.db          # Per-canvas SQLite DB (also supports PostgreSQL)

args/
  aadc_canvas_config.yaml       # Canvas behavior config

tools/manifest/
  agentic-ai-canvas.md          # Tool manifest shard
```

---

## Registration Points

Every canvas must be registered in **4 places**. If the canvas doesn't appear in the nav or throws ImportError, check all 4:

| Location | What to add |
|----------|-------------|
| `tools/dashboard/app.py` line ~147 | `("aadc", "ICDEV_AADC_ENABLED", "tools.agentic_ai_canvas.blueprint", "aadc_bp")` in `_CANVAS_DEFS` |
| `tools/canvas/orchestrator.py` | Add `"aadc"` to `VALID_CANVAS_KEYS` and `"aadc": ("agentic_ai_canvas.db", "aadc_assessments", "score")` to `CANVAS_DB_MAP` |
| `.env` / `.env.example` | `ICDEV_AADC_ENABLED=true` |
| Nav template | Link in `tools/dashboard/templates/base.html` canvases dropdown |

---

## Database Schema

**File:** `data/agentic_ai_canvas.db`
**Env var override:** `AADC_STORAGE_BACKEND=postgresql`

### Tables

| Table | Purpose |
|-------|---------|
| `aadc_designs` | Design graph storage (nodes + edges as JSON) |
| `aadc_templates` | 12 built-in + user-created solution templates |
| `aadc_snippets` | 12 built-in + user-created reusable subgraphs |
| `aadc_assessments` | Compliance assessment results per design save |
| `aadc_artifacts` | Generated artifacts (model cards, system cards, AI BOM) |
| `aadc_workflow_links` | Link designs → HITL `wf_instances` |
| `aadc_loop_links` | Link designs → `workflow_loops` (loop_engine) |
| `aadc_versions` | Design version history |
| `aadc_audit` | Append-only audit trail |

**To re-initialize DB:**
```bash
python tools/agentic_ai_canvas/db/init_db.py
```

---

## Node Palette (60+ Node Types)

### Category 1: Models
| Node Type | Label | Description |
|-----------|-------|-------------|
| `llm` | LLM | Large Language Model (Claude, GPT, Gemini, Llama) |
| `llm-local` | Local LLM | Air-gap / Ollama-hosted LLM |
| `embedding-model` | Embedding Model | Text embedding model (ada, e5, bge) |
| `fine-tuned-adapter` | Fine-Tuned Adapter | LoRA / PEFT adapter on base model |
| `classifier` | Classifier | ML classifier (intent, toxicity, PII) |
| `reranker` | Re-Ranker | Cross-encoder re-ranking model |
| `multimodal` | Multimodal Model | Vision-language model |

### Category 2: Memory & Storage
| Node Type | Label | Description |
|-----------|-------|-------------|
| `vector-db` | Vector DB | Embedding store (Chroma, Pinecone, pgvector) |
| `doc-store` | Document Store | Raw document repository |
| `short-term-mem` | Short-Term Memory | In-context or session memory |
| `long-term-mem` | Long-Term Memory | Persistent cross-session memory |
| `episodic-buffer` | Episodic Buffer | Event/interaction history |
| `knowledge-graph` | Knowledge Graph | Structured entity-relationship store |
| `embedding-cache` | Embedding Cache | Cached vector computation store |

### Category 3: Agents
| Node Type | Label | Description |
|-----------|-------|-------------|
| `autonomous-agent` | Autonomous Agent | Fully autonomous (L4/L5) agent |
| `semi-auto-agent` | Semi-Auto Agent | Human-in-the-loop (L2/L3) agent |
| `orchestrator` | Orchestrator | Multi-agent dispatcher |
| `sub-agent` | Sub-Agent | Specialized child agent |
| `researcher-agent` | Research Agent | Web/doc research specialist |
| `writer-agent` | Writer Agent | Content generation specialist |
| `analyst-agent` | Analyst Agent | Data analysis specialist |
| `reviewer-agent` | Reviewer Agent | Output review / critique specialist |

### Category 4: Tools & MCP
| Node Type | Label | Description |
|-----------|-------|-------------|
| `mcp-server` | MCP Server | Model Context Protocol server |
| `mcp-gateway` | MCP Gateway | Auth + rate-limit gateway for MCP |
| `tool-chain` | Tool Chain | Ordered function-calling sequence |
| `function-caller` | Function Caller | Single function invocation |
| `output-validator` | Output Validator | Structured output schema enforcer |
| `external-api` | External API | Third-party API endpoint |
| `code-executor` | Code Executor | Sandboxed code execution |
| `web-search` | Web Search | Web search tool |

### Category 5: Data
| Node Type | Label | Description |
|-----------|-------|-------------|
| `training-data` | Training Data | Dataset used for fine-tuning |
| `inference-input` | Inference Input | User query / prompt entry point |
| `feedback-collector` | Feedback Collector | User preference / rating collection |
| `rlhf-pipeline` | RLHF Pipeline | Reinforcement learning from human feedback |
| `data-lake` | Data Lake | Bulk data source |
| `chunker` | Chunker | Document splitting / preprocessing |
| `data-validator` | Data Validator | Input schema / type validation |

### Category 6: Safety
| Node Type | Label | Description |
|-----------|-------|-------------|
| `guardrail` | Guardrail | General-purpose content policy enforcer |
| `pii-detector` | PII Detector | Personally identifiable information detector |
| `toxicity-filter` | Toxicity Filter | Harmful content filter |
| `confidence-threshold` | Confidence Gate | Min confidence gate (blocks low-confidence output) |
| `circuit-breaker` | Circuit Breaker | Stops runaway agent chains |
| `rate-limiter` | Rate Limiter | Token / request rate enforcer |
| `input-sanitizer` | Input Sanitizer | Prompt injection mitigation |
| `redaction-engine` | Redaction Engine | PII / CUI redaction before output |

### Category 7: Governance
| Node Type | Label | Description |
|-----------|-------|-------------|
| `hitl-gate` | HITL Gate | Human-in-the-loop approval gate |
| `audit-logger` | Audit Logger | Append-only event logger |
| `approval-workflow` | Approval Workflow | Multi-stage approval chain |
| `caio-override` | CAIO Override | Chief AI Officer manual override |
| `compliance-reporter` | Compliance Reporter | Framework-aligned report generator |
| `alert-manager` | Alert Manager | Threshold-based alert dispatcher |
| `prompt-registry` | Prompt Registry | Versioned prompt template store |

### Category 8: Infrastructure
| Node Type | Label | Description |
|-----------|-------|-------------|
| `gpu-cluster` | GPU Cluster | Model inference compute |
| `model-registry` | Model Registry | Model version / lineage tracking |
| `token-budget` | Token Budget | Per-request token ceiling enforcer |
| `vector-index` | Vector Index | ANN index (HNSW, IVF) |
| `siem-forwarder` | SIEM Forwarder | Security event stream to SIEM |
| `baseline-snapshot` | Baseline Snapshot | Behavioral baseline capture |
| `drift-detector` | Drift Detector | Production vs. baseline comparator |

---

## Compliance Framework Mappings

### NIST AI RMF (Govern / Map / Measure / Manage)

| Function | AADC Check | Required Nodes |
|----------|-----------|----------------|
| GOVERN-1 | Oversight plan present | `approval-workflow` OR `hitl-gate` |
| GOVERN-2 | AI use case classified | Design has `classification` metadata |
| MAP-1 | System boundary defined | At least 1 `inference-input` + 1 output node |
| MAP-2 | Risk documented | Assessment run with findings |
| MEASURE-1 | Hallucination bounded | `confidence-threshold` present |
| MEASURE-2 | Drift monitored | `drift-detector` present |
| MANAGE-1 | Incident path exists | `alert-manager` → `hitl-gate` chain |
| MANAGE-2 | Circuit breaker present | `circuit-breaker` node in any agent chain |

### OWASP LLM Top 10

| Risk | Check | Failing Condition |
|------|-------|-------------------|
| LLM01 Prompt Injection | `input-sanitizer` present | No sanitizer upstream of any `llm` |
| LLM02 Insecure Output | `output-validator` present | No validator downstream of any `llm` |
| LLM03 Training Data Poisoning | `training-data` has provenance | `training-data` node with no audit link |
| LLM04 Model DoS | `token-budget` + `rate-limiter` | Neither present in design |
| LLM05 Supply Chain | `model-registry` linked | `llm` node with no `model-registry` edge |
| LLM06 Sensitive Info | `pii-detector` + `redaction-engine` | PII detector but no redaction |
| LLM07 Insecure Plugin | `mcp-gateway` upstream of `mcp-server` | `mcp-server` with no auth gateway |
| LLM08 Excessive Agency | `circuit-breaker` in agent chain | `autonomous-agent` with no circuit breaker |
| LLM09 Overreliance | `confidence-threshold` present | `hitl-gate` missing for low-confidence path |
| LLM10 Model Theft | `audit-logger` on inference path | No logging of inference calls |

### OMB M-25-21 — Rights/Safety-Impacting Classification

**Rights-impacting** (triggers HITL template `aadc-safety-review`):
- Design tagged with domain: benefits, credit, employment, housing, education, criminal-justice, immigration

**Safety-impacting** (triggers HITL template `aadc-safety-review`):
- Design tagged with domain: critical-infrastructure, safety-systems, medical, autonomous-vehicle

### Autonomy Level Classification (per Agent Node)

| Level | Label | Conditions |
|-------|-------|-----------|
| L0 | Human-Operated | No autonomous actions; all outputs go through `hitl-gate` |
| L1 | Human-Delegated | Autonomous low-risk actions; HITL for consequential decisions |
| L2 | Human-Supervised | Autonomous most actions; human can override via `caio-override` |
| L3 | Human-Initiated | Human starts task; agent completes autonomously with `circuit-breaker` |
| L4 | Fully Autonomous | Full autonomy with `circuit-breaker` + `confidence-threshold` + `audit-logger` |
| L5 | Unconstrained | No safety nodes — flagged as CRITICAL gap |

---

## Templates (12 Built-In)

| # | Name | Category | Key Nodes | Compliance Pre-Score |
|---|------|----------|-----------|---------------------|
| 1 | Simple RAG Q&A | retrieval | LLM + VectorDB + DocStore + ContentFilter | NIST AI RMF: 40% |
| 2 | HITL-Gated RAG | retrieval-gov | Above + HITLGate + AuditLogger | NIST AI RMF: 90% |
| 3 | Single Autonomous Agent | agent | LLM + MCPServer + ToolChain + Memory + CircuitBreaker + OutputValidator | OWASP: 70% |
| 4 | Multi-Agent Orchestrator | multi-agent | Orchestrator + 3×SubAgent + SharedMemory + ResultAggregator + HITLEscalation | NIST AI RMF: 85% |
| 5 | Agentic Research Pipeline | research | ResearchAgent + WebSearch + RAG + ReRanker + SynthesisLLM + ConfidenceGate | NIST AI 600-1: Low Risk |
| 6 | Proposal Genesis Pattern | enterprise | CaptureAgent + ResearchEngine + WriterAgent + ReviewerAgent + HITLApproval | OMB M-25-21: Compliant |
| 7 | RAG + Fine-Tuning Loop | learning | DocIngestion → Chunker → Embedder → VectorDB → Retriever → LLM → FeedbackCollector → RLHFPipeline → Adapter | NIST AI RMF: 60% |
| 8 | MCP Server Cluster | infrastructure | MCPServer×3 + MCPGateway + RateLimiter + AuditLogger | OWASP LLM07: Pass |
| 9 | Compliance-First AI | regulated | LLM + PIIDetector + RedactionEngine + AuditLogger + HITLGate + ComplianceReporter | OMB M-25-21: Compliant |
| 10 | Digital Twin AI | simulation | DomainLLM + SimulationEngine + MonteCarlo + ScenarioManager + ReportGenerator | DoD AI Ethics: Traceable |
| 11 | Conversational Intake Agent | gov-dod | IntentClassifier + DialogManager + RAG + FormExtractor + Validator + HumanEscalation | OMB M-25-21: Compliant |
| 12 | AI Security Monitor | security | BehaviorDriftDetector + AnomalyDetector + AlertManager + CircuitBreaker + SIEMForwarder | MITRE ATLAS: Covered |

---

## Snippets (12 Built-In)

| # | Name | Category | Nodes | Description |
|---|------|----------|-------|-------------|
| 1 | Basic RAG Retrieval | retrieval | 5 | Chunker → Embedder → VectorDB → Retriever → ReRanker |
| 2 | HITL Approval Gate | governance | 4 | DecisionPoint → HumanReview → Approve/Reject → AuditLog |
| 3 | Safety Guardrail Stack | safety | 4 | InputFilter → PIIDetector → ToxicityCheck → OutputValidator |
| 4 | Confidence + Circuit Breaker | safety | 4 | ConfidenceThreshold → [Low→CircuitBreaker→HITLEscalation] [High→Continue] |
| 5 | MCP Tool Surface | infrastructure | 5 | MCPServer → ToolRouter → ExternalAPI → ResponseValidator → AuditLog |
| 6 | Agent Memory System | memory | 4 | ShortTermMem → LongTermMem → EpisodicBuffer → MemoryConsolidator |
| 7 | Feedback Loop | learning | 4 | FeedbackCollector → PreferenceLabeler → RLHFTrainer → ModelUpdater |
| 8 | Behavioral Baseline | monitoring | 4 | BaselineSnapshot → ProductionMonitor → DriftDetector → AlertManager |
| 9 | Agent Spawn Pattern | multi-agent | 5 | Orchestrator → TaskDecomposer → SubAgent×N → ResultAggregator → Validator |
| 10 | Token Budget Enforcer | infrastructure | 4 | TokenCounter → BudgetGate → [Over→Truncate] [OK→Continue] |
| 11 | Prompt Registry Lookup | governance | 4 | PromptRegistry → VersionResolver → TemplateFiller → InjectionGuard |
| 12 | AI Audit Trail | compliance | 4 | EventEmitter → HMACSigner → AppendOnlyLog → SIEMForwarder |

---

## Workflow Engine Integration

### HITL (Human-in-the-Loop) Workflow

**Trigger:** On design save, if `agentic_engine.classify_design()` returns `safety_impacting=True` OR `rights_impacting=True`.

**Templates seeded in `wf_templates`:**
- `aadc-safety-review`: 3 stages — AI Ethics Review → CAIO Sign-off → Legal Compliance Review
- `aadc-standard-review`: 1 stage — Design Owner Approval

**Effect:** Artifact generation buttons (`Generate Model Card`, `Generate System Card`, `Launch Design`) are disabled until HITL instance reaches `approved` status.

**Code path:**
```
blueprint.py:save_design() →
  agentic_engine.classify_design() →
  workflow.py:maybe_create_hitl_instance() →
  tools/workflow_hitl/engine.py:WorkflowEngine.create_instance()
```

### Loop Engine (PLAN → APPLY → UNIFY)

**Trigger:** User clicks "Launch Design → Kanban" after design is HITL-approved.

**Effect:** Creates a `workflow_loop` record in `icdev.db`, then decomposes the design into Kanban tasks (one task per agent cluster / service in the design graph).

**Code path:**
```
blueprint.py:launch_design() →
  launcher.py:launch_to_kanban() →
  tools/workflow/loop_engine.py:create_loop() →
  tools/kanban/decomposer.py:decompose_tasks()
```

### Cross-Canvas Event Bus

**Publishes:**
- `aadc.design.saved` — payload: `{design_id, safety_impacting, rights_impacting, autonomy_max}`
- `aadc.agent.flagged` — payload: `{design_id, agent_node_id, autonomy_level, gaps}`

**Subscribes to:**
- `sdc.topology.saved` — pulls security context for AI nodes in linked SDC design
- `odc.source.added` — syncs monitoring baseline for linked observability design

---

## Epic / Task Reference

### Epic c1 — Core Foundation
| Task | File | Status |
|------|------|--------|
| aadc-c1-01 | `tools/agentic_ai_canvas/` module + constants.py | ✓ |
| aadc-c1-02 | `tools/agentic_ai_canvas/db/init_db.py` (schema + seeds) | ✓ |
| aadc-c1-03 | `tools/agentic_ai_canvas/agentic_engine.py` | ✓ |
| aadc-c1-04 | HTML templates (7 pages) | ✓ |
| aadc-c1-05 | `agentic-canvas.js` + `agentic-canvas.css` | ✓ |
| aadc-c1-06 | Register in orchestrator + app.py + nav | ✓ |
| aadc-c1-07 | Manifest shard + docs | ✓ |

### Epic w1 — Workflow Engine
| Task | File | Status |
|------|------|--------|
| aadc-w1-01 | Seed AADC HITL templates | ✓ |
| aadc-w1-02 | `tools/agentic_ai_canvas/workflow.py` | ✓ |
| aadc-w1-03 | Workflow status panel in canvas.html | ✓ |
| aadc-w1-04 | Wire save → workflow in blueprint | ✓ |
| aadc-w1-05 | `tools/agentic_ai_canvas/launcher.py` | ✓ |
| aadc-w1-06 | Launch button in canvas.html | ✓ |
| aadc-w1-07 | `tools/agentic_ai_canvas/bus_subscriber.py` | ✓ |

### Epic t1 — Templates Gallery
| Task | File | Status |
|------|------|--------|
| aadc-t1-01 | `templates.html` gallery | ✓ |
| aadc-t1-02 | Template API routes + template_manager.py | ✓ |
| aadc-t1-03 | Seed templates 1–4 | ✓ |
| aadc-t1-04 | Seed templates 5–8 | ✓ |
| aadc-t1-05 | Seed templates 9–12 | ✓ |
| aadc-t1-06 | Save-as-template flow | ✓ |

### Epic s1 — Snippets Library
| Task | File | Status |
|------|------|--------|
| aadc-s1-01 | `snippets.html` gallery | ✓ |
| aadc-s1-02 | Snippet API routes | ✓ |
| aadc-s1-03 | Seed snippets 1–6 | ✓ |
| aadc-s1-04 | Seed snippets 7–12 | ✓ |
| aadc-s1-05 | Insert-snippet sidebar in canvas.html | ✓ |

### Epic a1 — Assessment Engine
| Task | File | Status |
|------|------|--------|
| aadc-a1-01 | NIST AI RMF checks | ✓ |
| aadc-a1-02 | OWASP LLM Top 10 checks | ✓ |
| aadc-a1-03 | OMB M-25-21 classifier + HITL path verifier | ✓ |
| aadc-a1-04 | Autonomy level classifier | ✓ |
| aadc-a1-05 | MITRE ATLAS threat mapping | ✓ |
| aadc-a1-06 | `artifacts.py` (model card, system card, AI BOM) | ✓ |
| aadc-a1-07 | `artifacts.html` + download endpoints | ✓ |

### Epic v1 — V&V
| Task | File | Status |
|------|------|--------|
| aadc-v1-01 | E2E test: canvas CRUD + template apply + snippet insert | ⬜ |
| aadc-v1-02 | E2E test: HITL workflow trigger + approval gate | ⬜ |
| aadc-v1-03 | Companion sync + coherence gate | ⬜ |

---

## Quickstart Recovery

If the canvas stops working, check in this order:

```bash
# 1. Verify DB exists and has data
python tools/agentic_ai_canvas/db/init_db.py

# 2. Verify blueprint imports cleanly
python -c "from tools.agentic_ai_canvas.blueprint import aadc_bp; print('OK')"

# 3. Verify engine imports cleanly
python -c "from tools.agentic_ai_canvas.agentic_engine import assess_design; print('OK')"

# 4. Check feature flag
grep ICDEV_AADC_ENABLED .env

# 5. Check registration in app.py
grep -n "aadc" tools/dashboard/app.py

# 6. Check orchestrator registration
grep -n "aadc" tools/canvas/orchestrator.py

# 7. Check nav link
grep -n "agentic" tools/dashboard/templates/base.html
```

---

## Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `ICDEV_AADC_ENABLED` | `false` | Enable/disable AADC canvas |
| `AADC_STORAGE_BACKEND` | `sqlite` | `sqlite` or `postgresql` |
| `AADC_PG_DATABASE` | `agentic_ai_canvas` | PostgreSQL DB name (if PG backend) |
| `AADC_HITL_REQUIRE_APPROVAL` | `true` | Require HITL for safety-impacting designs |
| `AADC_AUTONOMY_BLOCK_L5` | `true` | Block artifact generation for L5 (unconstrained) agents |

---

_CUI // SP-CTI — ICDEV™ Internal Reference_
