---
ontology_id: icdev:mission:m-gov-01-transparency:step:1
step_class: icdev:Lesson
---
# AI Transparency: OMB M-25-21 and NIST AI 600-1

Federal agencies deploying AI are required to be transparent about their AI systems. Two documents drive most of that work.

## OMB M-25-21 (April 2025)

*Accelerating Federal Use of AI through Innovation, Governance, and Public Trust.* Among other things it requires agencies to:

- Designate a **Chief AI Officer (CAIO)** and run an AI governance board
- Maintain and publish an **AI use case inventory**, updated at least annually
- Apply **minimum risk management practices** to *high-impact* AI: pre-deployment testing, an AI impact assessment, ongoing monitoring, human oversight, and a way for affected people to seek remedy or appeal

## NIST AI 600-1 (July 2024)

The *Generative AI Profile* of the NIST AI Risk Management Framework (AI RMF 1.0 is NIST AI 100-1). It applies the framework's four functions to generative AI risks such as confabulation, information integrity and data privacy:

- **GOVERN**: policies, roles, accountability structures
- **MAP**: context and risk identification
- **MEASURE**: assessment (fairness, confabulation, robustness)
- **MANAGE**: risk response and monitoring

## How ICDEV implements the artifacts

| Artifact | What drives it | ICDEV tool |
|----------|----------------|------------|
| AI use case inventory | OMB M-25-21 | `tools/compliance/ai_inventory_manager.py` |
| Model card | OMB M-26-04, Google Model Cards format | `tools/compliance/model_card_generator.py` |
| System card (the whole AI system: models, tools, agents, data flows) | OMB M-26-04 | `tools/compliance/system_card_generator.py` |
| Cross-framework audit and gaps | M-25-21, M-26-04, AI 600-1, GAO | `tools/compliance/ai_transparency_audit.py` |

The dashboard view of all of this is **AI Transparency** on the Security canvas, `/security/ai-transparency`.

## Your task

Identify 3 AI systems in ICDEV that would appear in an agency's AI use case inventory (for example the LLM router, the RAG knowledge search, an ACE co-worker team). For each: what is it, who would own it, what data does it process, and is it minimal-risk or high-impact under M-25-21?
