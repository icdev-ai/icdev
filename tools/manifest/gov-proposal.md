# GovProposal Subsystem

> Migrated from standalone GovProposal app into ICDEV core (2026-05-16).
> Enabled via `ICDEV_GOV_PROPOSAL_ENABLED=true` in `.env`.

## Package Location

| Canonical | `icdev/tools/gov_proposal/` |
|-----------|----------------------------|
| Blueprint | `icdev/tools/gov_proposal/routes.py` — 66 routes |
| DB init   | `icdev/tools/gov_proposal/db_init.py` |
| DB file   | `data/govproposal.db` (SQLite, 28 tables) |
| Templates | `tools/dashboard/templates/gov_proposal/` |
| Context   | `context/far_dfars/`, `context/naics/`, `context/scg/` |
| Hardprompts | `hardprompts/proposal/`, `hardprompts/cag/` |

## Domain Tools (also in icdev/tools/)

| Module | Files | Description |
|--------|-------|-------------|
| `icdev/tools/rfx/` | 9 | RFx AI engine — document ingest, RAG, requirement extraction, fine-tuning, HITL proposal generation |
| `icdev/tools/cag/` | 5 | Classification Aggregation Guard — data tagging, exposure register, rules engine |
| `icdev/tools/crm/` | 3 | CRM — contact manager, vendor assessor |
| `icdev/tools/erp/` | 5 | ERP — employee manager, LCAT manager, LinkedIn importer, skills tracker |
| `icdev/tools/delivery/` | 1 | Delivery — contract manager (42KB, EVM-aware) |
| `icdev/tools/capture/` | 6 | Capture — black-hat review, customer intel, IDIQ manager, teaming engine, win-theme generator |
| `icdev/tools/competitive/` | 6 | Competitive — FPDS analyzer, price-to-win, recompete tracker, set-aside analyzer |
| `icdev/tools/proposal/` | 6 | Proposal — compliance matrix, content drafter, proposal assembler, SBIR manager, section parser |

## Routes (mounted at `/gov-proposal`)

| Path | Description |
|------|-------------|
| `/gov-proposal/` | Home dashboard — pipeline overview |
| `/gov-proposal/opportunities` | Opportunity listing with fit scores |
| `/gov-proposal/opportunities/<id>` | Opportunity detail + scorecard |
| `/gov-proposal/proposals` | Proposal listing |
| `/gov-proposal/proposals/kanban` | Proposal Kanban board |
| `/gov-proposal/proposals/<id>` | Proposal detail: sections, reviews, CAG, compliance |
| `/gov-proposal/knowledge` | Knowledge base browser |
| `/gov-proposal/cag` | CAG alert monitor |
| `/gov-proposal/competitors` | Competitive intelligence dashboard |
| `/gov-proposal/analytics` | Win/loss analytics |
| `/gov-proposal/team` | ERP: employee directory with skills/LCATs |
| `/gov-proposal/lcat-rates` | ERP: LCAT rate card |
| `/gov-proposal/crm` | CRM contact list |
| `/gov-proposal/crm/<id>` | Contact detail with interaction history |
| `/gov-proposal/pricing` | Pricing calculator |
| `/gov-proposal/rfx/documents` | RFx document upload (RFI/RFP + corpus) |
| `/gov-proposal/rfx/requirements` | Requirements view with compliance status |
| `/gov-proposal/rfx/exclusions` | Sensitive term masking |
| `/gov-proposal/rfx/research` | Web + government source research panel |
| `/gov-proposal/rfx/fine-tuning` | Unsloth fine-tuning job dashboard |
| `/gov-proposal/ai-proposals` | AI proposal dashboard |
| `/gov-proposal/contracts` | Contract list (CDRLs, EVM) |
| `/gov-proposal/sbir` | SBIR/STTR proposal listing |
| `/gov-proposal/idiq` | IDIQ vehicle listing |
| `/gov-proposal/recompetes` | Recompete tracking |
| + 41 API endpoints | `/gov-proposal/api/*` |

## Registration

Registered in `tools/dashboard/app.py` at line ~3474:
```python
if _GOV_PROPOSAL_ENABLED:
    from icdev.tools.gov_proposal import gov_proposal_bp
    from icdev.tools.gov_proposal.db_init import init_db
    init_db()
    app.register_blueprint(gov_proposal_bp)
```

## .env Toggle

```
ICDEV_GOV_PROPOSAL_ENABLED=true    # default on for developer PC
GOVPROPOSAL_DB_PATH=/path/to/govproposal.db  # optional override
GOVPROPOSAL_API_KEY=...            # optional API key gate for /api/* routes
GOVPROPOSAL_CUI_BANNER=CUI // SP-PROPIN
```
