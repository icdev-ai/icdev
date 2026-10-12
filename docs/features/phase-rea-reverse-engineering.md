# CUI // SP-CTI

# Phase 81 — REA: understanding an application WITHOUT its source

**Card:** `rea` (`args/projects.yaml`, prefix `rea-`, 8 epics, 10 tasks seeded 2026-10-11)
**ADR:** D409 (`docs/reference/adrs.md`, Phase 81)
**Upstream:** [morluto/rea](https://github.com/morluto/rea) — MIT, TypeScript, npm `rea-agents`, pinned **6.4.0**
**Status, read off `origin/main` and the board 2026-10-11:** 9 of 10 tasks landed —
`rea-spike-01`, `rea-eng-01`, `rea-eng-02`, `rea-wire-01`, `rea-wire-02`,
`rea-aiify-01`, `rea-sec-01`, `rea-pif-01` merged, `rea-docs-01` is this document.
**`rea-xlate-01` is NOT on main**: PR #2449 is open and `CONFLICTING`, and the board
reads `pr_opened`. Nothing below claims its module (`tools/translation/decompiled_extractor.py`)
as shipped, and `docs/reference/commands.md` carries no row for it — a documented command
whose file is absent is the defect `check_doc_command_paths` exists to stop.

The question was "should ICDEV adopt REA". The answer that made the card worth
building was a **measured gap**, not a feature list: **every ICDEV understanding
path needed SOURCE.**

| Path | What it accepts |
|---|---|
| `tools/aiify/engine.py::run_scan` | a source tree (local path, UNC share, git URL) — nothing else |
| `tools/translation/source_extractor.py` | parsers for 7 source languages |
| `tools/modernization/legacy_analyzer.py` | analyzers for python / java / csharp only |
| `tools/analyzers/binary_triage.py` | says in writing that modernization and `extract_source_ir` require source |

Government legacy estates are often binary-only: a COTS installer, an Electron
client, a .NET assembly with no repository behind it, a website nobody has the
code for. Before this phase ICDEV could say what format such an artifact was
(`binary_triage`, xrv-bin-01) and nothing about what it **does**.

REA is ONE local stdio MCP server + CLI for evidence-backed reverse engineering
of native binaries (Ghidra / Hopper / IDA providers), JS/Electron (modules,
source maps, routes, IPC, `trace_application_feature`), .NET CIL (static, no
engine), APK (JADX), WASM, firmware, websites/HAR, and browser and process
scenarios. Every result carries `evidence_id` (`ev_<sha256>`), `normalized_result`,
`raw_result`, `limitations` / `residual_unknowns` / `truncated`, and can be
bundled with `get_evidence_bundle` / `export_evidence_bundle`. Its own
`SECURITY.md` says: **"This is not a sandbox."** That one sentence decided the
containment design below.

It plugs into **two existing seams and no new subsystem**: `tools/mcp_client`
via `args/external_mcp_servers.yaml`, and `args/analyzer_contract.yaml` (zero
dispatcher change for the families, one `status_key` lift for honest statuses).

---

## Verdict per target type (rea-spike-01, measured 2026-10-11)

Full record: `docs/cards/rea-spike-01.md`. Two cold runs per target, each against
a fresh MCP server process, on Windows 11 (Node 24.11.0, Ghidra 12.1.4, Temurin
21.0.12.1) and a Debian 13 container (same pins, 2 CPUs / 3 GB). **No Omarchy/Arch
row was measured** — the laptop was unreachable — and none is claimed.

| Target type | Windows 11 | Linux (Debian 13 container) | Decision |
|---|---|---|---|
| Native PE x86-64, Ghidra | **GO** — `binary_overview` 24.30 / 24.07 s cold, then sub-0.1 s queries | **GO** — 29.56 / 28.98 s cold; pseudocode identical to Windows apart from CRLF | Keep, both OSes |
| Native ELF, Ghidra | **NO-GO** — refused: `Windows Ghidra P0 accepts PE targets only` | **GO** — 16.14 / 16.02 s cold; `icdev_checksum` decompiled near-verbatim | Keep, **Linux only** |
| .NET CIL (static) | **GO** — < 0.02 s per call | **GO** — < 0.06 s per call | Keep, both OSes, no engine |
| JS bundle + source map | **GO with conditions** — 2.03 / 2.05 s | **GO with conditions** — 2.12 / 1.97 s | Always `detail: summary` + `export_evidence_bundle`: `detail=complete` on a 30 KB bundle exceeded REA's 10,484,736 B MCP response budget |
| HAR capture | **GO with conditions** — 0.61 / 0.64 s | **GO with conditions** — 0.73 / 0.74 s | Export only, and ICDEV passes `sensitive_values`: REA nulled all 95 `Set-Cookie` values, but a CSRF token in a response BODY survived verbatim |
| WASM | **NO-GO** — `unsupported binary format` | **NO-GO** — same | **Dropped.** REA 6.4.0 has no WebAssembly analysis ("wasm" appears only in its EVM internals) |
| JS source recovery (`recover_javascript_sources`) | NO-GO — needs a caller-supplied Wakaru Linux x64 binary | not measured | Out of scope |

**Overall: GO, scoped to the GO rows.** The deciding evidence was the head-to-head:

| | `binary_triage.py` | `ghidra_headless.py` (before rea-eng-02) | REA, Ghidra provider |
|---|---|---|---|
| Windows PE | **0.11 s**, format/arch/sections/115 strings/imports | **FAILED, no result** (17–22 s to fail) | 24 s cold; pseudocode, callers/callees, xrefs, string→function trace |
| Linux ELF | 0.13–0.18 s | **FAILED** (exit 1, 10.0–10.3 s) | 16 s cold; named functions, decompiled |
| Function-level output | none, by design | none | yes |

On Ghidra 12.1.4, the version the card names as default, ICDEV's own
`ghidra_headless.py` produced **no result on either OS**: its post-script ran as
Jython and Ghidra 12 ships only a Jython STUB (`JythonStubException`), and the
abort went to stdout / `application.log`, so the report said `nonzero_exit` with
`stderr_tail: null`. Before the spike it had only ever reported `unavailable` on
this host, because no Ghidra was installed — an optional backend that had never
once been exercised. `rea-eng-02` fixed both (below).

**Determinism and egress.** Every analysis payload was byte-identical across the
two cold runs and across the two OSes; only `run_id`, `started_at` and the export
`path` differed, and `evidence_id`s are content-addressed. Egress, measured with a
Node `net`/`dns`/`dgram` hook plus a 200 ms psutil sampler (Windows) and
`strace --seccomp-bpf -f` (Linux): **0 DNS lookups, 0 UDP, 0 AF_INET/AF_INET6
connects** apart from REA's own engine bridge (`127.0.0.1:<ephemeral>` on
Windows, an AF_UNIX `bridge.sock` on Linux). .NET, JS, HAR and WASM opened no
socket at all.

**MCP surface.** 170 tools; server spawn 1.05–1.42 s. Each tool declares
`effects`: 45 declare `accessesNetwork`, 56 `launchesProcess`, 32
`writesFilesystem`. ICDEV's allowlist is DERIVED from those flags, not hand-kept.

**Undeclared host prerequisite.** On Linux with no `ps` on PATH, the JS, HAR and
Ghidra paths all failed closed (`Linux process ownership inspection requires a
procps-compatible ps`), and `rea doctor` flagged none of it.

## Engines and their licenses (verified 2026-10-11)

Declared in `args/re_engines.yaml`, decided by `tools/analyzers/re_engines.py`
(rea-eng-01). The license CLASS decides what ICDEV may do; the user maintains
their own commercial license and declares it in a user-owned file
(`~/.config/icdev/re_licenses.yaml` or `$ICDEV_RE_LICENSES`) — ICDEV stores no
keys and never downloads or installs a commercial engine.

| Engine | License | `license_class` | ICDEV posture |
|---|---|---|---|
| **Ghidra** (NSA) | Apache-2.0, latest 12.1.4; PyGhidra is the default since 12.0 | `oss_redistributable` | **Default everywhere**, no declaration needed. Installable by ICDEV (`docs/install/ghidra.md`; `icdev omarchy setup --ghidra`). `ghidra_headless` stays the air-gapped, **Node-free** path |
| **Hopper** | Commercial; Personal (named user) or Computer license; no redistribution | `byo_single_user` | Bring-your-own only. Never installed or downloaded by ICDEV. Off on Windows (platforms: macos, linux). Refused on a shared/SaaS deployment |
| **IDA** (Hex-Rays) | Commercial. **IDA Free is non-commercial only**; `idalib` ships in Home / Pro / Classroom; embedding idalib, a SaaS engine or a shared server needs an **OEM** license | `oem_required_for_shared` | Bring-your-own via REA's IDA provider, single-user, local only. **IDA Free + `use: commercial` is refused**; a non-OEM IDA under `ICDEV_SAAS_MODE` / `ICDEV_MULTI_TENANT` is refused |

The operator owns **neither** Hopper nor IDA: those paths are covered by fixtures
only, and reverse engineering runs **only on a single operator's own machine,
never on the SaaS/shared deployment** (`single_user_only: true` on the server
entry). Apache-2.0 on ICDEV grants nothing about IDA. The gate checks a
declaration's CONSISTENCY with the vendor's published terms; it cannot verify that
the user holds the license, and says so in every `allowed` message.

## Deliberately NOT adopted, and why

| Not adopted | Reason |
|---|---|
| **`rea setup`** | It rewrites harness configs to register itself (rea-spike-01 did not run it for that reason). ICDEV owns those files through `tools/dx/companion.py`, which already churns them; a second writer would fight it. REA is registered through `args/external_mcp_servers.yaml` and launched by `tools/mcp_client`, never by REA's own installer. The spike never ran it. |
| **Hopper or IDA as a default** | Commercial engines with no redistribution, and IDA Free is non-commercial only. A default would make every user's first run a license question ICDEV cannot answer for them. Ghidra (Apache-2.0) is the default everywhere; the commercial engines are bring-your-own behind the `re_engines` gate, and REA's `auto` provider choice is never used — the native provider comes from the gate (`--engine`, default `ghidra`). |
| **adb device actions** | Every adb tool declares `accessesNetwork` (rea-spike-01) and drives a physical device. That is acting on hardware, not reading an artifact, and none of it passes the read-only allowlist. APK analysis stays static (JADX, `unavailable` until measured). |
| **EVM / smart-contract analysis** | No ICDEV consumer: no estate, no canvas, no compliance path reads EVM bytecode. Allowlisting it would add a declared-but-unconsumed capability — this repository's signature defect — to win nothing. |
| **Non-opt-in capture** | Process and browser capture LAUNCH the target (`launchesProcess`; the browser one also `mutatesTarget` + `accessesNetwork`). `capture_process_scenario` is in `execute_tools:`, never on the default registry, and is exposed only to a registry built with `allow_execute=True` **for that one run** — `True`, not truthy. Browser capture is not wired at all: its target is a URL that `target_auth` cannot hash and grant (`browser_target_unauthorizable`). |
| **WASM** | Measured NO-GO on both OSes (above). Reported `unavailable` with the measured reason rather than dropped silently. |
| **ELF on Windows** | REA's Windows Ghidra P0 boundary is PE-only. ELF is a Linux-only GO row. |
| **`detail: complete` inline results** | Overflows the MCP response budget on a 30 KB bundle. `summary` + exported bundle is the durable artifact. |
| **Node or Ghidra in CI** | CI replays recorded REA 6.4.0 responses through a stub stdio MCP server (`tests/fixtures/rea/stub_rea_server.py`); the responses are deterministic, so the replay is a faithful fixture. |

---

## What shipped, by epic

### `spike` — does REA earn its place (rea-spike-01)

Scratch code only, nothing under `tools/` changed. Pinned `rea-agents@6.4.0`
(npm integrity `sha512-Vj1zWfSl…TEQ==`), measured GO/NO-GO per target type,
latency, determinism and egress, and the head-to-head that found the two
`ghidra_headless` defects. Record: `docs/cards/rea-spike-01.md`.

### `eng` — engines and their licenses (rea-eng-01, rea-eng-02)

| card | what shipped |
|---|---|
| `rea-eng-01` | `args/re_engines.yaml` + `tools/analyzers/re_engines.py`: the engine registry and license gate (`oss_redistributable` / `byo_single_user` / `oem_required_for_shared`), engine detection, a user-owned license declaration template. Sandbox-coverage **Gap 72**. |
| `rea-eng-02` | `ghidra_headless` on Ghidra 12 via PyGhidra (`<python> -m pyghidra.ghidra_launch … AnalyzeHeadless`); an older Ghidra is `unavailable` / `ghidra_version_unsupported`. The post-script was ported off Jython (strings via `DefinedStringIterator`); stdout and the Ghidra log tail now reach the report. A run needs an authorization basis or it is refused before the JVM starts. Installable on Linux and Windows (`docs/install/ghidra.md`, `icdev omarchy setup --ghidra`). |

### `wire` — two existing seams, no new subsystem (rea-wire-01, rea-wire-02)

| card | what shipped |
|---|---|
| `rea-wire-01` | The `rea` entry in `args/external_mcp_servers.yaml`: **`enabled: false`**, exact pin checked against the installed `package.json`, launched as `node env:ICDEV_REA_ENTRY mcp`, `timeout_seconds: 120`, `classification_ceiling: CUI`, `single_user_only: true`. A **47-tool read-only allowlist** derived from REA's own `toolEffects` (none declares `mutatesTarget`, `accessesNetwork` or `changesUiState`), 2 `write_exceptions` (`export_evidence_bundle`, `inspect_web_network_capture`), 1 `execute_tools` entry. `tools/mcp_client/target_auth.py`: every argument naming an artifact is hashed at call time and must match a grant (`grant_target("rea", path, basis=...)`, process-local, keyed by sha256). Sandbox-coverage **Gap 73**. |
| `rea-wire-02` | `tools/analyzers/rea_adapter.py` on the analyzer contract: observable types `dotnet_assembly`, `js_bundle`, `har` (plus `binary`); `binary` / `dotnet_assembly` / `js_bundle` / `har` run, `wasm` / `apk` / `website` report `unavailable` with the measured reason. Statuses `ok \| partial \| unavailable \| error`, lifted by the dispatcher (`status_key`) so a missing engine is never `ok`. Runs under `SandboxExecutor`. |

### `xlate` — translate what has no source (rea-xlate-01) — **NOT on main**

Compiled artifact → `source_extractor` IR with `basis: decompiled` →
`translate_unit` / `legacy_analyzer` / `architecture_extractor`. PR #2449 is open
and `CONFLICTING` on 2026-10-11. Documented here when it lands, not before.

### `aiify` — AI-ify from observed behaviour (rea-aiify-01)

`tools/aiify/observed_inventory.py` + `engine._resolve_input` / `run_scan`:
`input_type` `har | electron_app | website | apk` is a no-source input mode.
Observed features → `opportunity_scorer`, every opportunity `basis: observed`.
Agent readiness is reported `skipped` with the reason ("reads source, and there
is none"), never scored. Sandbox-coverage **Gap 74**.

### `pif` — Process-Ify from observed behaviour (rea-pif-01)

`tools/workflow_canvas/observed_processify.py` + two workflow-canvas routes. One
operator-run `capture_process_scenario` (opt-in per run) becomes a DRAFT workflow
in `studio_workflows` — every step `basis: observed` with its `evidence_id`,
`status: draft`, `review.state: pending_hitl`. It cannot be linked into a process
chain until a human approves it (`chain_eligibility`, 409 on the chain route).
A capture with no recognisable step sequence gives `steps: None` and no draft.
Sandbox-coverage **Gap 75**.

### `sec` — supply chain and attack surface from artifacts (rea-sec-01)

`tools/analyzers/rea_feeds.py`, the one reader of REA evidence shapes, feeding a
saved `rea_adapter --json` report into: SBOM hints (`sbom_generator --rea-report`;
.NET assembly references, JS packages), attack surface (`attack_surface_mapper
--artifact-report`; IPC channels, routes, HAR endpoints, APK permissions), SCRM
(`scrm_assessor --rea-report`) and ATO evidence (`evidence_collector
--rea-report`, provenance carrying the bundle digest). Sandbox-coverage **Gap 76**.

### `docs` — this document (rea-docs-01)

This feature doc, ADR D409, and the `commands.md` / manifest rows.

## The rails every card in this phase obeys

| Rail | How it is enforced |
|---|---|
| **Authorized targets only** | Target sha256 + operator authorization basis (`owned \| licensed \| customer-authorized`, flag or `ICDEV_RE_AUTHORIZATION_BASIS`) in provenance; a run with none is refused before REA or the JVM is reached. REA records `subject.digest.sha256`; the basis is ICDEV's, REA has no such field. |
| **Analysis stays local** | stdio transport only; measured egress is the loopback/AF_UNIX engine bridge and nothing else. Nothing is uploaded. |
| **"This is not a sandbox"** | REA and the engine run inside ICDEV containment: `tools/analyzers/sandbox.py` `SandboxExecutor`, plus the omx-guard-05 L3 write sandbox where the host has one; `containment` says when it does not. Every module has its row in `docs/security/sandbox-coverage.md` (Gaps 72–76). |
| **Capture is opt-in per run** | `allow_execute is True` for that call, or refused before a registry exists. |
| **CI never needs Node or Ghidra** | Stub stdio MCP server replaying recorded REA 6.4.0 responses (`tests/fixtures/rea/`). |
| **Decompiled is not source** | Pseudocode carries `basis: decompiled` and low confidence; observed behaviour carries `basis: observed`. REA's own `confidence: observed` on pseudocode is NOT copied through — the rail separates the two. |
| **Unmeasured is `None`, never `[]`** | REA uses `[]` for "measured, none". The adapter maps "call not made / errored" to `None` with a `*_basis` naming why. |
| **Single operator, never SaaS/shared** | `single_user_only: true`; Hopper and non-OEM IDA are refused under `ICDEV_SAAS_MODE` / `ICDEV_MULTI_TENANT`. |

## Not built, and named

- **An Omarchy/Arch measurement.** The card's named Linux host was unreachable for
  the spike; every Linux number is from Debian, which `rea doctor` calls
  `unsupported_host`.
- **`rea-xlate-01`** — open as PR #2449, see above.
- **APK, website and WASM families** — `unavailable` with a measured or stated
  reason; no JADX or browser path is wired.
- **Browser scenario capture** — `browser_target_unauthorizable` until a URL can be
  granted the way a file is.

## Re-derive everything

```bash
python -m tools.analyzers.re_engines --json                     # engine allow/refuse + reason
python -m tools.analyzers.rea_adapter --families                # which families run
python -m tools.analyzers.ghidra_headless <path> --authorization-basis owned --json
python -m pytest tests/analyzers/test_re_engines.py tests/analyzers/test_ghidra_headless.py \
  tests/test_rea_external_mcp.py tests/analyzers/test_rea_adapter.py \
  tests/test_aiify_observed_input.py tests/analyzers/test_rea_feeds.py \
  tests/test_observed_processify.py -q                          # 232 passed 2026-10-11, no Node/Ghidra
```

# CUI // SP-CTI
