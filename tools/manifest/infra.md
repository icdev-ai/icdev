# Infra IaC Generator

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Canvas Design → IaC
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| IaC Generator | tools/infra/iac_generator.py | Generate Terraform HCL from canvas design JSON. Maps: vm→aws_instance, lb→aws_lb, rds→aws_db_instance, s3→aws_s3_bucket. | generate_terraform(design_json) | HCL str |
| IaC Blueprint | tools/infra/blueprint.py | Flask blueprint: POST /infra/api/design/<design_id>/generate-iac. Loads design from DB or request body and returns HCL. | design_id (URL), design (body JSON, optional) | JSON {design_id, hcl, format} |
