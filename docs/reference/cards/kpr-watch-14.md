# CLAUDE.md is DECLARED for the union rung, and its generated copy is DERIVED (kpr-watch-14)

> Moved verbatim from CLAUDE.md on 2026-09-12 (xrv-docs-02). The prose is the card's own record; only the leading `#` comment markers were stripped and the command runs fenced.

```bash
python -m tools.kanban.claude_md_union_survey            # would a union have been right? ground truth = what LANDED
python -m tools.kanban.claude_md_union_survey --json
python -m tools.kanban.union_resolver --list-rules        # the declared table, derivations included
```

THE RUNG HAD RESOLVED NOTHING. `pr_watcher.union_resolved` was 0 rows against
65 `union_refused` over 9 tasks (measured 2026-09-08) -- reflex-level
liveness green, the ACT reaching nothing. 63 of the 65 refuse on
`undeclared:`, and replayed through the SHIPPED `match_declaration`:
  every file in the set undeclared   53  84.1%
  MIXED -- a DECLARED file refused because an undeclared sibling shared
           the set                   10  15.9%  (qa-fail-6a87916931be3793 7,
                                                 dwr-ev-03 3)
Most-named undeclared path: CLAUDE.md, 35 of 63 (55.6%).
A THEORY ALREADY TESTED AND FALSE -- do not re-derive it. Every refusal
message names the `icdev/` copy while every declaration is spelled `tools/`,
which looks like a mirror-declaration gap. It is not: `match_declaration`
strips the mirror prefix. `_unmerged_files` returns git's SORTED
`--diff-filter=U` output and `icdev/` precedes `tools/`, so the mirror is
simply the first undeclared file the loop reaches.

DECLARING CLAUDE.md ALONE WOULD HAVE RESOLVED NOTHING. All 35 of those sets
ALSO carried `icdev/data/claude_bootstrap/CLAUDE.md` -- mfx-ci-04 requires
that payload to be regenerated whenever CLAUDE.md changes, so both files
conflict together on every card. It is NOT unioned. `derived_from` declares
it a VERBATIM COPY: after the source resolves, its bytes are written to the
copy and the copy is RE-READ to confirm (`derivation_parity`), never trusted
from the write. Two independent unions of two files that must be
byte-identical is how a branch lands out of `bootstrap_parity` -- the union
is only approximately order-stable, and blank-line placement alone would
break it. Proven equivalent to the regeneration rather than asserted:
`prebuild_bootstrap.SOURCES` maps the pair through a bare `shutil.copy2`
(its only transform is keyed on `settings.json.template`), and a test runs
that module's OWN copier over CRLF input. Running `prebuild_bootstrap.py`
itself was REJECTED -- it copies nine other trees, so a rebase would write
files the card never touched.
THE ONE SHAPE IT REFUSES TO REPAIR: a declared copy of a resolved source
that is NOT in the conflict set and is already stale. Reachable only from a
branch ALREADY out of parity (two sides that each regenerated would both
have changed it, so it would be unmerged too). Writing it would put a change
into the replayed commit that neither side made; leaving it would land a
resolved CLAUDE.md beside a stale copy. So it refuses and names the repair.
A `derived_from` declaration must name ONE LITERAL path -- a copy has
exactly one source, and a glob would silently claim several.

SURVEYED BEFORE ARMING, and the survey is a tool, not a paragraph. Ground
truth is WHAT LANDED, so it shares no code with the resolver's claim about
itself; the population is the merges GIT ITSELF conflicted on (`git
merge-file`), because a merge git resolved alone is one the rung never sees
and counting it would inflate the agreement rate. MEASURED 2026-09-08 over
origin/main: 28 merge commits touch CLAUDE.md, 17 git merged cleanly, 11
CONFLICTED -- and of those 11, **7 reproduce the human resolution byte for
byte, 4 differ only in BLANK LINES at a block seam, and 0 lose content in
EITHER parent orientation**. Every card appends its own block, so the two
sides insert at the same point: the `keep_both_blocks` shape exactly.
`lost_content` is None, NEVER 0, when nothing was measured.
END TO END on a real historical conflict (merge 29dac7848, the same two
files as all 35 refusals, replayed through the SHIPPED declaration): both
resolve, `derivation_parity` holds at 327,665 bytes, and the result is
BYTE-IDENTICAL to what the human landed.
WHAT IT BUYS, replayed over all 63: 34 (54.0%) become fully declared -- four
whole tasks (mfx-own-04 10, dwr-ev-03 9, dwr-fid-03 8, dwr-fid-02 7) whose
every refusal was this pair alone. The other 29 name ORDINARY SOURCE MODULES
(canvas_compliance/posture.py 14, security/row_security.py 7,
doc_modernization/redline_drafter.py 3, document_intelligence/
{ingest_orchestrator,suggestion_store,redraft}.py) and are NOT candidates: a
source module is not append-shaped, and declaring one to quieten a refusal is
how the wrong rule shipped a broken file twice on 2026-09-03.

NO PARTIAL RESOLUTION, and it is MEASURED rather than judged not worth it.
The 10 mixed refusals look like the case for resolving the declared subset
and leaving the rest conflicted. It buys ZERO: `rebase_recovery` runs the
whole rebase in a `tempfile.mkdtemp` worktree and calls `git rebase --abort`
then `_cleanup` on any outcome that is not `resolved`, so a half-resolution
is DELETED seconds later and no human ever sees it -- and the undeclared
files stay unmerged, so `rebase --continue` cannot proceed either. Zero
rebases completed, zero human effort saved, in exchange for the invariant
`resolve_index_conflicts` exists to hold ("a human sees the conflict, not a
half-resolution"). Pinned by a test that a mixed set still writes nothing.
NOT declared, and named: `icdev/data/claude_bootstrap/.claude/commands/
start.md` is a second bootstrap copy of a declared file, and it has appeared
in NO refusal -- the `Pages:` line is derived by `nav_paths.py --write` now,
so cards no longer hand-append to it. Declare it when it is measured, not
before.
Survey: docs/audits/kpr-watch-14-claude-md-union-survey.md
