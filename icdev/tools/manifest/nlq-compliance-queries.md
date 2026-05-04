# NLQ Compliance Queries (Phase 40)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## NLQ Compliance Queries (Phase 40)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| NLQ Processor | tools/dashboard/nlq_processor.py | NLQ→SQL engine: schema extraction, Bedrock prompt, SQL validation, execution | query_text, actor | SQL results |
| SSE Manager | tools/dashboard/sse_manager.py | SSE connection manager: client tracking, event broadcasting, heartbeat | — | SSE stream |
| Events API | tools/dashboard/api/events.py | Blueprint: recent events, SSE stream, event ingest | GET/POST /api/events/* | Events |
| NLQ API | tools/dashboard/api/nlq.py | Blueprint: NLQ query, schema, history | POST /api/nlq/query | Query results |

