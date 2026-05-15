---
ontology_id: icdev:mission:m-devops-02-iac-generation:step:1
step_class: icdev:Lesson
---

# IaC Generation Agent

Infrastructure as Code (IaC) is the foundation of repeatable, auditable deployments. Writing Terraform from scratch for every project is slow and error-prone. In this mission you'll build an IaC generation agent that produces valid Terraform configuration from a structured spec.

## What You'll Build

A `TerraformGenerator` that converts infrastructure specs into Terraform HCL:

```python
gen = TerraformGenerator()
result = gen.generate({
    "provider": "aws",
    "resources": [
        {"type": "vpc", "name": "main", "cidr": "10.0.0.0/16"},
        {"type": "s3_bucket", "name": "artifacts", "versioning": True},
    ],
    "region": "us-gov-west-1"
})
```

## Terraform Block Structure

Each resource type follows a specific HCL pattern:

**VPC:**
```hcl
resource "aws_vpc" "main" {
  cidr_block = "10.0.0.0/16"
  tags = { Name = "main" }
}
```

**S3 Bucket:**
```hcl
resource "aws_s3_bucket" "artifacts" {
  bucket = "artifacts"
}
resource "aws_s3_bucket_versioning" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  versioning_configuration { status = "Enabled" }
}
```

## Success Criteria

- `generate_vpc_block()` returns valid HCL for a VPC resource
- `generate_s3_block()` returns HCL for bucket + optional versioning block
- `generate_provider_block()` returns provider + region configuration
- `TerraformGenerator.generate()` assembles all blocks into a complete .tf file
- Output contains no Python — only valid HCL syntax
- Unknown resource types are skipped with a warning (no crash)
