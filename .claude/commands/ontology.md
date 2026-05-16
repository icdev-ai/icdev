# [TEMPLATE: CUI // SP-CTI]
# /ontology — Ontology Operations Workflow

Run ontology extraction, validation, federation, querying, external export, and RAG-aware code generation across the ICDEV™ knowledge graph.

## Variables

QUERY: The ontology query string (default: "AWS VPC")
EXPORT_FORMAT: External mapping format — stix, oscal, or geosparql (default: stix)
PROMPT: The code-generation prompt for ontology-aware RAG (default: "Generate a Python class for the queried concept")

## Workflow

1. **Extract Schema**
   Dry-run schema extraction from the current knowledge graph:
   ```bash
   python tools/ontology/schema_extractor.py --dry-run --json
   ```

2. **Validate Catalog**
   Validate the ontology catalog for consistency and coverage:
   ```bash
   python tools/ontology/ontology_catalog.py --validate --json
   ```

3. **Build Federation**
   Merge domain ontologies into the ICDEV™ Core graph:
   ```bash
   python tools/ontology/federation.py --build --json
   ```

4. **Query Ontology**
   Run a SPARQL-like query over the unified ontology:
   ```bash
   python tools/ontology/federation.py --query "$QUERY" --json
   ```

5. **Export External Mappings**
   Export ICDEV™ ontology classes to an external standard:
   ```bash
   python tools/ontology/external_mappings.py --export "$EXPORT_FORMAT" --json
   ```

6. **List Ontology Classes**
   List all registered classes (optionally filtered by domain):
   ```bash
   python tools/ontology/federation.py --list-classes --json
   ```

## Notes

- All commands use the allowlisted `python tools/...` prefix.
- Results are returned in JSON for downstream chaining.
- If `$QUERY`, `$EXPORT_FORMAT`, or `$PROMPT` are empty, use the defaults above.
- Schema extraction and catalog validation are safe to run in dry-run mode.
- Federation build modifies the knowledge graph; run validation first.
