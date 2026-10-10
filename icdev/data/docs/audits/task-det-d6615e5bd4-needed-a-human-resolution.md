# CUI // SP-CTI

# task-det-d6615e5bd4 — [NEEDED-A-HUMAN] `dwr-ev-01`: resolution record

**Detector:** `recovery` (rem-hyg-16, `summarize_recovery`) — finding
`d6615e5bd4846d5e`, subject `dwr-ev-01`, first seen 2026-09-08T02:19:03Z.
**Verdict: MOOT at dispatch — and a POSITIVE CONTROL.** A human answered the
escalation. `escalate` outranking the later `merge` was **correct**, not an
inflation, so nothing about the detector is wrong and nothing in it was touched.

## The derivation, re-run before acting

```
python - <<'EOF'
from tools.awareness.claims import _recovery_rows
from tools.dashboard.recovery_summary import summarize_recovery
print([e for e in summarize_recovery(_recovery_rows(), limit=10_000) if e['task_id'] == 'dwr-ev-01'])
EOF
```

`[]` at 2026-09-09T02:05:04Z (65 rows in the 24h window). The subject's newest
attempt row is the 5th resume at 2026-09-08T01:54:13Z, so the entry aged out of
the 24h window at 2026-09-09T01:54:13Z — **11 minutes before** the re-derivation.
`detector_findings --dry-run` at 02:06:26Z reports 6 recovery findings and
`dwr-ev-01` is **not among them**.

Corroborating state: board `dwr-ev-01` = `done` (2026-09-08T07:02:16Z);
`restore_acts --apply reap_dead_lease --target dwr-ev-01 --dry-run` answers
"no live lease — nothing to reap", so there was no claim left to release.

## What actually happened (UTC)

PR [#2173](https://github.com/icdev-ai/icdev/pull/2173), head `kanban/dwr-ev-01`.
Ledger: 142 `wait`, 52 `sibling_conflict_warn`, 5 `resume`, 2 `rebase_failed`,
2 `union_refused`, 1 `escalate`, 1 `merge`.

| time | event |
|---|---|
| 01:05:24 | `rebase_failed` — real conflict; `union_refused`; resume #1 |
| **01:05:44** | **human merges `origin/main` into the branch** (`ce38e39fb`, 2 parents) — 20s later |
| 01:06:51 | `rebase_failed` again — **post-repair artifact** (head is now a merge commit, so `rebase origin/main` replays the original commit and reproduces the identical conflict) |
| 01:22 – 01:54 | resumes #2–#5, every one against an **already-repaired** branch |
| 01:55:08 | `escalate` — "resume cap reached (5/5) … NONE of the 5 injection(s) were ever read (5 still unread in the queue)" |
| **05:59:56** | **human fixes the real remaining defect** — `55ab0bfca`, "fix(tests): the external search reaches TWO backends now, and its audit tests still assumed one" |
| 06:00:31 | second hand-merge of `origin/main` (`bfa6b7fae`, 2 parents) |
| 07:02:09 | #2173 merged; watcher logs `merge` "PR already merged" at 07:02:16 |

Two merge commits and a real fix commit inside the squash — the rmf-ui-08 control
check is **positive**. The conflict was repaired 20s after the first
`rebase_failed`; what kept the PR open for the next 4 hours was a **red test**,
not the conflict, and no resume could have addressed it because
`resume_delivery --task dwr-ev-01` reports **`undelivered — 5 pr_watcher
message(s) still unread in the queue`** (kpr-watch-13: nobody ever reads a
resume). Five attempts that were never made.

The 01:06:51 `rebase_failed` is the documented post-repair artifact and must not
be read as "the repair did not hold" — it did, and the PR merged 5h55m later.

## The cause, and one general finding worth a card

`dwr-ev-01`'s own refusal names a two-file set, **both files undeclared**:

```
refused: files=['icdev/tools/document_intelligence/ingest_orchestrator.py',
                'tools/document_intelligence/ingest_orchestrator.py']
         rules=[] verifiers=[]
      -- undeclared: icdev/tools/document_intelligence/ingest_orchestrator.py
         matches no union_resolver.
```

**A theory this record does NOT ship, because it was tested and is false.** The
`icdev/` copy being the one NAMED looks like a declaration asymmetry — every
entry in `union_resolver.files` is spelled `tools/…`, none `icdev/…`. It is not.
`match_declaration` strips the mirror prefix; verified directly against three
declared pairs (`dashboard/app.py`, `dashboard/templates/base.html`,
`security_canvas/blueprint.py`) — **the `icdev/` copy matches the `tools/`-spelled
declaration in every case.** The `icdev/` name in the message is a **sort-order
artifact**: `_unmerged_files` returns git's sorted `--diff-filter=U` output and
`icdev/` precedes `tools/` lexically, so the mirror copy is simply the first
undeclared file the loop reaches.

### Survey — all 65 lifetime `pr_watcher.union_refused` rows, 9 distinct tasks

63 carry an `undeclared:` reason (2 have a different shape). Replayed through the
**shipped** `match_declaration` against the live `union_resolver.files`:

| bucket | rows | share |
|---|---|---|
| **every file in the set was undeclared** | 53 | 84.1% |
| **mixed** — a *declared* file refused because an undeclared sibling shared the set | 10 | 15.9% |

Most-named undeclared path: **`CLAUDE.md`, 35 of 63 (55.6%)** — the single
largest cause, and exactly the append-a-block shape `keep_both_blocks` already
handles for `docs/features/*.md`. The remaining 28 are ordinary source modules
undeclared under either spelling (`canvas_compliance/posture.py` 14,
`security/row_security.py` 7, `doc_modernization/redline_drafter.py` 3,
`document_intelligence/{ingest_orchestrator,suggestion_store}.py` 2 each).

The 10 mixed rows are **all one task** (`qa-fail-6a87916931be3793`): a declared
`dashboard/app.py` pair refused because an undeclared `security/row_security.py`
pair was in the same set. The refusal is **all-or-nothing** —
`union_resolver.py:787` raises on the first undeclared file and abandons the
whole plan — so one undeclared file forfeits every resolvable hunk beside it.
Narrow (1 task, 15.9%), and stated as measured rather than inflated.

**Not fixed here, deliberately.** The lever is `union_resolver.files` in
`args/pr_watcher_config.yaml`, which is itself a `protected_path`, so folding it
in would stall this card behind the mfx-mrg-04 audited door — the kpr-watch-12 /
kpr-watch-13 precedent. Filed as **kpr-watch-14** (unclaimed) with this survey.
Declaring `CLAUDE.md` is also not a one-liner: mfx-ci-04 requires
`icdev/data/claude_bootstrap/CLAUDE.md` to be regenerated whenever `CLAUDE.md`
changes, so a union-resolved `CLAUDE.md` must regenerate the payload or land
out of parity. That is the card's work, with its own survey.

## Why this card was safe to close

`earliest_clear_at` = newest attempt (2026-09-08T01:54:13Z) + 24h =
**2026-09-09T01:54:13Z**, passed at re-derivation. `fb989f6ad` (#2057,
`earliest_clear_at`) is on `origin/main`, so a terminal card inside the window
would be HELD rather than re-filed as `-r2`; here the window had closed anyway,
so it is safe on both grounds. Landed as an ordinary PR — no `hold` label, no
`scheduled_at` deferral.

The detector, its threshold and its window were not touched.

## Precedent

Twelfth instance of the class. Moot rate now 8 of 12; fourth positive control
after rmf-ui-08, fni-api-01 and rmf-ui-13 — a real human repair inside the
squash. See
`docs/audits/task-det-{27a4838a70,4f4ca191bc,cd1d099fff}-needed-a-human-resolution.md`.
