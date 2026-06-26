---
ontology_id: icdev:mission:m-gov-03-intake:step:2
step_class: icdev:configure
---
# Governance Advisory Integration

The ICDEV chat interface has a governance advisory mode that automatically surfaces relevant AI governance obligations when users discuss AI deployments.

## Governance sidebar config

The governance sidebar appears in the ICDEV chat when messages match governance-relevant intents (detected by the AI Governance Intake module):

- "We're deploying an AI system for..." → triggers AI inventory checklist
- "We need to automate decisions about..." → triggers oversight plan requirement
- "CAIO review..." → triggers OMB M-25-21 compliance check

## Your task

In the ICDEV chat (`/chat`), describe a fictional AI deployment scenario (e.g., "We're deploying an AI system to assist case workers in benefits eligibility determination"). Does the governance sidebar appear? What obligations does it surface? Are they accurate for that scenario?
