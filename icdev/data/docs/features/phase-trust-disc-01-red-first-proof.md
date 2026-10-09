# CUI // SP-CTI

# Red-First Proof as a Merge Gate (trust-disc-01)

**Status:** shipped
**Gate:** `python tools/ci/red_first_gate.py --gate` — the `test` job, step *Red-first proof*
**Policy:** [`args/red_first_gate.yaml`](../../args/red_first_gate.yaml)
**Prior art it shares a primitive with:** [`tools/security/reproduction_validator.py`](../../tools/security/reproduction_validator.py)

---

## The defect

ANVIL mandates RED → GREEN and **nothing anywhere recorded the RED.**

A process instruction whose evidence is never captured is the `|| true` failure of
D394 in a second form: the rule is stated, the artifact proving it fired is
absent, and no reader can distinguish a check that ran from one that did not.
`.claude/settings.json` wrapped the PreToolUse hook so that eleven checks printed
`BLOCKED:` and blocked nothing; "write a failing test first" has the same shape,
minus even the printout.

### The case that motivates it

A test asserting that `check_project_card_coverage` *"degrades honestly when the
board is unreachable"* passed locally — because the **unpatched** call raised. The
monkeypatch had landed on `tools.db.storage` while the checker resolved
`icdev.tools.db.storage`: two distinct module objects, so the fake was never
installed and the assertion was satisfied by the wrong exception. It was
correct-looking, reviewed, and worthless.

A red-first check catches it, because that test **passes against the pre-change
tree**.

---

## What the gate does

For every test file the branch **adds or modifies**:

1. resolve the merge base against `origin/main`;
2. check it out into a disposable `verify`-actor worktree;
3. copy **only that test file** on top — everything else stays at the merge base;
4. run it there, and run it in this tree;
5. it must **not pass** at the merge base and **must pass** here.

```
fails before, passes after   -> discriminating.     The RED is recorded.
passes before                -> not_discriminating. It asserts CURRENT behaviour
                                rather than REQUIRED behaviour, so it could never
                                have gone red.
fails after too              -> not_discriminating. Broken, not red-first.
passes before, fails after   -> not_discriminating. This change breaks a green test.
indecisive either side       -> reported, never blocking. We learned nothing.
```

The restriction in step 3 **is** the experiment. If the whole branch were checked
out, the fix would be present and the test would pass — proving nothing.

### The record

`--out <path>` writes the JSON proof: per file, the verdict, both pytest exit
codes, both summary lines, and a bounded tail of the **merge-base** output. That
tail is the recorded RED. CI uploads it as the `red-first-proof` artifact on
every run, pass or fail (`if: always()`).

It is a file artifact rather than a database row on purpose: CI runs on a cold
checkout with no migrated database, so a DB write there would be the very
"declared capability nothing consumes" defect this platform ships most.

---

## The primitive is shared, not copied

`tools/security/reproduction_validator.py` already stated the rule exactly:

> the same reproduction must fire against the vulnerable target and must **stop**
> firing once the fix is applied. Only then is `discriminating` set.

It is scoped to HTTP replay against an allowlisted target, so it is not a
drop-in. What *is* reused is the decision itself. `decide_discrimination` and
`DiscriminationVocabulary` were extracted there and are imported here:

| | `reproduction_validator` | `red_first_gate` |
|---|---|---|
| probe | a stored HTTP reproduction | a changed test file |
| "fires" | the vulnerability predicate evaluated true | the test did **not** pass |
| before-state | the vulnerable target | the merge-base tree |
| after-state | the fixed target | this tree |
| verdict | `DiscriminationProof` | `RedFirstProof` (same shape) |

`verify_discrimination`'s wording is unchanged, verbatim, and pinned by
`tests/test_red_first_gate.py::test_replay_vocabulary_wording_is_unchanged`. Two
gates asking the same question from two copies of the decision table is how they
end up disagreeing about it.

---

## Honest limits

Stated rather than hidden, because a gate that overclaims is worse than one that
does less:

* **A test for a module the PR also adds** goes red at the merge base by
  `ImportError`, whatever it asserts. That is a genuine RED — the canonical TDD
  red for new code — but a weak one. It is reported as `collection_error`, never
  folded into `failed`, so a reader can see which kind they are looking at.
* **Only the test file crosses over.** If it imports a helper the PR also adds,
  the merge-base run fails on the missing helper. Same shape, same visibility.
* **pytest exit 5 is `no_tests`, never `passed`.** Conflating "no tests ran" with
  "the tests passed" is the same error as the mandate this replaces.
* The gate is a **floor**. It makes "the test never went red" impossible to ship
  silently. It does not make every test that clears it a good test.

### Why an applicability check

A docstring fix, an import reorder, or a rename inside a test file behaves
identically before and after, so it would land in `never_established` and read as
*"you wrote a worthless test"*. The gate therefore asks the question only when the
diff **adds** a line containing an assertion or a test function
(`applicability.markers`). An applicability check with no gate is 100% false
positives, and a gate that cries wolf gets a `|| true` bolted onto it.

The check decides whether to **ask** the question, never what the answer is.

---

## Exit codes

| code | meaning |
|------|---------|
| 0 | clean, or `mode: advisory`, or `--gate` not passed |
| 1 | at least one changed test file is **not discriminating** |
| 2 | the gate **could not run** — no merge base, no policy, no worktree |

`2` is separate from `1` deliberately. A gate that cannot run is not a gate that
found nothing, and the log must be able to tell them apart — that distinction is
the entire subject of this task. In CI the usual cause is a shallow checkout, so
the `test` job's `actions/checkout` sets `fetch-depth: 0`.

---

## Escape hatches, in order of preference

1. **Write a red-first test.** Run it against the merge base before you write the
   fix, which is what ANVIL asked for in the first place.
2. **Exempt the file with a written reason** in `args/red_first_gate.yaml`. There
   is deliberately no numeric budget: a count can be held constant while the set
   churns, which is how a gap regrows behind a green gate. An exemption whose
   pattern matches nothing in the tree is reported as `::warning::` **stale**.
3. There is no third one. `mode: advisory` exists so the gate can be *measured*
   on a tree before it is armed — not to get a commit through — and a shell
   neutraliser is the failure this whole card is about.

---

## Files

| path | role |
|------|------|
| `tools/ci/red_first_gate.py` | the gate (mirrored to `icdev/tools/ci/`) |
| `args/red_first_gate.yaml` | mode, scope, applicability, run budget, exemptions |
| `tools/security/reproduction_validator.py` | `decide_discrimination` + `DiscriminationVocabulary` |
| `.github/workflows/icdev-ci.yml` | `test` job: `fetch-depth: 0`, *Red-first proof*, artifact upload |
| `tests/test_red_first_gate.py` | 39 tests; the end-to-end ones build throwaway git repos |

## Verification

Measured on this branch, 2026-08-15:

```
$ python tools/ci/red_first_gate.py --gate
Red-first proof vs origin/main (f9b3d5596596): 1 changed test file(s) —
  1 discriminating, 0 not, 0 indecisive, 0 exempt, 0 not applicable.
  [discriminating] tests/test_red_first_gate.py: the test did not pass against the
      merge-base tree and passes against this one — the RED is recorded
      merge-base: collection_error (exit 2) 1 error in 0.42s
      this tree:  passed (exit 0) 39 passed in 47.22s
exit 0
```

and, with a deliberately non-discriminating probe dropped into `tests/`:

```
  [not_discriminating] tests/test_zz_redfirst_acceptance_probe.py: the test PASSES
      unchanged against the merge-base tree — it asserts CURRENT behaviour rather
      than REQUIRED behaviour, so it can never have gone RED
      merge-base: passed (exit 0) 2 passed in 0.48s
      this tree:  passed (exit 0) 2 passed in 0.36s
exit 1
```

Both halves of the acceptance criterion, measured rather than asserted.

## A correction to the card

The card states the three MCP tools `finding_replay` /
`finding_enforce_reproduction` / `finding_verify_discrimination` are *"flagged
inert in tool_registry, all False"*. They are not. That `False` is their entry in
`READ_ONLY_DECLARATIONS`, which declares whether a handler **mutates state** —
it partitions tools into the concurrent and sequential halves of the agent loop.
`False` there means "this handler writes", which is correct for all three
(`enforce` persists to `dynamic_findings` and `finding_replay_attempts`). It says
nothing about liveness, and no caller was manufactured for them: the honest
consumer of an HTTP-replay tool is not a CI step that runs pytest.
