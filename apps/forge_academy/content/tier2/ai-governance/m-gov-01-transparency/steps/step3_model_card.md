---
ontology_id: icdev:mission:m-gov-01-transparency:step:3
step_class: icdev:configure
---
# Generate a Model Card

A model card documents a specific AI model: intended use, out-of-scope uses, evaluation factors, metrics, training data, ethical considerations, and caveats and limitations.

## ICDEV generates the card from evidence

You do not type a model card into ICDEV. `tools/compliance/model_card_generator.py` assembles it (Google Model Cards format, OMB M-26-04) from what the platform already records: the AI BOM, AI telemetry, and any XAI / SHAP assessments. You give it the project and the model:

```bash
python tools/compliance/model_card_generator.py --project-id <project-id> --model-name claude-sonnet --json
python tools/compliance/model_card_generator.py --project-id <project-id> --list --json
```

or `POST /api/ai-transparency/model-card` with `{"project_id": "...", "model_name": "..."}` (requires a compliance-write role), or the MCP tool `model_card_generate`. Generated cards are listed by `GET /api/ai-transparency/model-cards`.

The generated card's sections: `model_details`, `intended_use`, `factors`, `metrics`, `training_data`, `ethical_considerations`, `caveats_and_limitations`.

## Why generation, not a form

A hand-written card says what someone believed when they wrote it. A generated card says what the evidence shows, and it goes thin exactly where the evidence is thin. If `metrics` reports almost no telemetry, that is a finding, not a formatting problem.

## Your task

Pick one model behind the systems you inventoried. Before generating anything, write down what you expect each of the seven sections to say. Then note which sections you expect to be **thin** because the evidence (AI BOM entry, telemetry, XAI assessment) does not exist yet, and what you would have to do to fill each one. Press **Configure** to record that you completed the exercise.
