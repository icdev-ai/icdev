# rmf-zt-01 — ZT check verdict flip survey (recorded before arming)

**Date measured:** 2026-09-02
**Re-derive:** `SC_STORAGE_BACKEND=sqlite python -m tools.security_canvas.zt_verdict_survey --json`
**Tool:** `tools/security_canvas/zt_verdict_survey.py` (report only, no `--gate`)

## The defect

`tools/security_canvas/device_compliance_scanner.py::scan_device` evaluated every
CIS Control and STIG check as:

```python
passed = bool(ctx.get(check_id, True))   # optimistic default when no probe
```

An absent probe read as a **PASS**. So a device the scanner had measured nothing
about scored 100% compliant, and the ZIG device-pillar maturity number was
computed over it.

`health_score` carried the same shape one level up:

```python
health_score = trust.health_score if trust.health_score > 0 else 0.75
```

`0.75` is a constant wearing the name of a measurement — and it is the number
every live registry row carries.

## What was measured

Corpus: `zig_device_compliance_scans` on the SQLite canvas database
(`data/security_canvas.db`), written 2026-06-02.

| | |
|---|---|
| corpus state | `rows` (sqlite) |
| recorded checks | **108** over **6** devices |
| recorded pass | **108** |
| recorded fail | **0** |
| **flips to unknown** | **96** |
| **flip rate** | **88.89%** of recorded passes |
| undetermined (derived checks) | 12 |
| unattributable | 0 |
| unchanged | 0 |

Every one of the six devices carried `compliance_score = 1.0` and
`health_score = 0.75`.

Call-site census (`ast`, over `tools/` **and** `icdev/tools/`): **2 callers of
`scan_device`, 0 supplying unconditional probe data.** That is what turns "the
default fired" from an assumption into a measurement — with no probe supplied
anywhere, every ctx-driven row in the corpus was the optimistic default and
nothing else.

Live posture on this deployment: `not_evaluated`
(`ICDEV_DEVICE_TRUST_REQUIRED` unset), `stub_allowed = True`
(`ICDEV_ZT_ALLOW_STUB=1` is set in the running environment).

## Three things the survey deliberately does NOT merge

* **`undetermined_derived` (12) is neither a flip nor a non-flip.**
  `cc-07-continuous-mon` and `stig-antivirus` are derived from the device-trust
  adapter, and a stored row does not record whether the posture behind it was
  measured. The survey says it cannot tell, and reports the live posture beside
  it instead of guessing. (Under the new rule they are `unknown` on this
  deployment, because the live posture is `not_evaluated` — but that is a fact
  about today, not about the June rows.)
* **`absent` is not `empty`, and neither is "zero flips".** The scan tables are
  created lazily by the scanner itself. On the live **PostgreSQL** canvas
  database both are ABSENT — the scanner has never run there — which the survey
  reports as `measurable: false` with `flips_to_unknown: null`. A clean zero
  there would have been a fabrication.
* **A forwarded optional context is not probe data.**
  `context=probes.get(h)` (which is what `run_fleet_scan` now does) is counted
  as `conditional`, never as `supplies`. Counting it would report an
  uninstrumented fleet as an instrumented one — the exact defect being measured.

## What was armed on the strength of it

96 of 108 recorded checks change verdict. That is not a threshold decision — it
is the size of the correction, and it is large because the old default was
unconditional. Nothing here is being suppressed to keep a number green:

* checks return `pass | fail | unknown`; `unknown` leaves **both** the numerator
  and the denominator of every score;
* `compliance_score` / `overall_pass` / `health_score` are `None` — never 0.0,
  never 1.0, never `True` — when nothing was measured, and `score_basis` /
  `health_basis` name which;
* the fail-closed 0.0 for an unverifiable posture is kept, and **labelled**
  `fail_closed_unknown_posture`, so it is never read as a measurement of the
  checks;
* `run_fleet_scan` counts `passing` / `failing` / `unmeasured` separately and
  records ZIG activity `zig-act-p1-09` as `in_progress`, not `complete`, when a
  sweep measured nothing;
* `ICDEV_ZT_ALLOW_STUB` writes one `zt.stub_gate` audit row per device decision
  — the permit leg **and** the refusal leg;
* every `/security` page carries a standing banner while the gate is open.

## Consequence to expect on this deployment

Until a probe source is wired, `run_fleet_scan` reports `unmeasured` for every
device, `fleet_compliance_score: null`, and leaves `zig-act-p1-09`
`in_progress`. **That is the correct reading, not a regression** — it is the
same estate the board previously described as 100% compliant, now described
honestly. Instrumenting the probes is the follow-on work; this task removed the
fabrication that hid the need for it.
