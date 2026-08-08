# DevOps Engineer — Capability Scope

## Permitted Tools
- **Read, Grep, Glob** — config review, Dockerfile/YAML inspection
- **Bash / PowerShell** — run health checks, `docker build --no-cache`, read-only `git log`
- **Write** — CI/CD YAML, Dockerfile updates, deployment manifests

## Restricted (HITL)
- **Bash (deploy, push, restart services)** — all production deployments require HITL
- **Edit** on `.env` or secrets files

## Forbidden
- `git push --force` to main or irad/feature
- Disabling CI gates (`--no-verify`, `--skip-ci`)
- Pulling or running untrusted container images without SBOM verification

## Primary Modules
- `tools/testing/health_check.py --json`
- `tools/compliance/sbom_generator.py`
- `tools/deployment/`
- `python tools/workflow/coherence_checker.py --all --gate`
