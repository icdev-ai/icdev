# CUI // SP-CTI

# aca-trn-03 — a mission states what it teaches

**Status:** shipped
**Date:** 2026-08-02
**Closes:** `aca-trn-03`, `-d1` (model + JSON projection), `-d2` (extraction),
`-d3` (mission card), `-d4` (mission runner)
**Migration:** `20260803005919_fa_mission_learning_objective` — applied to the live
PostgreSQL instance 2026-08-02
**Supersedes:** the "deliberately NOT built" entry for `aca-trn-03` in
[forge-academy-assessment-integrity.md](forge-academy-assessment-integrity.md)

## The problem

A mission card advertised three costs — XP, estimated minutes, difficulty — and never
what the learner walks away able to do. For training used in a compliance context that
is backwards: the objective is the **auditable unit**, because "this learner was trained
on X" needs an X. The tagline is not it. *"The difference between a chatbot and a weapon
is the prompt"* is marketing copy, and a certificate that cites it cites nothing.

## Why the original refusal was wrong

`aca-trn-03` was first closed as *not built*, on a measurement: frontmatter carries only
`ontology_id` and `step_class` across all 212 step files, and only ~16 files contain
anything objective-shaped. Extracting from frontmatter alone would have put an objective
on roughly 7% of missions, which is worse than showing none.

The measurement was right and the conclusion was too narrow. It scoped extraction to
**frontmatter**, where authors had not been asked to write one. The objective was already
in the content — in prose, in the opening section of the first step, where an author
naturally states what the reader is about to build. Reading *that* is still extraction,
not authoring: nothing here writes an objective an author did not.

Measured on the live database after the backfill:

```
fa_missions                       124 rows  (122 active)
  learning_objective NOT NULL      53
  learning_objective NULL          71
  learning_objective = ''           0
```

**43%**, not 7%. The remaining 71 stay NULL, and both surfaces omit the line entirely.

## The design rule

> An absent objective is a **visible content gap** someone can go fix.
> An invented one is an **invisible false record** on the field an audit reads.

This is the same rule `aca-hon-01` applied to the configure handlers and `fga-fix-02`
applied to the watch-step Demo Output. It is why the extractor refuses more than it
accepts, and why NULL renders as nothing at all — no empty label, no placeholder, no
synthesised sentence derived from the tagline.

## What ships

### Extraction — `apps/forge_academy/content_loader.py`

`extract_learning_objective` / `objective_for_mission`, in precedence order:

1. an explicit `learning_objective:` frontmatter key;
2. the lead paragraph of an objective-bearing section in the mission's **first** step —
   later steps state per-step tasks, not the mission's outcome;
3. the opening claim of the step, where the objective-bearing section is a code fence.

What it declines to return is most of the behaviour, and most of
`tests/test_academy_learning_objective.py`:

- a section opening on a list or a code fence borrows nothing from the next section;
- a fragment under 40 characters is dropped rather than truncated;
- a paragraph carrying a question mark is the exercise being *posed*
  ("identify: what `listen_topics` does it subscribe to?"), not the capability claimed;
- over-long prose is cut back to a sentence boundary, never mid-word;
- a heading inside a fence is not a heading.

### Storage — migration `20260803005919`

`fa_missions.learning_objective TEXT`. Written in Python, not SQL, for two reasons: the
column must be added idempotently on **both** engines (`ADD COLUMN IF NOT EXISTS` is
PostgreSQL-only and a bare `ADD COLUMN` raises on a second SQLite run), and the backfill
values come from the extractor rather than from ~124 strings frozen into a `.sql` file
that would drift the moment either the content or the extractor changed. Re-running
re-extracts; only rows whose stored objective differs are touched.

### Surfaces

| Surface | File | Placement |
|---|---|---|
| Mission card | `templates/forge_academy/missions.html` | Between the tagline and the cost badges — above the costs, because it is not one |
| Mission runner | `templates/forge_academy/mission.html` | Above the first step pane **and** above the tier-locked notice |
| JSON | `blueprint.py::_LEARNER_MISSION_FIELDS` | The one field in the allowlist that is not a price |

Both Jinja copies are mirrored to `icdev/tools/dashboard/templates/`.

The runner placement is deliberate: `aca-ux-04` chose locked-but-readable, and
"readable but earns no XP" is only a decision a learner can make if they already know
what the mission teaches. So the objective precedes the refusal.

The JSON projection is its own task (`-d1`) because it was the failure that nearly
shipped: the column landed on the table, in the migration and on both Jinja surfaces,
but not in `_LEARNER_MISSION_FIELDS`. An allowlist drops an unlisted column with no
error, so `/api/academy/learning-path` recommended missions while silently withholding
the only field that says what they teach. **Storing a field is not the same as exposing
it.** A row read from before the migration omits the key rather than faking a `null`, so
a client can tell "nobody wrote one" from "this build predates the field".

## Verification

| Test | Covers |
|---|---|
| `tests/test_academy_learning_objective.py` | Extraction precedence and — mostly — refusal; the JSON projection's three states |
| `tests/test_aca_objective_surfacing.py` | Both surfaces render it, and render **nothing** when it is NULL or `''` |

`test_aca_objective_surfacing.py` renders the shipped templates through a real Jinja
environment with `StrictUndefined`, parametrised over both template roots (`tools/` and
the `icdev/` package mirror). The strictness is the point: both surfaces are
`{% if m.learning_objective %}` blocks, and Jinja's default `Undefined` is falsey — so a
route that stops passing the field, a query that stops selecting it, or a rename removes
the objective from the page with **no error anywhere**, indistinguishable from a mission
that legitimately states none. Grepping the template source, which is what the
neighbouring prereq test does, cannot see any of that.

The negative cases carry the weight, per the design rule above. The objective is
extracted from authored markdown, so it is untrusted text; both surfaces are asserted to
escape it.

Verified live on 2026-08-02 against the running dashboard on PostgreSQL:
`/academy/missions` renders 28 objective blocks in the default role-filtered view, and
`/academy/mission/m02-prompt-engineering` shows the panel above step 1. Cards for
missions that state none — *LLM Fundamentals*, *Tier 1 Capstone*, *Multimodal AI* —
show no objective line, which is the design working.

## Known gaps

- **71 missions state no objective.** That is a content-authoring backlog, deliberately
  left visible rather than papered over. The extractor will pick each one up on the next
  migration re-run once an author writes it — no code change needed.
- **Certificates do not yet cite the objective.** `aca-int-07` gave XP a provenance
  ledger and certificates cite verified evidence; naming the objective in that citation
  is the natural next step and is not built.
