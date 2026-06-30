# Hard Prompt: RFI Response Technical Appendix (CoT)

## Purpose
Generate the 2-page Technical Appendix for a government RFI response, describing:
A) Core system architecture (the "how it works" for evaluators who want depth)
B) Adaptive learning loop (Obj F detail)

Uses CoT to ensure technical descriptions match actual codebase, not aspirational claims.

## Variables
- `{architecture_name}` — System name (e.g., "FORGE™ Intelligent Orchestration Platform")
- `{learning_system_name}` — Learning system name (e.g., "NOVA™")
- `{three_tiers}` — Three-tier stack names and latency targets
- `{cloud_primary}` — Primary cloud platform
- `{objectives}` — Original RFI objectives (to ensure appendix maps to each)

---

## Chain-of-Thought Instructions

### Step 1 — REASON
Map each appendix section to a specific piece of existing code:
- Appendix A (Architecture): Which files implement each tier? `chain_orchestrator.py`, `router.py`, etc.
- Appendix B (Learning Loop): Which files implement ECHO, SOUL, TRUST, SELA? What are the actual class/function names?
- Which claims are current-state vs. planned? Mark clearly.
- What are the actual measured latencies (lab environment)?

### Step 2 — CRITIC
Challenge the architecture description:
- Does the diagram description (text-based, no images) actually convey the layering clearly to a non-ICDEV reader?
- Are the latency numbers defensible? Do they distinguish Python prototype from C/Rust production target?
- Is the learning loop description concrete enough? Does it answer "how long does a policy update take?"
- Would an NSA evaluator who reads only the appendix understand what to integrate with?

### Step 3 — SYNTHESIZE

**Appendix A: {architecture_name} — Intelligence and Execution Layers**

Structure:
1. Opening paragraph: The fundamental design principle (separate intelligence from execution)
2. Intelligence Layer description (policy derivation, XAI, HITL, traceability) — 3-4 sentences per component
3. Execution Layer description (Rule Engine, CoD, CoT) — with latency targets and honest qualifications
4. ASCII/text diagram showing the layering and data flow
5. Cloud-native deployment mapping (how each component maps to `{cloud_primary}` services)

**Appendix B: {learning_system_name} Adaptive Learning Loop**

Structure:
1. Opening: the four components and why each exists
2. ECHO — feedback ingestion mechanism, data stored, append-only guarantees
3. SOUL — Bayesian update mechanism, update cadence, what changes in the routing policy
4. TRUST — confidence scoring, HITL gate threshold, what happens to low-confidence updates
5. SELA — exploration rate, how it prevents policy convergence, how exploration feeds ECHO
6. Performance table: timings for each phase (ECHO→SOUL→TRUST→deploy)

## Writing Rules
- Appendix A = architecture; Appendix B = learning loop. Keep them separate.
- Label everything: "Appendix A: ...", "Appendix B: ..."  
- If a component is planned (not built), label it: "(planned for NSA integration)"
- Text-based diagrams only — no images; use ASCII art or indented text flow
- Mark any sections as [PROPRIETARY] if they contain competitive IP
- Page budget: 2 pages total (~1,000 words)
