# [TEMPLATE: CUI // SP-CTI]
# Skill: icdev-query
# Natural Language Compliance Query

## Description
Query the ICDEV compliance database using natural language. Converts questions
to safe SQL queries and returns formatted results.

## Usage
/icdev-query <question>

## Examples
- /icdev-query Show all active projects
- /icdev-query List CAT1 STIG findings
- /icdev-query What is the compliance coverage for FedRAMP?
- /icdev-query Show recent audit trail entries
- /icdev-query How many hook events were logged today?

## Workflow
1. Extract ICDEV database schema
2. Generate SQL via Bedrock (or pattern fallback)
3. Validate SQL is read-only (security gate)
4. Execute with row limit and timeout
5. Log query to nlq_queries audit table
6. Format and display results

## Security
- Only SELECT queries allowed
- DROP/DELETE/UPDATE/INSERT/ALTER blocked
- Multi-statement queries blocked
- 500 row limit, 10 second timeout
- All queries logged for audit

## Tool
python tools/dashboard/nlq_processor.py
