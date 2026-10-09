# RCA: qa-fail-a81ac8c6a8bafbd2 looped on `no_commits` (diag-e4e93801fe)

**Written 2026-09-20** against `origin/main` at `e474ce6ca`.

## Verdict

**The defect was already fixed on `main` before the looping task's worker ran, so
there was nothing left to commit.** The loop is a task-lifecycle defect, not a
code defect: a `qa-fail-*` card was dispatched for a failure whose fix a sibling
card had landed in the meantime, and the `no_commits` verifier reads "no commit"
as a failure with no way to say "already on main".

## Evidence

- `qa-fail-a81ac8c6a8bafbd2` failed on `/slides/api/asset-smoke` exceeding its
  10 s budget. That route calls `generate_for_slide(preferred_providers=["slides_svg"])`,
  which paid a cold `import torch` (~7 s) for a GPU probe it never needed.
- Sibling card `qa-fail-108b2061b017b237` fixed exactly this in PR #2302
  (`0ae8e7eb1`, merged as `1fefbf0de`): `tools/viz/asset_generator.py` (and its
  `icdev/` mirror) now skips the probe when every requested provider is in
  `CPU_ONLY_PROVIDERS` (`slides_svg`, `slides_matplotlib`).
- Verified on this tree: `asset_generator.py` lines ~165-277 carry
  `CPU_ONLY_PROVIDERS` and `_available_providers(probe_gpu=not cpu_only)`.
- Attempt log for the source task: "I made no code changes. The fix for this task
  is already on `main`" — the worker was correct, and the verifier's
  `branch_commits_ahead == 0` classified a correct no-op as `no_commits`.
- That classification is documented as "not auto-remediable — human review
  needed", so every retry repeated the same outcome until `self_debug` filed
  this diagnosis card. Attributed cost of the loop before this card: ~$0.35 for
  this card's first attempt plus the source task's attempts.

## Root cause

Two `qa-fail-*` cards were seeded for the same underlying failure (the same
E2E timeout observed in two sweeps). Nothing deduplicated them by *fix
already landed*, and the worker's honest "already fixed" outcome has no
representation in the verifier — only `--force-done --reason` on the board.

## Disposition

- No source change is required; the slide asset-smoke fix stands as landed in #2302.
- The source task should be closed as landed-elsewhere
  (`python tools/kanban/cli.py --set-status qa-fail-a81ac8c6a8bafbd2 done --force-done --reason "fixed by #2302 (qa-fail-108b2061b017b237)"`),
  which is what the last attempt did.
- Follow-up worth a separate card (not done here, out of this task's scope):
  make the `no_commits` classifier consult `python -m tools.kanban.landed_check --task <id>`
  and the failing spec's current pass state before treating a zero-commit run as a
  failure, so a re-dispatched `qa-fail-*` card whose fix has landed resolves
  instead of looping.
