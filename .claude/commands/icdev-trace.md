# [TEMPLATE: CUI // SP-CTI]
# /icdev-trace — Observability, Tracing & Explainable AI

Query distributed traces, provenance lineage, run AgentSHAP analysis, and assess
XAI compliance for ICDEV projects.

## Workflow

1. **Identify the Request**
   Determine what the user wants:
   - **Trace query** — find traces/spans by ID, project, or name
   - **Trace summary** — aggregate statistics (span count, errors, avg duration)
   - **Provenance lineage** — "what produced this?" or "what did this produce?"
   - **SHAP analysis** — tool importance attribution for a trace
   - **XAI assessment** — full compliance assessment (10 checks)
   - **Status overview** — combined observability dashboard summary

2. **Trace Query**
   ```bash
   # Query specific trace
   python -c "
   import sqlite3, json
   conn = sqlite3.connect('data/icdev.db')
   conn.row_factory = sqlite3.Row
   rows = conn.execute('SELECT * FROM otel_spans WHERE trace_id = ? ORDER BY start_time', ('TRACE_ID',)).fetchall()
   print(json.dumps([dict(r) for r in rows], indent=2))
   conn.close()
   "

   # Query recent spans for a project
   python -c "
   import sqlite3, json
   conn = sqlite3.connect('data/icdev.db')
   conn.row_factory = sqlite3.Row
   rows = conn.execute('SELECT * FROM otel_spans WHERE project_id = ? ORDER BY start_time DESC LIMIT 50', ('PROJECT_ID',)).fetchall()
   print(json.dumps([dict(r) for r in rows], indent=2))
   conn.close()
   "
   ```

3. **Trace Summary**
   ```bash
   python -c "
   import sqlite3, json
   conn = sqlite3.connect('data/icdev.db')
   stats = {
       'total_spans': conn.execute('SELECT COUNT(*) FROM otel_spans').fetchone()[0],
       'total_traces': conn.execute('SELECT COUNT(DISTINCT trace_id) FROM otel_spans').fetchone()[0],
       'mcp_tool_calls': conn.execute(\"SELECT COUNT(*) FROM otel_spans WHERE name = 'mcp.tool_call'\").fetchone()[0],
       'error_spans': conn.execute(\"SELECT COUNT(*) FROM otel_spans WHERE status_code = 'ERROR'\").fetchone()[0],
   }
   avg = conn.execute('SELECT AVG(duration_ms) FROM otel_spans').fetchone()[0]
   stats['avg_duration_ms'] = round(avg, 2) if avg else 0
   print(json.dumps(stats, indent=2))
   conn.close()
   "
   ```

4. **Provenance Lineage**
   ```bash
   # Backward lineage (what produced this entity?)
   python tools/observability/provenance/prov_query.py --entity-id ENTITY_ID --direction backward --json

   # Forward lineage (what did this entity produce?)
   python tools/observability/provenance/prov_query.py --entity-id ENTITY_ID --direction forward --json

   # Export PROV-JSON
   python tools/observability/provenance/prov_export.py --project-id PROJECT_ID --json
   ```

5. **AgentSHAP Analysis**
   ```bash
   # Analyze a specific trace
   python tools/observability/shap/agent_shap.py --trace-id TRACE_ID --iterations 1000 --json

   # Analyze last N traces for a project
   python tools/observability/shap/agent_shap.py --project-id PROJECT_ID --last-n 10 --json
   ```
   Review the Shapley values — higher values indicate more important tools for the outcome.

6. **XAI Compliance Assessment**
   ```bash
   # Run assessment
   python tools/compliance/xai_assessor.py --project-id PROJECT_ID --json

   # Gate evaluation (pass/fail)
   python tools/compliance/xai_assessor.py --project-id PROJECT_ID --gate
   ```
   Report the 10 check results and overall coverage percentage.

7. **Report Results**
   Present findings to the user:
   - For traces: span count, error rate, slowest spans, span waterfall
   - For provenance: lineage chain, entity relationships
   - For SHAP: tool importance ranking with Shapley values
   - For XAI: coverage percentage, satisfied/not_satisfied checks, remediation suggestions

## Dashboard Pages
Direct the user to the dashboard for visual exploration:
- `/traces` — Trace explorer with span waterfall
- `/provenance` — Provenance DAG viewer
- `/xai` — XAI assessment dashboard with SHAP chart

## MCP Tools
The `icdev-observability` MCP server provides these tools:
- `trace_query` — Query traces by ID, project, or name
- `trace_summary` — Aggregate trace statistics
- `prov_lineage` — Query provenance lineage for an entity
- `prov_export` — Export provenance graph as PROV-JSON
- `shap_analyze` — Run AgentSHAP tool attribution
- `xai_assess` — Run XAI compliance assessment
