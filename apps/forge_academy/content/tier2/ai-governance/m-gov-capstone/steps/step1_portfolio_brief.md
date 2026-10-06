---
ontology_id: icdev:mission:m-gov-capstone:step:1
step_class: icdev:design
---
# Governance Capstone: Full Portfolio

Your capstone: produce a complete AI governance portfolio for a fictional agency AI deployment.

## The scenario

A federal law enforcement agency is deploying "ARIA" (Automated Risk Intelligence Assistant), an AI system that analyzes case files to recommend investigation priorities. It processes PII and affects individuals' lives.

## Required artifacts

| # | Artifact | Where it lives in ICDEV |
|---|----------|-------------------------|
| 1 | **AI inventory entry**: name, purpose, risk level (`high_impact`), responsible official, oversight role, appeal mechanism | `ai_inventory_manager.py --register` |
| 2 | **Model card**: intended use, limitations, fairness considerations, metrics | `model_card_generator.py` (generated from evidence; note what is missing) |
| 3 | **Oversight plan**: CAIO designation, shutdown authority, appeals process | `accountability_manager.py --designate-caio` and `--register-oversight` |
| 4 | **Ethics review schedule**: frequency, metrics, conditions that trigger an unscheduled review | `accountability_manager.py --schedule-reassessment` and `--submit-ethics-review` |
| 5 | **Governance readiness score**: the six components from m-gov-03, the 2 weakest and their remediation | `tools/requirements/ai_governance_scorer.py` |

## Success criteria

- All 5 artifacts are recorded under the **same `--project-id`** and name the same AI system ("ARIA")
- The inventory entry is `high_impact`
- The oversight plan includes an appeals process
- The reassessment is scheduled `quarterly`

## Your task

Draft all 5 artifacts, either as the commands you would run or as the text you would put in each record. Keep them together; this portfolio is the evidence you would hand a reviewer.
