
"""
Tier 2 DevOps Mission 2: IaC Generation Agent
Goal: Build a TerraformGenerator that converts infrastructure specs into Terraform HCL.
"""


# ── Step 1: VPC Block Generator ───────────────────────────────────────────────

def generate_vpc_block(name: str, cidr: str) -> str:
    """TODO: Generate a Terraform aws_vpc resource block.

    Return HCL string in this format:
    resource "aws_vpc" "{name}" {
      cidr_block = "{cidr}"
      tags = {
        Name = "{name}"
      }
    }
    """
    # YOUR CODE HERE
    pass


# ── Step 2: S3 Block Generator ────────────────────────────────────────────────

def generate_s3_block(name: str, versioning: bool = False) -> str:
    """TODO: Generate Terraform aws_s3_bucket resource block(s).

    Always include the base bucket block:
    resource "aws_s3_bucket" "{name}" {
      bucket = "{name}"
    }

    If versioning is True, append a versioning block:
    resource "aws_s3_bucket_versioning" "{name}" {
      bucket = aws_s3_bucket.{name}.id
      versioning_configuration {
        status = "Enabled"
      }
    }

    Return both blocks joined with a newline if versioning=True, else just the bucket block.
    """
    # YOUR CODE HERE
    pass


# ── Step 3: Provider Block Generator ──────────────────────────────────────────

def generate_provider_block(provider: str, region: str) -> str:
    """TODO: Generate a Terraform provider + terraform required_providers block.

    Return:
    terraform {
      required_providers {
        {provider} = {
          source  = "hashicorp/{provider}"
          version = "~> 5.0"
        }
      }
    }

    provider "{provider}" {
      region = "{region}"
    }
    """
    # YOUR CODE HERE
    pass


# ── Step 4: TerraformGenerator ────────────────────────────────────────────────

class TerraformGenerator:
    """Assembles Terraform HCL from a structured infrastructure spec."""

    SUPPORTED_TYPES = {"vpc", "s3_bucket"}

    def generate(self, spec: dict) -> dict:
        """TODO: Generate a complete Terraform configuration from a spec.

        Spec format:
        {
            "provider": "aws",
            "region": "us-gov-west-1",
            "resources": [
                {"type": "vpc", "name": "main", "cidr": "10.0.0.0/16"},
                {"type": "s3_bucket", "name": "artifacts", "versioning": True},
            ]
        }

        Steps:
        1. Generate provider block using generate_provider_block()
        2. For each resource in spec["resources"]:
           - If type == "vpc": use generate_vpc_block(name, cidr)
           - If type == "s3_bucket": use generate_s3_block(name, versioning)
           - If type unknown: add to skipped list (do not crash)
        3. Join all blocks with "\n\n"
        4. Return:
        {
            "hcl": full HCL string,
            "resource_count": number of successfully generated resources,
            "skipped": [list of unknown resource type names],
        }
        """
        # YOUR CODE HERE
        pass


# Test
if __name__ == "__main__":
    gen = TerraformGenerator()
    result = gen.generate({
        "provider": "aws",
        "region": "us-gov-west-1",
        "resources": [
            {"type": "vpc", "name": "main", "cidr": "10.0.0.0/16"},
            {"type": "s3_bucket", "name": "artifacts", "versioning": True},
            {"type": "unknown_resource", "name": "test"},
        ]
    })
    print(f"Generated {result['resource_count']} resources")
    print(f"Skipped: {result['skipped']}")
    print(result["hcl"])
