# Declared Decisions (icx-cnf-02)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Decisions a person takes, through declared templates

| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Declared Decision Templates | tools/workflow_hitl/declared.py | Loads `args/workflow_hitl_templates/*.yaml` (one per decision a person takes in Cortex and ACE: `decides:` names the route or tool, one manual stage held by a role with `holders`), seeds them into `wf_templates`, and runs each decision route through the manual stage: `take_step` (a holder decides in the request), `propose` / `decide` / `spend` (a service key or non-holder proposes; a holder decides at `/decisions`; the machine re-posts once with the step id). Ledger `wf_decision_steps` is append-only; a step that cannot be recorded is refused and its effect does not run | --seed, --list, --status, --json | JSON |
| Decisions page | tools/workflow_hitl/decisions_blueprint.py | `/decisions`: pending and recent declared steps; a holder decides in one action. JSON at `/decisions/api/steps`, `/decisions/api/templates`, `POST /decisions/api/steps/<id>/decide` | HTTP | HTML / JSON |
