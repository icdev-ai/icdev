---
ontology_id: icdev:mission:m-gov-03-intake:step:1
step_class: icdev:Lesson
---
# AI Governance Intake: the 7th RICOAS Readiness Dimension

RICOAS (Requirements Intake, COA & Approval System) is ICDEV's conversational requirements intake. Before a session can move on to decomposition, it is scored for **readiness**, and the score must reach `0.7` (`readiness_threshold` in `args/ricoas_config.yaml`).

## The 7 readiness dimensions

| Dimension | Weight |
|-----------|--------|
| Completeness | 0.20 |
| Clarity | 0.20 |
| Feasibility | 0.16 |
| Compliance | 0.12 |
| Testability | 0.12 |
| DevSecOps readiness | 0.10 |
| **AI governance readiness** | 0.10 |

The weights come from `ricoas.readiness_weights` and sum to 1.0. AI governance readiness was the 7th dimension added (Phase 50, D323).

## Inside the AI governance dimension

`tools/requirements/ai_governance_scorer.py` checks six components against the database and returns a weighted 0.0-1.0 score with a gap list (weights from `args/ai_governance_config.yaml`):

| Component | Weight | Satisfied when |
|-----------|--------|----------------|
| `inventory_registered` | 0.20 | The AI system is in the use case inventory |
| `model_cards_present` | 0.15 | Model cards exist for its models |
| `oversight_plan_exists` | 0.20 | An oversight plan is registered |
| `impact_assessment_done` | 0.20 | An impact assessment has been recorded |
| `caio_designated` | 0.10 | A CAIO is designated for the project |
| `transparency_frameworks_selected` | 0.15 | Transparency frameworks are selected |

The intake also listens for governance language. `args/ai_governance_config.yaml` lists detection keywords per governance pillar (`ai_inventory`, `model_documentation`, `human_oversight`, `impact_assessment`, `transparency`, `accountability`), so mentioning "automated decision" or "chatbot" during intake raises the governance questions automatically.

## Your task

Score an ICDEV platform deployment on the six governance components (met / partly met / not met) using what you built in m-gov-01 and m-gov-02. Which component is missing, what would it cost the governance score, and what one action closes it?
