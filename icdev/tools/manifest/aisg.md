# AISG — AI Strategy Guide Tools

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## AISG — AI Strategy Guide Tools

| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| AISG Blueprint | tools/aisg/blueprint.py | Flask Blueprint: GET /api/explain/<event_id> — fetch audit event and return plain-English explanation | event_id (int) | JSON explanation |
| AISG Explain Translator | tools/aisg/explain_translator.py | Rule-based translator: maps audit event_type + event_data to human-readable explanation string | event_type, event_data dict | Explanation string |
| AISG Wizard | tools/aisg/wizard.py | AISG setup wizard: maps 5 answers (use_case, compliance_level, tech_stack, ai_maturity, cloud_provider) to recommended goals, skills, and sprint tasks | answers dict | {recommended_goals, recommended_skills, sprint_plan_tasks} |
| AISG Constants | tools/aisg/constants.py | Canonical ROI action types and time-savings rates for the AISG wizard | (import) | ROI_ACTION_TYPES, TIME_SAVINGS_RATES |
| Visual Agent Builder | tools/aisg/visual_agent_builder.py | FORGE goal generator: reads aisg_agent_designs.canvas_json (node list), topologically sorts on next_node_id, renders goals/custom_<design_id>.md, persists to generated_goal_md. No LLM. | design_id: str | Generated FORGE goal Markdown string |
