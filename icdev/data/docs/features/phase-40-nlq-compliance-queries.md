# Phase 40 — NLQ Compliance Queries

**CUI // SP-CTI**

| Field | Value |
|-------|-------|
| Phase | 40 |
| Title | Natural Language Compliance Queries |
| Status | Implemented |
| Priority | P2 |
| Dependencies | Phase 39 (Observability & Operations), Phase 21 (SaaS Multi-Tenancy) |
| Author | ICDEV Architect Agent |
| Date | 2026-02-23 |

---

## 1. Problem Statement

ICDEV's operational database contains 193 tables spanning compliance assessments, audit trails, security findings, project status, agent telemetry, and supply chain data. Compliance officers, ISSOs, and program managers need to query this data to answer questions like "Show all CAT1 STIG findings for project X" or "Which projects have expired cATO evidence?" — but they lack SQL expertise and should not be expected to learn the database schema.

Prior to Phase 40, all compliance data access required either navigating dashboard pages (limited to pre-built views) or writing raw SQL queries against the database (requiring technical expertise and risking accidental data modification). Neither approach serves the needs of non-technical compliance stakeholders who need ad-hoc answers to specific compliance questions.

Furthermore, the append-only audit trail (NIST 800-53 AU controls) must be protected from any modification, even accidental. A natural language query interface that generates SQL must enforce strict read-only access, blocking any query that could modify data. The system must also maintain a full audit trail of all queries executed, including generated SQL, execution time, and query status.

---

## 2. Goals

1. Enable natural language queries against the ICDEV compliance database through the web dashboard `/query` page
2. Generate SQL from natural language using Amazon Bedrock (Claude) with schema context and few-shot examples
3. Enforce strict read-only SQL execution — block all DML/DDL operations (DROP, DELETE, UPDATE, INSERT, ALTER, CREATE, TRUNCATE, ATTACH, DETACH)
4. Provide a pattern-based fallback SQL generator for air-gapped environments without Bedrock access
5. Log every query to the `nlq_queries` audit table with generated SQL, execution status, and actor identity
6. Integrate with the SSE event system (Phase 39) for real-time query event streaming on the `/events` page

---

## 3. Architecture

```
User (Dashboard /query)
         │
         ↓
   NLQ Processor (nlq_processor.py)
         │
    ┌────┴────┐
    │         │
    ↓         ↓
 Bedrock   Pattern-Based
 (Claude)   (Fallback)
    │         │
    └────┬────┘
         │
         ↓
   SQL Validator ──→ BLOCKED (if DML/DDL detected)
         │
         ↓
   Safe Executor (read-only connection, 500 row limit, 10s timeout)
         │
         ├──→ nlq_queries table (audit trail)
         └──→ SSE Manager ──→ /events page
```

The NLQ processor extracts the database schema (tables, columns, types, row counts) and sends it to Bedrock alongside few-shot examples from `context/dashboard/nlq_examples.json` and a system prompt from `hardprompts/dashboard/nlq_system_prompt.md`. The generated SQL passes through a strict validator that rejects any non-SELECT query. Safe execution uses a read-only database connection with a 500-row limit and 10-second timeout.

When Bedrock is unavailable (air-gapped or rate-limited), a pattern-based fallback matches common query patterns to pre-built SQL templates. All queries are logged to `nlq_queries` with status (success, error, blocked) and forwarded to the SSE event stream.

---

## 4. Requirements

### 4.1 Natural Language Processing

#### REQ-40-001: NLQ-to-SQL Generation
The system SHALL generate SQL queries from natural language input using Amazon Bedrock (Claude) with database schema context, table descriptions, and few-shot examples.

#### REQ-40-002: Schema Context Injection
The system SHALL extract and inject the current database schema (table names, column names, column types, row counts) into the LLM prompt to enable accurate SQL generation.

#### REQ-40-003: Few-Shot Examples
The system SHALL use curated NLQ-to-SQL examples from `context/dashboard/nlq_examples.json` to improve generation accuracy for common compliance queries.

#### REQ-40-004: Pattern-Based Fallback
The system SHALL provide a pattern-based SQL generator that matches common query patterns when Bedrock is unavailable, ensuring air-gap compatibility.

### 4.2 Security Enforcement

#### REQ-40-005: Read-Only SQL Enforcement
The system SHALL reject any generated SQL that does not start with SELECT or WITH. The system SHALL block: DROP, DELETE, UPDATE, INSERT, ALTER, CREATE, TRUNCATE, ATTACH, DETACH, and multi-statement queries.

#### REQ-40-006: Dangerous PRAGMA Blocking
The system SHALL block dangerous SQLite PRAGMA statements that could modify database behavior or expose internal state.

#### REQ-40-007: Row Limit Enforcement
The system SHALL enforce a maximum row limit of 500 rows (configurable) on all query results to prevent resource exhaustion.

#### REQ-40-008: Query Timeout
The system SHALL enforce a 10-second execution timeout on all queries to prevent long-running operations from blocking the system.

### 4.3 Audit Trail

#### REQ-40-009: Query Audit Logging
Every query SHALL be logged to the `nlq_queries` table with: natural language input, generated SQL, execution status (success/error/blocked), execution time, result row count, and actor identity.

### 4.4 Dashboard Integration

#### REQ-40-010: Query Dashboard Page
The dashboard SHALL provide a `/query` page with a natural language input field, SQL preview, result table display, and query history.

#### REQ-40-011: SSE Event Integration
Query events SHALL be streamed to the `/events` SSE timeline, including query submission, execution, and result status.

---

## 5. Database Schema

### Tables

| Table | Purpose |
|-------|---------|
| `nlq_queries` | Audit trail of all NLQ queries — natural language input, generated SQL, status (success/error/blocked), execution time, row count, actor, timestamp |

---

## 6. Tools

| Tool | Purpose |
|------|---------|
| `tools/dashboard/nlq_processor.py` | NLQ-to-SQL pipeline — schema extraction, Bedrock generation, SQL validation, safe execution |
| `tools/dashboard/api/nlq.py` | Flask blueprint for NLQ API endpoints (POST /api/nlq/query) |
| `tools/dashboard/api/events.py` | Flask blueprint for SSE event streaming (shared with Phase 39) |
| `tools/dashboard/sse_manager.py` | SSE connection management (shared with Phase 39) |

---

## 7. Architecture Decisions

| ID | Decision | Rationale |
|----|----------|-----------|
| D30 | Bedrock for NLQ-to-SQL (not OpenAI) | Air-gap safe via GovCloud, consistent with D23 COA generation, available in government regions |
| D34 | Read-only SQL enforcement for NLQ | Append-only audit trail (D6) must not be compromised by NLQ queries; defense in depth |
| D29 | SSE for real-time query events | Consistent with Phase 39 SSE infrastructure; Flask-native, unidirectional |

---

## 8. Security Gate

**NLQ Security Gate:**
- All generated SQL must pass read-only validation (no DML/DDL)
- Blocked queries must be logged with full context for security review
- Query execution timeout must be enforced (10 seconds max)
- Row limit must be enforced (500 rows max)
- All queries must be audit-logged to `nlq_queries` table regardless of status
- Pattern-based fallback must be available for air-gapped deployments

---

## 9. Commands

```bash
# Start dashboard with NLQ support
python tools/dashboard/app.py

# Navigate to /query for natural language compliance queries
# Navigate to /events for real-time event timeline (SSE)

# Configuration
# args/nlq_config.yaml — Bedrock model, row limits, blocked SQL patterns, SSE heartbeat

# Context files
# context/dashboard/nlq_examples.json — Few-shot NLQ-to-SQL examples
# context/dashboard/schema_descriptions.json — Human-readable table descriptions

# Hard prompts
# hardprompts/dashboard/nlq_system_prompt.md — Bedrock system prompt for SQL generation
```
