# Auto-grader for DevOps M2: IaC Generation Agent

# ── Test: generate_vpc_block ──────────────────────────────────────────────────

vpc = generate_vpc_block("main", "10.0.0.0/16")
assert vpc is not None, "generate_vpc_block() returned None"
assert isinstance(vpc, str), f"generate_vpc_block() must return str"
assert 'resource "aws_vpc" "main"' in vpc, "VPC block must have correct resource header"
assert "10.0.0.0/16" in vpc, "VPC block must include cidr_block"
assert "main" in vpc, "VPC block must include name tag"
assert "cidr_block" in vpc, "VPC block must have cidr_block attribute"

# ── Test: generate_s3_block — no versioning ───────────────────────────────────

s3_plain = generate_s3_block("artifacts", versioning=False)
assert s3_plain is not None, "generate_s3_block() returned None"
assert isinstance(s3_plain, str), "generate_s3_block() must return str"
assert 'resource "aws_s3_bucket" "artifacts"' in s3_plain, "S3 block must have bucket resource"
assert "aws_s3_bucket_versioning" not in s3_plain, "No versioning block when versioning=False"

# ── Test: generate_s3_block — with versioning ─────────────────────────────────

s3_ver = generate_s3_block("artifacts", versioning=True)
assert 'resource "aws_s3_bucket" "artifacts"' in s3_ver, "S3 block must have bucket resource"
assert "aws_s3_bucket_versioning" in s3_ver, "Versioning block required when versioning=True"
assert "Enabled" in s3_ver, "Versioning block must set status=Enabled"
assert "aws_s3_bucket.artifacts.id" in s3_ver, "Versioning block must reference bucket id"

# ── Test: generate_provider_block ─────────────────────────────────────────────

provider_hcl = generate_provider_block("aws", "us-gov-west-1")
assert provider_hcl is not None, "generate_provider_block() returned None"
assert isinstance(provider_hcl, str), "generate_provider_block() must return str"
assert "required_providers" in provider_hcl, "Provider block must have required_providers"
assert '"aws"' in provider_hcl or "aws" in provider_hcl, "Provider block must reference 'aws'"
assert "us-gov-west-1" in provider_hcl, "Provider block must include region"
assert "hashicorp/aws" in provider_hcl, "Provider source must be hashicorp/aws"

# ── Test: TerraformGenerator.generate ────────────────────────────────────────

gen = TerraformGenerator()
result = gen.generate({
    "provider": "aws",
    "region": "us-gov-west-1",
    "resources": [
        {"type": "vpc", "name": "main", "cidr": "10.0.0.0/16"},
        {"type": "s3_bucket", "name": "artifacts", "versioning": True},
        {"type": "unknown_type", "name": "mystery"},
    ]
})

assert result is not None, "generate() returned None"
assert isinstance(result, dict), "generate() must return dict"
assert "hcl" in result, "Result must have 'hcl'"
assert "resource_count" in result, "Result must have 'resource_count'"
assert "skipped" in result, "Result must have 'skipped'"

assert isinstance(result["hcl"], str), "hcl must be str"
assert len(result["hcl"]) > 50, "hcl must be non-trivial"

assert result["resource_count"] == 2, \
    f"2 valid resources -> resource_count=2, got {result['resource_count']}"
assert isinstance(result["skipped"], list), "skipped must be list"
assert len(result["skipped"]) == 1, \
    f"1 unknown type -> skipped has 1 entry, got {result['skipped']}"
assert any("unknown_type" in str(s) for s in result["skipped"]), \
    f"skipped should mention 'unknown_type', got {result['skipped']}"

# HCL content checks
hcl = result["hcl"]
assert "aws_vpc" in hcl, "HCL must include VPC resource"
assert "aws_s3_bucket" in hcl, "HCL must include S3 bucket resource"
assert "us-gov-west-1" in hcl, "HCL must include region"
assert "10.0.0.0/16" in hcl, "HCL must include VPC cidr"

# HCL must not contain Python syntax
python_keywords = ["def ", "import ", "class ", "print(", "return "]
for kw in python_keywords:
    assert kw not in hcl, f"HCL must not contain Python syntax '{kw}'"

print("PASS: TerraformGenerator complete. generate_vpc_block + generate_s3_block + generate_provider_block + generate() all verified.")
