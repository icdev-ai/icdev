# A sweep whose timeouts fell inside a HOST STALL can now say so (qa-fail-5cacee65f1d03c8c)

> Moved verbatim from CLAUDE.md on 2026-09-12 (xrv-docs-02). The prose is the card's own record; only the leading `#` comment markers were stripped and the command runs fenced.

```bash
python tools/testing/qa_agent_runner.py --run --json          # every batch carries `stall_census`, every failure `during_stall`
ICDEV_QA_STALL_SAMPLER=0 python tools/testing/qa_agent_runner.py --run   # off, and REPORTED as disabled_by_env
ICDEV_QA_STALL_SAMPLE_SECONDS=2 python tools/testing/qa_agent_runner.py --run --canvas finetune --no-record --json
```

MEASURED 2026-09-11, run qa-1789161604: 4 of 849 E2E tests timed out and all
four sat inside windows where the isolated server's request log went SILENT
for 20-59 s (13 such gaps) while a hand sampler's 5 s loop stretched to 12-14 s
and /api/health still answered in 0.10 s -- the HOST stalled, not the app, and
every page answered in under 2 s minutes later. The runner recorded that sweep
IDENTICALLY to four product defects. Of the card's three candidate repairs
this is the third; the first (`goto(url, {waitUntil:'domcontentloaded'})`)
was SURVEYED and not taken -- 282 goto calls in 68 specs, 233 of them
immediately followed by a domcontentloaded wait, a suite-wide rewrite of what
the tests assert -- and the second (the nav sweep's 10 s per-link budget) is a
threshold raised to quieten an alarm.
A `StallSampler` thread now runs beside EVERY batch: every 5 s it times
GET /api/health at the SAME origin the suite navigates to
(`resolve_e2e_base_url`, the Python mirror of tests/e2e/fixtures/base_url.ts
-- same three variables, same order) and measures how late its own sleep
returned. TWO SIGNALS, NEVER MERGED, because they send a reader to different
places: `host_stalled` (sleep overshoot >= 2.0 s; the incident measured 7-9 s,
ordinary jitter on this host is 6-15 ms) and `health_slow` (the server
answered, in >= 2.0 s). `unreachable` is counted and is NOT a stall kind --
Playwright starts and stops its own webServer per batch, so a probe before
the server is up is expected. Each failure's Playwright `startTime` +
`duration` window is intersected with each stall sample's covered interval
[sleep started, probe finished] to give `during_stall`: True | False | None,
and None is UNMEASURED (sampler off, no sample, a result with no timing),
never folded into False. `failures_during_stall` is None, never 0, when no
batch was sampled. THE VERDICT NEVER MOVES THE STATUS: a stall explains a
timeout and does not excuse it; re-running to get green is what the card
forbids.
PROVEN END TO END on the live dashboard, 2026-09-11 (`qa-1789165702`, the
read-only finetune spec, one batch, 153 samples at 2 s): 3 of 8 red, the
first with NO slow sample in its window (reported False) and the other two
inside a stretch where /api/health took 3.9 s then 15 s three times running
(the probe budget) -- `health_slow` 6, `host_stalled` 0, overshoot never above
15 ms. A DIFFERENT verdict from the incident's, which is the point of two.
FOUND ON THE WAY: `localhost` resolves to ::1 first on this host, the
dashboard binds IPv4 only, and the refusal takes 2.05 s -- so probing
`http://localhost:5050` costs 2.08 s a sample against 0.06 s direct, exactly
the slow threshold, and a default run would have read EVERY sample as
`health_slow`. `probe_url` connects that one hostname as 127.0.0.1 and the
census records what was probed. Chromium races both families and pays none
of it. AND `record_failure` had a definition and NO call site in the runner
(the sweep report's s8): `--record` now writes one `ace_qa_failures` row per
failure, carrying the card id only when a card was ACTUALLY created for it.
NOT built, and named: the census lives in the run JSON `report_path` names,
under a disposable `.tmp/`; `ace_qa_runs` has no column for it and this card
adds no migration, so the TABLE row still cannot say "stall" -- the sweep
report written from the JSON can. The sampler is in the runner's own process,
so `host_stalled` proves THIS process was starved and infers the host. It
does not read the server's request log, so the 20 s+ silence that was the
incident's primary evidence is not re-derived here.
