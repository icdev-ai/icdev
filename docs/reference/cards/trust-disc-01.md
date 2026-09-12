# Red-first proof — did the changed test actually go RED? (trust-disc-01)

> Moved verbatim from CLAUDE.md on 2026-09-12 (xrv-docs-02). The prose is the card's own record; only the leading `#` comment markers were stripped and the command runs fenced.

```bash
python tools/ci/red_first_gate.py --gate                 # the merge gate (0 clean / 1 finding / 2 could-not-run)
python tools/ci/red_first_gate.py                        # report only, always exit 0
python tools/ci/red_first_gate.py --files tests/test_x.py --gate
python tools/ci/red_first_gate.py --json --out red-first-proof.json
```

Checks out the merge base, applies ONLY the changed test file on top, and
asserts it does NOT pass there. The captured merge-base pytest output IS the
recorded RED. Exempt a file with a WRITTEN REASON in args/red_first_gate.yaml;
never `mode: advisory` and never a shell neutraliser.
