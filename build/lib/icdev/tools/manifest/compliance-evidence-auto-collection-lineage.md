# Compliance Evidence Auto-Collection + Lineage (Phase 56)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Compliance Evidence Auto-Collection + Lineage (Phase 56)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Evidence Collector | tools/compliance/evidence_collector.py | Universal evidence auto-collection across 14 compliance frameworks. DB query + file scan. | --project-id, --project-dir, --framework, --freshness, --list-frameworks, --json | Evidence manifest |
| Evidence Chain | tools/compliance/evidence_chain.py | Continuous Compliance Evidence Chain — connects PDC/NDC/SDC audit trails into OSCAL 1.1.2-aligned evidence timeline. Stores snapshots in compliance_evidence_chain table. Gate: fails if no assessment evidence or >10 unresolved findings. | --project-id, --since (24h/7d), --json, --gate, --export-oscal, --output | Evidence chain manifest + OSCAL Assessment Results |
| Evidence API | tools/dashboard/api/evidence.py | Blueprint: evidence stats, collect, freshness check, framework list | /api/evidence/* | REST endpoints |
| Evidence Page | tools/dashboard/templates/evidence.html | Dashboard: evidence inventory, freshness status, collect trigger | (template) | HTML page |
| Lineage API | tools/dashboard/api/lineage.py | Blueprint: artifact lineage DAG (digital thread + provenance + audit trail + SBOM), stats | /api/lineage/* | REST endpoints |
| Lineage Page | tools/dashboard/templates/lineage.html | Dashboard: SVG DAG artifact visualization, color-coded by source | (template) | HTML page |

