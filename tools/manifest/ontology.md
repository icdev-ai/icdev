# Ontology

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Ontology
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| External Mappings | tools/ontology/external_mappings.py | External ontology mappings — STIX 2.1, MITRE ATT&CK, GeoSPARQL, OSCAL, DCAT. Provides local-only mappings from ICDEV ontology classes to external standards. | `--export stix\|oscal\|geosparql`, `--write-ttl`, `--json` | JSON mappings or TTL file |
| Ontology Federation | tools/ontology/federation.py | Cross-canvas ontology federation — merges domain ontologies into ICDEV Core graph, resolves equivalent classes, adds cross-domain properties, SPARQL-like queries, pre-computes rdfs:subClassOf transitive closure. The TTL directory is the OWNING PARENT's `args/ontology` (resolved through `icdev.core.paths`, so `ICDEV_PROJECT_ROOT` selects a second parent such as ICDEV[FT]); `--no-builtin` loads only the TTL files and skips the IT domain vocabulary hard-coded in the module. | `--build-federation`, `--ttl-dir <dir>`, `--no-builtin`, `--query`, `--list-domains`, `--list-classes`, `--integrate-kg`, `--resolve`, `--json` | JSON federation/query results |
| AI Game Engine Ontology | tools/ai_game_engine/ontology.py | AI GameDay ontology — strategy, security, war, and geospatial concept mappings. Defines `SCENARIO_ONTOLOGY` (scenario-to-class mappings) and `ROLE_ONTOLOGY` (role-to-class mappings) across four namespaces. Provides `resolve_scenario_ontology(slug)` and `resolve_role_ontology(role_id)` APIs. | (import) | dict of ontology tags |
| AI Game Scenario Registry | tools/ai_game_engine/scenario_registry.py | Ontology-enriched scenario registry — wraps TTX scenario loader and enriches output with ontology tags via `OntologyScenarioRegistry`. Public API: `list_slugs()`, `load(slug) -> dict`. | (import) | dict (enriched scenario) |
| Ontology Catalog | tools/ontology/ontology_catalog.py | Ontology Catalog Validator — validate ontology consistency and coverage. Checks IRIs, duplicate pairs, prefix definitions. | `--validate`, `--query`, `--json` | JSON validation results |
| Ontology Bridge | tools/canvas/ontology_bridge.py | Canvas-to-ontology URI bridge — maps canvas entity types to ICDEV ontology class URIs via `get_ontology_id(canvas_key, entity_type)`. Supports lazy canvas constant loading and fallback URI synthesis. | `get_ontology_id("idc", "aws-ec2")` | URI string or None |
