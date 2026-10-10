# The Compliance Posture widget's TWO remaining perfect scores, refused (rmf-rail-02)

> Moved verbatim from CLAUDE.md on 2026-09-12 (xrv-docs-02). The prose is the card's own record; only the leading `#` comment markers were stripped and the command runs fenced.

```bash
python -m pytest tests/test_compliance_posture_rail_02.py -q
python tools/awareness/claim_verifier.py --claim posture_zero_trust_has_scan_corpus
python tools/awareness/claim_verifier.py --claim posture_security_agrees_with_its_assessments
SC_STORAGE_BACKEND=sqlite python -m tools.security_canvas.zt_verdict_survey     # the INDEPENDENT side of the ZT claim
```

rem-hyg-09 routed the per-canvas scores through `_score_or_none`; two blocks
in the same function never adopted it and both drew a full green 100 bar.
MEASURED 2026-09-07 on the live board, from compute_canvas_posture itself:
  Zero Trust 100.0  the latest-per-pillar slice of zig_maturity_scores is
                    EXACTLY 1.0 for all seven pillars, one run on 2026-06-27
                    (three runs 14 minutes apart: all 0.0, then 0.73-0.87, then
                    all 1.0 -- a seeded run, not an estate), while
                    zig_device_compliance_scans DOES NOT EXIST on the backend
                    and the device posture reads not_evaluated. Now `score`
                    None, `score_basis: unmeasured:no_device_scan_corpus`,
                    `declared_maturity: 100.0` carried, labelled, never
                    scored. The maturity number is a reduction over DECLARED
                    statuses (zig_capabilities / zig_activities); it becomes a
                    posture SCORE only over a scan corpus that holds rows.
  Security 100.0    beside "24 open findings" -- the 24 was COUNT(*) of
                    sc_assessments wearing a findings label. The 13 latest
                    assessments carried 501 findings on their own
                    findings_json and every one was stored posture_grade F.
                    `open_findings` now counts those findings (in Python,
                    never SQLite-dialect JSON SQL); a PERFECT 100.0 beside
                    open findings -- or findings that could not be read,
                    which is not "none" -- is `score_basis: contested` and
                    the score is None. A measured 80.0 with 0 findings, and
                    an honest 100.0 with 0 findings, still score.
  overall           `if zig_score > 0` dropped a MEASURED 0.0 from the
                    denominator, so the headline read HIGHER because Zero
                    Trust scored zero. Every fold is `is not None` now
                    (pinned by an AST test), and the overall is None, never
                    0.0, when nothing was measured. Live: 79.0 -> 73.8.
Every row carries `score_basis` (measured | unmeasured | contested | ...), so
a None score always has its reason beside it; the widget already renders a
null as "Not assessed" with an empty bar. `_has_rows` now ROLLS BACK after a
failed probe: on PostgreSQL a probe of an absent table aborts the transaction
and blanked the Zero Trust `last_assessed` on the first live run.
NOT fixed here, and named: sc_assessments.risk_score carries TWO semantics --
the STRIDE engine stores `100 - penalty` (0.0 = grade F) while the pipeline
writers store a penalty (F at >= 20) -- so `100 - avg(risk_score)` inverts the
engine's rows; a grade-F engine row at 30.0 reads 70.0. Choosing one meaning
for the column is a data-model card. And the DEVICE scanner is still unwired
(rmf-zt-01): the honest Zero Trust bar is empty until it writes rows.
TWO STANDING CLAIMS (autonomy-lrn-01), both `agrees` on the live board today.
`posture_score_needs_evidence` guards the loop rem-hyg-09 fixed and did not
catch either block -- the "same defect at a second site" case. Reported: the
two rows as the widget reads them. Derived, sharing no code with posture.py:
zt_verdict_survey.read_corpus / live_posture (rmf-zt-01's own verdict) for
Zero Trust; the latest assessments' OWN posture_grade + findings_json for
Security. A refused number always agrees; a number needs its evidence.
DO NOT delete or rewrite the 2026-06-27 zig rows (they are the evidence) and
DO NOT re-run a fleet scan to refresh the number -- unprobed IS the posture.
