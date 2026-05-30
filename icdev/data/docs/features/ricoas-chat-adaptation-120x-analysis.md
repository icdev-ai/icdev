# RICOAS Chat Adaptation — Transcript Analysis
# "Why Your AI Coding Projects Fail Before You Start" (120x-ai, kWIOB1mZfy0)

**CUI // SP-CTI**

| Field | Value |
|-------|-------|
| Source | https://youtu.be/kWIOB1mZfy0 |
| Channel | 120x-ai |
| Duration | 44:10 |
| Views | 1,817 (as of 2026-05-25) |
| Purpose | RICOAS chat adaptation — conversational intake patterns |

---

## 1. Video Summary

The video demonstrates the **Architect/Builder Method**: a structured workflow where a ChatGPT/Claude "architect" agent handles requirements design and sprint planning, while a Codex "builder" agent handles implementation. The presenter takes a project from blank desktop to a working dashboard committed to GitHub in three sprints (~1 hour). The 120x Project Launcher is a web app that conducts guided intake and generates the folder structure + primed architect prompt.

Key hashtags: `#ArchitectBuilderMethod` `#ClaudeCode` `#Codex` `#AICoding`

---

## 2. Chapter Map (with timestamps)

| Time | Chapter |
|------|---------|
| 0:00 | Why AI coding projects fail before they start |
| 1:30 | What the Project Launcher actually does |
| 2:30 | Step 1: Set up your architect workspace (ChatGPT or Claude) |
| 3:30 | Loading the Architect/Builder Method into your project |
| 4:30 | Adding your reference data and custom instructions |
| 7:30 | Step 2: Run the project intake |
| 9:00 | Filling in the discovery context (and what to leave blank on purpose) |
| 11:30 | Generating the project files and folder structure |
| 12:30 | Moving the folder into your build directory |
| 13:30 | Step 3: Hand the starter prompt to the architect |
| 15:00 | The architect catches what your intake missed |
| 17:00 | Architect Pack 001: the first sprint plan |
| 19:00 | Step 4: Hand the sprint to the builder (Codex) |
| 22:00 | Dry run, plan, execute, validate (the four-step loop) |
| 26:00 | Closing out Sprint 1 |
| 28:00 | Architect Pack 002: planning Sprint 2 |
| 30:30 | Inside the sprint folder: acceptance, blueprint, handoff, requirements |
| 33:00 | When the builder goes off the rails (real footage) |
| 35:00 | How the architect rescues the build |
| 37:30 | Sprint 2 closed, committing to GitHub |
| 39:30 | Sprint 3 and the finished dashboard |
| 41:00 | The final result: one hour, three sprints, working app |
| 42:30 | How to get the Project Launcher |

---

## 3. Key Verbatim Quotes

### On why intake must precede execution
> "The hardest part isn't the building going back and forth. The hardest part is getting started correctly because before you ever hand anything off to Codex or Claude Code, you need to get the project set up. You need to capture the project goals, the users workflow, data, risk, the requirements, the acceptance criteria — at least a basic of that and the builder handoff."

> "If you skip all that kind of setting up, the builder is just going to be guessing and that's where those AI software projects fall apart. You got to have that discipline."

### On structured intake with optional fields
> "There are a few things that are mandatory, but a lot of it's optional because the architect will ask you questions. But the more that you can fill in here, the better it's going to be."

> "If you don't know the answers, you don't have to necessarily answer them because the architect will kind of extract that out of you."

> "Rough notes are enough."

> "I need help with the unknowns, everything in here, as much information as you can."

### On gap detection (architect's response to incomplete intake)
> "The intake is strong enough to understand the business problem, the workflow and everything else. But it is not complete enough for a builder ready sprint."

> "We still don't have a systems tools involved, the data inputs, the MVP for the smallest usable next sprint, the dashboard metric definitions. So there's a lot of decisions that need to be made."

### On proposing defaults to handle ambiguity
> "Unless you override me, these are my proposed defaults." [Lists: project direction, sprint scope, tech stack, parsing approach, upload method, recommended style, output, authentication.]

> "Recommended move is basically saying generate the pack with your proposed defaults and then we'll go ahead and do it."

### On presenting options instead of open questions
> "Possible project directions. Option one, executive dashboard demo app. Best choice, a polished dashboard... Option two, an analyst validation workbench, more operational. And then option three, automated executive report generator — recommended minimal viable product boundary..."

### On the three-prompt execution rhythm
> "After each architect sprint, can you give me a starter prompt for the builder? The dry run prompt, the apply prompt, and then the execution prompt."

> "The dry run is basically verified the pack can be applied safely. The apply prompt is actually apply the pack to the project folder... And then of course the execution prompt where they actually start building."

### On validation before proceeding
> "We're basically just saying, 'Hey, read this and tell me you can do it.' It's got no warnings, no conflicts, no missing folders."

### On size estimation as readiness signal
> "How many sprints do you envision? This just gives me a sense of how big this project is going to be."

> "He's basically saying this is going to be five sprints... At a minimum, we're going to do three. So that gets me the functional demo."

### On escalation when the builder goes off-rails
> "I actually stopped the coding and I said, 'Hey, what is going on? Something is wrong.'"

> "I said, 'Hey, give me a status of where you're at.'"

> "Good. This is not a disaster. Sprint 2 is partially implemented but not accepted yet. The next move is not doc's closeout and not sprint three. The next move is a narrow sprint 2 debugging verification path."

### On sprint close as explicit gate
> "Sprint 01 is closed. Codex did exactly what we needed. Got everything correct. Next move. Now I should create architect pack 02."

> "Good. Sprint 2 is officially closed."

> "I'm ready to generate pack three. Just say when you're ready."

### On artifact structure
> "Inside each sprint, you're going to have four folders: an acceptance criteria for what sprint one is. You're going to have a blueprint. This is the actual plans that your builder is using. This is the handoff prompt that we actually used and gave to the builder. And here are the requirements."

> "There's no ambiguity about what he's supposed to be doing."

---

## 4. Conversational Patterns Observed

### Pattern 1 — Tiered Intake: Mandatory Minimum + Optional Enrichment
Mandatory core is minimal (project name, one sentence). The large discovery section is optional. The system accepts sparse input and fills gaps conversationally rather than blocking the user. User message: "You don't have to fill everything in — the architect will extract it from you."

**RICOAS mapping:** `ricoas_config.yaml` readiness threshold of 0.7 already implies partial intake is acceptable. The `intake_engine.py` session flow should allow submission with sparse fields and route to gap detection, not reject.

### Pattern 2 — Gap Detection Before Execution (Hold Gate)
After intake ingestion, the architect's first response is NOT a plan — it is a **named gap list**. "The intake is not complete enough for a builder-ready sprint. Still missing: [list]." This is an explicit hold gate — the system refuses to proceed to planning until gaps are addressed or delegated to proposed defaults.

**RICOAS mapping:** `gap_detector.py` already exists. The conversation flow should surface its output as a named phase ("Gap Detection Report") before allowing decomposition, not silently incorporate it into the next clarifying question.

### Pattern 3 — Options + Proposed Defaults (not open questions)
Instead of "What do you want to build?", the architect presents 2–3 concrete options and then proposes defaults: "Unless you override me, these are my proposed defaults." The user confirms, overrides, or chooses. This reduces cognitive load dramatically.

**RICOAS mapping:** `clarification_engine.py` uses a 2D Impact × Uncertainty priority matrix to rank questions. The output format should shift from open-ended questions to option sets with a recommended default. The highest-priority clarifications (mission_critical × unknown) should be the only ones presented as open questions; all others should be option+default.

### Pattern 4 — Scope Bounding Before Decomposition
Before defining sprints, the architect states what is in-scope and out-of-scope. Decomposition only happens after the boundary is confirmed.

**RICOAS mapping:** Add an explicit in-scope/out-of-scope confirmation step between gap detection and SAFe decomposition in the session state machine.

### Pattern 5 — Three-Prompt Execution Rhythm per Phase
Every phase transition: (1) dry run — "read this and tell me you can do it, no changes written"; (2) apply — write the plan artifacts; (3) execute — build from those artifacts.

**RICOAS mapping:** Map this to RICOAS's intake → planning → handoff flow. (1) RICOAS presents what it will generate and asks confirmation; (2) RICOAS writes requirements artifacts; (3) RICOAS produces the downstream handoff (Jira sync, task prompt, or DOORS NG export).

### Pattern 6 — Continuous Validation Loop with Escalation Path
After every builder output, the output can optionally be fed back to the architect for approval. The architect returns: approved / approved with caveats / not yet — narrow path needed. Escalation protocol: stop → "give me a status" → hand status to architect for diagnosis → corrective prompt.

**RICOAS mapping:** After decomposition is produced, RICOAS should offer a review gate: "Here is the decomposed backlog. Confirm to hand off, or ask me to adjust." For stalled sessions, add a `--status` command that produces a clean structured summary of what is confirmed, open, and blocking.

### Pattern 7 — Explicit Phase Close Confirmation
A phase is not done until the system says it is: "Sprint 01 is closed." / "Sprint 2 is officially closed." This is a formal handoff gate.

**RICOAS mapping:** Each RICOAS session phase (intake → gap detection → scope bound → decomposition → handoff) should end with an explicit closure statement and summary before the next phase begins. Never implicitly advance.

### Pattern 8 — Commit to Known State at Phase Close
After every sprint close, code is committed to version control as a save point. "We can always fall back to a known entry point."

**RICOAS mapping:** When an intake session phase closes, commit the confirmed state to the requirements database and produce a phase summary artifact. This is the recovery point if the conversation drifts.

### Pattern 9 — Persona Priming via System Instructions
Before any conversation: "You're basically priming your architect to act like an architect. You're not going to create code. Architect first, builder second."

**RICOAS mapping:** RICOAS's system prompt should explicitly enforce role separation: "You are the requirements intake conductor. You do not generate implementation plans. You do not write code. Your only output is structured requirements artifacts and clarifying questions. Architect first, builder second."

### Pattern 10 — Size Estimation as Readiness Signal
"How many sprints do you envision?" is asked early as a complexity calibration. It produces a rough readiness/sizing estimate before committing to execution.

**RICOAS mapping:** After gap detection, RICOAS should produce a complexity estimate alongside the readiness score: "Estimated decomposition: N epics, M stories. Readiness: 72/100. Gaps blocking full readiness: [list]." This makes the readiness score output-visible and actionable.

---

## 5. Recommended RICOAS Chat Adaptations

### R1 — Mandatory Minimum / Optional Enrichment Intake Model
Implement two intake tracks: (a) quick-start — user provides project name + one sentence, RICOAS fills gaps conversationally; (b) full intake — user arrives with detailed requirements. Both converge at the gap detection checkpoint. The quick-start path should not block submission on empty optional fields.

**Affected files:** `tools/requirements/intake_engine.py`, `args/ricoas_config.yaml`

### R2 — Gap Detection as Named Phase with Block Gate
Produce a structured gap report after initial intake: "Your intake is not complete enough for a SAFe-ready sprint. Missing: [specific list]." Then: "I'll propose defaults — confirm or override." Do not silently fold gaps into the next clarifying question. The gap report is a phase output, not a sidebar comment.

**Affected files:** `tools/requirements/gap_detector.py`, session state machine in `intake_engine.py`

### R3 — Options + Proposed Defaults for Clarification
Replace open-ended clarifying questions with option sets + recommended defaults for all but the highest-priority (mission_critical × unknown) gaps. Format: "Option A [recommended]: ... Option B: ... Option C: ... I'll proceed with A unless you override." This is the single highest-impact change from the transcript.

**Affected files:** `tools/requirements/clarification_engine.py` — update output format to include options and a default flag

### R4 — In-Scope / Out-of-Scope Boundary Step
Add an explicit boundary confirmation step between gap detection and SAFe decomposition. RICOAS states what is in-scope, what is explicitly out-of-scope, and asks for confirmation before decomposing.

**Affected files:** `tools/requirements/decomposition_engine.py`, session state machine

### R5 — Three-Phase Transition Protocol (Verify → Plan → Execute)
Each RICOAS phase transition follows: (1) RICOAS presents what it intends to generate and asks confirmation before writing; (2) RICOAS writes planning artifacts; (3) RICOAS produces the downstream handoff artifact. Never skip from intake to execution.

**Affected files:** `intake_engine.py` session flow, `intake_api_client.py`

### R6 — Explicit Phase Close Confirmation
Each RICOAS session phase ends with a closure statement + summary before the next phase begins. Phases: intake → gap detection → scope bound → decomposition → handoff. Never implicitly advance.

**Affected files:** `intake_engine.py` session state transitions

### R7 — Complexity Estimate Alongside Readiness Score
After gap detection, output a complexity estimate with the readiness score: "Estimated: N epics, M stories. Readiness: 72/100. Blocking gaps: [list]." This makes the score actionable and sets size expectations early.

**Affected files:** `tools/requirements/readiness_scorer.py`, `tools/requirements/complexity_scorer.py`

### R8 — Escalation / Status-Check Protocol for Stalled Sessions
When requirements are contradictory or incomplete after multiple rounds, RICOAS triggers a named status check: "Here is where we are — confirmed: [list], open: [list], blocking: [list]. Recommended next move: [narrow corrective path]." This prevents restart loss.

**Affected files:** `intake_engine.py` — add `--status` mode and drift detection logic

### R9 — Session Commit After Phase Close
When a phase closes, commit the confirmed session state to the requirements database and produce a phase summary artifact. This is the recovery point for re-entry after conversation drift.

**Affected files:** `intake_engine.py`, `tools/db/storage.py`

### R10 — Harden RICOAS System Prompt Role Separation
RICOAS system prompt must explicitly enforce: "You are the requirements intake conductor. You do not generate implementation plans or code. Your only output is structured requirements artifacts and clarifying questions." This prevents the intake conversation from drifting into solution design before the problem is fully understood.

**Affected files:** `args/ricoas_config.yaml` — add `system_prompt_role_guard` field; `intake_engine.py` — inject at session start

---

## 6. Priority Order for Implementation

| # | Recommendation | Impact | Effort |
|---|---------------|--------|--------|
| R3 | Options + proposed defaults for clarification | High | Medium |
| R2 | Gap detection as named phase with block gate | High | Low |
| R6 | Explicit phase close confirmation | High | Low |
| R1 | Mandatory minimum / optional enrichment intake | Medium | Medium |
| R5 | Three-phase transition protocol | Medium | Medium |
| R7 | Complexity estimate alongside readiness score | Medium | Low |
| R10 | Harden system prompt role separation | Medium | Low |
| R4 | In-scope / out-of-scope boundary step | Medium | Medium |
| R8 | Escalation / status-check for stalled sessions | Medium | High |
| R9 | Session commit after phase close | Low | Medium |

---

## 7. Mapping to Existing RICOAS Config

```yaml
# args/ricoas_config.yaml additions suggested:
ricoas:
  # R1 — Tiered intake
  intake_tracks:
    quick_start:
      required_fields: [project_name, one_sentence_description]
      gap_fill_mode: conversational
    full_intake:
      required_fields: [project_name, business_problem, target_outcome, primary_users]
      gap_fill_mode: structured

  # R3 — Options + defaults in clarification
  clarification:
    format: options_with_default  # vs open_question
    max_open_questions_per_turn: 1  # only for mission_critical x unknown

  # R6 — Phase close confirmation
  phase_close:
    require_explicit_confirmation: true
    emit_closure_summary: true

  # R10 — Role guard
  system_prompt_role_guard:
    enforce: true
    role: "requirements intake conductor"
    prohibited: ["generate code", "implementation plan", "architecture design"]
```

---

*Generated: 2026-05-25 | Task: task-78cef2e2a7 | Source: https://youtu.be/kWIOB1mZfy0*
