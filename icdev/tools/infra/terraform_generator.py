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
    path.write_text(content, encoding="utf-8", newline="")
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
  availability_zone = data.aws_availability_zones.available.names[count.index % length(data.aws_availability_zones.available.names)]  # noqa: E501

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
        Resource = "arn:aws-us-gov:logs:${{data.aws_region.current.name}}:${{data.aws_caller_identity.current.account_id}}:log-group:/icdev/*"  # noqa: E501
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

    Creates IAM role and policy that allows ICDEV™ agents to invoke
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
      organization = "ICDEV™"
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
# SCCA modules (Secure Cloud Computing Architecture — AWS GovCloud)
# ---------------------------------------------------------------------------

# -- Module 1: scca-network (Transit Gateway + Inspection VPC + Network Firewall)

SCCA_NETWORK_MAIN = """\
{{ cui_header }}
# SCCA Network — Transit Gateway, Inspection VPC, AWS Network Firewall
# DoD SCCA pattern for IL4/IL5/IL6 on AWS GovCloud

data "aws_availability_zones" "available" {
  state = "available"
}

locals {
  az_a = data.aws_availability_zones.available.names[0]
  az_b = data.aws_availability_zones.available.names[1]
}

# ---------------------------------------------------------------
# Transit Gateway
# ---------------------------------------------------------------
resource "aws_ec2_transit_gateway" "this" {
  description                     = "SCCA Transit Gateway"
  default_route_table_association = "disable"
  default_route_table_propagation = "disable"
  auto_accept_shared_attachments  = "disable"

  tags = merge(var.common_tags, {
    Name           = "${{var.project_name}}-${{var.environment}}-tgw"
    Classification = "CUI"
    ManagedBy      = "icdev"
  })
}

resource "aws_ec2_transit_gateway_route_table" "security" {
  transit_gateway_id = aws_ec2_transit_gateway.this.id

  tags = merge(var.common_tags, {
    Name = "${{var.project_name}}-tgw-rt-security"
  })
}

resource "aws_ec2_transit_gateway_route_table" "shared" {
  transit_gateway_id = aws_ec2_transit_gateway.this.id

  tags = merge(var.common_tags, {
    Name = "${{var.project_name}}-tgw-rt-shared"
  })
}

resource "aws_ec2_transit_gateway_route_table" "workload" {
  transit_gateway_id = aws_ec2_transit_gateway.this.id

  tags = merge(var.common_tags, {
    Name = "${{var.project_name}}-tgw-rt-workload"
  })
}

# ---------------------------------------------------------------
# Inspection VPC
# ---------------------------------------------------------------
resource "aws_vpc" "inspection" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = merge(var.common_tags, {
    Name           = "${{var.project_name}}-${{var.environment}}-inspection-vpc"
    Classification = "CUI"
    ManagedBy      = "icdev"
    Environment    = var.environment
    ProjectName    = var.project_name
  })
}

# Public subnets (NAT Gateways)
resource "aws_subnet" "public" {
  count             = 2
  vpc_id            = aws_vpc.inspection.id
  cidr_block        = cidrsubnet(var.vpc_cidr, 8, count.index)
  availability_zone = count.index == 0 ? local.az_a : local.az_b

  tags = merge(var.common_tags, {
    Name           = "${{var.project_name}}-inspection-public-${{count.index + 1}}"
    Tier           = "Public"
    Classification = "CUI"
  })
}

# Firewall subnets
resource "aws_subnet" "firewall" {
  count             = 2
  vpc_id            = aws_vpc.inspection.id
  cidr_block        = cidrsubnet(var.vpc_cidr, 8, count.index + 2)
  availability_zone = count.index == 0 ? local.az_a : local.az_b

  tags = merge(var.common_tags, {
    Name           = "${{var.project_name}}-inspection-firewall-${{count.index + 1}}"
    Tier           = "Firewall"
    Classification = "CUI"
  })
}

# TGW subnets
resource "aws_subnet" "tgw" {
  count             = 2
  vpc_id            = aws_vpc.inspection.id
  cidr_block        = cidrsubnet(var.vpc_cidr, 8, count.index + 4)
  availability_zone = count.index == 0 ? local.az_a : local.az_b

  tags = merge(var.common_tags, {
    Name           = "${{var.project_name}}-inspection-tgw-${{count.index + 1}}"
    Tier           = "TGW"
    Classification = "CUI"
  })
}

# ---------------------------------------------------------------
# Internet Gateway + NAT Gateways
# ---------------------------------------------------------------
resource "aws_internet_gateway" "this" {
  vpc_id = aws_vpc.inspection.id

  tags = merge(var.common_tags, {
    Name = "${{var.project_name}}-inspection-igw"
  })
}

resource "aws_eip" "nat" {
  count  = 2
  domain = "vpc"

  tags = merge(var.common_tags, {
    Name = "${{var.project_name}}-nat-eip-${{count.index + 1}}"
  })
}

resource "aws_nat_gateway" "this" {
  count         = 2
  allocation_id = aws_eip.nat[count.index].id
  subnet_id     = aws_subnet.public[count.index].id

  tags = merge(var.common_tags, {
    Name = "${{var.project_name}}-nat-gw-${{count.index + 1}}"
  })

  depends_on = [aws_internet_gateway.this]
}

# ---------------------------------------------------------------
# AWS Network Firewall
# ---------------------------------------------------------------
resource "aws_networkfirewall_rule_group" "stateful" {
  capacity = 100
  name     = "${{var.project_name}}-${{var.environment}}-stateful-rules"
  type     = "STATEFUL"

  rule_group {
    rules_source {
      stateful_rule {
        action = "DROP"
        header {
          destination      = "ANY"
          destination_port = "ANY"
          direction        = "ANY"
          protocol         = "TCP"
          source           = "ANY"
          source_port      = "ANY"
        }
        rule_option {
          keyword  = "sid"
          settings = ["1"]
        }
      }

      stateful_rule {
        action = "PASS"
        header {
          destination      = "ANY"
          destination_port = "ANY"
          direction        = "ANY"
          protocol         = "TCP"
          source           = "ANY"
          source_port      = "ANY"
        }
        rule_option {
          keyword  = "flow"
          settings = ["established"]
        }
        rule_option {
          keyword  = "sid"
          settings = ["2"]
        }
      }
    }

    rule_variables {
      ip_sets {
        key = "HOME_NET"
        ip_set {
          definition = [var.vpc_cidr]
        }
      }
    }
  }

  tags = merge(var.common_tags, {
    Name           = "${{var.project_name}}-stateful-rules"
    Classification = "CUI"
    ManagedBy      = "icdev"
  })
}

resource "aws_networkfirewall_firewall_policy" "this" {
  name = "${{var.project_name}}-${{var.environment}}-firewall-policy"

  firewall_policy {
    stateless_default_actions          = ["aws:forward_to_sfe"]
    stateless_fragment_default_actions = ["aws:forward_to_sfe"]

    stateful_rule_group_reference {
      resource_arn = aws_networkfirewall_rule_group.stateful.arn
    }
  }

  tags = merge(var.common_tags, {
    Name           = "${{var.project_name}}-firewall-policy"
    Classification = "CUI"
    ManagedBy      = "icdev"
  })
}

resource "aws_networkfirewall_firewall" "this" {
  name                = "${{var.project_name}}-${{var.environment}}-network-firewall"
  firewall_policy_arn = aws_networkfirewall_firewall_policy.this.arn
  vpc_id              = aws_vpc.inspection.id

  dynamic "subnet_mapping" {
    for_each = aws_subnet.firewall[*].id
    content {
      subnet_id = subnet_mapping.value
    }
  }

  tags = merge(var.common_tags, {
    Name           = "${{var.project_name}}-network-firewall"
    Classification = "CUI"
    ManagedBy      = "icdev"
    Environment    = var.environment
    ProjectName    = var.project_name
  })
}

# ---------------------------------------------------------------
# TGW VPC Attachment for Inspection VPC
# ---------------------------------------------------------------
resource "aws_ec2_transit_gateway_vpc_attachment" "inspection" {
  transit_gateway_id = aws_ec2_transit_gateway.this.id
  vpc_id             = aws_vpc.inspection.id
  subnet_ids         = aws_subnet.tgw[*].id

  transit_gateway_default_route_table_association = false
  transit_gateway_default_route_table_propagation = false

  tags = merge(var.common_tags, {
    Name = "${{var.project_name}}-tgw-attach-inspection"
  })
}

resource "aws_ec2_transit_gateway_route_table_association" "inspection" {
  transit_gateway_attachment_id  = aws_ec2_transit_gateway_vpc_attachment.inspection.id
  transit_gateway_route_table_id = aws_ec2_transit_gateway_route_table.security.id
}

# ---------------------------------------------------------------
# VPC Flow Logs
# ---------------------------------------------------------------
resource "aws_cloudwatch_log_group" "inspection_flow_log" {
  name              = "/aws/vpc/${{var.project_name}}-${{var.environment}}-inspection/flow-logs"
  retention_in_days = 365

  tags = merge(var.common_tags, {
    Classification = "CUI"
  })
}

resource "aws_iam_role" "inspection_flow_log" {
  name = "${{var.project_name}}-${{var.environment}}-inspection-flow-log-role"

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

resource "aws_iam_role_policy" "inspection_flow_log" {
  name = "${{var.project_name}}-${{var.environment}}-inspection-flow-log-policy"
  role = aws_iam_role.inspection_flow_log.id

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

resource "aws_flow_log" "inspection" {
  vpc_id               = aws_vpc.inspection.id
  traffic_type         = "ALL"
  log_destination_type = "cloud-watch-logs"
  log_destination      = aws_cloudwatch_log_group.inspection_flow_log.arn
  iam_role_arn         = aws_iam_role.inspection_flow_log.arn

  tags = merge(var.common_tags, {
    Name           = "${{var.project_name}}-inspection-flow-log"
    Classification = "CUI"
  })
}
"""

SCCA_NETWORK_VARIABLES = """\
{{ cui_header }}
variable "project_name" {
  description = "Project identifier"
  type        = string
}

variable "environment" {
  description = "Deployment environment (dev, staging, prod)"
  type        = string
}

variable "il_level" {
  description = "Impact level (IL4, IL5, IL6)"
  type        = string
  default     = "IL4"

  validation {
    condition     = contains(["IL4", "IL5", "IL6"], var.il_level)
    error_message = "IL level must be IL4, IL5, or IL6."
  }
}

variable "vpc_cidr" {
  description = "CIDR block for the inspection VPC"
  type        = string
  default     = "10.0.0.0/16"
}

variable "region" {
  description = "AWS GovCloud region"
  type        = string
  default     = "us-gov-west-1"
}

variable "common_tags" {
  description = "Common tags for all resources"
  type        = map(string)
  default     = {}
}
"""

SCCA_NETWORK_OUTPUTS = """\
{{ cui_header }}
output "tgw_id" {
  description = "Transit Gateway ID"
  value       = aws_ec2_transit_gateway.this.id
}

output "inspection_vpc_id" {
  description = "Inspection VPC ID"
  value       = aws_vpc.inspection.id
}

output "firewall_arn" {
  description = "AWS Network Firewall ARN"
  value       = aws_networkfirewall_firewall.this.arn
}

output "tgw_route_table_ids" {
  description = "Transit Gateway route table IDs"
  value = {
    security = aws_ec2_transit_gateway_route_table.security.id
    shared   = aws_ec2_transit_gateway_route_table.shared.id
    workload = aws_ec2_transit_gateway_route_table.workload.id
  }
}
"""

# -- Module 2: scca-security (GuardDuty + Security Hub + Config)

SCCA_SECURITY_MAIN = """\
{{ cui_header }}
# SCCA Security — GuardDuty, Security Hub, AWS Config
# Continuous monitoring and compliance for DoD SCCA

# ---------------------------------------------------------------
# GuardDuty
# ---------------------------------------------------------------
resource "aws_guardduty_detector" "this" {
  enable = true

  datasources {
    s3_logs {
      enable = true
    }
    kubernetes {
      audit_logs {
        enable = true
      }
    }
    malware_protection {
      scan_ec2_instance_with_findings {
        ebs_volumes {
          enable = true
        }
      }
    }
  }

  tags = merge(var.common_tags, {
    Name           = "${{var.project_name}}-${{var.environment}}-guardduty"
    Classification = "CUI"
    ManagedBy      = "icdev"
    Environment    = var.environment
    ProjectName    = var.project_name
  })
}

# ---------------------------------------------------------------
# Security Hub with NIST 800-53
# ---------------------------------------------------------------
resource "aws_securityhub_account" "this" {}

resource "aws_securityhub_standards_subscription" "nist_800_53" {
  standards_arn = "arn:aws-us-gov:securityhub:${{var.region}}::standards/nist-800-53/v/5.0.0"

  depends_on = [aws_securityhub_account.this]
}

# ---------------------------------------------------------------
# AWS Config
# ---------------------------------------------------------------
resource "aws_config_configuration_recorder" "this" {
  name     = "${{var.project_name}}-${{var.environment}}-config-recorder"
  role_arn = aws_iam_role.config.arn

  recording_group {
    all_supported                 = true
    include_global_resource_types = true
  }
}

resource "aws_iam_role" "config" {
  name = "${{var.project_name}}-${{var.environment}}-config-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action    = "sts:AssumeRole"
        Effect    = "Allow"
        Principal = { Service = "config.amazonaws.com" }
      }
    ]
  })

  tags = merge(var.common_tags, {
    Classification = "CUI"
  })
}

resource "aws_iam_role_policy_attachment" "config" {
  role       = aws_iam_role.config.name
  policy_arn = "arn:aws-us-gov:iam::aws:policy/service-role/AWS_ConfigRole"
}

resource "aws_s3_bucket" "config" {
  bucket        = "${{var.project_name}}-${{var.environment}}-config-${{var.region}}"
  force_destroy = false

  tags = merge(var.common_tags, {
    Name           = "${{var.project_name}}-config-bucket"
    Classification = "CUI"
    ManagedBy      = "icdev"
  })
}

resource "aws_s3_bucket_versioning" "config" {
  bucket = aws_s3_bucket.config.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_public_access_block" "config" {
  bucket                  = aws_s3_bucket.config.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "config" {
  bucket = aws_s3_bucket.config.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "aws:kms"
    }
  }
}

resource "aws_config_delivery_channel" "this" {
  name           = "${{var.project_name}}-${{var.environment}}-config-channel"
  s3_bucket_name = aws_s3_bucket.config.id

  depends_on = [aws_config_configuration_recorder.this]
}

resource "aws_config_configuration_recorder_status" "this" {
  name       = aws_config_configuration_recorder.this.name
  is_enabled = true

  depends_on = [aws_config_delivery_channel.this]
}

resource "aws_config_conformance_pack" "nist_800_53" {
  name = "${{var.project_name}}-${{var.environment}}-nist-800-53"

  template_body = <<-TEMPLATE
    Resources:
      ConformancePackNIST80053:
        Type: "AWS::Config::ConformancePack"
        Properties:
          ConformancePackName: "Operational-Best-Practices-for-NIST-800-53-rev-5"
  TEMPLATE

  depends_on = [aws_config_configuration_recorder_status.this]

  tags = merge(var.common_tags, {
    Name           = "${{var.project_name}}-nist-800-53-conformance"
    Classification = "CUI"
    ManagedBy      = "icdev"
  })
}
"""

SCCA_SECURITY_VARIABLES = """\
{{ cui_header }}
variable "project_name" {
  description = "Project identifier"
  type        = string
}

variable "environment" {
  description = "Deployment environment (dev, staging, prod)"
  type        = string
}

variable "il_level" {
  description = "Impact level (IL4, IL5, IL6)"
  type        = string
  default     = "IL4"

  validation {
    condition     = contains(["IL4", "IL5", "IL6"], var.il_level)
    error_message = "IL level must be IL4, IL5, or IL6."
  }
}

variable "region" {
  description = "AWS GovCloud region"
  type        = string
  default     = "us-gov-west-1"
}

variable "common_tags" {
  description = "Common tags for all resources"
  type        = map(string)
  default     = {}
}
"""

SCCA_SECURITY_OUTPUTS = """\
{{ cui_header }}
output "guardduty_detector_id" {
  description = "GuardDuty detector ID"
  value       = aws_guardduty_detector.this.id
}

output "security_hub_arn" {
  description = "Security Hub account ARN"
  value       = aws_securityhub_account.this.arn
}

output "config_recorder_id" {
  description = "AWS Config recorder ID"
  value       = aws_config_configuration_recorder.this.id
}

output "config_bucket_arn" {
  description = "Config S3 bucket ARN"
  value       = aws_s3_bucket.config.arn
}
"""

# -- Module 3: scca-logging (CloudTrail + S3 + KMS)

SCCA_LOGGING_MAIN = """\
{{ cui_header }}
# SCCA Logging — CloudTrail, KMS, S3 log archive
# FIPS 140-2 compliant encryption for audit logs

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

# ---------------------------------------------------------------
# KMS Key for Log Encryption (FIPS 140-2)
# ---------------------------------------------------------------
resource "aws_kms_key" "logs" {
  description             = "SCCA log encryption key (FIPS 140-2)"
  deletion_window_in_days = 30
  enable_key_rotation     = true

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "RootAccess"
        Effect = "Allow"
        Principal = {
          AWS = "arn:aws-us-gov:iam::${{data.aws_caller_identity.current.account_id}}:root"
        }
        Action   = "kms:*"
        Resource = "*"
      },
      {
        Sid    = "CloudTrailEncrypt"
        Effect = "Allow"
        Principal = {
          Service = "cloudtrail.amazonaws.com"
        }
        Action = [
          "kms:GenerateDataKey*",
          "kms:DescribeKey"
        ]
        Resource = "*"
      },
      {
        Sid    = "S3Encrypt"
        Effect = "Allow"
        Principal = {
          Service = "s3.amazonaws.com"
        }
        Action = [
          "kms:GenerateDataKey*",
          "kms:Decrypt"
        ]
        Resource = "*"
      }
    ]
  })

  tags = merge(var.common_tags, {
    Name           = "${{var.project_name}}-${{var.environment}}-log-kms"
    Classification = "CUI"
    ManagedBy      = "icdev"
    FIPS           = "140-2"
    Environment    = var.environment
    ProjectName    = var.project_name
  })
}

resource "aws_kms_alias" "logs" {
  name          = "alias/${{var.project_name}}-${{var.environment}}-logs"
  target_key_id = aws_kms_key.logs.key_id
}

# ---------------------------------------------------------------
# S3 Bucket for Logs
# ---------------------------------------------------------------
resource "aws_s3_bucket" "logs" {
  bucket        = "${{var.project_name}}-${{var.environment}}-audit-logs-${{data.aws_region.current.name}}"
  force_destroy = false

  tags = merge(var.common_tags, {
    Name           = "${{var.project_name}}-audit-logs"
    Classification = "CUI"
    ManagedBy      = "icdev"
  })
}

resource "aws_s3_bucket_versioning" "logs" {
  bucket = aws_s3_bucket.logs.id
  versioning_configuration {
    status     = "Enabled"
    mfa_delete = var.il_level == "IL6" ? "Enabled" : "Disabled"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "logs" {
  bucket = aws_s3_bucket.logs.id

  rule {
    id     = "archive-old-logs"
    status = "Enabled"

    transition {
      days          = 90
      storage_class = "GLACIER"
    }

    transition {
      days          = 365
      storage_class = "DEEP_ARCHIVE"
    }

    expiration {
      days = 2555  # 7 years retention
    }
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "logs" {
  bucket = aws_s3_bucket.logs.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.logs.arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "logs" {
  bucket                  = aws_s3_bucket.logs.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_policy" "logs" {
  bucket = aws_s3_bucket.logs.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "CloudTrailWrite"
        Effect = "Allow"
        Principal = {
          Service = "cloudtrail.amazonaws.com"
        }
        Action   = "s3:PutObject"
        Resource = "$${aws_s3_bucket.logs.arn}/AWSLogs/*"
        Condition = {
          StringEquals = {
            "s3:x-amz-acl" = "bucket-owner-full-control"
          }
        }
      },
      {
        Sid    = "CloudTrailCheck"
        Effect = "Allow"
        Principal = {
          Service = "cloudtrail.amazonaws.com"
        }
        Action   = "s3:GetBucketAcl"
        Resource = aws_s3_bucket.logs.arn
      },
      {
        Sid    = "DenyNonSSL"
        Effect = "Deny"
        Principal = "*"
        Action   = "s3:*"
        Resource = [
          aws_s3_bucket.logs.arn,
          "$${aws_s3_bucket.logs.arn}/*"
        ]
        Condition = {
          Bool = {
            "aws:SecureTransport" = "false"
          }
        }
      }
    ]
  })
}

# ---------------------------------------------------------------
# Organization CloudTrail
# ---------------------------------------------------------------
resource "aws_cloudtrail" "org" {
  name                       = "${{var.project_name}}-${{var.environment}}-org-trail"
  s3_bucket_name             = aws_s3_bucket.logs.id
  is_multi_region_trail      = true
  is_organization_trail      = var.is_organization_trail
  enable_log_file_validation = true
  kms_key_id                 = aws_kms_key.logs.arn

  event_selector {
    read_write_type           = "All"
    include_management_events = true

    data_resource {
      type   = "AWS::S3::Object"
      values = ["arn:aws-us-gov:s3"]
    }
  }

  tags = merge(var.common_tags, {
    Name           = "${{var.project_name}}-org-cloudtrail"
    Classification = "CUI"
    ManagedBy      = "icdev"
    Environment    = var.environment
    ProjectName    = var.project_name
  })

  depends_on = [aws_s3_bucket_policy.logs]
}

# ---------------------------------------------------------------
# VPC Flow Log Destination Bucket
# ---------------------------------------------------------------
resource "aws_s3_bucket" "flow_logs" {
  bucket        = "${{var.project_name}}-${{var.environment}}-vpc-flow-logs-${{data.aws_region.current.name}}"
  force_destroy = false

  tags = merge(var.common_tags, {
    Name           = "${{var.project_name}}-vpc-flow-logs"
    Classification = "CUI"
    ManagedBy      = "icdev"
  })
}

resource "aws_s3_bucket_server_side_encryption_configuration" "flow_logs" {
  bucket = aws_s3_bucket.flow_logs.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.logs.arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "flow_logs" {
  bucket                  = aws_s3_bucket.flow_logs.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
"""

SCCA_LOGGING_VARIABLES = """\
{{ cui_header }}
variable "project_name" {
  description = "Project identifier"
  type        = string
}

variable "environment" {
  description = "Deployment environment (dev, staging, prod)"
  type        = string
}

variable "il_level" {
  description = "Impact level (IL4, IL5, IL6)"
  type        = string
  default     = "IL4"

  validation {
    condition     = contains(["IL4", "IL5", "IL6"], var.il_level)
    error_message = "IL level must be IL4, IL5, or IL6."
  }
}

variable "region" {
  description = "AWS GovCloud region"
  type        = string
  default     = "us-gov-west-1"
}

variable "is_organization_trail" {
  description = "Whether CloudTrail is organization-wide"
  type        = bool
  default     = false
}

variable "common_tags" {
  description = "Common tags for all resources"
  type        = map(string)
  default     = {}
}
"""

SCCA_LOGGING_OUTPUTS = """\
{{ cui_header }}
output "kms_key_arn" {
  description = "KMS key ARN for log encryption"
  value       = aws_kms_key.logs.arn
}

output "log_bucket_arn" {
  description = "S3 bucket ARN for audit logs"
  value       = aws_s3_bucket.logs.arn
}

output "cloudtrail_arn" {
  description = "CloudTrail ARN"
  value       = aws_cloudtrail.org.arn
}

output "flow_log_bucket_arn" {
  description = "S3 bucket ARN for VPC flow logs"
  value       = aws_s3_bucket.flow_logs.arn
}
"""

# -- Module 4: scca-identity (IAM Identity Center + Managed AD)

SCCA_IDENTITY_MAIN = """\
{{ cui_header }}
# SCCA Identity — IAM Identity Center, Managed AD, Password Policy
# CAC/PIV/PKI integration for DoD environments

# ---------------------------------------------------------------
# IAM Identity Center (SSO)
# ---------------------------------------------------------------
resource "aws_ssoadmin_instance" "this" {}

data "aws_ssoadmin_instances" "this" {}

locals {
  identity_store_id = tolist(data.aws_ssoadmin_instances.this.identity_store_ids)[0]
  sso_instance_arn  = tolist(data.aws_ssoadmin_instances.this.arns)[0]
}

# ---------------------------------------------------------------
# Permission Sets
# ---------------------------------------------------------------
resource "aws_ssoadmin_permission_set" "admin" {
  name             = "SCCA-Admin"
  description      = "Full administrator access for SCCA management"
  instance_arn     = local.sso_instance_arn
  session_duration = "PT4H"

  tags = merge(var.common_tags, {
    Classification = "CUI"
    ManagedBy      = "icdev"
    Environment    = var.environment
    ProjectName    = var.project_name
  })
}

resource "aws_ssoadmin_managed_policy_attachment" "admin" {
  instance_arn       = local.sso_instance_arn
  managed_policy_arn = "arn:aws-us-gov:iam::aws:policy/AdministratorAccess"
  permission_set_arn = aws_ssoadmin_permission_set.admin.arn
}

resource "aws_ssoadmin_permission_set" "readonly" {
  name             = "SCCA-ReadOnly"
  description      = "Read-only access for auditors and reviewers"
  instance_arn     = local.sso_instance_arn
  session_duration = "PT8H"

  tags = merge(var.common_tags, {
    Classification = "CUI"
    ManagedBy      = "icdev"
  })
}

resource "aws_ssoadmin_managed_policy_attachment" "readonly" {
  instance_arn       = local.sso_instance_arn
  managed_policy_arn = "arn:aws-us-gov:iam::aws:policy/ReadOnlyAccess"
  permission_set_arn = aws_ssoadmin_permission_set.readonly.arn
}

resource "aws_ssoadmin_permission_set" "security_audit" {
  name             = "SCCA-SecurityAudit"
  description      = "Security audit access for compliance monitoring"
  instance_arn     = local.sso_instance_arn
  session_duration = "PT8H"

  tags = merge(var.common_tags, {
    Classification = "CUI"
    ManagedBy      = "icdev"
  })
}

resource "aws_ssoadmin_managed_policy_attachment" "security_audit" {
  instance_arn       = local.sso_instance_arn
  managed_policy_arn = "arn:aws-us-gov:iam::aws:policy/SecurityAudit"
  permission_set_arn = aws_ssoadmin_permission_set.security_audit.arn
}

resource "aws_ssoadmin_permission_set" "network_admin" {
  name             = "SCCA-NetworkAdmin"
  description      = "Network administration for SCCA infrastructure"
  instance_arn     = local.sso_instance_arn
  session_duration = "PT4H"

  tags = merge(var.common_tags, {
    Classification = "CUI"
    ManagedBy      = "icdev"
  })
}

resource "aws_ssoadmin_managed_policy_attachment" "network_admin" {
  instance_arn       = local.sso_instance_arn
  managed_policy_arn = "arn:aws-us-gov:iam::aws:policy/job-function/NetworkAdministrator"
  permission_set_arn = aws_ssoadmin_permission_set.network_admin.arn
}

# ---------------------------------------------------------------
# Managed Microsoft AD (CAC/PIV/PKI)
# ---------------------------------------------------------------
resource "aws_directory_service_directory" "managed_ad" {
  name     = "${{var.ad_domain_name}}"
  password = var.ad_admin_password
  edition  = var.il_level == "IL6" ? "Enterprise" : "Standard"
  type     = "MicrosoftAD"

  vpc_settings {
    vpc_id     = var.vpc_id
    subnet_ids = var.ad_subnet_ids
  }

  tags = merge(var.common_tags, {
    Name           = "${{var.project_name}}-managed-ad"
    Classification = "CUI"
    ManagedBy      = "icdev"
    Environment    = var.environment
    ProjectName    = var.project_name
  })
}

# ---------------------------------------------------------------
# IAM Password Policy (DoD Requirements)
# ---------------------------------------------------------------
resource "aws_iam_account_password_policy" "dod" {
  minimum_password_length        = 15
  require_uppercase_characters   = true
  require_lowercase_characters   = true
  require_numbers                = true
  require_symbols                = true
  allow_users_to_change_password = true
  max_password_age               = 60
  password_reuse_prevention      = 24
  hard_expiry                    = false
}
"""

SCCA_IDENTITY_VARIABLES = """\
{{ cui_header }}
variable "project_name" {
  description = "Project identifier"
  type        = string
}

variable "environment" {
  description = "Deployment environment (dev, staging, prod)"
  type        = string
}

variable "il_level" {
  description = "Impact level (IL4, IL5, IL6)"
  type        = string
  default     = "IL4"

  validation {
    condition     = contains(["IL4", "IL5", "IL6"], var.il_level)
    error_message = "IL level must be IL4, IL5, or IL6."
  }
}

variable "vpc_id" {
  description = "VPC ID for Managed AD deployment"
  type        = string
}

variable "ad_subnet_ids" {
  description = "Subnet IDs for Managed AD (minimum 2, different AZs)"
  type        = list(string)
}

variable "ad_domain_name" {
  description = "Active Directory domain name"
  type        = string
  default     = "scca.govcloud.local"
}

variable "ad_admin_password" {
  description = "Managed AD admin password"
  type        = string
  sensitive   = true
}

variable "region" {
  description = "AWS GovCloud region"
  type        = string
  default     = "us-gov-west-1"
}

variable "common_tags" {
  description = "Common tags for all resources"
  type        = map(string)
  default     = {}
}
"""

SCCA_IDENTITY_OUTPUTS = """\
{{ cui_header }}
output "sso_instance_arn" {
  description = "IAM Identity Center instance ARN"
  value       = local.sso_instance_arn
}

output "permission_set_arns" {
  description = "Permission set ARNs"
  value = {
    admin          = aws_ssoadmin_permission_set.admin.arn
    readonly       = aws_ssoadmin_permission_set.readonly.arn
    security_audit = aws_ssoadmin_permission_set.security_audit.arn
    network_admin  = aws_ssoadmin_permission_set.network_admin.arn
  }
}

output "managed_ad_id" {
  description = "Managed Microsoft AD directory ID"
  value       = aws_directory_service_directory.managed_ad.id
}

output "managed_ad_dns_ips" {
  description = "Managed AD DNS IP addresses"
  value       = aws_directory_service_directory.managed_ad.dns_ip_addresses
}
"""

# -- Module 5: scca-mission-vpc (Reusable spoke VPC)

SCCA_MISSION_VPC_MAIN = """\
{{ cui_header }}
# SCCA Mission VPC — Reusable spoke VPC for workloads
# No public subnets — all egress via Transit Gateway through inspection

data "aws_availability_zones" "available" {
  state = "available"
}

locals {
  az_a = data.aws_availability_zones.available.names[0]
  az_b = data.aws_availability_zones.available.names[1]
}

# ---------------------------------------------------------------
# Mission VPC
# ---------------------------------------------------------------
resource "aws_vpc" "mission" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = merge(var.common_tags, {
    Name           = "${{var.project_name}}-${{var.environment}}-mission-vpc"
    Classification = "CUI"
    ManagedBy      = "icdev"
    Environment    = var.environment
    ProjectName    = var.project_name
  })
}

# Application subnets
resource "aws_subnet" "app" {
  count             = 2
  vpc_id            = aws_vpc.mission.id
  cidr_block        = cidrsubnet(var.vpc_cidr, 8, count.index)
  availability_zone = count.index == 0 ? local.az_a : local.az_b

  map_public_ip_on_launch = false

  tags = merge(var.common_tags, {
    Name           = "${{var.project_name}}-mission-app-${{count.index + 1}}"
    Tier           = "Application"
    Classification = "CUI"
  })
}

# Data subnets
resource "aws_subnet" "data" {
  count             = 2
  vpc_id            = aws_vpc.mission.id
  cidr_block        = cidrsubnet(var.vpc_cidr, 8, count.index + 2)
  availability_zone = count.index == 0 ? local.az_a : local.az_b

  map_public_ip_on_launch = false

  tags = merge(var.common_tags, {
    Name           = "${{var.project_name}}-mission-data-${{count.index + 1}}"
    Tier           = "Data"
    Classification = "CUI"
  })
}

# ---------------------------------------------------------------
# TGW Attachment
# ---------------------------------------------------------------
resource "aws_ec2_transit_gateway_vpc_attachment" "mission" {
  transit_gateway_id = var.tgw_id
  vpc_id             = aws_vpc.mission.id
  subnet_ids         = aws_subnet.app[*].id

  transit_gateway_default_route_table_association = false
  transit_gateway_default_route_table_propagation = false

  tags = merge(var.common_tags, {
    Name = "${{var.project_name}}-tgw-attach-mission"
  })
}

# ---------------------------------------------------------------
# Route Tables — default route to TGW (inspection)
# ---------------------------------------------------------------
resource "aws_route_table" "app" {
  vpc_id = aws_vpc.mission.id

  route {
    cidr_block         = "0.0.0.0/0"
    transit_gateway_id = var.tgw_id
  }

  tags = merge(var.common_tags, {
    Name = "${{var.project_name}}-mission-app-rt"
  })
}

resource "aws_route_table_association" "app" {
  count          = 2
  subnet_id      = aws_subnet.app[count.index].id
  route_table_id = aws_route_table.app.id
}

resource "aws_route_table" "data" {
  vpc_id = aws_vpc.mission.id

  route {
    cidr_block         = "0.0.0.0/0"
    transit_gateway_id = var.tgw_id
  }

  tags = merge(var.common_tags, {
    Name = "${{var.project_name}}-mission-data-rt"
  })
}

resource "aws_route_table_association" "data" {
  count          = 2
  subnet_id      = aws_subnet.data[count.index].id
  route_table_id = aws_route_table.data.id
}

# ---------------------------------------------------------------
# VPC Flow Logs
# ---------------------------------------------------------------
resource "aws_cloudwatch_log_group" "mission_flow_log" {
  name              = "/aws/vpc/${{var.project_name}}-${{var.environment}}-mission/flow-logs"
  retention_in_days = 365

  tags = merge(var.common_tags, {
    Classification = "CUI"
  })
}

resource "aws_iam_role" "mission_flow_log" {
  name = "${{var.project_name}}-${{var.environment}}-mission-flow-log-role"

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

resource "aws_iam_role_policy" "mission_flow_log" {
  name = "${{var.project_name}}-${{var.environment}}-mission-flow-log-policy"
  role = aws_iam_role.mission_flow_log.id

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

resource "aws_flow_log" "mission" {
  vpc_id               = aws_vpc.mission.id
  traffic_type         = "ALL"
  log_destination_type = "cloud-watch-logs"
  log_destination      = aws_cloudwatch_log_group.mission_flow_log.arn
  iam_role_arn         = aws_iam_role.mission_flow_log.arn

  tags = merge(var.common_tags, {
    Name           = "${{var.project_name}}-mission-flow-log"
    Classification = "CUI"
  })
}

# ---------------------------------------------------------------
# VPC Endpoints (avoid internet traversal)
# ---------------------------------------------------------------
resource "aws_security_group" "endpoints" {
  name_prefix = "${{var.project_name}}-vpce-"
  vpc_id      = aws_vpc.mission.id
  description = "Security group for VPC endpoints"

  ingress {
    description = "HTTPS from VPC"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr]
  }

  tags = merge(var.common_tags, {
    Name           = "${{var.project_name}}-vpce-sg"
    Classification = "CUI"
  })
}

resource "aws_vpc_endpoint" "s3" {
  vpc_id            = aws_vpc.mission.id
  service_name      = "com.amazonaws.${{var.region}}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [aws_route_table.app.id, aws_route_table.data.id]

  tags = merge(var.common_tags, {
    Name = "${{var.project_name}}-vpce-s3"
  })
}

resource "aws_vpc_endpoint" "ssm" {
  vpc_id              = aws_vpc.mission.id
  service_name        = "com.amazonaws.${{var.region}}.ssm"
  vpc_endpoint_type   = "Interface"
  private_dns_enabled = true
  subnet_ids          = aws_subnet.app[*].id
  security_group_ids  = [aws_security_group.endpoints.id]

  tags = merge(var.common_tags, {
    Name = "${{var.project_name}}-vpce-ssm"
  })
}

resource "aws_vpc_endpoint" "kms" {
  vpc_id              = aws_vpc.mission.id
  service_name        = "com.amazonaws.${{var.region}}.kms"
  vpc_endpoint_type   = "Interface"
  private_dns_enabled = true
  subnet_ids          = aws_subnet.app[*].id
  security_group_ids  = [aws_security_group.endpoints.id]

  tags = merge(var.common_tags, {
    Name = "${{var.project_name}}-vpce-kms"
  })
}

resource "aws_vpc_endpoint" "logs" {
  vpc_id              = aws_vpc.mission.id
  service_name        = "com.amazonaws.${{var.region}}.logs"
  vpc_endpoint_type   = "Interface"
  private_dns_enabled = true
  subnet_ids          = aws_subnet.app[*].id
  security_group_ids  = [aws_security_group.endpoints.id]

  tags = merge(var.common_tags, {
    Name = "${{var.project_name}}-vpce-logs"
  })
}
"""

SCCA_MISSION_VPC_VARIABLES = """\
{{ cui_header }}
variable "project_name" {
  description = "Project identifier"
  type        = string
}

variable "environment" {
  description = "Deployment environment (dev, staging, prod)"
  type        = string
}

variable "il_level" {
  description = "Impact level (IL4, IL5, IL6)"
  type        = string
  default     = "IL4"

  validation {
    condition     = contains(["IL4", "IL5", "IL6"], var.il_level)
    error_message = "IL level must be IL4, IL5, or IL6."
  }
}

variable "vpc_cidr" {
  description = "CIDR block for the mission VPC"
  type        = string
  default     = "10.1.0.0/16"
}

variable "tgw_id" {
  description = "Transit Gateway ID for attachment"
  type        = string
}

variable "region" {
  description = "AWS GovCloud region"
  type        = string
  default     = "us-gov-west-1"
}

variable "common_tags" {
  description = "Common tags for all resources"
  type        = map(string)
  default     = {}
}
"""

SCCA_MISSION_VPC_OUTPUTS = """\
{{ cui_header }}
output "mission_vpc_id" {
  description = "Mission VPC ID"
  value       = aws_vpc.mission.id
}

output "app_subnet_ids" {
  description = "Application subnet IDs"
  value       = aws_subnet.app[*].id
}

output "data_subnet_ids" {
  description = "Data subnet IDs"
  value       = aws_subnet.data[*].id
}

output "tgw_attachment_id" {
  description = "Transit Gateway VPC attachment ID"
  value       = aws_ec2_transit_gateway_vpc_attachment.mission.id
}
"""


def generate_scca(project_path: str, project_config: dict = None) -> list:
    """Generate SCCA (Secure Cloud Computing Architecture) Terraform modules.

    Creates 5 modules for DoD SCCA on AWS GovCloud:
      - scca-network: Transit Gateway, Inspection VPC, Network Firewall
      - scca-security: GuardDuty, Security Hub, AWS Config
      - scca-logging: CloudTrail, KMS, S3 log archive
      - scca-identity: IAM Identity Center, Managed AD
      - scca-mission-vpc: Reusable spoke VPC with TGW attachment

    Args:
        project_path: Target project directory.
        project_config: Optional project configuration dict.

    Returns:
        List of generated file paths.
    """
    ctx = {"cui_header": _cui_header()}
    files = []

    modules = [
        (
            "scca-network",
            [
                ("main.tf", SCCA_NETWORK_MAIN),
                ("variables.tf", SCCA_NETWORK_VARIABLES),
                ("outputs.tf", SCCA_NETWORK_OUTPUTS),
            ],
        ),
        (
            "scca-security",
            [
                ("main.tf", SCCA_SECURITY_MAIN),
                ("variables.tf", SCCA_SECURITY_VARIABLES),
                ("outputs.tf", SCCA_SECURITY_OUTPUTS),
            ],
        ),
        (
            "scca-logging",
            [
                ("main.tf", SCCA_LOGGING_MAIN),
                ("variables.tf", SCCA_LOGGING_VARIABLES),
                ("outputs.tf", SCCA_LOGGING_OUTPUTS),
            ],
        ),
        (
            "scca-identity",
            [
                ("main.tf", SCCA_IDENTITY_MAIN),
                ("variables.tf", SCCA_IDENTITY_VARIABLES),
                ("outputs.tf", SCCA_IDENTITY_OUTPUTS),
            ],
        ),
        (
            "scca-mission-vpc",
            [
                ("main.tf", SCCA_MISSION_VPC_MAIN),
                ("variables.tf", SCCA_MISSION_VPC_VARIABLES),
                ("outputs.tf", SCCA_MISSION_VPC_OUTPUTS),
            ],
        ),
    ]

    for module_name, templates in modules:
        tf_dir = Path(project_path) / "terraform" / "modules" / module_name
        for filename, template in templates:
            p = _write(tf_dir / filename, _render(template, ctx))
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
    modules = config.get(
        "zta_modules", ["guardduty", "security_hub", "waf", "config_rules", "vpc_flow_logs", "secrets_rotation"]
    )

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
# WA Security Baseline Module (Well-Architected Security Pillar)
# ---------------------------------------------------------------------------

WA_SECURITY_MAIN = """\
{{ cui_header }}
# WA Security Baseline — Per-Account Security Foundation
# AWS Well-Architected Security Pillar (SEC01-SEC11)
# Deploys: GuardDuty, Security Hub (NIST 800-53), Config recorder + rules,
#          Inspector, CloudWatch alarms, VPC Flow Logs, S3 access logging

# ---------------------------------------------------------------
# GuardDuty — Threat Detection (SEC04)
# ---------------------------------------------------------------
resource "aws_guardduty_detector" "this" {
  enable = true

  datasources {
    s3_logs {
      enable = true
    }
    kubernetes {
      audit_logs {
        enable = true
      }
    }
    malware_protection {
      scan_ec2_instance_with_findings {
        ebs_volumes {
          enable = true
        }
      }
    }
  }

  tags = merge(var.common_tags, {
    Name           = "${{var.project_name}}-${{var.environment}}-guardduty"
    Classification = "CUI"
    ManagedBy      = "icdev"
  })
}

# ---------------------------------------------------------------
# Security Hub with NIST 800-53 (SEC01/SEC04)
# ---------------------------------------------------------------
resource "aws_securityhub_account" "this" {}

resource "aws_securityhub_standards_subscription" "nist_800_53" {
  standards_arn = "arn:aws-us-gov:securityhub:${{var.region}}::standards/nist-800-53/v/5.0.0"

  depends_on = [aws_securityhub_account.this]
}

# ---------------------------------------------------------------
# AWS Config — Compliance Monitoring (SEC04)
# ---------------------------------------------------------------
resource "aws_config_configuration_recorder" "this" {
  name     = "${{var.project_name}}-${{var.environment}}-wa-config"
  role_arn = aws_iam_role.config.arn

  recording_group {
    all_supported                 = true
    include_global_resource_types = true
  }
}

resource "aws_iam_role" "config" {
  name = "${{var.project_name}}-${{var.environment}}-wa-config-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action    = "sts:AssumeRole"
        Effect    = "Allow"
        Principal = { Service = "config.amazonaws.com" }
      }
    ]
  })

  tags = merge(var.common_tags, {
    Classification = "CUI"
  })
}

resource "aws_iam_role_policy_attachment" "config" {
  role       = aws_iam_role.config.name
  policy_arn = "arn:aws-us-gov:iam::aws:policy/service-role/AWS_ConfigRole"
}

resource "aws_s3_bucket" "config" {
  bucket        = "${{var.project_name}}-${{var.environment}}-wa-config-${{var.region}}"
  force_destroy = false

  tags = merge(var.common_tags, {
    Name           = "${{var.project_name}}-wa-config-bucket"
    Classification = "CUI"
    ManagedBy      = "icdev"
  })
}

resource "aws_s3_bucket_versioning" "config" {
  bucket = aws_s3_bucket.config.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_public_access_block" "config" {
  bucket                  = aws_s3_bucket.config.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "config" {
  bucket = aws_s3_bucket.config.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "aws:kms"
    }
  }
}

resource "aws_config_delivery_channel" "this" {
  name           = "${{var.project_name}}-${{var.environment}}-wa-config-channel"
  s3_bucket_name = aws_s3_bucket.config.id

  depends_on = [aws_config_configuration_recorder.this]
}

resource "aws_config_configuration_recorder_status" "this" {
  name       = aws_config_configuration_recorder.this.name
  is_enabled = true

  depends_on = [aws_config_delivery_channel.this]
}

# Config rules for security baseline
resource "aws_config_config_rule" "root_mfa" {
  name = "${{var.project_name}}-${{var.environment}}-root-mfa"

  source {
    owner             = "AWS"
    source_identifier = "ROOT_ACCOUNT_MFA_ENABLED"
  }

  depends_on = [aws_config_configuration_recorder_status.this]

  tags = merge(var.common_tags, {
    WA_Reference = "SEC02-BP01"
  })
}

resource "aws_config_config_rule" "s3_encryption" {
  name = "${{var.project_name}}-${{var.environment}}-s3-encryption"

  source {
    owner             = "AWS"
    source_identifier = "S3_BUCKET_SERVER_SIDE_ENCRYPTION_ENABLED"
  }

  depends_on = [aws_config_configuration_recorder_status.this]

  tags = merge(var.common_tags, {
    WA_Reference = "SEC08-BP02"
  })
}

resource "aws_config_config_rule" "ebs_encryption" {
  name = "${{var.project_name}}-${{var.environment}}-ebs-encryption"

  source {
    owner             = "AWS"
    source_identifier = "ENCRYPTED_VOLUMES"
  }

  depends_on = [aws_config_configuration_recorder_status.this]

  tags = merge(var.common_tags, {
    WA_Reference = "SEC08-BP02"
  })
}

# ---------------------------------------------------------------
# Inspector — Vulnerability Management (SEC06)
# ---------------------------------------------------------------
resource "aws_inspector2_enabler" "this" {
  account_ids    = [data.aws_caller_identity.current.account_id]
  resource_types = ["EC2", "ECR", "LAMBDA"]
}

data "aws_caller_identity" "current" {}

# ---------------------------------------------------------------
# CloudWatch Alarms — Security Monitoring (SEC04)
# ---------------------------------------------------------------
resource "aws_cloudwatch_metric_alarm" "guardduty_findings" {
  alarm_name          = "${{var.project_name}}-${{var.environment}}-guardduty-findings"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "FindingCount"
  namespace           = "AWS/GuardDuty"
  period              = 300
  statistic           = "Sum"
  threshold           = 0
  alarm_description   = "GuardDuty finding detected — investigate immediately"
  treat_missing_data  = "notBreaching"

  tags = merge(var.common_tags, {
    Classification = "CUI"
    WA_Reference   = "SEC04-BP03"
  })
}

# ---------------------------------------------------------------
# VPC Flow Logs (SEC05 — Network Visibility)
# ---------------------------------------------------------------
resource "aws_cloudwatch_log_group" "flow_logs" {
  name              = "/aws/vpc/${{var.project_name}}-${{var.environment}}/flow-logs"
  retention_in_days = 365

  tags = merge(var.common_tags, {
    Classification = "CUI"
    WA_Reference   = "SEC04-BP01"
  })
}

resource "aws_iam_role" "flow_logs" {
  name = "${{var.project_name}}-${{var.environment}}-wa-flow-log-role"

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

resource "aws_iam_role_policy" "flow_logs" {
  name = "${{var.project_name}}-${{var.environment}}-wa-flow-log-policy"
  role = aws_iam_role.flow_logs.id

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

# ---------------------------------------------------------------
# S3 Access Logging (SEC07/SEC08)
# ---------------------------------------------------------------
resource "aws_s3_bucket" "access_logs" {
  bucket        = "${{var.project_name}}-${{var.environment}}-wa-access-logs-${{var.region}}"
  force_destroy = false

  tags = merge(var.common_tags, {
    Name           = "${{var.project_name}}-wa-access-logs"
    Classification = "CUI"
    ManagedBy      = "icdev"
    WA_Reference   = "SEC07-BP02"
  })
}

resource "aws_s3_bucket_versioning" "access_logs" {
  bucket = aws_s3_bucket.access_logs.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_public_access_block" "access_logs" {
  bucket                  = aws_s3_bucket.access_logs.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "access_logs" {
  bucket = aws_s3_bucket.access_logs.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "aws:kms"
    }
  }
}
"""

WA_SECURITY_VARIABLES = """\
{{ cui_header }}
variable "project_name" {
  description = "Project identifier for resource naming"
  type        = string
}

variable "environment" {
  description = "Deployment environment (dev, staging, prod)"
  type        = string
}

variable "region" {
  description = "AWS GovCloud region"
  type        = string
  default     = "us-gov-west-1"
}

variable "common_tags" {
  description = "Common tags applied to all resources"
  type        = map(string)
  default     = {}
}
"""

WA_SECURITY_OUTPUTS = """\
{{ cui_header }}
output "guardduty_detector_id" {
  description = "GuardDuty detector ID"
  value       = aws_guardduty_detector.this.id
}

output "securityhub_arn" {
  description = "Security Hub account ARN"
  value       = aws_securityhub_account.this.arn
}

output "config_recorder_name" {
  description = "AWS Config recorder name"
  value       = aws_config_configuration_recorder.this.name
}
"""


def generate_wa_security(project_path: str, project_config: dict = None) -> list:
    """Generate WA Security Baseline Terraform module.

    Creates a single per-account security baseline module for
    AWS Well-Architected Security Pillar (SEC01-SEC11):
      - wa-security-baseline: GuardDuty, Security Hub (NIST 800-53),
        Config recorder + rules, Inspector, CloudWatch alarms,
        VPC Flow Logs, S3 access logging

    Lighter than SCCA modules — meant to be applied to every AWS account.

    Args:
        project_path: Target project directory.
        project_config: Optional project configuration dict.

    Returns:
        List of generated file paths.
    """
    ctx = {"cui_header": _cui_header()}
    files = []

    modules = [
        (
            "wa-security-baseline",
            [
                ("main.tf", WA_SECURITY_MAIN),
                ("variables.tf", WA_SECURITY_VARIABLES),
                ("outputs.tf", WA_SECURITY_OUTPUTS),
            ],
        ),
    ]

    for module_name, templates in modules:
        tf_dir = Path(project_path) / "terraform" / "modules" / module_name
        for filename, template in templates:
            p = _write(tf_dir / filename, _render(template, ctx))
            files.append(str(p))

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


def generate_for_csp(project_path: str, project_config: dict = None, csp: str = None) -> list:
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
        if config.get("scca"):
            files.extend(generate_scca(project_path, config))
        if config.get("wa_security"):
            files.extend(generate_wa_security(project_path, config))
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
        help="Comma-separated components: base,rds,ecr,vpc,bedrock_iam,agent_networking,scca,wa_security,zta_security",
    )
    parser.add_argument("--project-name", default="icdev-project", help="Project name for resource naming")
    parser.add_argument("--environment", default="dev", choices=["dev", "staging", "prod"], help="Target environment")
    parser.add_argument("--db-name", default="appdb", help="Database name for RDS module")
    parser.add_argument(
        "--csp",
        default=None,
        choices=["aws", "azure", "gcp", "oci"],
        help="Cloud service provider (auto-detected from cloud_config.yaml if omitted)",
    )
    parser.add_argument("--json", action="store_true", dest="json_output", help="JSON output")
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
        "scca": lambda: generate_scca(args.project_path, config),
        "wa_security": lambda: generate_wa_security(args.project_path, config),
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
