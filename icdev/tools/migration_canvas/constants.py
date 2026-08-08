# CUI // SP-CTI — ICDEV Migration Design Canvas Constants
"""Migration Design Canvas module-level constants.

Object palettes, compliance rules, lifecycle states, and assessment frameworks
for visual migration planning across cloud, application, network, and
data center migration scenarios.
"""

MIGRATION_OBJECTS = {
    "sources": [
        {
            "type": "src-legacy",
            "label": "Legacy Application",
            "icon": "LEG",
            "desc": "Legacy/heritage application to be assessed and migrated",
        },
        {
            "type": "src-monolith",
            "label": "Monolith",
            "icon": "MON",
            "desc": "Monolithic application — candidate for decomposition or rehost",
        },
        {
            "type": "src-database",
            "label": "Legacy Database",
            "icon": "LDB",
            "desc": "On-premises database (Oracle, SQL Server, DB2, etc.)",
        },
        {
            "type": "src-datacenter",
            "label": "Data Center",
            "icon": "DC ",
            "desc": "Physical data center facility — DCOI consolidation source",
        },
        {
            "type": "src-mainframe",
            "label": "Mainframe",
            "icon": "MFR",
            "desc": "Mainframe system (z/OS, AS/400) — high migration complexity",
        },
        {
            "type": "src-network",
            "label": "Legacy Network",
            "icon": "LNT",
            "desc": "On-premises network infrastructure (switches, routers, firewalls)",
        },
        {
            "type": "src-storage",
            "label": "On-Prem Storage",
            "icon": "STR",
            "desc": "On-premises storage (SAN, NAS, tape) — data migration source",
        },
        {
            "type": "src-middleware",
            "label": "Middleware",
            "icon": "MWR",
            "desc": "Legacy middleware (MQ, ESB, CORBA) — integration layer",
        },
    ],
    "targets": [
        {
            "type": "tgt-govcloud",
            "label": "GovCloud",
            "icon": "GOV",
            "desc": "AWS GovCloud / Azure Government — IL4/IL5 cloud target",
        },
        {
            "type": "tgt-cloud",
            "label": "Commercial Cloud",
            "icon": "CLD",
            "desc": "Commercial cloud (AWS, Azure, GCP) — IL2 workloads",
        },
        {
            "type": "tgt-vm",
            "label": "Cloud VM",
            "icon": "VM ",
            "desc": "Cloud virtual machine (EC2, Azure VM, GCE) — rehost target",
        },
        {
            "type": "tgt-container",
            "label": "Container",
            "icon": "CTR",
            "desc": "Container runtime (ECS, EKS, AKS) — replatform target",
        },
        {
            "type": "tgt-serverless",
            "label": "Serverless",
            "icon": "SLS",
            "desc": "Serverless functions (Lambda, Azure Functions) — rearchitect target",
        },
        {
            "type": "tgt-microservice",
            "label": "Microservice",
            "icon": "μSV",
            "desc": "Extracted microservice — output of monolith decomposition",
        },
        {
            "type": "tgt-managed-db",
            "label": "Managed Database",
            "icon": "MDB",
            "desc": "Cloud-managed database (RDS, Aurora, CosmosDB, Cloud SQL)",
        },
        {
            "type": "tgt-saas",
            "label": "SaaS Replacement",
            "icon": "SAS",
            "desc": "SaaS product replacing custom build — repurchase target",
        },
    ],
    "patterns": [
        {
            "type": "pat-rehost",
            "label": "Rehost",
            "icon": "R1 ",
            "desc": "Lift-and-shift to cloud VMs with minimal changes",
        },
        {
            "type": "pat-replatform",
            "label": "Replatform",
            "icon": "R2 ",
            "desc": "Migrate with minor optimizations (containers, managed DB)",
        },
        {
            "type": "pat-refactor",
            "label": "Refactor",
            "icon": "R3 ",
            "desc": "Re-architect for cloud-native with significant code changes",
        },
        {
            "type": "pat-rearchitect",
            "label": "Rearchitect",
            "icon": "R4 ",
            "desc": "Redesign application architecture (monolith → microservices)",
        },
        {
            "type": "pat-repurchase",
            "label": "Repurchase",
            "icon": "R5 ",
            "desc": "Replace with SaaS/COTS product — drop and shop",
        },
        {
            "type": "pat-retire",
            "label": "Retire",
            "icon": "R6 ",
            "desc": "Decommission application — no longer needed",
        },
        {
            "type": "pat-retain",
            "label": "Retain",
            "icon": "R7 ",
            "desc": "Keep as-is — not a migration candidate at this time",
        },
        {
            "type": "pat-strangler",
            "label": "Strangler Fig",
            "icon": "STF",
            "desc": "Incremental replacement behind API proxy — safe, reversible",
        },
    ],
    "middleware": [
        {
            "type": "mid-proxy",
            "label": "API Gateway",
            "icon": "GW ",
            "desc": "API gateway or reverse proxy for traffic routing during migration",
        },
        {
            "type": "mid-acl",
            "label": "Anti-Corruption Layer",
            "icon": "ACL",
            "desc": "Translation layer between legacy and modern interfaces",
        },
        {
            "type": "mid-etl",
            "label": "ETL Pipeline",
            "icon": "ETL",
            "desc": "Extract-Transform-Load pipeline for data migration",
        },
        {
            "type": "mid-queue",
            "label": "Message Queue",
            "icon": "MQ ",
            "desc": "Async message queue for decoupled migration (SQS, Kafka, MQ)",
        },
        {
            "type": "mid-sync",
            "label": "Data Sync",
            "icon": "SYN",
            "desc": "Real-time data synchronization between source and target",
        },
    ],
    "controls": [
        {
            "type": "ctl-ato-gate",
            "label": "ATO Gate",
            "icon": "ATO",
            "desc": "Authorization to Operate compliance gate — blocks until ATO cleared",
        },
        {
            "type": "ctl-compliance-bridge",
            "label": "Compliance Bridge",
            "icon": "CBR",
            "desc": "NIST 800-53 control inheritance bridge between legacy and target",
        },
        {
            "type": "ctl-compliance-gate",
            "label": "Compliance Gate",
            "icon": "CGT",
            "desc": "Generic compliance checkpoint (FedRAMP, CMMC, DCOI, FISMA)",
        },
        {
            "type": "ctl-security-scan",
            "label": "Security Scan",
            "icon": "SCN",
            "desc": "Pre-migration security scan (SAST, DAST, dependency audit)",
        },
        {
            "type": "ctl-test-gate",
            "label": "Test Gate",
            "icon": "TST",
            "desc": "Migration validation testing gate — blocks until tests pass",
        },
        {
            "type": "ctl-rollback",
            "label": "Rollback Point",
            "icon": "RBK",
            "desc": "Defined rollback checkpoint — enables safe migration reversal",
        },
    ],
    "planning": [
        {
            "type": "wave-group",
            "label": "Migration Wave",
            "icon": "WAV",
            "desc": "Wave container — groups migration targets by phase/priority",
        },
        {
            "type": "plan-milestone",
            "label": "Milestone",
            "icon": "MST",
            "desc": "Key milestone or decision point in migration timeline",
        },
        {
            "type": "plan-dependency",
            "label": "Dependency",
            "icon": "DEP",
            "desc": "External dependency that must be resolved before migration",
        },
    ],
}


# ── Compliance Rules ────────────────────────────────────────────────────────

MC_COMPLIANCE_RULES = [
    {
        "id": "MC-001",
        "title": "Every source must have a target or be explicitly retired",
        "severity": "CAT1",
        "category": "completeness",
        "description": "All source nodes must connect to a migration pattern or be marked as Retire/Retain.",
    },
    {
        "id": "MC-002",
        "title": "Migration pattern must connect to at least one target",
        "severity": "CAT1",
        "category": "completeness",
        "description": "Each migration pattern node must have an outgoing edge to a target node.",
    },
    {
        "id": "MC-003",
        "title": "ATO gate required for IL4+ migrations",
        "severity": "CAT1",
        "category": "compliance",
        "description": "Migrations targeting GovCloud or IL4/IL5 must include an ATO compliance gate.",
    },
    {
        "id": "MC-004",
        "title": "Compliance bridge required for multi-system migrations",
        "severity": "CAT2",
        "category": "compliance",
        "description": "Migrations with 3+ source systems should include a compliance bridge for control inheritance.",
    },
    {
        "id": "MC-005",
        "title": "Rollback point required for big-bang migrations",
        "severity": "CAT2",
        "category": "risk",
        "description": "Non-incremental migration patterns should include an explicit rollback checkpoint.",
    },
    {
        "id": "MC-006",
        "title": "Data migration path required when database sources exist",
        "severity": "CAT2",
        "category": "completeness",
        "description": "If source databases exist, an ETL pipeline or data sync node must be present.",
    },
    {
        "id": "MC-007",
        "title": "Security scan recommended before migration",
        "severity": "CAT3",
        "category": "security",
        "description": "A security scan control should be placed before migration pattern nodes.",
    },
    {
        "id": "MC-008",
        "title": "Test gate recommended after migration",
        "severity": "CAT3",
        "category": "quality",
        "description": "A test gate should validate post-migration functionality and performance.",
    },
    {
        "id": "MC-009",
        "title": "Wave grouping recommended for 5+ source systems",
        "severity": "CAT3",
        "category": "planning",
        "description": "Large migrations should use wave groups to phase the migration.",
    },
    {
        "id": "MC-010",
        "title": "Anti-corruption layer recommended for strangler fig",
        "severity": "CAT3",
        "category": "architecture",
        "description": "Strangler fig migrations should include an ACL between legacy and modern systems.",
    },
]


# ── Lifecycle States ────────────────────────────────────────────────────────

MIGRATION_LIFECYCLE_STATES = [
    "discovery",
    "assessment",
    "planning",
    "design",
    "execution",
    "cutover",
    "validation",
    "post_migration",
    "complete",
]

# Network migration session lifecycle — the vocabulary of mc_net_sessions.status.
# Single source of truth for the PATCH validator (blueprint.py) and the NMCE
# genesis reflex, which flags any session left in a non-terminal state.
NET_SESSION_STATUSES = (
    "in_progress",
    "draft",
    "blocked",
    "complete",
    "archived",
    "cancelled",
)

# Closed states — a session in one of these no longer counts as active work.
# 'complete'/'archived' are what blueprint.py and network_migration.py already
# query for; 'cancelled' closes a session that was abandoned rather than done.
NET_SESSION_TERMINAL_STATUSES = ("complete", "archived", "cancelled")

MIGRATION_TYPES = [
    "application",
    "cloud",
    "network",
    "infrastructure",
    "data_center",
    "database",
]

SOP_TYPES = [
    "migration_readiness",
    "source_discovery",
    "target_provisioning",
    "cutover_planning",
    "post_migration_validation",
    "rollback_procedure",
    "custom",
]

CLASSIFICATION_LEVELS = ["PUBLIC", "CUI", "SECRET", "TOP SECRET"]


# ── Server Migration Constants ──────────────────────────────────────────────

SERVER_MIGRATION_TYPES = [
    {"value": "p2p",              "label": "P2P — Physical to Physical",       "desc": "Bare-metal server refresh or datacenter consolidation"},
    {"value": "p2v_onprem",       "label": "P2V — Physical to On-Prem VM",     "desc": "Physical server to VMware, Hyper-V, KVM, Nutanix, or Proxmox"},
    {"value": "p2v_cloud",        "label": "P2V — Physical to Cloud",          "desc": "Physical server to cloud VM (AWS, Azure, GCP, OCI, IBM)"},
    {"value": "v2v_cloud",        "label": "V2V — VM to Cloud",                "desc": "On-prem VM to cloud (lift-and-shift via OVA/replication)"},
    {"value": "v2v_hypervisor",   "label": "V2V — Hypervisor to Hypervisor",   "desc": "Cross-hypervisor migration (e.g. VMware → KVM, Hyper-V → vSphere)"},
    {"value": "v2v_cloud2cloud",  "label": "V2V — Cloud to Cloud",             "desc": "Cross-cloud migration (e.g. AWS → Azure, GCP → OCI)"},
]

SERVER_PLATFORMS = [
    # Cloud
    {"value": "aws",      "label": "AWS EC2",            "category": "cloud",   "govcloud": True},
    {"value": "azure",    "label": "Azure VM",           "category": "cloud",   "govcloud": True},
    {"value": "gcp",      "label": "GCP Compute Engine", "category": "cloud",   "govcloud": False},
    {"value": "oci",      "label": "OCI Compute",        "category": "cloud",   "govcloud": True},
    {"value": "ibm",      "label": "IBM Cloud VSI",      "category": "cloud",   "govcloud": True},
    # On-prem hypervisors
    {"value": "vmware",   "label": "VMware vSphere",     "category": "onprem",  "govcloud": False},
    {"value": "hyperv",   "label": "Microsoft Hyper-V",  "category": "onprem",  "govcloud": False},
    {"value": "kvm",      "label": "KVM / QEMU",         "category": "onprem",  "govcloud": False},
    {"value": "nutanix",  "label": "Nutanix AHV",        "category": "onprem",  "govcloud": False},
    {"value": "proxmox",  "label": "Proxmox VE",         "category": "onprem",  "govcloud": False},
    # Physical
    {"value": "physical", "label": "Physical Server",    "category": "physical","govcloud": False},
]

SERVER_COMPAT_CATEGORIES = [
    {"value": "compute",   "label": "Compute",    "icon": "⚙"},
    {"value": "storage",   "label": "Storage",    "icon": "💾"},
    {"value": "network",   "label": "Network",    "icon": "🔌"},
    {"value": "os",        "label": "OS / Kernel","icon": "🖥"},
    {"value": "licensing", "label": "Licensing",  "icon": "📋"},
    {"value": "security",  "label": "Security",   "icon": "🛡"},
]

CUTOVER_PHASES = [
    {"value": "pre_migration",  "label": "Pre-Migration",  "badge_class": "badge-info"},
    {"value": "parallel_run",   "label": "Parallel Run",   "badge_class": "badge-primary"},
    {"value": "cutover",        "label": "Cutover",        "badge_class": "badge-warning"},
    {"value": "post_migration", "label": "Post-Migration", "badge_class": "badge-success"},
    {"value": "rollback",       "label": "Rollback",       "badge_class": "badge-danger"},
]

MIGRATION_TOOLS = {
    "p2p":             ["Manual / Clonezilla", "Custom Script", "Acronis Migrate"],
    "p2v_onprem":      ["VMware vCenter Converter", "StarWind V2V", "Virt-P2V / virt-v2v", "Disk2VHD (Hyper-V)"],
    "p2v_cloud":       ["AWS Application Migration Service (MGN)", "Azure Migrate", "GCP Migrate for Compute", "OCI Cloud Migrations", "IBM Cloud Migration"],
    "v2v_cloud":       ["AWS MGN", "Azure Site Recovery", "GCP Migrate", "CloudEndure", "OVA Export + Import"],
    "v2v_hypervisor":  ["VMware vMotion", "StarWind V2V", "qemu-img convert", "Microsoft SCVMM"],
    "v2v_cloud2cloud": ["AWS MGN", "Azure Site Recovery", "Zerto", "Velero (K8s)"],
}


# ── Compliance Frameworks ───────────────────────────────────────────────────

COMPLIANCE_FRAMEWORKS = {
    'fedramp': {'label': 'FedRAMP', 'applies_to_envs': ['AWS GovCloud', 'Azure Government', 'GCP Gov'], 'url': 'https://fedramp.gov'},
    'disa_stig': {'label': 'DISA STIG', 'applies_to_ils': ['IL4', 'IL5', 'IL6']},
    'nist_800_53': {'label': 'NIST 800-53', 'applies_to_ils': ['IL2', 'IL4', 'IL5', 'IL6']},
    'cmmc2': {'label': 'CMMC 2.0', 'applies_to_envs': ['On-Prem', 'Hybrid']},
}

IL_LEVEL_REQUIREMENTS = {
    'IL2': {'max_classification': 'Unclassified', 'commercial_ok': True},
    'IL4': {'max_classification': 'CUI', 'commercial_ok': False, 'requires_govcloud': True},
    'IL5': {'max_classification': 'CUI-High', 'commercial_ok': False, 'requires_govcloud': True, 'requires_disa_stig': True},
    'IL6': {'max_classification': 'SECRET', 'commercial_ok': False, 'requires_sipr': True},
}


# ── Wave & Dependency Constants ─────────────────────────────────────────────

WAVE_STATUS = {
    'planned':     {'label': 'Planned',     'color': '#6c757d'},
    'in_progress': {'label': 'In Progress', 'color': '#fd7e14'},
    'complete':    {'label': 'Complete',    'color': '#28a745'},
    'blocked':     {'label': 'Blocked',     'color': '#dc3545'},
}

DEPENDENCY_TYPES = {
    'network':     {'label': 'Network',       'color': '#17a2b8', 'icon': '🌐'},
    'application': {'label': 'Application',   'color': '#6f42c1', 'icon': '📦'},
    'database':    {'label': 'Database',      'color': '#e83e8c', 'icon': '🗄'},
    'auth':        {'label': 'Auth/Identity', 'color': '#fd7e14', 'icon': '🔑'},
    'storage':     {'label': 'Storage',       'color': '#20c997', 'icon': '💾'},
}

# ── Ontology Mapping (migration -> ICDEV migration ontology) ────────────────
MIGRATION_ONTOLOGY_MAP: dict[str, str] = {
    # Sources
    "src-legacy":       "https://icdev.dev/ontology/migration#Source.LegacyApplication",
    "src-monolith":     "https://icdev.dev/ontology/migration#Source.Monolith",
    "src-database":     "https://icdev.dev/ontology/migration#Source.LegacyDatabase",
    "src-datacenter":   "https://icdev.dev/ontology/migration#Source.DataCenter",
    "src-mainframe":    "https://icdev.dev/ontology/migration#Source.Mainframe",
    "src-network":      "https://icdev.dev/ontology/migration#Source.LegacyNetwork",
    "src-storage":      "https://icdev.dev/ontology/migration#Source.OnPremStorage",
    "src-middleware":   "https://icdev.dev/ontology/migration#Source.Middleware",
    # Targets
    "tgt-govcloud":     "https://icdev.dev/ontology/migration#Target.GovCloud",
    "tgt-cloud":        "https://icdev.dev/ontology/migration#Target.CommercialCloud",
    "tgt-vm":           "https://icdev.dev/ontology/migration#Target.CloudVM",
    "tgt-container":    "https://icdev.dev/ontology/migration#Target.Container",
    "tgt-serverless":   "https://icdev.dev/ontology/migration#Target.Serverless",
    "tgt-microservice": "https://icdev.dev/ontology/migration#Target.Microservice",
    "tgt-managed-db":   "https://icdev.dev/ontology/migration#Target.ManagedDatabase",
    "tgt-saas":         "https://icdev.dev/ontology/migration#Target.SaaS",
    # Patterns
    "pat-rehost":       "https://icdev.dev/ontology/migration#Pattern.Rehost",
    "pat-replatform":   "https://icdev.dev/ontology/migration#Pattern.Replatform",
    "pat-refactor":     "https://icdev.dev/ontology/migration#Pattern.Refactor",
    "pat-rearchitect":  "https://icdev.dev/ontology/migration#Pattern.Rearchitect",
    "pat-repurchase":   "https://icdev.dev/ontology/migration#Pattern.Repurchase",
    "pat-retire":       "https://icdev.dev/ontology/migration#Pattern.Retire",
    "pat-retain":       "https://icdev.dev/ontology/migration#Pattern.Retain",
    "pat-strangler":    "https://icdev.dev/ontology/migration#Pattern.StranglerFig",
    # Middleware
    "mid-proxy":        "https://icdev.dev/ontology/migration#Middleware.APIGateway",
    "mid-acl":          "https://icdev.dev/ontology/migration#Middleware.AntiCorruptionLayer",
    "mid-etl":          "https://icdev.dev/ontology/migration#Middleware.ETLPipeline",
    "mid-queue":        "https://icdev.dev/ontology/migration#Middleware.MessageQueue",
    "mid-sync":         "https://icdev.dev/ontology/migration#Middleware.DataSync",
    # Controls
    "ctl-ato-gate":     "https://icdev.dev/ontology/migration#Control.ATOGate",
    "ctl-compliance-bridge": "https://icdev.dev/ontology/migration#Control.ComplianceBridge",
    "ctl-compliance-gate":   "https://icdev.dev/ontology/migration#Control.ComplianceGate",
    "ctl-security-scan":     "https://icdev.dev/ontology/migration#Control.SecurityScan",
    "ctl-test-gate":    "https://icdev.dev/ontology/migration#Control.TestGate",
    "ctl-rollback":     "https://icdev.dev/ontology/migration#Control.RollbackPoint",
    # Planning
    "wave-group":       "https://icdev.dev/ontology/migration#Planning.MigrationWave",
    "plan-milestone":   "https://icdev.dev/ontology/migration#Planning.Milestone",
    "plan-dependency":  "https://icdev.dev/ontology/migration#Planning.Dependency",
}
