# EU AI Act + Platform One (Phase 57)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## EU AI Act + Platform One (Phase 57)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| EU AI Act Classifier | tools/compliance/eu_ai_act_classifier.py | BaseAssessor for EU AI Act (Regulation 2024/1689) risk classification. 12 requirements via ISO 27001 bridge. | --project-id, --json, --gate | Classification JSON |
| EU AI Act Catalog | context/compliance/eu_ai_act_annex_iii.json | 12 high-risk requirements, 8 Annex III categories, 4 risk levels with NIST crosswalk | (catalog) | JSON catalog |
| Iron Bank Generator | tools/infra/ironbank_metadata_generator.py | Generate Platform One / Iron Bank hardening_manifest.yaml and container_approval.json for DoD Iron Bank submission. Language auto-detection. | --project-id, --project-dir, --output-dir, --generate, --validate, --json | Hardening manifest + approval record |

