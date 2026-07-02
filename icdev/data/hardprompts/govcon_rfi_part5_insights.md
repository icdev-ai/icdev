# Hard Prompt: RFI Response Part 5 — Industry Insights (CoD)

## Purpose
Generate Part 5 (Industry Insights / "What did the Government miss?") using Chain-of-Debate
to surface the most valuable recommendations across multiple expert perspectives.

## Variables
- `{rfi_number}` — RFI identifier
- `{customer_agency}` — Customer agency (e.g., "NSA")
- `{objectives}` — Original RFI objectives (to identify gaps)
- `{domain}` — Primary technical domain (e.g., "AI/ML orchestration", "cloud security")
- `{num_insights}` — Target number of insights to surface (default: 4)

---

## Chain-of-Debate Instructions

### Debate Setup
Run `{num_insights}` parallel debater agents, each with a distinct expert persona:

**Debater 1 — Data Provenance & Auditability Expert**
"I have spent my career making AI systems auditable. What is missing from `{objectives}` 
is data lineage. Without immutable records linking each object to its routing decision and 
downstream outcome, the customer cannot conduct retrospective ROI analysis or satisfy 
oversight/IG requirements. I recommend adding an objective specifically for routing provenance."

**Debater 2 — Privacy & Federated Learning Expert**
"The adaptive learning objective assumes centralizing mission outcome data for model training. 
In a sensitive national security context, that centralization creates a high-value target. 
Federated learning — where models improve locally and only weight gradients are shared — 
achieves the same improvement without centralizing sensitive data. This should be specified."

**Debater 3 — Multi-Classification / Cross-Domain Expert**
"The RFI is UNCLASSIFIED but the customer operates at multiple classification levels. 
The solicitation does not specify whether the orchestrator must operate at a single IL 
or span classification domains. Cross-domain routing has major architectural implications 
(data guards, cross-domain solutions, IL-specific resource pools). The RFP must clarify this."

**Debater 4 — Human-Machine Teaming Expert**
"Fully automated routing is not sufficient for a mission-critical intelligence environment. 
Analysts need to be able to override routing decisions in near-real-time — especially for 
novel object types or emerging mission priorities the algorithm has never seen. 
Without a HITL escalation path, the system cannot respond to fast-moving mission scenarios."

*(Add additional debaters based on `{domain}` as needed: cost optimization, security, interoperability)*

### Judge Synthesis
The judge reviews all debater arguments and:
1. Scores each insight on: novelty (did the customer address this?), mission impact, implementability
2. Selects the top `{num_insights}` insights (no duplicates)
3. For each insight, writes:
   - **The Gap**: What the RFI/RFP did not address
   - **The Risk**: What happens if this gap is not addressed
   - **The Recommendation**: Specific language to add to a future solicitation
   - **ICDEV's Answer**: How ICDEV addresses this (positions our solution as ahead of the ask)

## Writing Rules
- Frame insights as constructive, not critical — "the Government may wish to consider"
- Each insight should be 2-4 sentences maximum
- Always include a concrete recommendation (specific objective language to add)
- End with ICDEV's capability that addresses the gap — turns insights into discriminators
- Page budget for Part 5: ~0.5 pages (~250 words)
