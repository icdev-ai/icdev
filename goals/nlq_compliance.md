# CUI // SP-CTI
# NLQ Compliance Query Goal — Natural Language Database Queries

## Purpose
Enable natural language queries against the ICDEV compliance database through the
web dashboard, with read-only enforcement and full audit trail.

## Trigger
- Dashboard `/query` page or POST to `/api/nlq/query`

## Workflow

### 1. Query Input
User types natural language question (e.g., "Show all CAT1 STIG findings")

### 2. SQL Generation
- Extract database schema (tables, columns, types, row counts)
- Send to Amazon Bedrock (Claude) with:
  - Schema context
  - Few-shot examples from `context/dashboard/nlq_examples.json`
  - System prompt from `hardprompts/dashboard/nlq_system_prompt.md`
- Fallback: pattern-based generation when Bedrock unavailable

### 3. SQL Validation (Security Gate)
- MUST start with SELECT or WITH
- Block: DROP, DELETE, UPDATE, INSERT, ALTER, CREATE, TRUNCATE, ATTACH, DETACH
- Block: Multi-statement queries (semicolons between statements)
- Block: Dangerous PRAGMAs
- If blocked → log to `nlq_queries` with status='blocked', return error

### 4. Safe Execution
- Execute with row limit (500 max)
- Timeout: 10 seconds
- Read-only connection

### 5. Audit Trail
- Every query logged to `nlq_queries` table
- Status: success, error, blocked
- Includes generated SQL, execution time, actor

## Tools Used
| Tool | Purpose |
|------|---------|
| `nlq_processor.py` | NLQ→SQL pipeline (generate, validate, execute) |
| `api/nlq.py` | Flask blueprint API endpoints |
| `api/events.py` | SSE event streaming blueprint |
| `sse_manager.py` | SSE connection management |

## Args
- `args/nlq_config.yaml` — NLQ settings

## Context
- `context/dashboard/nlq_examples.json` — Few-shot NLQ→SQL examples
- `context/dashboard/schema_descriptions.json` — Human-readable table descriptions

## Hard Prompts
- `hardprompts/dashboard/nlq_system_prompt.md` — Bedrock system prompt

## Success Criteria
- "Show all projects" → returns project list
- "Delete all projects" → blocked by security policy
- All queries logged in `nlq_queries` table
- SSE stream reflects real-time events
