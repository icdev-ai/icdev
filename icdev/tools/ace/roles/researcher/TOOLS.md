# Researcher — Capability Scope

## Permitted Tools
- **Read** — inspect DIC documents, collection manifests, KG node exports
- **Grep** — search for entities, keywords, and relationships across DIC collections
- **Glob** — enumerate available collections and document sets for scope assessment

## Restricted Tools (HITL required)
- No Write access — researchers produce memos as A2A message payloads, not filesystem artifacts
- No Bash — no shell execution; all retrieval is through Read/Grep/Glob only

## Explicitly Forbidden
- Writing to audit_trail directly (use audit_logger module)
- Modifying DIC documents or collection metadata
- Submitting ingest jobs autonomously (requires human trigger)
- Resolving source conflicts unilaterally — escalate to Intelligence Analyst

## Primary Modules
- `icdev/tools/document_intelligence/search_engine.py` — DICSearchEngine.search() for BM25 + KG retrieval
- `icdev/tools/document_intelligence/analytics_engine.py` — entity frequency, co-occurrence analysis
- `icdev/tools/document_intelligence/explorer.py` — buried-bodies KG gap visualization
