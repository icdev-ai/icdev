# An undeclared third-party import that fails SILENTLY (tsg-iso-03)

> Moved verbatim from CLAUDE.md on 2026-09-12 (xrv-docs-02). The prose is the card's own record; only the leading `#` comment markers were stripped and the command runs fenced.

```bash
python tools/ci/undeclared_import_census.py --check          # the gate; exit 1 on a NEW site
python tools/ci/undeclared_import_census.py --json
python tools/ci/undeclared_import_census.py --changed tools/foo.py --check
python tools/ci/undeclared_import_census.py --staged
python tools/ci/undeclared_import_census.py --prune          # drop entries whose site is gone
```

The finding is a CONJUNCTION, never the undeclared import alone: an
UNDECLARED third-party package imported inside a handler that SWALLOWS --
returns/passes/continues without logging, raising, or otherwise recording
that it fired. A genuinely optional dependency behind a handler that NAMES
the missing package is correct and passes (tools/blockchain/transports does
it properly), so a site leaves by fixing EITHER half.
`python-dateutil` had the bad shape at two sites: the stale reaper skipped
EVERY task and had never once run on CI, and every notification duration
rendered "unknown". It passed on Windows, where dateutil arrives
transitively as somebody else's dependency, and failed on the CI runner and
on any air-gapped install -- the deployment this project targets. That
asymmetry is what kept it alive. Both sites are now stdlib
(`tools.common.helpers.parse_utc_timestamp`); dateutil was DELETED, not
declared, and `tests/test_no_undeclared_dateutil.py` bans it outright.
Import name is mapped to DISTRIBUTION name (`yaml` -> `pyyaml`) from a
curated table -- `packages_distributions()` only knows what is INSTALLED, so
on the very runner where a package is missing it reports nothing.
210 sites grandfathered BY NAME in args/undeclared_import_census.txt;
`undeclared_max` in args/undeclared_import_gate.yaml may only go DOWN.
