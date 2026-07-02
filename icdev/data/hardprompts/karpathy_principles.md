# Karpathy Principles — Pre-Design Engineering Gate

> Five heuristics from Andrej Karpathy applied before every code change.
> Apply to: build, bug fix, refactor, TDD, and code review workflows.
> Enforced across all AI platform configs by `tools/workflow/coherence_checker.py::check_karpathy_sync`.

---

## 1. State Assumptions

Name the constraints, inputs, and invariants you're relying on **before writing code**.

- What is the caller guaranteed to pass? (types, ranges, non-null, authenticated, etc.)
- What does the system guarantee at this point? (DB transaction open, RLS active, etc.)
- What can change vs. what is fixed? (env var, config, table schema)

Unstated assumptions are where bugs hide. If you can't list them, the spec is incomplete.

---

## 2. Enumerate Interpretations

For any ambiguous requirement, list 2–4 ways it could be read before picking one.

```
INTERPRETATION A: [what it could mean]
INTERPRETATION B: [alternative reading]
CHOSEN: A, because [load-bearing reason]
```

Surface the choice to the user when the wrong interpretation would cost > 1 hour to undo.

---

## 3. Prefer Simpler

Three similar lines beat one clever abstraction. YAGNI.

- Don't design for hypothetical future requirements.
- Don't add error handling for scenarios that can't happen.
- Don't introduce a helper for code called once.
- If two approaches have equal correctness, pick the one a junior can read without context.

---

## 4. Bound Your Edit Scope

Only touch what the task requires.

- No drive-by refactors, surrounding cleanup, or speculative error handling.
- No feature flags or backwards-compatibility shims when you can just change the code.
- If you discover a nearby bug: file a task, don't fix it inline.
- State the files you will touch before starting. Stop yourself if you drift outside.

---

## 5. Success Criteria First

State how you'll know the change is done **before writing it**.

```
SUCCESS WHEN:
  - [ ] pytest tests/test_<name>.py passes
  - [ ] Route /path returns 200 with correct shape
  - [ ] No new ruff errors on changed files
  - [ ] DB migration applies cleanly
```

If you can't write the acceptance check, the spec is incomplete. Stop and clarify.

---

## Prompt Template

Apply this block at the top of any system prompt for a code-generating agent:

```
Before writing any code, apply the Karpathy engineering gate:
1. STATE ASSUMPTIONS: List all constraints, inputs, and invariants you're relying on.
2. ENUMERATE INTERPRETATIONS: For any ambiguity, list 2-4 readings; state which you chose and why.
3. PREFER SIMPLER: Choose the simplest implementation that satisfies the acceptance criteria.
4. BOUND SCOPE: List the exact files you will touch. Refuse to drift outside.
5. SUCCESS CRITERIA: Write the acceptance checks before any implementation.

Only proceed to implementation after completing all 5 steps.
```
