# CUI // SP-CTI
"""NLQ-to-SQL processor — converts natural language to safe SQL queries.

Uses Amazon Bedrock (Claude) for SQL generation, with strict read-only enforcement.
Decision D30: Bedrock for NLQ→SQL (air-gap safe).
Decision D34: Read-only SQL enforcement.
"""

import json
import re
import sqlite3
import time
from pathlib import Path
from typing import Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"

# SQL Security: Blocked patterns (read-only enforcement)
BLOCKED_PATTERNS = [
    r"\bDROP\s+",
    r"\bDELETE\s+",
    r"\bUPDATE\s+.*\bSET\b",
    r"\bINSERT\s+",
    r"\bALTER\s+",
    r"\bCREATE\s+",
    r"\bTRUNCATE\s+",
    r"\bATTACH\s+",
    r"\bDETACH\s+",
    r"\bREPLACE\s+",
    r"\bMERGE\s+",
    r"\bGRANT\s+",
    r"\bREVOKE\s+",
    r";\s*(?:SELECT|DROP|DELETE|UPDATE|INSERT)",  # Multi-statement
    r"\bPRAGMA\s+(?!table_info|table_list)",  # Block all PRAGMA except read-only ones
]

MAX_ROWS = 500
QUERY_TIMEOUT_SECONDS = 10


def extract_schema(db_path: Path = None) -> dict:
    """Extract database schema: table names, columns, types, row counts."""
    path = db_path or DB_PATH
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    schema = {}

    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()

    for table_row in tables:
        table_name = table_row["name"]
        columns = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        row_count = conn.execute(f"SELECT COUNT(*) as cnt FROM {table_name}").fetchone()["cnt"]

        schema[table_name] = {
            "columns": [
                {
                    "name": col["name"],
                    "type": col["type"],
                    "notnull": bool(col["notnull"]),
                    "pk": bool(col["pk"]),
                    "default": col["dflt_value"],
                }
                for col in columns
            ],
            "row_count": row_count,
        }

    conn.close()
    return schema


def validate_sql(sql: str) -> tuple:
    """Validate SQL is read-only. Returns (is_valid, error_message)."""
    normalized = sql.strip()

    # Must start with SELECT or WITH
    if not re.match(r"^\s*(SELECT|WITH)\b", normalized, re.IGNORECASE):
        return False, "Only SELECT queries are allowed"

    # Check blocked patterns
    for pattern in BLOCKED_PATTERNS:
        if re.search(pattern, normalized, re.IGNORECASE):
            return False, f"Blocked SQL pattern detected: {pattern}"

    return True, None


def generate_sql_via_bedrock(query: str, schema: dict) -> Optional[str]:
    """Generate SQL from natural language using Amazon Bedrock."""
    try:
        # Load few-shot examples
        examples_path = BASE_DIR / "context" / "dashboard" / "nlq_examples.json"
        examples = []
        if examples_path.exists():
            with open(examples_path) as f:
                examples = json.load(f)

        # Load system prompt
        system_prompt_path = BASE_DIR / "hardprompts" / "dashboard" / "nlq_system_prompt.md"
        system_prompt = ""
        if system_prompt_path.exists():
            with open(system_prompt_path) as f:
                system_prompt = f.read()

        # Build schema context
        schema_text = "Database tables:\n"
        for table_name, info in schema.items():
            cols = ", ".join(f"{c['name']} ({c['type']})" for c in info["columns"])
            schema_text += f"- {table_name} ({info['row_count']} rows): {cols}\n"

        # Build examples context
        examples_text = ""
        if examples:
            examples_text = "\nExamples:\n"
            for ex in examples[:5]:
                examples_text += f"Q: {ex['question']}\nSQL: {ex['sql']}\n\n"

        prompt = f"""{system_prompt}

{schema_text}

{examples_text}

Convert this question to a SQL SELECT query. Return ONLY the SQL, no explanation.

Question: {query}
SQL:"""

        # Use vendor-agnostic LLM router (reads model from llm_config.yaml)
        from tools.llm import get_router
        from tools.llm.provider import LLMRequest
        router = get_router()
        llm_resp = router.invoke("nlq_sql", LLMRequest(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=500,
            temperature=0.0,
        ))
        sql = llm_resp.content.strip()

        # Clean up: remove markdown code blocks if present
        sql = re.sub(r"^```sql\s*", "", sql)
        sql = re.sub(r"\s*```$", "", sql)
        sql = sql.strip()

        return sql

    except ImportError:
        return _generate_sql_fallback(query, schema)
    except Exception as e:
        print(f"Bedrock NLQ error: {e}")
        return _generate_sql_fallback(query, schema)


def _generate_sql_fallback(query: str, schema: dict) -> Optional[str]:
    """Simple pattern-based SQL generation when Bedrock is unavailable."""
    query_lower = query.lower()

    # Simple patterns
    if "all projects" in query_lower or "list projects" in query_lower or "show projects" in query_lower:
        return "SELECT id, name, type, status, classification, created_at FROM projects ORDER BY created_at DESC LIMIT 100"

    if "active projects" in query_lower:
        return "SELECT id, name, type, classification, created_at FROM projects WHERE status = 'active' ORDER BY created_at DESC"

    if "cat1" in query_lower and "stig" in query_lower:
        return "SELECT * FROM stig_findings WHERE severity = 'CAT1' AND status = 'Open' ORDER BY created_at DESC"

    if "open poam" in query_lower or "poam items" in query_lower:
        return "SELECT * FROM poam_items WHERE status = 'open' ORDER BY severity, created_at DESC"

    if "recent audit" in query_lower or "audit trail" in query_lower:
        return "SELECT * FROM audit_trail ORDER BY created_at DESC LIMIT 50"

    if "agent" in query_lower and ("status" in query_lower or "list" in query_lower):
        return "SELECT * FROM agents ORDER BY name"

    if "vulnerabilit" in query_lower:
        return "SELECT * FROM dependency_vulnerabilities WHERE status = 'open' ORDER BY severity, created_at DESC LIMIT 100"

    if "deployment" in query_lower:
        return "SELECT * FROM deployments ORDER BY created_at DESC LIMIT 50"

    if "compliance" in query_lower and "score" in query_lower:
        return "SELECT project_id, framework_id, coverage_pct, gate_status, last_assessed FROM project_framework_status ORDER BY coverage_pct ASC"

    if "hook" in query_lower and "event" in query_lower:
        return "SELECT * FROM hook_events ORDER BY created_at DESC LIMIT 100"

    # Generic: try to find table name in query
    for table_name in schema:
        if table_name.replace("_", " ") in query_lower or table_name in query_lower:
            return f"SELECT * FROM {table_name} ORDER BY ROWID DESC LIMIT 100"

    return None


def execute_safely(sql: str, db_path: Path = None) -> dict:
    """Execute SQL with row limit and timeout. Returns results dict."""
    path = db_path or DB_PATH
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row

    # Set timeout
    conn.execute(f"PRAGMA busy_timeout = {QUERY_TIMEOUT_SECONDS * 1000}")

    try:
        cursor = conn.execute(sql)
        columns = [desc[0] for desc in cursor.description] if cursor.description else []
        rows = cursor.fetchmany(MAX_ROWS)
        len(rows)

        # Check if there are more rows
        extra = cursor.fetchone()
        truncated = extra is not None

        results = [dict(zip(columns, row)) for row in rows]

        return {
            "columns": columns,
            "rows": results,
            "row_count": len(results),
            "truncated": truncated,
            "max_rows": MAX_ROWS,
        }
    finally:
        conn.close()


def format_results(results: dict) -> dict:
    """Format query results for JSON response."""
    return {
        "columns": results.get("columns", []),
        "rows": results.get("rows", []),
        "row_count": results.get("row_count", 0),
        "truncated": results.get("truncated", False),
    }


def log_nlq_query(query_text: str, generated_sql: str, result_count: int,
                  execution_time_ms: int, actor: str, status: str,
                  error_message: str = None):
    """Log NLQ query to audit table."""
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.execute(
            """INSERT INTO nlq_queries
               (query_text, generated_sql, result_count, execution_time_ms,
                actor, status, error_message, classification)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'CUI')""",
            (query_text, generated_sql, result_count, execution_time_ms,
             actor, status, error_message),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass  # Best-effort logging


def process_nlq_query(query_text: str, actor: str = "dashboard-user") -> dict:
    """Full NLQ pipeline: generate SQL, validate, execute, log."""
    start_time = time.time()

    # Extract schema
    schema = extract_schema()

    # Generate SQL
    generated_sql = generate_sql_via_bedrock(query_text, schema)

    if not generated_sql:
        duration_ms = int((time.time() - start_time) * 1000)
        log_nlq_query(query_text, None, 0, duration_ms, actor, "error",
                      "Could not generate SQL from query")
        return {
            "status": "error",
            "error": "Could not generate SQL from your question. Try rephrasing.",
            "classification": "CUI",
        }

    # Validate SQL (read-only enforcement)
    is_valid, validation_error = validate_sql(generated_sql)
    if not is_valid:
        duration_ms = int((time.time() - start_time) * 1000)
        log_nlq_query(query_text, generated_sql, 0, duration_ms, actor, "blocked",
                      validation_error)
        return {
            "status": "blocked",
            "error": f"Query blocked by security policy: {validation_error}",
            "generated_sql": generated_sql,
            "classification": "CUI",
        }

    # Execute safely
    try:
        results = execute_safely(generated_sql)
        formatted = format_results(results)
        duration_ms = int((time.time() - start_time) * 1000)

        log_nlq_query(query_text, generated_sql, formatted["row_count"],
                      duration_ms, actor, "success")

        return {
            "status": "success",
            "query": query_text,
            "generated_sql": generated_sql,
            "results": formatted,
            "execution_time_ms": duration_ms,
            "classification": "CUI",
        }
    except Exception as e:
        duration_ms = int((time.time() - start_time) * 1000)
        log_nlq_query(query_text, generated_sql, 0, duration_ms, actor, "error",
                      str(e))
        return {
            "status": "error",
            "error": f"Query execution failed: {str(e)}",
            "generated_sql": generated_sql,
            "classification": "CUI",
        }
