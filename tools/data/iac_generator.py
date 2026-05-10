"""Data IaC Generator — DDC Workflow Step 3.

Reads data canvas nodes and generates Terraform HCL for each data
infrastructure component: S3, RDS, Kafka, Redis, Redshift, KMS, VPCs, etc.
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

# Terraform resource template per node type
_TF_TEMPLATES: dict[str, str] = {
    "ent-s3": '''
resource "aws_s3_bucket" "{name}" {{
  bucket = "{label_slug}"
  tags = {{
    Name           = "{label}"
    Classification = "{classification}"
    ManagedBy      = "icdev-ddc"
  }}
}}

resource "aws_s3_bucket_versioning" "{name}_versioning" {{
  bucket = aws_s3_bucket.{name}.id
  versioning_configuration {{ status = "Enabled" }}
}}

resource "aws_s3_bucket_server_side_encryption_configuration" "{name}_sse" {{
  bucket = aws_s3_bucket.{name}.id
  rule {{
    apply_server_side_encryption_by_default {{
      sse_algorithm = "aws:kms"
    }}
  }}
}}
''',
    "ent-rds": '''
resource "aws_db_instance" "{name}" {{
  identifier        = "{label_slug}"
  engine            = "postgres"
  engine_version    = "15.4"
  instance_class    = "db.t3.medium"
  allocated_storage = 20
  storage_encrypted = true
  username          = "dbadmin"
  password          = var.db_password
  skip_final_snapshot = false
  deletion_protection = true
  backup_retention_period = 7
  tags = {{
    Name           = "{label}"
    Classification = "{classification}"
    ManagedBy      = "icdev-ddc"
  }}
}}
''',
    "ent-db": '''
resource "aws_db_instance" "{name}" {{
  identifier        = "{label_slug}"
  engine            = "mysql"
  engine_version    = "8.0"
  instance_class    = "db.t3.medium"
  allocated_storage = 20
  storage_encrypted = true
  username          = "dbadmin"
  password          = var.db_password
  skip_final_snapshot = false
  backup_retention_period = 7
  tags = {{
    Name           = "{label}"
    Classification = "{classification}"
    ManagedBy      = "icdev-ddc"
  }}
}}
''',
    "ent-kafka": '''
resource "aws_msk_cluster" "{name}" {{
  cluster_name           = "{label_slug}"
  kafka_version          = "3.5.1"
  number_of_broker_nodes = 3
  broker_node_group_info {{
    instance_type   = "kafka.m5.large"
    client_subnets  = var.private_subnet_ids
    storage_info {{
      ebs_storage_info {{ volume_size = 100 }}
    }}
  }}
  encryption_info {{
    encryption_in_transit {{ client_broker = "TLS" }}
  }}
  tags = {{
    Name           = "{label}"
    Classification = "{classification}"
    ManagedBy      = "icdev-ddc"
  }}
}}
''',
    "ent-redis": '''
resource "aws_elasticache_replication_group" "{name}" {{
  replication_group_id = "{label_slug}"
  description          = "{label} cache cluster"
  node_type            = "cache.t3.medium"
  num_cache_clusters   = 2
  at_rest_encryption_enabled = true
  transit_encryption_enabled = true
  tags = {{
    Name           = "{label}"
    Classification = "{classification}"
    ManagedBy      = "icdev-ddc"
  }}
}}
''',
    "ent-elasticsearch": '''
resource "aws_opensearch_domain" "{name}" {{
  domain_name    = "{label_slug}"
  engine_version = "OpenSearch_2.11"
  cluster_config {{
    instance_type  = "m5.large.search"
    instance_count = 2
  }}
  encrypt_at_rest {{ enabled = true }}
  node_to_node_encryption {{ enabled = true }}
  tags = {{
    Name           = "{label}"
    Classification = "{classification}"
    ManagedBy      = "icdev-ddc"
  }}
}}
''',
    "ent-warehouse": '''
resource "aws_redshift_cluster" "{name}" {{
  cluster_identifier = "{label_slug}"
  database_name      = "datawarehouse"
  master_username    = "dwadmin"
  master_password    = var.dw_password
  node_type          = "dc2.large"
  cluster_type       = "multi-node"
  number_of_nodes    = 2
  encrypted          = true
  tags = {{
    Name           = "{label}"
    Classification = "{classification}"
    ManagedBy      = "icdev-ddc"
  }}
}}
''',
    "ctrl-kms": '''
resource "aws_kms_key" "{name}" {{
  description             = "{label} encryption key"
  deletion_window_in_days = 30
  enable_key_rotation     = true
  tags = {{
    Name           = "{label}"
    Classification = "{classification}"
    ManagedBy      = "icdev-ddc"
  }}
}}

resource "aws_kms_alias" "{name}_alias" {{
  name          = "alias/{label_slug}"
  target_key_id = aws_kms_key.{name}.key_id
}}
''',
    "ctrl-backup": '''
resource "aws_backup_plan" "{name}" {{
  name = "{label_slug}"
  rule {{
    rule_name         = "daily-backup"
    target_vault_name = aws_backup_vault.{name}_vault.name
    schedule          = "cron(0 2 * * ? *)"
    lifecycle {{ delete_after = 30 }}
  }}
  tags = {{
    Name           = "{label}"
    Classification = "{classification}"
    ManagedBy      = "icdev-ddc"
  }}
}}

resource "aws_backup_vault" "{name}_vault" {{
  name = "{label_slug}-vault"
}}
''',
    "flow": '''
resource "aws_glue_job" "{name}" {{
  name     = "{label_slug}"
  role_arn = var.glue_role_arn
  command {{
    name            = "glueetl"
    script_location = "s3://icdev-glue-scripts/{label_slug}.py"
    python_version  = "3"
  }}
  default_arguments = {{
    "--job-language"        = "python"
    "--enable-metrics"      = ""
    "--enable-continuous-cloudwatch-log" = "true"
  }}
  tags = {{
    Name           = "{label}"
    Classification = "{classification}"
    ManagedBy      = "icdev-ddc"
  }}
}}
''',
    "bnd": '''
resource "aws_security_group" "{name}" {{
  name        = "{label_slug}-sg"
  description = "{label} boundary security group"
  vpc_id      = var.vpc_id
  egress {{
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }}
  tags = {{
    Name           = "{label}"
    Classification = "{classification}"
    ManagedBy      = "icdev-ddc"
  }}
}}
''',
}

_TF_HEADER = '''# Data Infrastructure — Generated by ICDEV™ DDC Workflow
# Canvas: {canvas_id}
# Generated: {ts}
# Classification: {classification}
# DO NOT EDIT — re-run DDC workflow to regenerate

terraform {{
  required_providers {{
    aws = {{
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }}
  }}
  required_version = ">= 1.5.0"
}}

variable "db_password" {{
  type      = string
  sensitive = true
}}

variable "dw_password" {{
  type      = string
  sensitive = true
}}

variable "vpc_id" {{
  type = string
}}

variable "private_subnet_ids" {{
  type = list(string)
}}

variable "glue_role_arn" {{
  type = string
}}

'''


def _slug(label: str) -> str:
    import re
    return re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")[:40]


def _tf_name(label: str) -> str:
    import re
    name = re.sub(r"[^a-z0-9_]", "_", label.lower())
    if name and name[0].isdigit():
        name = "r_" + name
    return name[:40]


def _get_conn():
    from tools.db.storage import get_connection
    return get_connection()


def generate_iac(project_id: str) -> dict:
    conn = _get_conn()
    try:
        nodes = conn.execute(
            "SELECT design_id, node_id, node_type, label, classification FROM data_nodes ORDER BY design_id"
        ).fetchall()
        kg_nodes = conn.execute(
            "SELECT canvas, design_id, node_id, node_type, label FROM canvas_kg_nodes WHERE canvas='ddc'"
        ).fetchall()
    finally:
        conn.close()

    designs: dict = {}
    for row in (nodes or []):
        did = row[0]
        designs.setdefault(did, [])
        designs[did].append({"id": row[1], "type": row[2], "label": row[3], "classification": row[4]})
    if not nodes and kg_nodes:
        for row in (kg_nodes or []):
            did = row[1]
            designs.setdefault(did, [])
            designs[did].append({"id": row[2], "type": row[3], "label": row[4], "classification": "CUI"})

    artifacts_dir = _ROOT / "data" / "studio_artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    generated = []
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    for did, node_list in designs.items():
        classification = next((n["classification"] or "CUI" for n in node_list), "CUI")
        blocks = [_TF_HEADER.format(canvas_id=did, ts=ts, classification=classification)]
        resource_count = 0

        for n in node_list:
            ntype = n.get("type") or ""
            label = n.get("label") or n["id"]
            clf = n.get("classification") or "CUI"
            prefix = ntype.split("-")[0] if "-" in ntype else ntype
            tmpl = _TF_TEMPLATES.get(ntype) or _TF_TEMPLATES.get(prefix)
            if not tmpl:
                continue
            blocks.append(tmpl.format(
                name=_tf_name(label),
                label=label,
                label_slug=_slug(label),
                classification=clf,
            ))
            resource_count += 1

        fname = f"data_iac_{uuid.uuid4().hex[:8]}.tf"
        fpath = artifacts_dir / fname
        fpath.write_text("".join(blocks), encoding="utf-8")
        generated.append({
            "design_id": did,
            "resource_count": resource_count,
            "path": f"data/studio_artifacts/{fname}",
        })

    return {"designs": generated, "project_id": project_id}


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
            "No data canvas designs found. Save a DDC canvas design to generate Terraform IaC.",
            "",
            "## Supported Resource Types",
            "",
            "| Canvas Node Type | Terraform Resource |",
            "|-----------------|-------------------|",
            "| `ent-s3` | `aws_s3_bucket` + versioning + SSE |",
            "| `ent-rds` | `aws_db_instance` (PostgreSQL) |",
            "| `ent-db` | `aws_db_instance` (MySQL) |",
            "| `ent-kafka` | `aws_msk_cluster` |",
            "| `ent-redis` | `aws_elasticache_replication_group` |",
            "| `ent-elasticsearch` | `aws_opensearch_domain` |",
            "| `ent-warehouse` | `aws_redshift_cluster` |",
            "| `ctrl-kms` | `aws_kms_key` + alias |",
            "| `ctrl-backup` | `aws_backup_plan` + vault |",
            "| `flow` | `aws_glue_job` |",
            "| `bnd` | `aws_security_group` |",
        ]
        return "\n".join(lines)

    for d in designs:
        lines += [
            f"## Design `{d['design_id']}`",
            f"**Resources generated:** {d['resource_count']}  ",
            f"**Artifact:** `{d['path']}`",
            "",
            "IaC generated successfully. Review and apply with:",
            "```bash",
            "terraform init",
            "terraform plan -var-file=terraform.tfvars",
            "terraform apply",
            "```",
            "",
        ]

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Data IaC Generator")
    parser.add_argument("--project-id", default="default")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    try:
        result = generate_iac(args.project_id)
        report_md = build_report(result)

        artifacts_dir = _ROOT / "data" / "studio_artifacts"
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        fname = f"data_iac_report_{uuid.uuid4().hex[:8]}.md"
        fpath = artifacts_dir / fname
        fpath.write_text(report_md, encoding="utf-8")

        output = {
            "status": "success",
            "designs_processed": len(result["designs"]),
            "total_resources": sum(d["resource_count"] for d in result["designs"]),
            "artifacts": [
                {"name": "Data IaC (Terraform)", "path": d["path"], "type": "tf"}
                for d in result["designs"]
            ] + [
                {"name": "IaC Generation Report", "path": f"data/studio_artifacts/{fname}", "type": "md"}
            ],
        }
        print(json.dumps(output))
        sys.exit(0)
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}))
        sys.exit(1)


if __name__ == "__main__":
    main()
