# CUI // SP-CTI

# kpr-watch-20 — the artifact-pin pair is DECLARED, and the survey that armed it counts HUNKS

```bash
python -m tools.kanban.artifact_pin_union_survey            # per hunk; ground truth = what LANDED
python -m tools.kanban.artifact_pin_union_survey --delta     # what THESE two entries buy
```

**THE RUNG WAS REACHED AND IT REFUSED.** #2267 / #2269 / #2270 sat CONFLICTING
for 3–5 hours with zero failing checks and escalated to a human, who resolved
all three by hand. Per PR: 217 `wait`, 8 `rebase_failed`, **8 `union_refused`**,
1 `resume`, 1 `escalate` — every refusal `undeclared: args/pinned_artifacts.yaml
matches no union_resolver.files entry`. kpr-watch-14's population exactly (63 of
65 on `undeclared:`), one epic later, on two files that card did not reach.
Three things were NOT broken and were not touched: kpr-watch-13's early
escalation WORKED (one resume, not five), mfx-mrg-08's `base_seg` guard WORKED,
and the escalation was CORRECT.

**HUNK-LEVEL, AND THAT IS THE DIFFERENCE FROM kpr-watch-14.** A whole-file
verdict is enough only when every conflict in a file has ONE shape. Run
whole-file, `args/pinned_artifacts.yaml` reports `1 refused, 1
mixed_difference` and reads like a file the union cannot handle. GIT DECIDES
THE HUNKS as well as the population — the regions come out of `git merge-file
--diff3`, so a hunk is a thing git refused to merge, never a thing the survey
chose to call one — and each is located in the landed file and in the union
output by the unchanged context that bounds it, the anchor grown until unique.
A region not anchorable UNIQUELY in both is `unanchorable`: unmeasured, never
agreement. `p2_as_main` is named as the RUNG'S orientation (the human ran `git
merge main` from the card branch, so p2 is main), with the other beside it.

**ONE VERDICT kpr-watch-14 DID NOT HAVE.** `human_also_edited`: a line that
landed, is absent from the union, and appears on NO SIDE was written by the
human *during* the resolution. The union could not have produced it and a
rebase resolution is not supposed to invent prose. Folding it into
`union_lost_content` would file editorial work as a union defect. Provenance is
a PER-HUNK question — the three sides of *this* hunk — which is why a
whole-file comparison cannot ask it.

**THE SIX HUNKS, MEASURED 2026-09-12** (the card's seed population, stated
rather than assumed): **1 exact, 3 union_kept_more, 1 human_also_edited, 1
refused, and `union_lost_content` = 0.** Byte-for-byte agreement is 1 of 6, not
6 of 6 — and every one of the other five differs because the HUMAN did
something the union did not.

* **The refusal is CORRECT and stays.** On `cea6f65b4` both sides REWROTE the
  "NOT CONVERTED HERE" enumeration paragraph (base 9 lines, main 44, card 24).
  `keep_both_blocks` opens `if base_seg: return None`. Two contradictory
  enumerations of which pins are behind is a question about what is TRUE.
  Declaring this pair does not claim otherwise: that merge still escalates.
* **`union_kept_more` POINTS AT THE HUMAN RESOLUTIONS, and is the argument FOR
  the union.** Three hunks, `extra=2`, the same two lines every time —
  `assert entry["decided_by"] == AF.DECIDED_BY_CONSUMER` and
  `assert entry["consumer"] == "floci"`. The two appended test functions end in
  identical lines, so a by-hand read of the conflict markers merges the wrong
  bodies. **Six assertion lines were lost that way and are missing from main
  today**: three of the five `test_the_shipped_*_pin_is_decided_by_floci` tests
  (ec2, mysql, valkey) no longer assert `decided_by` at all — the one thing
  each exists to pin. Green, gated, and weaker than their names. The union
  would have kept all six.

**WHAT IT BUYS**, replayed over the recorded corpus through the SHIPPED
`match_declaration`: of 118 `union_refused` rows, 56 were resolvable without
these two entries and 86 with them — **a DELTA of 30**, three whole tasks
clearing entirely (`artifact-fresh-a7486018c7` 12, `-da63da118f` 10,
`-ee3339893b` 8). A cumulative "86 of 118" would credit this card with
kpr-watch-14's entry and every entry before it, which is why `--delta` exists.
The 32 that remain name ORDINARY SOURCE MODULES (`canvas_compliance/posture.py`
28, `security/row_security.py` 14, …) and are NOT candidates: kpr-watch-14's
rule that a source module is not append-shaped is unchanged.

**THE DELETION CASE IS ASSERTED, NOT INHERITED.** On THESE files: one side
deletes a real pin entry (`  - name: opensearch`) or a real pinning test
(`def test_the_shipped_postgres_pin…`), the other edits the same lines, and the
merge refuses in BOTH orientations — *"the base branch DELETED these lines and
the card rewrote them"*. With the `base_seg` guard patched out the same merge
resolves and writes the card's version of lines the other side deleted. A
control test asserts two sides APPENDING still union cleanly, so the refusal is
the deletion and not a file the rules cannot handle.

**`icdev/data/args/floci_runtime_images.yaml` — CONSIDERED, AND NOT DECLARED.**
Two measured reasons, either sufficient. (a) Its one conflict is an ADD/ADD of
the SAME `note:` key under the SAME entry; `keep_both_blocks` keeps both, and
`yaml.safe_load` accepts a duplicate mapping key SILENTLY (last wins), so the
rung's YAML verifier PASSES and ships a declaration of record carrying two
contradictory notes — the wrong-rule failure of 2026-09-03 in a form no
verifier catches. (b) It is not append-shaped, it is DERIVED:
`tools/installer/sync_package_tree.py` regenerates it from
`args/floci_runtime_images.yaml`, which merged CLEAN in 4 of 4. It conflicted
only because the mirror was STALE (`ffce8d75a`: *"mirror_parity only ever
compares tools/ ↔ icdev/tools/"*), so both branches re-added the same missing
note. `derived_from` is not the answer either — kpr-watch-14 records the one
shape the derivation rung refuses to repair, *a declared copy of a resolved
source that is NOT in the conflict set*, and that is exactly this.

**NOT DONE, AND NAMED.** The six dropped assertions on main are a SEPARATE
card: `red_first_gate` would correctly reject the repair from this PR, because
those assertions PASS against the merge base — the defect is a missing
assertion, not a wrong value, and the gate has no `not_applicable` for that
shape. Nothing watches `args/` → `icdev/data/args/` drift; that is its own card
too. The historical replay is deliberately NOT a gated assertion: `test-shard`
checks out at depth 1, so a test replaying merge history would report
UNMEASURABLE on every CI run and pass — a perfect score over an empty
denominator (rem-hyg-13).

Survey: `docs/audits/kpr-watch-20-artifact-pin-union-survey.md`
