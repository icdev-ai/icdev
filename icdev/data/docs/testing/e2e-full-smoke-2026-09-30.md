# CUI // SP-CTI

# Playwright E2E Full Smoke — 2026-09-30 (task-e2e-47b0efe6) — result counts

The pass / fail / skip totals of the 2026-09-30 `[AUTO-RUN] Playwright E2E Suite —
full smoke` run, extracted from its line-reporter output by step
`task-e2e-47b0efe6-d5-d2`.
Successor to [`e2e-full-smoke-2026-09-26.md`](e2e-full-smoke-2026-09-26.md).

This record covers the counts, the failing-title list and the final summary only. The route smoke gate, the build-log capture
and the artifact check belong to other steps of the card and are **not** asserted
here.

## Final summary (step `task-e2e-47b0efe6-d5-d4`)

```
Playwright E2E full smoke - 2026-09-30 AUTO-RUN (task-e2e-47b0efe6)
  passed  : 837
  failed  : 0
  skipped : 16
  total   : 853   (15.3m, EXIT 0)
  failing test titles: none (failed = 0, so the list of up to 20 is empty)
  artifact: .tmp/pw_out.txt  (parent worktree .tmp/worktrees/task-e2e-47b0efe6/,
            sha256 26e44d5abd98afbce1850e82e4e10d23421424fb4d1992cd2ea382ccf77ea6c4)
```

Re-checked on 2026-10-04 at step d5-d4: the artifact's hash still matches, and its
last lines still read `16 skipped`, `837 passed (15.3m)`, `EXIT 0`. Sources are the
sections below. This summary adds no new measurements.

## Counts

| Passed | Failed | Skipped | Total |
|---|---|---|---|
| **837** | **0** | **16** | **853** |

Validated: all three are non-negative integers, and their sum (853) is greater
than zero and equals the `[853/853]` total on the last progress line. Playwright
reported 15.3m and the wrapper recorded `EXIT 0`. The counts match the 2026-09-26
AUTO-RUN and the 2026-09-29 QA-agent sweep exactly.

## Which file was parsed

| | |
|---|---|
| Path | `.tmp/pw_out.txt` in the **parent task's worktree** (`.tmp/worktrees/task-e2e-47b0efe6/`) |
| Size / lines | 2,395,283 bytes, 40,246 lines |
| Written | 2026-09-30 07:41 local (server log spans 07:26:58 → 07:41:52) |
| SHA-256 | `26e44d5abd98afbce1850e82e4e10d23421424fb4d1992cd2ea382ccf77ea6c4` |
| Run target | `http://127.0.0.1:5093`, Playwright-managed server, cwd = the parent worktree |

The step card names `.tmp/pw_out.txt` without saying which checkout. There are two
on this host, and they are different runs:

| File | Written | Result |
|---|---|---|
| `C:\AI\ICDev\.tmp\pw_out.txt` (main checkout) | 2026-09-01 | 830 total: 814 passed, 16 skipped |
| `.tmp/worktrees/task-e2e-47b0efe6/.tmp/pw_out.txt` | 2026-09-30 | 853 total: 837 passed, 16 skipped |

The preceding step (`task-e2e-47b0efe6-d5-d1`) read the main-checkout file, which
is a month-old run of an 830-test suite and is not this card's output. The counts
above come from the second file. `.tmp/` is gitignored and each step runs in its
own worktree, so a step's own `.tmp/pw_out.txt` does not exist.

## How the counts were extracted

ANSI cursor sequences were stripped, then each summary word was matched at the
start of a line as `^\s*(\d+)\s+(passed|failed|skipped)\b`, skipping lines that
begin with `[` (progress and `[WebServer]` lines).

| Line | Text | Value |
|---|---|---|
| 40244 | `  16 skipped` | skipped = 16 |
| 40245 | `  837 passed (15.3m)` | passed = 837 |
| — | no `N failed` line | failed = 0 |

Playwright prints a `failed` line only when a test fails, so its absence is read
as zero. Three things in the file agree with that reading: there are no numbered
failure blocks, no `flaky`, `interrupted` or `did not run` lines, and the exit
code is 0.

The word `failed` does appear 9 times in the file. All 9 are `[WebServer]`
request-log lines, none is a test result. The anchored pattern does not match
them; the card's step-3 heuristic (`'failed' in out.lower()`) would.

## Failing test titles — 0

Compiled by step `task-e2e-47b0efe6-d5-d3` from the same file (same SHA-256, read
only). The list is **empty**: `[]`. The cap of 20 was not reached.

Every failure indicator the step names was searched for after stripping ANSI
sequences, and none is present:

| Indicator | Pattern | Hits |
|---|---|---|
| Failure markers | `^\s*(✘\|×\|✗)\s` | 0 |
| Numbered failure blocks in the error summary | `^\s+\d+\)\s+\[\w+\]\s+›` | 0 |
| `FAIL` annotations | `\bFAIL\b` | 0 |
| `N failed` / `flaky` / `interrupted` / `did not run` summary lines | `^\s*\d+\s+(failed\|flaky\|…)\b` | 0 |
| Retries | `Retry #\d+` | 0 |
| Indented `Error:` lines | `^\s+Error: ` | 0 |

The progress lines cover all 853 indexes (`[1/853]` … `[853/853]`, 853 distinct),
so the output is complete rather than truncated before a failure section. Of the
77 non-blank lines that are neither a progress line nor a `[WebServer]` line, 75
are the environment-diagnostics banner at the top and 2 are the summary lines.

The 9 lowercase `failed` hits noted above are the `[WebServer]`
`executescript: skipping failed statement` lines (206–236). They are server log
output, not test titles, and are not in the list.

## Skips — 16

The line reporter does not name skipped tests, so the per-spec split is not
recorded. A skipped test is not a passing test, and none of these 16 asserted
anything.
