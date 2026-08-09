"""Data IaC Generator — DDC Workflow Step 3.

Reads canvas designs from data_canvas.db and generates:
  data/studio_artifacts/ddc/terraform/main.tf       — Terraform resources
  data/studio_artifacts/ddc/terraform/variables.tf  — Variable definitions
  data/studio_artifacts/ddc/terraform/terraform.tfvars — Example values
  data/studio_artifacts/ddc/iac_report_<id>.md      — Generation report

Outputs JSON with artifact paths to stdout.
"""
import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))

_ARTIFACTS_DIR = _ROOT / "data" / "studio_artifacts" / "ddc"

# ── Terraform resource blocks per node type ───────────────────────────────────

_TF_RESOURCES: dict[str, str] = {
    "ent-file": '''
resource "aws_s3_bucket" "{name}" {{
  bucket = "{slug}"
  tags   = local.common_tags
}}
resource "aws_s3_bucket_versioning" "{name}_ver" {{
  bucket = aws_s3_bucket.{name}.id
  versioning_configuration {{ status = "Enabled" }}
}}
resource "aws_s3_bucket_server_side_encryption_configuration" "{name}_sse" {{
  bucket = aws_s3_bucket.{name}.id
  rule {{ apply_server_side_encryption_by_default {{ sse_algorithm = "aws:kms" }} }}
}}
''',
    "ent-rds": '''
resource "aws_db_instance" "{name}" {{
  identifier          = "{slug}"
  engine              = "postgres"
  engine_version      = "16.1"
  instance_class      = var.db_instance_class
  allocated_storage   = 20
  storage_encrypted   = true
  username            = "dbadmin"
  password            = var.db_password
  skip_final_snapshot = false
  deletion_protection = true
  backup_retention_period = 7
  tags = local.common_tags
}}
''',
    "ent-vector": '''
resource "aws_db_instance" "{name}" {{
  identifier          = "{slug}"
  engine              = "postgres"
  engine_version      = "16.1"
  instance_class      = var.db_instance_class
  allocated_storage   = 50
  storage_encrypted   = true
  username            = "vectoradmin"
  password            = var.db_password
  skip_final_snapshot = false
  deletion_protection = true
  backup_retention_period = 7
  tags = merge(local.common_tags, {{ Purpose = "vector-store" }})
}}
# pgvector extension enabled via RDS parameter group
resource "aws_db_parameter_group" "{name}_pg" {{
  name   = "{slug}-params"
  family = "postgres16"
  parameter {{ name = "shared_preload_libraries" value = "pg_vector" }}
}}
''',
    "ent-graph": '''
resource "aws_neptune_cluster" "{name}" {{
  cluster_identifier                  = "{slug}"
  engine                              = "neptune"
  backup_retention_period             = 7
  preferred_backup_window             = "02:00-03:00"
  skip_final_snapshot                 = false
  iam_database_authentication_enabled = true
  storage_encrypted                   = true
  tags = local.common_tags
}}
resource "aws_neptune_cluster_instance" "{name}_instance" {{
  cluster_identifier = aws_neptune_cluster.{name}.id
  instance_class     = "db.r6g.large"
  engine             = "neptune"
  tags = local.common_tags
}}
''',
    "ent-cache": '''
resource "aws_elasticache_replication_group" "{name}" {{
  replication_group_id       = "{slug}"
  description                = "{label} cache"
  node_type                  = var.cache_node_type
  num_cache_clusters         = 2
  at_rest_encryption_enabled = true
  transit_encryption_enabled = true
  tags = local.common_tags
}}
''',
    "ent-redis": '''
resource "aws_elasticache_replication_group" "{name}" {{
  replication_group_id       = "{slug}"
  description                = "{label}"
  node_type                  = var.cache_node_type
  num_cache_clusters         = 2
  at_rest_encryption_enabled = true
  transit_encryption_enabled = true
  tags = local.common_tags
}}
''',
    "ent-kafka": '''
resource "aws_msk_cluster" "{name}" {{
  cluster_name           = "{slug}"
  kafka_version          = "3.6.0"
  number_of_broker_nodes = 3
  broker_node_group_info {{
    instance_type  = "kafka.m5.large"
    client_subnets = var.private_subnet_ids
    storage_info {{ ebs_storage_info {{ volume_size = 100 }} }}
  }}
  encryption_info {{ encryption_in_transit {{ client_broker = "TLS" }} }}
  tags = local.common_tags
}}
''',
    "ent-db": '''
resource "aws_db_instance" "{name}" {{
  identifier          = "{slug}"
  engine              = "mysql"
  engine_version      = "8.0"
  instance_class      = var.db_instance_class
  allocated_storage   = 20
  storage_encrypted   = true
  username            = "dbadmin"
  password            = var.db_password
  skip_final_snapshot = false
  backup_retention_period = 7
  tags = local.common_tags
}}
''',
    "ent-datalake": '''
# Medallion lake zones for {label}
resource "aws_kms_key" "{name}_lake_key" {{
  description             = "{label} lake CMK — {classification}"
  deletion_window_in_days = 30
  enable_key_rotation     = true
  tags                    = local.common_tags
}}
resource "aws_s3_bucket" "{name}_landing" {{
  bucket = "{slug}-landing"
  tags   = merge(local.common_tags, {{ Zone = "Landing" }})
}}
resource "aws_s3_bucket_server_side_encryption_configuration" "{name}_landing_sse" {{
  bucket = aws_s3_bucket.{name}_landing.id
  rule {{ apply_server_side_encryption_by_default {{
    sse_algorithm     = "aws:kms"
    kms_master_key_id = aws_kms_key.{name}_lake_key.arn
  }} }}
}}
resource "aws_s3_bucket_lifecycle_configuration" "{name}_landing_lc" {{
  bucket = aws_s3_bucket.{name}_landing.id
  rule {{ id = "expire-landing" status = "Enabled" expiration {{ days = 7 }} }}
}}
resource "aws_s3_bucket" "{name}_raw" {{
  bucket = "{slug}-raw"
  tags   = merge(local.common_tags, {{ Zone = "Raw" }})
}}
resource "aws_s3_bucket_versioning" "{name}_raw_ver" {{
  bucket = aws_s3_bucket.{name}_raw.id
  versioning_configuration {{ status = "Enabled" }}
}}
resource "aws_s3_bucket_server_side_encryption_configuration" "{name}_raw_sse" {{
  bucket = aws_s3_bucket.{name}_raw.id
  rule {{ apply_server_side_encryption_by_default {{
    sse_algorithm     = "aws:kms"
    kms_master_key_id = aws_kms_key.{name}_lake_key.arn
  }} }}
}}
resource "aws_s3_bucket" "{name}_curated" {{
  bucket = "{slug}-curated"
  tags   = merge(local.common_tags, {{ Zone = "Curated" }})
}}
resource "aws_s3_bucket_versioning" "{name}_curated_ver" {{
  bucket = aws_s3_bucket.{name}_curated.id
  versioning_configuration {{ status = "Enabled" }}
}}
resource "aws_s3_bucket" "{name}_consumption" {{
  bucket = "{slug}-consumption"
  tags   = merge(local.common_tags, {{ Zone = "Consumption" }})
}}
resource "aws_s3_bucket_versioning" "{name}_consumption_ver" {{
  bucket = aws_s3_bucket.{name}_consumption.id
  versioning_configuration {{ status = "Enabled" }}
}}
''',
    "ent-warehouse": '''
resource "aws_redshift_cluster" "{name}" {{
  cluster_identifier = "{slug}"
  database_name      = "warehouse"
  master_username    = "dwadmin"
  master_password    = var.dw_password
  node_type          = "dc2.large"
  cluster_type       = "multi-node"
  number_of_nodes    = 2
  encrypted          = true
  tags = local.common_tags
}}
''',
    "ent-elasticsearch": '''
resource "aws_opensearch_domain" "{name}" {{
  domain_name    = "{slug}"
  engine_version = "OpenSearch_2.13"
  cluster_config {{
    instance_type  = "m6g.large.search"
    instance_count = 2
  }}
  encrypt_at_rest {{ enabled = true }}
  node_to_node_encryption {{ enabled = true }}
  tags = local.common_tags
}}
''',
    "flow-api": '''
resource "aws_api_gateway_rest_api" "{name}" {{
  name        = "{slug}"
  description = "{label}"
  tags = local.common_tags
}}
resource "aws_lambda_function" "{name}_handler" {{
  function_name = "{slug}-handler"
  role          = aws_iam_role.lambda_exec.arn
  handler       = "index.handler"
  runtime       = "python3.12"
  filename      = "${{path.module}}/lambda/{slug}.zip"
  tags = local.common_tags
}}
''',
    "flow-etl": '''
resource "aws_glue_job" "{name}" {{
  name     = "{slug}"
  role_arn = aws_iam_role.glue_exec.arn
  command {{
    name            = "glueetl"
    script_location = "s3://${{var.artifacts_bucket}}/glue-scripts/{slug}.py"
    python_version  = "3"
  }}
  default_arguments = {{
    "--enable-metrics"                       = ""
    "--enable-continuous-cloudwatch-log"     = "true"
    "--enable-job-insights"                  = "true"
  }}
  tags = local.common_tags
}}
''',
    "flow": '''
resource "aws_glue_job" "{name}" {{
  name     = "{slug}"
  role_arn = aws_iam_role.glue_exec.arn
  command {{
    name            = "glueetl"
    script_location = "s3://${{var.artifacts_bucket}}/glue-scripts/{slug}.py"
    python_version  = "3"
  }}
  tags = local.common_tags
}}
''',
    "ctrl-kms": '''
resource "aws_kms_key" "{name}" {{
  description             = "{label}"
  deletion_window_in_days = 30
  enable_key_rotation     = true
  tags = local.common_tags
}}
resource "aws_kms_alias" "{name}_alias" {{
  name          = "alias/{slug}"
  target_key_id = aws_kms_key.{name}.key_id
}}
''',
    "ctrl-encryption": '''
resource "aws_kms_key" "{name}" {{
  description             = "{label}"
  deletion_window_in_days = 30
  enable_key_rotation     = true
  tags = local.common_tags
}}
resource "aws_kms_alias" "{name}_alias" {{
  name          = "alias/{slug}"
  target_key_id = aws_kms_key.{name}.key_id
}}
''',
    "ctrl-backup": '''
resource "aws_backup_vault" "{name}_vault" {{
  name = "{slug}-vault"
  tags = local.common_tags
}}
resource "aws_backup_plan" "{name}" {{
  name = "{slug}"
  rule {{
    rule_name         = "daily"
    target_vault_name = aws_backup_vault.{name}_vault.name
    schedule          = "cron(0 2 * * ? *)"
    lifecycle {{ delete_after = 30 }}
  }}
  tags = local.common_tags
}}
''',
    "ctrl-backup-policy": '''
resource "aws_backup_vault" "{name}_vault" {{
  name = "{slug}-vault"
  tags = local.common_tags
}}
resource "aws_backup_plan" "{name}" {{
  name = "{slug}"
  rule {{
    rule_name         = "daily"
    target_vault_name = aws_backup_vault.{name}_vault.name
    schedule          = "cron(0 2 * * ? *)"
    lifecycle {{ delete_after = 30 }}
  }}
  tags = local.common_tags
}}
''',
    "ctrl-rbac": '''
resource "aws_iam_role" "{name}" {{
  name = "{slug}"
  assume_role_policy = jsonencode({{
    Version = "2012-10-17"
    Statement = [{{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = {{ Service = "lambda.amazonaws.com" }}
    }}]
  }})
  tags = local.common_tags
}}
''',
    "ctrl-audit-log": '''
resource "aws_cloudtrail" "{name}" {{
  name                          = "{slug}"
  s3_bucket_name                = var.audit_log_bucket
  include_global_service_events = true
  is_multi_region_trail         = true
  enable_log_file_validation    = true
  tags = local.common_tags
}}
''',
    "ctrl-retention": '''
resource "aws_s3_bucket_lifecycle_configuration" "{name}" {{
  bucket = var.primary_bucket
  rule {{
    id     = "{slug}"
    status = "Enabled"
    expiration {{ days = var.retention_days }}
  }}
}}
''',
    "bnd": '''
resource "aws_security_group" "{name}" {{
  name        = "{slug}-sg"
  description = "{label} boundary"
  vpc_id      = var.vpc_id
  egress {{
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }}
  tags = local.common_tags
}}
''',
}

_TF_HEADER = '''\
# Data Infrastructure — Generated by ICDEV™ DDC Workflow
# Design: {design_id}
# Generated: {ts}
# DO NOT EDIT — regenerate via DDC Workflow

terraform {{
  required_providers {{
    aws = {{
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }}
  }}
  required_version = ">= 1.6.0"
}}

locals {{
  common_tags = {{
    ManagedBy      = "icdev-ddc"
    DesignId       = "{design_id}"
    Classification = "{classification}"
  }}
}}

'''

_TF_VARIABLES = '''\
# variables.tf — Generated by ICDEV™ DDC Workflow

variable "aws_region" {{
  type    = string
  default = "us-gov-west-1"
}}

variable "vpc_id" {{
  type        = string
  description = "VPC ID for data infrastructure"
}}

variable "private_subnet_ids" {{
  type        = list(string)
  description = "Private subnet IDs"
}}

variable "db_instance_class" {{
  type    = string
  default = "db.t3.medium"
}}

variable "db_password" {{
  type      = string
  sensitive = true
}}

variable "dw_password" {{
  type      = string
  sensitive = true
  default   = ""
}}

variable "cache_node_type" {{
  type    = string
  default = "cache.t3.medium"
}}

variable "artifacts_bucket" {{
  type        = string
  description = "S3 bucket for Glue scripts and Lambda ZIPs"
}}

variable "audit_log_bucket" {{
  type        = string
  description = "S3 bucket for CloudTrail audit logs"
  default     = ""
}}

variable "primary_bucket" {{
  type        = string
  description = "Primary S3 bucket for lifecycle policies"
  default     = ""
}}

variable "retention_days" {{
  type    = number
  default = 365
}}
'''

_TFVARS_EXAMPLE = '''\
# terraform.tfvars — Example values for {design_name}
# Copy to terraform.tfvars and fill in real values before running terraform apply

aws_region         = "us-gov-west-1"
vpc_id             = "vpc-xxxxxxxxxxxxxxxxx"
private_subnet_ids = ["subnet-xxxxxxxxxxxxxxxxx", "subnet-yyyyyyyyyyyyyyyyy"]
db_instance_class  = "db.t3.medium"
db_password        = "CHANGE_ME_secure_password_here"
cache_node_type    = "cache.t3.medium"
artifacts_bucket   = "icdev-artifacts-ACCOUNT_ID"
audit_log_bucket   = "icdev-audit-logs-ACCOUNT_ID"
primary_bucket     = ""
retention_days     = 365
'''


_VALIDATION_SCRIPT_HEADER = '''\
#!/usr/bin/env python3
"""Data Infrastructure Validation Script — Generated by ICDEV™ DDC Workflow
Design: {design_id}
Generated: {ts}

Run after `terraform apply` to verify all resources are provisioned correctly.
Usage:
  pip install boto3
  python validate_{uid}.py --region us-gov-west-1 [--profile <aws-profile>]
"""
import argparse
import sys
import boto3

DESIGN_ID = "{design_id}"


def check(label, ok, detail=""):
    status = "PASS" if ok else "FAIL"
    msg = "  [" + status + "] " + str(label)
    if detail:
        msg += " -- " + str(detail)
    print(msg)
    return ok


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--region", default="us-gov-west-1")
    parser.add_argument("--profile", default=None)
    args = parser.parse_args()

    session = boto3.Session(region_name=args.region, profile_name=args.profile)
    passed = failed = 0
    print("\\n=== DDC Validation: " + DESIGN_ID + " ===\\n")

'''

_VALIDATION_CHECKS = {
    "ent-file": '''\
    try:
        s3 = session.client("s3")
        s3.head_bucket(Bucket="{slug}")
        ok = check("{label} (S3)", True)
    except Exception as e:
        ok = check("{label} (S3)", False, str(e))
    passed += ok; failed += not ok
''',
    "ent-rds": '''\
    try:
        rds = session.client("rds")
        r = rds.describe_db_instances(DBInstanceIdentifier="{slug}")
        state = r["DBInstances"][0]["DBInstanceStatus"]
        ok = check("{label} (RDS)", state == "available", state)
    except Exception as e:
        ok = check("{label} (RDS)", False, str(e))
    passed += ok; failed += not ok
''',
    "ent-vector": '''\
    try:
        rds = session.client("rds")
        r = rds.describe_db_instances(DBInstanceIdentifier="{slug}")
        state = r["DBInstances"][0]["DBInstanceStatus"]
        ok = check("{label} (RDS/pgvector)", state == "available", state)
    except Exception as e:
        ok = check("{label} (RDS/pgvector)", False, str(e))
    passed += ok; failed += not ok
''',
    "ent-graph": '''\
    try:
        neptune = session.client("neptune")
        r = neptune.describe_db_clusters(DBClusterIdentifier="{slug}")
        state = r["DBClusters"][0]["Status"]
        ok = check("{label} (Neptune)", state == "available", state)
    except Exception as e:
        ok = check("{label} (Neptune)", False, str(e))
    passed += ok; failed += not ok
''',
    "ent-cache": '''\
    try:
        ec = session.client("elasticache")
        r = ec.describe_replication_groups(ReplicationGroupId="{slug}")
        state = r["ReplicationGroups"][0]["Status"]
        ok = check("{label} (ElastiCache)", state == "available", state)
    except Exception as e:
        ok = check("{label} (ElastiCache)", False, str(e))
    passed += ok; failed += not ok
''',
    "ent-redis": '''\
    try:
        ec = session.client("elasticache")
        r = ec.describe_replication_groups(ReplicationGroupId="{slug}")
        state = r["ReplicationGroups"][0]["Status"]
        ok = check("{label} (Redis)", state == "available", state)
    except Exception as e:
        ok = check("{label} (Redis)", False, str(e))
    passed += ok; failed += not ok
''',
    "ent-kafka": '''\
    try:
        kafka = session.client("kafka")
        clusters = kafka.list_clusters(ClusterNameFilter="{slug}")["ClusterInfoList"]
        ok = check("{label} (MSK)", bool(clusters), f"{{len(clusters)}} cluster(s) found")
    except Exception as e:
        ok = check("{label} (MSK)", False, str(e))
    passed += ok; failed += not ok
''',
    "ent-warehouse": '''\
    try:
        rs = session.client("redshift")
        r = rs.describe_clusters(ClusterIdentifier="{slug}")
        state = r["Clusters"][0]["ClusterStatus"]
        ok = check("{label} (Redshift)", state == "available", state)
    except Exception as e:
        ok = check("{label} (Redshift)", False, str(e))
    passed += ok; failed += not ok
''',
    "ctrl-kms": '''\
    try:
        kms = session.client("kms")
        aliases = kms.list_aliases()["Aliases"]
        found = any(a.get("AliasName") == "alias/{slug}" for a in aliases)
        ok = check("{label} (KMS)", found)
    except Exception as e:
        ok = check("{label} (KMS)", False, str(e))
    passed += ok; failed += not ok
''',
    "ctrl-backup": '''\
    try:
        bk = session.client("backup")
        plans = bk.list_backup_plans()["BackupPlansList"]
        found = any(p.get("BackupPlanName") == "{slug}" for p in plans)
        ok = check("{label} (Backup Plan)", found)
    except Exception as e:
        ok = check("{label} (Backup Plan)", False, str(e))
    passed += ok; failed += not ok
''',
}

_VALIDATION_SCRIPT_FOOTER = '''\
    print("\\n=== Result: " + str(passed) + " passed, " + str(failed) + " failed ===\\n")
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
'''


def _build_validation_script(design_id: str, nodes: list, uid: str) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines = [_VALIDATION_SCRIPT_HEADER.format(design_id=design_id, ts=ts, uid=uid)]
    for n in nodes:
        ntype = n.get("type") or ""
        label = n.get("label") or n.get("id", "")
        tmpl = _VALIDATION_CHECKS.get(ntype)
        if not tmpl:
            continue
        lines.append(tmpl.format(label=label, slug=_slug(label)))
    lines.append(_VALIDATION_SCRIPT_FOOTER)
    return "".join(lines)


def _build_wave_plan(design_id: str, nodes: list, ts: str) -> str:
    """YAML deployment plan — DDC equivalent of MDC Wave Plan."""
    db_nodes = [n for n in nodes if any(k in (n.get("type") or "") for k in ("ent-", "ent"))]
    ctrl_nodes = [n for n in nodes if (n.get("type") or "").startswith("ctrl-")]
    flow_nodes = [n for n in nodes if (n.get("type") or "").startswith("flow")]
    bnd_nodes = [n for n in nodes if (n.get("type") or "").startswith("bnd")]

    def phase_resources(ns):
        return [{"id": n.get("id", "")[:12], "label": n.get("label", ""), "type": n.get("type", "")} for n in ns]

    import yaml as _yaml
    plan = {
        "design_id": design_id,
        "generated": ts,
        "classification": "CUI",
        "deployment_phases": [
            {"phase": 1, "name": "Network Boundary", "description": "VPC security groups and boundaries",
             "resources": phase_resources(bnd_nodes) or [{"note": "no boundary nodes"}]},
            {"phase": 2, "name": "Security Controls", "description": "KMS keys, backup vaults, IAM roles",
             "resources": phase_resources(ctrl_nodes) or [{"note": "no control nodes"}]},
            {"phase": 3, "name": "Data Stores", "description": "Databases, caches, object stores",
             "resources": phase_resources(db_nodes) or [{"note": "no data store nodes"}]},
            {"phase": 4, "name": "Data Flows", "description": "ETL jobs, APIs, stream processors",
             "resources": phase_resources(flow_nodes) or [{"note": "no flow nodes"}]},
        ],
        "estimated_duration_minutes": 45 + len(nodes) * 5,
        "rollback_strategy": "terraform destroy -target per phase in reverse order",
    }
    return _yaml.dump(plan, default_flow_style=False, allow_unicode=True)


def _build_ansible_playbook(design_id: str, nodes: list, ts: str) -> str:
    """Ansible playbook for post-Terraform database configuration."""
    import re
    tasks = []
    for n in nodes:
        ntype = n.get("type") or ""
        label = n.get("label") or n.get("id", "")
        slug = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")[:40]
        if ntype in ("ent-vector",):
            tasks.append(f"    - name: Enable pgvector extension on {label}\n"
                         f"      community.postgresql.postgresql_ext:\n"
                         f"        db: postgres\n        name: vector\n        state: present\n"
                         f"      vars:\n        ansible_host: \"{{{{ rds_{slug}_endpoint }}}}\"\n")
        elif ntype in ("ent-rds", "ent-db"):
            tasks.append(f"    - name: Verify {label} connectivity\n"
                         f"      community.postgresql.postgresql_ping:\n"
                         f"      vars:\n        ansible_host: \"{{{{ rds_{slug}_endpoint }}}}\"\n")
        elif ntype in ("ent-graph",):
            tasks.append(f"    - name: Verify Neptune {label} endpoint\n"
                         f"      ansible.builtin.uri:\n"
                         f"        url: \"http://{{{{ neptune_{slug}_endpoint }}}}:8182/status\"\n"
                         f"        method: GET\n        status_code: 200\n")
    if not tasks:
        tasks.append("    - name: No post-provision tasks required\n      ansible.builtin.debug:\n        msg: All data stores are cloud-managed\n")
    task_block = "".join(tasks)
    return f"""---
# Ansible Playbook — DDC Post-Provisioning
# Design: {design_id}
# Generated: {ts}
# Run after: terraform apply

- name: DDC Data Infrastructure Post-Provisioning
  hosts: data_infra
  gather_facts: false
  vars_files:
    - terraform_outputs.yml

  tasks:
{task_block}
    - name: Validate all endpoints reachable
      ansible.builtin.debug:
        msg: "Post-provisioning configuration complete for design {design_id}"
"""


def _build_ansible_inventory(design_id: str, nodes: list, ts: str) -> str:
    """Ansible inventory populated from Terraform output variable references."""
    import re
    db_lines = ["[databases]"]
    cache_lines = ["[caches]"]
    graph_lines = ["[graph_stores]"]
    for n in nodes:
        ntype = n.get("type") or ""
        label = n.get("label") or n.get("id", "")
        slug = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")[:40]
        tf_slug = re.sub(r"[^a-z0-9_]", "_", label.lower()).strip("_")[:40]
        if ntype in ("ent-rds", "ent-vector", "ent-db", "ent-warehouse"):
            db_lines.append(f"{slug} ansible_host={{{{ rds_{tf_slug}_endpoint }}}} ansible_port=5432")
        elif ntype in ("ent-cache", "ent-redis"):
            cache_lines.append(f"{slug} ansible_host={{{{ elasticache_{tf_slug}_endpoint }}}} ansible_port=6379")
        elif ntype in ("ent-graph",):
            graph_lines.append(f"{slug} ansible_host={{{{ neptune_{tf_slug}_endpoint }}}} ansible_port=8182")

    sections = [
        "# Ansible Inventory — DDC Data Infrastructure",
        f"# Design: {design_id}",
        f"# Generated: {ts}",
        "# Populate from: terraform output -json > terraform_outputs.json",
        "",
    ]
    if len(db_lines) > 1:
        sections += db_lines + [""]
    if len(cache_lines) > 1:
        sections += cache_lines + [""]
    if len(graph_lines) > 1:
        sections += graph_lines + [""]
    sections += [
        "[data_infra:children]",
        "databases" if len(db_lines) > 1 else "",
        "caches" if len(cache_lines) > 1 else "",
        "graph_stores" if len(graph_lines) > 1 else "",
        "",
        "[data_infra:vars]",
        "ansible_connection=local",
        "ansible_python_interpreter=/usr/bin/python3",
    ]
    return "\n".join(s for s in sections if s is not None)


def _build_discovery_report(design_id: str, nodes: list, edges: list, ts: str) -> str:
    """Executive discovery report — DDC equivalent of MDC Discovery Report."""
    ts_fmt = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    by_type: dict = {}
    for n in nodes:
        t = n.get("type") or "unknown"
        by_type.setdefault(t, []).append(n.get("label") or n.get("id", ""))

    classifications = [n.get("classification") for n in nodes if n.get("classification")]
    classification_summary = ", ".join(sorted(set(classifications))) if classifications else "Not tagged"
    has_sensitive = any(t in by_type for t in ("ent-pii", "ent-phi", "ent-cui", "ent-classified", "ent-vector", "ent-file"))
    has_encryption = any(t in by_type for t in ("ctrl-encrypt", "ctrl-kms", "ctrl-cmk", "ctrl-fips", "ctrl-encryption"))

    inventory_rows = "\n".join(
        f"| `{ntype}` | {len(labels)} | {', '.join(labels[:4])}{'…' if len(labels) > 4 else ''} |"
        for ntype, labels in sorted(by_type.items())
    )

    findings = []
    if has_sensitive and not has_encryption:
        findings.append("⚠ **NIST SC-28 Gap** — Sensitive data nodes present with no encryption control")
    if not edges:
        findings.append("⚠ No data flow edges defined — lineage relationships are incomplete")
    orphans = [n for n in nodes if not any(e.get("source") == n["id"] or e.get("target") == n["id"] for e in edges)]
    if orphans:
        findings.append(f"⚠ {len(orphans)} orphaned node(s) with no lineage connections")
    if not findings:
        findings.append("✓ No critical findings — design is ready for IaC generation")

    findings_block = "\n".join(f"- {f}" for f in findings)

    return f"""# Data Infrastructure Discovery Report
**Design:** `{design_id}`
**Generated:** {ts_fmt}
**Classification:** CUI

---

## Executive Summary

This report documents the data infrastructure discovered from DDC canvas design
`{design_id}`. It provides an inventory of all data assets, controls, and flows
identified, along with a compliance assessment and readiness for IaC generation.

| Metric | Value |
|--------|-------|
| Total Nodes | {len(nodes)} |
| Total Edges (data flows) | {len(edges)} |
| Data Stores | {sum(len(v) for k, v in by_type.items() if k.startswith("ent-"))} |
| Control Nodes | {sum(len(v) for k, v in by_type.items() if k.startswith("ctrl-"))} |
| Flow/API Nodes | {sum(len(v) for k, v in by_type.items() if k.startswith("flow"))} |
| Classification Tags | {classification_summary} |
| Encryption Coverage | {"✓ Present" if has_encryption else "✗ Not found"} |

---

## Node Inventory

| Type | Count | Labels |
|------|-------|--------|
{inventory_rows}

---

## Data Flow Summary

{"**" + str(len(edges)) + " data flow edge(s) defined** connecting data stores, APIs, and controls." if edges else "**No data flow edges defined.** Add edges in the DDC canvas to capture lineage."}

---

## Key Findings

{findings_block}

---

## Compliance Posture

- **NIST 800-53 SC-28 (Protection of Information at Rest):** {"✓ Encryption control node present" if has_encryption else "✗ No encryption control — add ctrl-kms or ctrl-encryption node"}
- **Backup/Recovery:** {"✓ Backup control present" if any(t in by_type for t in ("ctrl-backup", "ctrl-snapshot", "ctrl-dr", "ctrl-backup-policy")) else "⚠ No backup control node detected"}
- **Audit Logging:** {"✓ Audit log control present" if "ctrl-audit-log" in by_type else "⚠ No ctrl-audit-log node — CloudTrail not provisioned"}

---

## Recommendation

Proceed with IaC generation. Review all findings above before applying Terraform.
Obtain DBA approval (Step 4 — DBA Approval gate) before routing production traffic.
"""


def _build_migration_runbook(design_id: str, nodes: list, ts: str) -> str:
    """Step-by-step deployment runbook — DDC equivalent of MDC Migration Runbook."""
    ts_fmt = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    db_nodes = [n for n in nodes if any(k in (n.get("type") or "") for k in ("ent-rds", "ent-db", "ent-vector", "ent-graph", "ent-warehouse", "ent-cache", "ent-redis", "ent-file", "ent-kafka"))]
    ctrl_nodes = [n for n in nodes if (n.get("type") or "").startswith("ctrl-")]

    resource_list = "\n".join(f"  - `{n.get('label', n.get('id',''))}` ({n.get('type','')})" for n in nodes)

    rollback_steps = []
    for n in db_nodes:
        label = n.get("label") or n.get("id", "")
        rollback_steps.append(f"  - `terraform destroy -target <resource>.{label}` if provisioning failed")

    rollback_block = "\n".join(rollback_steps) if rollback_steps else "  - `terraform destroy` for full teardown"

    return f"""# Data Infrastructure Migration Runbook
**Design:** `{design_id}`
**Generated:** {ts_fmt}
**Classification:** CUI

---

## Overview

This runbook documents the step-by-step procedure to deploy the data infrastructure
defined in DDC design `{design_id}`. Execute phases in order. Do not proceed to the
next phase until the current phase is verified complete.

**Resources in scope:** {len(nodes)} nodes ({len(db_nodes)} data stores, {len(ctrl_nodes)} controls)

{resource_list}

---

## Phase 1 — Pre-Deployment Preparation

**Estimated time:** 15 minutes

1. Confirm AWS credentials are active for target account:
   ```bash
   aws sts get-caller-identity
   ```
2. Verify VPC and subnet IDs exist in target region:
   ```bash
   aws ec2 describe-vpcs --region us-gov-west-1
   ```
3. Copy and populate tfvars:
   ```bash
   cp terraform.tfvars.example terraform.tfvars
   vi terraform.tfvars  # fill in vpc_id, subnet_ids, passwords
   ```
4. Initialize Terraform backend:
   ```bash
   terraform init -backend-config=backend.hcl
   ```
5. Run plan and review — zero unexpected destroys:
   ```bash
   terraform plan -var-file=terraform.tfvars -out=tfplan.bin
   ```
6. **Gate:** DBA reviews `tfplan.bin` output. Sign off before proceeding.

---

## Phase 2 — Security Controls Deployment

**Estimated time:** 10 minutes

Deploy KMS keys, backup vaults, and IAM roles first (other resources depend on them).

```bash
terraform apply -var-file=terraform.tfvars -target=aws_kms_key \\
  -target=aws_kms_alias -target=aws_backup_vault \\
  -target=aws_backup_plan -target=aws_iam_role
```

**Verify:**
- KMS keys status: `Enabled`
- Key rotation: `true`
- Backup vault created

---

## Phase 3 — Network Boundary Deployment

**Estimated time:** 5 minutes

```bash
terraform apply -var-file=terraform.tfvars -target=aws_security_group
```

**Verify:**
- Security groups created with correct VPC association
- Egress rules match policy

---

## Phase 4 — Data Store Provisioning

**Estimated time:** 20–40 minutes (RDS/Neptune take time to initialize)

```bash
terraform apply -var-file=terraform.tfvars
```

Monitor provisioning:
```bash
aws rds describe-db-instances --query 'DBInstances[*].[DBInstanceIdentifier,DBInstanceStatus]'
aws neptune describe-db-clusters --query 'DBClusters[*].[DBClusterIdentifier,Status]'
```

**Wait until all data stores report:** `available`

---

## Phase 5 — Post-Provisioning Configuration (Ansible)

**Estimated time:** 10 minutes

Export Terraform outputs for Ansible:
```bash
terraform output -json > terraform_outputs.json
python3 -c "
import json, yaml
out = json.load(open('terraform_outputs.json'))
yaml.dump({{k: v['value'] for k, v in out.items()}}, open('terraform_outputs.yml','w'))
"
```

Run Ansible playbook:
```bash
ansible-playbook -i ansible_inventory_*.ini ansible_playbook_*.yml
```

---

## Phase 6 — Validation

**Estimated time:** 10 minutes

Run automated validation:
```bash
python validate_*.py --region us-gov-west-1
```

Complete manual validation checklist (`validation_checklist_*.md`).

**Gate:** All automated checks PASS + DBA signs off checklist before traffic is routed.

---

## Rollback Procedure

If any phase fails, execute targeted destroy in reverse order:

{rollback_block}

Full rollback:
```bash
terraform destroy -var-file=terraform.tfvars
```

---

## Sign-off

| Role | Name | Signature | Date |
|------|------|-----------|------|
| DBA Lead | | | |
| Security Officer | | | |
| Program Manager | | | |
"""


def _build_validation_checklist(design_id: str, nodes: list, ts: str) -> str:
    """DBA validation checklist — MDC-equivalent Validation Checklist."""
    ts_fmt = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "# DBA Validation Checklist",
        f"**Design:** `{design_id}`  ",
        f"**Generated:** {ts_fmt}  ",
        "",
        "Complete each item after `terraform apply` succeeds.",
        "",
        "## Pre-Deployment",
        "- [ ] Terraform plan reviewed and approved by DBA",
        "- [ ] AWS credentials configured for target account",
        "- [ ] VPC and subnet IDs confirmed in `terraform.tfvars`",
        "- [ ] All sensitive variables set (db_password, dw_password)",
        "- [ ] Backup retention periods meet policy requirements",
        "",
        "## Infrastructure Checks",
    ]
    for n in nodes:
        ntype = n.get("type") or ""
        label = n.get("label") or n.get("id", "")
        if ntype in ("ent-rds", "ent-db"):
            lines.append(f"- [ ] **{label}** — RDS instance status: `available`")
            lines.append(f"- [ ] **{label}** — Automated backups enabled, retention ≥ 7 days")
            lines.append(f"- [ ] **{label}** — Storage encryption enabled (KMS)")
        elif ntype == "ent-vector":
            lines.append(f"- [ ] **{label}** — RDS/pgvector instance status: `available`")
            lines.append(f"- [ ] **{label}** — `vector` extension enabled in PostgreSQL")
            lines.append(f"- [ ] **{label}** — Parameter group `shared_preload_libraries` includes `pg_vector`")
        elif ntype == "ent-graph":
            lines.append(f"- [ ] **{label}** — Neptune cluster status: `available`")
            lines.append(f"- [ ] **{label}** — IAM auth enabled for Gremlin/SPARQL access")
        elif ntype in ("ent-cache", "ent-redis"):
            lines.append(f"- [ ] **{label}** — ElastiCache replication group status: `available`")
            lines.append(f"- [ ] **{label}** — In-transit and at-rest encryption confirmed")
        elif ntype == "ent-file":
            lines.append(f"- [ ] **{label}** — S3 bucket exists with correct policy")
            lines.append(f"- [ ] **{label}** — Versioning enabled, KMS SSE configured")
        elif ntype == "ent-kafka":
            lines.append(f"- [ ] **{label}** — MSK cluster status: `ACTIVE`")
            lines.append(f"- [ ] **{label}** — TLS broker encryption confirmed")
        elif ntype in ("ctrl-kms", "ctrl-encryption"):
            lines.append(f"- [ ] **{label}** — KMS key status: `Enabled`, rotation on")
        elif ntype in ("ctrl-backup", "ctrl-backup-policy"):
            lines.append(f"- [ ] **{label}** — Backup vault created, daily plan active")
    lines += [
        "",
        "## Post-Deployment",
        "- [ ] Run `python validate_*.py` — all checks PASS",
        "- [ ] Run Ansible playbook — no failed tasks",
        "- [ ] CloudTrail logging active for all data store access",
        "- [ ] Alert thresholds configured in CloudWatch",
        "- [ ] IaC state stored in remote backend (S3 + DynamoDB lock)",
        "- [ ] DBA sign-off obtained before production traffic routing",
        "",
        "**DBA Sign-off:** ___________________  **Date:** ___________",
    ]
    return "\n".join(lines)




def _slug(label: str) -> str:
    import re
    return re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")[:40]


def _tf_name(label: str) -> str:
    import re
    name = re.sub(r"[^a-z0-9_]", "_", label.lower()).strip("_")
    if name and name[0].isdigit():
        name = "r_" + name
    return name[:40] or "resource"


def generate_iac(project_id: str) -> dict:
    from tools.data.canvas_reader import load_designs
    designs = load_designs(project_id)

    _ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    tf_dir = _ARTIFACTS_DIR / "terraform"
    tf_dir.mkdir(parents=True, exist_ok=True)

    all_results = []
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    for did, d in designs.items():
        nodes = d["nodes"]
        edges = d["edges"]
        classification = next((n.get("classification") for n in nodes if n.get("classification")), "CUI")

        blocks = [_TF_HEADER.format(design_id=did, ts=ts, classification=classification)]
        resource_count = 0
        skipped_types = set()

        for n in nodes:
            ntype = n.get("type") or ""
            label = n.get("label") or n["id"]
            prefix = ntype.split("-")[0] if "-" in ntype else ntype
            tmpl = _TF_RESOURCES.get(ntype) or _TF_RESOURCES.get(prefix)
            if not tmpl:
                skipped_types.add(ntype)
                continue
            blocks.append(tmpl.format(
                name=_tf_name(label),
                label=label,
                slug=_slug(label),
                classification=classification,
            ))
            resource_count += 1

        uid = uuid.uuid4().hex[:8]
        main_tf = tf_dir / f"main_{uid}.tf"
        vars_tf = tf_dir / "variables.tf"
        tfvars = tf_dir / "terraform.tfvars.example"
        validate_py = _ARTIFACTS_DIR / f"validate_{uid}.py"
        wave_plan_yaml = _ARTIFACTS_DIR / f"data_plan_{uid}.yaml"
        ansible_pb = _ARTIFACTS_DIR / f"ansible_playbook_{uid}.yml"
        ansible_inv = _ARTIFACTS_DIR / f"ansible_inventory_{uid}.ini"
        runbook_md = _ARTIFACTS_DIR / f"migration_runbook_{uid}.md"
        checklist_md = _ARTIFACTS_DIR / f"validation_checklist_{uid}.md"
        discovery_md = _ARTIFACTS_DIR / f"discovery_report_{uid}.md"

        main_tf.write_text("".join(blocks), encoding="utf-8", newline="")
        vars_tf.write_text(_TF_VARIABLES, encoding="utf-8", newline="")
        tfvars.write_text(_TFVARS_EXAMPLE.format(design_name=did), encoding="utf-8", newline="")
        validate_py.write_text(_build_validation_script(did, nodes, uid), encoding="utf-8", newline="")
        wave_plan_yaml.write_text(_build_wave_plan(did, nodes, ts), encoding="utf-8", newline="")
        ansible_pb.write_text(_build_ansible_playbook(did, nodes, ts), encoding="utf-8", newline="")
        ansible_inv.write_text(_build_ansible_inventory(did, nodes, ts), encoding="utf-8", newline="")
        runbook_md.write_text(_build_migration_runbook(did, nodes, ts), encoding="utf-8", newline="")
        checklist_md.write_text(_build_validation_checklist(did, nodes, ts), encoding="utf-8", newline="")
        discovery_md.write_text(_build_discovery_report(did, nodes, edges, ts), encoding="utf-8", newline="")

        all_results.append({
            "design_id": did,
            "resource_count": resource_count,
            "skipped_types": sorted(skipped_types),
            "main_tf": main_tf.relative_to(_ROOT).as_posix(),
            "vars_tf": vars_tf.relative_to(_ROOT).as_posix(),
            "tfvars": tfvars.relative_to(_ROOT).as_posix(),
            "validate_py": validate_py.relative_to(_ROOT).as_posix(),
            "wave_plan_yaml": wave_plan_yaml.relative_to(_ROOT).as_posix(),
            "ansible_pb": ansible_pb.relative_to(_ROOT).as_posix(),
            "ansible_inv": ansible_inv.relative_to(_ROOT).as_posix(),
            "runbook_md": runbook_md.relative_to(_ROOT).as_posix(),
            "checklist_md": checklist_md.relative_to(_ROOT).as_posix(),
            "discovery_md": discovery_md.relative_to(_ROOT).as_posix(),
        })

    return {"designs": all_results, "project_id": project_id}


def build_report(result: dict) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    designs = result["designs"]
    lines = [
        "# Data IaC Generation Report",
        f"**Generated:** {ts}  ",
        f"**Project:** {result['project_id']}  ",
        f"**Designs:** {len(designs)}",
        "",
    ]

    if not designs:
        lines += [
            "## No Designs Found",
            "",
            "No canvas designs found. Save a DDC canvas design to generate Terraform IaC.",
        ]
        return "\n".join(lines)

    for d in designs:
        lines += [
            f"## Design `{d['design_id']}`",
            f"**Resources generated:** {d['resource_count']}",
        ]
        if d.get("skipped_types"):
            lines.append(f"**Unsupported types (no template):** {', '.join(d['skipped_types'])}")
        lines += [
            "",
            "**Artifacts:**",
            f"- `{d['main_tf']}` — Terraform resource definitions",
            f"- `{d['vars_tf']}` — Variable declarations",
            f"- `{d['tfvars']}` — Example variable values",
            f"- `{d['validate_py']}` — Post-deploy boto3 validation script",
            "",
            "**Apply with:**",
            "```bash",
            "cp terraform.tfvars.example terraform.tfvars  # fill in real values",
            "terraform init && terraform plan -var-file=terraform.tfvars",
            "terraform apply -var-file=terraform.tfvars",
            "```",
            "",
        ]

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Data IaC Generator")
    parser.add_argument("--project-id", default="default")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    try:
        result = generate_iac(args.project_id)
        report_md = build_report(result)

        _ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
        uid = uuid.uuid4().hex[:8]
        report_path = _ARTIFACTS_DIR / f"iac_report_{uid}.md"
        report_path.write_text(report_md, encoding="utf-8", newline="")

        artifacts = []
        for d in result["designs"]:
            artifacts += [
                {"name": "Terraform Main", "path": d["main_tf"], "type": "tf"},
                {"name": "Terraform Variables", "path": d["vars_tf"], "type": "tf"},
                {"name": "Terraform tfvars", "path": d["tfvars"], "type": "tf"},
                {"name": "Discovery Report", "path": d["discovery_md"], "type": "md"},
                {"name": "Wave Plan (YAML)", "path": d["wave_plan_yaml"], "type": "yaml"},
                {"name": "Ansible Playbook", "path": d["ansible_pb"], "type": "yml"},
                {"name": "Ansible Inventory", "path": d["ansible_inv"], "type": "ini"},
                {"name": "Migration Runbook", "path": d["runbook_md"], "type": "md"},
                {"name": "Validation Checklist", "path": d["checklist_md"], "type": "md"},
                {"name": "Data Validation Script", "path": d["validate_py"], "type": "py"},
            ]
        artifacts.append({"name": "IaC Generation Report", "path": report_path.relative_to(_ROOT).as_posix(), "type": "md"})

        output = {
            "status": "success",
            "designs_processed": len(result["designs"]),
            "total_resources": sum(d["resource_count"] for d in result["designs"]),
            "artifacts": artifacts,
        }
        print(json.dumps(output))
        sys.exit(0)
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}))
        sys.exit(1)


if __name__ == "__main__":
    main()
