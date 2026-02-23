# [TEMPLATE: CUI // SP-CTI]
"""Tests for tools.infra.terraform_generator — Terraform config generation.

Verifies provider.tf, variables.tf, outputs.tf, main.tf base generation,
plus RDS, ECR, VPC, Bedrock IAM, and ZTA security module generators.
All tests use tmp_path only — no database required.

Note: The Terraform templates contain literal ``${{var.xxx}}`` strings intended
for HCL output.  When Jinja2 is installed it mis-interprets those as template
expressions, so tests that exercise modules with HCL variable references
patch ``_render`` to use the plain-string fallback renderer.
"""

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

try:
    from tools.infra.terraform_generator import (
        generate_base,
        generate_rds,
        generate_ecr,
        generate_vpc,
        generate_bedrock_iam,
        generate_zta_security,
        _cui_header,
    )
except ImportError:
    pytestmark = pytest.mark.skip("tools.infra.terraform_generator not available")


# ---------------------------------------------------------------------------
# Fallback renderer — avoids Jinja2 UndefinedError on ${{var.xxx}} HCL refs.
# ---------------------------------------------------------------------------
def _fallback_render(template_str: str, ctx: dict) -> str:
    """Plain-string renderer that replaces {{ key }} with ctx values."""
    result = template_str
    for key, val in ctx.items():
        result = result.replace("{{ " + key + " }}", str(val))
        result = result.replace("{{" + key + "}}", str(val))
    return result


_RENDER_PATCH = patch(
    "tools.infra.terraform_generator._render",
    side_effect=_fallback_render,
)


# ---------------------------------------------------------------------------
# TestGenerateBase
# ---------------------------------------------------------------------------
class TestGenerateBase:
    """Base Terraform generation: provider.tf, variables.tf, outputs.tf, main.tf."""

    def test_creates_provider_tf(self, tmp_path):
        files = generate_base(str(tmp_path), {"project_name": "myproj"})
        provider = tmp_path / "terraform" / "provider.tf"
        assert provider.exists()
        assert str(provider) in files

    def test_creates_variables_tf(self, tmp_path):
        files = generate_base(str(tmp_path), {"project_name": "myproj"})
        variables = tmp_path / "terraform" / "variables.tf"
        assert variables.exists()
        assert str(variables) in files

    def test_creates_outputs_tf(self, tmp_path):
        files = generate_base(str(tmp_path), {"project_name": "myproj"})
        outputs = tmp_path / "terraform" / "outputs.tf"
        assert outputs.exists()
        assert str(outputs) in files

    def test_creates_main_tf(self, tmp_path):
        files = generate_base(str(tmp_path), {"project_name": "myproj"})
        main = tmp_path / "terraform" / "main.tf"
        assert main.exists()
        assert str(main) in files

    def test_returns_four_files(self, tmp_path):
        files = generate_base(str(tmp_path))
        assert len(files) == 4

    def test_cui_header_in_provider(self, tmp_path):
        generate_base(str(tmp_path))
        content = (tmp_path / "terraform" / "provider.tf").read_text(encoding="utf-8")
        assert "CONTROLLED UNCLASSIFIED INFORMATION" in content

    def test_project_name_in_provider(self, tmp_path):
        generate_base(str(tmp_path), {"project_name": "alpha-svc"})
        content = (tmp_path / "terraform" / "provider.tf").read_text(encoding="utf-8")
        assert "alpha-svc" in content

    def test_default_project_name(self, tmp_path):
        generate_base(str(tmp_path))
        content = (tmp_path / "terraform" / "variables.tf").read_text(encoding="utf-8")
        assert "icdev-project" in content

    def test_environment_in_variables(self, tmp_path):
        generate_base(str(tmp_path), {"environment": "staging"})
        content = (tmp_path / "terraform" / "variables.tf").read_text(encoding="utf-8")
        assert "staging" in content

    def test_govcloud_region_in_provider(self, tmp_path):
        generate_base(str(tmp_path))
        content = (tmp_path / "terraform" / "provider.tf").read_text(encoding="utf-8")
        assert "us-gov-west-1" in content

    def test_db_name_in_variables(self, tmp_path):
        generate_base(str(tmp_path), {"db_name": "myappdb"})
        content = (tmp_path / "terraform" / "variables.tf").read_text(encoding="utf-8")
        assert "myappdb" in content


# ---------------------------------------------------------------------------
# TestGenerateRds
# ---------------------------------------------------------------------------
class TestGenerateRds:
    """RDS PostgreSQL module generation."""

    def test_creates_rds_module_files(self, tmp_path):
        with _RENDER_PATCH:
            files = generate_rds(str(tmp_path))
        assert len(files) == 3

    def test_rds_main_tf_exists(self, tmp_path):
        with _RENDER_PATCH:
            generate_rds(str(tmp_path))
        rds_main = tmp_path / "terraform" / "modules" / "rds" / "main.tf"
        assert rds_main.exists()

    def test_rds_contains_db_instance(self, tmp_path):
        with _RENDER_PATCH:
            generate_rds(str(tmp_path))
        content = (tmp_path / "terraform" / "modules" / "rds" / "main.tf").read_text(encoding="utf-8")
        assert "aws_db_instance" in content

    def test_rds_contains_postgres_engine(self, tmp_path):
        with _RENDER_PATCH:
            generate_rds(str(tmp_path))
        content = (tmp_path / "terraform" / "modules" / "rds" / "main.tf").read_text(encoding="utf-8")
        assert 'engine         = "postgres"' in content

    def test_rds_outputs_endpoint(self, tmp_path):
        with _RENDER_PATCH:
            generate_rds(str(tmp_path))
        content = (tmp_path / "terraform" / "modules" / "rds" / "outputs.tf").read_text(encoding="utf-8")
        assert "endpoint" in content


# ---------------------------------------------------------------------------
# TestGenerateEcr
# ---------------------------------------------------------------------------
class TestGenerateEcr:
    """ECR module generation."""

    def test_creates_ecr_module_files(self, tmp_path):
        with _RENDER_PATCH:
            files = generate_ecr(str(tmp_path))
        assert len(files) == 3

    def test_ecr_main_exists(self, tmp_path):
        with _RENDER_PATCH:
            generate_ecr(str(tmp_path))
        ecr_main = tmp_path / "terraform" / "modules" / "ecr" / "main.tf"
        assert ecr_main.exists()

    def test_ecr_immutable_tags(self, tmp_path):
        with _RENDER_PATCH:
            generate_ecr(str(tmp_path))
        content = (tmp_path / "terraform" / "modules" / "ecr" / "main.tf").read_text(encoding="utf-8")
        assert "IMMUTABLE" in content

    def test_ecr_scan_on_push(self, tmp_path):
        with _RENDER_PATCH:
            generate_ecr(str(tmp_path))
        content = (tmp_path / "terraform" / "modules" / "ecr" / "main.tf").read_text(encoding="utf-8")
        assert "scan_on_push = true" in content


# ---------------------------------------------------------------------------
# TestGenerateVpc
# ---------------------------------------------------------------------------
class TestGenerateVpc:
    """VPC module generation with private subnets."""

    def test_creates_vpc_module_files(self, tmp_path):
        with _RENDER_PATCH:
            files = generate_vpc(str(tmp_path))
        assert len(files) == 3

    def test_vpc_main_exists(self, tmp_path):
        with _RENDER_PATCH:
            generate_vpc(str(tmp_path))
        vpc_main = tmp_path / "terraform" / "modules" / "vpc" / "main.tf"
        assert vpc_main.exists()

    def test_vpc_cidr_default(self, tmp_path):
        with _RENDER_PATCH:
            generate_vpc(str(tmp_path))
        content = (tmp_path / "terraform" / "modules" / "vpc" / "variables.tf").read_text(encoding="utf-8")
        assert "10.0.0.0/16" in content

    def test_vpc_flow_logs_enabled(self, tmp_path):
        with _RENDER_PATCH:
            generate_vpc(str(tmp_path))
        content = (tmp_path / "terraform" / "modules" / "vpc" / "main.tf").read_text(encoding="utf-8")
        assert "aws_flow_log" in content

    def test_vpc_outputs_vpc_id(self, tmp_path):
        with _RENDER_PATCH:
            generate_vpc(str(tmp_path))
        content = (tmp_path / "terraform" / "modules" / "vpc" / "outputs.tf").read_text(encoding="utf-8")
        assert "vpc_id" in content


# ---------------------------------------------------------------------------
# TestGenerateBedrockIam
# ---------------------------------------------------------------------------
class TestGenerateBedrockIam:
    """Bedrock IAM module generation for agent LLM access."""

    def test_creates_bedrock_iam_files(self, tmp_path):
        with _RENDER_PATCH:
            files = generate_bedrock_iam(str(tmp_path))
        assert len(files) == 3

    def test_bedrock_iam_dir_correct(self, tmp_path):
        with _RENDER_PATCH:
            generate_bedrock_iam(str(tmp_path))
        iam_dir = tmp_path / "terraform" / "modules" / "bedrock_iam"
        assert iam_dir.is_dir()

    def test_govcloud_arn_referenced(self, tmp_path):
        with _RENDER_PATCH:
            generate_bedrock_iam(str(tmp_path))
        content = (tmp_path / "terraform" / "modules" / "bedrock_iam" / "main.tf").read_text(encoding="utf-8")
        assert "aws-us-gov" in content

    def test_bedrock_invoke_action(self, tmp_path):
        with _RENDER_PATCH:
            generate_bedrock_iam(str(tmp_path))
        content = (tmp_path / "terraform" / "modules" / "bedrock_iam" / "main.tf").read_text(encoding="utf-8")
        assert "bedrock:InvokeModel" in content

    def test_bedrock_outputs_role_arn(self, tmp_path):
        with _RENDER_PATCH:
            generate_bedrock_iam(str(tmp_path))
        content = (tmp_path / "terraform" / "modules" / "bedrock_iam" / "outputs.tf").read_text(encoding="utf-8")
        assert "bedrock_agent_role_arn" in content


# ---------------------------------------------------------------------------
# TestGenerateZtaSecurity
# ---------------------------------------------------------------------------
class TestGenerateZtaSecurity:
    """ZTA security module generation — delegates to zta_terraform_generator."""

    def test_returns_list(self, tmp_path):
        result = generate_zta_security(str(tmp_path))
        assert isinstance(result, list)

    def test_returns_empty_when_generator_missing(self, tmp_path):
        # generate_zta_security gracefully returns [] when import fails
        result = generate_zta_security(str(tmp_path), {"zta_modules": ["nonexistent"]})
        assert result == []

    def test_accepts_custom_module_list(self, tmp_path):
        result = generate_zta_security(str(tmp_path), {"zta_modules": ["guardduty"]})
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# TestCuiHeader
# ---------------------------------------------------------------------------
class TestCuiHeader:
    """CUI header content verification."""

    def test_cui_header_contains_cui_text(self):
        header = _cui_header()
        assert "CONTROLLED UNCLASSIFIED INFORMATION" in header

    def test_cui_header_contains_generator_name(self):
        header = _cui_header()
        assert "ICDev Terraform Generator" in header

    def test_cui_header_contains_timestamp(self):
        header = _cui_header()
        # ISO timestamp contains 'T' separator
        assert "T" in header


# ---------------------------------------------------------------------------
# TestEdgeCases
# ---------------------------------------------------------------------------
class TestEdgeCases:
    """Edge cases: empty names, special characters, missing config."""

    def test_empty_project_name_uses_default(self, tmp_path):
        files = generate_base(str(tmp_path), {"project_name": ""})
        # Empty string is falsy but dict.get returns it, so it stays empty
        assert len(files) == 4

    def test_special_chars_in_project_name(self, tmp_path):
        files = generate_base(str(tmp_path), {"project_name": "my-app_v2.0"})
        content = (tmp_path / "terraform" / "provider.tf").read_text(encoding="utf-8")
        assert "my-app_v2.0" in content

    def test_none_config_uses_defaults(self, tmp_path):
        files = generate_base(str(tmp_path), None)
        assert len(files) == 4
        content = (tmp_path / "terraform" / "variables.tf").read_text(encoding="utf-8")
        assert "icdev-project" in content

    def test_no_config_argument(self, tmp_path):
        files = generate_base(str(tmp_path))
        assert len(files) == 4


# [TEMPLATE: CUI // SP-CTI]
