# A twin over the EMULATOR, read through the broker, marked `emulated` (flx-twin-01)

> Moved verbatim from CLAUDE.md on 2026-09-12 (xrv-docs-02). The prose is the card's own record; only the leading `#` comment markers were stripped and the command runs fenced.

A library, no CLI. Import it:
  from tools.twin_core.registry import TwinRegistry
  twin = TwinRegistry.get("floci")
  snap = twin.take_snapshot("local", label="pre-apply")   # 7 brokered reads
  env  = twin.simulate_delta("local", {"services": ["lambda", "s3"]})
  twin.latest_status("local")   # the newest PERSISTED verdict; probes nothing
The twelfth adapter in tools/twin_core/adapters/ (filesystem-discovered), over
the LOCAL floci AWS emulator. `floci` is a `core_extension` in
args/component_registry.yaml, not a canvas -- it has no page, so the 8-point
page gate does not apply; the adapter renders inside the existing Twin
Observatory and emits the existing `twin_snapshot_taken` event.

EVERY READ GOES THROUGH tools/databridge/broker.py::fetch as
`twin_observatory_analyst` -- the flx-bridge-02 grant -- so each of the seven
logical tables is authorized against the manifest and lands one
databridge_agent_access_log row. MEASURED 2026-09-05 on a scratch board: 57
rows, 28 allowed and 29 denied, every one under that agent id. Importing
FlociConnector and calling read() would return the SAME rows with NO
authorization check and NO audit row -- the ungoverned side channel cef-fnd-03
exists to close, and nothing about the values would look wrong. A structural
AST test refuses a direct connector read and pins the import list to
declarations + capability predicates (TABLES, boto3_available,
table_needs_boto3, table_is_docker_backed, table_service).

THE BROKER NOW RELAYS THE CONNECTOR'S STATUS, and it had to. `fetch` read
`response.data` and nothing else, so a connector answering `disabled`,
`unsupported_without_docker` or `error` came back as `ok=True, row_count=0` --
indistinguishable from a table that answered and held no rows. That is the
exact conflation floci_connector._unsupported_response was written to prevent,
undone one layer up. FetchOutcome gains `connector_status` /
`connector_errors`; `ok` is deliberately UNCHANGED, so no existing caller's
verdict moves.

FOUR VERDICTS, AND UNKNOWN IS NEVER PASS. Every one MEASURED live against
floci/floci:2.0.1 on 2026-09-05:
  pass     every declared table answered. resource_count 0 with an empty
           emulator and 1 after creating one bucket -- a MEASURED zero.
  warn     a container-backed table reported `unsupported_without_docker`
           (FLOCI_DOCKER_SOCKET pointed at an absent unix socket: lambda_
           functions refused, 6/7 answered, rc still 1), or a boto3-backed
           table has no SDK. boto3 is NOT in requirements.txt, so five of the
           seven tables error on a host without it -- that error did not come
           from the emulator, no socket opened, and scoring it `fail` would
           blame the estate for a local tooling gap. It is an UNANSWERED
           table, basis `sdk_unavailable`, kept apart from the docker case
           because the repairs differ.
  fail     a REACHABLE emulator returned an error.
  unknown  disabled | unreachable | broker_denied. resource_count is None --
           NEVER 0 -- because an unreachable emulator holds an UNKNOWN number
           of buckets and 0 asserts it holds none.
`saas_base.connect` returns False whenever health_check is not `healthy`, so
the connector's own `disabled` status NEVER reaches a brokered read and both
"switched off" and "on but nothing answered" arrive as one broker refusal. The
verdict is `unknown` either way; the BASIS is recovered from STRUCTURED facts
-- broker.list_available() for the grant, emulator.enabled() for the switch --
and never from the refusal's prose. Measured: FLOCI_ENABLED unset ->
`disabled`; set with no container -> `unreachable`; no connection row ->
`broker_denied`. Do NOT collapse them: one is a flag, one is
`docker compose --profile floci up -d`.

PROVENANCE IS THE WHOLE DESIGN, and it is asserted THREE ways. target_csp is
`aws` / us-gov-west-1 so the GovCloud presets in args/twin_target_presets.yaml
and their service_parity flags apply to simulate_delta -- but every snapshot
carries provenance `emulated` (twin_core.schema.PROVENANCE_EMULATED, the
ni_devices.source vocabulary where `synthetic` is spelled out as "NOT evidence
of anything"). A floci S3 bucket is a container's in-process state and ranking
it beside a real inventory is the rmf-disc-02 defect one layer up. So:
`_persist_snapshot` takes NO provenance parameter and binds the module
constant; a test reads its AST and refuses one (a behavioural test over
today's callers -- which pass none -- would still pass the day somebody
threads a kwarg through); and migration 20260905070028 DERIVES a CHECK from
schema.SNAPSHOT_PROVENANCES, so the database refuses one too.
simulate_delta carries `provenance` in `extra` on EVERY envelope including a
clean one -- a consumer that only learns the estate was emulated when
something is wrong will read a clean run as evidence about a real deployment.

Table `floci_twin_snapshots` (owner `it` by PREFIX RULE in
args/schema_ownership_rules.yaml, never a manifest line), registered as a
substrate in args/capability_consumption.yaml -- it reads `absent` until
`python tools/db/migrate.py --up` runs, which is the tool's own distinction
between "a migration never ran" and "a writer never ran".
NEVER source a performance, cost or capacity claim from this twin: an emulator
reproduces the AWS API contract, not its performance characteristics
(docs/spikes/twx-spk-01-localstack-go-no-go.md's standing guard).
