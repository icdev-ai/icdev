# Promote an ungated test module — but only if it is green BOTH WAYS (rem-tst-06)

> Moved verbatim from CLAUDE.md on 2026-09-12 (xrv-docs-02). The prose is the card's own record; only the leading `#` comment markers were stripped and the command runs fenced.

```bash
python -m tools.ci.gate_promoter --plan --limit 10        # candidates, runs nothing
python -m tools.ci.gate_promoter --limit 10               # verify, report only
python -m tools.ci.gate_promoter --limit 10 --apply       # write + ratchet
```

`ungated_test_census.py` measures which of the ~1,700 ungated modules pass and
deliberately promotes nothing. NOTHING consumed that measurement: the snapshot
was three days stale when this shipped, and the backlog shrank only when a
human hand-moved a file. A measurement nobody acts on is the same defect as a
capability nobody calls.
THE SAFEGUARD IS THE POINT. The census runs each module ALONE, and green-alone
is NOT green-in-suite — four cortex/dashboard modules flipped on run order via
a shared app singleton, and kpr-watch-03 (2026-08-19) failed in CI, passed
alone, and read as flake when it was an order dependency. So a module is
promoted only when it passes ALONE (re-verified, never trusted from the
census) AND IN-SUITE appended to the gated set in ONE process.
FAIL-CLOSED ON THE BATCH: an in-suite failure promotes NOTHING from that
batch. The culprit may be an INTERACTION between two survivors, so promoting
"the innocent ones" could ship exactly the interacting pair — re-run with a
smaller --limit to isolate. Writes a PER-RUN core.d fragment (never core.txt),
and RATCHETS backlog_max DOWN; it can never raise it. Never a gate.
Weekly via .github/workflows/gate-promoter.yml, which opens a PR rather than
pushing — the promotion is then proved by the same gated suite it modifies.
