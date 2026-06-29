---
ontology_id: icdev:mission:m-sre-xai-01:step:3
step_class: icdev:reflect
---

# XAI Compliance: Explaining SLO Decisions

Explainability isn't just for data scientists — it's a compliance requirement. OMB M-25-21 §5(b) requires that automated decisions affecting individuals be explainable. SREs need to be able to explain: "Why did the system take this action?"

## The SRE XAI challenge

Your SLO agent made a decision to roll back a deployment at 2:47 AM. The on-call engineer needs to explain to the CTO in 15 minutes: why did it happen?

AgentSHAP + PROV-AGENT give you the answer:
- Which tool call triggered the rollback decision (AgentSHAP)
- What inputs that tool consumed (PROV-AGENT `prov:used`)
- Which agent was responsible (PROV-AGENT `prov:wasAttributedTo`)

## Your task

Design an "explanation report" template for SRE autonomous decisions. It should include: (1) the decision made and timestamp, (2) top-3 tool attributions with SHAP values, (3) the triggering metric and its value at decision time, (4) the human-readable explanation in one sentence. Write the template as a Python dict with placeholder values.
