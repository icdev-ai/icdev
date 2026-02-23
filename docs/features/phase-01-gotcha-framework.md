# Phase 1 — GOTCHA Framework Foundation

**CUI // SP-CTI**

| Field | Value |
|-------|-------|
| Phase | 1 |
| Title | GOTCHA Framework Foundation |
| Status | Implemented |
| Priority | P0 |
| Dependencies | None |
| Author | ICDEV Architect Agent |
| Date | 2026-02-23 |

---

## 1. Problem Statement

Large Language Models are probabilistic systems. When used directly for business logic, accuracy compounds negatively across steps: 90% accuracy per step yields only ~59% accuracy over 5 sequential steps. In government and DoD environments, this compounding unreliability is unacceptable. Systems handling CUI, FOUO, or SECRET information must produce deterministic, auditable, and reproducible results.

The fundamental challenge is separating what LLMs are good at (orchestration, decision-making, natural language understanding) from what must be deterministic (business logic, compliance checks, security scanning, infrastructure generation). Without a structured framework, AI-assisted development devolves into "vibe coding" -- producing demos that cannot survive production scrutiny, compliance audits, or operational stress.

ICDEV requires a foundational architecture that enforces this separation of concerns while providing a complete operational model: persistent memory across sessions, manifest-driven tool discovery, goal-driven workflows, configurable behavior, reusable context, and templated prompts. This is the GOTCHA framework.

---

## 2. Goals

1. Establish a 6-layer architecture (Goals, Orchestration, Tools, Args, Context, Hard Prompts) that enforces separation between probabilistic AI orchestration and deterministic tool execution
2. Implement a manifest system (`goals/manifest.md`, `tools/manifest.md`) that serves as a single source of truth for all available workflows and tools
3. Build a dual-storage memory system (markdown files for human readability + SQLite databases for machine searchability) with 6 memory types
4. Define a session start protocol that loads long-term context, daily logs, and project state before any work begins
5. Create a guardrails system that prevents common failure modes (tool duplication, unverified output chaining, destructive operations)
6. Provide configurable behavior through YAML/JSON args files that change system behavior without modifying goals or tools
7. Enable static reference material (context/) and reusable LLM instruction templates (hardprompts/) for consistent outputs

---

## 3. Architecture

### 3.1 The 6 Layers

```
+---------------------------------------------------------------+
|                    GOTCHA Framework                             |
|                                                                |
|  +-------------------+    +-----------------------------+      |
|  | GOALS             |    | ORCHESTRATION (LLM)         |      |
|  | goals/            |    | Read goal -> decide tool    |      |
|  | Process defs,     |--->| order -> apply args ->      |      |
|  | workflows,        |    | reference context ->        |      |
|  | expected outputs  |    | handle errors               |      |
|  +-------------------+    +----+---+----+----+----------+      |
|                                |   |    |    |                 |
|          +-----------+---------+   |    |    +--------+        |
|          v           v             v    v             v        |
|  +------------+ +---------+ +---------+ +-----------+          |
|  | TOOLS      | | ARGS    | | CONTEXT | | HARD      |          |
|  | tools/     | | args/   | | context/| | PROMPTS   |          |
|  | Python     | | YAML/   | | Static  | | hardprompts/         |
|  | scripts,   | | JSON    | | ref     | | Reusable  |          |
|  | one job    | | behavior| | material| | LLM       |          |
|  | each,      | | settings| | tone,   | | templates |          |
|  | determin-  | |         | | samples | |           |          |
|  | istic      | |         | |         | |           |          |
|  +------------+ +---------+ +---------+ +-----------+          |
+---------------------------------------------------------------+
```

### 3.2 Memory System

```
+-------------------------------------+
|         MEMORY SYSTEM               |
|                                     |
|  +---------------+  +------------+  |
|  | Markdown      |  | SQLite     |  |
|  | (Human)       |  | (Machine)  |  |
|  |               |  |            |  |
|  | MEMORY.md     |  | memory.db  |  |
|  | logs/YYYY-    |  |  - entries |  |
|  |   MM-DD.md    |  |  - logs    |  |
|  |               |  |  - access  |  |
|  +---------------+  +------------+  |
|                                     |
|  Types: fact, preference, event,    |
|         insight, task, relationship |
|                                     |
|  Search: hybrid = 0.7*BM25 +       |
|          0.3*semantic (vector)      |
|                                     |
|  Embeddings: text-embedding-3-small |
|              1536 dims, BLOB store  |
+-------------------------------------+
```

### 3.3 Key Files

| File | Purpose |
|------|---------|
| `goals/manifest.md` | Index of all goal workflows |
| `tools/manifest.md` | Master list of all tools |
| `memory/MEMORY.md` | Curated long-term facts and preferences |
| `memory/logs/YYYY-MM-DD.md` | Daily session logs |
| `.env` | API keys and environment variables |
| `.tmp/` | Disposable scratch work |
| `args/project_defaults.yaml` | Default TDD, compliance, security, infra settings |

---

## 4. Requirements

### 4.1 Layer Separation

#### REQ-01-001: Deterministic Tool Execution
The system SHALL execute all business logic through deterministic Python scripts in `tools/`, never through direct LLM generation of business-critical outputs.

#### REQ-01-002: Single Responsibility Tools
Each tool in `tools/` SHALL perform exactly one job, accepting structured inputs and producing structured outputs.

#### REQ-01-003: Orchestration Layer
The AI orchestration layer SHALL read goals, decide tool execution order, apply args, reference context, and handle errors without directly executing business logic.

#### REQ-01-004: Configurable Behavior
The system SHALL support YAML/JSON configuration files in `args/` that modify tool behavior without requiring changes to goal definitions or tool code.

### 4.2 Manifest System

#### REQ-01-005: Goal Manifest
The system SHALL maintain a `goals/manifest.md` file indexing all available goal workflows with file paths and descriptions.

#### REQ-01-006: Tool Manifest
The system SHALL maintain a `tools/manifest.md` file indexing all available tools, checked before creating any new script.

#### REQ-01-007: Manifest Verification
The orchestration layer SHALL check the relevant manifest before starting any task or creating any new tool.

### 4.3 Memory System

#### REQ-01-008: Dual Storage
The memory system SHALL store data in both markdown files (human-readable) and SQLite databases (machine-searchable).

#### REQ-01-009: Memory Types
The memory system SHALL support 6 memory types: fact, preference, event, insight, task, and relationship.

#### REQ-01-010: Hybrid Search
The memory system SHALL support hybrid search combining BM25 keyword search (weight 0.7) and semantic vector search (weight 0.3), configurable via CLI flags.

#### REQ-01-011: Embedding Storage
The memory system SHALL store OpenAI text-embedding-3-small embeddings (1536 dimensions) as BLOBs in SQLite for vector similarity search.

#### REQ-01-012: Session Start Protocol
The system SHALL load `memory/MEMORY.md`, today's daily log, and yesterday's log at the start of every session before performing any work.

### 4.4 Guardrails

#### REQ-01-013: Tool Duplication Prevention
The system SHALL check `tools/manifest.md` before writing any new script to prevent duplicate tool creation.

#### REQ-01-014: Output Verification
The system SHALL verify tool output format before chaining output into another tool.

#### REQ-01-015: Intermediate Output Preservation
When a workflow fails mid-execution, the system SHALL preserve intermediate outputs before retrying.

---

## 5. Database Schema

### Tables

| Table | Purpose |
|-------|---------|
| `memory_entries` | All memory entries with embeddings, types, importance scores |
| `daily_logs` | Daily session log entries linked to markdown files |
| `memory_access_log` | Tracks when and how memory entries are accessed |
| `tasks` | Activity tracking in `data/activity.db` |

---

## 6. Tools

| Tool | Purpose |
|------|---------|
| `tools/memory/memory_read.py` | Load all memory (markdown format) |
| `tools/memory/memory_write.py` | Write to daily log + DB, update MEMORY.md sections |
| `tools/memory/memory_db.py` | Keyword search across memory database |
| `tools/memory/semantic_search.py` | Semantic vector search (requires OpenAI key) |
| `tools/memory/hybrid_search.py` | Combined keyword + semantic search (best results) |
| `tools/memory/embed_memory.py` | Generate embeddings for all memory entries |
| `tools/memory/time_decay.py` | Time-decay scoring for memory ranking |
| `tools/db/init_icdev_db.py` | Initialize the ICDEV database |
| `tools/project/project_create.py` | Create new project records |
| `tools/project/project_list.py` | List all projects |
| `tools/project/project_status.py` | Get project status |

---

## 7. Architecture Decisions

| ID | Decision | Rationale |
|----|----------|-----------|
| D1 | SQLite for ICDEV internals; PostgreSQL for apps ICDEV builds | Zero-config portability for the framework itself; production-grade DB for generated applications |
| D6 | Audit trail is append-only/immutable (no UPDATE/DELETE) | Satisfies NIST 800-53 AU controls; ensures tamper-evident audit records |
| D168 | Time-decay uses exponential formula with per-type half-lives | Memory relevance degrades naturally; events decay fast (7d), facts slowly (90d), preferences slowest (180d) |

---

## 8. Security Gate

**No dedicated security gate for Phase 1.**

Phase 1 establishes the foundational architecture. Security gates are enforced starting in Phase 5 (Security Scanning) and Phase 7 (Code Review Gates). However, Phase 1 establishes the critical guardrail:

- Audit trail is append-only -- no UPDATE/DELETE operations permitted on audit tables
- Memory system uses SHA-256 hashing for entry integrity
- `.env` files excluded from version control

---

## 9. Commands

```bash
# Initialize framework (first run)
/initialize                    # Custom slash command -- sets up all dirs, manifests, memory, databases

# Memory system
python tools/memory/memory_read.py --format markdown          # Load all memory
python tools/memory/memory_write.py --content "text" --type event  # Write to daily log + DB
python tools/memory/memory_write.py --content "text" --type fact --importance 7  # Store a fact
python tools/memory/memory_write.py --update-memory --content "text" --section user_preferences  # Update MEMORY.md
python tools/memory/memory_db.py --action search --query "keyword"   # Keyword search
python tools/memory/semantic_search.py --query "concept"             # Semantic search
python tools/memory/hybrid_search.py --query "query"                 # Combined keyword + semantic
python tools/memory/embed_memory.py --all                            # Generate embeddings for all entries
python tools/memory/time_decay.py --rank --query "keyword" --top-k 10 --json  # Time-decay ranked search

# Database initialization
python tools/db/init_icdev_db.py                    # Initialize ICDEV database

# Project management
python tools/project/project_create.py --name "my-app" --type microservice
python tools/project/project_list.py
python tools/project/project_status.py --project-id "proj-123"

# Health check
python tools/testing/health_check.py                 # Full system health check
python tools/testing/health_check.py --json           # JSON output
```
