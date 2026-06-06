# Karpathy Principles — Pre-Design Engineering Gate

> Reusable instruction template. Apply these 5 heuristics BEFORE writing code on
> any build, bug-fix, refactor, TDD, or code-review task. Referenced by
> `CLAUDE.md`, `CONVENTIONS.md`, and all AI-platform configs; kept in sync by
> `tools/workflow/coherence_checker.py::check_karpathy_sync`.

## The 5 Heuristics

1. **State assumptions** — Name the constraints, inputs, invariants you're relying
   on. Unstated assumptions are where bugs hide.

2. **Enumerate interpretations** — For any ambiguous requirement, list the 2–4 ways
   it could be read before picking one. Surface them to the user if the choice is
   load-bearing.

3. **Prefer simpler** — Three similar lines beats one clever abstraction. Don't
   design for hypothetical future requirements. YAGNI.

4. **Bound your edit scope** — Only touch what the task requires. No drive-by
   refactors, no surrounding cleanup, no speculative error handling.

5. **Success criteria** — State how you'll know the change is done before writing
   it. If you can't write the test / acceptance check, the spec is incomplete.

## How to Apply (pre-RED reflection, < 5 minutes)

- Write the assumptions and the bounded scope (what will NOT change) before the
  first edit.
- If a requirement is ambiguous, list interpretations; ask only when the choice
  changes the implementation.
- State the success check (the test) first. No test ⇒ spec incomplete ⇒ stop.
- Generate the **minimum** code that satisfies the success check. Reuse existing
  symbols over writing new ones (see `hardprompts/minimal_generation.md`).

## Anti-patterns this gate prevents

- Speculative parameters, options, hooks, or abstractions "for later."
- Drive-by refactors outside the stated scope.
- Placeholder/stub bodies (`pass`, `...`, `TODO`, `NotImplementedError`) shipped
  as if complete.
- Re-implementing a helper that already exists in the codebase.
