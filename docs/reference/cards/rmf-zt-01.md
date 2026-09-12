# A ZT check with NO PROBE behind it says `unknown`, never `passed` (rmf-zt-01)

> Moved verbatim from CLAUDE.md on 2026-09-12 (xrv-docs-02). The prose is the card's own record; only the leading `#` comment markers were stripped and the command runs fenced.

```bash
SC_STORAGE_BACKEND=sqlite python -m tools.security_canvas.zt_verdict_survey        # the flip survey
SC_STORAGE_BACKEND=sqlite python -m tools.security_canvas.zt_verdict_survey --json
python -c "from tools.security.stub_gate import stub_status; print(stub_status())"  # is this deployment stubbed?
```

device_compliance_scanner.scan_device evaluated every CIS/STIG check as
`bool(ctx.get(check_id, True))` -- AN ABSENT PROBE READ AS A PASS. So a device
the scanner had measured nothing about scored 100% compliant, and the ZIG
device-pillar maturity number was computed over it. `health_score` was the
same shape one level up: `trust.health_score if > 0 else 0.75`, a constant
wearing the name of a measurement, and 0.75 is what every live registry row
carries.
SURVEYED BEFORE ARMING, and the flip count is the size of the correction, not
a threshold: 108 recorded checks over 6 devices, 108 PASSES, 0 fails, every
device at compliance_score 1.0 -- and 96 of them (88.89% of recorded passes)
flip to `unknown`. The other 12 are the DERIVED checks and the survey says it
CANNOT TELL, because a stored row does not record whether the posture behind
it was measured. The census is what makes this a measurement rather than an
assumption: `ast` over tools/ AND icdev/tools/ finds 2 callers of
`scan_device` and ZERO supplying unconditional probe data.
  docs/audits/rmf-zt-01-zt-check-verdict-flip-survey.md
THREE VERDICTS, and `unknown` leaves BOTH SIDES of every ratio -- it is not a
pass and it is not a failure, so adding twenty unprobed checks to one pass and
one fail leaves the score at 0.5. `compliance_score`, `overall_pass` and
`health_score` are None -- NEVER 0.0, NEVER 1.0, NEVER True -- when nothing
was measured, with `score_basis` / `health_basis` naming which
(unmeasured | blended | cis_only | stig_only | fail_closed_unknown_posture).
A GAP AND AN UNKNOWN ARE DIFFERENT FINDINGS and are never merged: a gap is a
known deficiency with a remediation, an unknown is an unmeasured control with
an instrumentation task. `unknown_checks` is its own list.
THE FAIL-CLOSED 0.0 IS KEPT AND LABELLED. An unverifiable posture (CrowdStrike
stub, gate closed) still denies -- score 0.0, overall_pass False -- but
`score_basis` says `fail_closed_unknown_posture` so the zero is never quoted
as "this device scored zero on its STIGs". It was unknown either way.
`verdict` is persisted beside the legacy `passed` integer, which cannot spell
a third state and records an unknown as 0 (fail-CLOSED). An older canvas DB
gains the column IN PLACE -- CREATE TABLE IF NOT EXISTS never alters an
existing table, and without the ALTER the INSERT raises on every scan.
`run_fleet_scan` counts passing / failing / unmeasured as THREE numbers (an
uninstrumented fleet is not a broken one) and records zig-act-p1-09
`in_progress`, NOT `complete`, on a sweep that measured nothing.
ICDEV_ZT_ALLOW_STUB IS AUDITED, both legs: one `zt.stub_gate` audit_trail row
per device decision, permit AND refusal, namespaced in `action`
(device_compliance_scanner.stub_honored / .fail_closed). Migration
20260903003714 admits the event type -- a type the deployed CHECK does not
admit is rejected on log_event's first line and swallowed. Best-effort but
NEVER SILENT: `stub.audit.recorded` on the scan result says whether it landed.
A STUBBED DEPLOYMENT SAYS SO ON SCREEN: a standing banner on every /security
page (security_canvas/base.html `global_banner`, fed by a bp.context_processor
calling `stub_status()`). Its ABSENCE asserts the gate is CLOSED, not that the
estate is live -- `banner` is None only when `stub_allowed()` is False.
EXPECT THIS DEPLOYMENT TO READ `unmeasured` until a probe source is wired:
device trust is not required here, so the posture is `not_evaluated` and every
check is unknown. That is the SAME estate the board called 100% compliant,
described honestly. Wiring the probes is the follow-on; this removed the
fabrication that hid the need for it.
