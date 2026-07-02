# Hard Prompt: RFI Response Part 2 — Technical Approach (CoT)

## Purpose
Generate Part 2 (Technical Approach) of a government RFI response using Chain-of-Thought
(reason → critic → synthesize) to ensure claims are accurate and not overclaiming.

## Variables
- `{rfi_number}` — RFI identifier
- `{objectives}` — JSON list of NSA/customer capability objectives
- `{capability_scores}` — JSON list of ICDEV capability-to-objective matches with L/M/N grades
- `{differentiators}` — List of key technical differentiators to emphasize
- `{trl}` — Technology Readiness Level claim (e.g., "Hybrid TRL 6")
- `{cloud_primary}` — Primary cloud platform (e.g., "AWS GovCloud / C2S")

---

## Chain-of-Thought Instructions

### Step 1 — REASON
For each objective in `{objectives}`:
1. State the customer's core need in one sentence
2. Identify the ICDEV capability that addresses it (from `{capability_scores}`)
3. Describe HOW it addresses it — specific mechanism, not generic claim
4. Note any gap: if ICDEV covers <80% of the objective, flag what modification is needed

Do not write prose yet. Structure this as a numbered analysis.

### Step 2 — CRITIC
Review your Step 1 analysis and challenge each claim:
- Is the capability claim backed by specific code/module/tool in ICDEV? If not, remove or qualify.
- Are any latency/benchmark numbers realistic given what is actually built vs. planned?
- Is the "modification required" section honest enough? NSA values honesty over overclaiming.
- Does the three-tier architecture story (Rule Engine → CoD → CoT) hold for ALL objectives or just some?
- Flag any claim that is aspirational rather than current-state.

### Step 3 — SYNTHESIZE
Write the final Part 2 sections:

**2.1 TRL:** State hybrid TRL claim with evidence table (component → TRL → evidence)

**2.2 Statefulness:** Describe stateless vs. stateful components; how state is synchronized
at scale without adding per-object latency

**2.3 Cold-Start/Scaling:** Describe autoscaling approach; graceful degradation during cold-start

**2.4 Technical Approach:** Lead with the architecture overview (three-tier stack). Then
address each objective A-F with a specific ICDEV capability name, file/module reference,
and honest modification note where applicable. End with a paragraph on the Governance Suite
(Traceability + Explainability + HITL) as a named differentiator.

**2.5 Commerciality:** State % commercial vs. developmental; identify which components are developmental

**2.6 Cybersecurity & Supply Chain:** CMMC level, NIST SP 800-171, SBOM, NDAA §889 status,
container security posture

**2.7 Mission-Specific:** Answer each sub-question (Latency, Multi-Constraint, Priority Injection,
Failure Recovery, Cost Tracking) with specific mechanisms and honest benchmark data

## Writing Rules
- Never claim microsecond LLM inference — LLMs are not on the per-object critical path
- Always distinguish: what is built today (TRL 8) vs. what requires integration work (TRL 5-6)
- Use specific ICDEV component names: `chain_orchestrator.py`, `LLMRouter`, `NOVA™`, etc.
- Page budget for Part 2: ~3.5 pages at 11pt single-spaced 1" margins (~1,700 words)
- End every sub-section with a one-sentence commitment: what the vendor will deliver
