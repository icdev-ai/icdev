# A closed census may LOSE names and must never GAIN one (cef-ci-02)

> Moved verbatim from CLAUDE.md on 2026-09-12 (xrv-docs-02). The prose is the card's own record; only the leading `#` comment markers were stripped and the command runs fenced.

```bash
python tools/ci/census_growth.py --check           # the gate (0 clean / 1 gained / 2 could-not-compare)
python tools/ci/census_growth.py --json
python tools/ci/census_growth.py --base origin/main --root .
```

`gated_test_list.py --check-coverage` enforces args/ci_test_backlog.txt by a
COUNT, and a count is exactly what an ENUMERATED census exists to distrust —
that file's own comment says so ("a bare count can be held constant while the
set churns"). Identity was tracked and never RATCHETED: nothing compared a
closed census against its previous self, so the ceiling's slack was the whole
guard. Measured on main at 42f7ea894 the slack was 8 slots (`backlog_max` 1711
against 1703 entries, while `skip_max` was 81 against 81) — enough to delete
the core.d fragments naming eight gated CEF suites, among them
tests/cortex/test_resolve_facade.py and test_resolve_trust_loop.py, append
those paths to the census, and have --check-coverage report "0 unlisted" and
exit 0. `backlog_max` is now 1703; the SET rule is what actually closes it.
Set-compares args/ci_test_backlog.txt and args/ci_skip_census.txt vs the MERGE
BASE. Catches the SWAP a ceiling structurally cannot — one line out, one in,
count unchanged, a suite gone from CI.
Deliberately NOT a tighter ceiling: "ceiling must equal the count" red-lights
main whenever two concurrent PRs each gate a backlogged file and each lower it
by one, and census deletions land ~5x/day here. Set monotonicity has no such
interaction — two PRs that each gained nothing merge to a tree that gained
nothing. Do NOT "fix" this by tightening the ceiling instead.
SURVEYED BEFORE ARMING: every commit touching args/ci_test_backlog.txt since
adoption (ceb10709b) is +0 — 35 commits, 150 deletions, ZERO additions, so it
refuses nothing the census's history contains. args/ci_skip_census.txt has had
no post-adoption commit, so its rate reports UNMEASURABLE, never a clean zero.
A census ABSENT at the base is `introduced`, not grown. Exit 2 (needs
fetch-depth: 0) stays red — a gate that could not run is not one that found
nothing. args/undeclared_import_census.txt and args/kanban_raw_insert_census.txt
are the same discipline and are deliberately NOT registered: unrelated to test
gating and unsurveyed. Adding one is a `CENSUSES` entry plus its own survey.
