#!/usr/bin/env python3
# CUI // SP-CTI
"""Generate Terraform configurations for AWS GovCloud deployments.
Produces provider.tf, variables.tf, outputs.tf, main.tf, and optional modules
for RDS, ECR, and VPC — all with CUI header comments."""

import argparse
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"

CUI_HEADER = """# //CUI
# CONTROLLED UNCLASSIFIED INFORMATION
# Authorized for: Internal project use only
# Generated: {timestamp}
# Generator: ICDev Terraform Generator
# //CUI
"""

# ---------------------------------------------------------------------------
# Jinja2 fallback: try import, else use str.format
# ---------------------------------------------------------------------------
try:
    from jinja2 import Template as Jinja2Template

    def _render(template_str: str, ctx: dict) -> str:
        return Jinja2Template(template_str).render(**ctx)

except ImportError:

    def _render(template_str: str, ctx: dict) -> str:
        """Minimal fallback — replaces {{ var }} with ctx[var]."""
        result = template_str
        for key, val in ctx.items():
            result = result.replace("{{ " + key + " }}", str(val))
            result = result.replace("{{" + key + "}}", str(val))
        return result


def _cui_header() -> str:
    return CUI_HEADER.format(timestamp=datetime.now(timezone.utc).isoformat())


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Base infrastructure
# ---------------------------------------------------------------------------
PROVIDER_TF = """\
{{ cui_header }}
terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  backend "s3" {
    bucket         = "{{ project_name }}-tf-state"
    key            = "{{ environment }}/terraform.tfstate"
    region         = "us-gov-west-1"
    encrypt        = true
    dynamodb_table = "{{ project_name }}-tf-locks"
  }
}

provider "aws" {
  region = "us-gov-west-1"

  default_tags {
    tags = {
      Project        = "{{ project_name }}"
      Environment    = "{{ environment }}"
      Classification = "CUI"
      ManagedBy      = "Terraform"
    }
  }
}
"""

VARIABLES_TF = """\
{{ cui_header }}
variable "project_name" {
  description = "Project identifier"
  type        = string
  default     = "{{ project_name }}"
}

variable "environment" {
  description = "Deployment environment (dev, staging, prod)"
  type        = string
  default     = "{{ environment }}"

  validation {
    condition     = contains(["dev", "staging", "prod"], var.environment)
    error_message = "Environment must be dev, staging, or prod."
  }
}

variable "vpc_cidr" {
  description = "VPC CIDR block"
  type        = string
  default     = "10.0.0.0/16"
}

variable "private_subnet_cidrs" {
  description = "Private subnet CIDRs"
  type        = list(string)
  default     = ["10.0.1.0/24", "10.0.2.0/24", "10.0.3.0/24"]
}

variable "db_instance_class" {
  description = "RDS instance class"
  type        = string
  default     = "db.t3.medium"
}

variable "db_name" {
  description = "Database name"
  type        = string
  default     = "{{ db_name }}"
}

variable "db_username" {
  description = "Database master username"
  type        = string
  default     = "dbadmin"
  sensitive   = true
}

variable "db_password" {
  description = "Database master password"
  type        = string
  sensitive   = true
}

variable "common_tags" {
  description = "Common tags for all resources"
  type        = map(string)
  default     = {}
}
"""

OUTPUTS_TF = """\
{{ cui_header }}
output "vpc_id" {
  description = "VPC ID"
  value       = module.vpc.vpc_id
}

output "private_subnet_ids" {
  description = "Private subnet IDs"
  value       = module.vpc.private_subnet_ids
}

output "rds_endpoint" {
  description = "RDS endpoint"
  value       = module.rds.endpoint
  sensitive   = true
}

output "ecr_repository_url" {
  description = "ECR repository URL"
  value       = module.ecr.repository_url
}
"""

MAIN_TF = """\
{{ cui_header }}
module "vpc" {
  source = "./modules/vpc"

  project_name         = var.project_name
  environment          = var.environment
  vpc_cidr             = var.vpc_cidr
  private_subnet_cidrs = var.private_subnet_cidrs
  common_tags          = var.common_tags
}

module "rds" {
  source = "./modules/rds"

  project_name    = var.project_name
  environment     = var.environment
  vpc_id          = module.vpc.vpc_id
  subnet_ids      = module.vpc.private_subnet_ids
  instance_class  = var.db_instance_class
  db_name         = var.db_name
  db_username     = var.db_username
  db_password     = var.db_password
  common_tags     = var.common_tags
}

module "ecr" {
  source = "./modules/ecr"

  project_name = var.project_name
  environment  = var.environment
  common_tags  = var.common_tags
}
"""


def generate_base(project_path: str, project_config: dict = None) -> list:
    """Generate provider.tf, variables.tf, outputs.tf, main.tf."""
    config = project_config or {}
    project_name = config.get("project_name", "icdev-project")
    environment = config.get("environment", "dev")
    db_name = config.get("db_name", "appdb")

    tf_dir = Path(project_path) / "terraform"
    ctx = {
        "cui_header": _cui_header(),
        "project_name": project_name,
        "environment": environment,
        "db_name": db_name,
    }

    files = []
    for name, template in [
        ("provider.tf", PROVIDER_TF),
        ("variables.tf", VARIABLES_TF),
        ("outputs.tf", OUTPUTS_TF),
        ("main.tf", MAIN_TF),
    ]:
        p = _write(tf_dir / name, _render(template, ctx))
        files.append(str(p))

    return files


# ---------------------------------------------------------------------------
# RDS module
# ---------------------------------------------------------------------------
RDS_MAIN = """\
{{ cui_header }}
resource "aws_db_subnet_group" "this" {
  name       = "${{var.project_name}}-${{var.environment}}-db-subnet"
  subnet_ids = var.subnet_ids

  tags = merge(var.common_tags, {
    Name = "${{var.project_name}}-${{var.environment}}-db-subnet"
  })
}

resource "aws_security_group" "rds" {
  name_prefix = "${{var.project_name}}-rds-"
  vpc_id      = var.vpc_id

  ingress {
    description = "PostgreSQL from private subnets"
    from_port   = 5432
    to_port     = 5432
    protocol    = "tcp"
    cidr_blocks = var.allowed_cidrs
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(var.common_tags, {
    Name = "${{var.project_name}}-rds-sg"
  })
}

resource "aws_db_instance" "this" {
  identifier     = "${{var.project_name}}-${{var.environment}}"
  engine         = "postgres"
  engine_version = "15.4"
  instance_class = var.instance_class

  db_name  = var.db_name
  username = var.db_username
  password = var.db_password

  allocated_storage     = 20
  max_allocated_storage = 100
  storage_encrypted     = true
  storage_type          = "gp3"

  db_subnet_group_name   = aws_db_subnet_group.this.name
  vpc_security_group_ids = [aws_security_group.rds.id]

  multi_az                = var.environment == "prod" ? true : false
  backup_retention_period = var.environment == "prod" ? 35 : 7
  deletion_protection     = var.environment == "prod" ? true : false

  enabled_cloudwatch_logs_exports = ["postgresql", "upgrade"]
  monitoring_interval             = 60
  performance_insights_enabled    = true

  skip_final_snapshot       = var.environment != "prod"
  final_snapshot_identifier = "${{var.project_name}}-${{var.environment}}-final"

  tags = merge(var.common_tags, {
    Name           = "${{var.project_name}}-${{var.environment}}-rds"
    Classification = "CUI"
    DataSensitivity = "High"
  })
}
"""

RDS_VARIABLES = """\
{{ cui_header }}
variable "project_name" { type = string }
variable "environment" { type = string }
variable "vpc_id" { type = string }
variable "subnet_ids" { type = list(string) }
variable "instance_class" { type = string; default = "db.t3.medium" }
variable "db_name" { type = string }
variable "db_username" { type = string; sensitive = true }
variable "db_password" { type = string; sensitive = true }
variable "allowed_cidrs" { type = list(string); default = ["10.0.0.0/16"] }
variable "common_tags" { type = map(string); default = {} }
"""

RDS_OUTPUTS = """\
{{ cui_header }}
output "endpoint" {
  value     = aws_db_instance.this.endpoint
  sensitive = true
}

output "db_name" {
  value = aws_db_instance.this.db_name
}

output "security_group_id" {
  value = aws_security_group.rds.id
}
"""


def generate_rds(project_path: str, db_config: dict = None) -> list:
    """Generate RDS PostgreSQL Terraform module."""
    tf_dir = Path(project_path) / "terraform" / "modules" / "rds"
    ctx = {"cui_header": _cui_header()}

    files = []
    for name, template in [
        ("main.tf", RDS_MAIN),
        ("variables.tf", RDS_VARIABLES),
        ("outputs.tf", RDS_OUTPUTS),
    ]:
        p = _write(tf_dir / name, _render(template, ctx))
        files.append(str(p))
    return files


# ---------------------------------------------------------------------------
# ECR module
# ---------------------------------------------------------------------------
ECR_MAIN = """\
{{ cui_header }}
resource "aws_ecr_repository" "this" {
  name                 = "${{var.project_name}}-${{var.environment}}"
  image_tag_mutability = "IMMUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "AES256"
  }

  tags = merge(var.common_tags, {
    Name           = "${{var.project_name}}-${{var.environment}}-ecr"
    Classification = "CUI"
  })
}

resource "aws_ecr_lifecycle_policy" "this" {
  repository = aws_ecr_repository.this.name

  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Keep last 30 tagged images"
        selection = {
          tagStatus   = "tagged"
          tagPrefixList = ["v"]
          countType   = "imageCountMoreThan"
          countNumber = 30
        }
        action = {
          type = "expire"
        }
      },
      {
        rulePriority = 2
        description  = "Remove untagged images after 7 days"
        selection = {
          tagStatus   = "untagged"
          countType   = "sinceImagePushed"
          countUnit   = "days"
          countNumber = 7
        }
        action = {
          type = "expire"
        }
      }
    ]
  })
}
"""

ECR_VARIABLES = """\
{{ cui_header }}
variable "project_name" { type = string }
variable "environment" { type = string }
variable "common_tags" { type = map(string); default = {} }
"""

ECR_OUTPUTS = """\
{{ cui_header }}
output "repository_url" {
  value = aws_ecr_repository.this.repository_url
}

output "repository_arn" {
  value = aws_ecr_repository.this.arn
}
"""


def generate_ecr(project_path: str) -> list:
    """Generate ECR Terraform module."""
    tf_dir = Path(project_path) / "terraform" / "modules" / "ecr"
    ctx = {"cui_header": _cui_header()}

    files = []
    for name, template in [
        ("main.tf", ECR_MAIN),
        ("variables.tf", ECR_VARIABLES),
        ("outputs.tf", ECR_OUTPUTS),
    ]:
        p = _write(tf_dir / name, _render(template, ctx))
        files.append(str(p))
    return files


# ---------------------------------------------------------------------------
# VPC module
# ---------------------------------------------------------------------------
VPC_MAIN = """\
{{ cui_header }}
data "aws_availability_zones" "available" {
  state = "available"
}

resource "aws_vpc" "this" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = merge(var.common_tags, {
    Name           = "${{var.project_name}}-${{var.environment}}-vpc"
    Classification = "CUI"
  })
}

resource "aws_subnet" "private" {
  count = length(var.private_subnet_cidrs)

  vpc_id            = aws_vpc.this.id
  cidr_block        = var.private_subnet_cidrs[count.index]
  availability_zone = data.aws_availability_zones.available.names[count.index % length(data.aws_availability_zones.available.names)]

  map_public_ip_on_launch = false

  tags = merge(var.common_tags, {
    Name           = "${{var.project_name}}-${{var.environment}}-private-${{count.index + 1}}"
    Tier           = "Private"
    Classification = "CUI"
  })
}

resource "aws_route_table" "private" {
  vpc_id = aws_vpc.this.id

  tags = merge(var.common_tags, {
    Name = "${{var.project_name}}-${{var.environment}}-private-rt"
  })
}

resource "aws_route_table_association" "private" {
  count = length(aws_subnet.private)

  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private.id
}

resource "aws_flow_log" "this" {
  vpc_id               = aws_vpc.this.id
  traffic_type         = "ALL"
  log_destination_type = "cloud-watch-logs"
  log_destination      = aws_cloudwatch_log_group.flow_log.arn
  iam_role_arn         = aws_iam_role.flow_log.arn
}

resource "aws_cloudwatch_log_group" "flow_log" {
  name              = "/aws/vpc/${{var.project_name}}-${{var.environment}}/flow-logs"
  retention_in_days = 365

  tags = merge(var.common_tags, {
    Classification = "CUI"
  })
}

resource "aws_iam_role" "flow_log" {
  name = "${{var.project_name}}-${{var.environment}}-flow-log-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action    = "sts:AssumeRole"
        Effect    = "Allow"
        Principal = { Service = "vpc-flow-logs.amazonaws.com" }
      }
    ]
  })
}

resource "aws_iam_role_policy" "flow_log" {
  name = "${{var.project_name}}-${{var.environment}}-flow-log-policy"
  role = aws_iam_role.flow_log.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents",
          "logs:DescribeLogGroups",
          "logs:DescribeLogStreams"
        ]
        Effect   = "Allow"
        Resource = "*"
      }
    ]
  })
}

resource "aws_default_security_group" "default" {
  vpc_id = aws_vpc.this.id

  # No rules — effectively denies all traffic on the default SG
  tags = merge(var.common_tags, {
    Name = "${{var.project_name}}-default-sg-deny-all"
  })
}
"""

VPC_VARIABLES = """\
{{ cui_header }}
variable "project_name" { type = string }
variable "environment" { type = string }
variable "vpc_cidr" { type = string; default = "10.0.0.0/16" }
variable "private_subnet_cidrs" { type = list(string); default = ["10.0.1.0/24", "10.0.2.0/24", "10.0.3.0/24"] }
variable "common_tags" { type = map(string); default = {} }
"""

VPC_OUTPUTS = """\
{{ cui_header }}
output "vpc_id" {
  value = aws_vpc.this.id
}

output "private_subnet_ids" {
  value = aws_subnet.private[*].id
}

output "vpc_cidr" {
  value = aws_vpc.this.cidr_block
}
"""


def generate_vpc(project_path: str) -> list:
    """Generate VPC Terraform module with private subnets."""
    tf_dir = Path(project_path) / "terraform" / "modules" / "vpc"
    ctx = {"cui_header": _cui_header()}

    files = []
    for name, template in [
        ("main.tf", VPC_MAIN),
        ("variables.tf", VPC_VARIABLES),
        ("outputs.tf", VPC_OUTPUTS),
    ]:
        p = _write(tf_dir / name, _render(template, ctx))
        files.append(str(p))
    return files


# ---------------------------------------------------------------------------
# Bedrock IAM module (Phase 19 — agentic LLM access)
# ---------------------------------------------------------------------------
BEDROCK_IAM_MAIN = """\
{{ cui_header }}
# Bedrock IAM policy for agent LLM inference access
# Restricts to specific models and regions (GovCloud)

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

resource "aws_iam_role" "bedrock_agent" {
  name = "${{var.project_name}}-${{var.environment}}-bedrock-agent"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action    = "sts:AssumeRole"
        Effect    = "Allow"
        Principal = {
          Service = [
            "ecs-tasks.amazonaws.com",
            "eks.amazonaws.com"
          ]
        }
        Condition = {
          StringEquals = {
            "aws:RequestedRegion" = "us-gov-west-1"
          }
        }
      }
    ]
  })

  tags = merge(var.common_tags, {
    Name           = "${{var.project_name}}-bedrock-agent-role"
    Classification = "CUI"
    Purpose        = "Agent LLM inference via Bedrock"
  })
}

resource "aws_iam_policy" "bedrock_invoke" {
  name        = "${{var.project_name}}-${{var.environment}}-bedrock-invoke"
  description = "Allow agents to invoke Bedrock models for LLM inference"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "BedrockInvokeModel"
        Effect = "Allow"
        Action = [
          "bedrock:InvokeModel",
          "bedrock:InvokeModelWithResponseStream"
        ]
        Resource = [
          "arn:aws-us-gov:bedrock:${{data.aws_region.current.name}}::foundation-model/anthropic.claude-*",
          "arn:aws-us-gov:bedrock:${{data.aws_region.current.name}}::foundation-model/amazon.titan-*"
        ]
      },
      {
        Sid    = "BedrockListModels"
        Effect = "Allow"
        Action = [
          "bedrock:ListFoundationModels",
          "bedrock:GetFoundationModel"
        ]
        Resource = "*"
      },
      {
        Sid    = "CloudWatchLogging"
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Resource = "arn:aws-us-gov:logs:${{data.aws_region.current.name}}:${{data.aws_caller_identity.current.account_id}}:log-group:/icdev/*"
      }
    ]
  })

  tags = merge(var.common_tags, {
    Classification = "CUI"
  })
}

resource "aws_iam_role_policy_attachment" "bedrock_invoke" {
  role       = aws_iam_role.bedrock_agent.name
  policy_arn = aws_iam_policy.bedrock_invoke.arn
}
"""

BEDROCK_IAM_VARIABLES = """\
{{ cui_header }}
variable "project_name" { type = string }
variable "environment" { type = string }
variable "common_tags" { type = map(string); default = {} }
"""

BEDROCK_IAM_OUTPUTS = """\
{{ cui_header }}
output "bedrock_agent_role_arn" {
  description = "ARN of the Bedrock agent IAM role"
  value       = aws_iam_role.bedrock_agent.arn
}

output "bedrock_invoke_policy_arn" {
  description = "ARN of the Bedrock invoke policy"
  value       = aws_iam_policy.bedrock_invoke.arn
}
"""


def generate_bedrock_iam(project_path: str, config: dict = None) -> list:
    """Generate Bedrock IAM policy for agent LLM access.

    Creates IAM role and policy that allows ICDEV agents to invoke
    Amazon Bedrock models (Claude, Titan) in GovCloud for LLM inference.
    Follows least-privilege principle with region-locked access.

    Args:
        project_path: Target project directory.
        config: Optional project configuration dict.

    Returns:
        List of generated file paths.
    """
    tf_dir = Path(project_path) / "terraform" / "modules" / "bedrock_iam"
    ctx = {"cui_header": _cui_header()}

    files = []
    for name, template in [
        ("main.tf", BEDROCK_IAM_MAIN),
        ("variables.tf", BEDROCK_IAM_VARIABLES),
        ("outputs.tf", BEDROCK_IAM_OUTPUTS),
    ]:
        p = _write(tf_dir / name, _render(template, ctx))
        files.append(str(p))
    return files


# ---------------------------------------------------------------------------
# Agent networking module (Phase 19 — mTLS, FIPS endpoints)
# ---------------------------------------------------------------------------
AGENT_NETWORKING_MAIN = """\
{{ cui_header }}
# Agent networking — mTLS between agents, FIPS 140-2 endpoints
# Private subnets only, no public internet access

resource "aws_security_group" "agent_mesh" {
  name_prefix = "${{var.project_name}}-agent-mesh-"
  vpc_id      = var.vpc_id
  description = "Security group for agent-to-agent mTLS communication"

  # Allow mTLS between agents (ports 8443-8452)
  ingress {
    description = "Agent mTLS communication"
    from_port   = 8443
    to_port     = 8452
    protocol    = "tcp"
    self        = true
  }

  # Health check port
  ingress {
    description = "Agent health checks"
    from_port   = 8080
    to_port     = 8080
    protocol    = "tcp"
    self        = true
  }

  # DNS resolution
  egress {
    description = "DNS resolution"
    from_port   = 53
    to_port     = 53
    protocol    = "udp"
    cidr_blocks = ["10.0.0.0/16"]
  }

  egress {
    description = "DNS resolution TCP"
    from_port   = 53
    to_port     = 53
    protocol    = "tcp"
    cidr_blocks = ["10.0.0.0/16"]
  }

  # Bedrock FIPS endpoint (HTTPS)
  egress {
    description = "Bedrock FIPS endpoint"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = var.fips_endpoint_cidrs
  }

  # Agent-to-agent within mesh
  egress {
    description = "Agent mesh egress"
    from_port   = 8443
    to_port     = 8452
    protocol    = "tcp"
    self        = true
  }

  tags = merge(var.common_tags, {
    Name           = "${{var.project_name}}-agent-mesh-sg"
    Classification = "CUI"
    Purpose        = "Agent A2A mTLS communication"
  })
}

resource "aws_vpc_endpoint" "bedrock_fips" {
  vpc_id              = var.vpc_id
  service_name        = "com.amazonaws.us-gov-west-1.bedrock-runtime"
  vpc_endpoint_type   = "Interface"
  private_dns_enabled = true
  subnet_ids          = var.private_subnet_ids

  security_group_ids = [aws_security_group.agent_mesh.id]

  tags = merge(var.common_tags, {
    Name           = "${{var.project_name}}-bedrock-fips-endpoint"
    Classification = "CUI"
    FIPS           = "true"
  })
}

resource "aws_vpc_endpoint" "secrets_manager" {
  vpc_id              = var.vpc_id
  service_name        = "com.amazonaws.us-gov-west-1.secretsmanager"
  vpc_endpoint_type   = "Interface"
  private_dns_enabled = true
  subnet_ids          = var.private_subnet_ids

  security_group_ids = [aws_security_group.agent_mesh.id]

  tags = merge(var.common_tags, {
    Name           = "${{var.project_name}}-secrets-manager-endpoint"
    Classification = "CUI"
  })
}

# ACM Private CA for agent mTLS certificates
resource "aws_acmpca_certificate_authority" "agent_ca" {
  type = "ROOT"

  certificate_authority_configuration {
    key_algorithm     = "RSA_4096"
    signing_algorithm = "SHA512WITHRSA"

    subject {
      common_name  = "${{var.project_name}}-agent-ca"
      organization = "ICDEV"
      country      = "US"
    }
  }

  revocation_configuration {
    crl_configuration {
      enabled = true
    }
  }

  tags = merge(var.common_tags, {
    Name           = "${{var.project_name}}-agent-ca"
    Classification = "CUI"
    Purpose        = "Agent mTLS certificate authority"
  })
}
"""

AGENT_NETWORKING_VARIABLES = """\
{{ cui_header }}
variable "project_name" { type = string }
variable "environment" { type = string }
variable "vpc_id" { type = string }
variable "private_subnet_ids" { type = list(string) }
variable "fips_endpoint_cidrs" {
  type        = list(string)
  default     = ["10.0.0.0/8"]
  description = "CIDR blocks allowed to reach FIPS endpoints"
}
variable "common_tags" { type = map(string); default = {} }
"""

AGENT_NETWORKING_OUTPUTS = """\
{{ cui_header }}
output "agent_mesh_security_group_id" {
  description = "Security group ID for agent mesh communication"
  value       = aws_security_group.agent_mesh.id
}

output "bedrock_endpoint_id" {
  description = "VPC endpoint ID for Bedrock FIPS"
  value       = aws_vpc_endpoint.bedrock_fips.id
}

output "secrets_manager_endpoint_id" {
  description = "VPC endpoint ID for Secrets Manager"
  value       = aws_vpc_endpoint.secrets_manager.id
}

output "agent_ca_arn" {
  description = "ARN of the agent mTLS certificate authority"
  value       = aws_acmpca_certificate_authority.agent_ca.arn
}
"""


def generate_agent_networking(project_path: str, config: dict = None) -> list:
    """Generate agent networking (mTLS, FIPS endpoints).

    Creates security groups for agent mesh communication, VPC endpoints
    for Bedrock FIPS and Secrets Manager, and ACM Private CA for mTLS
    certificate issuance.

    Args:
        project_path: Target project directory.
        config: Optional project configuration dict.

    Returns:
        List of generated file paths.
    """
    tf_dir = Path(project_path) / "terraform" / "modules" / "agent_networking"
    ctx = {"cui_header": _cui_header()}

    files = []
    for name, template in [
        ("main.tf", AGENT_NETWORKING_MAIN),
        ("variables.tf", AGENT_NETWORKING_VARIABLES),
        ("outputs.tf", AGENT_NETWORKING_OUTPUTS),
    ]:
        p = _write(tf_dir / name, _render(template, ctx))
        files.append(str(p))
    return files


# ---------------------------------------------------------------------------
# ZTA Security Modules (Phase 25b)
# ---------------------------------------------------------------------------

def generate_zta_security(project_path: str, project_config: dict = None) -> list:
    """Generate ZTA-specific Terraform security modules.

    Delegates to tools.devsecops.zta_terraform_generator for GuardDuty,
    Security Hub, WAF, Config Rules, enhanced VPC Flow Logs, and Secrets
    Manager rotation. Only generates modules when ZTA profile is active.

    Args:
        project_path: Target project directory.
        project_config: Optional dict with zta_modules list.

    Returns:
        List of generated file paths.
    """
    config = project_config or {}
    modules = config.get("zta_modules", ["guardduty", "security_hub", "waf",
                                          "config_rules", "vpc_flow_logs",
                                          "secrets_rotation"])

    try:
        import importlib
        zta_gen = importlib.import_module("tools.devsecops.zta_terraform_generator")
    except (ImportError, ModuleNotFoundError):
        print("[terraform] zta_terraform_generator not available; skipping")
        return []

    files = []
    module_map = {
        "guardduty": "generate_guardduty",
        "security_hub": "generate_security_hub",
        "waf": "generate_waf",
        "config_rules": "generate_config_rules",
        "vpc_flow_logs": "generate_vpc_flow_logs_enhanced",
        "secrets_rotation": "generate_secrets_rotation",
    }

    for mod_name in modules:
        func_name = module_map.get(mod_name)
        if not func_name:
            continue
        gen_fn = getattr(zta_gen, func_name, None)
        if not gen_fn:
            continue
        try:
            result = gen_fn(project_path, config)
            for fp in result.get("files_written", []):
                files.append(fp)
        except Exception as e:
            print(f"[terraform] Warning: ZTA module {mod_name} failed: {e}")

    return files


# ---------------------------------------------------------------------------
# CSP Dispatcher (Phase 38 — D225)
# ---------------------------------------------------------------------------

def _detect_csp() -> str:
    """Detect cloud service provider from cloud_config.yaml or env var."""
    import os
    csp = os.environ.get("ICDEV_CLOUD_PROVIDER", "").lower()
    if csp:
        return csp
    try:
        import yaml
        config_path = BASE_DIR / "args" / "cloud_config.yaml"
        if config_path.exists():
            with open(config_path, encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
            return cfg.get("cloud", {}).get("provider", "aws").lower()
    except Exception:
        pass
    return "aws"


def generate_for_csp(project_path: str, project_config: dict = None,
                     csp: str = None) -> list:
    """CSP dispatcher — delegates to CSP-specific generator.

    Detects CSP from cloud_config.yaml or ICDEV_CLOUD_PROVIDER env var,
    then delegates to the appropriate Terraform generator module.

    Args:
        project_path: Target project directory.
        project_config: Project configuration dict.
        csp: Explicit CSP override (aws, azure, gcp, oci).

    Returns:
        List of generated file paths.
    """
    provider = csp or _detect_csp()

    if provider == "aws":
        # Use this module's existing generators (default)
        config = project_config or {}
        files = []
        files.extend(generate_base(project_path, config))
        files.extend(generate_vpc(project_path))
        files.extend(generate_rds(project_path, config))
        files.extend(generate_ecr(project_path))
        return files

    generator_map = {
        "azure": "tools.infra.terraform_generator_azure",
        "gcp": "tools.infra.terraform_generator_gcp",
        "oci": "tools.infra.terraform_generator_oci",
    }

    module_name = generator_map.get(provider)
    if not module_name:
        print(f"[terraform] Unknown CSP: {provider}. Falling back to AWS.")
        return generate_for_csp(project_path, project_config, csp="aws")

    try:
        import importlib
        mod = importlib.import_module(module_name)
        return mod.generate(project_path, project_config)
    except (ImportError, ModuleNotFoundError) as e:
        print(f"[terraform] CSP module {module_name} not available: {e}. Falling back to AWS.")
        return generate_for_csp(project_path, project_config, csp="aws")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Generate Terraform for Government Cloud")
    parser.add_argument("--project-path", required=True, help="Target project directory")
    parser.add_argument(
        "--components",
        default="base,rds,ecr,vpc",
        help="Comma-separated components: base,rds,ecr,vpc,bedrock_iam,agent_networking,zta_security",
    )
    parser.add_argument("--project-name", default="icdev-project", help="Project name for resource naming")
    parser.add_argument("--environment", default="dev", choices=["dev", "staging", "prod"], help="Target environment")
    parser.add_argument("--db-name", default="appdb", help="Database name for RDS module")
    parser.add_argument("--csp", default=None, choices=["aws", "azure", "gcp", "oci"],
                        help="Cloud service provider (auto-detected from cloud_config.yaml if omitted)")
    args = parser.parse_args()

    config = {
        "project_name": args.project_name,
        "environment": args.environment,
        "db_name": args.db_name,
    }

    # If --csp is specified, use the CSP dispatcher for full generation
    if args.csp and args.csp != "aws":
        all_files = generate_for_csp(args.project_path, config, csp=args.csp)
        print(f"\n[terraform] Generated {args.csp.upper()} Terraform: {len(all_files)} files")
        for f in all_files:
            print(f"  -> {f}")
        return

    components = [c.strip() for c in args.components.split(",")]
    all_files = []

    generators = {
        "base": lambda: generate_base(args.project_path, config),
        "rds": lambda: generate_rds(args.project_path, config),
        "ecr": lambda: generate_ecr(args.project_path),
        "vpc": lambda: generate_vpc(args.project_path),
        "bedrock_iam": lambda: generate_bedrock_iam(args.project_path, config),
        "agent_networking": lambda: generate_agent_networking(args.project_path, config),
        "zta_security": lambda: generate_zta_security(args.project_path, config),
    }

    for comp in components:
        if comp in generators:
            files = generators[comp]()
            all_files.extend(files)
            print(f"[terraform] Generated {comp}: {len(files)} files")
        else:
            print(f"[terraform] Unknown component: {comp}")

    print(f"\n[terraform] Total files generated: {len(all_files)}")
    for f in all_files:
        print(f"  -> {f}")


if __name__ == "__main__":
    main()
