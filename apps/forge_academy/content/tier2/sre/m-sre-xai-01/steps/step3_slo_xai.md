---
ontology_id: icdev:mission:m-sre-xai-01:step:3
step_class: icdev:reflect
---

# XAI Compliance: Explaining SLO Decisions

Explainability isn't just for data scientists; it is a compliance requirement. OMB M-25-21 sets minimum practices for high-impact AI. They include human oversight, transparency and notice, and a way to appeal or seek redress. ICDEV's assessor tracks them as `M25-OVR-1` to `M25-OVR-3` in `context/compliance/omb_m25_21_high_impact_ai.json`. None of them can be met if nobody can answer "Why did the system take this action?"

## The SRE XAI challenge

Your SLO agent rolled back a deployment at 2:47 AM. The on-call engineer has 15 minutes to explain to the CTO why it happened.

AgentSHAP and PROV-AGENT give you the answer:
- Which tool call drove the rollback decision (AgentSHAP `attributions`)
- What inputs that tool consumed (PROV `used`)
- Which agent was responsible (PROV `wasAttributedTo`)

## Your task

Design an "explanation report" template for autonomous SRE decisions. It should include:
1. the decision made and its timestamp
2. the top-3 tool attributions with their Shapley values
3. the triggering metric and its value at decision time
4. a one-sentence human-readable explanation

Sketch it as a Python dict with placeholder values, in your notes or a scratch file. This step has no form fields. Click **I Understand → Continue** when your template covers all four parts.
