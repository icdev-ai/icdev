# rea-spike-01 — does REA earn its place? GO/NO-GO per target type, Windows and Linux

Go/no-go spike, scratch code only. Nothing under `tools/` changed. Measured on 2026-10-11.
The harness, fixtures and raw outputs live outside the repo in the session scratch directory
(`%TEMP%\rea-spike-01\`) and are not committed: the HAR holds a live session's CSRF token.

## Pinned version

| Item | Value |
|---|---|
| Package | `rea-agents@6.4.0` (MIT), installed with `npm install --save-exact`; Linux used `npm ci` from the same lockfile |
| npm integrity | `sha512-Vj1zWfSlna5bphGoaoI/TcVBuYvze9u+OyPC3Shz8YkBqaCTqB4COnnRoX7XDwMpwI24ZlFEel2yEj0k8LTWEQ==` |
| Tarball | `https://registry.npmjs.org/rea-agents/-/rea-agents-6.4.0.tgz` |
| Engines | `node ^22.19.0 \|\| ^24.11.0 \|\| >=26.0.0`. Measured on Node **24.11.0** on both OSes |
| Ghidra | 12.1.4 (`ghidra_12.1.4_PUBLIC_20260921.zip`, sha256 `ddac49f903da9d5bac833e5cc79395098b9c33cfd3279be5f31bd00387d2d4db`, matches the GitHub release digest) |
| JDK | Temurin 21.0.12.1+1 (Windows zip sha256 `f9d6e191…8b4e`, Linux tar sha256 `ce79869e…ee94`). The ICDEV baseline used Temurin 21.0.12+8 (`9ba963ee…21d0`), see §ICDEV baseline |
| Not run | `rea setup` (it rewrites harness configs), any Hopper install, any IDA config. `rea doctor` lists Hopper/IDA as `missing_analysis_engine`, and that is how it stays |
| MCP surface | 170 tools over stdio. The server spawns in ~1.4 s (Windows) and ~1.1–1.3 s (Linux) |

## Verdict

"Linux" below means a **Debian 13 container on this host**. The Omarchy laptop was unreachable (see §Linux).
Debian is outside REA's supported list, but every GO row ran there once `procps` was present.

| Target type | Windows 11 (host) | Linux (Debian 13 container) | Decision for later cards |
|---|---|---|---|
| **Native PE (x86-64), Ghidra** | **GO**: 24.1–24.3 s cold, deterministic, no egress | **GO**: 29.0–29.6 s cold (2-CPU cap), deterministic, no egress. The pseudocode is identical to the Windows output apart from CRLF | Keep, both OSes |
| **Native ELF, Ghidra** | **NO-GO**: refused (`Windows Ghidra P0 accepts PE targets only`) | **GO**: 16.0–16.1 s cold. Name lookup works (`icdev_checksum` decompiled near-verbatim) | Keep **Linux-only**. ELF on Windows is dropped |
| **.NET CIL (static)** | **GO**: <0.02 s per call | **GO**: <0.06 s per call | Keep, both OSes. No engine needed |
| **JS bundle + source map** | **GO with conditions**: 2.0 s | **GO with conditions**: 2.0–2.1 s | Keep, but always `detail: summary` plus `export_evidence_bundle` (complete inline > MCP budget) |
| **HAR capture** | **GO with conditions**: 0.6 s | **GO with conditions**: 0.7 s | Keep, but only via export, and ICDEV must pass `sensitive_values` (a body CSRF token survives) |
| **WASM** | **NO-GO**: `open_binary` → `unsupported binary format` | **NO-GO**: same error | **Drop.** REA 6.4.0 has no WebAssembly analysis at all ("wasm" appears only in its EVM/evmole internals) |
| JS source recovery (`recover_javascript_sources`) | NO-GO: needs a caller-supplied Wakaru **Linux x64** binary | not measured (Wakaru not supplied) | Out of scope until a card supplies Wakaru |

**Overall: GO for REA as ICDEV's no-source front end**, scoped to the GO rows. The deciding
evidence is in the head-to-head (§ICDEV baseline). On the card's own engine, Ghidra 12.1.4, ICDEV's
existing `ghidra_headless.py` produced **no result at all** on either OS. REA's Ghidra provider decompiled
the same binaries, deterministically and identically across OSes, with content-addressed evidence ids and no
egress beyond its own loopback/AF_UNIX engine bridge.

**Still owed:** an Omarchy/Arch measurement (the card's named Linux host, and on REA's supported list).
Every Linux number here is from Debian, which REA's doctor calls `unsupported_host`.

## Measurements: Windows 11 (10.0.26300, Node 24.11.0, Ghidra 12.1.4, Temurin 21.0.12.1)

Each target ran **twice**, each run against a **fresh** MCP server process (cold start), with the same
call sequence. Latencies are per MCP `tools/call`. Bytes are the size of the response text.

| Target | Key call | Cold latency run1 / run2 | Output size | Determinism (run1 vs run2) |
|---|---|---|---|---|
| PE `fixture.exe` (169 KB, zig 0.15.2 `-O1`, symbols in a separate .pdb Ghidra did not load) | `binary_overview` (= Ghidra start + auto-analysis) | **24.30 s / 24.07 s** | 3.1 KB | identical |
| | `trace_feature "ICDEV_REA_SPIKE_FIXTURE"` | 0.012 / 0.011 s | 3.1 KB | identical. It found the string at `0x140023023`, its xref, and the containing `FUN_140001060` (= `main`) |
| | `analyze_function FUN_140001060` | 0.085 / 0.076 s | **99 KB** | identical |
| | `procedure_pseudo_code` | 0.013 / 0.011 s | 5.3 KB | identical. It recovered the inlined FNV-1a loop (`0x811c9dc5`, `0x1000193`) and the classify switch |
| | `export_evidence_bundle` | 0.014 / 0.009 s | 111,004 B file | **byte-identical** (sha256 `844a6128…`) |
| ELF `fixture.elf` | every provider call | ~0.003 s | 1.3 KB error | identical refusal: `target_format_unsupported` / `Windows Ghidra P0 accepts PE targets only` |
| .NET `Fixture.dll` (4 KB, csc 4.8) | `inspect_managed_artifact` / `_members` / `_native_boundaries` | 0.019 / 0.015 / 0.007 s | 5.0 / 18.9 / 4.0 KB | identical. The export (28,281 B) is byte-identical. It found both types, all 7 methods, and the `kernel32.dll` P/Invoke ModuleRef |
| JS `dist/` (30 KB bundle + 109 KB map, esbuild 0.25.10) | `analyze_javascript_application detail=summary` | **2.03 / 2.05 s** | 10 KB | identical. The export (**10,492,119 B**, ~350× the input) is byte-identical |
| | same with `detail=complete` | 2.1 s | **>10.5 MB → refused** | `resource_constraint`: it exceeds the 10,484,736 B MCP stdio response budget |
| HAR (93 entries, 5.9 MB, all `127.0.0.1:5050`) | `inspect_web_network_capture` (all records) | 0.61 / 0.64 s | **over budget → refused** | identical. The export (5,962,221 B) is byte-identical |
| WASM `fixture.wasm` (1.7 KB) | `open_binary` | 0.006 s | error | identical refusal: `unsupported binary format` |

Determinism, overall: **every analysis payload was byte-identical across the two cold runs.** Only three
fields ever differed: `binary_session.analysis_run.run_id`, `server_identity.server.started_at`, and the export
`path` (which contains the run number). `evidence_id`s are content-addressed and stable across runs.

The first pass on the PE gave 36.7 s / 26.3 s for `binary_overview`, but its run 1 overlapped an ICDEV
baseline run on the same host, so it is discarded. The table uses the second pass, which ran with no other Ghidra.

## Measurements: Linux (Debian 13 container, Node 24.11.0, Ghidra 12.1.4, Temurin 21.0.12.1, 2 CPUs / 3 GB)

Same plan, same fixtures (the identical PE and ELF bytes), two cold runs each.

| Target | Key call | Cold latency run1 / run2 | Output size | Determinism |
|---|---|---|---|---|
| PE | `binary_overview` (Ghidra start + analysis) | **29.56 / 28.98 s** | 3.0 KB | identical. The export (109,150 B) is byte-identical |
| | `trace_feature` / `analyze_function` / `procedure_pseudo_code` | 0.014 / 0.19 / 0.015 s | 3.0 / 98 / 4.5 KB | identical. The pseudocode equals the Windows text apart from CRLF |
| ELF | `binary_overview` | **16.14 / 16.02 s** | 4.1 KB | identical. The export (144,799 B) is byte-identical |
| | `analyze_function icdev_checksum` (by name) | 0.10 / 0.02 s | 39 KB | identical |
| .NET | three `inspect_managed_*` calls | 0.02–0.06 s | 4.8 / 18.7 / 3.8 KB | identical. The export (27,552 B) is byte-identical |
| JS `dist/` | `analyze_javascript_application detail=summary` | 2.12 / 1.97 s | 9.8 KB | identical. The export (10,491,611 B) is byte-identical |
| HAR | `inspect_web_network_capture` → over budget, then export | 0.73 / 0.74 s | export 5,962,013 B | identical |
| WASM | `open_binary` | 0.02 s | error | `unsupported binary format` |

Server spawn takes 1.05–1.42 s. The differing fields are the same three as on Windows (`run_id`, `started_at`, export `path`).

### Tool availability (`binary_session.tool_availability`, Windows)

| Session state | Available / 170 | Unavailable reasons |
|---|---|---|
| PE open, Ghidra bound | **101** | 56 `provider_missing` (Hopper/IDA/JEB/adb/…), 13 `unsupported_host` (macOS-only) |
| ELF open (refused provider) | 64 | 96 `provider_missing`, 10 `unsupported_host` |
| No native target (.NET/JS/HAR) | 59 | 59 `target_required`, 42 `provider_missing`, 10 `unsupported_host` |

`binary_session.capabilities`: 33 of 46 available. Each tool declares `effects`: **45 tools declare
`accessesNetwork`** (all adb, browser, Electron, JEB, web-page and runtime-observe tools), 56 declare
`launchesProcess`, and 32 declare `writesFilesystem`. None of the tools used in this spike declares network access.
ICDEV can build its allowlist from this metadata. It does not need a hand-kept list.

## Egress

| Host | Method | Result |
|---|---|---|
| Windows | (1) `NODE_OPTIONS=--require egress_hook.cjs` logs every `net.Socket.connect`, `dns.lookup` and `dgram.send` in every Node process of the REA tree. (2) A psutil sampler (200 ms) records every inet socket of the whole process tree (node, cmd, java, decompile.exe) | **No DNS lookups and no UDP. The only outbound connect was `127.0.0.1:<ephemeral>`**: Node → Ghidra JVM, which listens on `127.0.0.1` only. That is REA's documented "authenticated IPv4 loopback" bridge, used because Java cannot accept Node's AF_UNIX on Windows. .NET, JS, HAR and WASM opened **no socket at all** |
| Linux (container) | `strace --seccomp-bpf -f -e trace=connect,sendto,sendmsg` over the whole tree, plus the same Node hook. The tracer was verified live: it logged 2–22 AF_UNIX connects per run | **0 AF_INET/AF_INET6 syscalls** across every target and both runs. The only Node connect was the Ghidra bridge, `/tmp/rea-ghidra-*/bridge.sock` (AF_UNIX) |

Expected none, measured none, apart from the loopback engine bridge. The sampler cannot catch a socket that
opens and closes inside 200 ms. On Windows the Node hook is complete for Node, but the JVM is covered only
by the sampler. The Linux strace is complete for the targets it measured.

## Evidence / unknowns shape

Every successful result carries the same top-level record: `evidence_id` (`ev_<sha256>`), `subject`
(name, **sha256**, format, arch, local_path), `provider` (id/name/version, e.g. `ghidra 12.1.4`,
`rea-dotnet-static 1`, `har-schema/lossless-json`), `analysis_profile`, `operation`, `parameters`,
`raw_result` (null in every call measured), `normalized_result`, `confidence`, `authority`, `environment`,
`limitations[]`, `locations`, and `evidence_links`.

- `confidence: "observed"` and `authority: "shipped-artifact"` on Ghidra pseudocode and .NET metadata.
  The pseudocode's `limitations` state outright that it "is Ghidra decompiler output, not original source".
  This maps directly onto the house rail `basis: decompiled|observed`. ICDEV should store `decompiled`
  for pseudocode. It should not copy REA's `observed` through unchanged, because the rail separates the two.
- `trace_feature` returns `truncated: false` and `residual_unknowns: []`. `list_unknowns` returns `[]`
  for a fresh session. A shape note for the wiring card: **REA uses `[]` to mean "measured, none"**.
  The house rule wants unmeasured to be `None`, so the adapter must map "call not made / errored" to
  `None` and must never default to `[]`.
- Errors are structured: `{error: {code, category, retryable, remediation, details}}`, with the provider
  rejection list and reasons. This is enough to report an unavailable result honestly.
- **HAR credential handling:** REA nulls `Set-Cookie` header values (95 of them) as promised. A CSRF
  token embedded in the HTML **response body** stays verbatim in the evidence. The wiring card must pass
  `sensitive_values` (or run ICDEV redaction) before persisting HAR evidence.
- **Size:** use `detail: summary` plus `export_evidence_bundle` to a path. A complete inline JS result
  overflowed MCP for a 30 KB bundle. The exported bundle is the durable artifact, with canonical, byte-stable ordering.

## ICDEV baseline, head-to-head on the C binary

Same fixtures, two runs each. ICDEV `binary_triage.py` and `ghidra_headless.py` were run as CLIs
(`--json`) from this worktree.

| | `binary_triage.py` | `ghidra_headless.py` | REA (Ghidra provider) |
|---|---|---|---|
| Windows PE | **0.11 s**, 6.8 KB. Format, arch, sections, 115 strings (both fixture literals), imports. Deterministic except `generated_at` | **FAILED, no result** (17–22 s to fail) | 24 s cold, then sub-0.1 s queries. Pseudocode, callers/callees, xrefs, basic blocks, string→function trace |
| Windows ELF | 0.19–0.38 s, 16.8 KB | **FAILED** (11–12 s) | refused (Windows P0 is PE-only) |
| Linux PE (Debian container) | 0.09–0.10 s, 4.6 KB | **FAILED** (exit 1, 14.4–17.0 s) | 29.0–29.6 s cold. Same pseudocode as Windows; 109 KB export, byte-identical across runs |
| Linux ELF (Debian container) | 0.13–0.18 s, 16.1 KB | **FAILED** (exit 1, 10.0–10.3 s) | 16.0–16.1 s cold. Named functions, decompiled `icdev_checksum`; 145 KB export, byte-identical |
| Function-level output | none, by design (triage) | none | yes |

`binary_triage` stays useful: it answers format/strings/imports in ~0.1 s with no JVM, and REA does
not replace that first look. What REA adds is everything at function level, which ICDEV can produce today on
**no** OS with Ghidra 12.

**Why `ghidra_headless.py` fails: two pre-existing ICDEV defects, both found by this spike:**

1. **Jython post-script on Ghidra 12.** `tools/analyzers/ghidra_scripts/icdev_export.py` runs as a
   Jython `-postScript`. Ghidra 12 ships PyGhidra and only a Jython **stub**, so the run aborts with
   `JythonStubException: … you must install the Jython Ghidra Extension, or (recommended) port your
   script to PyGhidra or Java` (from Ghidra's `application.log`). Confirmed on **both** Windows and Linux. On Ghidra ≥ 12.0, the version the
   `rea` card names as the default, `ghidra_headless.py` can never return a result. Before this spike it
   had only ever reported `unavailable` on this host, because no Ghidra was installed.
2. **The cause is invisible in the report.** The abort goes to stdout/`application.log`, not stderr,
   so the report says `nonzero_exit` with `stderr_tail: null`. An operator cannot tell a Jython abort
   from a JDK problem.
3. (Environment, not ICDEV) On Windows, `analyzeHeadless.bat` refused **both** Temurin 21.0.12.1 and
   21.0.12+8 whenever `JAVA_HOME` was set ("JAVA_HOME environment specifies unsupported java version"),
   even as `JAVA_HOME_OVERRIDE`. Ghidra's `LaunchSupport` then hit an interactive prompt and threw an NPE on EOF.
   It worked only with `JAVA_HOME` unset, `java` on PATH and a `java_home.save` in
   `%APPDATA%\ghidra\ghidra_12.1.4_PUBLIC\`. REA validates the JDK itself and launches the JVM directly,
   so it never hit this.

Not fixed here (the card is scratch-only). These belong to the engine card (`rea-eng-*`), which owns the
Node-free `ghidra_headless` path: port `icdev_export.py` to PyGhidra or Java, and capture stdout plus the
Ghidra log tail into the report.

## Linux

**The Omarchy laptop (`ssh omarchy`, 192.168.1.179) was unreachable for the whole session**: no ping
reply and TCP/22 timed out on five attempts. So **no Omarchy/Arch row is measured**, and none is claimed.

As a substitute I measured a Linux container on this host (Debian 13 `python:3.12-slim`, the same pinned
Node, the same Ghidra zip, Temurin 21.0.12.1, plus strace, psutil and procps; capped at `--memory 3g --cpus 2`):

- `rea doctor` → `host: unsupported_host (debian 13)`. REA 6.4.0 supports "macOS 12+, Ubuntu 24.04+, Fedora 41+,
  Nobara 44+, 64-bit Arch Linux, CachyOS, Omarchy, and the experimental Windows x64 Ghidra P0
  boundary". Debian is not on that list, but every GO row above ran there.
- **Undeclared host prerequisite: `ps` (procps).** In the first pass, with no `ps` on PATH,
  `analyze_javascript_application` and `inspect_web_network_capture` failed closed with `Linux process
  ownership inspection requires a procps-compatible ps`, and Ghidra failed with `GhidraLaunchError: Ghidra headless
  launch failed`. **`rea doctor` flags neither.** After adding `procps` (and changing nothing else) all of them ran.
  The engine card must check for `ps` itself before it reports REA available.
- The Linux Ghidra cold start is slower than Windows on PE (29 s vs 24 s), but the container had 2 CPUs and
  the host had all of its cores. Treat that as an ordering, not a benchmark.
- An Arch container (pacman `jdk21-openjdk 21.0.12.1.u1-1`) was attempted as the closest stand-in for
  Omarchy. **The image build crashed this host's Docker engine**, which restarted `icdev-postgres` (about 6
  minutes of crash recovery, then healthy again) and the CI runner containers. `icdev-rt-runner` came back
  crash-looping on a GitHub `runner-registration` 404, an expired registration token that the restart exposed.
  The Arch build was abandoned rather than retried.

Omarchy/Arch measurement is **still owed** on the laptop the card names.

## Rails check (for the cards that follow)

| Rail | What the spike found |
|---|---|
| Authorized targets, sha256 in provenance | REA records `subject.digest.sha256` on every Evidence record, so ICDEV can join on it. The authorization basis is ICDEV's to record, because REA has no such field |
| Analysis stays local | Measured: no egress except loopback to its own engine |
| "Not a sandbox" | Confirmed by its own limits. Windows P0 declares "managed, hostile, sensitive, and mutable-path targets are unsupported", and analysis runs with the user's rights. ICDEV containment is still required |
| Capture needs `allow_execute` | 56 tools declare `launchesProcess` and 45 declare `accessesNetwork`. Gate on these `effects` flags |
| CI without Node/Ghidra | The stub stdio server can replay the recorded responses. They are deterministic, so a replay is a faithful fixture |
| Decompiled ≠ source | REA says so in `limitations`. ICDEV must label the result `basis: decompiled` |
| Unmeasured list = `None` | REA uses `[]` for "measured, none", so the adapter has to map not-run/error to `None` |

## Fixtures (all ICDEV-owned, built in the spike)

| Fixture | Built with | sha256 |
|---|---|---|
| `fixture.c` → `fixture.exe` (PE x86-64) | zig 0.15.2 `cc -O1 -target x86_64-windows-gnu` | `5dc1a51a…fc47` |
| `fixture.c` → `fixture.elf` (static musl) | zig 0.15.2 `cc -O1 -target x86_64-linux-musl` | `38fd386b…4f50` |
| `Fixture.cs` → `Fixture.dll` | .NET Framework 4.8 `csc.exe -target:library` (no dotnet SDK on the host) | `2a65db84…5478` |
| `wasm.c` → `fixture.wasm` | zig 0.15.2 `cc --target=wasm32-freestanding` | `93350a48…a7b4` |
| `dist/app.js` + `app.js.map` | esbuild 0.25.10 over 6 ICDEV dashboard scripts (`tools/dashboard/static/js/{api,esc,debounce_filter,charts,live,kanban}.js`) | `13cc77ba…cb01` / `f52c4ac3…7768` |
| `dashboard.har` | Playwright 1.58 against `http://127.0.0.1:5050` (`/`, `/kanban`), read-only GETs | `90f8573d…4e343` |

**Substitution:** the card asked for "the ICDEV frontend's built bundle + source maps", but none exists.
`frontend/` holds only generated API types (`lib/api-types.ts`), and its `build:airgap` script needs a
Next.js app that is not in the repo. So the JS fixture is an esbuild bundle of ICDEV-owned dashboard
JavaScript, with a source map.
