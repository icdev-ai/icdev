# CUI // SP-CTI — ICDEV Infrastructure Design Canvas Constants
# Classification: CUI — Controlled Unclassified Information
"""
Infrastructure Design Canvas (IDC) module-level constants.

Complete compute, storage, serverless, database, and container service
catalogs for all 6 supported CSPs (AWS, Azure, GCP, OCI, IBM, On-Prem).
NDC handles the network plumbing; IDC handles what runs on it.
"""

# ── Compute Services ─────────────────────────────────────────────────────────
INFRA_OBJECTS = {
    "compute": {
        "aws": [
            {
                "type": "aws-ec2",
                "label": "EC2",
                "icon": "EC2",
                "desc": "AWS EC2 instance — general purpose, compute, memory, GPU, HPC",
            },
            {
                "type": "aws-ec2-asg",
                "label": "Auto Scaling",
                "icon": "ASG",
                "desc": "EC2 Auto Scaling Group — horizontal scale-out/in",
            },
            {
                "type": "aws-ec2-spot",
                "label": "Spot Instance",
                "icon": "SPT",
                "desc": "EC2 Spot Instance — up to 90% cost savings, interruptible",
            },
            {
                "type": "aws-ec2-dedicated",
                "label": "Dedicated Host",
                "icon": "DDH",
                "desc": "EC2 Dedicated Host — single-tenant physical server (ITAR/compliance)",
            },
            {
                "type": "aws-lightsail",
                "label": "Lightsail",
                "icon": "LS",
                "desc": "AWS Lightsail — simplified VPS for small workloads",
            },
            {"type": "aws-batch", "label": "Batch", "icon": "BAT", "desc": "AWS Batch — managed batch computing"},
            {
                "type": "aws-parallel",
                "label": "ParallelCluster",
                "icon": "HPC",
                "desc": "AWS ParallelCluster — HPC cluster orchestration",
            },
        ],
        "azure": [
            {
                "type": "az-vm",
                "label": "Virtual Machine",
                "icon": "VM",
                "desc": "Azure VM — general purpose, compute, memory, GPU, HPC",
            },
            {
                "type": "az-vmss",
                "label": "VM Scale Set",
                "icon": "VMSS",
                "desc": "Azure VM Scale Sets — auto-scaling VM fleet",
            },
            {
                "type": "az-spot",
                "label": "Spot VM",
                "icon": "SPT",
                "desc": "Azure Spot VM — evictable, low-cost compute",
            },
            {
                "type": "az-dedicated",
                "label": "Dedicated Host",
                "icon": "DDH",
                "desc": "Azure Dedicated Host — single-tenant physical server",
            },
            {"type": "az-batch", "label": "Batch", "icon": "BAT", "desc": "Azure Batch — managed batch processing"},
            {
                "type": "az-hpc",
                "label": "HPC/CycleCloud",
                "icon": "HPC",
                "desc": "Azure CycleCloud — HPC cluster management",
            },
        ],
        "gcp": [
            {
                "type": "gcp-gce",
                "label": "Compute Engine",
                "icon": "GCE",
                "desc": "GCP Compute Engine VM — general, compute, memory, GPU, TPU",
            },
            {
                "type": "gcp-mig",
                "label": "Managed Instance Group",
                "icon": "MIG",
                "desc": "GCP Managed Instance Group — auto-scaling VM fleet",
            },
            {
                "type": "gcp-spot",
                "label": "Spot VM",
                "icon": "SPT",
                "desc": "GCP Spot VM — preemptible, low-cost compute",
            },
            {
                "type": "gcp-sole",
                "label": "Sole-Tenant Node",
                "icon": "STN",
                "desc": "GCP Sole-Tenant Node — dedicated physical server (compliance)",
            },
            {"type": "gcp-batch", "label": "Batch", "icon": "BAT", "desc": "GCP Batch — managed batch job service"},
            {
                "type": "gcp-tpu",
                "label": "Cloud TPU",
                "icon": "TPU",
                "desc": "GCP Cloud TPU — tensor processing unit for ML training",
            },
        ],
        "oci": [
            {
                "type": "oci-compute",
                "label": "Compute",
                "icon": "OCI",
                "desc": "OCI Compute Instance — flexible shapes, AMD/Intel/Arm/GPU",
            },
            {
                "type": "oci-instance-pool",
                "label": "Instance Pool",
                "icon": "IPL",
                "desc": "OCI Instance Pool — auto-scaling compute fleet",
            },
            {
                "type": "oci-preemptible",
                "label": "Preemptible",
                "icon": "PRE",
                "desc": "OCI Preemptible Instance — reclaimable, low-cost",
            },
            {
                "type": "oci-dedicated-host",
                "label": "Dedicated VM Host",
                "icon": "DDH",
                "desc": "OCI Dedicated VM Host — single-tenant bare metal",
            },
            {
                "type": "oci-bm",
                "label": "Bare Metal",
                "icon": "BM",
                "desc": "OCI Bare Metal — full physical server, no hypervisor",
            },
            {"type": "oci-hpc", "label": "HPC", "icon": "HPC", "desc": "OCI HPC cluster with RDMA networking"},
        ],
        "ibm": [
            {
                "type": "ibm-vsi",
                "label": "Virtual Server",
                "icon": "VSI",
                "desc": "IBM Cloud Virtual Server (VPC or Classic)",
            },
            {"type": "ibm-bm", "label": "Bare Metal", "icon": "BM", "desc": "IBM Cloud Bare Metal Server"},
            {
                "type": "ibm-power",
                "label": "Power VS",
                "icon": "PWR",
                "desc": "IBM Power Virtual Server — AIX/IBMi workloads",
            },
            {"type": "ibm-hpc", "label": "HPC", "icon": "HPC", "desc": "IBM Spectrum LSF HPC cluster"},
        ],
        "onprem": [
            {
                "type": "op-server",
                "label": "Physical Server",
                "icon": "SVR",
                "desc": "On-premises physical server (rack/blade/tower)",
            },
            {
                "type": "op-hypervisor",
                "label": "Hypervisor",
                "icon": "HYP",
                "desc": "VMware vSphere / Hyper-V / KVM / Proxmox hypervisor",
            },
            {"type": "op-vm", "label": "On-Prem VM", "icon": "VM", "desc": "Virtual machine on on-premises hypervisor"},
            {
                "type": "op-hci",
                "label": "HCI Cluster",
                "icon": "HCI",
                "desc": "Hyper-converged infrastructure (Nutanix/vSAN/Azure Stack HCI)",
            },
        ],
    },
    # ── Container & Orchestration ────────────────────────────────────────────
    "containers": {
        "aws": [
            {"type": "aws-eks", "label": "EKS", "icon": "EKS", "desc": "AWS Elastic Kubernetes Service — managed K8s"},
            {
                "type": "aws-ecs",
                "label": "ECS",
                "icon": "ECS",
                "desc": "AWS Elastic Container Service — managed Docker",
            },
            {
                "type": "aws-fargate",
                "label": "Fargate",
                "icon": "FGT",
                "desc": "AWS Fargate — serverless container execution",
            },
            {
                "type": "aws-ecr",
                "label": "ECR",
                "icon": "ECR",
                "desc": "AWS Elastic Container Registry — private container images",
            },
            {
                "type": "aws-apprunner",
                "label": "App Runner",
                "icon": "AR",
                "desc": "AWS App Runner — fully managed container web app",
            },
        ],
        "azure": [
            {"type": "az-aks", "label": "AKS", "icon": "AKS", "desc": "Azure Kubernetes Service — managed K8s"},
            {
                "type": "az-aci",
                "label": "Container Instances",
                "icon": "ACI",
                "desc": "Azure Container Instances — serverless containers",
            },
            {
                "type": "az-acr",
                "label": "ACR",
                "icon": "ACR",
                "desc": "Azure Container Registry — private container images",
            },
            {
                "type": "az-aca",
                "label": "Container Apps",
                "icon": "ACA",
                "desc": "Azure Container Apps — serverless microservices on Dapr",
            },
            {
                "type": "az-openshift",
                "label": "ARO",
                "icon": "ARO",
                "desc": "Azure Red Hat OpenShift — managed OpenShift",
            },
        ],
        "gcp": [
            {
                "type": "gcp-gke",
                "label": "GKE",
                "icon": "GKE",
                "desc": "GCP Google Kubernetes Engine — managed K8s (Autopilot/Standard)",
            },
            {
                "type": "gcp-cloudrun",
                "label": "Cloud Run",
                "icon": "CRN",
                "desc": "GCP Cloud Run — serverless container execution",
            },
            {
                "type": "gcp-gar",
                "label": "Artifact Registry",
                "icon": "GAR",
                "desc": "GCP Artifact Registry — container + language package registry",
            },
            {
                "type": "gcp-anthos",
                "label": "Anthos",
                "icon": "ANT",
                "desc": "GCP Anthos — multi-cloud/hybrid K8s management",
            },
        ],
        "oci": [
            {
                "type": "oci-oke",
                "label": "OKE",
                "icon": "OKE",
                "desc": "OCI Container Engine for Kubernetes — managed K8s",
            },
            {
                "type": "oci-ocir",
                "label": "OCIR",
                "icon": "OCR",
                "desc": "OCI Container Registry — private container images",
            },
            {
                "type": "oci-fn",
                "label": "Functions",
                "icon": "FN",
                "desc": "OCI Functions — serverless function (Fn Project)",
            },
        ],
        "ibm": [
            {"type": "ibm-iks", "label": "IKS", "icon": "IKS", "desc": "IBM Kubernetes Service — managed K8s"},
            {"type": "ibm-openshift", "label": "ROKS", "icon": "ROS", "desc": "Red Hat OpenShift on IBM Cloud"},
            {
                "type": "ibm-ce",
                "label": "Code Engine",
                "icon": "CE",
                "desc": "IBM Code Engine — serverless container/batch/job",
            },
            {"type": "ibm-cr", "label": "Container Registry", "icon": "ICR", "desc": "IBM Cloud Container Registry"},
        ],
        "onprem": [
            {
                "type": "op-k8s",
                "label": "K8s Cluster",
                "icon": "K8S",
                "desc": "Self-managed Kubernetes (kubeadm/RKE2/k3s)",
            },
            {
                "type": "op-openshift",
                "label": "OpenShift",
                "icon": "OCP",
                "desc": "Red Hat OpenShift Container Platform (on-prem)",
            },
            {"type": "op-docker", "label": "Docker Host", "icon": "DKR", "desc": "Docker/Podman container host"},
            {
                "type": "op-harbor",
                "label": "Harbor Registry",
                "icon": "HBR",
                "desc": "Harbor container registry (air-gap capable)",
            },
            {
                "type": "op-bigbang",
                "label": "Big Bang",
                "icon": "BB",
                "desc": "DoD Platform One Big Bang — GitOps K8s baseline",
            },
        ],
    },
    # ── Storage ──────────────────────────────────────────────────────────────
    "storage": {
        "aws": [
            {"type": "aws-s3", "label": "S3", "icon": "S3", "desc": "AWS S3 — object storage (Standard/IA/Glacier)"},
            {"type": "aws-ebs", "label": "EBS", "icon": "EBS", "desc": "AWS EBS — block storage for EC2 (gp3/io2/st1)"},
            {"type": "aws-efs", "label": "EFS", "icon": "EFS", "desc": "AWS EFS — managed NFS file system"},
            {
                "type": "aws-fsx",
                "label": "FSx",
                "icon": "FSX",
                "desc": "AWS FSx — managed Windows/Lustre/NetApp/OpenZFS",
            },
            {"type": "aws-backup", "label": "Backup", "icon": "BKP", "desc": "AWS Backup — centralized backup service"},
            {
                "type": "aws-storage-gw",
                "label": "Storage Gateway",
                "icon": "SGW",
                "desc": "AWS Storage Gateway — hybrid cloud storage",
            },
        ],
        "azure": [
            {
                "type": "az-blob",
                "label": "Blob Storage",
                "icon": "BLB",
                "desc": "Azure Blob Storage — object storage (Hot/Cool/Archive)",
            },
            {
                "type": "az-disk",
                "label": "Managed Disk",
                "icon": "DSK",
                "desc": "Azure Managed Disk — block storage for VMs",
            },
            {
                "type": "az-files",
                "label": "Azure Files",
                "icon": "AZF",
                "desc": "Azure Files — managed SMB/NFS file shares",
            },
            {
                "type": "az-netapp",
                "label": "NetApp Files",
                "icon": "NAF",
                "desc": "Azure NetApp Files — enterprise NAS",
            },
            {
                "type": "az-backup",
                "label": "Backup",
                "icon": "BKP",
                "desc": "Azure Backup — centralized backup + site recovery",
            },
            {
                "type": "az-data-box",
                "label": "Data Box",
                "icon": "DBX",
                "desc": "Azure Data Box — offline data transfer appliance",
            },
        ],
        "gcp": [
            {
                "type": "gcp-gcs",
                "label": "Cloud Storage",
                "icon": "GCS",
                "desc": "GCP Cloud Storage — object storage (Standard/Nearline/Coldline/Archive)",
            },
            {
                "type": "gcp-pd",
                "label": "Persistent Disk",
                "icon": "PD",
                "desc": "GCP Persistent Disk — block storage for GCE (SSD/Balanced/Standard)",
            },
            {"type": "gcp-filestore", "label": "Filestore", "icon": "FS", "desc": "GCP Filestore — managed NFS"},
            {
                "type": "gcp-backup",
                "label": "Backup & DR",
                "icon": "BKP",
                "desc": "GCP Backup and DR — managed backup service",
            },
        ],
        "oci": [
            {
                "type": "oci-os",
                "label": "Object Storage",
                "icon": "OOS",
                "desc": "OCI Object Storage — S3-compatible object store",
            },
            {
                "type": "oci-bv",
                "label": "Block Volume",
                "icon": "BV",
                "desc": "OCI Block Volume — block storage for compute",
            },
            {
                "type": "oci-fss",
                "label": "File Storage",
                "icon": "FSS",
                "desc": "OCI File Storage Service — managed NFS",
            },
        ],
        "ibm": [
            {
                "type": "ibm-cos",
                "label": "Cloud Object Storage",
                "icon": "COS",
                "desc": "IBM Cloud Object Storage — S3-compatible",
            },
            {"type": "ibm-block", "label": "Block Storage", "icon": "BLK", "desc": "IBM Cloud Block Storage for VPC"},
            {"type": "ibm-file", "label": "File Storage", "icon": "FIL", "desc": "IBM Cloud File Storage — NFS shares"},
        ],
        "onprem": [
            {"type": "op-san", "label": "SAN", "icon": "SAN", "desc": "Storage Area Network (FC/iSCSI)"},
            {"type": "op-nas", "label": "NAS", "icon": "NAS", "desc": "Network Attached Storage (NFS/SMB/CIFS)"},
            {
                "type": "op-s3-compat",
                "label": "S3 Compatible",
                "icon": "S3C",
                "desc": "MinIO/Ceph S3-compatible object storage",
            },
        ],
    },
    # ── Database Services ────────────────────────────────────────────────────
    "databases": {
        "aws": [
            {
                "type": "aws-rds",
                "label": "RDS",
                "icon": "RDS",
                "desc": "AWS RDS — managed relational DB (PostgreSQL/MySQL/Oracle/SQL Server)",
            },
            {
                "type": "aws-aurora",
                "label": "Aurora",
                "icon": "AUR",
                "desc": "AWS Aurora — MySQL/PostgreSQL-compatible, up to 5x performance",
            },
            {
                "type": "aws-dynamodb",
                "label": "DynamoDB",
                "icon": "DDB",
                "desc": "AWS DynamoDB — serverless NoSQL key-value/document",
            },
            {
                "type": "aws-redshift",
                "label": "Redshift",
                "icon": "RS",
                "desc": "AWS Redshift — petabyte-scale data warehouse",
            },
            {
                "type": "aws-elasticache",
                "label": "ElastiCache",
                "icon": "ECC",
                "desc": "AWS ElastiCache — managed Redis/Valkey",
            },
            {
                "type": "aws-documentdb",
                "label": "DocumentDB",
                "icon": "DOC",
                "desc": "AWS DocumentDB — MongoDB-compatible document DB",
            },
            {"type": "aws-neptune", "label": "Neptune", "icon": "NPT", "desc": "AWS Neptune — managed graph database"},
            {
                "type": "aws-timestream",
                "label": "Timestream",
                "icon": "TSM",
                "desc": "AWS Timestream — serverless time series DB",
            },
            {
                "type": "aws-keyspaces",
                "label": "Keyspaces",
                "icon": "KSP",
                "desc": "AWS Keyspaces — managed Cassandra-compatible",
            },
            {
                "type": "aws-opensearch",
                "label": "OpenSearch",
                "icon": "OS",
                "desc": "AWS OpenSearch — managed Elasticsearch + dashboards",
            },
        ],
        "azure": [
            {
                "type": "az-sql",
                "label": "SQL Database",
                "icon": "SQL",
                "desc": "Azure SQL Database — managed SQL Server",
            },
            {
                "type": "az-sql-mi",
                "label": "SQL MI",
                "icon": "SMI",
                "desc": "Azure SQL Managed Instance — near-100% SQL Server compat",
            },
            {
                "type": "az-postgres",
                "label": "PostgreSQL",
                "icon": "PGS",
                "desc": "Azure Database for PostgreSQL — Flexible Server",
            },
            {"type": "az-mysql", "label": "MySQL", "icon": "MYS", "desc": "Azure Database for MySQL — Flexible Server"},
            {
                "type": "az-cosmos",
                "label": "Cosmos DB",
                "icon": "CDB",
                "desc": "Azure Cosmos DB — multi-model global NoSQL",
            },
            {
                "type": "az-synapse",
                "label": "Synapse",
                "icon": "SYN",
                "desc": "Azure Synapse Analytics — data warehouse + big data",
            },
            {
                "type": "az-redis",
                "label": "Cache for Redis",
                "icon": "RDS",
                "desc": "Azure Cache for Redis — managed in-memory cache",
            },
            {
                "type": "az-search",
                "label": "AI Search",
                "icon": "AIS",
                "desc": "Azure AI Search — managed search (vector + semantic)",
            },
        ],
        "gcp": [
            {
                "type": "gcp-cloudsql",
                "label": "Cloud SQL",
                "icon": "SQL",
                "desc": "GCP Cloud SQL — managed PostgreSQL/MySQL/SQL Server",
            },
            {
                "type": "gcp-spanner",
                "label": "Spanner",
                "icon": "SPN",
                "desc": "GCP Spanner — globally distributed relational DB",
            },
            {
                "type": "gcp-bigtable",
                "label": "Bigtable",
                "icon": "BT",
                "desc": "GCP Bigtable — wide-column NoSQL (HBase-compatible)",
            },
            {
                "type": "gcp-firestore",
                "label": "Firestore",
                "icon": "FST",
                "desc": "GCP Firestore — serverless document DB",
            },
            {
                "type": "gcp-bigquery",
                "label": "BigQuery",
                "icon": "BQ",
                "desc": "GCP BigQuery — serverless data warehouse",
            },
            {
                "type": "gcp-memorystore",
                "label": "Memorystore",
                "icon": "MEM",
                "desc": "GCP Memorystore — managed Redis/Memcached",
            },
            {
                "type": "gcp-alloydb",
                "label": "AlloyDB",
                "icon": "ALY",
                "desc": "GCP AlloyDB — PostgreSQL-compatible, 4x faster",
            },
        ],
        "oci": [
            {
                "type": "oci-adb",
                "label": "Autonomous DB",
                "icon": "ADB",
                "desc": "OCI Autonomous Database — self-driving Oracle DB",
            },
            {
                "type": "oci-mysql",
                "label": "MySQL HeatWave",
                "icon": "MHW",
                "desc": "OCI MySQL HeatWave — MySQL + in-memory analytics",
            },
            {
                "type": "oci-nosql",
                "label": "NoSQL",
                "icon": "NOS",
                "desc": "OCI NoSQL Database — managed key-value/document",
            },
            {
                "type": "oci-postgresql",
                "label": "PostgreSQL",
                "icon": "PGS",
                "desc": "OCI Database with PostgreSQL — managed PostgreSQL",
            },
        ],
        "ibm": [
            {
                "type": "ibm-db2",
                "label": "Db2",
                "icon": "DB2",
                "desc": "IBM Db2 on Cloud — managed enterprise relational DB",
            },
            {"type": "ibm-postgres", "label": "PostgreSQL", "icon": "PGS", "desc": "IBM Databases for PostgreSQL"},
            {"type": "ibm-mongo", "label": "MongoDB", "icon": "MDB", "desc": "IBM Databases for MongoDB"},
            {"type": "ibm-redis", "label": "Redis", "icon": "RDS", "desc": "IBM Databases for Redis"},
            {"type": "ibm-elastic", "label": "Elasticsearch", "icon": "ES", "desc": "IBM Databases for Elasticsearch"},
        ],
        "onprem": [
            {"type": "op-postgres", "label": "PostgreSQL", "icon": "PGS", "desc": "Self-managed PostgreSQL"},
            {"type": "op-mysql", "label": "MySQL/MariaDB", "icon": "MYS", "desc": "Self-managed MySQL or MariaDB"},
            {"type": "op-oracle", "label": "Oracle DB", "icon": "ORA", "desc": "Oracle Database (on-premises)"},
            {
                "type": "op-sqlserver",
                "label": "SQL Server",
                "icon": "MSS",
                "desc": "Microsoft SQL Server (on-premises)",
            },
            {"type": "op-mongo", "label": "MongoDB", "icon": "MDB", "desc": "Self-managed MongoDB"},
            {"type": "op-redis", "label": "Redis", "icon": "RDS", "desc": "Self-managed Redis/Valkey"},
        ],
    },
    # ── Serverless & Functions ───────────────────────────────────────────────
    "serverless": {
        "aws": [
            {
                "type": "aws-lambda",
                "label": "Lambda",
                "icon": "LBD",
                "desc": "AWS Lambda — serverless function (Python/Node/Java/Go/.NET)",
            },
            {
                "type": "aws-step-fn",
                "label": "Step Functions",
                "icon": "SFN",
                "desc": "AWS Step Functions — serverless workflow orchestration",
            },
            {
                "type": "aws-eventbridge",
                "label": "EventBridge",
                "icon": "EB",
                "desc": "AWS EventBridge — serverless event bus",
            },
            {"type": "aws-sqs", "label": "SQS", "icon": "SQS", "desc": "AWS SQS — managed message queue"},
            {"type": "aws-sns", "label": "SNS", "icon": "SNS", "desc": "AWS SNS — managed pub/sub notifications"},
            {
                "type": "aws-apigw",
                "label": "API Gateway",
                "icon": "AGW",
                "desc": "AWS API Gateway — managed REST/WebSocket/HTTP API",
            },
        ],
        "azure": [
            {
                "type": "az-functions",
                "label": "Functions",
                "icon": "FN",
                "desc": "Azure Functions — serverless compute",
            },
            {
                "type": "az-logic-apps",
                "label": "Logic Apps",
                "icon": "LA",
                "desc": "Azure Logic Apps — workflow automation",
            },
            {"type": "az-event-grid", "label": "Event Grid", "icon": "EG", "desc": "Azure Event Grid — event routing"},
            {
                "type": "az-service-bus",
                "label": "Service Bus",
                "icon": "SB",
                "desc": "Azure Service Bus — enterprise message broker",
            },
            {
                "type": "az-event-hub",
                "label": "Event Hubs",
                "icon": "EH",
                "desc": "Azure Event Hubs — big data event streaming (Kafka-compatible)",
            },
            {
                "type": "az-apim",
                "label": "API Management",
                "icon": "APM",
                "desc": "Azure API Management — API gateway + developer portal",
            },
        ],
        "gcp": [
            {
                "type": "gcp-functions",
                "label": "Cloud Functions",
                "icon": "GCF",
                "desc": "GCP Cloud Functions — serverless function (Gen 2 on Cloud Run)",
            },
            {
                "type": "gcp-workflows",
                "label": "Workflows",
                "icon": "WF",
                "desc": "GCP Workflows — serverless orchestration",
            },
            {
                "type": "gcp-pubsub",
                "label": "Pub/Sub",
                "icon": "PS",
                "desc": "GCP Pub/Sub — managed message queue / event streaming",
            },
            {"type": "gcp-tasks", "label": "Cloud Tasks", "icon": "CT", "desc": "GCP Cloud Tasks — managed task queue"},
            {
                "type": "gcp-apigee",
                "label": "Apigee",
                "icon": "APG",
                "desc": "GCP Apigee — full-lifecycle API management",
            },
        ],
        "oci": [
            {
                "type": "oci-functions",
                "label": "Functions",
                "icon": "FN",
                "desc": "OCI Functions — serverless Fn Project",
            },
            {"type": "oci-events", "label": "Events", "icon": "EVT", "desc": "OCI Events — event routing service"},
            {
                "type": "oci-streaming",
                "label": "Streaming",
                "icon": "STM",
                "desc": "OCI Streaming — Kafka-compatible streaming",
            },
            {"type": "oci-queue", "label": "Queue", "icon": "QUE", "desc": "OCI Queue — managed message queue"},
            {"type": "oci-apigw", "label": "API Gateway", "icon": "AGW", "desc": "OCI API Gateway — managed API proxy"},
        ],
        "ibm": [
            {
                "type": "ibm-functions",
                "label": "Functions",
                "icon": "FN",
                "desc": "IBM Cloud Functions — serverless (Apache OpenWhisk)",
            },
            {"type": "ibm-mq", "label": "MQ", "icon": "MQ", "desc": "IBM MQ on Cloud — enterprise message queue"},
            {
                "type": "ibm-event-streams",
                "label": "Event Streams",
                "icon": "ES",
                "desc": "IBM Event Streams — managed Kafka",
            },
        ],
    },
    # ── AI/ML Services ───────────────────────────────────────────────────────
    "ai_ml": {
        "aws": [
            {
                "type": "aws-sagemaker",
                "label": "SageMaker",
                "icon": "SM",
                "desc": "AWS SageMaker — ML training, hosting, MLOps",
            },
            {
                "type": "aws-bedrock",
                "label": "Bedrock",
                "icon": "BRK",
                "desc": "AWS Bedrock — managed foundation models (Claude, Llama, Titan)",
            },
            {
                "type": "aws-comprehend",
                "label": "Comprehend",
                "icon": "CMP",
                "desc": "AWS Comprehend — NLP text analysis",
            },
            {
                "type": "aws-rekognition",
                "label": "Rekognition",
                "icon": "REK",
                "desc": "AWS Rekognition — image/video analysis",
            },
        ],
        "azure": [
            {
                "type": "az-ml",
                "label": "Machine Learning",
                "icon": "AML",
                "desc": "Azure Machine Learning — ML workspace + MLOps",
            },
            {
                "type": "az-openai",
                "label": "OpenAI Service",
                "icon": "AOI",
                "desc": "Azure OpenAI Service — GPT-4, embeddings, DALL-E",
            },
            {
                "type": "az-cognitive",
                "label": "AI Services",
                "icon": "AIS",
                "desc": "Azure AI Services — vision, speech, language, decision",
            },
            {
                "type": "az-search-ai",
                "label": "AI Search",
                "icon": "AIS",
                "desc": "Azure AI Search — vector + semantic search",
            },
        ],
        "gcp": [
            {
                "type": "gcp-vertex",
                "label": "Vertex AI",
                "icon": "VAI",
                "desc": "GCP Vertex AI — ML platform + model garden + RAG",
            },
            {
                "type": "gcp-genai",
                "label": "Gemini API",
                "icon": "GEM",
                "desc": "GCP Gemini API — Google foundation models",
            },
            {
                "type": "gcp-vision",
                "label": "Vision AI",
                "icon": "VIS",
                "desc": "GCP Vision AI — image analysis and OCR",
            },
        ],
        "oci": [
            {
                "type": "oci-ds",
                "label": "Data Science",
                "icon": "ODS",
                "desc": "OCI Data Science — managed Jupyter + model deploy",
            },
            {
                "type": "oci-genai",
                "label": "Generative AI",
                "icon": "GAI",
                "desc": "OCI Generative AI — hosted LLMs (Cohere, Meta)",
            },
            {
                "type": "oci-ai-vision",
                "label": "AI Vision",
                "icon": "VIS",
                "desc": "OCI AI Vision — image analysis service",
            },
        ],
        "ibm": [
            {
                "type": "ibm-watsonx",
                "label": "watsonx.ai",
                "icon": "WXA",
                "desc": "IBM watsonx.ai — foundation models + prompt lab",
            },
            {
                "type": "ibm-watson-nlp",
                "label": "Watson NLP",
                "icon": "WNL",
                "desc": "IBM Watson NLP — text classification, entities, sentiment",
            },
        ],
        "onprem": [
            {
                "type": "op-ollama",
                "label": "Ollama",
                "icon": "OLL",
                "desc": "Ollama — local LLM inference (Llama, Qwen, Phi)",
            },
            {"type": "op-mlflow", "label": "MLflow", "icon": "MLF", "desc": "MLflow — open-source MLOps platform"},
            {
                "type": "op-gpu-node",
                "label": "GPU Node",
                "icon": "GPU",
                "desc": "On-premises GPU server (NVIDIA A100/H100/RTX)",
            },
        ],
    },
    # ── Security & Identity ──────────────────────────────────────────────────
    "security": {
        "aws": [
            {"type": "aws-iam", "label": "IAM", "icon": "IAM", "desc": "AWS IAM — identity and access management"},
            {
                "type": "aws-kms",
                "label": "KMS",
                "icon": "KMS",
                "desc": "AWS KMS — managed encryption key service (FIPS 140-2 L2/L3)",
            },
            {
                "type": "aws-secrets",
                "label": "Secrets Manager",
                "icon": "SM",
                "desc": "AWS Secrets Manager — managed secret rotation",
            },
            {
                "type": "aws-cognito",
                "label": "Cognito",
                "icon": "COG",
                "desc": "AWS Cognito — user pool + identity federation",
            },
            {
                "type": "aws-config",
                "label": "Config",
                "icon": "CFG",
                "desc": "AWS Config — resource compliance auditing",
            },
            {
                "type": "aws-securityhub",
                "label": "Security Hub",
                "icon": "SH",
                "desc": "AWS Security Hub — aggregated security findings (CSPM)",
            },
        ],
        "azure": [
            {
                "type": "az-entra",
                "label": "Entra ID",
                "icon": "EID",
                "desc": "Microsoft Entra ID (Azure AD) — identity platform",
            },
            {
                "type": "az-keyvault",
                "label": "Key Vault",
                "icon": "KV",
                "desc": "Azure Key Vault — secrets, keys, certificates (FIPS 140-2 L2/L3)",
            },
            {
                "type": "az-defender",
                "label": "Defender for Cloud",
                "icon": "DFC",
                "desc": "Microsoft Defender for Cloud — CSPM + CWPP",
            },
            {
                "type": "az-sentinel-sec",
                "label": "Sentinel",
                "icon": "STN",
                "desc": "Microsoft Sentinel — cloud-native SIEM + SOAR",
            },
            {
                "type": "az-purview",
                "label": "Purview",
                "icon": "PUR",
                "desc": "Microsoft Purview — data governance + DLP",
            },
        ],
        "gcp": [
            {
                "type": "gcp-iam",
                "label": "Cloud IAM",
                "icon": "IAM",
                "desc": "GCP Cloud IAM — identity and access management",
            },
            {
                "type": "gcp-kms",
                "label": "Cloud KMS",
                "icon": "KMS",
                "desc": "GCP Cloud KMS — managed encryption keys (FIPS 140-2 L1-L3)",
            },
            {
                "type": "gcp-secret",
                "label": "Secret Manager",
                "icon": "SM",
                "desc": "GCP Secret Manager — managed secret storage",
            },
            {
                "type": "gcp-scc",
                "label": "Security Command Center",
                "icon": "SCC",
                "desc": "GCP Security Command Center — CSPM + threat detection",
            },
        ],
        "oci": [
            {
                "type": "oci-iam",
                "label": "IAM",
                "icon": "IAM",
                "desc": "OCI IAM — identity and access management with compartments",
            },
            {
                "type": "oci-vault",
                "label": "Vault",
                "icon": "VLT",
                "desc": "OCI Vault — managed key management (FIPS 140-2 L3)",
            },
            {
                "type": "oci-cspm",
                "label": "Cloud Guard",
                "icon": "CG",
                "desc": "OCI Cloud Guard — CSPM + auto-remediation",
            },
        ],
        "ibm": [
            {
                "type": "ibm-iam",
                "label": "IAM",
                "icon": "IAM",
                "desc": "IBM Cloud IAM — identity and access management",
            },
            {
                "type": "ibm-kms",
                "label": "Key Protect",
                "icon": "KP",
                "desc": "IBM Key Protect — managed encryption keys (FIPS 140-2 L3)",
            },
            {
                "type": "ibm-secrets",
                "label": "Secrets Manager",
                "icon": "SM",
                "desc": "IBM Secrets Manager — centralized secret store",
            },
            {
                "type": "ibm-scc",
                "label": "SCC",
                "icon": "SCC",
                "desc": "IBM Security & Compliance Center — posture management",
            },
        ],
    },
    # ── Application Services ─────────────────────────────────────────────────
    "app_services": {
        "aws": [
            {"type": "aws-elb", "label": "ELB", "icon": "ELB", "desc": "AWS Elastic Load Balancing (ALB/NLB/CLB)"},
            {
                "type": "aws-elasticbeanstalk",
                "label": "Elastic Beanstalk",
                "icon": "EB",
                "desc": "AWS Elastic Beanstalk — managed PaaS",
            },
            {"type": "aws-ses", "label": "SES", "icon": "SES", "desc": "AWS SES — managed email sending"},
        ],
        "azure": [
            {
                "type": "az-app-service",
                "label": "App Service",
                "icon": "APS",
                "desc": "Azure App Service — managed web app hosting",
            },
            {
                "type": "az-static-web",
                "label": "Static Web Apps",
                "icon": "SWA",
                "desc": "Azure Static Web Apps — JAMstack hosting",
            },
            {
                "type": "az-signalr",
                "label": "SignalR",
                "icon": "SGR",
                "desc": "Azure SignalR — managed WebSocket service",
            },
        ],
        "gcp": [
            {"type": "gcp-app-engine", "label": "App Engine", "icon": "GAE", "desc": "GCP App Engine — managed PaaS"},
            {
                "type": "gcp-firebase",
                "label": "Firebase",
                "icon": "FB",
                "desc": "GCP Firebase — app platform (hosting, auth, DB)",
            },
        ],
        "oci": [
            {
                "type": "oci-app-config",
                "label": "App Config",
                "icon": "ACF",
                "desc": "OCI Application Performance Monitoring + config",
            },
        ],
    },
    # ── IaC & DevOps Tools ───────────────────────────────────────────────────
    "iac_tools": [
        {"type": "iac-terraform", "label": "Terraform", "icon": "TF", "desc": "HashiCorp Terraform — multi-cloud IaC"},
        {"type": "iac-opentofu", "label": "OpenTofu", "icon": "OTF", "desc": "OpenTofu — open-source Terraform fork"},
        {"type": "iac-pulumi", "label": "Pulumi", "icon": "PLM", "desc": "Pulumi — IaC in Python/TypeScript/Go/C#"},
        {
            "type": "iac-ansible",
            "label": "Ansible",
            "icon": "ANS",
            "desc": "Red Hat Ansible — configuration management + automation",
        },
        {"type": "iac-helm", "label": "Helm Chart", "icon": "HLM", "desc": "Helm — Kubernetes package manager"},
        {"type": "iac-argocd", "label": "ArgoCD", "icon": "AGO", "desc": "ArgoCD — GitOps continuous delivery for K8s"},
        {"type": "iac-flux", "label": "Flux", "icon": "FLX", "desc": "Flux — GitOps toolkit for Kubernetes"},
        {
            "type": "iac-crossplane",
            "label": "Crossplane",
            "icon": "XP",
            "desc": "Crossplane — Kubernetes-native cloud infrastructure management",
        },
        {
            "type": "iac-packer",
            "label": "Packer",
            "icon": "PKR",
            "desc": "HashiCorp Packer — machine image builder (AMI/VHD/OVA)",
        },
        {
            "type": "iac-vault",
            "label": "Vault",
            "icon": "VLT",
            "desc": "HashiCorp Vault — secret management + encryption as a service",
        },
    ],
    "digital_twin": [
        {
            "type": "twin-iac",
            "label": "IaC Twin",
            "icon": "IT",
            "desc": "Infrastructure-as-Code digital twin — imports Terraform/Pulumi/AWS state into append-only snapshots for drift detection and pre-apply compliance gating",
        },
        {
            "type": "twin-drift",
            "label": "Infra Drift Detector",
            "icon": "ID",
            "desc": "Compares live infra state against last approved snapshot; emits drift events (added/removed/changed resources) on deviation",
        },
        {
            "type": "twin-preapply-gate",
            "label": "Pre-Apply Gate",
            "icon": "PG",
            "desc": "Parses terraform plan JSON, runs IQE seed checks against planned final state, exits 1 on fail — blocks non-compliant applies",
        },
        {
            "type": "twin-cross-csp",
            "label": "Cross-CSP Simulator",
            "icon": "CC",
            "desc": "Simulates migrating a workload across CSPs — surfaces cost delta, FedRAMP IL compliance delta, and unmapped resource types",
        },
        {
            "type": "twin-compliance-gate",
            "label": "IDC Compliance Gate",
            "icon": "CG",
            "desc": "Evaluates delta snapshot against FIPS 199/200, FedRAMP baseline, STIG checks, and IL boundary rules before apply",
        },
    ],
}

# ── Infrastructure Compliance Frameworks ─────────────────────────────────────
INFRA_COMPLIANCE_FRAMEWORKS = {
    "nist_800_53",
    "fedramp",
    "cmmc_l2",
    "cis_benchmark",
    "dod_cc_srg",
    "disa_stig",
    "fips_140",
    "hipaa",
    "pci_dss",
}

# ── Infrastructure Compliance Rules (deterministic checks) ───────────────────
INFRA_COMPLIANCE_RULES = [
    # Encryption
    {
        "id": "IDC-ENC-001",
        "title": "Storage encrypted at rest",
        "severity": "CAT1",
        "category": "encryption",
        "description": "All storage resources (S3/EBS/Blob/Disk) must have encryption enabled — FIPS 140-2 validated for CUI (NIST SC-28).",
        "check": "storage_encrypted",
    },
    {
        "id": "IDC-ENC-002",
        "title": "Database encryption at rest",
        "severity": "CAT1",
        "category": "encryption",
        "description": "All database services must have encryption at rest enabled with KMS-managed keys (NIST SC-28).",
        "check": "db_encrypted",
    },
    {
        "id": "IDC-ENC-003",
        "title": "KMS present for key management",
        "severity": "CAT1",
        "category": "encryption",
        "description": "Design must include a KMS/Key Vault service for centralized encryption key management (NIST SC-12).",
        "check": "kms_present",
    },
    # Compute
    {
        "id": "IDC-CMP-001",
        "title": "No public-facing compute without load balancer",
        "severity": "CAT2",
        "category": "compute",
        "description": "Compute instances should not be directly internet-facing — use load balancer or API gateway (NIST SC-7).",
        "check": "compute_behind_lb",
    },
    {
        "id": "IDC-CMP-002",
        "title": "Auto-scaling configured for production",
        "severity": "CAT2",
        "category": "availability",
        "description": "Production compute should have auto-scaling (ASG/VMSS/MIG) for availability and resilience (NIST CP-7, SC-36).",
        "check": "autoscaling_configured",
    },
    {
        "id": "IDC-CMP-003",
        "title": "Dedicated hosts for compliance workloads",
        "severity": "CAT2",
        "category": "compliance",
        "description": "ITAR/EAR/CJIS workloads should use dedicated or sole-tenant hosts to ensure physical isolation.",
        "check": "dedicated_for_compliance",
    },
    # Container
    {
        "id": "IDC-CTR-001",
        "title": "Container registry is private",
        "severity": "CAT1",
        "category": "containers",
        "description": "Container registry must be private (not public) with image scanning enabled (NIST CM-2, SA-10).",
        "check": "registry_private",
    },
    {
        "id": "IDC-CTR-002",
        "title": "K8s cluster uses RBAC",
        "severity": "CAT1",
        "category": "containers",
        "description": "Kubernetes clusters must have RBAC enabled and default service account restricted (NIST AC-3, AC-6).",
        "check": "k8s_rbac",
    },
    # Identity
    {
        "id": "IDC-IAM-001",
        "title": "IAM/IdP service present",
        "severity": "CAT1",
        "category": "identity",
        "description": "Design must include an IAM or identity provider service for authentication and authorization (NIST IA-2, AC-2).",
        "check": "iam_present",
    },
    {
        "id": "IDC-IAM-002",
        "title": "Secrets managed centrally",
        "severity": "CAT1",
        "category": "identity",
        "description": "Application secrets must use a secrets manager — not hardcoded or in environment variables (NIST IA-5).",
        "check": "secrets_managed",
    },
    # Backup & Recovery
    {
        "id": "IDC-BAK-001",
        "title": "Backup service configured",
        "severity": "CAT2",
        "category": "backup",
        "description": "Production data stores should have backup service configured with defined RPO/RTO (NIST CP-9, CP-10).",
        "check": "backup_configured",
    },
    # CSPM
    {
        "id": "IDC-SEC-001",
        "title": "CSPM/Security posture service present",
        "severity": "CAT2",
        "category": "security",
        "description": "Design should include a CSPM service (Security Hub/Defender/SCC/Cloud Guard) for continuous compliance monitoring.",
        "check": "cspm_present",
    },
    # IaC
    {
        "id": "IDC-IAC-001",
        "title": "Infrastructure defined as code",
        "severity": "CAT2",
        "category": "iac",
        "description": "Design should include an IaC tool (Terraform/Pulumi/Crossplane) for reproducible, auditable infrastructure.",
        "check": "iac_present",
    },
]

# ── CSP Equivalence Mapping ──────────────────────────────────────────────────
# Maps generic service concepts to CSP-specific implementations.
# Used by the recommendation engine to suggest alternatives.
CSP_EQUIVALENCE = {
    "managed_k8s": {
        "aws": "aws-eks",
        "azure": "az-aks",
        "gcp": "gcp-gke",
        "oci": "oci-oke",
        "ibm": "ibm-iks",
    },
    "serverless_function": {
        "aws": "aws-lambda",
        "azure": "az-functions",
        "gcp": "gcp-functions",
        "oci": "oci-functions",
        "ibm": "ibm-functions",
    },
    "object_storage": {
        "aws": "aws-s3",
        "azure": "az-blob",
        "gcp": "gcp-gcs",
        "oci": "oci-os",
        "ibm": "ibm-cos",
    },
    "managed_postgres": {
        "aws": "aws-rds",
        "azure": "az-postgres",
        "gcp": "gcp-cloudsql",
        "oci": "oci-postgresql",
        "ibm": "ibm-postgres",
    },
    "key_management": {
        "aws": "aws-kms",
        "azure": "az-keyvault",
        "gcp": "gcp-kms",
        "oci": "oci-vault",
        "ibm": "ibm-kms",
    },
    "container_registry": {
        "aws": "aws-ecr",
        "azure": "az-acr",
        "gcp": "gcp-gar",
        "oci": "oci-ocir",
        "ibm": "ibm-cr",
    },
    "message_queue": {
        "aws": "aws-sqs",
        "azure": "az-service-bus",
        "gcp": "gcp-pubsub",
        "oci": "oci-queue",
        "ibm": "ibm-mq",
    },
    "data_warehouse": {
        "aws": "aws-redshift",
        "azure": "az-synapse",
        "gcp": "gcp-bigquery",
        "oci": "oci-adb",
        "ibm": "ibm-db2",
    },
    "foundation_model": {
        "aws": "aws-bedrock",
        "azure": "az-openai",
        "gcp": "gcp-genai",
        "oci": "oci-genai",
        "ibm": "ibm-watsonx",
    },
}

# ── Ontology Mapping (infra->ICDEV infrastructure ontology) ──────────────────
INFRA_ONTOLOGY_MAP: dict[str, str] = {
    # Compute
    "aws-ec2":          "https://icdev.dev/ontology/infrastructure#ComputeService.EC2",
    "aws-ec2-asg":      "https://icdev.dev/ontology/infrastructure#ComputeService.AutoScaling",
    "aws-ec2-spot":     "https://icdev.dev/ontology/infrastructure#ComputeService.Spot",
    "aws-ec2-dedicated":"https://icdev.dev/ontology/infrastructure#ComputeService.DedicatedHost",
    "az-vm":            "https://icdev.dev/ontology/infrastructure#ComputeService.VirtualMachine",
    "az-vmss":          "https://icdev.dev/ontology/infrastructure#ComputeService.VMScaleSet",
    "gcp-gce":          "https://icdev.dev/ontology/infrastructure#ComputeService.ComputeEngine",
    "gcp-mig":          "https://icdev.dev/ontology/infrastructure#ComputeService.InstanceGroup",
    "oci-compute":      "https://icdev.dev/ontology/infrastructure#ComputeService.Compute",
    "ibm-vsi":          "https://icdev.dev/ontology/infrastructure#ComputeService.VirtualServer",
    "op-server":        "https://icdev.dev/ontology/infrastructure#ComputeService.PhysicalServer",
    "op-vm":            "https://icdev.dev/ontology/infrastructure#ComputeService.OnPremVM",
    # Containers
    "aws-eks":          "https://icdev.dev/ontology/infrastructure#ContainerService.EKS",
    "aws-ecs":          "https://icdev.dev/ontology/infrastructure#ContainerService.ECS",
    "aws-fargate":      "https://icdev.dev/ontology/infrastructure#ContainerService.Fargate",
    "az-aks":           "https://icdev.dev/ontology/infrastructure#ContainerService.AKS",
    "gcp-gke":          "https://icdev.dev/ontology/infrastructure#ContainerService.GKE",
    "oci-oke":          "https://icdev.dev/ontology/infrastructure#ContainerService.OKE",
    "ibm-iks":          "https://icdev.dev/ontology/infrastructure#ContainerService.IKS",
    "op-k8s":           "https://icdev.dev/ontology/infrastructure#ContainerService.Kubernetes",
    # Storage
    "aws-s3":           "https://icdev.dev/ontology/infrastructure#ObjectStorage.S3",
    "aws-ebs":          "https://icdev.dev/ontology/infrastructure#BlockStorage.EBS",
    "az-blob":          "https://icdev.dev/ontology/infrastructure#ObjectStorage.Blob",
    "gcp-gcs":          "https://icdev.dev/ontology/infrastructure#ObjectStorage.CloudStorage",
    "oci-os":           "https://icdev.dev/ontology/infrastructure#ObjectStorage.ObjectStorage",
    # Database
    "aws-rds":          "https://icdev.dev/ontology/infrastructure#DatabaseService.RDS",
    "aws-dynamodb":     "https://icdev.dev/ontology/infrastructure#DatabaseService.DynamoDB",
    "az-sql":           "https://icdev.dev/ontology/infrastructure#DatabaseService.SQLDatabase",
    "gcp-cloudsql":     "https://icdev.dev/ontology/infrastructure#DatabaseService.CloudSQL",
    # Serverless
    "aws-lambda":       "https://icdev.dev/ontology/infrastructure#ServerlessFunction.Lambda",
    "az-functions":     "https://icdev.dev/ontology/infrastructure#ServerlessFunction.Functions",
    "gcp-functions":    "https://icdev.dev/ontology/infrastructure#ServerlessFunction.CloudFunctions",
    # AI/ML
    "aws-sagemaker":    "https://icdev.dev/ontology/infrastructure#AIMLService.SageMaker",
    "az-ml":            "https://icdev.dev/ontology/infrastructure#AIMLService.MachineLearning",
    "gcp-vertex":       "https://icdev.dev/ontology/infrastructure#AIMLService.VertexAI",
    # Security
    "aws-iam":          "https://icdev.dev/ontology/infrastructure#IdentityService.IAM",
    "aws-kms":          "https://icdev.dev/ontology/infrastructure#KeyManagementService.KMS",
    "az-keyvault":      "https://icdev.dev/ontology/infrastructure#KeyManagementService.KeyVault",
    # Load Balancer
    "aws-elb":          "https://icdev.dev/ontology/infrastructure#LoadBalancer.ELB",
    "aws-alb":          "https://icdev.dev/ontology/infrastructure#LoadBalancer.ALB",
    "az-appgw":         "https://icdev.dev/ontology/infrastructure#LoadBalancer.AppGateway",
    # IaC
    "iac-terraform":    "https://icdev.dev/ontology/infrastructure#IaCTool.Terraform",
    "iac-ansible":      "https://icdev.dev/ontology/infrastructure#IaCTool.Ansible",
    "iac-helm":         "https://icdev.dev/ontology/infrastructure#IaCTool.Helm",
    # Digital Twin
    "twin-iac":         "https://icdev.dev/ontology/infrastructure#DigitalTwin.IaCTwin",
    "twin-drift":       "https://icdev.dev/ontology/infrastructure#DigitalTwin.DriftDetector",
}

