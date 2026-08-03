# CUI // SP-CTI

# Vendored-Copy Parity (cxo-doc-03)

Detects when a deliberately stdlib-only ICDEV module outruns the verbatim copies
that standalone apps keep in their **own, separate git repositories**.

## The problem this exists to catch

`tools/cortex/client.py` is dependency-free by contract (urllib/json/os only) so
standalone apps can vendor it verbatim into their own
`tools/integrations/cortex_client.py` — documented in the module's own docstring
(ctx-expose-06). Two consumers do this today, each in its own repo:

- `C:/AI/standalone/compass`
- `C:/AI/standalone/idea_lab`

**Measured 2026-08-02**, both copies had silently fallen behind canonical:

| File | Lines | Public methods | Missing vs canonical |
|------|-------|----------------|----------------------|
| canonical `tools/cortex/client.py` | 440 | 24 | — |
| compass copy | 389 | 22 | `bom`, `export_dashboard`, `price_cost_volume` |
| idea_lab copy | 304 | 19 | the above + `push_priced_cost_volume`, `push_staffing_matrix`, `transition_won_opportunity` |

No caller was broken, because the missing methods were simply unused. **That is
precisely why nobody noticed** — the drift is latent until someone calls a method
that isn't there. Worse, compass's vendored header carried an
`AHEAD OF CANONICAL` warning telling readers *not* to re-copy (written when
`push_staffing_matrix` had not yet landed upstream). That precondition had since
been met, so the warning was stale and was the only thing still discouraging the
update.

The copies themselves were fixed directly in their own repos on 2026-08-02.
**This feature is the detection**, without which a hand re-vendor simply drifts
again.

## Why `mirror_parity` could not be reused

`args/mirror_parity.yaml` / `check_icdev_mirror_parity` maps `tools/<root>` to
`icdev/tools/<root>` **inside** this repo. It has no concept of an out-of-repo
consumer, no way to express a path that may not exist on a given machine, and it
byte-compares. All three are wrong for this problem.

## Design

### Config, not code — `args/vendor_parity.yaml`

Following the `mirrored_roots` precedent: adding a vendor target needs **no code
change**.

```yaml
path_defaults:
  ICDEV_STANDALONE_ROOT: C:/AI/standalone

vendored_copies:
  - source: tools/cortex/client.py
    last_synced: "2026-08-02"
    consumers:
      - name: compass
        path: "${ICDEV_STANDALONE_ROOT}/compass/tools/integrations/cortex_client.py"
      - name: idea_lab
        path: "${ICDEV_STANDALONE_ROOT}/idea_lab/tools/integrations/cortex_client.py"
```

`${VAR}` resolves from the environment first, then `path_defaults`. Mirrored to
`icdev/data/args/vendor_parity.yaml`, matching how every other coherence-gate
config ships.

### Public API comparison, not bytes

`_public_api()` parses with `ast` and collects classes, module-level functions,
and methods with their **parameter names** — including `/`, `*`, `*args`,
`**kwargs` markers and whether a default is *present*.

Deliberately ignored, because they are legitimate noise between a file and its
verbatim copy:

- the provenance header the consumer adds
- **line endings** — canonical is LF, both consumers are CRLF under
  `core.autocrlf=true`; a byte or line compare would be 100% false positives
- docstrings, comments, annotations, and default *values*

Parameter **names** are kept, because every consumer call site depends on them —
renaming `question` to `query` is a breaking change even though the method still
exists.

### Subset, not equality

Canonical's public API must be a **subset** of each copy's. A copy that is
temporarily *ahead* of canonical is allowed — that is the normal state while a
method is being proven out in a consumer before it lands upstream, and it is
exactly the situation that produced compass's stale `AHEAD OF CANONICAL` header.

### Absent consumers skip, never fail

The standalone repos are not checked out on most machines, and never in CI. Any
consumer whose path does not exist — or whose `${VAR}` placeholder resolves to
nothing — is recorded as a skip with a note and does **not** fail the check. A
copy that exists but cannot be parsed is likewise skipped rather than failed.

### Severity

| Scope | Severity |
|-------|----------|
| `--changed-files` includes a declared source | **fail** (per-task gate) |
| full-repo sweep | **warn** (report-only, like `mirror_drift`) |
| no declared source in the changed set | pass, short-circuits immediately |

Not in `HEAVY_CHECKS`, so it runs in both the `fast` and `full` tiers; it is
diff-scoped and costs ~10ms.

## Usage

```bash
python tools/workflow/coherence_checker.py --check vendor_parity --json
python tools/workflow/coherence_checker.py --check vendor_parity \
    --changed-files "tools/cortex/client.py" --gate
```

Point the check at consumer repos in a non-default location by exporting
`ICDEV_STANDALONE_ROOT`.

## Verification (2026-08-03)

Verified against the **real** consumer repos, not only fixtures:

- **In sync → pass.** Both copies report `in sync (23 public members)`.
- **Drift → fail.** Appending a `brand_new_method` to canonical made the gate
  exit `1` and name the missing member for both consumers.
- **Absent consumer → pass.** With `ICDEV_STANDALONE_ROOT` pointed at a
  nonexistent directory, both consumers skip with a note and the gate exits `0`.

`tests/workflow/test_vendor_parity.py` — 13 tests, hermetic (`PROJECT_ROOT`
monkeypatched to a temp tree, so they never depend on whether `C:/AI/standalone`
is checked out). Fast-tier coherence gate: 49 checks, 0 failures.
