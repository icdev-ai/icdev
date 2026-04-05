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
