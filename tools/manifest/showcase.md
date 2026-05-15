# Showcase

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Showcase
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Generate App | tools/showcase/generate_app.py | Scaffold a new showcase app entry | --slug, --category, --json | App metadata JSON |
| OSINT Engine | tools/showcase/osint_engine.py | Fetch and normalize open-source intelligence feeds | --source (cve\|nvd\|rss), --fetch, --json | Records JSON |
| Synthetic Data Engine | tools/showcase/synthetic_data_engine.py | Generate realistic synthetic datasets per domain | --domain (cyber\|finance\|health\|gov), --records, --json | Dataset metadata JSON |
| Validator | tools/showcase/validator.py | Lint and validate a showcase app structure | --app, --json | Validation result JSON |
