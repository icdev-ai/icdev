# Phase DDC — Column-Level Lineage & External Catalog Sync

**CUI // SP-CTI**

| Field | Value |
|-------|-------|
| Phase | DDC Lineage |
| Title | Data Design Canvas — Column-Level Lineage, Data Contracts & External Catalog Sync |
| Status | Complete |
| Priority | P1 |
| Dependencies | Phase 72 (ICDEV Studio / 7-Canvas Suite), DDC Blueprint (tools/data_canvas/blueprint.py) |
| Author | ICDEV™ Architect Agent |
| Date | 2026-04-18 |

---

## 1. Problem Statement

Data governance for government and DoD programs requires knowing not just what data exists, but how it flows — column to column, system to system, across classification boundaries. Without machine-readable lineage, auditors cannot trace a classified field from its origin to every downstream consumer, data engineers cannot assess the blast radius of a schema change, and compliance officers cannot certify that CUI never touches an unclassified pipeline.

Existing tools (OpenMetadata, DataHub) provide catalog infrastructure but require external integrations and do not enforce classification-aware lineage policies. The DDC Lineage phase adds a first-class, deterministic column-level lineage layer directly inside the Data Design Canvas: graph construction, impact analysis, provenance tracing, data contract assertions, and bidirectional sync with external catalog tools — all without any LLM dependency.

---

## 2. Goals

1. Represent column-to-column lineage as a typed, directed acyclic graph (DAG) stored in `dd_lineage`
2. Enforce 8 lineage types covering all common transformation patterns (derive, join, filter, aggregate, union, cast, passthrough, column-lineage)
3. Detect classification violations at the edge level (SECRET/CUI cannot downgrade to UNCLASSIFIED)
4. Generate 5 deterministic data contract assertions against any lineage graph
5. Compute downstream impact and upstream provenance via BFS (max depth 20)
6. Sync DDC designs to OpenMetadata (11 entity types, column tags, lineage aspects)
7. Sync DDC designs to DataHub GMS (12 entity types, UpstreamLineage aspect)
8. Pull external lineage from OpenMetadata into DDC via a read-only REST client

---

## 3. Architecture

```
+-----------------------------------+
|  DDC Blueprint (blueprint.py)     |
|  REST API: /api/designs/*/lineage |
+------------------+----------------+
                   |
        +----------v-----------+
        |  lineage.py          |   <-- Core DAG engine
        |  - build_dag()       |
        |  - downstream_impact |
        |  - upstream_prov.    |
        |  - contract_assert.  |
        +----------+-----------+
                   |
        +----------v-----------+
        |  data_engine.py      |   <-- Assessment wrapper
        |  - assess()          |
        |  - nist_coverage()   |
        |  - detect_gaps()     |
        +----------+-----------+
                   |
         +---------+---------+
         |                   |
+--------v-------+  +--------v-------+
| openmetadata_  |  |  datahub_      |
| sync.py        |  |  sync.py       |
| (push to OM)   |  | (push to DH)   |
+----------------+  +----------------+
         |
+--------v-------+
| clients/       |
| openmetadata.py|   <-- Read-only pull client
| (pull from OM) |
+----------------+
```

---

## 4. Key Components

### 4.1 Lineage Engine (`tools/data_canvas/lineage.py`)

The lineage engine is a pure-Python, LLM-free module. All operations are deterministic.

**Lineage Types (8)**

| Type | Use Case |
|------|----------|
| `flow-column-lineage` | Default: generic column-level data flow |
| `col-derive` | Computed expression (e.g., `full_name = first + last`) |
| `col-join` | JOIN predicate column (foreign key linkage) |
| `col-filter` | WHERE/HAVING clause column |
| `col-aggregate` | GROUP BY / SUM / COUNT / AVG |
| `col-union` | UNION / INTERSECT / EXCEPT |
| `col-cast` | Type conversion (e.g., `VARCHAR → DATE`) |
| `col-passthrough` | Unchanged copy across system boundary |

**Public API**

| Function | Purpose |
|----------|---------|
| `validate_lineage_edge(edge)` | Pre-insert validation; rejects self-loops and missing required fields |
| `build_column_lineage_dag(design_id)` | Constructs full DAG from `dd_lineage` records |
| `compute_downstream_impact(dag, start_node_id, max_depth=20)` | BFS forward: all columns affected by a change |
| `compute_upstream_provenance(dag, end_node_id, max_depth=20)` | BFS backward: all upstream sources for a column |
| `summarize_lineage(dag)` | Metrics: edge count, unique columns, type breakdown, cross-boundary flags, orphan nodes |
| `generate_contract_assertions(dag)` | Runs 5 contract checks; returns list of assertion results with pass/fail status |

### 4.2 Data Contract Assertions (5)

| Assertion | ID | Description | Severity |
|-----------|----|-------------|---------|
| No self-loop edges | A1 | Source and target node IDs must differ | CAT1 |
| Valid lineage type | A2 | Edge `lineage_type` must be one of the 8 allowed values | CAT2 |
| Classification non-escalation | A3 | SECRET/CUI edges must not target UNCLASSIFIED nodes | CAT1 |
| Dangling references | A4 | All node IDs referenced in edges must exist in the graph | CAT2 |
| Column name presence | A5 | Column-level edges must carry a non-empty `column_name` | CAT3 |

CAT1 assertions block the compliance gate; CAT2 generate findings; CAT3 produce warnings.

### 4.3 Data Assessment Engine (`tools/data_canvas/data_engine.py`)

Runs 12 deterministic compliance rules against the DDC graph. No LLM dependency.

**Rule Categories and NIST Families**

| Category | Rules | NIST 800-53 Families |
|----------|-------|---------------------|
| Encryption | DDC-ENC-001, DDC-ENC-002 | SC |
| Access Control | DDC-AC-001, DDC-AC-002 | AC, IA |
| Audit | DDC-AU-001, DDC-AU-002 | AU |
| Retention | DDC-RET-001, DDC-RET-002 | MP |
| Availability | DDC-AVL-001 | CP |
| Classification | DDC-CLS-001, DDC-CLS-002 | MP, SC |
| Data Protection | DDC-DLP-001 | SI, PT, SR |

**Scoring**

- `risk_score`: 0–100 (higher = more risk)
- `posture_grade`: A (0–19) / B (20–39) / C (40–59) / D (60–79) / F (80–100)

### 4.4 OpenMetadata Sync (`tools/data_canvas/sync/openmetadata_sync.py`)

One-way push from DDC to OpenMetadata. Uses stdlib `urllib` only — no `requests` or external packages.

**Entity Type Mapping (11)**

| DDC Type | OpenMetadata Service Type |
|----------|--------------------------|
| table, view | CustomSQL |
| collection | CustomNoSQL |
| topic, queue | CustomMessaging |
| datalake, file | CustomStorage |
| warehouse | CustomDWH |
| cache | CustomCache |
| graph | CustomGraph |
| timeseries, vector | CustomTimeSeries |
| etl, api, replication, cdc | CustomPipeline |

**Column Tags Pushed**

`DDC.PII`, `DDC.PHI`, `DDC.CUI`, `DDC.SECRET`, `DDC.Encrypted`

**CLI Usage**
```bash
# Sync a single design
python tools/data_canvas/sync/openmetadata_sync.py --design-id abc123 --json

# Sync all designs (dry run first)
python tools/data_canvas/sync/openmetadata_sync.py --all --dry-run --json

# Gate mode (exits 1 on failure)
python tools/data_canvas/sync/openmetadata_sync.py --all --gate --json
```

**Configuration**
```bash
ICDEV_OM_URL=http://openmetadata.internal:8585
ICDEV_OM_TOKEN=<jwt>
ICDEV_OM_TIMEOUT=30   # seconds
```

### 4.5 DataHub Sync (`tools/data_canvas/sync/datahub_sync.py`)

One-way push from DDC to DataHub GMS (v0.10.x–v0.14.x). Uses stdlib `urllib` only.

**Platform Aliases (12)**

`ddc_sql`, `ddc_nosql`, `ddc_kafka`, `ddc_s3`, `ddc_warehouse`, `ddc_cache`, `ddc_queue`, `ddc_graph`, `ddc_timeseries`, `ddc_vector`, `ddc_file`

**Flow Subtypes (8)**

`ETL`, `API`, `Replication`, `CDC`, `Backup`, `Export`, `CrossDomain`, `ColumnLineage`

**UpstreamLineage Aspect** — populated from `dd_lineage` records on push.

**CLI Usage**
```bash
python tools/data_canvas/sync/datahub_sync.py --design-id abc123 --json
python tools/data_canvas/sync/datahub_sync.py --all --dry-run --json
```

**Configuration**
```bash
ICDEV_DATAHUB_URL=http://datahub-gms.internal:8080
ICDEV_DATAHUB_TOKEN=<token>
ICDEV_DATAHUB_ENV=PROD   # DataHub environment tag
ICDEV_DATAHUB_TIMEOUT=30
```

### 4.6 OpenMetadata Pull Client (`tools/data_canvas/clients/openmetadata.py`)

Read-only REST client for pulling external lineage into DDC. Supports paginated table listing and upstream/downstream lineage retrieval.

```python
from tools.data_canvas.clients.openmetadata import OpenMetadataClient

client = OpenMetadataClient()
tables = client.list_tables(limit=100)          # paginated, returns [] on error
lineage = client.get_lineage(table_id,           # returns {} on error
                             upstream_depth=2,
                             downstream_depth=2)
```

---

## 5. Database Schema

**Table: `dd_lineage`**

| Column | Type | Description |
|--------|------|-------------|
| id | TEXT PK | UUID |
| design_id | TEXT FK | References `data_designs.id` |
| source_node_id | TEXT | Source graph node |
| target_node_id | TEXT | Target graph node |
| lineage_type | TEXT | One of 8 allowed types |
| column_name | TEXT | Required for column-level edges |
| transform_desc | TEXT | Human-readable transform description |
| classification | TEXT | Edge classification level |
| created_at | TEXT | ISO-8601 timestamp |

---

## 6. REST API Routes

| Method | Route | Description |
|--------|-------|-------------|
| GET | `/api/designs/<id>/lineage` | List all lineage edges for a design |
| POST | `/api/designs/<id>/lineage` | Add a column-level lineage edge (validated) |
| DELETE | `/api/designs/<id>/lineage/<edge_id>` | Remove a lineage edge |
| POST | `/api/designs/<id>/assess` | Run full compliance assessment with lineage summary |
| GET | `/api/designs/<id>/assessments` | List assessment history |
| POST | `/api/sync/openmetadata` | Push design(s) to OpenMetadata |
| POST | `/api/sync/datahub` | Push design(s) to DataHub GMS |

---

## 7. Security & Compliance Controls

| Control | Implementation |
|---------|---------------|
| Classification enforcement | Every lineage edge carries a `classification` tag; CAT1 assertion blocks SECRET→UNCLASSIFIED flows |
| Audit trail | All lineage writes recorded in `dd_audit` (append-only) |
| No LLM dependency | All lineage, assessment, and sync operations are deterministic |
| Air-gap compatibility | Both sync clients use stdlib `urllib` only — no external packages required |
| NIST 800-53 coverage | 12 rules map to AC, AU, CP, IA, MP, SC, SI, PT, SR families |

---

## 8. Acceptance Criteria

- [x] `dd_lineage` table created and seeded in `data_canvas.db`
- [x] `validate_lineage_edge` rejects self-loops, invalid types, and missing fields
- [x] `build_column_lineage_dag` constructs correct DAG from DB records
- [x] `compute_downstream_impact` traverses BFS forward up to max depth 20
- [x] `compute_upstream_provenance` traverses BFS backward up to max depth 20
- [x] All 5 contract assertions execute deterministically
- [x] A3 (classification non-escalation) blocks SECRET→UNCLASSIFIED edges
- [x] 12 assessment rules run against a graph and return posture grade
- [x] OpenMetadata sync maps all 11 entity types and pushes column tags
- [x] DataHub sync populates UpstreamLineage aspects from `dd_lineage`
- [x] OpenMetadata pull client returns empty collections on connection failure
- [x] Both sync CLIs support `--dry-run`, `--gate`, and `--json` flags
- [x] Manifest entries added to `tools/manifest/design-canvases.md`
- [x] Companion sync written to all 10 AI platforms

---

## 9. Configuration Reference

| Env Var | Default | Purpose |
|---------|---------|---------|
| `ICDEV_DATA_CANVAS_ENABLED` | `true` | Feature flag for DDC canvas |
| `ICDEV_OM_URL` | `http://localhost:8585` | OpenMetadata base URL |
| `ICDEV_OM_TOKEN` | _(none)_ | OpenMetadata JWT bearer token |
| `ICDEV_OM_TIMEOUT` | `15` | OpenMetadata HTTP timeout (seconds) |
| `ICDEV_DATAHUB_URL` | `http://localhost:8080` | DataHub GMS base URL |
| `ICDEV_DATAHUB_TOKEN` | _(none)_ | DataHub auth token |
| `ICDEV_DATAHUB_ENV` | `PROD` | DataHub environment tag |
| `ICDEV_DATAHUB_TIMEOUT` | `15` | DataHub HTTP timeout (seconds) |
| `OPENMETADATA_HOST` | `http://localhost:8585` | Pull client base URL |
| `OPENMETADATA_TOKEN` | _(none)_ | Pull client auth token |
| `OPENMETADATA_TIMEOUT` | `15` | Pull client HTTP timeout (seconds) |

YAML config file: `args/data_canvas_config.yaml`

---

## 10. Related Components

| Component | Relationship |
|-----------|-------------|
| `tools/data_canvas/blueprint.py` | Hosts all REST routes; calls lineage + data_engine |
| `tools/data_canvas/constants.py` | Defines entity types, lineage types, classification levels, 12 rule IDs |
| `tools/data_canvas/db/init_db.py` | Creates `dd_lineage`, `dd_assessments`, `dd_audit`, `dd_versions` tables |
| `tools/canvas/orchestrator.py` | Cross-canvas integration; triggers DDC assessment on design save |
| `tools/dashboard/api/lineage.py` | Dashboard lineage API (digital thread + provenance + DDC) |
| `tools/data_canvas/sync/` | OpenMetadata sync, DataHub sync |
| `tools/data_canvas/clients/` | OpenMetadata read-only pull client |
