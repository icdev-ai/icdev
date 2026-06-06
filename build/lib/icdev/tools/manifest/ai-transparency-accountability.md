# AI Transparency & Accountability (Phase 48-49)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## AI Transparency & Accountability (Phase 48-49)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Accountability Manager | tools/compliance/accountability_manager.py | AI oversight plans, CAIO, appeals, ethics (D316-D321) | --summary, --register-oversight, --designate-caio, --json | Accountability records |
| AI Accountability Audit | tools/compliance/ai_accountability_audit.py | Cross-framework accountability audit (D316-D321) | --project-id, --json | Audit report |
| AI Impact Assessor | tools/compliance/ai_impact_assessor.py | Algorithmic impact assessment (D320) | --project-id, --ai-system, --json | Impact assessment |
| AI Incident Response | tools/compliance/ai_incident_response.py | AI incident logging and stats (D318) | --log, --stats, --project-id, --json | Incident records |
| AI Inventory Manager | tools/compliance/ai_inventory_manager.py | OMB M-25-21 AI system inventory (D312) | --register, --list, --export, --json | Inventory records |
| AI Reassessment Scheduler | tools/compliance/ai_reassessment_scheduler.py | Reassessment schedule manager (D316) | --create, --overdue, --json | Schedule records |
| AI Transparency Audit | tools/compliance/ai_transparency_audit.py | Cross-framework transparency audit (D307-D315) | --project-id, --json, --human | Audit report |
| Classification Resolver | tools/compliance/classification_resolver.py | Dynamic classification resolution per project | (library) | Classification level |
| Compliance Exporter [DEPRECATED] | tools/compliance/compliance_exporter.py | Multi-format compliance artifact export | --project-id, --format, --json | Exported artifacts |
| Fairness Assessor | tools/compliance/fairness_assessor.py | AI fairness compliance assessment (D311) | --project-id, --gate, --json | Fairness assessment |
| GAO AI Assessor | tools/compliance/gao_ai_assessor.py | GAO-21-519SP AI accountability assessment | --project-id, --json | Assessment results |
| GAO Evidence Builder | tools/compliance/gao_evidence_builder.py | GAO evidence collection from ICDEV™ data (D313) | --project-id, --json | Evidence bundle |
| Model Card Generator | tools/compliance/model_card_generator.py | Google-format model cards (D308) | --project-id, --model-name, --json | Model card |
| Narrative Generator | tools/compliance/narrative_generator.py | Compliance narrative workflow (F4) | --project-id, --batch, --pending, --json | Narrative drafts |

