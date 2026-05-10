# AI Governance Inventory — Watch It Run

OMB Memorandum M-25-21 ("Advancing Responsible Use of AI in the Federal Government") requires agencies to maintain a complete inventory of all AI systems used in mission-critical operations. Most agencies have no idea how many AI systems they're actually running. Watch ICDEV find out.

## What the agent just did

For a mid-size DoD agency (1,200 personnel, 85 IT systems):

1. **Scanned** all 85 systems in the ICDEV registry for AI components
2. **Identified** 23 systems with active AI/ML components (vs. 7 self-reported)
3. **Classified** each by OMB M-25-21 use case category:
   - Safety-impacting: 4 systems (MANDATORY human oversight)
   - Rights-impacting: 6 systems (MANDATORY human oversight)
   - Mission-operational: 13 systems (standard governance)
4. **Flagged** 3 systems with no AI governance documentation
5. **Generated** the OMB-format inventory spreadsheet, ready for submission

## Why the number is always higher than expected

Shadow AI is real. Development teams add AI capabilities (Copilot integrations, LLM calls in microservices, ML-based anomaly detection) without registering them as "AI systems." The ICDEV scanner finds them by analyzing API calls, Python dependencies, and service manifests.

## Next step

Configure the AI Inventory scanner to run against your agency's system registry.
