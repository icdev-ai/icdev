# Phase 69 — Chat UI Modernization + Codebase Assistant Widget

**CUI // SP-CTI**

| Field | Value |
|-------|-------|
| Phase | 69 |
| Title | Chat UI Modernization + Codebase Assistant Widget |
| Status | Implemented |
| Priority | P1 |
| Dependencies | Phase 44 (Multi-stream Chat), Phase 51 (Unified Chat), Phase 64 (RAG Subsystem) |
| Author | ICDEV™ Architect Agent |
| Date | 2026-03-19 |
| Inspiration | OpenMAIC (THU-MAIC/OpenMAIC) — UX patterns only, zero code (AGPL-3.0) |

---

## 1. Problem Statement

ICDEV™'s multi-stream chat (`/chat`) was functional but visually dated — 100% inline styles, plain text bubbles with CSS `::before` emoji hacks, no streaming indicators, no markdown/code rendering. Meanwhile, the codebase has grown to 500+ Python tools, 400+ templates, 60+ goal files, and a sprawling docs/args ecosystem. Contributors need a fast way to ask "how does X work?" without digging through files.

---

## 2. Deliverables

### Chat UI Modernization (Phases A-D)
- **Phase A**: Extracted all inline styles to [chat.css](tools/dashboard/static/css/chat.css) (~350 lines). CSS Grid layout, message bubble classes, typing indicator animation, smooth transitions.
- **Phase B**: Rich content rendering via [chat-renderers.js](tools/dashboard/static/js/chat-renderers.js). Markdown (marked.js MIT), syntax highlighting (highlight.js BSD), code block copy, collapsible cards, phase dividers, citation badges.
- **Phase C**: Agent persona registry in [args/chat_personas.yaml](args/chat_personas.yaml). 7 personas: ANVIL, Guardian, Scout, Architect, Analyst, Sentinel, Assistant. API at `GET /api/chat/personas`.
- **Phase D**: Resizable panes (drag handles), tabbed right sidebar (RICOAS | Gov | Intel), focus mode, localStorage persistence of pane widths.

### Codebase Assistant Widget (Phases E-H)
- **Phase E**: AST-based codebase indexer ([tools/rag/codebase_indexer.py](tools/rag/codebase_indexer.py)). Hybrid `ast.parse()` for Python, text chunking for .md/.yaml/.html. Security deny-list excludes .env/secrets.
- **Phase F**: Assistant query manager ([tools/dashboard/assistant_manager.py](tools/dashboard/assistant_manager.py)). RAG retrieval → LLM invoke (scanner tier, qwen3.5) → citation formatting → Q&A caching.
- **Phase G**: Floating widget on every page. HTML include in both base templates. sessionStorage persistence. Auto-scoping via `ROUTE_MODULE_MAP`. Fullscreen transition to `/chat?widget_context=<id>`.
- **Phase H**: Background indexing (30-min daemon thread), contextual suggestions, contributor scope override.

---

## 3. Architecture Decisions

| ID | Decision | Rationale |
|----|----------|-----------|
| D-CU-1 | Migrate inline styles to chat.css | Enables animations/transitions |
| D-CU-2 | Typed message renderers by content_type | Foundation for rich content |
| D-CU-3 | Agent persona registry in YAML | Data-driven, no code per persona |
| D-CU-4 | Extend content_type CHECK | Additive, backward compatible |
| D-CU-5 | CSS Grid with resizable panes | Replace fixed 280px sidebars |
| D-CA-1 | Codebase indexer as filesystem RAG source | Reuses chunker pipeline |
| D-CA-2 | Hybrid AST + text chunking | ast.parse() for Python, fallback on error |
| D-CA-3 | Widget as global include, state in sessionStorage | Persists across navigation |
| D-CA-4 | Auto-scope via ROUTE_MODULE_MAP | URL prefix to module directory |
| D-CA-5 | codebase_query as scanner-tier function | Ollama first, Claude fallback |
| D-CA-6 | Q&A cache in codebase_qa_cache table | 3+ hits = cached answer |
| D-CA-7 | Fullscreen via URL param | Shares chat_contexts/chat_messages tables |
| D-CA-8 | Contributor scoping via localStorage | Filters RAG to specific module |
| D-CA-9 | Security exclusions in deny-list | .env, *.pem, *.key, credentials |
| D-CA-10 | Background indexing daemon thread | 30-min interval, thread-safe |

---

## 4. New Files

| File | LOC | Purpose |
|------|-----|---------|
| `tools/dashboard/static/css/chat.css` | ~350 | Chat layout, bubbles, animations |
| `tools/dashboard/static/js/chat-renderers.js` | ~180 | Typed message renderers |
| `tools/dashboard/static/vendor/marked/marked.min.js` | vendor | Markdown parser (MIT) |
| `tools/dashboard/static/vendor/hljs/highlight.min.js` | vendor | Syntax highlighter (BSD) |
| `tools/dashboard/static/vendor/hljs/github-dark.min.css` | vendor | Highlight theme |
| `args/chat_personas.yaml` | ~80 | Agent persona registry |
| `tools/rag/codebase_indexer.py` | ~720 | AST + text codebase indexer |
| `tools/dashboard/assistant_config.py` | ~120 | Route-module map + security exclusions |
| `tools/dashboard/assistant_manager.py` | ~610 | Query handler with RAG + cache |
| `tools/dashboard/templates/includes/assistant_widget.html` | ~55 | Widget HTML template |
| `tools/dashboard/static/js/assistant-widget.js` | ~280 | Widget JS logic |
| `tools/dashboard/static/css/assistant-widget.css` | ~250 | Widget styles |

---

## 5. Modified Files

| File | Change |
|------|--------|
| `tools/dashboard/templates/chat.html` | Replaced all inline styles with CSS classes, added resize handles, tabbed sidebar, focus mode |
| `tools/dashboard/static/js/chat.js` | Refactored message rendering to typed dispatchers, CSS classes, typing indicator, modal classes |
| `tools/dashboard/templates/base.html` | Added widget include, vendor scripts, route map, CSS links |
| `tools/saas/portal/templates/portal_base.html` | Added widget include for portal |
| `tools/dashboard/app.py` | Added personas endpoint, 4 assistant API endpoints, route_module_map context processor |
| `tools/db/init_icdev_db.py` | Added codebase_index + codebase_qa_cache tables, extended content_type CHECK |
| `args/llm_config.yaml` | Added codebase_query to scanner_functions |
| `tools/manifest.md` | Added codebase indexer + assistant tools |
| `.claude/hooks/pre_tool_use.py` | Added codebase_qa_cache to APPEND_ONLY_TABLES |

---

## 6. New DB Tables

- `codebase_index` — File index with SHA-256 hashes, module, symbols JSON
- `codebase_qa_cache` — Popular Q&A cache with hit counting

---

## 7. API Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/chat/personas` | Agent persona registry |
| POST | `/api/assistant/query` | Codebase Q&A query |
| GET | `/api/assistant/status` | Indexer status |
| POST | `/api/assistant/scope` | Set module scope |
| GET | `/api/assistant/suggestions` | Contextual suggestions |

---

## 8. Testing

```bash
# Compile check
python -m py_compile tools/rag/codebase_indexer.py
python -m py_compile tools/dashboard/assistant_manager.py
python -m py_compile tools/dashboard/assistant_config.py

# Lint
ruff check tools/rag/codebase_indexer.py tools/dashboard/assistant_manager.py tools/dashboard/assistant_config.py

# Security
bandit -r tools/rag/codebase_indexer.py tools/dashboard/assistant_manager.py --severity-level medium

# Codebase indexer
python tools/rag/codebase_indexer.py --scan --json

# DB init
python tools/db/init_icdev_db.py
```

---

## 9. Related

- [Phase 44: Innovation Adaptation](phase-44-innovation-adaptation.md) — Multi-stream chat manager
- [Phase 51: Unified Chat Dashboard](phase-51-unified-chat-dashboard.md) — Chat page unification
- [Phase 64: RAG Subsystem](phase-52-code-intelligence.md) — RAG retriever, chunker, vector stores
