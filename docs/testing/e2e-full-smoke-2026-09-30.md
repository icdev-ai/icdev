# CUI // SP-CTI

# Playwright E2E Full Smoke — 2026-09-30 (task-e2e-47b0efe6) — result counts

The pass / fail / skip totals of the 2026-09-30 `[AUTO-RUN] Playwright E2E Suite —
full smoke` run, extracted from its line-reporter output by step
`task-e2e-47b0efe6-d5-d2`.
Successor to [`e2e-full-smoke-2026-09-26.md`](e2e-full-smoke-2026-09-26.md).

This record covers the counts only. The route smoke gate, the build-log capture
and the artifact check belong to other steps of the card and are **not** asserted
here.

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

## Skips — 16

The line reporter does not name skipped tests, so the per-spec split is not
recorded. A skipped test is not a passing test, and none of these 16 asserted
anything.
