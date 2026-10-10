# CI/CD Pipeline

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## CI/CD Pipeline
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Pipeline Config Generator | tools/ci/pipeline_config_generator.py | Generate GitHub Actions/GitLab CI from icdev.yaml (D192) | --dir, --platform, --write, --dry-run, --json | YAML config + metadata |
| floci IaC Gate | tools/ci/floci_iac_gate.py | Opt-in: does tools/infra_canvas/preapply_gate.py's verdict match what a real AWS API surface (floci) accepts? plan -> gate -> apply over two fixture canvases. NEVER a required check (flx-ci-01) | --image, --fixture, --no-start, --artifacts, --out, --json | Report JSON + plan/gate/apply artifacts; exit 0 clean / 1 finding / 2 could-not-run |
