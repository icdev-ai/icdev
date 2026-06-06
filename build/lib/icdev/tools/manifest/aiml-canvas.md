# AI/ML Model Canvas (AIMC — Phase AIMC)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## AI/ML Model Canvas (AIMC)

9th ICDEV canvas. Visual design surface for the foundation model lifecycle —
model selection, adaptation strategy, evaluation, safety, deployment planning,
and AI governance. Distinct from AADC (agent topology) — focuses on the model layer.
Route: `/ai-ml/`. DB: `data/aiml_canvas.db`. Flag: `ICDEV_AIML_CANVAS_ENABLED`.

| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| AIMC Constants | tools/aiml_canvas/constants.py | Node palette (9 groups, ~40 node types), foundation model catalog (13 models), IL suitability matrix, compliance rules (15), DoD RAI principles, adaptation decision matrix | (import) | Palette dicts, FOUNDATION_MODELS, AIMC_COMPLIANCE_RULES |
| AIMC DB Init | tools/aiml_canvas/db/init_db.py | Schema + 4 canonical templates + 5 snippets; dual-backend SQLite/PostgreSQL | (run or import) | aiml_canvas.db |
| AIMC Engine | tools/aiml_canvas/aiml_engine.py | Design CRUD, graph assessment (15 deterministic rules), model card generation, deploy manifest generation, version management, dashboard stats | (import) | Design dicts, assessment results, artifacts |
| Adaptation Engine | tools/aiml_canvas/adaptation_engine.py | Decision matrix: ranks Prompt-only/RAG/Fine-tune/Hybrid based on corpus, training data, GPU, latency, accuracy, IL constraints | --has_corpus, --has_training_data, --vram_gb, --il_level, … | Ranked strategies + rationale |
| Deployment Planner | tools/aiml_canvas/deployment_planner.py | Recommends inference server (Ollama/vLLM/Bedrock/TGI), quantization strategy (Q4-fp16), GPU requirements for a given model + IL constraints | --model_id, --il_level, --vram_gb, --latency_target_ms | Plan dict with server config + warnings |
| Governance Assessor | tools/aiml_canvas/governance_assessor.py | DoD RAI 5 Principles assessment, IL suitability scoring, OMB M-25-21 readiness check, optional NIST AI RMF/ATLAS/OWASP wrappers | graph dict, design dict | Multi-framework report dict |
| AIMC Blueprint | tools/aiml_canvas/blueprint.py | Flask Blueprint: 30+ routes — design CRUD, canvas pages, adaptation advisor, deployment planner, artifact generation, templates, snippets, model catalog | Flask routes at /ai-ml/ | HTML pages + JSON API |
| AIMC Seed (DoD/IC) | tools/db/seeds/seed_ai_canvases_aimc.py | Seeds 8 DoD/IC AIMC designs (8 designs, 68 nodes, 60 edges, 16 assessments, 8 model card artifacts). Run via seed_ai_canvases_all.py. | --reset, --json | stdout counts |
