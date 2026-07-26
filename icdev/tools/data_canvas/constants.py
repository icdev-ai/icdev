# CUI // SP-CTI — ICDEV Data Design Canvas Constants
"""Data Design Canvas module-level constants.

Object palettes, classification levels, compliance rules, and assessment
frameworks for visual data model design with CUI/SECRET markings.
"""

# ── Data Object Types ────────────────────────────────────────────────────────
DATA_OBJECTS = {
    "entities": [
        {"type": "ent-table", "label": "Table/Entity", "icon": "TBL", "desc": "Relational database table or entity"},
        {"type": "ent-view", "label": "View", "icon": "VW", "desc": "Database view — virtual table from query"},
        {
            "type": "ent-collection",
            "label": "Collection",
            "icon": "COL",
            "desc": "NoSQL document collection (MongoDB/DynamoDB/CosmosDB)",
        },
        {
            "type": "ent-topic",
            "label": "Stream/Topic",
            "icon": "STR",
            "desc": "Kafka topic / Kinesis stream / Event Hub",
        },
        {"type": "ent-cache", "label": "Cache", "icon": "CCH", "desc": "In-memory cache (Redis/ElastiCache/Memcached)"},
        {"type": "ent-queue", "label": "Queue", "icon": "QUE", "desc": "Message queue (SQS/RabbitMQ/Service Bus)"},
        {"type": "ent-datalake", "label": "Data Lake", "icon": "DLK", "desc": "Object store data lake (S3/ADLS/GCS)"},
        {
            "type": "ent-warehouse",
            "label": "Data Warehouse",
            "icon": "DWH",
            "desc": "Analytical warehouse (Redshift/Synapse/BigQuery/Snowflake)",
        },
        {
            "type": "ent-graph",
            "label": "Graph DB",
            "icon": "GDB",
            "desc": "Graph database (Neo4j/Neptune/CosmosDB Gremlin)",
        },
        {
            "type": "ent-timeseries",
            "label": "Time Series DB",
            "icon": "TSD",
            "desc": "Time series database (TimescaleDB/InfluxDB/Timestream)",
        },
        {
            "type": "ent-vector",
            "label": "Vector DB",
            "icon": "VDB",
            "desc": "Vector/embedding store (Pinecone/Weaviate/pgvector)",
        },
        {
            "type": "ent-file",
            "label": "File Store",
            "icon": "FIL",
            "desc": "File/blob storage (S3/Azure Files/NFS/SMB share)",
        },
    ],
    "columns": [
        {"type": "col-pk", "label": "Primary Key", "icon": "PK", "desc": "Primary key column — unique row identifier"},
        {"type": "col-fk", "label": "Foreign Key", "icon": "FK", "desc": "Foreign key — reference to parent entity"},
        {
            "type": "col-data",
            "label": "Data Column",
            "icon": "DC",
            "desc": "Standard data column (varchar, int, date, etc.)",
        },
        {
            "type": "col-pii",
            "label": "PII Column",
            "icon": "PII",
            "desc": "Personally Identifiable Information (name, SSN, email, phone)",
        },
        {
            "type": "col-phi",
            "label": "PHI Column",
            "icon": "PHI",
            "desc": "Protected Health Information (HIPAA-regulated)",
        },
        {
            "type": "col-cui",
            "label": "CUI Column",
            "icon": "CUI",
            "desc": "Controlled Unclassified Information — requires CUI markings",
        },
        {
            "type": "col-secret",
            "label": "SECRET Column",
            "icon": "SEC",
            "desc": "SECRET-classified data — requires NSA Type 1 encryption",
        },
        {
            "type": "col-encrypted",
            "label": "Encrypted Column",
            "icon": "ENC",
            "desc": "Column-level encryption (TDE/AES-256/client-side)",
        },
        {
            "type": "col-audit",
            "label": "Audit Column",
            "icon": "AUD",
            "desc": "Audit trail column (created_at, updated_by, etc.)",
        },
    ],
    "flows": [
        {"type": "flow-etl", "label": "ETL Pipeline", "icon": "ETL", "desc": "Extract-Transform-Load data flow"},
        {
            "type": "flow-api",
            "label": "API Data Flow",
            "icon": "API",
            "desc": "REST/GraphQL/gRPC data flow between services",
        },
        {
            "type": "flow-replication",
            "label": "Replication",
            "icon": "REP",
            "desc": "Database replication (sync/async, primary-replica)",
        },
        {
            "type": "flow-cdc",
            "label": "CDC Stream",
            "icon": "CDC",
            "desc": "Change Data Capture (Debezium/DMS/Golden Gate)",
        },
        {
            "type": "flow-backup",
            "label": "Backup Flow",
            "icon": "BAK",
            "desc": "Backup/restore data flow (RTO/RPO tracking)",
        },
        {
            "type": "flow-export",
            "label": "Data Export",
            "icon": "EXP",
            "desc": "Bulk export / data feed (CSV, Parquet, FHIR, EDI)",
        },
        {
            "type": "flow-cross-domain",
            "label": "Cross-Domain",
            "icon": "CDS",
            "desc": "Cross-domain data transfer (CDS/guard/diode) between classification levels",
        },
        {
            "type": "flow-column-lineage",
            "label": "Column Lineage",
            "icon": "CLN",
            "desc": "Column-to-column lineage edge — tracks how a specific column flows and transforms between entities",
        },
    ],
    "controls": [
        {
            "type": "ctrl-encryption",
            "label": "Encryption",
            "icon": "ENC",
            "desc": "Data encryption at rest (TDE/KMS/HSM/LUKS)",
        },
        {
            "type": "ctrl-masking",
            "label": "Data Masking",
            "icon": "MSK",
            "desc": "Dynamic data masking / tokenization / anonymization",
        },
        {
            "type": "ctrl-dlp",
            "label": "DLP Policy",
            "icon": "DLP",
            "desc": "Data Loss Prevention policy — egress filtering",
        },
        {"type": "ctrl-rbac", "label": "RBAC", "icon": "RBA", "desc": "Role-based access control on data objects"},
        {
            "type": "ctrl-audit-log",
            "label": "Audit Logging",
            "icon": "LOG",
            "desc": "Data access audit logging (who accessed what, when)",
        },
        {
            "type": "ctrl-retention",
            "label": "Retention Policy",
            "icon": "RET",
            "desc": "Data retention and purge schedule (NARA, DoD 5015.02)",
        },
        {
            "type": "ctrl-classification",
            "label": "Classification",
            "icon": "CLS",
            "desc": "Data classification marking engine (CUI/SECRET/TS banners)",
        },
        {
            "type": "ctrl-backup-policy",
            "label": "Backup Policy",
            "icon": "BKP",
            "desc": "Backup schedule, RTO/RPO targets, geographic replication",
        },
    ],
    "boundaries": [
        {
            "type": "bnd-schema",
            "label": "Schema/Database",
            "icon": "SCH",
            "desc": "Database schema or catalog boundary",
        },
        {
            "type": "bnd-classification",
            "label": "Classification Zone",
            "icon": "CLZ",
            "desc": "Data classification boundary — all entities inside share a classification level",
        },
        {
            "type": "bnd-region",
            "label": "Data Residency",
            "icon": "REG",
            "desc": "Geographic data residency boundary (GDPR, data sovereignty)",
        },
        {
            "type": "bnd-tenant",
            "label": "Tenant Boundary",
            "icon": "TNT",
            "desc": "Multi-tenant data isolation boundary",
        },
        {
            "type": "bnd-enclave",
            "label": "Data Enclave",
            "icon": "ENV",
            "desc": "Secure data enclave — restricted access analytical environment",
        },
    ],
    "digital_twin": [
        {
            "type": "twin-lineage",
            "label": "Data Lineage Twin",
            "icon": "LT",
            "desc": "Snapshots the table schema graph and lineage edges as a baseline for schema drift detection and downstream impact analysis",
        },
        {
            "type": "twin-schema-drift",
            "label": "Schema Drift Detector",
            "icon": "SD",
            "desc": "Detects added/removed/renamed columns and type changes between lineage snapshots; scores coverage delta",
        },
        {
            "type": "twin-impact-analyzer",
            "label": "Impact Analyzer",
            "icon": "IA",
            "desc": "Traces downstream impact of a proposed schema change through the lineage graph — surfaces breaking changes before they hit production",
        },
        {
            "type": "twin-quality-gate",
            "label": "Data Quality Gate",
            "icon": "QG",
            "desc": "Evaluates null constraints, referential integrity, and CUI boundary rules against a proposed schema change",
        },
        {
            "type": "twin-catalog",
            "label": "Catalog Twin",
            "icon": "CT",
            "desc": "Integrates with external data catalogs (Collibra/Alation pattern) — bridges catalog metadata into ICDEV lineage graph",
        },
    ],
    "data_science": [
        {
            "type": "ent-feature-store",
            "label": "Feature Store",
            "icon": "FS",
            "desc": "Online/offline feature store for ML models (Feast/Tecton/SageMaker Feature Store)",
        },
        {
            "type": "ent-model-registry",
            "label": "Model Registry",
            "icon": "MR",
            "desc": "ML model artifact store with versioning, lineage, and lifecycle stages (MLflow/SageMaker/Vertex AI)",
        },
        {
            "type": "ent-dataset",
            "label": "Training Dataset",
            "icon": "DST",
            "desc": "Labeled dataset for supervised/unsupervised learning — train/val/test splits with provenance tracking",
        },
        {
            "type": "ent-experiment",
            "label": "Experiment Run",
            "icon": "EXP",
            "desc": "ML experiment with hyperparameters, metrics, and artifact tracking (MLflow/Weights & Biases)",
        },
        {
            "type": "ent-ml-pipeline",
            "label": "ML Pipeline",
            "icon": "MLP",
            "desc": "Orchestrated ML workflow (Kubeflow Pipelines/SageMaker Pipelines/Vertex AI Pipelines)",
        },
    ],
    "data_mesh": [
        {
            "type": "ent-data-product",
            "label": "Data Product",
            "icon": "DP",
            "desc": "Self-contained, self-describing data mesh product with input/output ports and SLA guarantees",
        },
        {
            "type": "ent-domain",
            "label": "Data Domain",
            "icon": "DOM",
            "desc": "Organizational data domain with ownership, stewardship, and bounded context definition",
        },
        {
            "type": "ent-contract",
            "label": "Data Contract",
            "icon": "DCT",
            "desc": "ODCS/bitol-io data contract — schema, SLA, quality rules, and ownership terms",
        },
        {
            "type": "ent-input-port",
            "label": "Input Port",
            "icon": "INP",
            "desc": "Data product input port — CDC, API, or batch ingest interface with schema enforcement",
        },
        {
            "type": "ent-output-port",
            "label": "Output Port",
            "icon": "OTP",
            "desc": "Data product output port — REST API, export file, or streaming interface with SLA tracking",
        },
    ],
}

# Classification levels for data objects
DATA_CLASSIFICATION_LEVELS = [
    {"level": "PUBLIC", "color": "#4CAF50", "marking": ""},
    {"level": "FOUO", "color": "#2196F3", "marking": "CUI // SP-FOUO"},
    {"level": "CUI", "color": "#FF9800", "marking": "CUI // SP-CTI"},
    {"level": "SECRET", "color": "#F44336", "marking": "SECRET // NOFORN"},
    {"level": "TS/SCI", "color": "#9C27B0", "marking": "TOP SECRET // SCI"},
]

# ── Data Compliance Rules (deterministic checks) ────────────────────────────
DATA_COMPLIANCE_RULES = [
    {
        "id": "DDC-ENC-001",
        "title": "CUI data encrypted at rest",
        "severity": "CAT1",
        "category": "encryption",
        "description": "All entities containing CUI columns must have encryption control connected (NIST SC-28, DFARS 252.204-7012).",
        "check": "cui_encrypted_at_rest",
    },
    {
        "id": "DDC-ENC-002",
        "title": "SECRET data uses NSA Type 1 or Suite B",
        "severity": "CAT1",
        "category": "encryption",
        "description": "Entities in SECRET classification zone must use NSA-approved encryption (CNSS Policy 15).",
        "check": "secret_encryption",
    },
    {
        "id": "DDC-ENC-003",
        "title": "Cross-domain flows use CDS/guard",
        "severity": "CAT1",
        "category": "cross_domain",
        "description": "Data flows crossing classification boundaries must traverse a cross-domain solution (NIST AC-4(21)).",
        "check": "cross_domain_guard",
    },
    {
        "id": "DDC-ACC-001",
        "title": "PII columns have masking control",
        "severity": "CAT2",
        "category": "access_control",
        "description": "Entities with PII columns should have data masking or tokenization control for non-production use.",
        "check": "pii_masking",
    },
    {
        "id": "DDC-ACC-002",
        "title": "RBAC on all data entities",
        "severity": "CAT2",
        "category": "access_control",
        "description": "All entities should have RBAC control connected — no anonymous access to data (NIST AC-3).",
        "check": "rbac_enforced",
    },
    {
        "id": "DDC-AUD-001",
        "title": "Audit logging on CUI/SECRET entities",
        "severity": "CAT1",
        "category": "audit",
        "description": "All entities containing CUI or SECRET data must have audit logging control (NIST AU-2, AU-3, AU-12).",
        "check": "audit_logging_classified",
    },
    {
        "id": "DDC-AUD-002",
        "title": "Entities have audit columns",
        "severity": "CAT2",
        "category": "audit",
        "description": "All entities should include audit trail columns (created_at, updated_by) for change tracking.",
        "check": "audit_columns_present",
    },
    {
        "id": "DDC-RET-001",
        "title": "Retention policy on all entities",
        "severity": "CAT2",
        "category": "retention",
        "description": "All entities should have a retention policy control — governs how long data is kept (NARA, DoD 5015.02).",
        "check": "retention_policy",
    },
    {
        "id": "DDC-BAK-001",
        "title": "Backup policy with RTO/RPO",
        "severity": "CAT2",
        "category": "availability",
        "description": "Production entities must have backup policy control with defined RTO/RPO targets (NIST CP-9, CP-10).",
        "check": "backup_policy",
    },
    {
        "id": "DDC-CLS-001",
        "title": "All entities classified",
        "severity": "CAT1",
        "category": "classification",
        "description": "Every entity must reside within a classification zone boundary — unclassified entities are a marking violation.",
        "check": "entity_classified",
    },
    {
        "id": "DDC-SOV-001",
        "title": "Data residency for regulated data",
        "severity": "CAT2",
        "category": "sovereignty",
        "description": "Entities with PII/PHI/CUI should reside within a data residency boundary to enforce geographic constraints.",
        "check": "data_residency",
    },
    {
        "id": "DDC-DLP-001",
        "title": "DLP on egress flows",
        "severity": "CAT2",
        "category": "data_protection",
        "description": "Data export and API flows leaving a classification zone should have DLP policy control.",
        "check": "dlp_on_egress",
    },
]

# ── Edge Type Constants ───────────────────────────────────────────────────────
# Column-to-column lineage edge type (used in lineage.py and data_engine.py)
EDGE_TYPE_COLUMN_LINEAGE = "flow-column-lineage"

# All recognized column-level lineage transformation types stored in dd_lineage.lineage_type
COLUMN_LINEAGE_TYPES = [
    "flow-column-lineage",  # Generic column-to-column flow (default)
    "col-derive",           # Computed / derived expression (e.g. full_name = first || ' ' || last)
    "col-join",             # Column participates in a JOIN predicate
    "col-filter",           # Column used in WHERE / HAVING clause
    "col-aggregate",        # Column produced by GROUP BY / SUM / COUNT / AVG
    "col-union",            # Column merged via UNION / INTERSECT / EXCEPT
    "col-cast",             # Type-cast or format conversion (e.g. VARCHAR → DATE)
    "col-passthrough",      # Column copied unchanged (SELECT col FROM ...)
]

# ── Data Science Constants ────────────────────────────────────────────────────

DS_CHECK_TYPES = ["completeness", "uniqueness", "range", "pattern", "freshness"]
DS_DB_TYPES = ["sqlite", "postgresql", "duckdb"]
DS_PROFILER_MAX_ROWS = 50_000
DS_QUERY_MAX_ROWS = 1_000

# NIST 800-53 control families relevant to data design
DATA_NIST_FAMILIES = {
    "AC": "Access Control",
    "AU": "Audit and Accountability",
    "CP": "Contingency Planning",
    "IA": "Identification and Authentication",
    "MP": "Media Protection",
    "SC": "System and Communications Protection",
    "SI": "System and Information Integrity",
    "SR": "Supply Chain Risk Management",
    "PT": "PII Processing and Transparency",
}

# ── Data Mesh Constants ───────────────────────────────────────────────────────

DM_DOMAIN_MATURITY_LEVELS = [
    {"level": 0, "label": "Initial",    "description": "Ad-hoc data practices, no formal ownership or governance"},
    {"level": 1, "label": "Defined",    "description": "Domains identified, ownership assigned, basic data products exist"},
    {"level": 2, "label": "Managed",    "description": "Contracts published, SLAs tracked, quality gates active"},
    {"level": 3, "label": "Optimized",  "description": "Federated governance active, cross-domain lineage, schema drift alerts"},
    {"level": 4, "label": "Autonomous", "description": "Self-service, automated quality gates, AI-assisted governance"},
]

DM_PORT_TYPES = ["cdc", "api", "batch", "stream", "file"]
DM_CONTRACT_STATUSES = ["draft", "active", "deprecated", "archived"]
DM_GOVERNANCE_POLICY_TYPES = ["opa", "rbac", "classification", "retention", "dlp"]

# Data Mesh v2 constants (dm-found-02 — used by data_mesh/* modules)
DM_MATURITY_LEVELS = ["defined", "managed", "optimizing"]       # simple validation list
# label→int mapping for the INTEGER dm_domains.maturity_level column (dcpr-fix-08).
# Aligns the DM_MATURITY_LEVELS labels with the DM_DOMAIN_MATURITY_LEVELS numeric
# levels so create_domain persists an int, not a string, into the INTEGER column.
DM_MATURITY_LEVEL_MAP = {"defined": 1, "managed": 2, "optimizing": 3}
DM_DEFAULT_MATURITY_LEVEL = 1                                    # "defined"
DM_PRODUCT_STATUS = ["draft", "published", "deprecated"]
DM_OUTPUT_PORT_TYPES = ["table", "api", "stream", "file", "lakehouse"]
DM_SLA_TIERS = ["bronze", "silver", "gold", "platinum"]
DM_CONTRACT_STATUS = ["draft", "active", "violated", "deprecated"]
DM_CSP_PROVIDERS = ["aws_datazone", "azure_purview", "gcp_dataplex"]
DM_GOVERNANCE_SCORE_GATE: float = 0.6  # fraction of domains needing active policy

# ── AI Data Mapping Constants ─────────────────────────────────────────────────

MAPPING_SOURCE_FORMATS = ["json_schema", "csv_headers", "sql_ddl", "openapi3"]
MAPPING_TARGET_FORMATS = ["json_schema", "csv_headers", "sql_ddl", "dbt_model"]

MAPPING_SESSION_STATUSES = ["pending", "ingested", "suggested", "complete", "error"]
MAPPING_FIELD_STATUSES   = ["pending", "confirmed", "rejected", "needs_review"]
MAPPING_MATCH_METHODS    = ["name", "semantic", "type", "combined", "manual"]
MAPPING_ARTIFACT_TYPES   = ["sql", "python", "dbt", "xslt"]

MAPPING_CONF_AUTO_CONFIRM: float = 0.95   # auto-confirm at or above this score
MAPPING_CONF_SUGGEST:      float = 0.50   # show to user; below → needs_review

_D = "https://icdev.dev/ontology/data#"

DATA_ONTOLOGY_MAP: dict[str, str] = {
    # Data entities
    "ent-table":            f"{_D}DataEntity.Table",
    "ent-view":             f"{_D}DataEntity.View",
    "ent-collection":       f"{_D}DataEntity.Collection",
    "ent-topic":            f"{_D}DataEntity.Topic",
    "ent-cache":            f"{_D}DataEntity.Cache",
    "ent-queue":            f"{_D}DataEntity.Queue",
    "ent-datalake":         f"{_D}DataEntity.DataLake",
    "ent-warehouse":        f"{_D}DataEntity.Warehouse",
    "ent-graph":            f"{_D}DataEntity.Graph",
    "ent-timeseries":       f"{_D}DataEntity.TimeSeries",
    "ent-vector":           f"{_D}DataEntity.VectorStore",
    "ent-file":             f"{_D}DataEntity.File",
    "ent-feature-store":    f"{_D}DataEntity.FeatureStore",
    "ent-model-registry":   f"{_D}DataEntity.ModelRegistry",
    "ent-dataset":          f"{_D}DataEntity.Dataset",
    "ent-experiment":       f"{_D}DataEntity.Experiment",
    "ent-ml-pipeline":      f"{_D}DataEntity.MLPipeline",
    "ent-data-product":     f"{_D}DataEntity.DataProduct",
    "ent-domain":           f"{_D}DataEntity.Domain",
    "ent-contract":         f"{_D}DataEntity.Contract",
    "ent-input-port":       f"{_D}DataEntity.InputPort",
    "ent-output-port":      f"{_D}DataEntity.OutputPort",
    # Column classifications
    "col-pk":               f"{_D}Column.PrimaryKey",
    "col-fk":               f"{_D}Column.ForeignKey",
    "col-data":             f"{_D}Column.Data",
    "col-pii":              f"{_D}Column.PII",
    "col-phi":              f"{_D}Column.PHI",
    "col-cui":              f"{_D}Column.CUI",
    "col-secret":           f"{_D}Column.Secret",
    "col-encrypted":        f"{_D}Column.Encrypted",
    "col-audit":            f"{_D}Column.Audit",
    # Data flows
    "flow-etl":             f"{_D}DataFlow.ETL",
    "flow-api":             f"{_D}DataFlow.API",
    "flow-replication":     f"{_D}DataFlow.Replication",
    "flow-cdc":             f"{_D}DataFlow.CDC",
    "flow-backup":          f"{_D}DataFlow.Backup",
    "flow-export":          f"{_D}DataFlow.Export",
    "flow-cross-domain":    f"{_D}DataFlow.CrossDomain",
    "flow-column-lineage":  f"{_D}DataFlow.ColumnLineage",
    # Data controls
    "ctrl-encryption":      f"{_D}DataControl.Encryption",
    "ctrl-masking":         f"{_D}DataControl.Masking",
    "ctrl-dlp":             f"{_D}DataControl.DLP",
    "ctrl-rbac":            f"{_D}DataControl.RBAC",
    "ctrl-audit-log":       f"{_D}DataControl.AuditLog",
    "ctrl-retention":       f"{_D}DataControl.Retention",
    "ctrl-classification":  f"{_D}DataControl.Classification",
    "ctrl-backup-policy":   f"{_D}DataControl.BackupPolicy",
    # Data boundaries
    "bnd-schema":           f"{_D}DataBoundary.Schema",
    "bnd-classification":   f"{_D}DataBoundary.Classification",
    "bnd-region":           f"{_D}DataBoundary.Region",
    "bnd-tenant":           f"{_D}DataBoundary.Tenant",
    "bnd-enclave":          f"{_D}DataBoundary.Enclave",
    # Digital twins
    "twin-lineage":         f"{_D}DigitalTwin.Lineage",
    "twin-schema-drift":    f"{_D}DigitalTwin.SchemaDrift",
    "twin-impact-analyzer": f"{_D}DigitalTwin.ImpactAnalyzer",
    "twin-quality-gate":    f"{_D}DigitalTwin.QualityGate",
    "twin-catalog":         f"{_D}DigitalTwin.Catalog",
}
