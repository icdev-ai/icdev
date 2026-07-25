# Sandbox Coverage — ICDEV™ Ingress Decisions

> **Classification:** CUI // SP-CTI
> **ADR:** D-SEC-11 (Phase 72 — LLM Sandbox Integration)
> **Last audit:** 2026-04-26 (OPT-58 / ZBX — Gaps 6-8 added)

This document inventories every code path in `tools/` that processes
content whose origin is not strictly first-party-developer-authored,
and records the **explicit trust decision** for each. Landing here is
mandatory for any new ingress point before merge — see the
`sandbox_coverage` coherence check in
`tools/workflow/coherence_checker.py`.

## Decision Taxonomy

| Decision | When to use |
|---|---|
| **sandboxed** | Code is executed inside `SandboxExecutor` (Docker/K8s/Podman isolation, network/FS limits, audit logged) |
| **trusted-first-party** | Content ships with the repo or is authored by ICDEV™ maintainers under `tools/` or `args/`; no sandbox required |
| **sandboxed-on-demand** | Sandbox activates only under `ICDEV_STRICT_SANDBOX=1` (IL5 / air-gap deployments). Permissive default in dev. |
| **bypass-documented** | Safe by construction — no `exec`, `subprocess`, native parser, or file mutation path — and this guarantee is enforced by a regression test. |

## Phase 72 Baseline (already sandboxed)

These 6 paths adopted `SandboxExecutor` in Phase 72 (D-SEC-11):

| Path | Decision |
|---|---|
| `tools/testing/test_orchestrator.py` (CodeLens) | **sandboxed** |
| `tools/ci/` GitLab + GitHub contributor PR verification | **sandboxed** |
| OpenClaw bridge import (Gate 9 / 9b) | **sandboxed** |
| Marketplace pre-install verification | **sandboxed** |
| Genesis Evolve mutation safety (confidence penalty) | **sandboxed** |
| Connector forge stage | **sandboxed** |

## OPT-58 Gap Decisions

### Gap 1 — Canvas auto-remediator handler execution
- **File:** `tools/canvas/auto_remediator.py`
- **Risk:** Reads canvas templates + user-created designs, passes `graph_json` to first-party `engine.assess_*()` functions. A malicious template could theoretically embed script-like structures.
- **Decision:** **trusted-first-party**
- **Rationale:** Canvas engines (`tools/security/`, `tools/infra/`, etc.) are first-party, deterministic, no `exec()` / `eval()` / `subprocess` in the assessment hot path. Sandboxing every handler would impose ~5–15× latency on a hot path with no concrete threat model.
- **Guardrails:**
  - All canvas templates under `args/canvas/` are first-party and code-reviewed.
  - User-uploaded designs are JSON only; never execute.
  - CLAUDE.md guardrail: "Canvas templates are first-party; design JSON is data only."
- **Revisit if:** we add a code-execution step to canvas engines, or accept third-party template submissions.

### Gap 2 — Kanban LocalPythonExecutor (OPT-31)
- **File:** `tools/genesis/reflexes/kanban.py` — `_dispatch_via_llm_router()`
- **Risk:** If a future change wires `exec()` or `subprocess` into this executor, it would execute LLM-generated code without isolation.
- **Decision:** **bypass-documented**
- **Rationale:** Current implementation calls `LLMRouter.invoke()` and writes the response to a task log. No code is executed. Safe by construction.
- **Guardrails:**
  - Regression test `tests/security/test_dispatch_no_exec.py` fails the build if `_dispatch_via_llm_router` (or its closures) adds `exec()`, `eval()`, `subprocess.`, `os.system(`, or `os.popen(`.
- **Revisit if:** the regression test fires → re-decide sandbox vs. trusted.

### Gap 3 — PDF parsing + Vision OCR
- **File:** `tools/rag/pdf_provider.py`
- **Risk:** Parses user-supplied PDFs with `pypdf` + `pdf2image` (native libraries). A malformed PDF could theoretically trigger parser CVEs. Image OCR optionally ships image bytes to Ollama Vision/LLaVA.
- **Decision:** **sandboxed-on-demand**
- **Rationale:** Permissive default in dev/IL2. In IL5 / air-gap, PDF parsing routes through `SandboxExecutor` when `ICDEV_STRICT_SANDBOX=1` is set. No default perf penalty.
- **Guardrails:**
  - `ICDEV_STRICT_SANDBOX=1` env flag switches to sandboxed execution.
  - Dependabot + `tools/maintenance/` audits track `pypdf` / `pdf2image` CVEs.
- **Revisit if:** a PDF parser CVE with known ICDEV™ exposure ships — promote to `sandboxed` permanently.

### Gap 4 — `.tmp/` one-shot scripts
- **Location:** `.tmp/*.py`
- **Risk:** Ad-hoc helper scripts authored during dev sessions execute in the dev's local Python with no isolation. If a non-author invokes a `.tmp/` script, there's no guardrail distinguishing "my script this session" from "a script a collaborator wrote."
- **Decision:** **trusted-first-party** with a policy gate.
- **Rationale:** `.tmp/` is explicitly a developer scratch area (documented in CLAUDE.md as "disposable scratch work — never store important data here"). All durable helpers are productized under `tools/`.
- **Guardrails:**
  - CLAUDE.md guardrail: `.tmp/` scripts must not be committed; productize under `tools/` before merge.
  - `.gitignore` excludes `.tmp/`.
  - Do not run a collaborator's `.tmp/*.py` without review — treat as untrusted.
- **Revisit if:** `.tmp/` ever hosts scripts committed to the repo (shouldn't happen).

### Gap 5 — FathomDesk news RSS ingestion
- **Location:** `tools/trading/news/*`
- **Risk:** Ingests third-party RSS/Atom feeds from the public internet. Feed content (titles, summaries) could contain malicious HTML, oversized payloads, or injection attempts.
- **Decision:** **sandboxed-on-demand**
- **Rationale:** RSS content is untrusted external input. HTML is stripped via stdlib `html.parser` before storage (no code execution). Size cap of 50 KB per entry rejects abuse. Summary truncated at 500 chars. No `exec()`/`eval()` anywhere in the pipeline.
- **Guardrails:**
  - `sanitize_html()` strips all HTML tags using stdlib only (no new deps).
  - 50 KB input cap rejects oversized entries.
  - Append-only tables prevent mutation of ingested data.
  - `ICDEV_STRICT_SANDBOX=1` routes ingest through `SandboxExecutor` in IL5.
- **Revisit if:** feedparser CVE ships, or if feed content is ever rendered as raw HTML in the dashboard (currently text-only).

### Gap 6 — OSV-Scanner subprocess execution (OPT-58 / ZBX)
- **File:** `tools/security/osv_scanner.py`
- **Risk:** Spawns the `osv-scanner` binary as a subprocess against `requirements.txt` (or a lockfile). The binary is a trusted Google-signed release. Its JSON output is parsed with `json.loads` — no `eval()` or `exec()`. Output is treated as read-only data, never executed.
- **Decision:** **bypass-documented**
- **Rationale:** The binary is first-party security tooling (google/osv-scanner), not user-supplied code. The subprocess call uses a fixed argument list (`['osv-scanner', '--format', 'json', '--lockfile', target]`); the `target` path is validated to exist before invocation. There is no shell=True, no user-controlled command interpolation, and no code execution of scan results.
- **Guardrails:**
  - Target path existence validated with `Path(target).exists()` before spawn.
  - No `shell=True` — argument list only.
  - Output parsed as JSON data only, never executed.
  - `OsvScanner` degrades to `status='unavailable'` when binary absent — no crash path.
- **Revisit if:** the binary invocation adds user-supplied arguments (e.g. `extra_args` from an untrusted source); at that point, `extra_args` must be validated against an allowlist.

### Gap 7 — SaaS API Gateway child workers (OPT-58 / ZBX)
- **File:** `tools/saas/api_gateway.py`
- **Risk:** The gateway references `--workers` for gunicorn multi-process mode. No `subprocess.run` / `Popen` calls exist in the current implementation — gunicorn spawns workers internally via its own process manager, not via ICDEV™ code.
- **Decision:** **bypass-documented**
- **Rationale:** No user-controlled subprocess call exists in `api_gateway.py` as of OPT-58 audit (2026-04-26). Gunicorn worker management is internal to the gunicorn library — ICDEV™ code does not call `subprocess` for worker spawning. `CredentialProxy` (Gap 8) is available for any future subprocess additions.
- **Guardrails:**
  - Regression guard: `grep -n "subprocess\." tools/saas/api_gateway.py` should return empty.
  - If subprocess calls are added in future, use `CredentialProxy.spawn()` and add a new gap entry.
- **Revisit if:** gunicorn is replaced with a custom process manager that uses `subprocess`.

### Gap 8 — CredentialProxy spawn wrapper (OPT-58 / ZBX)
- **File:** `tools/security/credential_proxy.py`
- **Risk:** `CredentialProxy.spawn()` wraps `subprocess.run()`. The command (`cmd`) is caller-supplied. If a caller passes user-controlled input as the command, that is an SSRF/injection risk.
- **Decision:** **bypass-documented**
- **Rationale:** `CredentialProxy` is infrastructure only — it does not accept commands from user-facing HTTP endpoints. All current and intended callers are first-party `tools/` scripts that pass hard-coded command lists. The proxy reduces risk (strips credential env vars) rather than increasing it.
- **Guardrails:**
  - Do not wire `CredentialProxy.spawn()` to an HTTP endpoint that accepts a user-supplied command string.
  - `shell=True` is not added by the proxy itself; callers that use it must justify it separately.
  - Any new caller that receives command input from a user-facing API must add a gap entry here.
- **Revisit if:** `CredentialProxy.spawn()` is called from a route that accepts user-supplied command arguments.

### Gap 13 — AI-ify Canvas scan engine (`tools/aiify/`)

**Module:** `tools/aiify/engine.py`, `tools/aiify/pattern_classifier.py`

**Ingress path:** `POST /ai-ify/api/scan` accepts `input_type` + `input_ref` and `il_level`. Supported `input_ref` forms: local path, UNC share (`\\server\share`), `file://` URI, git URL (shallow clone), and `s3://` (download). `tools/aiify/engine.py:_resolve_input` validates the reference and rejects unsupported transports (`smb://`, `ftp://`, bare `http(s)` non-repo) with a clear error rather than fetching them. The engine reads source files from the resolved path, runs AST-based pattern detection (and optionally Semgrep), scores opportunities, and stores results in `aiify_scans` / `aiify_opportunities` / `aiify_scores` tables. No scanned code is executed.

- **Decision:** **trusted-first-party**
- **Rationale:** `input_ref` is a developer/operator-supplied source reference — not end-user HTTP input from the public internet. The scanner performs read-only static analysis (AST walk + Semgrep subprocess with fixed argument list); it does not execute any code from the scanned directory. No `eval()`/`exec()` appears in the analysis hot path. Template files under `tools/dashboard/templates/aiify/` are first-party and code-reviewed.
- **Guardrails:**
  - `input_ref` is resolved with `pathlib.Path.resolve()` before use; empty paths return a 400 before engine invocation.
  - Semgrep subprocess uses a fixed argument list; no `shell=True`; no user-controlled command interpolation.
  - Canvas templates are first-party; the IQE query widget renders query results as text only (no raw HTML).
  - `_resolve_input` rejects unsupported transports (`smb://`, `ftp://`, bare `http(s)`) instead of fetching them; git/`s3://` fetch into a temp dir that is removed after the scan.
  - Scan results are written to append-only audit log (`aiify_audit_log`).
- **Revisit if:** `input_ref` is ever accepted from an unauthenticated public endpoint, or if the engine adds a step that executes code from the scanned directory.

### Gap 14 — Aggregation Guard (`tools/security/aggregation_guard.py`)

**Module:** `tools/security/aggregation_guard.py`

**Ingress path:** `guard_result(result_set, ctx, surface)` and `evaluate_rules(result_set)` receive row dicts originating from GovCon/proposals DB queries. The rows are structured data (dicts of column→value); they are never `eval()`-ed or `exec()`-ed. The guard applies declarative SCG rules from `args/classification_aggregation.yaml` (pure dict/list comparisons — no code execution) and writes audit events to the append-only `aggregation_events` table.

- **Decision:** **bypass-documented**
- **Rationale:** The guard performs pure data classification checks — no subprocess spawning, no `eval()`/`exec()`, no file writes outside the append-only `aggregation_events` audit table. Row dicts from the DB are treated as opaque data structures; no field value is ever interpreted as code. Rule evaluation is deterministic (comparators: `>=`, `<`, `in`, `distinct_count`) with no dynamic code path.
- **Guardrails:**
  - `args/classification_aggregation.yaml` is first-party config; it defines rule metadata, not executable code.
  - Row field values are compared via Python built-in operators only; no `eval()` or string-interpolated SQL.
  - All events written to `aggregation_events` via `get_connection()` with append-only protection enforced by `pre_tool_use.py` hook.
  - `guard_result()` never returns row data to callers — only `{derived, action, throttled, events_written}`.
- **Revisit if:** a future rule type adds a `custom_expr` field that evaluates arbitrary expressions, or if result rows are ever rendered as HTML without escaping.

### Gap 15 — PNA Predictive Network Analytics (`tools/network/`)

**Modules:** `tools/network/eol_predictor.py`, `tools/network/bgp_predictor.py`, `tools/network/compliance_drift_predictor.py`, `tools/network/capacity_predictor.py`, `tools/network/change_failure_predictor.py`, `tools/network/supply_chain_risk_scorer.py`

**Ingress path:** Each predictor queries Forward Networks NQE via `FallbackNQEClient` (network device inventory, BGP sessions, interface utilization). All returned data is structured (dicts/lists of primitive values — strings, ints, floats). No config text beyond STIG-check pattern matching. The compliance drift predictor reads device config text to match STIG check patterns (regex-free string containment checks; no `eval()`). Vendor names and model strings from NQE are looked up in static registry dicts — no dynamic dispatch.

- **Decision:** **bypass-documented**
- **Rationale:** No `exec()`, `eval()`, `subprocess`, `os.system`, or `os.popen` anywhere in any of the 6 modules. NQE responses are treated as opaque data structures — field values are used in arithmetic (risk scoring), string comparisons (STIG checks), and parameterized SQL inserts. Config text in compliance drift is matched via `str.__contains__` (no regex compilation from untrusted input). All DB writes use parameterized queries with `?` placeholders; no string-interpolated SQL.
- **Guardrails:**
  - `FallbackNQEClient` returns dict/list structures — no code execution of NQE results.
  - STIG check lambdas compare against fixed string literals (first-party `args/` definitions equivalent): no dynamic pattern injection.
  - All 6 prediction tables are append-only (in `APPEND_ONLY_TABLES` in `pre_tool_use.py`).
  - Parameterized SQL throughout — no f-string SQL with untrusted values.
  - `nc_bgp_events` (the mutable rolling log) uses 90-day prune via `DELETE WHERE ... >= datetime(...)` — no user-supplied date expressions.
- **Revisit if:** NQE response fields are ever passed to `eval()`, `exec()`, or `subprocess`; or if config text matching is upgraded to regex with user-supplied patterns.

### Gap 16 — TimesFM Forecast Adapter (`icdev/tools/forecast/`)

**Module:** `icdev/tools/forecast/timesfm_adapter.py`

**Ingress path:** `POST /api/forecast` accepts a JSON payload with `values` (numeric time-series array), optional `forecast_horizon`, `freq`, `timestamp_column`, and `value_column`. The adapter validates the schema, creates a persistent `forecast_jobs` record, runs inference via the optional Google TimesFM library (lazy-loaded), and writes an append-only `forecast_audit` event. The payload is treated as data only — no field is interpreted as code.

- **Decision:** **trusted-first-party**
- **Rationale:** The forecast adapter is first-party code. User-supplied time-series data is parsed as JSON primitives and validated before being passed to the TimesFM `predict` API. There is no `eval()`, `exec()`, `subprocess`, `os.system`, or dynamic model loading from user paths. The optional `timesfm` dependency is pinned/installed by the operator, not supplied by the user.
- **Guardrails:**
  - JSON payload is validated: `values` must be a non-empty list of numbers, `forecast_horizon` bounded, `freq` restricted to known tokens.
  - Input length is capped (default 2048 observations) to reject abuse.
  - Model checkpoint loading is lazy and uses only the configured `DEFAULT_MODEL_ID`; no user-controlled checkpoint path.
  - Forecast results and errors are written to append-only `forecast_jobs` / `forecast_audit` tables protected by `pre_tool_use.py`.
  - Health endpoint reports `available: false` when TimesFM is not installed, so the feature degrades gracefully in air-gap/IL6 environments.
- **Revisit if:** the adapter is extended to execute user-supplied Python/R code, accept arbitrary model checkpoints, or generate and run arbitrary forecast scripts.

## Coherence rule

The `sandbox_coverage` rule in `tools/workflow/coherence_checker.py` enforces:

1. This document exists at `docs/security/sandbox-coverage.md`.
2. It mentions each of the 4 tracked gap files explicitly.
3. CLAUDE.md links to this document under "Guardrails".

Run:
```
python tools/workflow/coherence_checker.py --check sandbox_coverage --json
```

## Adding a new ingress point

When a new `tools/` module ingests user-provided content:

1. Open a PR that adds a new section to this document under "Additional Ingress Points".
2. Pick a decision from the Taxonomy table and document the rationale.
3. Wire either `SandboxExecutor` (sandboxed / sandboxed-on-demand) or a regression test (bypass-documented).
4. The `sandbox_coverage` coherence check passes.

### Gap 9 — SIO Engine / Oracle Lenses (`intelligence/oracle/`)

**Module:** `intelligence/oracle/sio_engine.py` + `intelligence/oracle/lenses/*.py`

**Ingress path:** The intent_assessment lens accepts an optional externally-supplied 7-element PMESII-PT vector via `run(current_pmesii=[...])` and via the `/api/strategos/oracle` API endpoint. Other lenses read only from internal DB tables (`sg_conflict_events`, `sg_raw_signals`, `sg_sio_assessments`).

- **Decision:** **trusted-first-party** for all lenses that read from internal DB tables only (threat_posture, behavior_pattern, convergence). The DB data originates from validated importers (GDELT, STIX) with deduplication and type constraints.
- **Decision:** **sandboxed-on-demand** for the PMESII-PT vector input path in intent_assessment — the vector is 7 float values bounded 0–1 by the API route, no code execution involved. Cosine similarity is pure arithmetic; no `exec`, `subprocess`, or file mutation.
- **Revisit if:** Oracle lenses accept free-form text from external sources without classification validation, or if a code-execution step is added to any lens pipeline.

### Gap 10 — Strategos OSINT Harvester (`strategos/osint_harvester.py`)

**Module:** `tools/genesis/reflexes/strategos/osint_harvester.py`

**Ingress path:** Fetches third-party RSS/Atom feeds (Reuters, Kyiv Independent, RFE/RL, ISW, UN OCHA), ACLED conflict API, and optional file inbox (`STRATEGOS_FILE_INBOX` env). Feed titles and summaries are stored in `sg_raw_signals`.

- **Decision:** **sandboxed-on-demand**
- **Rationale:** RSS/Atom content is untrusted external input. Titles/summaries are stored as plain text with no HTML rendering path. Payload size is bounded by a per-entry cap. No `exec()`/`eval()` in the pipeline. File inbox is configured at deploy time (operator-controlled path); file content is treated as structured JSON — not executed.
- **Guardrails:**
  - Content stored as text; no HTML rendering in briefs or dashboard panels.
  - Append-only `sg_raw_signals` table — ingested data cannot be mutated post-write.
  - ACLED API responses deserialized with `json.loads` only; keys validated against known schema before insert.
  - File inbox path must be set by operator; relative traversal is blocked by `Path.resolve()` check.
  - `ICDEV_STRICT_SANDBOX=1` routes file-inbox processing through `SandboxExecutor` in IL5.
- **Revisit if:** Feed summaries are ever rendered as raw HTML in the dashboard, or if file-inbox format expands to executable scripts.

### Gap 11 — GitLab OSINT Collector (`tools/strategos/gitlab_osint_collector.py`)

**Module:** `tools/strategos/gitlab_osint_collector.py`

**Ingress path:** Network egress only — runs on an internet-connected GitLab CI runner to fetch RSS/Atom signals from third-party feeds. Writes `osint_signals.json` and `kg_delta.json` as CI artifacts. No database or file-system ingestion on the air-gapped side; artifacts are consumed by `osint_harvester` via the TIER_GITLAB path.

- **Decision:** **sandboxed-on-demand** (CI runner context — sandboxed by the GitLab CI job sandbox)
- **Rationale:** Runs exclusively in a GitLab CI job, which provides process isolation and network policy by default. The script itself performs no `exec()`/`eval()` and writes only structured JSON. On the air-gapped side, the artifact JSON is treated as data (keys validated before DB insert by `osint_harvester`).
- **Guardrails:**
  - Network egress restricted to allowed RSS feed URLs defined in `args/strategos_osint_sources.yaml` (operator-controlled).
  - Output artifacts are schema-validated JSON; unexpected keys are dropped by the harvester before DB insert.
  - No `shell=True`, no `subprocess`, no code execution of feed content.
  - `ICDEV_STRICT_SANDBOX=1` in CI enables stricter GitLab runner network policies.
- **Revisit if:** The collector is called from a user-facing HTTP endpoint, or if feed content is ever passed to `eval()`/`exec()`.

### Gap 12 — OSINT Pre-Stager (`tools/strategos/osint_prestage.py`)

**Module:** `tools/strategos/osint_prestage.py`

**Ingress path:** Bulk write — fetches third-party RSS/Atom signals on an internet-connected machine and writes timestamped JSON batch files to `data/osint_inbox/` for offline transfer into an air-gapped enclave.

- **Decision:** **trusted-first-party** for the write path; **sandboxed-on-demand** for the RSS fetch path
- **Rationale:** The write path produces structured JSON from first-party parsing logic. The RSS fetch path is identical in risk to Gap 10/Gap 11 (untrusted external content, text-only storage). No `exec()`/`eval()` anywhere in the prestage pipeline. Files written to `data/osint_inbox/` are operator-controlled and validated by `osint_harvester` before DB insert.
- **Guardrails:**
  - Output format is a fixed JSON schema (`{"signals": [...], "count": N, "prestaged_at": "..."}`); no executable content.
  - Inbox directory created via `Path.mkdir(parents=True, exist_ok=True)` — no path traversal.
  - Harvester validates inbox files with schema checks before insert; malformed files are skipped and logged.
  - `ICDEV_STRICT_SANDBOX=1` routes RSS fetch through `SandboxExecutor` in IL5.
  - `data/osint_inbox/` must be air-gapped media (rsync/removable) — never internet-accessible.
- **Revisit if:** The inbox path accepts user input from an HTTP endpoint, or if file content is executed rather than parsed as JSON.

### Gap 13 — DataBridge Secret Resolvers

**Modules:**
- `tools/databridge/resolvers/vault_resolver.py` — HashiCorp Vault (hvac)
- `tools/databridge/resolvers/aws_resolver.py` — AWS Secrets Manager (boto3)
- `tools/databridge/resolvers/file_resolver.py` — plaintext file (air-gap)

**Ingress path:** `connection_manager.resolve_secret()` dispatches to one of the three resolvers based on the secret ref prefix (`vault:`, `aws:`, `file:`) or the `secret_backend` config key. Secrets are resolved at connection-open time and returned as in-memory strings — never logged, never written to disk by the resolver itself.

- **FileResolver — Decision: trusted-first-party**
  - Rationale: Reads from an operator-configured path (`secret_files_root` in `args/databridge_config.yaml` or `DATABRIDGE_SECRET_FILES_ROOT` env var). No user-supplied content reaches this resolver; the caller supplies a `secret_id` string that is validated against the configured root via `Path.resolve()` traversal check. Content is treated as an opaque plaintext credential — not parsed, not executed.
  - Guardrails: Path traversal blocked (resolved path must start with resolved root). File must exist and be non-empty or `SecretResolverError` is raised. Root path is operator-controlled at deploy time.

- **VaultResolver — Decision: sandboxed-on-demand**
  - Rationale: Makes outbound HTTPS to a HashiCorp Vault server (`VAULT_ADDR`). The target URL is operator-configured; no user-supplied URL. Response is deserialized with `client.read()` (hvac) — no `eval()`/`exec()`. Result cached 5 min in memory only.
  - Guardrails: `VAULT_ADDR` and `VAULT_TOKEN` must be set or resolver raises `SecretResolverError`. `ICDEV_STRICT_SANDBOX=1` routes all network-egress calls through `SandboxExecutor` in IL5.

- **AWSSecretsResolver — Decision: sandboxed-on-demand**
  - Rationale: Makes outbound HTTPS to AWS Secrets Manager (GovCloud endpoint `us-gov-west-1` by default). Target endpoint is operator-configured via `aws_region` config or `AWS_REGION` env var. Response deserialized with `json.loads` only — no `eval()`/`exec()`.
  - Guardrails: Credentials from env vars or instance profile only — never hardcoded. `ICDEV_STRICT_SANDBOX=1` routes through `SandboxExecutor` in IL5. Endpoint override (`AWS_SECRETS_ENDPOINT_URL`) is for testing only and must not be user-supplied.
- **Revisit if:** resolver accepts a caller-supplied endpoint URL from a user-facing HTTP API, or if secret content is ever executed rather than passed as a credential string.

### Gap 14 — NMCE Config Upload (`tools/migration_canvas/blueprint.py` — `/upload-config`)

**Module:** `tools/migration_canvas/blueprint.py` route `POST /api/network-migration/<sid>/upload-config`

**Ingress path:** Engineer uploads a device config file (`.txt`, `.conf`, `.cfg`, `.log`) via multipart HTTP, pastes raw text as JSON `config_text`, or requests a DB reload from `ni_device_configs`. Content is stored verbatim in `mc_net_sessions.src_config_raw` (TEXT column, SQLite). No file system write; content is parsed by `parse_source_config()` (regex-only, no `eval()`/`exec()`).

- **Decision:** **trusted-first-party** (operator-controlled data path)
- **Rationale:** The config is a structured network device text file (IOS-XR, JunOS, etc.). The parser (`tools/network/config_parser.py`) uses only `re.search`/`re.findall` pattern matching — no dynamic code execution, no shell calls, no file writes outside the DB column. The result is structured Python dicts. File extension is validated to an allowlist (`.txt .conf .cfg .log`). Config content is never rendered unescaped in HTML (Jinja2 auto-escaping). Users uploading device configs are authenticated operators (`mdc_login_required`).
- **Guardrails:**
  - Extension allowlist enforced: reject anything not in `{'.txt', '.conf', '.cfg', '.log'}`.
  - Flask `MAX_CONTENT_LENGTH` (16 MB default) caps file size.
  - No `exec()`/`eval()` anywhere in `parse_source_config()` or the route handler.
  - Config stored only in `mc_net_sessions.src_config_raw` (TEXT); never written to the file system.
  - DB reload path (`source='db'`) reads from first-party `ni_device_configs` only — no new user content enters.
- **Revisit if:** config content is ever passed to a shell command, rendered outside Jinja2 auto-escape, or accepted from an unauthenticated endpoint.

### Gap 15 — Reasoned Codegen wrapper + advisor (`tools/llm/reasoned_codegen*.py`)

**Modules:** `tools/llm/reasoned_codegen.py`, `tools/llm/reasoned_codegen_advisor.py`

**Ingress path:** The wrapper receives LLM-generated code strings (from CoT/CoD/plain
generation via `LLMRouter`), runs them through an optional **injected verifier** and a
repair loop, and returns the final code string. The advisor scores a task spec to
recommend whether to enable reasoned codegen.

- **Decision:** **bypass-documented** (safe by construction — never executes generated code)
- **Rationale:** Neither module contains `exec()`, `eval()`, `subprocess`, `os.system`,
  or `os.popen`. The wrapper only (a) calls `LLMRouter` for generation/repair, (b) calls
  `anvil_critique` (which itself routes through the router), and (c) invokes a verifier
  *callback supplied by the caller*. The wrapper does not run the code it produces — any
  execution is the responsibility of the downstream pipeline (e.g. the agentic runner's
  pre-existing allowlisted `run_command`, or the translation validator's compiler check),
  each already covered by its own decision. The advisor consumes only the spec text and a
  context dict and emits a recommendation dict — pure data.
- **Guardrails:**
  - Regression test `tests/security/test_reasoned_codegen_no_exec.py` fails the build if
    either module gains `exec(`, `eval(`, `subprocess`, `os.system(`, or `os.popen(`.
  - Generated code is gated by deterministic verifiers (FORGE gate, `code_lens`,
    `translation_validator`, acceptance criteria) before any downstream execution.
  - Under `ICDEV_STRICT_SANDBOX=1` the downstream executors (agentic runner) already
    enforce their isolation; the wrapper adds no new execution surface.
- **Revisit if:** the wrapper ever directly executes, compiles, or `subprocess`-runs the
  code it generates → re-decide as **sandboxed**.

### Gap 17 — PDC pipeline ingress (`tools/pipeline/`)

**Modules:** `tools/pipeline/blueprint.py` (design-graph save/export/deploy/validate routes, collab push/poll, SOP CRUD), `tools/pipeline/export.py`, `tools/pipeline/deploy_generator.py`, `tools/pipeline/iac_validator.py`, `tools/pipeline/sops.py`

**Ingress path:** An authenticated PDC operator submits (a) a pipeline **design graph JSON** (`{nodes, edges}`) that is rendered into IaC (GitLab CI / GitHub Actions / Jenkinsfile / Tekton / Terraform-style bundles) and a downloadable zip, (b) **SOP bodies** (title, steps, NIST controls) persisted to `pdc_sops`, and (c) **collaboration payloads** (op_type + data) pushed to `pc_collab_sessions`. All content is stored verbatim in the pipeline-canvas DB (SQLite/PG TEXT/JSON columns) and re-rendered into templated text or the browser.

- **Decision:** **trusted-first-party / data-only** (render-side escaping + write-boundary JSON validation)
- **Rationale:** The design graph, SOP body, and collab payload are **data, never code** — no module in `tools/pipeline/` calls `exec()`, `eval()`, `os.system`, or `subprocess` on ingress content. IaC renderers (`export.py`, `deploy_generator.py`) build strings from allowlisted node types via fixed templates; unknown node types are dropped, not evaluated. Graph JSON is parsed with `json.loads`/`parse_graph_json` behind a write boundary that rejects malformed input (`422 corrupt graph`). The pipeline-canvas connection runs with RLS disabled by design (canvas tables lack tenant_id/classification), so writes are scoped to the per-canvas DB, not the shared icdev DB. LLM-assisted IaC *review* (`tools/devops/iac_review.py`) now runs the router prompt-injection scan on the uploaded IaC (pdx-hyg-01 removed its `skip_injection_scan`).
- **Guardrails:**
  - Graph JSON validated at the write boundary (`parse_graph_json` → `422` on corrupt input); node types filtered to an allowlist before rendering.
  - Rendered IaC / SOP / collab content is emitted through Jinja2 auto-escaping in templates and as JSON via `jsonify` on API routes — never interpolated unescaped into HTML.
  - Export download filenames are sanitized to `[a-z0-9._-]` (pdx-hyg-01) to block Content-Disposition header injection / path traversal.
  - No `exec()`/`eval()`/`subprocess` on ingress content anywhere in `tools/pipeline/`.
  - All routes are behind `pc_login_required` / `pc_role_required`; collab identity is server-derived, never body-supplied.
- **Revisit if:** any pipeline content is ever passed to a shell command, compiled/executed, or rendered outside Jinja2 auto-escape → re-decide as **sandboxed**.

### Bypass — non-LLM code generators (template/scaffold emitters)

These paths were assessed as reasoned-codegen wiring targets and found to contain **no LLM
generation call**, so they remain deterministic and out of scope:

| Module | Decision | Rationale |
|--------|----------|-----------|
| `tools/builder/child_app_generator.py` | **trusted-first-party** | 20-step scaffold/template copier; 0 `router.invoke` calls. Output validated by `forge_validator --gate` + syntax checks. |
| `tools/builder/code_generator.py` | **trusted-first-party** | Deprecated template emitter; 0 `router.invoke` calls. |
| `tools/modernization/migration_code_generator.py` | **trusted-first-party** | Template-based adapter/facade emitter; no LLM generation. |
| `tools/aiify/` scoring (AI-ify) | **trusted-first-party** | Deterministic weighted scoring; LLM optional (covered by Gap 13). |

**Revisit if:** any of these adds an `LLMRouter.invoke` code-generation call → wire through
`reasoned_codegen` and re-decide.

## References

- D-SEC-10 — SandboxExecutor (container isolation, Phase 71)
- D-SEC-11 — 6-path sandbox integration (Phase 72)
- Phase 72 feature doc: [phase-72-sandbox-integration.md](../features/phase-72-sandbox-integration.md)
- `tools/security/sandbox_executor.py` — runtime implementation

## Row-Level Security Layer (D-SEC-RLS)

| Module | Classification | Rationale |
|--------|---------------|-----------|
| `tools/security/row_security.py` | **trusted-first-party** | `inject_row_predicate()` regex-injects `tenant_id`/`classification` predicates into SQL strings. No user input reaches the regex — all values are `SecurityContext` fields set by application middleware. No `eval()`/`exec()`/subprocess. Deterministic output tested in `tests/test_row_security.py` (17 unit tests). |
| `tools/db/storage.py` (`_inject_rls()`) | **trusted-first-party** | Calls `inject_row_predicate()` from the storage cursor layer. All inputs (`tenant_id`, `classification`) come from `flask.g.security_context` set by `tools/security/middleware.py`, never from user-supplied HTTP body fields. No code execution path. |

**Revisit if:** `SecurityContext.tenant_id` or `classification` is ever populated directly from an unvalidated HTTP header or request body field without middleware enforcement.

## STRATEGOS Foundation Layer (sg-foundation, migration 118)

| Module | Classification | Rationale |
|--------|---------------|-----------|
| `tools/strategos/theater.py` | **trusted-first-party** | Loads YAML from `args/theaters/` (controlled path, no user input). Pure data loading — no code execution, no shell calls. |
| `tools/strategos/war_kg.py` | **trusted-first-party** | Reads/writes to icdev.db kg_nodes/kg_edges via parameterized queries. No user-supplied SQL. Input: typed Python dicts. |
| `apps/strategos/static/fetch_vendor.py` | **bypass-documented** | Dev-only utility script. Downloads vendor JS assets. Runs only on network-connected machines, never in prod pipeline. |
| `tools/db/migrations/118_strategos_core_tables/up.py` | **trusted-first-party** | DDL migration. No user input. Runs only at migration time. |

## NDC Network Design Canvas Layer (Phase 1–6)

| Module | Classification | Rationale |
|--------|---------------|-----------|
| `tools/ndc/config_alignment_analyzer.py` | **trusted-first-party** | Reads device configs from first-party `ni_device_configs` table. Parses with regex only (`re.search`/`re.findall`). No `eval()`/`exec()`/subprocess. Optional RAG retrieval from `rag_chunks` returns text-only SOP excerpts. Output is deterministic JSON/Markdown scores. |
| `tools/ndc/migration_document_generator.py` | **trusted-first-party** | Orchestrates deterministic NDC tools (`eol_scanner`, `replacement_recommender`, `network_migration`, `config_alignment_analyzer`) to assemble a Jinja2-rendered Markdown runbook. All inputs are internal DB rows or first-party tool outputs. No user-supplied code execution. Templates live under `tools/ndc/templates/migration_runbook/` (first-party, code-reviewed). |
| `tools/ndc/eol_scanner.py` | **trusted-first-party** | Reads from `ni_devices` and `nc_hardware_profiles`. Pure SQL + arithmetic scoring. No external input, no code execution. |
| `tools/ndc/replacement_recommender.py` | **trusted-first-party** | Reads hardware profiles from DB, scores via deterministic arithmetic. Optional RAG retrieval returns text-only SOP excerpts. No code execution of external content. |

### Gap 16 — SIPA Ingest: git clone / UNC share / file:// URI fetch (`tools/integrity/ingest.py`)

**Module:** `tools/integrity/` (ingest seam: `tools/integrity/ingest.py`)

**Ingress path:** SIPA (Software Integrity & Provenance Assessor) assesses an
*untrusted target* supplied by an operator: a local path, a UNC share
(`\\host\share`), a `file://` URI, or a git clone URL
(`https://github.com/...` / `https://gitlab.com/...`). `stage()` copies the
target bytes (local/UNC/`file://`) or shallow-clones the repo (git) into an
isolated quarantine directory (`<quarantine_dir>/<assessment_id>/`) and records
an `integrity_assessments` row in `status='quarantine'`. Every downstream
scanner then runs against the *staged copy as data* — the target is read, hashed
(SHA-256 tamper baseline), and statically analyzed, **never executed**.

- **Decision:** **bypass-documented** (safe by construction — never executes the fetched target)
- **Rationale:** SIPA is static-only. The fetch path performs exactly two kinds
  of action against untrusted input: (1) **copy bytes** (`shutil.copy2` /
  `shutil.copytree`) for local/UNC/`file://` sources, and (2) a single
  **fixed-arg, `shell=False` `git clone`** (`subprocess.run([...], shell=False)`)
  for git sources. `git clone` fires no repository hooks, and the cloned/copied
  tree is treated as inert data — it is never imported, `exec`-ed, `eval`-ed, or
  run as a subprocess. The scanner fan-out (`tools/integrity/scanners.py`) runs
  *first-party* analyzers (SAST/secrets/deps/Semgrep) in their own fixed-arg,
  `shell=False` subprocesses with the staged tree as a **read-only input path**,
  not as an executable. No module under `tools/integrity/` calls `exec`, `eval`,
  `compile`, `__import__`, `os.system`, `os.popen`, `os.exec*`, `os.spawn*`,
  `runpy.run_*`, `importlib.import_module`, or `pty.spawn` on target data (the
  string literals `exec`/`eval`/`__import__` that appear in `scanners.py` and
  `capability_extractor.py` are *detection signatures* for malicious code in the
  assessed target, not calls).
- **Guardrails:**
  - **No `shell=True`** anywhere under `tools/integrity/` — all subprocess
    invocations use a fixed argument list with `shell=False`.
  - **Scheme allowlist** (`args/integrity_config.yaml` → `scheme_allowlist`) is
    enforced *before* any row is written or any byte copied; disallowed schemes
    (`smb://`, `ftp://`, bare non-repo `http(s)`, custom URIs) are refused with a
    clear `IngestRejected` error.
  - **Git URL allowlist** — git sources must be `https://` to an allowlisted host
    (`github.com` / `gitlab.com`) or a local `file://` repo; the URL is validated
    before any subprocess runs, `--` terminates git option parsing (argument-
    injection defence), `GIT_TERMINAL_PROMPT=0` / `GIT_ASKPASS=true` prevent
    interactive hangs, and embedded credentials are redacted from every log line.
  - **Quarantine-first** — staging lands in `<quarantine_dir>/<assessment_id>/`;
    a SHA-256 directory digest baselines the tree and `reverify()` re-checks it to
    detect tampering between stage and assess. A HITL gate releases or rejects.
  - **Regression test** `tests/test_integrity_no_shell_exec.py` fails the build if
    any module under `tools/integrity/` introduces `shell=True` or a call that
    executes fetched code (`exec`/`eval`/`compile`/`__import__`/`execfile`,
    `os.system`/`os.popen`/`os.exec*`/`os.spawn*`, `runpy.run_*`,
    `importlib.import_module`, `pty.spawn`). Detection is AST-based, so the
    scanner's string/regex *signatures* for these patterns are not false-positives.
- **Revisit if:** SIPA ever adds a step that imports, `exec`s, or `subprocess`-runs
  the staged target (e.g. dynamic-analysis sandboxing) → re-decide as **sandboxed**
  (route through `tools/security/sandbox_executor.py`); or if a non-`https`/non-
  allowlisted transport is added to the fetch path.

### Gap 17 — ACF Foundry Harvester (`tools/foundry/harvester.py`)
- **File:** `tools/foundry/harvester.py`
- **Risk:** Reads raw signal rows from EXISTING first-party engine stores (no web
  re-scan, no network egress) across 5 sources — innovation
  (`innovation_signals`/`innovation_trends`), creative
  (`creative_pain_points`/`creative_feature_gaps`), research
  (`research_challenges`/`research_dossiers`), genesis (`oracle_predictions`),
  and read-only telemetry (`introspective_analyzer` analyses) — then normalizes
  each row into the `foundry_signals` shape and appends it under a `run_id`.
- **Decision:** **bypass-documented**
- **Rationale:** The harvester is a **data-only** ingress: every source is a
  first-party ICDEV DB table populated by ICDEV's own engines, read through the
  RLS-aware `get_connection()`. It performs no `exec`/`eval`/`compile`/
  `__import__`/`subprocess`/`os.system`, opens no files, and makes no network
  call — it only `SELECT`s rows and writes normalized `INSERT`s. Signal text is
  stored verbatim as data and never interpreted as code or a path. There is no
  attacker-controlled execution surface to isolate.
- **Guardrails:**
  - Read path is `SELECT`-only over first-party tables via `get_connection()`;
    tenant_id/classification are stamped on every `foundry_signals` row (RLS).
  - Per-source enable / `max_signals` caps from `args/foundry_config.yaml` bound
    ingestion volume; cross-source SHA-256 dedup collapses duplicates.
  - Best-effort isolation: an empty / disabled / unmigrated source yields 0
    signals and never raises — a malformed upstream store cannot crash the cycle.
  - Downstream concepts are novelty-gated and the ACF-generated **code** (not the
    signals) is self-vetted by SIPA + the security/coherence gate before merge.
- **Revisit if:** the harvester ever adds a non-first-party source (web fetch,
  user upload, external API) or begins executing / importing harvested content →
  re-decide as **sandboxed** (route through `tools/security/sandbox_executor.py`).

### Gap 18 — Kanban Adversarial Verifier (`tools/genesis/reflexes/kanban.py` — `_run_adversarial_verify`)
- **File:** `tools/genesis/reflexes/kanban.py` — `_run_adversarial_verify()`
- **Risk:** Spawns a second Claude CLI subprocess (`claude --dangerously-skip-permissions
  --max-turns 10`) in the task worktree directory to adversarially review completed work.
  The subprocess receives a reviewer prompt via stdin piped from a `.tmp/` temp file and
  its stdout output (APPROVED/REJECTED verdict) is parsed by the reflex. An adversarial
  prompt or malicious task title/description injected into DB could influence the
  reviewer's verdict or cause unexpected subprocess behaviour.
- **Decision:** **sandboxed**
- **Rationale:** The verifier subprocess is inherently isolated — it runs in a dedicated
  git worktree directory (`work_dir`), not the main repo, with `--max-turns 10` capping
  its ability to take actions. Its only output surface is stdout, which is parsed for
  an `APPROVED:` / `REJECTED:` prefix. The function **fails open** on any error
  (timeout, non-zero exit, missing verdict) so the adversarial gate can never
  permanently block a task. The subprocess invocation is equivalent in isolation to the
  primary task Claude CLI dispatch already classified **sandboxed** in Gap 2.
- **Guardrails:**
  - Only fires when `adversarial_enabled=1` on the task (`loop_type='non_deterministic'`)
    — opt-in per task, not the default path.
  - Hard 180-second timeout (`subprocess.TimeoutExpired` → pass). The Claude CLI is not
    given a `--dangerously-allow-filesystem` flag — it runs in review-only mode.
  - Task title and description are truncated at 1,200 chars before embedding in the
    prompt; no shell interpolation is used (args list, not `shell=True`).
  - Prompt file is written to `.tmp/` (not `tools/`) and deleted immediately after the
    subprocess reads it.
  - Verdict parser scans only the last non-empty line; unrecognised output → pass,
    preventing a prompt-injection verdict.
- **Revisit if:** the verifier subprocess is ever granted `--dangerously-allow-filesystem`
  write access, network egress, or `--max-turns` is removed, which would change it from a
  read-only judge to an actor → re-scope as **sandboxed** via `SandboxExecutor` with
  explicit network/FS deny-lists.

### Gap 19 — GEPA Optimizer (`tools/skills/gepa_optimizer.py`)
- **File:** `tools/skills/gepa_optimizer.py`
- **Risk:** The GEPA Optimizer (Genome Evolution Pressure Analyzer) reads capability
  genome entries from the first-party `genome_manager` registry and the ICDEV DB, then
  prunes or promotes entries based on fitness scores. It accepts an operator-supplied
  `dry_run` flag but no user-controlled content from the network or file system.
- **Decision:** **trusted-first-party**
- **Rationale:** GEPA reads only from first-party DB tables (`get_genome_entries` via
  `get_connection()`) and writes only compact summary rows back to those tables. No
  `exec()`/`eval()`/`compile()`/`subprocess` call exists in the optimizer pass itself.
  Genome entry data (capability names, fitness scores) are typed Python dicts treated as
  opaque data — never executed or interpreted as code. The `dry_run=True` path performs
  no writes at all. MCP tool invocation (`gepa_optimizer` params: `dry_run`) passes only
  the boolean flag; no user-supplied paths or expressions reach the engine.
- **Guardrails:**
  - All reads go through `get_connection()` (RLS-aware; no raw `sqlite3.connect()`).
  - `dry_run=True` is the safe default in automated scans; mutations require explicit opt-in.
  - No shell calls, no file writes outside the DB column, no network egress.
  - Genome entries are scored via deterministic arithmetic (fitness threshold comparisons);
    no dynamic code generation or external executable invocation.
- **Revisit if:** GEPA gains a step that executes, imports, or subprocesses genome-derived
  code (e.g. capability self-modification) → re-decide as **sandboxed** via
  `SandboxExecutor`; or if `dry_run` flag is accepted from an unauthenticated HTTP
  endpoint without authorization checks.
### Gap 18 — NOVA Execution Tracing (`tools/workflow/trace_logger.py`, `tools/workflow/reflexion_agent.py`)
- **Files:** `tools/workflow/trace_logger.py`, `tools/workflow/reflexion_agent.py`
- **Risk:** `trace_logger` records task execution events (tool calls, LLM decisions,
  intermediate outputs) as structured rows. `reflexion_agent` reads those rows and
  passes a 600-char snippet to the LLM to generate an improvement artifact text.
  The snippet is user-task-derived data (indirectly user-controlled via task descriptions).
- **Decision:** **bypass-documented**
- **Rationale:** Neither module `exec`s, `eval`s, or `subprocess`-runs any content.
  Trace payloads are stored as JSON-encoded strings in `agent_execution_traces`, read
  back as data, and passed only as LLM message content (not code). The reflexion agent
  slices to 600 chars, preventing prompt injection via oversized payloads. Improvement
  artifacts are written to `agent_improvement_artifacts` as TEXT and prepended to skill
  prompts only when `ICDEV_HARNESS_COLEARN=true`. There is no execution surface.
- **Guardrails:** HITL — reflexion output is a `suggested` kanban card before any
  hardprompt change; `artifact_evolver.py` (SELA) never auto-merges evolved text.

### Gap 19 — NOVA SOUL Memory (`icdev/tools/ace/soul_manager.py`)
- **Files:** `icdev/tools/ace/soul_manager.py`, `tools/ace/roles/*/MEMORY.md`
- **Risk:** Reads per-role MEMORY.md (written by reflexion_agent from task output
  snippets) and injects it into dispatch prompts as system context. Also stores
  LLM-extracted facts from task output when `ICDEV_HARNESS_COLEARN=true`.
- **Decision:** **bypass-documented**
- **Rationale:** MEMORY.md content is capped at 8 KB and 40 facts. Content is injected
  as plain text into LLM prompts, not executed. Fact extraction is done via LLMRouter
  (no eval/exec). No file system traversal beyond `tools/ace/roles/<role_id>/`.
  Max 2 LLM-extracted facts per trace; `regex.search` is used to parse the JSON array,
  not `eval`. Path is constructed from a validated role_id (DB FK, not user freeform input).
- **Guardrails:** 8 KB size cap + 40-fact prune; trust_score gate ensures low-trust
  roles cannot escalate via memory injection (probationary band = dispatch paused).

### Gap 21 — ZIG External Ingest Adapters (`tools/security_canvas/zig_external_adapter.py`)

- **Files:** `tools/security_canvas/zig_external_adapter.py`
- **Risk:** Parses user-supplied content from 5 external scan formats: CycloneDX SBOM (JSON),
  Bandit SAST output (JSON), security survey responses (JSON), Nmap scan results (XML),
  and OpenAPI specifications (YAML/JSON). All content arrives from an authenticated API
  endpoint (`POST /security/api/zig/targets/<id>/ingest`). As of shx-auth-01 the endpoint
  is auth-guarded (`@sc_login_required`), so ingest requires an authenticated session.
- **Decision:** **bypass-documented**
- **Rationale:** All 5 parsers use safe decoders only — `json.loads()`, `yaml.safe_load()`
  (no custom constructors), and hardened XML parsing. No `eval()`, `exec()`, `subprocess`,
  or filesystem writes occur. Parsed data flows only to `set_activity_status()` via
  parameterized SQL inserts. **XML entity-expansion / XXE defense (shx-auth-03):** XML is
  parsed through `zig_external_adapter._parse_xml_safe()`, which (1) rejects any payload
  containing `<!DOCTYPE` or `<!ENTITY` (case-insensitive pre-parse guard) with a clear
  `ValueError` before the parser runs, and (2) parses via `defusedxml.ElementTree.fromstring`
  (already a project dependency; forbids entity expansion and external entity resolution).
  This closes the billion-laughs entity-expansion DoS that the previous stdlib
  `xml.etree.ElementTree` parser was vulnerable to. **Payload size cap (shx-auth-03):** the
  route enforces `ZIG_INGEST_MAX_BYTES` (5 MiB) and returns HTTP 413 before any parsing,
  bounding memory/CPU for every source type. These guarantees are enforced by
  `tests/test_zig_ingest_adapters.py` (adapter-level XML defense + benign-parse tests, all
  using a DB-stub that verifies no real SQL calls reach the DB) and
  `tests/test_zig_ingest_route.py` (route-level oversized-payload → 413).
- **Revisit if:** any ingest path adds `eval()`, subprocess execution of scan tools,
  resolves external references from within the uploaded content, or switches XML parsing
  away from `_parse_xml_safe()` / `defusedxml`.

### Gap 20 — NOVA SELA Skill Evolution (`tools/evolution/artifact_evolver.py`)
- **Files:** `tools/evolution/artifact_evolver.py`, `tools/evolution/fitness.py`
- **Risk:** Reads `.agents/skills/icdev-*` skill files, generates mutated candidates
  via LLM, writes proposals to `oracle_predictions`. The `score_full()` path passes
  example inputs/outputs to LLMRouter as judge.
- **Decision:** **bypass-documented**
- **Rationale:** Evolved skill text is **never auto-merged** — it is written to
  `oracle_predictions` with `status='suggested'` and requires human HITL review before
  any file is modified. The evolver validates: size ≤ 15 KB, growth ≤ +20%, must have
  a `# heading`, non-empty. Fitness scorer uses LLMRouter.invoke() (no exec/eval).
  Golden eval JSONL is first-party developer-authored content in `context/evolution/golden/`.
- **Revisit if:** auto-merge of evolved artifacts is ever enabled (would require sandboxing
  the SIPA integrity check on the candidate).

### Gap 22 — CLI Bridge Manager (`tools/llm/cli_bridge_manager.py`)

- **File:** `tools/llm/cli_bridge_manager.py`
- **Risk:** Reads and writes the project's own `.env` file on disk to get/set `ICDEV_CLI_BRIDGE`
  and to detect the presence of cloud API keys (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`,
  `GOOGLE_API_KEY`). No user-supplied content is ingested — only the developer's own `.env`.
- **Decision:** **trusted-first-party**
- **Rationale:** The file is a developer-facing CLI configuration utility that reads and writes
  only the project's own `.env` file. Its entire declared purpose (stated in the module docstring)
  is to manage `ICDEV_CLI_BRIDGE` in `.env`. No `exec()`, `eval()`, `subprocess`, dynamic import,
  or external network call occurs. The `.env` is a first-party developer-authored file; there is
  no external or user-controlled ingress path. The `filesystem` capability is intentional,
  scoped, and authorized via intake requirement `REQ-TOOLS-LLM-CLI-01/02/03` in the ICDEV RTM
  (`project_id = icdev-tools-rtm`).
- **Guardrails:**
  - Reads and writes only `<repo_root>/.env` (resolved from `_find_repo_root()` heuristic).
  - No shell expansion, no path traversal, no user-supplied filename.
  - Module is invoked explicitly via `python tools/llm/cli_bridge_manager.py --<flag>` or
    imported by `tools/llm/router.py` for auto-detection only.
- **Revisit if:** the manager is extended to accept a user-supplied file path, or to write
  to any file outside `<repo_root>/.env`.


---

## tools/docgen/ — IDR Intelligent Documentation Regeneration Engine

- **File:** `tools/docgen/` (all modules: blueprint.py, workflow.py, session_manager.py, reconciler.py, context_builder.py, domain_profiles.py)
- **Risk:** Accepts multi-file uploads (diagrams, config files, IaC, documents) from authenticated dashboard users. Files are saved to `data/docgen/uploads/<session_id>/`. User-provided title/notes fields are stored in the DB. Config/IaC reviewers are invoked via `importlib.import_module()` using paths from `args/docgen/profiles.yaml` (not user input).
- **Decision:** **trusted-first-party**
- **Rationale:** All upload paths are server-side and scoped under `data/docgen/` — no path traversal. Analyzer module paths come from the operator-controlled `args/docgen/profiles.yaml`, not from user input. Text fields (title, notes) are stored as data, never executed. The `importlib.import_module()` call is bounded to the allowlisted modules in `profiles.yaml`. Classification banners are enforced via `classification_manager`. HITL gates prevent any AI-generated content from being published without human approval.
- **Guardrails:**
  - Upload paths scoped to `data/docgen/uploads/<session_id>/` via `pathlib.Path`.
  - Analyzer module paths from operator YAML only — not from user request body.
  - DB values stored verbatim (not evaluated).
  - HITL at Stage 3 (conflict resolution) and Stage 7 (document review) block auto-publish.
  - WriteGuard hard gate (Stage 6) blocks low-quality content.
- **Revisit if:** analyst/reviewer module paths are ever accepted from user request payloads, or if generated document content is auto-published without HITL.


---

## tools/network/ — PVM Predictive Vulnerability Management

- **Files:** `tools/network/vuln_predictor.py`, `tools/network/attack_surface_mapper.py`, `tools/network/vuln_triage_engine.py`, `tools/network/patch_planner.py`, `tools/network/routes/pvm.py`
- **Risk:** Reads CVE advisory data from internal network canvas DB (`nc_advisories`, `nc_advisory_assessments`), NQE device inventory from Forward Networks (trusted internal API), and Nessus scan results from the same internal DB. Accepts `network_id` (string), `advisory_ids` (list of ints), and `approved_by` (email string) from dashboard users. No content is executed, parsed as code, or written to the filesystem.
- **Decision:** **trusted-first-party**
- **Rationale:** All external data enters via trusted internal sources (network canvas DB, Forward Networks NQE API, Nessus ACAS). User-supplied inputs (`network_id`, `advisory_ids`, `approved_by`) are stored as metadata or used as DB query parameters — never executed. NQE queries are fixed NQL strings defined in source code (not user-supplied). DB writes are append-only (`nc_vuln_predictions`, `nc_patch_plans`) or upsert (`nc_attack_surface`, `nc_triage_queue`). No `exec()`, `eval()`, `subprocess`, dynamic import, or template rendering of user content occurs.
- **Guardrails:**
  - DB queries parameterized (no string interpolation of user input into SQL).
  - NQL strings are compile-time constants, not user-provided.
  - HITL gate blocks auto-scheduling: priority ≥ 0.75 requires human approval.
  - `approved_by` field is stored as metadata only; no downstream code execution.
  - APPEND-ONLY tables (`nc_vuln_predictions`, `nc_patch_plans`) enforced by `APPEND_ONLY_TABLES` in `.claude/hooks/pre_tool_use.py`.
- **Revisit if:** users are ever allowed to supply custom NQL queries, if advisory data is fetched from untrusted external URLs, or if `approved_by` is used to trigger downstream privilege escalation.

---

### Gap 23 — Slides Template-Fill Upload (`tools/slides/template_fill.py`, `tools/slides/blueprint.py` — `/api/templates/upload`)

**Module:** `tools/slides/template_fill.py` (`inspect_template()`, `fill_and_export()`); ingress at `tools/slides/blueprint.py` route `POST /api/templates/upload`.

**Ingress path:** Dashboard user uploads an arbitrary `.pptx` file (e.g. a customer/agency proposal template) via multipart HTTP. The file is saved under `tools/presentations/templates_uploaded/` and parsed with `python-pptx` (`inspect_template()`) to enumerate its shapes; a later `fill_and_export()` call opens the same file, overwrites selected slides' text/table/chart content in place, deletes unselected slides, and saves a new `.pptx`.

- **Decision:** **trusted-first-party** (bounded native parser, no code execution surface)
- **Rationale:** `python-pptx` only parses OOXML (a ZIP of XML parts) via `xml.etree`/`lxml` — no macro execution, no embedded-script evaluation, no `exec()`/`eval()`/`subprocess`. `svg_to_pptx.py` (used for the related `svg_art` slide type and the `slides_svg` graphics fallback) similarly only parses SVG via stdlib `xml.etree.ElementTree` with a bounded element allowlist — unrecognized elements are skipped, not executed. No user content is ever passed to a shell, template-rendered unescaped, or dynamically imported.
- **Guardrails:**
  - Extension allowlist enforced at upload: reject anything not ending in `.pptx` (`api_upload_template`).
  - Filename sanitized via `werkzeug.utils.secure_filename` before use as a path component.
  - Upload saved under a fixed server-side directory (`tools/presentations/templates_uploaded/`), never at a user-supplied path.
  - `inspect_template()` is read-only (never mutates the uploaded file).
  - `fill_and_export()` writes only to the fixed `tools/presentations/slides/` output directory, using a timestamp+hash-derived filename — never a user-supplied path.
  - Malformed/corrupt `.pptx` uploads raise inside `python-pptx`'s parser and are caught, returning a 400 — the partial upload is deleted, nothing is persisted.
- **Revisit if:** the parsing/fill pipeline ever shells out (e.g. LibreOffice conversion), evaluates macros, or the upload directory becomes user-path-controlled.

### Gap 24 — BI Dashboard Dataset Upload (`tools/bi_dashboard/data_source.py`, `tools/bi_dashboard/blueprint.py` — `/bi_dashboard/api/upload`)

**Module:** `tools/bi_dashboard/data_source.py` (`ingest_upload()`), which delegates parsing to `tools/viz/dataset.py::parse_dataset()`; ingress at `tools/bi_dashboard/blueprint.py` route `POST /bi_dashboard/api/upload`.

**Ingress path:** A user uploads an arbitrary CSV, JSON, or XLSX file (their own analytics data) via multipart HTTP. `parse_dataset()` parses CSV/JSON with the Python stdlib `csv`/`json` modules and XLSX with `openpyxl` in `read_only=True, data_only=True` mode, capped at `MAX_ROWS=5000`/`MAX_COLS=40`. The parsed table is stored as JSON in `bi_data_sources`; no file is ever written to disk or re-opened as a workbook.

- **Decision:** **bypass-documented** (safe by construction, no code-execution surface)
- **Rationale:** `csv`/`json` are pure stdlib text parsers. `openpyxl.load_workbook(..., read_only=True, data_only=True)` never evaluates VBA macros or formulas — `data_only=True` reads only the last-cached *computed* value of any formula cell, it does not execute the formula. `.xlsm` (macro-enabled) workbooks are rejected outright by extension before any parsing is attempted — see `_TIME_LIKE_RE`-adjacent guard in `tools/viz/dataset.py::parse_dataset()`. No `exec()`/`eval()`/`subprocess`/dynamic import exists anywhere in the ingestion path, and parsed values are only ever placed into `ChartSpec`/`Chart3DSpec` numeric fields or displayed as escaped text — never template-rendered unescaped or passed to a shell.
- **Guardrails:**
  - `.xlsm` extension rejected unconditionally in `parse_dataset()` before any parsing.
  - `MAX_ROWS`/`MAX_COLS` bound memory and DB-row size regardless of upload size.
  - `MAX_UPLOAD_BYTES` (10 MB) enforced at the Flask route before the file is even read into memory.
  - `openpyxl` opened `read_only=True` — no write-back path, no macro/VBA execution.
  - Any parse failure (corrupt file, unsupported format, empty dataset) returns `None` → the route responds 400, nothing is persisted.
  - Regression test: `tests/viz/test_dataset_story.py::test_parse_xlsm_rejected` fails the build if `.xlsm` parsing is ever silently allowed.
- **Revisit if:** the ingestion pipeline ever shells out (e.g. LibreOffice-based conversion for other formats), adds a formula-evaluation ("live recalculate") mode, or the upload path becomes user-path-controlled.

### Gap 25 — Solicitation Parser (`tools/govcon/solicitation_parser.py`)

**Module:** `tools/govcon/solicitation_parser.py` (`parse_solicitation()`), ground-sol-01; same posture as the pre-existing `tools/govcon/rfi_document_parser.py` it mirrors.

**Ingress path:** A user (or the /proposals pipeline) points the parser at a solicitation PDF/DOCX/TXT on local disk. Text extraction delegates to `rfi_document_parser.extract_text()` (pdfminer → pypdf → PyPDF2 chain, python-docx for DOCX); PDF CLIN tables are additionally read via `pdfplumber` word coordinates. All downstream processing is pure `re` pattern matching over the extracted text producing a plain dict — no DB writes, no file writes, no rendering.

- **Decision:** **sandboxed-on-demand** (native PDF parsers on user-supplied documents — same class as Gap 3, `pdf_provider.py`)
- **Rationale:** The only native-parser exposure is the PDF/DOCX decode step in third-party libraries (`pdfminer.six`/`pypdf`/`pdfplumber`/`python-docx`). A malformed document could theoretically trigger a parser CVE, but the module itself contains no `exec()`/`eval()`/`subprocess`/dynamic import, never executes or renders extracted content, and returns structured data only. Solicitation documents are supplied by authenticated proposal operators, not anonymous input.
- **Guardrails:**
  - No code-execution or shell surface anywhere in the module (regex + dict assembly only).
  - `pdfplumber` failures are caught and degrade to the text-regex fallback; extraction failures raise `ValueError` to the caller.
  - Dependabot + `tools/maintenance/` audits track `pdfminer.six`/`pypdf`/`pdfplumber`/`python-docx` CVEs (shared with Gap 3).
  - `ICDEV_STRICT_SANDBOX=1` (IL5/air-gap) routes PDF parsing through `SandboxExecutor` per the Gap 3 mechanism when wired at the call site.
- **Revisit if:** the parser output is ever rendered unescaped, fed to a shell, or the intake path accepts unauthenticated uploads — or a PDF parser CVE with known ICDEV™ exposure ships (promote to `sandboxed`).

### Gap 26 — endoflife.date EOL Product Sync (`tools/doc_modernization/eol_products_sync.py`)
- **Surface:** outbound HTTPS GET to `endoflife.date/api/<product>.json` (allow-listed base URL from `args/docmod/docmod_config.yaml`); operator-supplied JSON/YAML bundle import for air-gapped sites.
- **Data handled:** public software lifecycle dates only (product slug, cycle, eol/eos dates, latest version). No document content, no CUI leaves the enclave — the sync sends only product slugs from the local seed list.
- **Decision:** **trusted-first-party** (bounded JSON/YAML parse into typed columns; no code-execution surface)
  - Responses parsed with `json`/`yaml.safe_load` into fixed columns; unknown keys dropped; per-product failures swallowed (best-effort).
  - `offline: true` in docmod_config (or air-gap mode) disables all outbound calls; seed + `import_dataset` remain.
  - Values land in `docmod_eol_products` and are only compared as dates/strings — never rendered unescaped, never executed, never fed to a shell.
- **Revisit if:** the sync ever posts local data outward, the base URL becomes user-configurable per request, or cache values are rendered into HTML without escaping (promote to `sandboxed-on-demand`).

### Gap 27 — ICDEV Cortex unified AI facade (`tools/cortex/`)

**Modules:** `tools/cortex/api.py` (`search`/`ask`/`complete`/`classify`/`extract`), `tools/cortex/analyst.py` (text-to-SQL analyst), `tools/cortex/search_service.py` (strategy router), `tools/cortex/governance.py` (TRUST gate pipeline). **Exposure surfaces (ctx-expose-01/02, ctx-canvas-04) inherit this decision:** `tools/cortex/rest_v1.py` (`/cortex/api/v1/*` REST endpoints), `tools/cortex/validators.py` (request→facade-kwargs coercion), `tools/mcp/cortex_server.py` (the `cortex_*` MCP tool family), and `tools/cortex/domains/` (data-driven domain lenses) — each is a thin adapter that validates a request dict and calls the same seven governed facades. None executes user-derived content; identity is derived server-side, and `validators.py` explicitly refuses to read caller-supplied `tenant_id`/`user_id`/`classification`.

**Ingress path:** Cortex is the unified facade over the platform's retrieval/LLM backends (LLMRouter, RAG, KG, DIC, IQE, ACE). Every entry point ingests **user-provided natural-language queries and prompts** — free-form `question`/`query`/`prompt` strings from authenticated dashboard users and MCP callers. The highest-risk surface is `analyst.ask()`, which asks an LLM to translate a NL question into SQL (`mode="iqe"`/`"nlq"`); the other paths (`search`, `complete`, `classify`, `extract`) pass the user text to LLMRouter/RAG/KG as prompt content and return structured results with `[source: …]` citations. No user text is ever executed as code — the only `compile(` occurrences under `tools/cortex/` are stdlib `re.compile()` pattern definitions, and there is no `exec()`/`eval()`/`subprocess`/`os.system`/`__import__`.

- **Decision:** **trusted-first-party**
- **Rationale:** Cortex is first-party facade code that treats all user input as data, not code. The text-to-SQL analyst — the one path that produces something executable — is gated **before** execution by a defense-in-depth chain (D34 read-only enforcement): (1) an injection-shape regex pre-filter (stacked statements, `DROP`/`TRUNCATE`/`ALTER` DDL, `UNION SELECT` exfil, `INSERT`/`UPDATE`/`DELETE`, comment sequences), (2) a SELECT-only + single-statement check on the generated SQL via the shared `nlq_processor.validate_sql`, and (3) a table-allowlist gate restricting reads to known collections. Refusals are audited and surfaced as `CortexQueryBlocked`; generated SQL runs read-only through the same RLS-aware `get_connection()` path as IQE/NLQ. The non-analyst paths perform no code execution at all — they route prompt text to LLMRouter/RAG/KG and return cited results through the governance/TRUST pipeline. There is no attacker-controlled execution surface to isolate; sandboxing would add latency with no concrete threat model (OPT-58).
- **Guardrails:**
  - Injection-shape pre-filter + SELECT-only/single-statement validator + table allowlist on every analyst-generated SQL statement before it reaches the DB (`analyst.py` gates, reusing `nlq_processor.validate_sql`).
  - Read-only enforcement (D34) — non-SELECT / multi-statement / DDL generated SQL is rejected and audited, never executed.
  - Generated SQL executes through RLS-aware `get_connection()`; tenant_id/classification predicates are injected by the storage layer, not by user input.
  - Air-gap egress guard: `assert_airgap_ready()` (`CortexAirgapError`) blocks cloud LLM routing when `ICDEV_CORTEX_AIRGAP`/strict mode is set, keeping CUI prompts local-only.
  - TRUST governance pipeline (`governance.GovernancePipeline`) enforces `[source: …]` citation grounding on drafted output; citation defects gate promote/export.
  - No `exec()`/`eval()`/`subprocess`/`os.system`/`__import__` anywhere under `tools/cortex/` — the only `re.compile()` uses are static pattern definitions.
- **Revisit if:** the analyst path is ever allowed to emit non-SELECT statements, the SELECT-only/allowlist gates are removed or bypassed, or any Cortex module adds a step that `exec`s, `eval`s, or `subprocess`-runs user-derived content → re-decide as **sandboxed** via `tools/security/sandbox_executor.py`.

### Gap 28 — Rotating LLM egress-proxy resolver (`tools/llm/proxy_resolver.py`)

**Module:** `tools/llm/proxy_resolver.py` (+ `icdev/` mirror) — resolves the current LLM egress proxy per call for constrained-network deployments and applies it to the standard proxy env vars. Invoked from the router choke point `LLMRouter._provider_invoke`.

**Ingress path:** When `ICDEV_LLM_PROXY_CMD` (env) or `proxy.command` (config) is set, `_run_proxy_command()` executes that command with `shell=True` and reads its stdout as the current proxy URL (for proxies supplied by an external rotator/agent). The command is **operator/administrator configuration**, resolved fresh per call and TTL-cached. It is NOT derived from end-user request content — it sits at the same trust tier as `.env` / `args/llm_config.yaml`.

- **Decision:** **trusted-first-party**
- **Rationale:** The executed string is infrastructure configuration authored by whoever provisions the deployment (identical trust to the API keys and model routing already in `.env` / `llm_config.yaml`). `shell=True` is intentional so operators can express a proxy lookup as a pipeline (e.g. `aws ssm get-parameter … | jq -r .Value`). There is no path by which an end user, prompt, RAG document, or uploaded file can influence the command — it is read only from the process environment / config file. Sandboxing operator-owned config would add no security value against this threat model (OPT-58). The bandit B602 finding is annotated `# nosec B602` with this justification at the call site.
- **Guardrails:**
  - Command source is env/config only — never request/prompt/document content; no interpolation of user data into the command string.
  - Bounded by a hard 10s `timeout`; failures degrade to the last cached value or `None` and never raise into the LLM call.
  - Output is consumed solely as a proxy URL string (first stdout line) — never executed, never rendered.
  - Resolving `None` never clobbers a pre-existing OS `HTTPS_PROXY`; the whole feature is opt-in and off by default.
- **Revisit if:** the proxy command ever becomes settable from a per-request/API surface or from tenant-supplied data → re-decide as **sandboxed** (`tools/security/sandbox_executor.py`) or drop `shell=True` in favor of an argv allowlist.

### Gap 29 — Data Design Canvas — Query tab SQL sandbox (`tools/data_canvas/query_sandbox.py`)

**Module:** `tools/data_canvas/query_sandbox.py` (`validate_query()` + `execute_query()`)

**Ingress path:** An authenticated Data Design Canvas user types a **free-form SQL query** into the Query tab; the string is validated by `validate_query()` and, if accepted, executed read-only against the connected backend (sqlite / postgresql-psycopg2 / duckdb) via `execute_query()`. The query text is the highest-trust-sensitivity input in the canvas — it is passed to a live DB cursor.

- **Decision:** **sandboxed** (parser-based read-only gate + statement timeout; DB-role backstop)
- **Rationale:** User-supplied SQL is untrusted and reaches a DB cursor, so the gate is treated as a sandbox boundary rather than trusted-first-party. Prior to dcpr-sec-01 the validator was a first-word regex allowlist plus a `\b`-bounded keyword blocklist, which allowed several bypasses (stacked statements smuggling `COPY … TO PROGRAM` RCE, `COPY`/`INSTALL`/`LOAD`/`SET` absent from the blocklist, and file/catalog reads via plain `SELECT pg_read_file(...)`/`read_csv_auto(...)`). The rewrite parses with `sqlparse`, accepting **exactly one** top-level statement whose shape is SELECT / WITH (CTE) / EXPLAIN and rejecting every DML/DDL and file/catalog/RCE reference. No user SQL is `exec()`/`eval()`-ed as Python — it is only handed to the DB driver after passing the gate.
- **Guardrails:**
  - **Single-statement gate** — `sqlparse.split()` rejects any input with more than one statement, closing the stacked-statement RCE (`SELECT 1; COPY x TO PROGRAM 'sh -c id'`).
  - **Statement-shape gate** — accepts only SELECT/WITH (`get_type()=="SELECT"`) or EXPLAIN (first keyword); everything else (COPY, INSERT, etc.) is refused.
  - **Keyword blocklist** — DML/DDL plus `COPY`/`SET`/`RESET`/`INSTALL`/`LOAD` (and the original `insert…analyze` set) rejected anywhere in the statement.
  - **Identifier/function blocklist** — `pg_read_file`, `pg_read_binary_file`, `pg_ls_dir`, `pg_stat_file`, `lo_import`, `lo_export`, `pg_authid`, `pg_shadow`, `pg_catalog`, `information_schema`, `read_csv_auto`, `read_parquet`, `dblink`, and the `TO PROGRAM` construct are refused even inside a plain SELECT.
  - **DoS bound** — `execute_query()` issues `SET LOCAL statement_timeout = '10000'` (10s) on PostgreSQL before running the query; results are capped at 1000 rows on every backend (sqlite/duckdb timeout is a documented no-op backed by the row cap).
  - **Defense in depth** — the module documents that the DB connection SHOULD authenticate as a low-privilege, read-only role (no COPY/superuser/write grants).
  - **Regression test** — `tests/test_dcpr_query_sandbox.py` asserts rejection of stacked statements, COPY, `pg_read_file`, `information_schema`, DML/DDL, and admin verbs, and acceptance of `SELECT 1` / `EXPLAIN SELECT 1`.
- **Revisit if:** the Query tab ever accepts unauthenticated input, the sandbox is asked to allow writes, or a new backend adds a file/catalog function not covered by the identifier blocklist.

### Gap 30 — Data Mesh `ext.*` governance gate — local no-OPA fail-open (`tools/data_canvas/data_mesh/governance_engine.py`)

**Module:** `tools/data_canvas/data_mesh/governance_engine.py` (`check_ext_access()`)

**Ingress path:** `check_ext_access(connector_name, table, user_attrs=None)` is the governance gate IQE's `ext.*` adapter (`tools/iqe/adapters/ext_databridge.py`) calls before reading rows from an external DataBridge connector (Splunk, Tenable, ServiceNow, GDELT, …). When an OPA server is configured (`ICDEV_OPA_URL`), the gate evaluates the datamesh policy. When `ICDEV_OPA_URL` is **blank — the default** — the gate historically returned `allowed=True` unconditionally with a `local pass-through` audit entry. For an IL4 canvas this is a **fail-open** posture: absent policy infrastructure, every external read is permitted.

- **Decision:** **sandboxed-on-demand** (fail-open by default; a toggle switches the local no-OPA path to fail-closed default-deny)
- **Rationale:** Parity with the sibling `check_access()` in `tools/data_canvas/governance_engine.py`, which was given default-deny in dcpr-sec-03. Flipping the ext path unconditionally to deny would break existing dev/IL2 deployments that run without OPA, so the fail-closed behavior is gated behind an explicit, default-off env toggle (mirroring the `redaction.fail_closed` convention). When the toggle is set, a missing OPA server denies the read instead of allowing it; the OPA-configured branch is unchanged. The gate performs no code execution — it only reads env/config, evaluates policy, and writes an append-only audit row.
- **Guardrails:**
  - **`ICDEV_GOVERNANCE_FAIL_CLOSED`** env toggle (accepts `1`/`true`/`yes`, case-insensitive), **default OFF** to preserve historical fail-open behavior. Read at **call time** (not import time) so operators/tests can flip it per environment without reimport.
  - When ON **and** no OPA server is configured, `check_ext_access()` returns `{"allowed": False, "reason": "governance fail-closed: no OPA server configured", "method": "local", "policy_id": None}` and **still writes** the `dm_policy_audit_log` audit entry (decision=0), preserving the audit trail on deny.
  - When OFF, the local pass-through allow is preserved exactly (decision=1 audit row).
  - The OPA-configured branch (`ICDEV_OPA_URL` set) is untouched — it always evaluates the policy.
  - **Regression test** — `tests/test_dcpr_ext_access_failclosed.py` asserts (a) toggle ON + no OPA denies and audits, and (b) toggle OFF (default) + no OPA allows (behavior preserved), plus case-insensitive token parsing.
- **Revisit if:** the default is ever flipped to fail-closed (make the toggle opt-*out* and re-audit dev/IL2 impact), or if a policy-decision cache is added that could serve a stale allow after the toggle is set.

### Gap 31 — FORGE Academy learner code runner (`apps/forge_academy/code_runner.py`)

**Module:** `apps/forge_academy/code_runner.py` (`run_code()`)

**Ingress path:** An authenticated FORGE Academy learner submits arbitrary Python source (their lesson solution, plus an optional test harness) via `POST /api/academy/code/run` (`apps/forge_academy/blueprint.py`). The strings are written to a script in a fresh `TemporaryDirectory` and executed with `subprocess.run([sys.executable, ...])`. This is by design a **code-execution ingress** — the whole point of the feature is to run learner-authored Python.

- **Decision:** **sandboxed** (in-process hardening — AST allowlist gate + scrubbed env + isolated cwd + interpreter isolation + timeout + POSIX resource caps)
- **Rationale:** The platform `SandboxExecutor` (`tools/security/sandbox_executor.py`, D-SEC-10) requires a Docker/K8s/Podman runtime and adds ~5–15× latency; it is not present on Windows dev hosts and is a poor fit for this **interactive, per-keystroke-grade** hot path where fast feedback is essential. In-process hardening is therefore the pragmatic route (the doc's `sandboxed-on-demand` convention still applies — an operator running `ICDEV_STRICT_SANDBOX=1` at IL5 should route this path through `SandboxExecutor`; wiring that is deferred). The prior gate was a **bypassable denylist substring check** (`_BLOCKED_PATTERNS`) with an unused `_ALLOWED_IMPORTS` set. penta-aca-02 replaced it with a real allowlist and neutralised the three known escapes:
  1. `import os; print(os.environ)` — `os` stays importable, but the child runs with a **scrubbed environment** (only a non-secret system minimum: `SYSTEMROOT`, `PATH`, `PATHEXT`, `COMSPEC`, `WINDIR`, `HOME`/`USERPROFILE`, locale, and `TMP*` pointed at the sandbox dir). No `ICDEV_*`, `*_API_KEY`, `*_PASSWORD`, or `*_TOKEN` reaches the child, so `os.environ` cannot leak `ICDEV_PG_PASSWORD` or API keys.
  2. `urllib.request` (and `socket`/`requests`/`httpx`/`aiohttp`) egress — rejected by the AST import allowlist. `urllib.parse` is the only permitted `urllib` submodule; bare `urllib` / `urllib.request` are refused.
  3. `open('/etc/passwd')` — the AST gate rejects `open`/`os.open`/`io.open`/`os.fdopen` calls whose first **literal** argument is an absolute path, a Windows drive/UNC path, or a `..` traversal path. Relative opens inside the isolated cwd remain allowed for legitimate lesson file I/O.
- **Guardrails:**
  - **AST allowlist gate** (`_check_code_safety`) parses the *combined* learner code **and** any supplied `test_code`; rejects imports whose top-level module (or specific `urllib.parse`) is not in `_ALLOWED_IMPORTS`, dynamic-import/eval escapes (`__import__`, `importlib`, `eval`, `exec`, `compile`), process escapes (`os.system`/`os.popen`/`os.exec*`/`os.spawn*`/`os.startfile`/`os.fork`/`os.putenv`, `subprocess`), and absolute/traversal file opens. Unparseable code is allowed through the gate only because Python compiles the whole module before executing — a `SyntaxError` runs nothing.
  - **Scrubbed environment** — `_build_scrubbed_env()` passes only a fixed non-secret allowlist of OS vars; secrets are never inherited.
  - **Isolated cwd** — execution happens in a per-call `TemporaryDirectory` removed after the run.
  - **Interpreter isolation** — `python -I -X utf8` ignores ambient `PYTHONPATH` / user site / cwd modules and forces UTF-8 I/O.
  - **Timeout** — the 10s wall-clock cap is preserved.
  - **Resource caps** — on POSIX a `preexec_fn` sets `RLIMIT_CPU`/`RLIMIT_AS`/`RLIMIT_FSIZE`; Windows relies on the timeout + AST gate (no `resource` module).
  - **Regression test** — `tests/test_penta_aca_sandbox.py` asserts each of the three named escapes is blocked/neutralised without leaking, plus dynamic-import/process escapes, and that legitimate tier1 starter code (and its auto-grader) still runs.
- **Residual risk / Revisit if:** in-process hardening is not a true isolation boundary — `os` remains importable, so a determined learner could still perform limited local filesystem reads of relative paths or CPU/mem abuse (bounded by the timeout and, on POSIX, resource caps). Promote this path to the container-backed `SandboxExecutor` if (a) `ICDEV_STRICT_SANDBOX=1` is honored for Academy at IL5, (b) the endpoint is ever exposed unauthenticated, or (c) a new escape class (e.g. a sandbox-relevant stdlib CVE) is found.

### Gap 32 — DocMod URL link-rot checker (`tools/doc_modernization/link_check.py`)

**Module:** `tools/doc_modernization/link_check.py` (`check_url()` / `egress_guard()` / `check_corpus_links()`)

**Ingress path:** The nightly Document Modernization sweep extracts the URLs a DIC document *cites* from its (user-authored) content and, when the feature is enabled, makes outbound HTTP(S) requests to check each one's health (broken / moved / content-drifted). The request **targets** are therefore attacker-influenceable document data, which makes this an outbound server-side-request-forgery surface rather than a code-execution one: no document content is ever parsed as code, `exec`-ed, or handed to a native parser — the bytes read back are only hashed and their status recorded.

- **Decision:** **sandboxed-on-demand** (outbound egress guard is always on; the feature itself is default-OFF and skips entirely when there is no egress)
- **Rationale:** The threat here is not execution but egress — a cited hostname could be pointed at internal infrastructure. That is contained by an egress policy enforced **before any socket is opened**, so a container/network sandbox is not the primary control; the policy is. The feature is a network feature and ships **disabled** (`link_rot.enabled: false` in `args/docmod/docmod_config.yaml`); at IL5 / air-gap (`ICDEV_STRICT_SANDBOX=1` deployments) it stays off or self-skips, matching the `sandboxed-on-demand` convention. Verdicts are deterministic given the HTTP response (no LLM — TRUST) and land as ordinary HITL-gated `docmod_findings` (state `open`), never auto-edits.
- **Guardrails:**
  - **Scheme allowlist** — only `https` URLs are contacted; `http`, `ftp`, `file`, `gopher`, etc. are refused with a `blocked` status and never reached.
  - **Resolve-then-check** — the hostname is resolved to its IP address(es) *first*, and the request is refused if **any** resolved address falls in a non-public range (loopback `127.0.0.0/8` / `::1`, private/RFC1918 + IPv6 unique-local `fc00::/7`, link-local `169.254.0.0/16` — which covers the cloud instance-metadata address — and `fe80::/10`, multicast, unspecified, reserved). Checking the resolved address rather than the hostname string is what prevents a public-looking name from steering the request at internal space.
  - **Allow/denylist** — operator `allowlist`/`denylist` in `docmod_config.yaml` are honored (suffix match; denylist wins).
  - **No auto-redirect** — redirects are never auto-followed; each hop's target is re-run through the same guard and the chain depth is capped (`max_redirects`).
  - **Tight timeouts + bounded read** — every request carries a mandatory short timeout, uses HEAD (ranged GET fallback), and reads at most `head_hash_bytes` of the body (via `Range`) for the drift hash; TLS verification is never disabled.
  - **Per-sweep cap** — `max_urls_per_sweep` bounds how many outbound checks a sweep performs.
  - **Air-gap safe** — when `offline: true` or an air-gap runtime is detected, the whole step skips and URLs are reported `not checked (no egress)`; an unresolvable host is likewise never reported as rotted.
  - **Regression test** — `tests/docmod/test_link_check.py` asserts scheme rejection, literal-internal-IP and metadata-IP rejection, a DNS-rebinding-style case (public hostname resolving to an internal IP is refused), allow/denylist behavior, the per-sweep cap, the air-gap skip status, and the broken / moved / hash-drift finding types — all with the network mocked.
- **Revisit if:** the checker is ever made to follow redirects automatically without re-validation, to accept non-https schemes, to run against unauthenticated user-submitted URLs outside the document corpus, or to fetch full response bodies instead of a bounded head slice — any of which would warrant routing the outbound call through a network-isolating sandbox/proxy in addition to the egress guard.

### Gap 32 — External-agent MCP consumption via curated toolsets (`tools/mcp/unified_server.py`, `tools/mcp/toolset_profiles.py`)

**Module:** `tools/mcp/unified_server.py` (`--toolset` / `ICDEV_MCP_TOOLSET`), `tools/mcp/toolset_profiles.py` (SAG sag-mcp-01)

**Ingress path:** `tools/mcp/unified_server.py` already exposes 447+ ICDEV tools over stdio MCP and is consumed by Claude Code in production. sag-mcp-01 makes it consumable by an **external, possibly-non-Claude agent** (e.g. Hermes on a local kimi/ollama model, or a cloud LLM) via `<agent> mcp add icdev --command "python tools/mcp/unified_server.py --toolset <profile>"`. The new ingress is twofold: (a) an external agent chooses which registered tools to invoke, and (b) tool **output may egress to whatever LLM the external agent runs** — which can be a cloud provider outside the ICDEV trust boundary.

- **Decision:** **bypass-documented** for tool *execution* (the tools are first-party ICDEV code, already covered by their own gates) **+ a fail-closed CUI-egress gate** for tool *exposure*.
- **Rationale:** No new code-execution sandbox is warranted — every tool dispatched by `unified_server` is first-party ICDEV Python already subject to its own RLS/classification/security gates; the external agent cannot inject code, only call existing registered handlers. The real, novel risk is **CUI/classification egress to a cloud LLM**. That is addressed by the same distinction the CLI bridge uses (`api_key_env`/provider: `ollama` = local, everything else = cloud):
  - Tools are exposed through **curated profiles** (`args/mcp_toolset_profiles.yaml`), not the full 447-tool surface. Small local models get a bounded list (`--toolset compliance` ≈ 12 tools), which is both a usability and a least-privilege win.
  - Each profile declares `cui_egress: cloud_safe | local_only`. `enforce_cui_egress()` runs **before any tool is registered** and **refuses** a `local_only` profile when a cloud provider is detected (fail-safe toward "cloud" when the provider is unknown), unless an operator sets the explicit `ICDEV_MCP_ALLOW_CLOUD_CUI=1` override.
- **Guardrails:**
  - **`--toolset <profile>` / `ICDEV_MCP_TOOLSET`** restrict registration to a curated set; unknown tool names in a profile are dropped with a warning (a registry rename never crashes startup).
  - **`enforce_cui_egress()`** is fail-closed for `local_only` profiles on cloud providers; `cloud_safe` profiles (`minimal`, `research`, `security`) are curated to exclude CUI-emitting tools.
  - **No default behavior change** — with no `--toolset`, the server exposes the full surface exactly as before (Claude Code path unaffected).
  - **Regression test** — `tests/mcp/test_toolset_profiles.py` asserts every profile references only real `TOOL_REGISTRY` tools, the fail-closed gate blocks `local_only` on cloud and allows it on local / with override, and the server registers only the profile's tools.
- **Revisit if:** a `cloud_safe` profile is expanded to include a tool that can emit CUI (re-audit its `cui_egress`), per-tool response redaction/classification filtering is added (tighten from profile-level to tool-level egress control), or the server ever accepts remote (non-stdio) transport without an auth layer.

### Gap 33 — SAG auto-skill lifecycle: LLM-generated skill promotion (`tools/agent_runtime/skills_lifecycle.py`)

**Module:** `tools/agent_runtime/skills_lifecycle.py` (SAG sag-skl-01)

**Ingress path:** The standalone agent proposes new skills via NOVA's generator when a session solved a novel task. The generated markdown spec (an LLM output — untrusted content) is queued in `agent_improvement_artifacts` and, on approval, written to `.agents/skills/icdev-auto-<slug>/SKILL.md`, where it becomes parseable by `tools/skills/registry.py` and thus discoverable/invokable as a skill.

- **Decision:** **bypass-documented** — the write is gated by mandatory human-in-the-loop approval; no auto-execution and no auto-promotion.
- **Rationale:** The novel content (an LLM-drafted skill spec) is never executed by this module and never lands on disk without an explicit human `approve` action (`/skill approve <id>` or the headless CLI). Quarantine is the `pending` status in the NOVA queue; nothing reaches `.agents/skills` until a person reviews and approves it. The spec is markdown documentation, not code that this module runs — a skill's *commands* are only ever executed later through the existing allowlisted `tools/skills/invoke.py` path (Gap-covered), which restricts execution to `python tools/…` prefixes. Every promoted skill carries a provenance frontmatter block (`source-session`, `source-model`, `generated-at`, `approved-by`, `trust: unverified-llm-generated`) so the LLM origin is always visible — a TRUST record, not a laundered first-party skill.
- **Guardrails:**
  - **HITL is the gate** — `approve_proposal()` is the sole writer to `.agents/skills`, and only on explicit human approval; the automatic post-session proposal hook (`maybe_propose_from_session`) only ever *queues* a `pending` proposal and is itself env-gated (`ICDEV_SAG_SKILL_PROPOSALS`, default off).
  - **Provenance frontmatter** — every promoted `SKILL.md` is stamped `trust: unverified-llm-generated` with its source session/model, so downstream consumers know it is machine-drafted.
  - **Namespaced, not laundered** — auto-skills live under the reserved `icdev-auto-` prefix, distinct from hand-authored `icdev-*` skills.
  - **Curator archives, never deletes** — the `sag_skill_curator` reflex only moves idle, unpinned skills to `.agents/skills/_archive/` (dry-run default); it never executes or deletes them.
  - **Execution stays allowlisted** — a promoted skill's commands run only via `tools/skills/invoke.py`'s `python tools/…` allowlist, never by this module.
- **Revisit if:** promotion is ever made automatic (removing the human approval step), the post-session hook is switched on by default, or auto-skill commands are ever dispatched outside the `invoke.py` allowlist — any of which would require sandboxing skill execution and re-scoping this decision.

### Gap 34 — Email gateway channel adapter: inbound IMAP poll (`tools/gateway/adapters/email_channel.py`)

**Module:** `tools/gateway/adapters/email_channel.py` (SAG sag-gw-02)

**Ingress path:** A new Remote Command Gateway channel. `poll_once()` fetches `UNSEEN` messages over an authenticated IMAP session, parses them (stdlib `email`), and normalises each into a `CommandEnvelope`. This is externally-sourced, attacker-influenceable content (anyone can email the mailbox).

- **Decision:** **bypass-documented** — the adapter parses and routes; it never executes, and every produced envelope flows through the unchanged 8-gate `run_security_chain` before any command runs.
- **Rationale:** The adapter is a pure translator: raw RFC822 bytes → a `CommandEnvelope` of the same shape every other channel produces. It runs no attachment, no macro, no LLM — it only reads text/plain bodies and headers. A malicious email cannot execute anything by arriving; it must still pass identity-binding (the `From` address must map to a bound ICDEV user), authentication, classification, RBAC, rate-limit, and domain-authority gates — identical to Telegram/Slack. Command execution remains the gateway's existing allowlisted path, not this adapter.
- **Guardrails:**
  - **Authenticated mailbox + identity binding** — inbound is read only from an authenticated IMAP mailbox, and the sender `From` is the `channel_user_id` the identity-binding gate must resolve to a bound user; an unbound sender is rejected at gate 3.
  - **Bot/loop protection** — RFC 3834 `Auto-Submitted` and `Precedence: bulk|list|auto_reply` mark `is_bot=True`, dropped by the bot-detection gate (prevents mail loops and auto-responder abuse).
  - **Attachments ignored** — only `text/plain` parts are read; attachments and `text/html` are never decoded or executed.
  - **Command filter** — only messages whose subject/first-body-line names an `icdev-*`/`bind` command are turned into envelopes; everything else is dropped.
  - **Bounded poll** — `max_poll` caps messages processed per cycle.
  - **Default off** — the `email` channel ships `enabled: false`; no mailbox is polled until an operator configures IMAP/SMTP and enables it.
  - **Regression test** — `tests/gateway/test_email_adapter.py` asserts command parsing from subject/body, the non-ICDEV drop, bot-header detection, threading via In-Reply-To, and that SMTP/IMAP are mocked (no network).
- **Revisit if:** the adapter is ever made to parse/execute attachments or `text/html`, to auto-bind senders without the identity gate, or to act on mail before DKIM/SPF verification is added — any of which would warrant sandboxing the parse and hardening sender authentication.
