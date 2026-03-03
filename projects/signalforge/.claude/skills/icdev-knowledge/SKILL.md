---
name: icdev-knowledge
description: Query, search, and update the ICDEV learning knowledge base for patterns, solutions, and recommendations
context: fork
allowed-tools: Bash, Read, Write, Edit, Glob, Grep
---

# /icdev-knowledge — Knowledge Base Management

## Usage
```
/icdev-knowledge <action> [options]

Actions:
  search <query>           Search for patterns and solutions
  add <name>               Add a new pattern to the knowledge base
  recommend <project-id>   Get improvement recommendations
  analyze <error-message>  Analyze a failure for root cause
  stats                    Show knowledge base statistics
```

## What This Does
Manages the self-learning knowledge base that powers self-healing:
1. Search for known patterns, solutions, and best practices
2. Add new patterns discovered during development/operations
3. Get project-specific improvement recommendations
4. Analyze failures to determine root cause
5. View knowledge base statistics and health

## Steps

### Action: search
Use the `search_knowledge` MCP tool from icdev-knowledge:
- query: search terms from `$ARGUMENTS`
- pattern_type: optional filter (error, performance, security, compliance, deployment, configuration)
- limit: default 10

Display matching patterns with:
- Name, type, description
- Detection rule
- Solution
- Confidence score
- Use count

### Action: add
Use the `add_pattern` MCP tool from icdev-knowledge:
- name: from arguments
- Interactive prompts for:
  - pattern_type (error/performance/security/compliance/deployment/configuration)
  - description
  - detection_rule (regex, log pattern, or metric threshold)
  - solution (remediation steps)
  - auto_healable (true/false)
  - confidence (0.0-1.0)

### Action: recommend
Use the `get_recommendations` MCP tool from icdev-knowledge:
- project_id: from arguments
- Returns recommendations based on:
  - Recent failure history
  - Common error patterns
  - Knowledge base pattern matching

### Action: analyze
Use the `analyze_failure` MCP tool from icdev-knowledge:
- error_message: from arguments
- Matches against known patterns
- Returns:
  - Matching patterns with confidence
  - Root cause determination
  - Suggested actions
  - Whether auto-healable

### Action: stats
Query knowledge base statistics:
```bash
python -c "
import sqlite3
conn = sqlite3.connect('data/icdev.db')
patterns = conn.execute('SELECT COUNT(*) FROM knowledge_patterns').fetchone()[0]
heals = conn.execute('SELECT COUNT(*) FROM self_healing_events').fetchone()[0]
failures = conn.execute('SELECT COUNT(*) FROM failure_log').fetchone()[0]
print(f'Patterns: {patterns}')
print(f'Self-heal events: {heals}')
print(f'Failures logged: {failures}')
"
```

## Self-Healing Integration
The knowledge base powers the self-healing system:
- Patterns with confidence >= 0.7 and auto_healable=true trigger automatic remediation
- Each successful heal increases the pattern's confidence and use_count
- Failed heals decrease confidence
- This creates a feedback loop that improves over time

## Example
```
/icdev-knowledge search "database connection timeout"
/icdev-knowledge add "OOM Kill Pattern"
/icdev-knowledge recommend abc123-uuid
/icdev-knowledge analyze "ConnectionRefusedError: [Errno 111] Connection refused"
/icdev-knowledge stats
```

## Error Handling
- If knowledge DB empty: suggest running /icdev-secure and /icdev-test to generate initial patterns
- If search returns no results: suggest broader query terms
- If pattern already exists (by name): update existing instead of duplicate
