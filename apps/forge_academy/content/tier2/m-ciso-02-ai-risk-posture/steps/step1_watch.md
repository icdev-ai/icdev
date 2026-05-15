---
ontology_id: icdev:mission:m-ciso-02-ai-risk-posture:step:1
step_class: icdev:Lesson
---

# AI Risk Posture — Watch It Run

Knowing what AI systems you have (M-CISO-01) is step one. Step two is understanding the risk they represent. Watch ICDEV's AI Risk Posture engine assess your agency's exposure.

## What the agent just did

For the same 23 AI systems discovered in the inventory scan:

1. **Scored** each system on 5 risk dimensions:
   - Data sensitivity (what data does the AI process?)
   - Decision autonomy (does a human review AI outputs before they affect people?)
   - Model provenance (is this a commercial model, open-source, or internally trained?)
   - Failure impact (what breaks if the AI is wrong or unavailable?)
   - Adversarial exposure (can external actors interact with this AI?)

2. **Computed** a composite risk score (0–100) for each system

3. **Identified** the 3 highest-risk systems:
   - Personnel evaluation AI: 87/100 (rights-impacting, no human review)
   - Threat detection ML: 71/100 (safety-impacting, commercial model, adversarial exposure)
   - Benefits eligibility classifier: 68/100 (rights-impacting, limited auditability)

4. **Generated** an executive risk memo: 3 paragraphs, no jargon, ready for Congressional inquiry

## Why this matters

OMB M-25-21 requires not just an inventory, but a **risk-based prioritization** of governance actions. Without scoring, you're treating all AI systems equally — and allocating ISSO resources to the wrong ones.

## Next step

Configure the risk scoring model for your organization's specific risk tolerance and mission context.
