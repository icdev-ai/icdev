# A cheap static check that runs only on CI is a MANUAL FIX 20 minutes later (mfx-ci-01)

> Moved verbatim from CLAUDE.md on 2026-09-12 (xrv-docs-02). The prose is the card's own record; only the leading `#` comment markers were stripped and the command runs fenced.

```bash
python tools/dx/mirror_parity.py --files tools/db/storage.py --json     # ONE pair, not the package
python tools/dx/mirror_parity.py --files tools/db/storage.py --fix --gate
python tools/ci/undeclared_import_census.py --staged --check
python tools/testing/pre_commit_check.py                # both, plus everything it already ran
npx playwright test --list                              # every spec PARSES; no browser, no server
```

THREE RED PRs IN ONE DAY, each fixed by hand, each a check that could have run
at `git commit` in under a second:
 * the #2052 squash left tests/e2e/key_pages_smoke.spec.ts unparseable and ALL
   FOUR E2E shards on main failed at COLLECTION -- Playwright loads every spec
   before it runs one, so 840 tests in 65 files executed ZERO -- and it went
   unnoticed for hours because `E2E (Playwright)` is not a required check and
   nothing parses .ts at commit time;
 * rmf-rfp-01 changed tools/db/schema/{pg_consolidated.sql,tables.yaml} without
   their icdev/ twins; test_mirror_drift_baseline went red on `db` and a human
   mirrored 199 files;
 * rmf-wp-02 imported `markdown` inside a bare `except Exception` in
   exporter.py and the tsg-iso-03 census refused it.

THE REQUIRED `Lint` JOB NOW PARSES EVERY SPEC. `--list` loads and transforms
all 65 spec files with NO browser and NO dashboard (`webServer` and
globalSetup do not run for --list; ICDEV_NO_SERVER states it) -- ~2.15s
median over five warm runs (1.94-2.61s; an independent measure on this host
saw 6.7s under load), after an `npm ci` the job did not previously need. Quote
the RANGE: a single figure from one run does not survive re-measurement, and
an earlier "1.8s warm / 14.6s cold" here reproduced as neither. It is a PARSE
gate and NOTHING MORE: `forbidOnly` is not applied to --list (probed -- a
`test.only` lists clean under CI=1), so a stray `.only` is still the E2E job's
to refuse, and no browser is installed here. Never `|| true`, never
`continue-on-error` -- a parse gate behind either gates nothing.

THE HOOK BLOCKS ON THE STAGED FILES AND NOTHING ELSE. That is the only scope a
hook may block on: `--paths db` hashes 864 pairs to answer a question about the
three files a commit staged, and it reports a package's PRE-EXISTING backlog,
which the author neither caused nor can fix without stepping on the PR that
owns it. Hence `--files` (mfx-ci-01), which audits ONLY the named pairs.
Either spelling resolves to the SAME pair, so a change to the packaged copy
alone is drift too.
MEASURED 2026-09-04 on this checkout, and the numbers ARE the design argument:
  nothing staged under tools/<pkg>/     0 ms   both checks skipped entirely
  3 staged files (this commit's shape)  78 ms mirror + 359 ms census
  60 staged files in one package        97 ms mirror + ~400-580 ms census
  (for contrast) --paths db            512 ms  -- 6.4x, the walk not taken
  (for contrast) census with NO --staged     ~28 s  -- 40x the whole budget
So the WORST realistic added cost is ~437 ms at 3 files and ~677 ms at 60,
inside the 1s budget, and the common commit still pays 0.
MIRROR IS FLAT IN FILE COUNT (78 -> 97 over a 20x change); THE CENSUS IS NOT
(359 -> ~580), because a per-file AST parse sits on top of a fixed ~240 ms
first-party walk. Do not describe them with one shape.
THREE FIGURES HERE WERE WRONG BEFORE THEY WERE RIGHT, and the reasons are the
card's own subject matter: 4486 ms for `--paths db` was a single COLD run
(truth 512 ms, 6.4x not 46x); "1.8s warm / 14.6s cold" for --list reproduced
for nobody (truth ~2-4 s warm); and a flat "126 ms" census across 1, 3 and 60
files was THE EMPTY PATH -- `git add` on an UNMODIFIED tracked file stages
nothing, so `_staged_files()` returned [] and the probe timed a scan of zero
files while labelled 1/3/60. A SERIES THAT DOES NOT MOVE WHEN ITS INPUT MOVES
60x IS NOT A STABLE MEASUREMENT, IT IS A MEASUREMENT OF NOTHING -- the same
shape as the sandbox artifact above, and as `posture_score` 100.0 for canvases
nobody assessed. Every number here is now the SHIPPED code path, corroborated
by a second session (359 ms here, ~350 ms there).
The walk is avoided for CORRECTNESS OF SCOPE first (a package backlog is not
the committer's to fix) and cost second.
The hook's own budget note is 155ms; the common commit still pays 0.

CONTENT DRIFT ONLY. A staged file whose twin is MISSING is a NOTE, never a
block: missing_from_mirror is ungated in args/mirror_parity_gate.yaml and in
the CI test, and the backlog is ~300 files -- blocking on it refuses routine
work. Excluded extensions are READ from that gate file, never respelled (.md
manifest shards are merge=union and diverge transiently BY DESIGN -- they were
124 of the survey's 131 raw (commit, file) EVENTS -- 60 distinct files;
events and distinct files are different quantities and are never merged).
The census blocks on an UNREGISTERED
site only; a ceiling breach the commit did not cause is reported and left to
CI. Both fail OPEN on any error -- no `gh`, no yaml, an unreadable report --
because CI is the backstop and a hook that wedges a commit gets `--no-verify`d.

ONE COPY OF THE SHIM RULE. `coherence_checker._is_mirror_shim` DELEGATES to
`mirror_parity.is_mirror_shim`, so the hook and the gate cannot disagree about
what a shim is. The marker admits "shim" as well as "re-export": three of the
five real shims (testing/qa_agent_runner, testing/selector_healer,
billing/tier) say "Backward-compat shim" and the narrower marker counted them
as DRIFT -- they were 3 of the survey's 7 post-exclusion candidates, every one
a false refusal. A shim's two names resolve to ONE module object (xit-decl-02),
so there is no stale half and comparing bytes is meaningless.
audit_path/audit_all deliberately do NOT apply the shim rule: the recorded
args/mirror_drift_baseline.yaml was measured without it and its test refuses an
empty file, so the package report keeps its historical meaning.

SURVEYED BEFORE ARMING, 200 first-parent commits on origin/main, replayed
against EACH COMMIT'S OWN TREE through the SHIPPED predicate (_mirror_scope and
is_mirror_shim themselves, never a second copy):
  e2e     22 in scope, 2 fires (1.00%) -- BOTH the same broken file, #2052 and
          the #2051 merge that landed on top of it. 0 routine refusals.
  mirror  142 in scope, 4 fires (2.00% of all commits) -- and ZERO are routine
          work. All four were REAL unreconciled drift, and the proof is that
          every one was later repaired BY HAND: db/schema/tables.yaml is the
          rmf-rfp-01 incident; slides/constants.py sat drifted for weeks and
          was reconciled by THIS card; genesis/daemon.py by a later commit; and
          workflow/validated_commit.py cost an ENTIRE FOLLOW-UP CARD, #1986
          "mirror the PYTHONPATH fix into icdev/, where the wheel reads it"
          (kpr-rvfy-06). The hook would have made that PR unnecessary.
          The shim rule saved 3 refusals and the extension rule 124.
  census  141 in scope, 0 routine fires, and it fires on its incident
          (exporter.py:434 `markdown`). Two apparent fires were SANDBOX
          ARTIFACTS of the replay, not findings: it copies only the changed
          .py files, so first-party modules (`cui_marker`, `audit_logger`) are
          unresolvable and read as undeclared third-party. Re-derived against
          the real tree those files report 0 sites. Counting them would have
          reported 1.00% for a check that refuses nothing.
Method, every number above, and the replay script IN FULL:
  docs/audits/mfx-ci-01-precommit-gate-fire-rate-survey.md
(a scratch path under .tmp/ is disposable and must never be cited as the way
to re-derive a published number -- same rule as never documenting a command
whose file does not exist).
Do NOT raise a budget, a timeout or a census ceiling to get a commit through,
and NEVER edit args/mirror_drift_baseline.yaml -- the fix is `--fix` and a
`git add` of the icdev/ copy.
