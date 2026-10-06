---
ontology_id: icdev:mission:m-aie-03-vibe-vs-engineering:step:1
step_class: icdev:Lesson
skill_tag: ai_assisted_engineering
learning_objective: Decide when vibe coding is an acceptable way to produce code and when a change needs engineering discipline, and apply the five Karpathy principles before an AI writes a line.
---

# The Spectrum: Vibe Coding vs Engineering

*Facts in this lesson are current as of October 2026.*

In February 2025 Andrej Karpathy named a habit many people already had: **vibe coding** —
you describe what you want, accept whatever the AI produces, paste error messages back
in until it runs, and "forget that the code even exists." You never read the diff. You
judge the result only by whether it *seems* to work.

That is not a slur. It is a real technique with a real place. The mistake is not
vibe coding — it is vibe coding something that needed engineering.

## It is a spectrum, not a switch

| | Vibe coding | AI-assisted engineering |
|---|---|---|
| **You read the diff?** | No | Every line you ship |
| **Proof it works** | "It ran once" | Tests that fail on the bug and pass on the fix |
| **Scope of a change** | Whatever the AI touched | Only what the task requires |
| **Who owns the code** | Nobody, really | You — the AI is a tool, not an author |
| **Cost of being wrong** | You throw it away | An outage, a breach, a failed ATO |

The deciding question is always the same: **what does it cost if this code is wrong and
nobody notices?**

## When vibe coding is fine

- **Throwaway prototypes** — a UI mock to show a stakeholder, deleted next week.
- **Spikes** — "can this library even do X?" You keep the *answer*, not the code.
- **Personal scripts** — renaming your own files, a one-off data reshape on a copy.
- **Learning** — exploring an unfamiliar API to see what it looks like.

All four share two properties: the blast radius is **you**, and the code has an
**expiry date**. If a spike starts getting imported by something else, it has stopped
being a spike — and it must now be engineered.

## When it is not

- **Production code** — anything other people or systems depend on.
- **Security-relevant code** — authentication, authorisation, input handling, crypto,
  secrets, anything on a trust boundary. "Seems to work" is exactly how an access
  check that never denies anything looks.
- **Regulated and CUI systems** — under NIST 800-53 and an ATO you must be able to show
  *evidence*: who changed what, how it was tested, which control it satisfies. A diff
  nobody read cannot be attested to. Classification markings, audit trails and
  supply-chain records (SBOM) are not optional extras the AI might remember.
- **Shared infrastructure** — CI pipelines, database migrations, hooks. One wrong
  line here breaks every other team, not just you.

## The Karpathy principles — ICDEV's pre-design gate

ICDEV applies five heuristics before every code change, written down in
`hardprompts/karpathy_principles.md` and enforced across all of ICDEV's AI platform
configs by `coherence_checker.py::check_karpathy_sync`. They are the opposite of
"forget the code exists":

1. **State assumptions** — name the constraints, inputs and invariants you rely on.
   Unstated assumptions are where bugs hide.
2. **Enumerate interpretations** — for an ambiguous requirement, list 2–4 readings and
   say which you chose and why, *before* building.
3. **Prefer simpler** — three similar lines beat one clever abstraction. YAGNI: do not
   design for hypothetical future requirements.
4. **Bound your edit scope** — touch only what the task requires. No drive-by
   refactors. If you find a nearby bug, file a task instead of fixing it inline.
5. **Success criteria first** — say how you will know the change is done (a test that
   passes, a route that returns 200) *before* writing it. If you cannot write the
   acceptance check, the spec is incomplete.

Notice that every principle is something you do **before** the AI generates code. An AI
will happily produce a confident answer to an ambiguous request; the principles are how
you stop the ambiguity from being resolved silently, by the model, in a way you never
reviewed.

## Key takeaway

Vibe coding is a fine way to make something you will throw away. The moment code has
users, security impact, a compliance obligation or a future, you stop judging it by
"it seems to work" and start judging it by evidence you can show someone else.
