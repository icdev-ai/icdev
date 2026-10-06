---
ontology_id: icdev:mission:m-gov-03-intake:step:2
step_class: icdev:configure
---
# Governance Advisory Integration

The ICDEV chat (`/chat`) surfaces AI governance obligations while you talk, in two ways:

- **Advisory messages.** After assistant responses, chat checks the conversation for AI keywords (`chat_governance.ai_keywords` in `args/ai_governance_config.yaml`: "automated decision", "chatbot", "llm", "predictive model", …). When they appear, an **[AI Governance Advisory]** message is injected. Advisories follow a priority order (`oversight_plan_missing`, `impact_assessment_missing`, `model_card_missing`, `caio_not_designated`, `fairness_not_assessed`, `reassessment_overdue`) and the same advisory is not repeated within 5 turns (`advisory_cooldown_turns`).
- **The Governance sidebar.** The **Governance** button in the chat header toggles a sidebar with live counts from `/api/ai-transparency/stats` and `/api/ai-accountability/stats`: AI systems, model cards, oversight plans and so on.

## Your task

In `/chat`, describe a fictional AI deployment, for example: "We're deploying an AI system to assist case workers in benefits eligibility determination; it makes automated decisions about applications." Then:

1. Does an **[AI Governance Advisory]** appear? Which obligation does it lead with, and does that match the priority order above?
2. Open the **Governance** sidebar. Which counts are zero for your project?
3. Is the advisory accurate for this scenario (a high-impact system that affects individuals)? What did it miss?

Press **Configure** to record that you completed the exercise.
