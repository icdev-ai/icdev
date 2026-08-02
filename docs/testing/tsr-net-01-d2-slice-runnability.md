# TSR NET — slice runnability: how to invoke the NET slice (tsr-net-01-d2)

Diagnostic only. No source or test file was modified to produce this report.

**This is the companion note to [`tsr-net-01-d2-baseline.md`](tsr-net-01-d2-baseline.md).** That
document is the authoritative pass/fail classification for the slice. This one answers a narrower,
purely operational question the epic hits first: *what happens when you actually try to run
`tsr-net-01-slice.txt`, and why does the obvious command return `no tests ran`?*

## Finding — 3 files abort the entire pytest session

`docs/testing/tsr-net-01-slice.txt` (131 files, from `tsr-net-01-d1`) contains three files that call
`sys.exit()` at **module scope**. pytest's assertion-rewriting importer raises that through
collection as `SystemExit`, which is not a collectable error — it aborts the whole session with
`INTERNALERROR`, so **zero tests run**, including the other 128 files:

```
INTERNALERROR>   File "C:\AI\ICDev\tests\e2e_dual_track_lifecycle.py", line 535, in <module>
INTERNALERROR>     sys.exit(0 if FAIL == 0 else 1)
INTERNALERROR> SystemExit: 1

no tests collected
```

`--continue-on-collection-errors` does not help; `SystemExit` escapes the collection-error handler.

The three files:

```
tests/e2e_dual_track_lifecycle.py
tests/e2e_full_lifecycle_complex.py
tests/e2e_ndc_full_lifecycle.py
```

They are executable scripts, not pytest modules — each ends in a bare
`sys.exit(0 if FAIL == 0 else 1)` after printing its own PASS/FAIL tally.

### Verification

Each file was collected on its own against the shared checkout at `C:\AI\ICDev`:

```bash
python -m pytest <file> --collect-only -q -p no:cacheprovider --continue-on-collection-errors
```

| file | result |
|------|--------|
| `tests/e2e_dual_track_lifecycle.py` | `INTERNALERROR` / `SystemExit: 1` — **aborts** |
| `tests/e2e_full_lifecycle_complex.py` | `INTERNALERROR` / `SystemExit: 1` — **aborts** |
| `tests/e2e_ndc_full_lifecycle.py` | `INTERNALERROR` / `SystemExit: 1` — **aborts** |
| `tests/e2e_devops_twin.py` | collects cleanly, 0 tests — safe |
| `tests/e2e_network_nl_query.py` | collects cleanly, **34 tests** — safe |

The last two matter because an earlier revision of this note listed all five as aborting. They do
not. Both guard their exit behind `if __name__ == "__main__":`, so nothing runs on import:

```python
if __name__ == "__main__":
    sys.exit(main())          # e2e_devops_twin.py
```

Excluding them costs 34 real, passing tests and buys nothing. **Grep for `sys.exit` is not
sufficient — the guard is what decides.** Collect the file and look at the exit status.

## The runnable runlist

[`tsr-net-01-d2-runlist.txt`](tsr-net-01-d2-runlist.txt) is the 131-file slice minus the three
aborting files: **128 files**. Use it instead of `tsr-net-01-slice.txt` for any whole-slice
invocation.

```bash
export PYTHONPATH='C:\AI\ICDev'          # or the worktree root
export ICDEV_STORAGE_BACKEND=sqlite
export ICDEV_DASHBOARD_URL=http://127.0.0.1:5050   # else it inherits host.docker.internal
unset ICDEV_DB_PATH
python -m pytest $(tr '\n' ' ' < docs/testing/tsr-net-01-d2-runlist.txt) \
  -q --tb=no -p no:cacheprovider --timeout=90 --timeout-method=thread \
  --continue-on-collection-errors
```

Anyone re-running `tsr-net-01-slice.txt` verbatim gets `no tests ran` and may mistake it for a clean
tree.

## Per-file invocation avoids this entirely

`tsr-net-01-d2-baseline.md` ran **one pytest process per file** and so was never exposed to the
abort: a `SystemExit` in one file kills only that file's process. That is the more robust shape for
this slice — it also preserves per-file attribution when a file hangs or hard-crashes — and it is
what produced the authoritative classification. The runlist above is for the cases where a single
combined invocation is genuinely wanted.

## Note on the three aborting files

They are not broken *tests*; they are standalone scripts that predate the slice inventory and were
swept in by filename pattern. Fixing them is out of scope for a diagnostic card. The two options,
for whoever picks it up:

- guard the exit behind `if __name__ == "__main__":`, matching `e2e_devops_twin.py`, which makes
  them harmless to collect; or
- drop them from the slice inventory as non-pytest artefacts.

Until then the runlist is the workaround.
