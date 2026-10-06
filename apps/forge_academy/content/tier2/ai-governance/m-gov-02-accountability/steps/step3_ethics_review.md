---
ontology_id: icdev:mission:m-gov-02-accountability:step:3
step_class: icdev:reflect
---
# Ethics Review Schedule

AI accountability does not stop at deployment. It needs periodic reviews that ask: "Is this system still doing what we intended? Is it fair? Has it drifted?"

## How ICDEV records it

- **Reviews**: `accountability_manager.py --submit-ethics-review --review-type <type> --findings "..." --recommendation "..."`, where `<type>` is one of `bias_testing_policy`, `impact_assessment`, `ethics_framework`, `legal_compliance`, `pre_deployment`, `annual_review`, `other`
- **Schedule**: `accountability_manager.py --schedule-reassessment --ai-system "<name>" --frequency <f>`, where `<f>` is one of `quarterly`, `semi_annual`, `annual`, `biennial`. Overdue reassessments show up in `GET /api/ai-accountability/overdue`

## A reasonable cadence by risk

| Risk | Review frequency |
|------|------------------|
| Minimal risk | `annual` |
| High-impact | `semi_annual` |
| High-impact, affecting individuals' rights or safety | `quarterly`, plus an unscheduled review after any incident |

(This cadence is a recommendation; ICDEV enforces whatever frequency you schedule.)

## Reflect

Design an ethics review checklist for one of your AI systems: (1) a fairness check, naming the demographic parity metric you would measure; (2) a confabulation check, saying how you would detect and measure hallucinations; (3) a drift check, naming the behavioral signals that show the model has drifted from its intended use. Make each criterion specific and measurable, decide which `--review-type` and `--frequency` you would record it under, then press Continue.
