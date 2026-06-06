# AI Augmentation Canvas (AAC) — Design Specification

**Status:** Design / Pre-Implementation
**Created:** 2026-05-19
**Canvas ID:** AAC
**Canvas Number:** 13 of 12 (new addition)

---

## Problem Statement

ICDEV has 12 canvases. The Migration canvas handles *where* to run code and *how* to modernize it (7Rs: Rehost, Replatform, Refactor, Rearchitect, Repurchase, Retire, Retain). The AI/ML Canvas and Agentic AI Canvas manage *how to design and operate AI systems*.

**What's missing:** A capability to scan an external client codebase and answer: *"Which specific features in this system should be replaced or enhanced by AI — and with what approach?"*

This is an **AI Augmentation Opportunity Assessment** — a bridge between static code analysis and AI capability mapping.

---

## What This Canvas Does

Given a client codebase (git URL, zip upload, or local path), the AI Augmentation Canvas:

1. **Scans** the codebase using AST analysis across all 6 ICDEV languages
2. **Classifies** features into 8 AI-augmentable patterns
3. **Scores** each opportunity by value × feasibility × risk
4. **Maps** each opportunity to a specific AI paradigm + IL-appropriate model
5. **Generates** a prioritized transformation roadmap for technical leads and architects

---

## Scope Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Input | External client codebases (git URL / zip / path) | Advisory/consulting use case; no self-scan in Phase 1 |
| Languages | Python, Java, C#, Go, Rust, TypeScript | All 6 ICDEV-supported languages |
| Output audience | Technical leads and architects | Code-level recommendations with module/function paths |
| Report mode | Technical deep-dive only | Executive summary deferred to Phase 2 |

---

## Architecture

### Canvas Identifiers

| Property | Value |
|----------|-------|
| Canvas ID | `AAC` |
| Route | `/ai-augmentation` |
| Feature flag | `ICDEV_AAC_ENABLED` |
| Database | `ai_augmentation.db` |
| Blueprint | `tools/ai_augmentation/blueprint.py` |

### Core Pipeline

```
Codebase Input (git URL / zip / local path)
    ↓
AST + Static Analysis
(extends tools/modernization/legacy_analyzer.py)
    ↓
Pattern Classifier
(tools/ai_augmentation/pattern_classifier.py)
Detects 8 AI-augmentable patterns across 6 languages
    ↓
Opportunity Scorer
(tools/ai_augmentation/opportunity_scorer.py)
value = usage_freq × task_complexity × automation_deficit
feasibility = data_availability × IL_model_exists × (1 / integration_complexity)
risk = reversibility × compliance_impact × dependency_complexity
    ↓
AI Capability Mapper
(tools/ai_augmentation/capability_mapper.py)
Maps pattern → AI paradigm + IL-appropriate model + data requirements
    ↓
Roadmap Generator
(tools/ai_augmentation/roadmap_generator.py)
Prioritized implementation plan with AIMC/AADC integration links
```

---

## 8-Component Completeness Gate

All 8 components must ship together per CLAUDE.md rules.

| # | Component | Path |
|---|-----------|------|
| 1 | Template | `tools/dashboard/templates/ai_augmentation/index.html` |
| 2 | icdev/ mirror | `icdev/tools/dashboard/templates/ai_augmentation/index.html` |
| 3 | Route | `tools/ai_augmentation/blueprint.py` → `@bp.route('/')` |
| 4 | Backing module | `tools/ai_augmentation/aaa_engine.py` |
| 5 | Constants | `tools/ai_augmentation/constants.py` (PATTERN_TYPES, AI_PARADIGMS, SCORING_WEIGHTS) |
| 6 | DB migration | `tools/ai_augmentation/db/init_db.py` |
| 7 | Nav link | `base.html` sidebar under "AI" section |
| 8 | IQE integration | `tools/iqe/adapters/ai_augmentation.py` + `context/iqe/queries/ai_augmentation/` |

---

## Files to Create

### Engine (new)
| File | Purpose |
|------|---------|
| `tools/ai_augmentation/aaa_engine.py` | Main orchestration: scan → classify → score → map → roadmap |
| `tools/ai_augmentation/pattern_classifier.py` | AST + heuristic pattern detection per language |
| `tools/ai_augmentation/opportunity_scorer.py` | Value × feasibility × risk scoring matrix |
| `tools/ai_augmentation/capability_mapper.py` | Pattern → AI paradigm + model recommendation |
| `tools/ai_augmentation/roadmap_generator.py` | Prioritized implementation roadmap builder |
| `tools/ai_augmentation/constants.py` | PATTERN_TYPES, AI_PARADIGMS, SCORING_WEIGHTS |
| `tools/ai_augmentation/blueprint.py` | Flask blueprint for `/ai-augmentation` routes |
| `tools/ai_augmentation/db/init_db.py` | DB schema initialization |

### Data / Context (new)
| File | Purpose |
|------|---------|
| `context/ai_augmentation/pattern_catalog.json` | Pattern definitions, AI equivalents, effort estimates |
| `context/ai_augmentation/il_model_matrix.json` | IL-appropriate model recommendations per AI paradigm |
| `args/aac_config.yaml` | Scoring weights, confidence thresholds, scan depth settings |

### Templates (new)
| File | Purpose |
|------|---------|
| `tools/dashboard/templates/ai_augmentation/index.html` | Main canvas page |
| `icdev/tools/dashboard/templates/ai_augmentation/index.html` | Mirror (companion sync) |

### IQE (new)
| File | Purpose |
|------|---------|
| `tools/iqe/adapters/ai_augmentation.py` | Registers `ai_augmentation.*` collections |
| `context/iqe/queries/ai_augmentation/01_top_opportunities.iqe` | Seed query: top-scored opportunities |
| `context/iqe/queries/ai_augmentation/02_high_value_low_risk.iqe` | Seed query: value > 0.7, risk < 0.3 |
| `context/iqe/queries/ai_augmentation/03_agentic_candidates.iqe` | Seed query: agentic workflow candidates |

### Genesis Reflex (new)
| File | Purpose |
|------|---------|
| `tools/genesis/reflexes/aac_scanner.py` | Autonomous periodic scan trigger |

---

## DB Schema

```sql
-- Codebase scan sessions (append-only)
CREATE TABLE aac_scans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id TEXT UNIQUE NOT NULL,           -- sha256 of input + timestamp
    input_type TEXT NOT NULL,              -- 'git_url' | 'zip' | 'local_path'
    input_ref TEXT NOT NULL,               -- URL or path
    language_profile TEXT,                 -- JSON: {python: N, java: N, ...}
    total_files INTEGER,
    total_loc INTEGER,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    completed_at TEXT
);

-- Discovered augmentation opportunities
CREATE TABLE aac_opportunities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id TEXT NOT NULL,
    module_path TEXT NOT NULL,             -- relative file path
    function_name TEXT,
    line_start INTEGER,
    line_end INTEGER,
    language TEXT NOT NULL,
    pattern_type TEXT NOT NULL,            -- see PATTERN_TYPES constant
    pattern_detail TEXT,                   -- JSON: pattern-specific metadata
    ai_paradigm TEXT,                      -- 'llm' | 'ml_classifier' | 'embedding' | 'agent' | 'anomaly_detection'
    il_recommended_model TEXT,
    data_requirements TEXT,               -- JSON: {needs_training_data, zero_shot_feasible, ...}
    created_at TEXT NOT NULL
);

-- Per-opportunity scores
CREATE TABLE aac_scores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    opportunity_id INTEGER NOT NULL,
    value_score REAL,                      -- 0.0–1.0
    feasibility_score REAL,               -- 0.0–1.0
    risk_score REAL,                       -- 0.0–1.0 (lower = less risky)
    composite_score REAL,                  -- weighted aggregate
    score_detail TEXT,                    -- JSON: per-dimension breakdown
    scored_at TEXT NOT NULL
);

-- Generated transformation roadmaps
CREATE TABLE aac_roadmaps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id TEXT NOT NULL,
    roadmap_id TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL,
    phases TEXT NOT NULL,                  -- JSON: [{phase, opportunities[], effort_days, dependencies}]
    total_effort_days INTEGER,
    aimc_links TEXT,                       -- JSON: [{opportunity_id, aimc_model_id}]
    aadc_links TEXT,                       -- JSON: [{opportunity_id, aadc_topology_id}]
    created_at TEXT NOT NULL
);

-- Audit trail (append-only, NIST AU)
CREATE TABLE aac_audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    scan_id TEXT,
    actor TEXT,
    detail TEXT,
    created_at TEXT NOT NULL
);
```

---

## AI Pattern Detection — All 6 Languages

| Pattern | Python | Java | C# | Go | Rust | TypeScript | AI Replacement |
|---------|--------|------|----|----|------|------------|----------------|
| Nested conditionals (depth ≥ 3) | `ast.If` | `IfStatement` | `IfStatement` | `ast.IfStmt` | `syn::ExprIf` | `IfStatement` | ML classifier |
| Regex on user input | `re.match/search` | `Pattern.compile` | `Regex.Match` | `regexp.Compile` | `regex::Regex` | `/regex/` literal | NLP/NLU extractor |
| String template rendering | `.format()`, f-str, Jinja2 | `String.format`, Velocity | `string.Format`, Razor | `fmt.Sprintf` | `format!()` | template literals | LLM generation |
| Scheduled/cron definitions | `schedule()`, APScheduler | `@Scheduled`, Quartz | `IHostedService`, Hangfire | `time.Ticker` | `tokio::time` | `setInterval`, cron | Agentic trigger |
| Hardcoded threshold comparisons | `x > LITERAL` | `x > LITERAL` | `x > LITERAL` | `x > LITERAL` | `x > LITERAL` | `x > LITERAL` | ML anomaly detection |
| DB → render → notify chain | ORM + template + smtp | JDBC + Thymeleaf + mail | EF + Razor + SMTP | `sql.DB` + html/template + SMTP | `sqlx` + `askama` + lettre | TypeORM + Handlebars + nodemailer | LLM synthesis |
| Keyword-list search | `x in list` | `list.contains()` | `list.Contains()` | `slices.Contains` | `.contains()` | `array.includes()` | Vector semantic search |
| Large rule table lookups | `dict` ≥ 10 keys | `HashMap` ≥ 10 | `Dictionary` ≥ 10 | `map[...]` ≥ 10 | `HashMap::from` ≥ 10 | `Record<>` ≥ 10 | ML/RL decision agent |

### AST Parsers per Language

| Language | Parser | Notes |
|----------|--------|-------|
| Python | `ast` (stdlib) | Already used in `legacy_analyzer.py` — extend directly |
| Java | `javalang` (PyPI) | Pure Python Java parser |
| C# | `tree-sitter-c-sharp` | Via `tree-sitter` Python bindings |
| Go | `tree-sitter-go` | Via `tree-sitter` Python bindings |
| Rust | `tree-sitter-rust` | Via `tree-sitter` Python bindings |
| TypeScript | `tree-sitter-typescript` | Via `tree-sitter` Python bindings |

---

## Scoring Formula

```python
# Per opportunity
value_score = (usage_freq_norm * 0.4) + (task_complexity_norm * 0.35) + (automation_deficit_norm * 0.25)
feasibility_score = (data_avail * 0.4) + (il_model_exists * 0.35) + ((1 - integration_complexity_norm) * 0.25)
risk_score = (reversibility * 0.4) + (compliance_impact_inv * 0.35) + ((1 - dep_complexity_norm) * 0.25)

# Composite (higher = higher priority)
composite = (value_score * 0.45) + (feasibility_score * 0.35) + ((1 - risk_score) * 0.20)
```

---

## IL-Appropriate Model Matrix

| IL Level | AI Paradigm | Recommended Model | Notes |
|----------|-------------|-------------------|-------|
| IL4 | LLM generation | Claude Sonnet/Opus (Anthropic API) | Standard commercial cloud |
| IL4 | ML classifier | Fine-tuned sentence-transformers | Hosted on approved CSP |
| IL5 | LLM generation | GovCloud-deployed Bedrock Claude | AWS GovCloud (East) |
| IL5 | Embedding/search | Amazon Titan Embeddings (GovCloud) | |
| IL6 | LLM generation | Ollama (air-gap) + approved model | NSA Type 1 encrypted storage |
| IL6 | ML classifier | Locally trained scikit-learn / PyTorch | No cloud connectivity |

---

## Integration Points

| Integration | How |
|-------------|-----|
| **AIMC** | Roadmap links each opportunity → AIMC model selection wizard (`/ai-ml/models/new?paradigm=<paradigm>`) |
| **AADC** | Agentic candidates link → AADC topology designer (`/agentic-ai/topologies/new?trigger=<opportunity_id>`) |
| **Migration Canvas** | Implementation SOPs link → `/migration-canvas/sops/<sop_id>` |
| **Genesis Reflex** | `tools/genesis/reflexes/aac_scanner.py` — periodic re-scan of registered client repos |
| **IQE** | `foreach o in ai_augmentation.opportunities where o.value_score > 0.7 select o.module, o.pattern, o.ai_paradigm` |
| **Kanban** | Top 5 opportunities auto-promoted to `kanban_tasks` type `ai_opportunity` via `suggested_card_writer.py` |

---

## What to Reuse (Don't Rebuild)

| Existing Tool | Reuse How |
|---------------|-----------|
| `tools/modernization/legacy_analyzer.py` | Inherit AST walker; extend for AI pattern nodes |
| `tools/modernization/seven_r_assessor.py` | Copy weighted multi-criteria scoring architecture |
| `tools/modernization/architecture_extractor.py` | Reuse call graph detection for DB→render→notify chain pattern |
| `tools/awareness/component_indexer.py` | Reuse sha256 node ID scheme for opportunity dedup |
| `tools/awareness/value_scorer.py` | Reuse confidence × weight ranking for prioritization |
| `context/modernization/seven_rs_catalog.json` | Reference effort/risk estimation ranges |

---

## IQE Seed Query Examples

```
# 01_top_opportunities.iqe
foreach o in ai_augmentation.opportunities
  where o.composite_score > 0.6
  select o.module_path, o.pattern_type, o.ai_paradigm, o.composite_score

# 02_high_value_low_risk.iqe
foreach o in ai_augmentation.opportunities
  where o.value_score > 0.7
  where o.risk_score < 0.3
  select o.module_path, o.function_name, o.ai_paradigm, o.il_recommended_model

# 03_agentic_candidates.iqe
foreach o in ai_augmentation.opportunities
  where o.ai_paradigm == 'agent'
  select o.module_path, o.pattern_type, o.feasibility_score
```

---

## Verification Checklist

- [ ] `ICDEV_AAC_ENABLED=true` in `.env`
- [ ] `http://localhost:5050/ai-augmentation` renders without errors
- [ ] Input a git URL or local path; scan completes; `aac_scans` row inserted
- [ ] `aac_opportunities` populated with module_path, pattern_type, language, ai_paradigm
- [ ] `aac_scores` populated with value/feasibility/risk scores per opportunity
- [ ] `aac_roadmaps` populated with prioritized phases
- [ ] IQE query returns results: `foreach o in ai_augmentation.opportunities where o.composite_score > 0.5 select o.module_path, o.pattern_type`
- [ ] Top opportunities appear in `kanban_tasks` with `task_type = 'ai_opportunity'`
- [ ] `pytest tests/test_aac_engine.py -v` passes
- [ ] Playwright E2E: scan page loads, results table renders, roadmap displays
- [ ] `python tools/dx/companion.py --sync --write --json` completes without error
- [ ] `python tools/workflow/coherence_checker.py --all --gate` passes

---

## Effort Estimate

| Phase | Scope | Effort |
|-------|-------|--------|
| P1 | Engine core + DB schema + basic template + IQE adapter | 3–4 days |
| P2 | All 8 patterns fully ported to all 6 languages + pattern catalog + IL model matrix | 2 days |
| P3 | Roadmap generator + AIMC/AADC deep links + Genesis reflex | 2 days |
| P4 | Kanban promotion + Nav link + companion sync + coherence gate | 1 day |
| **Total** | | **~8–9 days** |

---

## Related Canvases

| Canvas | Route | Relationship |
|--------|-------|-------------|
| AIMC — AI/ML Model Canvas | `/ai-ml` | Roadmap implementation: model selection |
| AADC — Agentic AI Design Canvas | `/agentic-ai` | Roadmap implementation: agentic topology |
| MDC — Migration Design Canvas | `/migration-canvas` | Shares SOP infrastructure; different audience |
| MI — Migration Intelligence Engine | `/migration-intel` | Sibling: MI finds infra migration opportunities; AAC finds AI augmentation opportunities |
