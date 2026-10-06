---
ontology_id: icdev:mission:m-ciso-01-ai-inventory:step:1
step_class: icdev:Lesson
---

# AI Governance Inventory — Watch It Run

OMB Memorandum M-25-21 ("Accelerating Federal Use of AI through Innovation, Governance, and Public Trust", April 2025) requires agencies to inventory their AI use cases every year and to apply minimum risk-management practices to every **high-impact** use. Most agencies don't know how many AI systems they actually run. This walkthrough shows how ICDEV finds out. The numbers below are an illustrative scenario.

## What the agent just did

For a mid-size DoD agency (1,200 personnel, 85 IT systems):

1. **Scanned** all 85 systems in the ICDEV registry for AI components
2. **Identified** 23 systems with active AI/ML components (vs. 7 self-reported)
3. **Classified** each use case against M-25-21: 10 are **high-impact** and get the minimum practices (pre-deployment testing, an AI impact assessment, ongoing monitoring, and human oversight and accountability). The other 13 are not high-impact and stay under standard governance
4. **Flagged** 3 systems with no AI governance documentation
5. **Exported** the inventory for the agency's annual submission

> M-25-21 replaced the earlier M-24-10 split into "safety-impacting" and "rights-impacting" AI with a single **high-impact** category. ICDEV's assessor, `tools/compliance/omb_m25_21_assessor.py`, uses the M-25-21 term. The inventory itself lives in `tools/compliance/ai_inventory_manager.py` (`--register`, `--list`, `--export`).

## Why the number is always higher than expected

Shadow AI is real. Development teams add AI capabilities (Copilot integrations, LLM calls in microservices, ML-based anomaly detection) without registering them as "AI systems." A discovery scan finds them by looking at API calls, Python dependencies and service manifests, which a self-report never sees.

## Next step

Walk through the settings an AI inventory run takes for your agency's system registry.
