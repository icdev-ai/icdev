# The page was live, the five endpoints it called were DEFINED NOWHERE (rmf-disc-02)

> Moved verbatim from CLAUDE.md on 2026-09-12 (xrv-docs-02). The prose is the card's own record; only the leading `#` comment markers were stripped and the command runs fenced.

```bash
python -m tools.network.discovery_store                    # ni_devices BY PROVENANCE + scan summary
python -m tools.network.discovery_store --list-scans
python -m tools.network.discovery_store --seed-synthetic --count 24   # FABRICATED demo fleet
python -m tools.genesis.reflexes.asset_discovery           # the 24h passive sweep, once
python tools/genesis/daemon.py --reflex asset_discovery --json
```

UI: http://localhost:5050/network/discovery

THE ROUTE RETURNED HTTP 200 AND THE WORD "SOON". `/network/discovery` guarded
render_template behind an os.path.exists on
`tools/network/dashboard/templates/...` -- a directory that has never existed
in this tree, so the guard was permanently false and the route returned the
string "Discovery page coming soon" with a 200. Every uptime check and route
smoke test looks for a 200. Measured live 2026-09-02: exactly that, while a
complete page (scan form, history table, device modal, import control, diff
panel) sat unrendered in the template directory. It also passed NO context, so
`scans`, `topologies` and both protocol flags were undefined and Jinja renders
an undefined as empty. Do NOT re-add a filesystem guard around a
render_template: a missing template raises something a reader can act on; a
guard that downgrades it to a 200 does not.
The five URLs its JavaScript fetched -- scan, scans/<id>, DELETE, import/<topo>,
diff -- were defined nowhere. A URL in a template is a STRING; no import error,
no route-coverage check and no test can see it, which is why this survived.
`tests/network/test_discovery_wiring.py` now greps the template's own fetch()
literals and asserts each resolves to a rule, so the NEXT unbacked fetch fails.

PROVENANCE IS THE WHOLE DESIGN, because ni_devices is not just a table.
args/docmod/inventory_feeds.yaml ranks it `evidence_kind: inventory` at the
BEST precedence there is -- an OBSERVED DEPLOYED ESTATE, which outranks every
design topology, and no quantity of drawings adds up to an observation. It held
0 rows and that emptiness was HONEST: no NetBox and no CSV export is reachable
from this deployment. The moment anything writes rows, "how many" stops being
the question and "what KIND" starts. `ni_devices.source` (migration
20260902210030) records it, and NOTHING infers it:
  discovery        a scan reached the host and the host ANSWERED
  synthetic        SyntheticDataEngine output. NOT evidence of anything.
  netbox / csv     a real inventory system
  topology_ingest  a device read off a DESIGN DIAGRAM -- a drawing of an estate
  NULL             written before the migration. UNKNOWN, never assumed.
`synthetic` and `topology_ingest` are excluded BY NAME from that feed via a new
`exclude_when: {column: [values]}` key (applied in PYTHON -- the learner's
contract is pure-Python aggregation, and a WHERE built from config is a dialect
problem and an injection surface for no gain). A NULL is NEVER excluded:
absent provenance is UNKNOWN, and dropping unknowns silently turns a feed into
"only the rows some writer happened to label". `topology_ingest` is excluded
because the `topology_nodes` feed ALREADY counts those same nodes as `design` --
reading them here would promote a drawing to `inventory` AND double-count it.
MEASURED after seeding 24 synthetic devices: the ni_devices feed reads 0
records, `topology_nodes` still reads 48. The seed lights up the inventory
SURFACES and moves the evidence engine not at all, which is correct.
`import_scan_devices` can only write `discovery` and `seed_synthetic_devices`
can only write `synthetic` -- neither takes a `source` parameter, and a test
reads their AST to prove it, because the failure mode is a future edit passing
a caller-supplied label through, which a behavioural test would still pass.

THE REFLEX IS PASSIVE-ONLY BY DEFAULT AND THAT IS A SECURITY POSTURE.
`ping` unless `allow_active_scan: true`. `snmp` presents a community string and
`ssh` presents an account password to live infrastructure, on a schedule, with
no human present -- a different act from an ICMP echo, and exactly what an
assessor asks about. It ships with `targets: []` (nothing can guess a
deployment's address space) and reports `unmeasured` / `no_targets_declared`
rather than a clean run it did not make: a reflex whose empty result is
indistinguishable from a healthy one is how a capability stays dead behind a
green dashboard. It NEVER seeds synthetic devices -- one deliberate CLI act is
a fixture, a 24-hour cadence is a data source. Registered in BOTH
daemon.REFLEX_NAMES and args/genesis_config.yaml, and DISPATCHED once through
the daemon: `reflex` went 77/78 consumed -> 78/78, `never_run_lifetime` empty,
with no budget bump. Note the trap it hit first: a reflex returning no
`success` key is scored a FAILURE on every cycle forever
(tools/daemon/base.py::classify_failure).

PVM WAS READING A TABLE WITH NO DDL ANYWHERE IN THE REPO. `nqe_client`'s local
fallback mapped every collection onto `nc_nodes`/`nc_edges`, and there is no
CREATE TABLE for either in this tree -- topology is JSON in
`topologies.graph_json`, inventory is `ni_devices`. So every local NQE query
raised `relation "nc_nodes" does not exist`, was swallowed by a broad `except`,
and returned []; `_build_device_map` built an empty map, and
`map_attack_surface` correlated every advisory against ZERO devices while
reporting success. Measured 2026-09-02: both tables absent, nc_attack_surface
0 rows. `network.devices` now reads `ni_devices` (devices_queried 0 -> 24) and
the collections with no backing table report `unsupported_locally` instead of
an empty list -- "this deployment cannot answer that" is not "the answer is
nothing", and conflating them is what hid this. The advisory matcher also
compared affected MODELS against the device HOSTNAME only; operators do not put
model numbers in hostnames, so it could essentially never fire. STILL EMPTY
after all that, for a different and honest reason: `nc_advisories` holds ONE
row, a stub with NULL affected_models_json. That is an advisory-ingestion gap,
not a wiring one, and it is reported rather than papered over with fabricated
CVEs.
