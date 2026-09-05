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

### Gap 18 — SBOM dependency resolution (`tools/compliance/dependency_resolver.py`)

**Modules:** `tools/compliance/dependency_resolver.py` (mirrored at `icdev/tools/compliance/dependency_resolver.py`), consumed by `tools/compliance/sbom_generator.py`

**Ingress path:** To satisfy the SBOM 2026 **Coverage** element (all transitive dependencies, no minimum depth — sbx-cov-01), the resolver reads a target project's **lockfiles and installed package metadata** from `projects.directory_path`: `uv.lock`, `poetry.lock`, `pdm.lock`, `Pipfile.lock`, `*.dist-info/METADATA`, `package-lock.json`, `yarn.lock` (v1 text and Berry YAML), `go.mod`, `go.sum`, `Cargo.lock`, `mvn dependency:list` output, `gradle.lockfile`, `obj/project.assets.json`, `packages.lock.json`. These files originate with whoever authored the target project, so they are **not** first-party content.

- **Decision:** **bypass-documented** (parse-only; no execution path exists)
- **Rationale:** The module parses and never evaluates. Its entire toolkit is `json.loads`, `tomllib`/`tomli` (a non-executing parser), `yaml.safe_load`, `re`, and `importlib.metadata.PathDistribution`. It contains **no** `subprocess`, `os.system`, `exec`, `eval`, `__import__`, or `pickle` — this is a deliberate design constraint, not an accident: resolution is **offline-first** precisely so it never shells out to `npm`, `mvn`, `gradle`, `go` or `dotnet`, which is what makes it usable in an air-gapped enclave and simultaneously removes the arbitrary-execution surface that invoking a package manager would create. `PathDistribution` reads `*.dist-info/METADATA` as text with `email.parser`; it does **not** import the distribution, so no third-party code in the target environment runs. Every parser is wrapped so a malformed or hostile lockfile degrades that ecosystem to `declared-only` with a stated reason rather than raising — a crafted file can suppress its own resolution, but suppression is *reported in the SBOM's coverage statement*, not silent.
- **Guardrails:**
  - `yaml.safe_load` only. `yaml.load` instantiates arbitrary Python objects and is banned here.
  - Per-ecosystem resolution runs inside a `try/except` in `resolve_project`; an exception becomes an `incomplete` coverage entry naming the exception type, so a bad lockfile cannot abort SBOM generation or be mistaken for "this ecosystem has no dependencies".
  - `tests/test_sbom_coverage_resolution.py::test_resolver_never_executes_what_it_parses` asserts the absence of every execution primitive in **both** the root copy and the `icdev/` mirror, so the bypass cannot silently lapse.
  - `tests/test_sbom_coverage_resolution.py::test_a_corrupt_lockfile_degrades_rather_than_aborting_the_sbom` pins the fail-open-but-loud behaviour.
- **Revisit if:** the resolver ever shells out to a package manager to obtain a resolved set (`mvn dependency:list`, `gradle dependencies`, `go list -m all`, `npm ls`, `dotnet restore`) — that would be a real execution path over attacker-influenced project content and must be re-decided as **sandboxed**; or if it gains a plugin/entry-point mechanism that imports code from the target environment rather than reading its metadata.

### Gap 19 — SBOM author signature verification (`tools/compliance/sbom_signer.py`)

**Modules:** `tools/compliance/sbom_signer.py` (mirrored at `icdev/tools/compliance/sbom_signer.py`), consumed by `tools/compliance/sbom_generator.py`

**Ingress path:** Signing only ever reads an SBOM this tree just produced, so it is first-party. **Verification is not.** `verify_sbom` is the consumer-side entry point: it reads an SBOM document and a detached `<sbom>.sig.json` that arrive from whoever is claiming to have signed them, and the signature file contains a **PEM public key supplied by that same party**. The attacker-controlled surface is therefore two JSON documents plus a PEM blob handed to `cryptography`'s `load_pem_public_key`.

- **Decision:** **bypass-documented** (parse-and-verify only; no execution path exists)
- **Rationale:** The module parses and verifies; it never evaluates. Its whole toolkit is `json.loads`, `hashlib`, `pathlib`, and the `cryptography` library's PEM loader and signature verifier. It contains no `subprocess`, `os.system`, `exec`, `eval`, `__import__`, `pickle` or `yaml.load`, and — unlike every other signing path in the industry — **no network client at all**: no sigstore, no Fulcio, no Rekor, no OCSP, no CRL fetch. That absence is a requirement, not an accident, because air-gapped verification is one of the card's acceptance criteria; it also removes the entire SSRF / hostile-response surface that a transparency-log lookup would introduce. `load_pem_public_key` on hostile bytes is a parse in a memory-safe Rust/OpenSSL boundary that returns a key object or raises; it does not deserialize Python objects the way `pickle` or `yaml.load` would.
- **Guardrails:**
  - Every load is wrapped: a malformed SBOM, a malformed signature file, a corrupt PEM, or a bad base64 blob returns `verified: False` with a stated reason. `verify_sbom` is documented never to raise on bad input, so a hostile artifact cannot abort a gate by throwing.
  - The algorithm is checked against a closed `APPROVED_ALGORITHMS` allowlist **before** any signature maths runs, so an attacker cannot select an algorithm the verifier merely happens to accept. HMAC-SHA256 is refused by name: it is a symmetric MAC, so anyone who can verify it can forge it.
  - Verification is **fail-closed by omission** — an SBOM with no signature file reports `verified: False`, never "nothing to check, so fine".
  - Integrity and authenticity are reported as two fields (`verified`, `trusted`), never one. The embedded public key can only establish that the document matches *some* signature; `trusted` is `True` only when the caller pinned a fingerprint obtained out of band. `tests/test_sbom_author_signature.py::test_a_tampered_sbom_resigned_by_another_key_fails_a_pinned_fingerprint` pins that distinction so it cannot be "simplified" away.
  - `tests/test_sbom_author_signature.py::test_the_signer_has_no_network_import` asserts the absence of every network and subprocess import in **both** the root copy and the `icdev/` mirror, so the offline property cannot silently lapse.
- **Revisit if:** the signer gains a transparency-log, OCSP, CRL, KMS or HSM-over-network lookup — that is a real network path over attacker-influenced material and must be re-decided; or if it ever accepts a key *path*, key *identifier* or algorithm name from the signature file and resolves it (rather than only PEM bytes it validates), which would turn parsed content into a resource reference.

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
- **Where the subprocess actually starts:** since hgx-exec-03 both this verifier and the
  primary build dispatch call `tools/agents/adapters/claude_cli.py`
  (`ClaudeCliAdapter.invoke` / `.spawn`) rather than each building their own command
  line. That module is the single review point for every property below — argv,
  `shell=False`, the `.tmp/` prompt file and its deletion, and the environment handed to
  the child.
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
  - **`POST /cortex/api/v1/agent` (hgx-cx-02) — the one Cortex endpoint that starts EXECUTION.** It does not weaken the decision above, and the guardrails are what keep that true: (a) it requires `cortex:agent`, which is deliberately absent from `DEFAULT_SCOPES` — a search key cannot reach it; (b) `tools` and `tool_handlers` are refused from the wire, so the single-agent loop runs with no tools at all and a remote caller can never name the capabilities its agent holds; (c) tool-bearing work is only reachable through `mode="graph"`, which runs a Studio workflow an **operator** authored, on the durable DAG runtime whose nodes carry their own per-node tool authorization (MCP-WF-001) and human gates; (d) `rubric` is refused, because grading a loop shells out to the server's own checkout; (e) `webhook_url` is refused (SSRF — a caller-named address the server reaches from inside). The goal text itself is still data: it is injection-screened and input-redacted by the `cortex.agent` governed facade before dispatch.
- **Revisit if:** the analyst path is ever allowed to emit non-SELECT statements, the SELECT-only/allowlist gates are removed or bypassed, `/agent` starts accepting `tools`/`tool_handlers`/`rubric`/`webhook_url` from the request body or `cortex:agent` enters the default grant, or any Cortex module adds a step that `exec`s, `eval`s, or `subprocess`-runs user-derived content → re-decide as **sandboxed** via `tools/security/sandbox_executor.py`.

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

**Ingress path:** An authenticated FORGE Academy learner submits arbitrary Python source (their lesson solution) via `POST /api/academy/code/run` or `POST /api/academy/step/submit` (`apps/forge_academy/blueprint.py`). It is concatenated with the step's test harness, written to a script in a fresh `TemporaryDirectory` and executed with `subprocess.run([sys.executable, ...])`. This is by design a **code-execution ingress** — the whole point of the feature is to run learner-authored Python.

**Narrowed by aca-int-02 (2026-07-30).** The request used to carry `test_code` as well, which `mission.html` helpfully posted back from the step payload — so the party being graded supplied half the executed program. Both routes now take a **`step_id`** and load the harness server-side from `fa_mission_steps.test_code_path` via `grading.run_step_code()` / `grading.grade_step()`; `grade_step` has no `test_code` parameter for a caller to pass, and `grading.client_safe_steps()` strips `test_code`/`test_code_path` from the page payload. The learner-controlled share of the executed script is therefore now **only their own solution body**, and the harness is a first-party authored asset under `apps/forge_academy/content/`. That is a strict reduction in attacker-controlled input to the sandbox; the decision and every guardrail below are unchanged.

- **Decision:** **sandboxed** (in-process hardening — AST allowlist gate + scrubbed env + isolated cwd + interpreter isolation + timeout + POSIX resource caps)
- **Rationale:** The platform `SandboxExecutor` (`tools/security/sandbox_executor.py`, D-SEC-10) requires a Docker/K8s/Podman runtime and adds ~5–15× latency; it is not present on Windows dev hosts and is a poor fit for this **interactive, per-keystroke-grade** hot path where fast feedback is essential. In-process hardening is therefore the pragmatic route (the doc's `sandboxed-on-demand` convention still applies — an operator running `ICDEV_STRICT_SANDBOX=1` at IL5 should route this path through `SandboxExecutor`; wiring that is deferred). The prior gate was a **bypassable denylist substring check** (`_BLOCKED_PATTERNS`) with an unused `_ALLOWED_IMPORTS` set. penta-aca-02 replaced it with a real allowlist and neutralised the three known escapes:
  1. `import os; print(os.environ)` — `os` stays importable, but the child runs with a **scrubbed environment** (only a non-secret system minimum: `SYSTEMROOT`, `PATH`, `PATHEXT`, `COMSPEC`, `WINDIR`, `HOME`/`USERPROFILE`, locale, and `TMP*` pointed at the sandbox dir). No `ICDEV_*`, `*_API_KEY`, `*_PASSWORD`, or `*_TOKEN` reaches the child, so `os.environ` cannot leak `ICDEV_PG_PASSWORD` or API keys.
  2. `urllib.request` (and `socket`/`requests`/`httpx`/`aiohttp`) egress — rejected by the AST import allowlist. `urllib.parse` is the only permitted `urllib` submodule; bare `urllib` / `urllib.request` are refused.
  3. `open('/etc/passwd')` — the AST gate rejects `open`/`os.open`/`io.open`/`os.fdopen` calls whose first **literal** argument is an absolute path, a Windows drive/UNC path, or a `..` traversal path. Relative opens inside the isolated cwd remain allowed for legitimate lesson file I/O.
- **Guardrails:**
  - **AST allowlist gate** (`_check_code_safety`) parses the *combined* learner code **and** the server-loaded `test_code` — note the consequence: a stored harness must itself satisfy the allowlist, and six that did not were permanently uncompletable until aca-vv-01 rewrote them; rejects imports whose top-level module (or specific `urllib.parse`) is not in `_ALLOWED_IMPORTS`, dynamic-import/eval escapes (`__import__`, `importlib`, `eval`, `exec`, `compile`), process escapes (`os.system`/`os.popen`/`os.exec*`/`os.spawn*`/`os.startfile`/`os.fork`/`os.putenv`, `subprocess`), and absolute/traversal file opens. Unparseable code is allowed through the gate only because Python compiles the whole module before executing — a `SyntaxError` runs nothing.
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

### Gap 35 — AGX benchmark suite (`tools/llm/architectures/benchmark.py`, `leaderboard.py`, `baseline.py`)

**Modules:** `tools/llm/architectures/benchmark.py`, `leaderboard.py`, `baseline.py` (AGX agx-bench-01/02).

**Ingress path:** The benchmark reads a first-party, checked-in task suite (`args/agx/benchmark_tasks.yaml`); the leaderboard reads the tool's own machine-generated report (`data/agx/benchmark_latest.json`); the `baseline` architecture issues a single `LLMRouter.invoke` on a benchmark task prompt. No end-user, attacker, or externally-sourced content enters any of these modules.

- **Decision:** **trusted-first-party**
- **Rationale:** All inputs are repo-authored data or the tool's own output. The modules perform no `exec()`/`eval()`/`subprocess`; they call the LLMRouter and the deterministic fitness judge and aggregate results in pure Python. There is no user-provided-content ingress to sandbox. CUI safety is preserved by routing all inference through `LLMRouter` (the api_key_env local-vs-cloud distinction keeps CUI local); the harness itself moves no CUI.
- **Revisit if:** the benchmark is ever pointed at a user-supplied or externally-fetched task corpus (rather than the first-party `args/agx/` suite), or the leaderboard is made to ingest reports from an untrusted source — either of which would introduce untrusted-content ingress and warrant re-scoping.

### Gap 36 — Agent browser scope controls (`tools/browser/scope.py`)

**Module:** `tools/browser/scope.py` (OSS adaptation oss-browse-02).

**Ingress path:** The guardrail seam an LLM-driven agent must use to drive a live WebDriver. Two untrusted inputs converge here: (a) **agent-authored action arguments** — URLs handed to `navigate()`, text handed to `type_text()`, scripts handed to `run_script()` — which are LLM output and therefore attacker-influenceable whenever the model reads untrusted content; and (b) **remote page content** rendered by the browser itself, which reaches the process via `current_url`, page text, and screenshots, and can carry prompt-injection back into the agent loop.

- **Decision:** **bypass-documented** — the browser process is not sandboxed; `scope.py` is the enforcement boundary, and it is default-deny.
- **Rationale:** Sandboxing the browser is the wrong seam: a WebDriver is *already* a separate OS process, and the risk is not code execution inside ICDEV but an agent reaching a host it should not, or leaking a credential into a page/transcript. Both are network-and-data problems, so the control is an allowlist plus a credential broker, not a container. Out of the box the agent can reach **loopback only** — the default policy denies every routable host even if the config file is missing, empty, or unparseable.
- **Guardrails:**
  - **Default-deny domain allowlist** — `check_navigation()` refuses any host not in `allowed_domains` (default `localhost`/`127.0.0.1`/`::1`). A routable host needs three independent switches: allowlisted **and** `allow_non_local: true` **and** cleared by `egress_guard` (https-only, DNS resolve-then-check against private/loopback/link-local ranges). A missing `egress_guard` import fails closed.
  - **Scheme allowlist** — only `http`/`https` are navigable. `javascript:`, `data:`, `file:` and `about:` are refused, which is what stops `navigate()` from doubling as a script-injection or local-file-read primitive.
  - **Post-action re-check** — `assert_in_scope()` re-validates `current_url` after every action and parks the session at `about:blank` before raising, so a redirect cannot walk the session out of scope.
  - **Credentials never reach the model** — the agent writes `<secret>name</secret>`; `SensitiveDataResolver` substitutes the value at the driver, sourced from the env var named in config, only after `tools/security/credential_broker.py` authorizes the request (fail-closed on denial or broker unavailability). The prompt, transcript, and audit row keep the placeholder, and `redact()` reverses any secret that surfaces in persisted text. No new secret store is introduced.
  - **Bounded run** — `ActionBudget` caps actions per run (50), total failures per run (3, cumulative — not reset by an intervening success), and per-step wall-clock (15s), backed by Selenium page-load/script timeouts.
  - **Full audit** — every action, allowed or denied, writes an `audit_trail` row via the existing `agent_task_completed`/`agent_task_failed` event types (the `audit=True` pattern from `tools/agent_toolkit/_shell.py`); audit failures never fail the call, but denials always raise.
  - **Bypass is explicit** — `get`, `execute_script`, `execute_cdp_cmd` and the timeout setters raise `ScopeViolation` on the wrapper; `.driver` is the single documented escape hatch. **CDP-transport note (cdp-port-03/04):** when the CDP backend is selected, CDP is an *internal transport detail beneath the guard*, never a caller-reachable escape hatch — `GuardedDriver` wraps the `CDPDriver` exactly as it wraps a Selenium `WebDriver`, and `execute_cdp_cmd` stays in `_BYPASS_ATTRS`, so reaching the DevTools protocol through the guarded wrapper still raises `ScopeViolation`. The backend swap does not widen this seam.
  - **Regression tests** — `tests/browser/test_scope.py` (52 tests) covers scheme/denylist/allowlist/locality ordering, the three-switch non-local path, egress fail-closed, placeholder substitution + broker denial, budget caps, post-action redirect containment, and bypass-attribute refusal.
- **Revisit if:** the agent is ever given a non-loopback default, `require_egress_guard` is turned off in a shipped config, or page content is fed back into the planning prompt without passing through the prompt-injection controls — the last would make rendered remote content a direct LLM ingress and warrant treating the browser as untrusted-content intake in its own right.

### Gap 37 — Third-party HTML content filter (`tools/http/page_extract.py`)

**Module:** `tools/http/page_extract.py` (OSS adaptation oss-filter-01), consumed by `tools/chat_router/url_analyzer.py::fetch_content`.

**Ingress path:** Raw HTML fetched from arbitrary third-party URLs a user pastes into chat or a canvas. This is the most openly attacker-influenceable input the platform accepts — anyone who can get a URL in front of a user controls the bytes. The module parses that HTML, scores its blocks, and renders markdown that is subsequently placed in an LLM prompt.

- **Decision:** **bypass-documented** — the module is a parser and a scorer; it executes nothing, fetches nothing, and resolves nothing. Sandboxing a pure stdlib text transform would add a process boundary without removing a capability, because the module has no capability to remove.
- **Rationale:** Parsing is stdlib `html.parser` in its default, non-executing mode. There is no `exec`/`eval`/`subprocess`, no network call, no filesystem write, and no deserialisation of attacker bytes into objects (the only `yaml.safe_load` reads the first-party `args/page_extract.yaml`). Scripts and styles never enter the tree at all — `script`, `style`, `noscript`, `template`, `svg`, `canvas`, `iframe`, `form`, `button`, `select` and `textarea` are dropped as whole subtrees during parsing, so their contents cannot reach the output or the LLM. `javascript:` and fragment hrefs are emitted as plain label text and never as links. No new runtime dependency is introduced (stdlib + the already-pinned `rank_bm25`), so the third-party attack surface is unchanged by this module.
- **Guardrails:**
  - **Non-executing parse** — `html.parser` with `convert_charrefs=True`; no scripting engine, no DOM, no resource loading. Malformed and hostile markup is tolerated (stray close tags are ignored, deep nesting is bounded by Python recursion, empty input returns an empty result) — covered by `tests/http/test_page_extract.py::test_malformed_input_does_not_raise`.
  - **Executable subtrees never enter the tree** — the `pruning.drop_tags` list in `args/page_extract.yaml` is applied at parse time, not after; `tests/http/test_page_extract.py::test_chrome_and_scripts_never_reach_the_output` asserts script and style bodies are absent from every corpus page's output.
  - **URLs are quarantined into a reference block** — reference-style citations mean the prose the LLM reads contains `[label][1]`, never a raw URL, which blunts prompt-injection-via-URL and keeps token spend down. `test_links_are_reference_style_and_urls_stay_out_of_the_prose` asserts no `https://` survives in the body.
  - **No egress** — the module takes an HTML *string*. It never fetches; the caller (`url_analyzer`) owns the fetch and its existing egress posture.
  - **Deterministic and bounded** — identical input yields byte-identical output (asserted per corpus page), and `markdown.max_block_chars` / `bm25.max_blocks` cap output size independently of input size.
  - **The output is still untrusted content** — this module *reduces* untrusted text; it does not *sanitise* it into trusted text. Downstream consumers must keep treating `fit_markdown` as third-party data subject to the existing prompt-injection and TRUST/provenance controls.
- **Revisit if:** the module is ever given the fetch itself, gains a JavaScript/DOM execution path (headless browser rendering), grows a non-stdlib HTML parser dependency, or begins resolving/inlining remote resources (images, imports, `srcset`) — any of which converts it from a text transform into an execution surface and warrants sandboxing.

### Gap 38 — Agent Browser page ingestion (`tools/browser/agent_browser.py`, `agent_tools.py`)

**Modules:** `tools/browser/agent_browser.py`, `tools/browser/agent_tools.py` (OSS adaptation oss-browse-01).

**Relationship to Gap 36:** Gap 36 covers the *policy* seam; this entry covers the *content* seam on the other side of it. `AgentBrowser` holds a `scope.GuardedDriver` and has no unguarded path to the session, so every control listed in Gap 36 — default-deny allowlist, scheme allowlist, post-action re-check, credential broker, action budget, audit row — applies to every method here without being restated or reimplemented. What follows is only what is *additional* to Gap 36: the risk created by pulling remote DOM content into an LLM context.

**Ingress path:** This is the most externally-exposed ingress in `tools/` — an agent navigates a real headless Edge/Chrome to an allowlisted URL and the page's DOM is extracted into `PageState`, whose `to_text()` rendering is fed straight into an LLM context by `BrowserToolRegistry`. Everything on the far side of `navigate()` is attacker-controllable: element text, attribute values, page title.

- **Decision:** **sandboxed** — the isolation boundary is the browser's own renderer sandbox, not `SandboxExecutor`.
- **Rationale:** Hostile web content is exactly the workload Chromium's renderer sandbox and site isolation were built for; wrapping the driver in `SandboxExecutor` would add a container boundary around a process that already has a stronger, purpose-built one, at the cost of making the vendored-driver path unusable. **This boundary is transport-independent (cdp-port-03/04):** whether the page is driven over Selenium/WebDriver or over CDP, the renderer sandbox is the same browser-side control, and on the Python side the ingested content is equally inert on both paths — `_EXTRACT_JS` returns plain JSON (over CDP it is run via `Runtime.evaluate` and returned by value), and no page-derived string is ever passed to `exec()`, `eval()`, `subprocess`, or a native parser regardless of transport. The only file mutation is a PNG written to `playwright/screenshots/` under a sanitised stem (`[^A-Za-z0-9_.-]` stripped, `..` collapsed, extension forced) — page content cannot influence the path.
- **Guardrails additional to Gap 36:**
  - **Prompt-injection blast radius is bounded by the representation, not by trust in the page.** `include_attributes` is an allowlist (page-authored attributes outside it never reach the model at all), and `max_elements` / `max_text_length` / `max_attr_length` cap what a single page can inject. This shrinks the channel; it does **not** close it — a page can still place instruction-shaped text in an element's visible text.
  - Element indices are per-observation: a stale index raises `StaleIndexError` rather than falling through to whatever now occupies that position, so a page that re-renders under the agent cannot redirect a click.
  - The extraction round trip and index lookup are read-only and deliberately **not** charged to the action budget, but they still run `assert_in_scope()` first — observation cannot be used to read a page the policy would refuse to navigate to.
- **Residual risk (accepted, stated plainly):** indirect prompt injection via page text. Any caller wiring `BrowserToolRegistry` into an agent loop with privileged tools owns that risk and should narrow `allowed_domains` in `args/browser_scope.yaml`.
- **Revisit if:** the browser is given a non-allowlisted attribute set, page-derived content is ever routed into a code-execution or file-write path, or `AgentBrowser` is exposed through an unauthenticated surface (a dashboard route or an MCP tool) rather than in-process to an agent loop.

### Gap 39 — Untrusted URL fetch/extract/scan path (`tools/http/fetch_extract.py`)

**Module:** `tools/http/fetch_extract.py` (OSS adaptation oss-filter-02), consumed by `tools/chat_router/url_analyzer.py`, `tools/document_intelligence/extractors.py::extract_url`, `tools/research/source_scanner.py`, `tools/creative/competitor_discoverer.py` and `tools/genesis/reflexes/research.py`.

**Relationship to Gap 37:** Gap 37 records that `page_extract` is a pure text transform which deliberately *does not fetch*. This entry covers the module that does — the composition of fetch, extract and scan into one path — and is where the egress and LLM-ingress guarantees for third-party pages now live.

**Ingress path:** Arbitrary third-party URLs supplied by a user (pasted into chat, registered as a research feed, configured as a competitor review page) are fetched over the network and the response body is extracted and placed into an LLM prompt. The bytes are fully attacker-controlled, and unlike Gap 37 this module performs the network call itself.

- **Decision:** **bypass-documented** — the module composes three existing controls (the central HTTP client, the `page_extract` parser, the injection scanner) and adds no execution, deserialisation or file-mutation capability of its own.
- **Rationale:** Response bytes are only ever `.decode()`d to `str` and handed to `page_extract` (Gap 37: stdlib `html.parser`, non-executing) or returned verbatim to a caller that parses them as XML/JSON with the stdlib. There is no `exec`/`eval`/`subprocess`, no filesystem write, no pickle/YAML load of attacker bytes, and no HTML rendering engine. The one `yaml.safe_load` reads the first-party `args/http_client.yaml`. Because the module's whole purpose is to *replace* ~5 hand-rolled fetch paths that had none of these controls, sandboxing it would isolate the hardened path while the bypasses it retires were never isolated at all — the security gain is in adoption, not in a process boundary.
- **Guardrails:**
  - **Egress is centralised, never ad hoc** — every fetch goes through `tools.http.client.get_session()`, so mTLS, the CA bundle, the egress proxy, `max_retries`/`backoff_factor` and redirect limits from `args/http_client.yaml` apply. `tests/http/test_fetch_extract.py::test_fetch_raw_uses_the_central_client_session` asserts the session is obtained from the client rather than constructed locally, and `test_no_raw_urllib_or_requests_fetch_in_retrofitted_modules` is an AST-level regression guard that the retrofitted call sites do not grow a direct `urlopen`/`requests.get` back.
  - **Memory is bounded during the read, not after it** — the body is streamed with `iter_content()` and cut at `fetch.max_bytes` (2 MiB default) *while* reading, so an unbounded or decompression-heavy response cannot exhaust memory before the cap is applied. `test_body_is_cut_at_the_byte_cap_and_flagged` asserts the cap bounds what is read. Truncation is reported (`truncated`, and a note in `reason`) rather than hidden.
  - **The byte cap is a transport ceiling, not a content budget** — relevance selection is `page_extract`'s job, so no caller re-truncates positionally. This is the defect the task set out to remove: a `[:7000]` cut keeps whatever sits at the top of the file (nav, cookie banner) and drops the answer.
  - **Injection scanning is mandatory and fails closed** — extracted text is passed to `tools/security/injection_scanner.py::scan_text` *after* extraction, so instructions hidden in markup a regex strip would have dropped are still seen. A `critical` finding replaces the text with the empty string, so a caller that ignores the `blocked` flag still cannot forward the payload. A scanner that is missing or raises is treated as a critical finding, not as a pass — asserted by `test_a_missing_scanner_fails_closed` and `test_a_crashing_scanner_fails_closed`.
  - **Scanning is opt-out only in code, never by config** — `scan=False` exists for forensic callers that must see raw payloads and has no `args/` switch; `block_on_critical_injection` in `args/http_client.yaml` defaults to `true`.
  - **A hostile or dead URL is data, not an exception** — every path returns `FetchedPage`, so one bad URL cannot take down a feed sweep or an ingestion run. Errors surface in `error`/`reason` instead of propagating.
  - **The output is still untrusted content** — as with Gap 37, this module *reduces and screens* third-party text; it does not launder it into trusted text. Downstream consumers keep the existing TRUST/provenance obligations.
- **Residual risk (accepted, stated plainly):** the injection scanner is pattern-based, so novel instruction-shaped text below `critical` severity still reaches the model. The module narrows the channel and makes the screening universal; it does not claim to close it.
- **Revisit if:** the module gains a JavaScript/DOM rendering path, begins resolving remote sub-resources, learns to write response bodies to disk, deserialises attacker bytes into objects (pickle, unsafe YAML, a native parser), or `block_on_critical_injection` is shipped as `false` in any profile — the last would make the fail-closed guarantee configuration-dependent.

### Gap 40 — CDP browser transport (`tools/browser/cdp/*`, `tools/browser/backend.py`, `browser_locator.py`)

**Modules:** `tools/browser/cdp/ws_client.py`, `session.py`, `launcher.py`, `driver.py`, `preflight.py`; `tools/browser/backend.py`; `tools/browser/browser_locator.py` (card `cdp-`, spike cdp-00).

**Relationship to Gaps 36/38:** this is the *transport* underneath the agent browser, one layer below Gap 38's content seam and Gap 36's policy seam. It carries no attacker-authored content decision of its own — page content ingestion and its renderer-sandbox boundary stay exactly where Gap 38 records them, transport-independent. What this entry adds is the one genuinely new surface the transport creates: a **local, unauthenticated DevTools debug port**.

**Ingress path:** To drive a browser over CDP, `launcher.py` starts it with `--remote-debugging-port`, and the browser then serves the DevTools protocol over a loopback WebSocket. CDP is **unauthenticated** — any local process that discovers the port can drive the browser. This is exactly why Chrome ≥136 refuses remote debugging on the default profile. The bytes the transport reads come from a browser ICDEV itself launched (first-party), not from a remote peer; the exposure is the port, not the framed data.

- **Decision:** **bypass-documented** — the transport is not sandboxed; it is a first-party channel to a browser process ICDEV owns, and the debug-port exposure is mitigated by construction rather than by a container.
- **Rationale:** There is nothing to isolate on the Python side — the frame codec (`ws_client`) does stdlib socket I/O and a 4-byte XOR mask, the session (`session.py`) parses JSON with `json.loads`, and no CDP-derived string is passed to `exec`/`eval`/`subprocess` or a native parser. `execute_script` results return by value as JSON. The launcher's `subprocess.Popen` argv is built only from a locator-resolved browser executable and fixed flags — never from user input. The real risk is the *port*, and a container boundary would not address it; the construction below does.
- **Guardrails:**
  - **Loopback only** — the listener binds to `127.0.0.1` (the CDP default), never `0.0.0.0`. Only `ws://` loopback is accepted (`ws_client.connect` refuses `wss://`/non-loopback schemes); TLS is out of scope by design.
  - **Ephemeral port** — `--remote-debugging-port=0` lets the OS assign the port; it is neither predictable (no fixed 9222) nor long-lived. The real port is read from the `DevToolsActivePort` file, not assumed.
  - **Fresh temp profile per run** — a mandatory `--user-data-dir` under a `tempfile.mkdtemp("icdev-cdp-")` carries no cookies, no saved credentials, no session state, so there is nothing of value for a co-resident local process to steal through the port. This profile is doing double duty: Chrome/Edge ≥136 *require* a non-default profile for debugging, and it is also the control that makes an unauthenticated port acceptable.
  - **Deterministic teardown** — `LaunchedBrowser.terminate()` stops the process and removes the temp profile; the session lifetime is short and bounded.
  - **Beneath the guard, never a bypass** — the CDP driver is what a backend selector hands to `GuardedDriver`; `execute_cdp_cmd` stays in `_BYPASS_ATTRS`, so CDP is not a caller-reachable escape hatch (see Gap 36).
  - **Loud degradation** — when no browser is present or policy forbids debugging, the transport refuses with an actionable error (naming the searched families / the tier), rather than binding something or stalling on a timeout.
- **Residual risk (accepted, stated plainly):** on a multi-user host, a co-resident local process could, within the session's short lifetime, connect to the ephemeral loopback port and drive the throwaway profile. The mitigation is that the profile holds nothing worth stealing and the port is short-lived and unpredictable; `--remote-debugging-pipe` (fd-based, no TCP listener at all) is the documented future hardening that removes the port entirely.
- **Revisit if:** the listener is ever bound to a routable address, the debug port is made fixed/predictable or long-lived, a persistent (non-temp) profile is used for a debugging session, or CDP is exposed to a caller as anything other than an internal transport beneath `GuardedDriver`.

### Gap 41 — Generic MCP tool executor for workflow steps (`tools/studio/executors/mcp_executor.py`)

**Module:** `tools/studio/executors/mcp_executor.py` (+ `icdev/` mirror) — dispatches any entry of `tools/mcp/tool_registry.py::TOOL_REGISTRY` as a Studio workflow step, so 455 registered tools become reachable without a hand-written executor each (card `dwo-`, task dwo-mcp-01).

**Ingress path:** A workflow step supplies two strings — `--tool <name>` and `--params '<json>'` — that originate from a Studio workflow template or the DAG editor, i.e. from an **authenticated Studio author**, not from an anonymous request or an ingested document. The executor then `importlib.import_module()`s a module path and `getattr()`s a handler, and calls it with the parsed params.

- **Decision:** **trusted-first-party** (import target). Authorization of *which* tools a step may call is the default-deny allowlist landed by dwo-mcp-02-d1/d2; *which caller* may call them is the IL/RBAC check landed by dwo-mcp-02-d3.
- **Rationale:** The security-relevant property is that **no user-supplied string ever reaches `importlib`**. `--tool` is used only as a dictionary key into `TOOL_REGISTRY`; a miss raises `LookupError` and exits 1. The `module`/`handler` strings actually passed to `importlib.import_module()`/`getattr()` come exclusively from that repo-authored registry, so the reachable import set is a fixed, committed allowlist of 455 entries — not an open namespace. This is the identical resolution path `tools/mcp/unified_server.py::_resolve_handler` already uses for the same registry; this executor adds no import capability that the MCP gateway did not already expose. Params are `json.loads`-parsed only (never `eval`), and are schema-checked before dispatch. There is no `exec`, no `subprocess`, and no shell.
- **Guardrails:**
  - **Registry-keyed dispatch** — `resolve_entry()` refuses anything not in `TOOL_REGISTRY`; MCP *resources* are refused explicitly rather than silently dispatched.
  - **Schema gate before dispatch** — params are validated against the entry's own `input_schema` (`jsonschema.Draft7Validator`) and the handler is **not called** if validation fails; `test_run_rejects_invalid_params_before_dispatch` asserts non-invocation.
  - **Object-only params** — `parse_params()` refuses non-JSON and refuses any JSON that is not an object, so a handler always receives a `dict`.
  - **Fails the step, never launders the error** — an unknown tool, invalid params, an unimportable handler, or a raising handler all exit 1, so `workflow_runner` marks the step failed. This deliberately diverges from the MCP protocol layer, which returns a handler exception as a *successful* call with `{"error": ...}` in the payload.
  - **Default-deny allowlist before lookup** — `check_tool_allowed()` refuses any tool absent from `mcp_workflow_tools.allowed` (gate MCP-WF-001) *before* the registry is touched, so a refused tool is never imported. A missing or non-default-deny policy refuses everything.
  - **Caller IL/RBAC before dispatch** — `check_caller_authorized()` refuses a caller below the tool's `min_il` or missing a required role, reading both from `args/component_registry.yaml` via the component that owns the handler's module. Runs after lookup (it needs the module) but before params and dispatch; `test_run_refuses_before_the_handler_is_called` asserts non-invocation.
  - **Regression test** — `tests/studio/test_mcp_executor.py` (39 tests) covers registry lookup, resource rejection, each validation failure mode, handler-exception propagation, the allowlist gate, and the CLI exit-code contract; `tests/studio/test_mcp_executor_rbac.py` (34 tests) covers caller resolution and the IL/role refusals.
- **Revisit if:** the tool name or module path ever becomes derivable from tenant/end-user content rather than an authenticated template author; the registry lookup is relaxed to accept an arbitrary dotted module path; or the `mcp_workflow_tools` allowlist is removed or made default-allow → re-decide as **sandboxed** (`tools/security/sandbox_executor.py`).

### Gap 42 — SAML ACS response parsing (`tools/auth/saml.py`)

**Module:** `tools/auth/saml.py` (`process_acs_response()`, + `icdev/` mirror); ingress at `tools/auth/blueprint.py` route `POST /auth/saml/<provider_id>/acs` (task aca-hyg-06-d2).

**Ingress path:** An identity provider (or anyone who can reach the ACS route) POSTs a base64-encoded `SAMLResponse` form field. `process_acs_response()` base64-decodes it and parses the resulting XML to extract `NameID` and `Attribute` values, which are then persisted to `sso_sessions`. This is **unauthenticated, remote-attacker-reachable XML** — the ACS endpoint must accept the POST before any session exists.

- **Decision:** **bypass-documented** (hardened parser, no code-execution surface)
- **Rationale:** Parsing goes through `defusedxml.ElementTree.fromstring` (already a project dependency, `defusedxml>=0.7` in both `requirements.txt` and `pyproject.toml`), which forbids internal entity expansion (billion-laughs DoS), external entity resolution (XXE / local file disclosure), and external DTD retrieval (SSRF). Previously this call site used stdlib `xml.etree.ElementTree.fromstring`, which permits internal entity expansion — Bandit B314. The stdlib `ET` import is retained *only* to BUILD the XML this module emits (`register_namespace`, `Element`, `SubElement`, `tostring` in `generate_sp_metadata`/`initiate_saml_login`) and is marked `# nosec B405` accordingly; no untrusted bytes reach it. Extraction is read-only tree traversal (`find`/`findall`) into parameterized SQL — no `eval()`, `exec()`, `subprocess`, or filesystem writes.
- **Guardrails:**
  - `defusedxml.ElementTree.fromstring` for all untrusted parsing; stdlib `ET` is construction-only.
  - Both `ET.ParseError` (malformed XML) and `ValueError` (defusedxml's `EntitiesForbidden` / `DTDForbidden` / `ExternalReferenceForbidden`, which subclass `ValueError`) are caught and re-raised as a uniform `ValueError("Invalid SAMLResponse XML")`, so the parser's internals are never echoed to the caller.
  - `tools/auth/blueprint.py::acs` catches that `ValueError` and returns HTTP 400 — a hostile payload is rejected without creating a session.
  - Invalid base64 is rejected before the parser runs.
  - `tests/test_ecr_sso.py` covers metadata parsing and benign ACS round-trips (16 tests).
- **Revisit if:** parsing moves back to stdlib `xml.etree`/`minidom`/`lxml` without `resolve_entities=False`, the module starts resolving external references from assertion content, or a signature-verification path is added that shells out to an external XMLSec binary.
- **Scope of this entry:** this decision covers the **parser's** attack surface only (entity expansion, external references, malformed input). SAML *protocol trust* — assertion signature verification and condition/audience validation — is a separate concern reviewed and tracked outside this document.

### Gap 43 — Reproduce-or-drop finding replay (`tools/security/reproduction_validator.py`)

**Module:** `tools/security/reproduction_validator.py` (oss-poc-01).

**Ingress path:** Two, both untrusted by construction. (1) A *reproduction* — a JSON object carrying `steps[]` (method/path/headers/body) and a `predicate` — which may be authored by an LLM agent claiming a dynamic finding, not by a maintainer. (2) The *response* from the replayed target, which is by definition attacker-influenceable if the target is compromised. This module is unusual among ICDEV ingress points in that it deliberately issues outbound HTTP as its core function.

- **Decision:** **bypass-documented**
- **Rationale:** Neither ingress reaches an execution path. A reproduction is interpreted, never executed: `validate_reproduction()` rejects unknown `kind` values, and steps are consumed only as arguments to `session.request()` — there is no `exec`/`eval`/`subprocess`/`os.system`/deserialization-of-code anywhere in the module. Predicates are evaluated by `evaluate_predicate()`, a closed dispatch over six literal types (`PREDICATE_TYPES`); an unknown type returns `False` rather than falling through, so an unevaluable predicate can never confirm a finding. Response bodies are truncated to `replay.max_body_bytes`, matched with `re.search`/`in`, and stripped by `_redact()` before they leave the replay — only status, length and sha256 persist.
- **Guardrails:**
  - **Scope lock (the load-bearing control):** `is_target_allowed()` is default-deny against `target_allowlist` in `args/reproduction_policy.yaml` — loopback only out of the box. A non-allowlisted host returns `refused` with zero observations; **nothing is sent**. Widening is an explicit operator act (`ICDEV_REPRO_TARGET_ALLOWLIST`), and the intent is own-targets-only, never a third-party host. `tests/test_reproduction_validator.py::TestScopeLock` asserts the refusal and that an unparseable URL is not allowlisted.
  - Retries are disabled per replay (`HTTPAdapter(max_retries=0)`) so a reproduction is exactly-once; redirects are not followed unless a step opts in (a 302 to a login page *is* the authz signal).
  - Proxies are cleared and `trust_env` disabled for loopback targets, so an operator-configured egress proxy cannot answer in place of the target.
  - `replay.max_steps` (8) bounds a reproduction to session-setup-then-access; it is not a crawler.
  - `TestEvidenceHygiene` asserts no response body — and specifically no seeded secret marker — survives into an observation, which matters because ICDEV is a public repo and these rows are read by dashboards.
- **Revisit if:** a non-loopback host is added to the shipped allowlist; a reproduction `kind` is added whose replay executes rather than interprets its steps (a registered `_TRACE_REPLAYER` driving a real browser is the likely candidate — that engine must land its own decision); or `_redact()` is relaxed to retain bodies.

### Gap 44 — Swallowed-persistence detector and codemod (`tools/refactor/swallowed_persistence.py`, `fix_swallowed_persistence.py`)

**Modules:** `tools/refactor/swallowed_persistence.py`, `tools/refactor/fix_swallowed_persistence.py` (swp-swallow-01).

**Ingress path:** Repository Python source. The detector reads every `*.py` under the paths it is given, decodes it, and parses it with `ast.parse`. The fixer additionally **writes** rewritten source back to those same files. The content is first-party — it is the checkout the tool is running inside — but the fixer is a maintainer-invoked codemod with write access to the tree, so it is listed here rather than left implicit.

- **Decision:** **trusted-first-party**
- **Rationale:** Source is parsed, never executed. Neither module contains `exec`, `eval`, `compile`, `subprocess`, `os.system`, `importlib`, or any deserialization of the files it reads — `ast.parse` builds a tree and the tools walk it. Nothing read from a scanned file is interpolated into SQL, a shell command, or a path: the only value taken from file *content* is the table name in the log message, and that comes from a `[A-Za-z_][\w.]*` capture group written into a Python string literal. Output paths are always the input path — the fixer rewrites in place and never derives a destination from file content.
- **Guardrails:**
  - The fixer re-parses its own output (`ast.parse(new_src)`) before writing and raises rather than emitting a file it just broke; a file whose module logger did not land at module level is refused.
  - Write is opt-in: `--dry-run` is the default posture and `--write` must be passed explicitly; `run(paths, write=False)` produces the same report with no filesystem effect.
  - A per-file exception is caught, recorded in `errors[]`, and the sweep continues — one malformed file cannot abort or half-apply the run. The process exits non-zero when `errors` is non-empty.
  - Scanning excludes `migrations/`, `tests/`, `.tmp/`, `node_modules/`, `__pycache__/`, and the three self-referential modules, so the tool cannot rewrite its own detector or the gate that consumes it.
  - Line endings and file encoding are preserved (`read_source` normalises to LF for parsing and restores the original newline on write).
  - `tests/test_coherence_swallowed_persistence.py` pins the detector in both directions and asserts the real tree is clean.
- **Revisit if:** the fixer gains an LLM-authored rewrite path (source text becoming model output rather than a deterministic transform), starts writing to a path derived from file content, or is wired into an unattended reflex that runs `--write` without review.

### Gap 45 — Observable dispatch fan-out (`tools/analyzers/dispatch.py`)

**Module:** `tools/analyzers/dispatch.py` (anz-disp-01), exposed over MCP as `analyzer_dispatch` / `analyzer_capabilities`.

**Ingress path:** Two. (1) The **observable value** — an IP, domain, URL, file hash, CVE id, vendor name, file path or free text supplied by a caller (a chat turn, an MCP client, an agent) and passed as an argument into first-party analyzer functions. (2) The **analyzer's return payload**, from which `extract_taxonomy()` reads `{predicate, level, value}` tags. Both are listed because dispatch is deliberately a *convergence point*: one entry point now carries caller-controlled content into ~79 analyzer-shaped modules.

- **Decision:** **trusted-first-party** (the dispatcher itself); each analyzer keeps its own declared posture.
- **Rationale:** The dispatcher interprets, never executes. It contains no `exec`/`eval`/`compile`/`subprocess`/`os.system` and no deserialization; the observable value is only ever bound to a keyword argument of a declared callable. Critically, **the caller cannot steer the import**: `resolve_entrypoint()` takes its dotted `module`/`entrypoint` from `args/analyzer_contract.yaml` — first-party and code-reviewed — and `dispatch()` accepts an *observable type* from a closed vocabulary, never a module path. An unknown type raises `UnknownObservableType` rather than resolving anything. Taxonomy tags are validated against the declaration, and the namespace is stamped from the declaration rather than read from the payload, so an analyzer cannot label its output as another subsystem's.
- **Guardrails:**
  - `sandbox:` is declared per analyzer in the contract and defaults to the strictest posture (`sandboxed`) for anything that declares nothing. Dispatch surfaces that posture through `analyzer_capabilities`; **enforcement lands in `anz-rate-01`** via the existing `sandbox_execute` path, not a second isolation mechanism.
  - Responders (`kind: responder`) — which take real-world action, e.g. RTBH blackholing a prefix — are excluded from the default fan-out and require an explicit `include_responders`. Submitting an IP for analysis cannot trigger one.
  - Every analyzer runs under its declared `timeout_seconds` on a bounded process-wide pool (`ICDEV_ANALYZER_MAX_WORKERS`, default 8), so a hostile or hanging input cannot exhaust threads or stall the caller; a timed-out future is abandoned, not joined.
  - An analyzer that raises is contained: `_run_one()` catches per analyzer and returns an `error` report, so one bad input cannot abort the fan-out or leak a stack trace to the caller as an unhandled exception.
  - Nothing is dropped silently — `timeout`, `error`, `unavailable`, `misdeclared` and `skipped` are all reported by name and set `partial`, so a caller cannot mistake "the analyzer never answered" for "the analyzer found nothing".
  - `tests/test_analyzer_dispatch.py` pins the timeout, error-containment, responder-exclusion and taxonomy-validation behaviours.
- **Revisit if:** an analyzer is declared whose entrypoint shells out or executes the observable (at that point the declaration must be `sandboxed` and `anz-rate-01`'s enforcement is a prerequisite, not a follow-up); or `dispatch()` ever accepts a module path, callable, or contract file from a caller rather than from `args/analyzer_contract.yaml`.

### Gap 46 — Agent approval gate: reversibility classification of tool calls (`tools/agent_runtime/approval_gate.py`)

**Module:** `tools/agent_runtime/approval_gate.py` (ars-appr-01).

**Ingress path:** Two, and the first is genuinely untrusted. (1) The **tool input of every agent tool call** — an arbitrary dict authored by an LLM, flattened to a string and matched against the policy's regexes. This module sees every tool in the platform, so it sees every model-authored argument. (2) `args/agent_approval_policy.yaml`, a first-party config that supplies the regex patterns and the tier lists.

- **Decision:** **bypass-documented**
- **Rationale:** Neither ingress reaches an execution path. Tool input is *read about*, never run: `flatten_input()` builds a string, `re.search` matches it, and `hashlib.sha256` digests it — there is no `exec`/`eval`/`compile`/`subprocess`/`os.system`/`pickle`/`importlib` anywhere in the module, and the gate never invokes the tool it is classifying. It returns a verdict; the *caller* decides whether to run the handler. The policy file is parsed with `yaml.safe_load` (never `yaml.load`), and its patterns are compiled with `re.compile` inside a `try` that logs and skips a bad pattern rather than raising. Classification is a pure function of (tool name, flattened input, policy).
- **Guardrails:**
  - **Fail-closed by construction (the load-bearing control):** `default_tier` is `unknown` and `unknown` is in `require_approval_tiers`, so a tool must be *named* in the policy to run unattended. An unreadable, malformed, or missing policy file falls back to `_FALLBACK_POLICY`, which enumerates **zero** tools — every call then requires a human. A config failure cannot be the reason an irreversible action ran unattended.
  - Content patterns are **asymmetric**: an `irreversible` pattern escalates any tool, but `recoverable`/`reversible` patterns apply only to the declared `command_tools`. Model-authored argument text therefore cannot *lower* a tool's tier — the reverse would let an LLM auto-approve itself by mentioning `mkdir`.
  - Hard blocks are consulted first via `run_pre_tool_check` and are never escalated to the approver, so the gate cannot be used to talk past `.claude/hooks/pre_tool_use.py`.
  - A raising approver is caught and **denies** (`test_a_broken_approver_denies`); `console_approver` denies on EOF, so a non-interactive run cannot silently self-approve.
  - Argument **values never persist**. `agent_approval_log` stores argument key names and a SHA-256 of the flattened input only — model-authored input may carry CUI, and ICDEV is a public repo.
  - `tests/test_agent_approval_gate.py` pins all of the above, including the regression that incidental argument text cannot downgrade `git_push`.
- **Revisit if:** the gate gains a policy source that is not first-party (a tenant-supplied or LLM-authored policy would make the regexes untrusted input), a pattern language more expressive than `re` is adopted, or the module starts *executing* a remediation rather than returning a verdict.

### Gap 47 — Semantic loop detection and transcript replay (`tools/llm/loop_detector.py`, `loop_detector_tune.py`)

**Modules:** `tools/llm/loop_detector.py`, `tools/llm/loop_detector_tune.py` (ars-loop-01).

**Ingress path:** Two. (1) The detector is handed every executed tool call's **arguments and result text** by `run_agent_loop` — result text is model- and environment-influenced, and for a tool that fetches remote content it is attacker-influenceable. (2) The tuner reads recorded agent transcripts (`*.jsonl` / `*.json`) from an operator-supplied path; those files contain the same untrusted tool output, persisted.

- **Decision:** **trusted-first-party** (detector) / **bypass-documented** (tuner)
- **Rationale:** Neither module interprets what it reads. The detector's entire contact with tool content is string comparison: `str.replace`, `re.sub` over three fixed patterns, `str.split`, `difflib.SequenceMatcher`, and set intersection. There is no `exec`/`eval`/`compile`, no `subprocess`, no `importlib`, no deserialization of content (`json.dumps` is used to *emit* a comparison key, never `json.loads` on tool output), no SQL, and no path derived from content — the only filesystem read is `args/llm_config.yaml` via the canonical `resolve_llm_config_path()`. The tuner adds `json.loads` per transcript line, which builds data, not code, and a per-file `try/except` so a malformed transcript is skipped rather than aborting the sweep.
- **Guardrails:**
  - Result text is sampled to `result_sample_chars` (400 head + tail) before comparison, so a multi-megabyte tool result cannot drive an unbounded comparison.
  - Comparison windows are bounded by `window` (8 calls), making the O(n²) clustering trivially bounded regardless of transcript size.
  - `load_detector_config()` catches every exception and falls back to `DEFAULT_CONFIG` — a malformed config file can neither crash a running agent nor silently disable the control with attacker-chosen thresholds.
  - The detector is pure: it returns a verdict, and only `run_agent_loop` acts on it (ending the run). Content it reads never reaches a handler, a prompt, or a write.
  - The tuner is operator-invoked, read-only, and writes nothing outside stdout.
- **Revisit if:** the detector gains an LLM-based similarity judge (tool output would then become prompt content and needs the injection posture that applies to `tool_result_sanitizer`), or the tuner grows a `--fix`/`--write` mode that edits config from what it read.

### Gap 48 — Ported analyzer declarations and posture enforcement (`args/analyzer_contract.yaml`)

**Modules:** the six analyzer/responder declarations seeded into
`args/analyzer_contract.yaml` (anz-con-01), dispatched through
`tools/analyzers/dispatch.py` (anz-disp-01) and gated by
`tools/analyzers/rate_limit.py` + `tools/analyzers/sandbox.py` (anz-rate-01).

**Ingress path:** Gap 45 records the decision for the *dispatcher*. This gap
records one decision per *ported analyzer*, as OPT-58 requires: dispatch is a
convergence point that carries a caller-supplied observable (IP, domain, URL,
file hash, CVE id, vendor name, file path, STIX bundle) into each declared
entrypoint, so each entrypoint is its own ingress point and needs its own
recorded trust decision rather than inheriting the dispatcher's.

**Enforcement (new in anz-rate-01).** The `sandbox:` posture below is no longer
advisory. `tools/analyzers/sandbox.py::resolve_execution_mode` turns it into an
execution mode on every dispatch, and `_run_one` routes the call accordingly:

- `sandboxed` → always executed through `SandboxExecutor`
  (`tools/security/sandbox_executor.py`, D-SEC-10) — the same object behind the
  `sandbox_execute` MCP tool. **No second isolation path was built.**
- `sandboxed_on_demand` → in-process, promoted to `SandboxExecutor` when
  `ICDEV_STRICT_SANDBOX=1` (IL5 / air-gap), matching this document's convention.
- `trusted_first_party` / `bypass_documented` → in-process.
- anything else → **sandboxed** (fail closed).

If a posture requires the sandbox and the sandbox is disabled or has no
container runtime, the analyzer is reported `sandbox_unavailable` and **is not
run in-process**. Failing closed is the point: a silent downgrade would give
the strictest declaration the weakest behaviour on exactly the hosts that
cannot isolate it.

| Analyzer (contract key) | Entrypoint | Decision | Rationale |
|---|---|---|---|
| `cve_triage` | `tools/supply_chain/cve_triager.py::triage_cve` | **trusted-first-party** | Scores a CVE id against the first-party dependency graph in `data/icdev.db`. No `exec`/`eval`/`subprocess`/`importlib`, no deserialization of the observable, no network egress — the CVE id is bound to a parameter and used in parameterised SQL. Blast radius is computed from first-party rows. |
| `section_889_screen` | `tools/supply_chain/ndaa_889_screener.py::screen_item` | **trusted-first-party** | String-matches a vendor name against the repo-resident Section 889 Part A/B covered-entity lists. No execution primitives; the observable is compared, never interpreted. |
| `threat_intel_match` | `tools/security_canvas/threat_intel_engine.py::match_observable` | **trusted-first-party** | Looks an observable up among already-ingested indicator rows. The untrusted step is *feed ingestion*, which is upstream of this analyzer and carries its own posture; matching is a parameterised read with no execution primitives. |
| `secret_scan` | `tools/security/secret_detector.py::scan` | **sandboxed-on-demand** | The one seed analyzer that **executes an external process over caller-supplied input**: it shells out to `detect-secrets` via `subprocess.run` against a caller-supplied `file_path` / `repository`. Argument-list form (never `shell=True`) with a timeout, and the scanner only reads. Permissive in dev because the target is normally a local first-party checkout; under `ICDEV_STRICT_SANDBOX=1` dispatch routes it through `SandboxExecutor` so an attacker-supplied checkout is parsed inside the container boundary. |
| `stix_ingest` | `tools/strategos/stix_importer.py::parse_bundle` | **sandboxed** | Parses a **wholly attacker-controllable** STIX 2.x bundle — the largest untrusted-content surface in the seed set. Declared `sandboxed`, and now enforced: dispatch will not call it in-process. Note the deployment prerequisite below. |
| `rtbh_blackhole` | `tools/dsoc_canvas/rtbh_manager.py::trigger_rtbh` | **trusted-first-party** | A **responder**, excluded from the default fan-out (`kinds=("analyzer",)`) because blackholing a prefix must never be triggered by submitting an IP for analysis. Its first parameter is a live DB connection; since anz-mig-01 the dispatcher *can* supply one via `binding.connection`, but this declaration deliberately does not — a responder that mutates routing should stay unreachable by dispatch. No execution primitives. |
| `bgp_prefix_hijack` | `tools/dsoc_canvas/bgp_hijack_detector.py::detect_prefix_hijack` | **trusted-first-party** | Compares an observed prefix/origin-AS pair against first-party ownership rows and writes a detection. The observable is bound to a parameter and used in parameterised SQL — no execution primitives, no deserialization, no egress. Takes a DB handle supplied by `binding.connection` (anz-mig-01), which is why it cannot be `sandboxed`: a live connection cannot cross the sandbox boundary, and dispatch reports that combination `misdeclared` rather than silently passing `conn=None`. |
| `bgp_route_leak` | `tools/dsoc_canvas/bgp_hijack_detector.py::detect_route_leak` | **trusted-first-party** | Same module, same posture and same connection reasoning as `bgp_prefix_hijack`: a parameterised comparison of announcement scope against first-party peering rows, writing its finding through the dispatcher-owned handle. |
| `pvm_risk_prediction` | `tools/network/vuln_predictor.py::predict_advisory_risk` | **trusted-first-party** | Reads one already-ingested `nc_advisories` row by id and computes scores arithmetically. The observable is an integer row id, never interpreted; ingestion of the advisory is the untrusted step and is upstream of this analyzer. |
| `pvm_triage_scoring` | `tools/network/vuln_triage_engine.py::score_advisories` | **trusted-first-party** | Batch-shaped read over the same first-party advisory rows (`observable_form: list`). Arithmetic scoring only — no execution primitives and no content parsing. |
| `pvm_attack_surface` | `tools/network/attack_surface_mapper.py::map_attack_surface` | **trusted-first-party** | Scopes NQE device-inventory queries by a first-party network id and aggregates exposure counts. The observable selects rows; it is never executed or parsed as content. |

**Deployment prerequisite for `sandboxed` analyzers.** The sandbox driver runs
`importlib.import_module(<declared module>)` inside the container, so the image
must have the platform importable. The stock `python:3.12-slim` in
`args/sandbox_config.yaml` does not, and `stix_ingest` will report `error`
naming `ModuleNotFoundError` against it. An operator declaring an analyzer
`sandboxed` must point `sandbox.images.python` at an image carrying ICDEV. This
is a configuration step and it fails **loudly**; that is the intended trade
against silently running untrusted-bundle parsing in-process.

**Guardrails:**
- Postures are declared as data in `args/analyzer_contract.yaml` and validated
  against the contract's closed `sandbox_postures` vocabulary at load time; an
  unknown posture raises rather than defaulting to permissive.
- An analyzer that declares nothing inherits `defaults.sandbox: sandboxed` —
  the strictest posture, not the cheapest.
- Arguments crossing the sandbox boundary must be JSON-serializable; a bound
  DB connection or file handle is rejected as `misdeclared`, by name, instead
  of producing a container-side stack trace.
- Rate limits are enforced per analyzer *after* binding and posture resolution,
  so an analyzer that never ran spends no quota against a metered external API
  (SAM.gov, CISA KEV, NVD, ACLED, the OSINT feeds).
- Exceeding a rate limit **queues or reports — it never drops**: the analyzer
  still produces a `rate_limited` report carrying `retry_after_seconds`, and
  `DispatchResult.partial` names it. An omitted report would be
  indistinguishable from "the analyzer ran and found nothing".
- `tests/test_analyzer_rate_limit.py` pins the fail-closed sandbox gate, the
  never-dropped rate-limit reporting, and the requirement that **every**
  declared analyzer has a decision in this table whose wording matches its
  declared posture — so a newly ported analyzer cannot merge without landing
  here.
- **Revisit if:** an analyzer is ported whose entrypoint executes, deserializes
  (`pickle`, `yaml.load`), or shells out over the observable — it must be
  declared `sandboxed` and this table updated; or if `dispatch()` ever accepts
  a module path or callable from a caller rather than from the contract file.

### Gap 49 — SBOM Component Producer resolution (`tools/compliance/component_producer.py`)

**Module:** `tools/compliance/component_producer.py` (sbx-fld-02), imported by
`tools/compliance/sbom_generator.py`.

**Ingress path:** Third-party package metadata, read straight off the target
project's disk — `*.dist-info/METADATA` under a virtualenv's `site-packages`,
`node_modules/**/package.json`, `.pom` files in a Maven local repository,
`Cargo.toml` in a vendor directory or the Cargo registry source cache, and
`.nuspec` files in a NuGet packages folder. Every one of those files is written
by whoever published the dependency, so all of it is untrusted by construction —
that is the point of inventorying it. `--validate` additionally reads a
CycloneDX JSON SBOM from an operator-supplied path, which may have come from
another vendor's tool.

- **Decision:** **bypass-documented**
- **Rationale:** The module reads and never runs. Its total contact with
  untrusted content is `Path.read_text`, `json.loads`, `tomllib.loads`,
  `importlib.metadata.PathDistribution` (which parses `METADATA` as text —
  nothing from the target environment is imported), a handful of anchored
  regexes, and string normalization. There is no `exec`/`eval`/`compile`, no
  `subprocess`, no `pickle`, no `yaml.load`, no SQL, and no network call of any
  kind: resolution is offline-first for the same reason `dependency_resolver`
  is, so it behaves identically in an air-gapped enclave. A hostile author
  string is normalized into a name or rejected as a placeholder; it is never
  interpreted.
- **Guardrails:**
  - POM and `.nuspec` are read with regexes rather than an XML parser
    *deliberately*. Every stdlib XML parser is exposed to entity-expansion and
    external-entity attacks against exactly this kind of third-party packaging
    metadata; a regex is not. `_parse_pom_xml` in `sbom_generator.py` made the
    same call for the same reason.
  - Every file path is composed from the component's own coordinates under a
    caller-supplied root. The npm install path taken from a lockfile key is
    rejected if it contains a `..` segment, so a malicious `package-lock.json`
    cannot walk out of `node_modules`.
  - `_resolve_uncached` wraps each ecosystem resolver: a malformed manifest
    becomes unknown provenance with the exception type as its reason, never an
    aborted SBOM and never a silently absent element.
  - `yaml.safe_load` is used once, on ICDEV's own
    `args/sbom_producer_registry.yaml`, and a missing or corrupt registry
    degrades to an empty one — which marks components unknown rather than
    naming an organization that was never established.
  - The producer is only ever *emitted*, never dispatched on. It reaches SQL
    solely through `producer_db_value()` as a bound parameter.
  - `tests/test_sbom_component_producer.py` pins the no-execution posture over
    both the root and the `icdev/` mirror, and exercises the placeholder,
    namespace-echo and missing-metadata paths directly.
- **Revisit if:** the module gains registry lookups over the network (a real
  crates.io owner query or a PyPI JSON API call) — that is a different posture
  and a different threat model — or if it starts reading *artifacts* rather than
  metadata, which is sbx-fld-03's territory.

### Gap 50 — SBOM correction ingress (`tools/compliance/sbom_revision.py`)

**Modules:** `tools/compliance/sbom_revision.py` (mirrored at
`icdev/tools/compliance/sbom_revision.py`), consumed by
`tools/compliance/sbom_generator.py` and `tools/compliance/sbd_assessor.py`

**Ingress path:** Two surfaces, and only one of them is first-party.
`plan_revision` / `content_digest` on the generation path only ever see a
document this tree just built, so that half is trusted-first-party. `--correct
--sbom <path>` is not: the corrected SBOM is an operator-supplied JSON document
that may well have originated with an upstream producer, and it is the document
that becomes the project's SBOM of record. `source_revision` additionally shells
out to `git` in a caller-supplied directory.

- **Decision:** **trusted-first-party** for the generation path; **bypass-documented**
  for the correction path (parse-and-record only; no execution path exists)
- **Rationale:** The module's whole toolkit is `json`, `hashlib`, `pathlib` and
  one fixed-argv `git rev-parse`. It contains no `exec`, `eval`, `__import__`,
  `pickle`, `yaml.load` or `os.system`, and no network client. A corrected SBOM
  is round-tripped through `json.load`/`json.dump` and digested; **nothing in it
  is ever dispatched on, resolved as a path, or used to build SQL**. The only
  values from the document that reach the database are the component count, the
  `serialNumber` string and the digest, each as a bound parameter. `git rev-parse`
  is not a shell: fixed argv, `shell=False`, a 15-second timeout, and a
  non-zero exit or any `OSError` yields `None` — which is reported as
  `sbom_build_identity_unknown` rather than being smoothed into a pass.
- **Guardrails:**
  - The output path for a correction is derived from the **predecessor's own
    recorded `file_path`**, never from anything inside the supplied document, so
    a hostile SBOM cannot choose where bytes land.
  - A correction is append-only by construction: it can only INSERT a successor
    row. There is no code path by which a supplied document rewrites, blanks or
    deletes the record it corrects, which is what stops a bad artifact from
    *destroying* prior evidence rather than merely adding wrong evidence.
  - `revision_reason` is validated against the closed `REVISION_REASONS`
    vocabulary and `apply_correction` refuses a non-corrective code, so the
    caller cannot mislabel a correction as a routine build.
  - A correction must state a reason; an empty one raises rather than recording
    an unexplained change to a compliance artifact.
  - The head is re-resolved inside `apply_correction` and compared to the row it
    was prepared against, so a concurrent writer cannot make a correction
    supersede the wrong document.
  - `gate_threshold` reads only ICDEV's own `args/security_gates.yaml`, via
    `yaml.safe_load`, and a missing or corrupt file degrades to the documented
    default.
  - `tests/test_sbom_revision_2026.py` records every statement a correction
    issues and fails on an `UPDATE` or `DELETE`, and compares the predecessor row
    field-by-field before and after — so the append-only property is pinned, not
    merely intended.
- **Revisit if:** the correction path ever resolves anything *out of* the supplied
  document — a file path, a URL, a tool name, a signing key reference — which
  would turn parsed content into a resource reference; or if `source_revision`
  gains a remote lookup (a CI API call to name the build), which is a network
  path over attacker-influenceable material and a different threat model.
### Gap 50 — SBOM disclosure convention and policy (`tools/compliance/unknown_information.py`)

**Module:** `tools/compliance/unknown_information.py` (sbx-prc-01), imported by
`tools/compliance/sbom_generator.py`.

**Ingress path:** Two inputs. `load_disclosure_policy` reads ICDEV's own
`args/sbom_disclosure_policy.yaml`, which is first-party configuration but is
edited by an operator to declare redactions. `validate_sbom_disclosure`, reached
through `--validate`, reads a **CycloneDX JSON SBOM from an operator-supplied
path** — which may be another vendor's output, since the point of a conformance
validator is to be pointed at documents ICDEV did not produce.

- **Decision:** **bypass-documented**
- **Rationale:** The module compares strings against closed vocabularies and
  emits property dicts. Its total contact with untrusted content is
  `json.loads`, `yaml.safe_load`, `str()`, `.strip()`, `.lower()` and set
  membership. There is no `exec`/`eval`/`compile`, no `subprocess`, no
  `pickle`, no SQL, no regex over attacker input, and no network call. Nothing
  read from a foreign SBOM is dispatched on: a reason code either *is* a member
  of `UNKNOWN_REASONS`/`WITHHELD_REASONS` or becomes a validation error, and a
  field name either *is* one of the 17 minimum elements or becomes one.
- **Guardrails:**
  - `_clean_rules` drops a policy rule whose field or reason is unrecognised
    rather than defaulting it, so a typo cannot silently widen or narrow a
    redaction. `policy_defects()` reports every dropped rule, and the CLI exits
    non-zero when there are any, so a mistyped redaction is loud rather than
    absent.
  - `_rule_matches` treats an unmatched key as a **non**-match. A redaction that
    accidentally applied to every component would be far worse than one that
    applied to nothing, because only the second is visible on inspection.
  - A withheld field carries no free-text detail — :meth:`Disclosure.withheld`
    evicts any detail the field had. Explaining a redaction inside the document
    the redaction protects would undo it, and the validator fails a document
    that does so.
  - `yaml.safe_load` (never `yaml.load`) on the policy; a missing or corrupt
    policy degrades to a default that withholds nothing and still names an
    enquiry route, because an SBOM that withholds without one is what the
    standard forbids.
  - `Disclosure.from_db_values` treats unreadable JSON as empty, so a corrupt
    `unknown_fields_json` column cannot raise inside SBOM rendering.
  - `tests/test_sbom_unknown_information.py` exercises the rejection paths
    directly: a withheld reason under the unknown prefix, an unknown reason
    under the withheld prefix, a field in both states, an unrecognised field
    name, and the pre-2026 conflating literals.
- **Revisit if:** the enquiry process gains an actual transport (an API endpoint
  that accepts recipient requests, rather than a property naming a route) — that
  turns a document property into an attack surface with its own authorization
  model, and belongs with sbx-gov-02.

### Gap 51 — SPDX writer and validator (`tools/compliance/spdx_writer.py`)

**Module:** `tools/compliance/spdx_writer.py` (sbx-fmt-01, mirrored at
`icdev/tools/compliance/spdx_writer.py`), imported by
`tools/compliance/sbom_generator.py`.

**Ingress path:** In its library role the module only ever sees the CycloneDX
document ICDEV itself just built, which is first-party. Its CLI is the
untrusted surface: `--validate` and `--compare` read an SBOM JSON document from
an operator-supplied path, and that document may have been produced by another
vendor's tool or handed over by a supplier. The vendored SPDX 2.3 schema at
`context/compliance/schemas/spdx-2.3.schema.json` is first-party content
committed to the repo.

- **Decision:** **bypass-documented**
- **Rationale:** The module translates and validates; it never executes. Its
  total contact with untrusted content is `json.load`, dict/list traversal, one
  anchored character-class regex used to sanitize SPDX identifiers, and
  `jsonschema.Draft7Validator` over a schema that is read from disk rather than
  fetched. There is no `exec`/`eval`/`compile`, no `subprocess`, no `pickle`, no
  `yaml.load`, no SQL and no network call — validation is deliberately offline
  so it behaves identically in an air-gapped enclave, which is why the official
  schema is vendored rather than resolved from `spdx.org` at runtime.
- **Guardrails:**
  - `jsonschema` is never given a `$ref`-resolvable remote schema: `load_schema`
    reads one local file and the validator is constructed directly from it, so a
    hostile document cannot steer schema resolution anywhere.
  - Every value copied out of the source document is coerced with `str()` before
    it reaches an SPDX field, and identifiers pass through `_sanitize_id`, which
    admits only `[A-Za-z0-9.-]`. A component name cannot forge an `SPDXRef-`
    collision: `_spdx_id` de-duplicates against the identifiers already issued.
  - Malformed input degrades rather than aborting. A non-dict component, a
    non-dict property entry, or an annotation whose comment is not JSON is
    skipped; a dependency edge naming a `bom-ref` that is not in the document is
    dropped rather than emitted as a dangling relationship.
  - A missing `jsonschema` is reported as a validation **error**, not as a pass.
    A validator that silently approves everything is worse than none.
  - Nothing the module reads reaches SQL, a filesystem path, or a shell. The
    only path it writes is the one the caller passes to `write_spdx`.
  - `tests/test_sbom_spdx_format.py` pins the deliberate failure modes: a
    document with a field removed fails validation, a broken parity check fails,
    and edges to absent components produce no relationships.
- **Revisit if:** the module gains an SPDX *parser* that maps a third-party
  document back into ICDEV's component model — that is sbx-fmt-02's ingest
  parity work and a materially different posture, because the values would then
  reach the database rather than only a report.

### Gap 52 — Agent-node tool authorization gate (`tools/studio/executors/agent_tool_gate.py`)

**Module:** `tools/studio/executors/agent_tool_gate.py` (+ `icdev/` mirror) —
decides which tools a Studio `node_type: agent` step may offer a model and which
calls that model may actually make (task hgx-agent-02, gate `AGENT-WF-001`).

**Ingress path:** Two inputs, from two different trust levels. The **policy** is
the `agent_workflow_tools` section of `args/security_gates.yaml` — repo-authored,
first-party, read with `yaml.safe_load`. The **tool name and arguments** come
from an LLM mid-loop: `hook(tool_name, tool_input)` is called by
`run_agent_loop` with whatever the model emitted. That is
**model-generated content**, and the whole point of this module is that it is
treated as such.

- **Decision:** **trusted-first-party** (policy) + **bypass-documented**
  (model-supplied names/arguments — used as dictionary keys and hashed, never
  resolved, executed, or interpolated).
- **Rationale:** No model-supplied string reaches an interpreter, an import, a
  path, a shell, or SQL from this module. `tool_name` is used only as a set
  membership test against the two committed allowlists and as a key into
  `tool_limits`; a miss is a refusal, so the reachable set is a fixed nine-name
  list, not an open namespace. `tool_input` is never inspected for meaning at
  all — it is passed to `params_digest()`, which canonicalises it with
  `json.dumps(..., default=repr)` and returns a SHA-256 hex digest. The digest,
  not the arguments, is what reaches the parameterised audit INSERT, so an
  agent's `write_file` content cannot appear in the audit trail or steer the
  query. There is no `exec`/`eval`/`compile`, no `subprocess`, no `importlib`
  driven by model output, no filesystem path built from model output, and no
  network call. The handlers the authorized call eventually reaches are the
  worktree toolset's (`tools/genesis/rubric_build_tools.py`), which resolve and
  traversal-guard every path inside the step's `work_dir` — that confinement is
  theirs, not this module's, and is unchanged by it.
- **Guardrails:**
  - **Default-deny, fail-closed policy** — `load_policy()` refuses a section
    whose `default` is not `deny`, and refuses when no section is readable at
    all (`agent_gate_policy_unavailable`). `agent_executor.apply_tool_gate`
    turns that into `agent_step_gate_unavailable`: no policy means **no
    toolset**, never an unbounded one.
  - **Enforced twice** — `authorize_toolset()` withholds an unauthorized tool
    before it is described to the model; `build_gate_hook()` re-checks every
    call before the handler runs. The second layer is the one that decides, so a
    model naming a tool it was never offered is refused rather than dispatched.
  - **Human gate on anything mutating** — `write_file` / `patch_file` /
    `run_command` are `requires_approval`, so the first call parks an
    `awaiting_approval` step row and blocks. No run to park a gate on is a
    refusal, not a pass.
  - **Caller IL / RBAC** — `check_caller_authorized()` refuses a caller below
    the tool's declared `min_il` (or holding an unrecognised level — the gate
    does not guess) or missing a required role. `run_command` is held at IL5.
  - **Composed with, not substituted for, the reversibility gate** — the hook
    chains to `tools/agent_runtime/approval_gate.py`; a call must clear both.
  - **Append-only audit with digested arguments** — every decision (allowed,
    refused, pending_approval) writes one `studio_mcp_dispatch_audit` row via
    the existing `record_dispatch_audit`, classification-marked from the
    caller's IL. The write is best-effort and never changes the decision.
  - **Regression tests** — `tests/studio/test_agent_tool_gate.py` (28 tests)
    covers the allowlist refusal, the parked/approved/rejected/undecided human
    gate, the IL and role refusals, gate-ordering, the fail-closed policy paths
    and the executor wiring; `tests/test_dwo_agent_allowlist.py` (17) pins the
    policy data, the mirrors, and that every `block_on` condition is actually
    raised somewhere in the module.
- **Revisit if:** the policy becomes tenant-editable or derivable from anything
  other than a committed repo file; `tool_input` starts being inspected,
  interpolated into a path/command, or stored verbatim; registry-backed bundles
  become dispatchable from an agent node (the dispatch path then needs its own
  entry — this one covers authorization only); or the allowlist is made
  default-allow → re-decide as **sandboxed**
  (`tools/security/sandbox_executor.py`).

### Gap 53 — SBOM component licensing (`tools/compliance/component_licenser.py`)

*(There are two entries numbered 50 above: `sbom_revision` and `unknown_information`
landed from sibling `sbx` branches that each allocated the next number concurrently.
Left as-is rather than renumbered — the headings are referenced from those PRs.)*

**Module:** `tools/compliance/component_licenser.py` and its data module
`tools/compliance/spdx_license_data.py` (sbx-fld-04), imported by
`tools/compliance/sbom_generator.py`. The license-reading additions to
`tools/compliance/dependency_resolver.py` are covered here too, since they are the
ingress that feeds it.

**Ingress path:** Three, all third-party by construction. (1) A **license string declared
by a dependency manifest** — `package-lock.json` `license`/`licenses` — which is
attacker-controlled if a registry account or a lockfile is. (2) **Installed Python
distribution metadata**, read as text from `*.dist-info/METADATA` via
`importlib.metadata.PathDistribution`. (3) The **project's own manifests**
(`pyproject.toml`, `package.json`, `Cargo.toml`, `pom.xml`), read whole off disk by
`project_license_from_manifests()` to resolve the document's target component.

- **Decision:** **bypass-documented**
- **Rationale:** No ingress reaches an execution or deserialization path. A declared
  license is consumed as text: it is tokenized on whitespace and parentheses, each token
  is looked up in a closed frozenset of SPDX identifiers, and an unrecognized token is
  *reported* as a license name, never dispatched on. Manifest files are read with
  `Path.read_text()` and matched with anchored regular expressions and `json.loads` —
  there is no `exec`/`eval`/`subprocess`/`os.system`/`pickle`/`yaml.load` in either
  module, no TOML/XML parser is instantiated, and no file a manifest names is ever opened
  (a `license = { file = "…" }` pointer is carried through as text, not followed).
  `PathDistribution` parses METADATA as text and imports nothing from the target
  environment, which is the same posture sbx-cov-01 already recorded for it.
- **Guardrails:**
  - The SPDX License List is **vendored**, not fetched and not imported from a
    third-party package, so the set of identifiers ICDEV will emit cannot change under a
    dependency upgrade or a network response. It is data with no behaviour.
  - Validation is **allow-list only and fails soft in the safe direction**: an identifier
    absent from the vendored set can only cause a license to be emitted as a *name*
    instead of an SPDX id. A stale list can never cause an unvalidated id to be emitted,
    which is the direction that would matter.
  - A `License:` metadata field that is multi-line or longer than `_LICENSE_FIELD_MAX`
    (120 chars) is discarded rather than carried, so a distribution that pastes its whole
    license body — or a crafted one that pastes anything else — cannot inject unbounded
    third-party text into the SBOM as a license *name*.
  - `project_license_from_manifests()` cannot raise: an unreadable or malformed manifest
    is treated as an absent one, so a hostile project directory cannot abort SBOM
    generation for the ~25 call sites and the blocking `bdc_canvas` gate that consume the
    document. `_python_metadata_license` is likewise called inside the resolver's existing
    per-distribution `try`.
  - Every regex is anchored or non-greedy and bounded by a literal delimiter; none is
    built from input.
  - `tests/test_sbom_component_license.py` pins the rejection set (invented identifiers,
    a license used as an exception, malformed expressions), the malformed-manifest path,
    the metadata-length guard, and the guarantee that every emitted SPDX identifier is on
    the vendored list.
- **Revisit if:** the module starts *following* a license-file pointer (`license-file`,
  `SEE LICENSE IN <file>`) and reading that file's text — that is a new ingress with a
  path-traversal question this decision does not cover; or if license data begins arriving
  over the network from a registry API rather than from a manifest already on disk.

### Gap 54 — SBOM component hashing (`tools/compliance/component_hasher.py`)

**Module:** `tools/compliance/component_hasher.py` (sbx-fld-03), imported by
`tools/compliance/sbom_generator.py`. The digest-reading and artifact-locating additions
to `tools/compliance/dependency_resolver.py` are covered here too, since they are the
ingress that feeds it.

**Ingress path:** Three, all third-party by construction. (1) **Digest strings declared
by a lockfile** — npm/yarn `integrity`, `Cargo.lock` `checksum`, NuGet `sha512` /
`contentHash`, a Python lock's `sha256:` file hashes, `go.sum` `h1:` lines — every one
attacker-controlled if a registry account or a lockfile is. (2) **Artifact bytes**: this
is the first module in the SBOM pipeline that opens a third-party binary and reads it end
to end, namely a jar in the Maven local repository. (3) `--validate` reads a CycloneDX
JSON SBOM from an operator-supplied path, which may have come from another vendor's tool.

- **Decision:** **bypass-documented**
- **Rationale:** The artifact is read as an opaque byte stream and fed to `hashlib` — it
  is never unpacked, never parsed, never imported and never executed. A jar is a zip and
  this module has no zip machinery; `hash_file` opens in `"rb"`, iterates fixed-size
  chunks into a digest object and returns hexadecimal. Declared digests are consumed as
  text: the algorithm token is looked up in a closed dict of IANA names and the value is
  either hexadecimal-validated by an anchored regex or `base64.b64decode(validate=True)`.
  There is no `exec`/`eval`/`subprocess`/`pickle`/`yaml.load`/`zipfile`/`tarfile` in the
  module, and no network call of any kind — recomputation is a local filesystem read, so
  it behaves identically in an air-gapped enclave.
- **Guardrails:**
  - The IANA Hash Function Textual Names registry is **vendored**, and validation is
    **allow-list only**. An unrecognized algorithm name can only cause the unknown
    marker to be emitted; it can never cause an unvalidated name to reach a document.
    Because approval is tracked separately from registration, a `md5`/`sha-1` digest is
    recognized and *refused*, not silently passed through.
  - A declared digest is length-checked against its own algorithm before adoption, so a
    crafted lockfile cannot get a short or oversized value emitted as the element.
    `b64decode(validate=True)` rejects any character outside the standard alphabet.
  - `hash_file` returns `""` rather than raising on any `OSError`, so an artifact that
    is unreadable, a dangling symlink, or removed between resolution and generation
    degrades to the unknown marker instead of aborting the document the ~25 call sites
    and the blocking `bdc_canvas` gate consume.
  - Artifact paths are **composed from the component's own coordinates** under a
    caller-supplied or environment-supplied root (`MAVEN_REPO_LOCAL` / `~/.m2`), and are
    hashed only when `Path.is_file()` holds — a directory or a device node is not read.
    No path is taken verbatim from third-party content.
  - Reads are chunked at 1 MiB, so a hostile or merely enormous artifact cannot be used
    to exhaust memory.
  - A digest is only ever *emitted*, never dispatched on, and reaches SQL solely as a
    bound parameter through `_persist_components`.
  - `tests/test_sbom_component_hash.py` pins the refusal set (unapproved algorithm,
    unregistered name, mislabelled length, non-artifact digest, ambiguous multi-artifact
    lock) and has a dedicated section for the artifact-inaccessible path.
- **Revisit if:** the module starts reading *inside* an artifact — computing per-entry
  digests from a jar or wheel, or following a manifest within it — which introduces
  archive parsing and a zip-slip question this decision does not cover; or if digests
  begin arriving over the network from a registry or a transparency log rather than from
  a lockfile already on disk.

### Gap 55 — SBOM Component Identifiers derivation and validation (`tools/compliance/sbom_identifiers.py`)

**Module:** `tools/compliance/sbom_identifiers.py` (sbx-fld-05), imported by
`tools/compliance/sbom_generator.py`.

**Ingress path:** Two. (1) As a library it receives component dicts built by
the generator's manifest parsers from a target project's `requirements.txt`,
`package.json`, `pom.xml`, `go.mod`, `Cargo.toml`, `*.csproj` and friends —
third-party content by definition, since the whole point is to inventory
someone else's dependency tree. (2) `--validate` reads a CycloneDX JSON SBOM
from an operator-supplied path, which may have been produced by another vendor's
tool rather than by ICDEV.

- **Decision:** **bypass-documented**
- **Rationale:** The module treats every input as an opaque string. Its total
  contact with untrusted content is `str.lower`/`partition`/`split`, character
  iteration, seven anchored `re` patterns, `hashlib.sha256`, `uuid.uuid5`,
  `json.loads` (building data, never code) and `json.dumps`. There is no
  `exec`/`eval`/`compile`, no `subprocess`, no `importlib`, no `pickle` or
  `yaml.load`, no SQL, no network call, and no filesystem path derived from
  content — the CLI opens exactly the one path the operator named and writes
  nothing. A malicious package name is escaped into a CPE attribute or rejected
  by the validator; it is never interpreted.
- **Guardrails:**
  - Identifier values are only ever *emitted or compared*, never dispatched on.
    `validate_identifier` is a pure function returning a string or `None`.
  - `split_cpe` is a hand-rolled character scanner with no backtracking, and
    every regex is anchored with bounded quantifiers, so a hostile package name
    cannot drive catastrophic backtracking.
  - `identifiers_from_json` catches `ValueError`/`TypeError` and returns an
    empty list, so a corrupt `identifiers_json` column degrades to "no
    identifiers" — which the validator then reports as a conformance failure
    rather than passing silently.
  - `_persist_components` in the generator binds every value as a parameter;
    no identifier is ever interpolated into SQL.
  - `tests/test_sbom_component_identifiers.py` exercises the malformed-input
    paths directly, including a package name carrying a `:` that would
    otherwise tear a CPE string in half.
- **Revisit if:** the module gains artifact reading to compute real OmniBOR
  gitoids or SWHIDs — hashing archive bytes is a different posture from string
  manipulation, and sbx-fld-03's hasher deliberately stopped short of it — or if
  `--validate` grows a `--fix` mode that writes back into an SBOM it parsed, or
  if `--component` starts accepting anything other than coordinates.

### Gap 56 — SBOM distribution and version-specific retrieval (`tools/compliance/sbom_distribution.py`)

**Module:** `tools/compliance/sbom_distribution.py` (sbx-gov-02, mirrored at
`icdev/tools/compliance/sbom_distribution.py`), backing three routes in
`tools/supply_chain/blueprint.py`.

**Ingress path:** Two, and only one of them is first-party.

1. **The request.** A `project_id` and a `version` arriving from an
   unauthenticated HTTP caller, used to look up an `sbom_records` row.
2. **The artifact.** The bytes at `sbom_records.file_path`. Today ICDEV writes
   every such row itself, so the artifact is first-party — but the module is
   explicitly built to hold third-party SBOMs too (the 2026 standard is aimed at
   organizations that *procure* software as much as those that produce it), so
   it is treated as untrusted content, not as its own output.

- **Decision:** **bypass-documented** (parameterised lookup plus a byte-for-byte
  file read; no execution path and no parse on the served path)
- **Rationale:** The retrieval path does not parse the artifact at all. It
  `read_bytes()`s the file and streams it — deliberately, because sbx-sig-01
  signs those exact bytes and re-encoding would break the recipient's signature
  check. There is therefore no parser between a hostile SBOM and the response.
  The only place the document *is* parsed is `document_markings()`, which
  `json.loads` a copy purely to echo the classification and distribution
  statements into response headers; it is wrapped so any malformed document
  yields empty markings rather than an error, and its output is never dispatched
  on. `conformance()` parses too, but only to hand the document to sbx-sig-02's
  validator, and its failure is logged and swallowed rather than propagated.
  The module contains no `subprocess`, `os.system`, `exec`, `eval`,
  `__import__`, `pickle` or `yaml.load`, and no network client.
- **Guardrails:**
  - Both request-derived values reach SQL only as bound parameters
    (`resolve_record` uses `%s` placeholders exclusively) and never as
    formatted-in text; the only interpolated fragments are the module's own
    `_RECORD_COLUMNS` constant and a column name chosen from a two-element
    literal tuple.
  - `file_path` is read from the database, never from the request, so a caller
    cannot address an arbitrary file. A row whose path is missing from disk
    yields a 404 through `ArtifactUnavailable`, not a traceback.
  - `evaluate_access` gates every byte: unauthenticated is 401, a role with no
    supply-chain need is 403, and an artifact whose classification is not
    dominated by the caller's clearance is withheld. Both legs are audited.
  - The catalog and version-index responses strip `file_path`, so the host's
    directory layout is not published to anyone who can reach the page.
  - `tests/test_sbom_distribution.py` covers the deny legs at the HTTP boundary
    — not merely on the helper — alongside the allow legs that the 2026 element
    requires to keep working.
- **Revisit if:** the module starts *validating* or *rewriting* the artifact on
  the served path (that would put a parser back in front of hostile bytes and
  break the signature guarantee at the same time), or if retrieval grows a
  fetch-by-URL mode that pulls an SBOM from a remote registry — that is an SSRF
  surface this module does not currently have.

### Gap 57 — SBOM Component Name derivation and validation (`tools/compliance/component_names.py`)

**Module:** `tools/compliance/component_names.py` (sbx-fld-06), imported by
`tools/compliance/sbom_generator.py`.

**Ingress path:** Two, and they are the same two as Gap 55's. (1) As a library it
receives component dicts built by the generator's manifest parsers from a target
project's `requirements.txt`, `pyproject.toml`, `package.json`, `pom.xml`,
`go.mod`, `Cargo.toml` and `*.csproj` — third-party content by definition, since
the point is to inventory someone else's dependency tree. A package name is
attacker-influenced in exactly the way a typosquat is. (2) `--validate` reads a
CycloneDX JSON SBOM from an operator-supplied path, which may have been produced
by another vendor's tool.

- **Decision:** **bypass-documented**
- **Rationale:** Every input is treated as an opaque string and every output is a
  rewriting of one. The module's total contact with untrusted content is
  `str.strip`/`lower`/`split`/`rsplit`, three anchored `re` patterns,
  `urllib.parse.unquote`, `json.loads` (building data, never code) and
  `json.dumps`. There is no `exec`/`eval`/`compile`, no `subprocess`, no
  `importlib`, no `pickle` or `yaml.load`, no SQL, no network call, and no
  filesystem path derived from content — the CLI opens exactly the one path the
  operator named and writes nothing. A hostile package name becomes a string in a
  property value; it is never interpreted, never dispatched on, and never used to
  select a code path.
- **Guardrails:**
  - Derivation is closed: five kinds, fixed in `NAME_KINDS`, each a mechanical
    transform of a field the component already carries. There is no lookup table
    of "also known as", no fuzzy matching and no registry consult, so a name
    cannot be introduced from outside the component record.
  - `validate_names` rejects an alternate whose kind is outside `NAME_KINDS`, so
    a third-party SBOM cannot smuggle in a property that a downstream reader
    would treat as an ICDEV-derived name.
  - `_PURL_HEAD`, `_PEP503_SEPARATORS` and the qualified-name join are anchored
    with bounded quantifiers; there is no nested quantifier for a hostile name to
    drive into catastrophic backtracking.
  - `names_from_json` catches `TypeError`/`ValueError` and degrades to "no
    alternates" rather than raising, so a corrupt stored value cannot take down a
    generation run.
  - The generator passes the `Disclosure`, so a withheld or unknown name emits no
    alternates at all — the redaction cannot be undone by a derived spelling.
  - `_persist_components` binds every value as a parameter; no name is ever
    interpolated into SQL.
  - `tests/test_sbom_component_names.py` exercises the malformed paths directly —
    an unrecognised kind, a repeated alternate, an alternate that repeats the
    primary, unreadable JSON, and a purl whose namespace contains an `@`.
- **Revisit if:** alternates begin arriving from a registry, an advisory feed or
  another vendor's SBOM rather than being derived from the component's own
  coordinates — accepting a name from outside the record is a different posture
  from rewriting one inside it — or if `--validate` grows a mode that writes back
  into an SBOM it parsed.

### Gap 58 — SBOM dependency graph construction and validation (`tools/compliance/dependency_graph.py`)

**Module:** `tools/compliance/dependency_graph.py` (sbx-cov-02), imported by
`tools/compliance/sbom_generator.py` and `tools/compliance/sbom_conformance_gate.py`.

**Ingress path:** Two, and they are the same two as Gap 57's. (1) As a library it
receives resolver-shape component dicts built from a target project's lockfiles
and manifests — third-party content by definition, since the point is to
inventory someone else's dependency tree. Both the component metadata and the
*edge set* are attacker-influenced: a hostile `package-lock.json` chooses the
names, the versions and which package points at which. (2) `--validate` reads a
CycloneDX JSON SBOM from an operator-supplied path, which may have been produced
by another vendor's tool.

- **Decision:** **bypass-documented**
- **Why:** the module evaluates nothing. It reads six string fields per
  component, compares and hashes them, and walks an integer-indexed adjacency
  map. There is no `eval`, no `subprocess`, no import driven by input, no
  filesystem write, and no network call — `--validate` opens exactly the one
  path it was given. The untrusted content reaches only `str()`, `sorted()`,
  set membership and `hashlib`, so the sandbox would be guarding arithmetic.
- **Residual risk and what bounds it:**
  - **Graph blow-up.** A malicious lockfile can declare a very deep or very
    dense tree. `detect_cycles` and `_reachable` are both iterative, so depth
    cannot exhaust the interpreter stack —
    `test_cycle_detection_terminates_on_a_deep_chain` pins that at 3000 levels.
    Cost stays linear in nodes plus edges; the resolver that produced the set is
    the component that bounds its size.
  - **Cycles.** A dependency cycle is legal input, not an error, and is detected
    and reported rather than followed. Nothing in the module recurses over the
    graph, so a cycle cannot hang generation.
  - **Ref collision.** A crafted component set cannot make two nodes share a
    bom-ref: collisions are broken deterministically and
    `validate_dependency_graph` independently rejects a document in which two
    components carry the same ref.
  - **Dangling edges.** An edge naming a component the resolver never emitted is
    dropped and counted, never emitted as a `dependsOn` that resolves to
    nothing.
  - `dependency_rows` returns bound parameters; no ref or relationship type is
    interpolated into SQL, and `relationship_type` is additionally constrained
    by the CHECK migration 20260809232803 installs.
  - `tests/test_sbom_dependency_graph.py` exercises the malformed paths directly
    — a dangling edge, a duplicated entry, an unrooted graph, an unreachable
    component, two components sharing a ref, a non-object entry, a self-cycle
    and mutual recursion.
- **Revisit if:** the module gains a mode that fetches a linked SBOM per
  dependency rather than embedding (the standard permits linking, and ICDEV
  deliberately does not — that would add an SSRF surface this module does not
  currently have), or if edges begin arriving from an advisory feed or registry
  rather than from the resolver's own reading of a lockfile.
### Gap — AGOV CASE bundle export (agov-case-02)
- **File:** `tools/agent_case/case_bundler.py`
- **Risk:** The exporter reads rows an agent's own activity produced — hook
  payloads, audit details, approval-gate `reason`/`detail` free text, and
  `affected_files` path lists — and writes them to a directory that is then
  handed to someone else. Two distinct exposures: content the agent influenced
  reaching a recipient (a leak), and content the agent influenced steering the
  exporter (an execution surface).
- **Decision:** **trusted-first-party**, with the leak side handled by contract
  rather than by sandboxing
- **Rationale:** Every value read comes from ICDEV's own append-only tables via
  a fixed column allowlist, and nothing read is ever executed, resolved or
  dispatched on. There is no `exec`/`eval`, no `subprocess`, no `importlib` on
  a database value, no `pickle`/`yaml.load`, and no network call. Record content
  is serialized with `json.dumps` and hashed; it never selects a code path. The
  one place external input could become behaviour — a filesystem path out of
  `audit_trail.affected_files` — is deliberately **not** followed: see below.
- **Guardrails:**
  - `collect_artifact_paths` records artifact paths as *referenced*, never
    resolved. The exporter does not call `open`, `stat` or `resolve` on a path
    that came out of the database, so `../../etc/shadow` in an `affected_files`
    cell is copied into the bundle as a string and nothing more. This is also
    the only route by which a transcript could re-enter a bundle that queried no
    transcript table, which is why `contents_included` is a fixed `false`.
  - `TRANSCRIPT_SOURCES` names the conversation-bearing tables, verified against
    the live DDL, and no query in this module touches one. Exclusion is by closed
    allowlist, not by filtering after the fact. Two of them —
    `intake_conversation` and `ci_conversation_turns` — carry both a `session_id`
    and raw turn `content`, so a join one column wider would pull the exported
    session's own conversation into forensic evidence;
    `tests/test_agov_case_bundle.py` seeds a canary into all four transcript
    tables, asserts the two session-keyed rows really are reachable, and then
    asserts the canary is absent from the bundle's bytes.
  - Operator-writable free text (`agent_approval_log.reason`, `.detail`) passes
    through `tools/llm/output_redactor.py::redact` before export, and a row whose
    text changed is flagged `redacted: true` so a reader can distinguish "nothing
    sensitive" from "something removed".
  - Signed and hash-chained values (`hook_events.payload`, `audit_trail.hash`)
    are exported verbatim on purpose: rewriting one would make an untampered
    bundle report as tampered. The context header states this explicitly rather
    than leaving a recipient to infer it.
  - The endpoint header carries the storage backend NAME, never a DSN — a
    connection string can carry a password.
- **Revisit if:** the exporter gains the ability to copy artifact BYTES into the
  bundle, to resolve or stat a database-supplied path, to accept an
  externally-authored bundle as input, or to export a table not in the allowlist
  — any of those turns "read, redact and hash" into a genuine trust boundary.
### Gap — AGOV CASE timeline, bundler and CLI (agov-case-04)
- **File:** `tools/agent_case/session_timeline.py`, `tools/agent_case/case_bundler.py`,
  `tools/agent_case/cli.py`
- **Risk:** Two distinct inputs. (1) The `session_id`, `--since`/`--until` and
  `--limit` an operator supplies reach SQL. (2) The row contents these modules
  export — `hook_events.payload` above all — are agent-authored, so a hostile
  agent could try to make its own recorded activity misbehave on the way out.
- **Decision:** **bypass-documented**
- **Rationale:** Neither module executes anything it reads. The timeline runs
  parameterized SELECTs and hands rows to `json.dumps`; the bundler adds
  `hashlib.sha256` over bytes and file writes into the operator-named bundle
  directory. There is no `exec`/`eval`/`compile`, no `subprocess`, no
  `importlib`, no `pickle` or `yaml.load`, and no network call. Row content never
  selects a code path — a payload is copied verbatim and hashed, never parsed for
  behaviour, and specifically never `json.loads`ed on the export path, because the
  HMAC is over the raw stored text and re-serializing it would break every
  signature. (agov-case-01 added a `json.loads` on the *display* path only; see
  the entry below. `entry["record"]` still carries the raw stored text and is
  what the bundler writes and the verifier hashes.)
- **Guardrails:**
  - Every value from the caller is bound as a parameter, never interpolated:
    `session_id`, `since` and `until` go through `sql_placeholder(conn)`, and
    `--limit` is coerced with `int()` before it reaches the `LIMIT` clause. The
    only interpolated identifiers are table and column names drawn from the
    module-level `SOURCES` constant, which no input can reach.
  - Column selection is an explicit allowlist per source, resolved against the
    live table's actual columns. A later `ALTER TABLE` cannot silently widen a
    forensic export, and a column that migration 149 has not added yet is dropped
    from the SELECT rather than failing the whole query.
  - `build_case_bundle` refuses to write into a directory that already holds a
    `manifest.json` unless `overwrite=True` (`--force`), so an export cannot
    half-replace an existing evidence bundle and leave a manifest describing some
    files and not others.
  - Bundle members are written with `newline="\n"` and `sort_keys=True` so a
    bundle written on Windows verifies byte-identically on Linux; the manifest
    hashes raw bytes and CRLF would break every member digest.
  - `tests/test_agov_case_cli.py` round-trips a real bundle through the separate
    verifier and asserts all three layers PASS, tampers a member and asserts it is
    named, and asserts no CRLF reaches any member file.
- **Revisit if:** the timeline gains a free-text filter that is interpolated
  rather than bound, the bundler starts reading an existing bundle it did not
  write (that is the verifier's posture, covered above), or a member is ever
  fetched over the network instead of from the database.

### Gap — AGOV CASE timeline display projection and redaction (agov-case-01)
- **File:** `tools/agent_case/timeline_redaction.py`, and the operand/redaction
  path added to `tools/agent_case/session_timeline.py`
- **Risk:** This is the first code in the CASE package that *parses* agent-authored
  content rather than copying it. `hook_events.payload` is written by the agent
  being investigated, so it is hostile-by-assumption: it can be malformed JSON, a
  deeply nested structure, a huge string, or prose crafted to look like a command
  the agent never ran. The parsed values are then rendered to an operator and, via
  the bundler, carried to another machine.
- **Decision:** **trusted-first-party** for the parse, **bypass-documented** for
  the redaction stack it calls.
- **Rationale:** The parse is `json.loads` into plain data followed by dictionary
  lookups against a module-level allowlist. No parsed value ever selects a code
  path, names a module, becomes a format string, or reaches a subprocess; the
  worst a malformed payload achieves is no operands. The redaction stack it calls
  (`tools/redaction/detector.py` + `anonymizer.py`) is the platform's existing
  sanitizer and is already covered above; this module constrains it further rather
  than loosening it.
- **Guardrails:**
  - **Allowlist, not filter.** Only `OPERAND_KEYS` (`command`, `file_path`,
    `notebook_path`, `path`, `url`) are read, at the payload top level and one
    level down inside `OPERAND_CONTAINER_KEYS`. `FREE_TEXT_KEYS` — tool output,
    model prose, file contents — are never read, so a command quoted in a tool's
    *output* cannot be rendered as though the agent ran it. A module-level guard
    raises at import if a later edit moves a free-text key into the allowlist,
    and `tests/test_agov_case_timeline.py` asserts the two sets stay disjoint.
  - **Non-strings are not coerced.** Only `str` values become operands; a dict,
    list or int is skipped rather than `str()`-ed into something to regex over.
  - **Malformed input yields nothing, never an exception.** `json.loads` failures
    and non-dict payloads return the operands found so far.
  - **No LLM and no clock in the path.** The detector's Ollama NER layer is
    switched off here. It is a network call to a generative model, and one
    non-reproducible field would make the timeline unusable as the basis of a
    bundle manifest — `test_two_runs_over_identical_data_are_byte_identical`
    is the check that keeps it out.
  - **Redaction is a projection, not a mutation.** Masked strings land in
    `entry["display"]`; `entry["record"]` is untouched, which is what lets
    `bundle_verifier` still re-compute the `hook_events` HMACs and the
    migration-149 hash chain. Asserted by
    `test_redaction_does_not_touch_the_record_the_verifier_hashes`.
  - **Reads do not write.** `TimelineRedactor` disables the anonymizer's audit
    INSERT by default, so building a timeline stays a read; the bundler turns it
    on at the moment an actual disclosure happens.
  - The credential patterns this uses are opt-in platform-wide
    (`detection.secret_patterns.enabled`, default `false`) so enabling them for
    the timeline does not change what any existing LLM-egress caller sends —
    `tests/test_redaction_secret_patterns.py` asserts the shipped default is off.
- **Revisit if:** operand extraction moves from an allowlist to a denylist, a
  parsed value is ever used to choose a code path or reach a subprocess, or the
  redactor is given a detection backend that makes a network call.
### Gap — AGOV CASE bundle verification (agov-case-03)
- **File:** `tools/agent_case/bundle_verifier.py` (+ `tools/agent_case/bundle_format.py`)
- **Risk:** A case bundle is, by design, evidence handed over by someone else — an
  auditor verifies bundles that ICDEV did not produce. Every byte read is
  attacker-controllable: `manifest.json`, the record files, and — most sharply —
  the **member paths inside the manifest**, which the verifier is asked to open.
- **Decision:** **bypass-documented**
- **Rationale:** The verifier only reads and hashes. Its entire contact with the
  bundle is `json.loads` (building data, never code), `hashlib.sha256` /
  `hmac.new` over bytes, and `Path.is_file()` / `open(..., "rb")`. There is no
  `exec`/`eval`/`compile`, no `subprocess`, no `importlib`, no `pickle` or
  `yaml.load`, no SQL, no network call, and nothing is written back into the
  bundle — a bundle under verification is never mutated. Content never selects a
  code path: a record's fields are joined into a string and hashed, and the
  result is compared, never dispatched on.
- **Guardrails:**
  - `bundle_format.is_safe_member_path` refuses absolute paths, drive letters,
    NTFS alternate-data-stream `:` syntax, and any `..` segment **before** the
    path is resolved, so a manifest cannot make the verifier read
    `../../etc/passwd`. The refusal is itself a reported finding
    (`unsafe_member_path`), not a silent skip — a manifest that tries this is
    evidence, so the layer fails rather than ignoring the entry.
  - `tests/test_agov_case_bundle_verifier.py` drives four traversal shapes
    (`../../etc/passwd`, `/etc/passwd`, `C:\Windows\win.ini`,
    `records/../../escape.json`) through the real manifest layer and asserts each
    is refused and named.
  - Unreadable or non-JSON members are caught and reported as findings; a corrupt
    bundle produces a report, not a traceback.
  - No secret is read from the bundle. The HMAC key comes only from
    `ICDEV_HOOK_HMAC_SECRET` or `--secret`, and the verifier refuses to fall back
    to the shipped default the writer uses.
- **Revisit if:** the verifier gains the ability to write into or repair a bundle,
  to fetch a member over the network, or to load a bundle-supplied plugin/schema
  — any of those turns "read and hash" into a genuine execution surface.
### Gap 59 — AGOV pre-tool-use detection gate (`tools/agent_detect/gate.py`)

**Module:** `tools/agent_detect/gate.py` (with `tools/agent_detect/rules.py`,
`sequence.py`, `findings.py`), reached from `.claude/hooks/pre_tool_use.py` and
`tools/airgap/hook_compat.py::run_pre_tool_check` via
`tools/hooks/shared_checks.py::check_agent_rules` (agov-det-06).

**Ingress paths — three, and they are not equally trusted:**

1. **Tool-call payloads** (`tool_name`, `tool_input`) — agent-controlled, and the
   most hostile input in the repo: this is literally the thing being policed.
   `normalize_tool_call` copies a handful of string fields out of the payload
   into a flat dict and runs one anchored regex over the command text to lift a
   URL. Values are then compared — `==`, `fnmatch`, `re.search` against
   **operator-authored** patterns, or membership in a parsed argv. No payload
   value is ever compiled as a pattern, executed, interpolated into SQL, or used
   to build a path.
2. **Rule YAML** (`args/agent_rules/**`, `args/agent_rules_enforce/**`) —
   first-party config, and the enforcement directory is operator-controlled by
   design. `yaml.safe_load` only. Conditions are **structured matchers, not an
   expression language**; the design record in `args/agent_rules/README.md`
   states why CEL and a restricted-AST evaluator were both rejected. The only
   operator text that becomes executable-ish is a regex under `command_matches`
   / `url_matches`, compiled by `re.compile` at load, with a compile failure
   invalidating the whole rule rather than degrading it.
3. **The JSON side-cache and the session trail** (`$TEMP/icdev-agent-detect/`) —
   `json.loads` only, never `pickle`. Both are latency artefacts, not trust
   boundaries. See the rationale below.

- **Decision:** **bypass-documented**
- **Rationale:** No `exec`, `eval`, `pickle`, `subprocess`, `os.system`, shell
  invocation or native parser anywhere in the path. The gate reads data, compares
  it against declarative patterns, and appends a row. It has no allow verb — it
  can only ever add a refusal to a call the eight hardcoded checks in
  `shared_checks.py` already allowed — so no rule, however malformed or hostile,
  can widen what an agent may do.
- **Guardrails:**
  - **Enforcement authority is a directory, not a field.** A rule blocks only
    when it sets `enforce: true` **and** lives in the operator directory
    (`args/agent_rules_enforce/`, `ICDEV_AGENT_ENFORCE_RULES_DIR`), which ships
    with no rule files. Shipped-pack matches are forced monitor-only at the
    gate, so `enforce: true` landing in `args/agent_rules/` is inert.
    `tests/test_agov_gate.py` pins it.
  - **The JSON side-cache is never consulted for a blocking decision.** It
    accelerates the monitor-only pack only; the operator directory is always
    read live from YAML and is never given a cache file. A process that could
    write the cache could therefore degrade *detection* — the same thing editing
    `args/agent_rules/` achieves, and that edit is itself matched by
    `tamper.control_surface_write` — but could never suppress or fabricate a
    block. The cache stores documents, not compiled rules, so a cached entry
    still has to survive `compile_rule` on every load.
  - **Fails open, deliberately.** Every other check in `shared_checks.py` encodes
    a fixed reviewed judgement and fails closed. This one runs YAML that may have
    landed five minutes ago, before every tool call, so a rule pack that cannot
    be parsed leaves the session exactly as protected as it was before AGOV.
  - The session trail is bounded twice (a byte-capped tail seek and a line cap),
    is per-session, holds only normalized event fields, and is scratch — losing
    it costs chain detection and nothing else.
  - `findings.record_finding` uses a static column list with `%s` placeholders
    through `get_connection()`; `agent_findings` is in `APPEND_ONLY_TABLES`.
- **Revisit if:** a matcher key is ever added that compiles a pattern from the
  *event* rather than from the rule; if the rule schema grows a `custom_expr` or
  any field evaluated as code; if the side-cache is ever consulted for the
  enforcement directory or switched from JSON to `pickle`; or if the gate gains
  an allow/exempt verb, which would make a rule file able to *weaken* the
  hardcoded blocks rather than only add to them.
### Gap 60 — Agent shell-command parser (`tools/agent_detect/shell_parse.py`)
### Gap 61 — Agent shell-command parser (`tools/agent_detect/shell_parse.py`)
- **File:** `tools/agent_detect/shell_parse.py` (agov-det-02)
- **Risk:** This module's entire input is hostile by assumption — the command
  string an agent asked a shell to run, read back out of `hook_events` /
  `agent_executions`. It is reached from the detection path that agov-det-06
  wires into `.claude/hooks/pre_tool_use.py`, so it sees every command before
  the shell does.
- **Decision:** **bypass-documented**
- **Rationale:** It classifies a command; it never runs one. The module is
  stdlib-only (`hashlib`, `posixpath`, `shlex`, `dataclasses`) and imports
  nothing first-party — deliberately, because the hook is a fresh interpreter on
  every tool call. There is no `subprocess`, `os.system`, `os.popen`, `eval`,
  `exec`, `__import__`, `pickle`, `yaml.load`, no file handle and no network
  client anywhere in it. `shlex` is a pure-Python lexer with no execution path;
  the only other parsing is a hand-written character scan over the same string.
  A sandbox would add process isolation around a function whose worst-case
  output is a wrong string in a dataclass.
- **Guardrails:**
  - `parse_command` cannot raise. Every failure path — including an unforeseen
    lexer fault — returns `parsed=False` with a stable `reason` and NO
    statements, because a parser fault must be unable to fire *or suppress* a
    detection rule.
  - Refusal is total, never partial. A command with command substitution,
    control flow, `eval`, a sequence operator or an unbalanced quote yields
    zero statements, and consumers (`tools/agent_detect/rules.py`) are
    contractually required to decline with it rather than fall back to
    substring matching on the raw command — that fallback is precisely the
    fail-open recorded at `args/agent_approval_policy.yaml`:107-126.
  - Ids are SHA-256 of the command text: deterministic, no clock, no RNG, so
    nothing here can perturb a workflow replay.
  - `tests/test_agov_shell_parse.py::test_the_parser_has_no_execution_path`
    asserts the absence of every execution/IO primitive listed above against
    the module source, and
    `::test_the_module_imports_nothing_first_party` pins the stdlib-only
    property. The claim in this entry is worth exactly what those two tests
    enforce.
- **Revisit if:** the module grows a recursive parse of a nested program
  (`bash -c "..."`), starts resolving a command name against `PATH` on disk, or
  gains a second dialect implemented by shelling out to a real shell for
  tokenization — any of those puts execution or filesystem access back in front
  of hostile input.

### Gap 62 — Agent policy chain (`tools/agent_runtime/policy_engine.py`)

**Module:** `tools/agent_runtime/policy_engine.py` (exa-policy-01).

**Ingress path:** Two, and the first is genuinely untrusted. (1) The **tool
input of every agent tool call**, carried on `PolicyEvent.arguments` and handed
to every policy in the chain — an arbitrary dict authored by an LLM. This layer
sits in front of the same surface `approval_gate.py` does (Gap 46), so it sees
every model-authored argument in the platform. (2) `args/agent_policy_chain.yaml`,
a first-party config naming which registered policies run, in what order.

- **Decision:** **bypass-documented**
- **Rationale:** Same reasoning as Gap 46, and for the same reason: neither
  ingress reaches an execution path. The engine *routes* the event to policy
  functions and combines their verdicts; it never invokes the tool it is
  judging. There is no `exec`/`eval`/`compile`/`subprocess`/`os.system`/
  `pickle`/`importlib` in the module, and it does not even pattern-match the
  arguments itself — the one shipped policy delegates that to
  `approval_gate.classify()`, which Gap 46 already covers. The config is parsed
  with `yaml.safe_load` (never `yaml.load`) and supplies only **names**, which
  are looked up in an in-process registry populated by first-party
  `register_policy()` calls; a config file cannot introduce a callable, an
  import path, or a code string.
- **Guardrails:**
  - **Fail-closed at every layer.** A policy that raises resolves to
    `on_policy_error`, which accepts only `deny` (default) or `ask` — `allow`
    is rejected rather than honoured, so a config typo cannot authorise an
    irreversible action. A nonsense return value or unrecognised effect is a
    DENY. An empty chain is an ASK. A missing or unreadable config falls back to
    the reversibility-only chain, which is itself fail-closed.
  - **A policy named in the config that is not registered resolves to a DENY
    naming itself**, never a silent skip. A chain that quietly drops a policy is
    a chain that has stopped enforcing what its own config says it enforces —
    the declared-but-unconsumed failure the EXA card exists to close.
  - **DENY short-circuits and is never escalated to the approver.** `dry_run`
    and `off` apply to ASK only, so the escape hatch for an escalation is not an
    escape hatch for a refusal.
  - Hard blocks from `.claude/hooks/pre_tool_use.py` are consulted and win
    before any policy runs, so this layer cannot be used to talk past the hook.
  - A **floor** in the config can only raise the chain's answer, never lower it,
    and an unparseable floor is treated as no floor rather than as `allow`.
  - Argument **values never persist and never render**. The audit row is written
    by `approval_gate.record_decision()` — reused, not reimplemented, precisely
    so the key-names-plus-SHA-256 rule has one owner — and `PolicyEvent.__repr__`
    elides argument values so a traceback or debug log cannot leak what the
    audit row was designed not to hold. The `--json` CLI emits policy names,
    effects, reasons and rules only.
  - `tests/test_agent_policy_engine.py` pins all of the above, including that a
    policy after a DENY is never called and that a CUI-shaped argument value
    reaches neither the audit row nor the event repr.
- **Revisit if:** the chain gains a policy source that is not first-party (a
  tenant-supplied or LLM-authored policy would make the callables untrusted
  input), the config starts naming an import path or a code string rather than a
  registered name, or a policy is added that *acts* — remediates, notifies, or
  mutates state — rather than returning a verdict.

### Gap 63 — Three-level policy composition and session state (`tools/agent_runtime/policy_composition.py`)

**Module:** `tools/agent_runtime/policy_composition.py` (exa-policy-02).

**Ingress path:** Three. (1) The **tool input of every agent tool call**, as in
Gap 62 — unchanged, and covered there. (2) A **session-level policy config set
by the END USER**, passed in as a dict by the runtime or read from
`$ICDEV_AGENT_POLICY_CHAIN_SESSION`. (3) An **agent-level policy config set by
the agent author**, from `<profile_dir>/policy_chain.yaml` or
`$ICDEV_AGENT_POLICY_CHAIN_AGENT`.

Ingress (2) is the one Gap 62's "revisit if" named in advance — *"the chain gains
a policy source that is not first-party"* — so it is answered explicitly below
rather than by analogy. So is the second trigger, *"a policy is added that
mutates state rather than returning a verdict"*: `state_updates` does exactly
that.

- **Decision:** **bypass-documented**
- **Rationale:** A session config is lower-trust than the server config, and the
  composition treats it as such — but it still cannot reach an execution path,
  for the same structural reason Gap 62 gives and one more.
  - **A config supplies only NAMES, and cannot introduce a callable.** A session
    or agent config names policies that must already be in the in-process
    registry, populated exclusively by first-party `register_policy()` calls at
    import time. There is no import path, no code string, no `exec`/`eval`/
    `compile`/`subprocess`/`pickle`/`importlib` in the module. A name the
    registry does not hold resolves to a **DENY that names itself**, per level —
    not a skip, and not an attempt to resolve it some other way. So the
    end-user-controlled surface is "which of the admin's policies also run
    against me", which is a request for *more* scrutiny.
  - **Levels are additive, so a lower-trust level can only tighten.** The
    composed answer is the strictest effect any level returned. There is no
    session-level syntax for removing a policy from the agent or server chain,
    for lowering a floor, or for turning a DENY into an ALLOW — not because a
    check rejects those, but because composition never reads a lower level as an
    override. A session ALLOW is indistinguishable from a session abstention.
    That is what makes evaluating the least-trusted level FIRST safe.
  - **State is data written by policies, not by callers.** `state_updates` is a
    closed vocabulary of five actions (`increment`, `decrement`, `set`, `append`,
    `delete`) applied to a JSON-serialisable value; there is no action that
    executes, no key that is interpreted as a path or a name, and the composition
    never copies `PolicyEvent.arguments` into state. So the "policy that acts"
    trigger resolves to "a policy that counts", which is not an execution path.
  - Config files are parsed with `yaml.safe_load` (never `yaml.load`), and an
    unreadable one yields an **empty level** rather than an exception or a
    permissive default.
- **Guardrails:**
  - **The level ORDER is a module constant (`LEVELS`), not a config key.** A
    config that could reorder the levels could put the session level last, so it
    is kept out of reach rather than validated.
  - **Server-only keys are server-only.** `audit` below the server level is
    ignored, so a session cannot stop its own denials being logged. An attempted
    lowering — a softer floor, a disabled policy a stricter level enables, a
    server-only key — is **reported** as a `Relaxation` and logged at WARNING,
    never silently dropped: a key ignored in silence is a key somebody keeps
    writing.
  - **`on_policy_error: allow` is refused at every level, including server.** A
    broken policy is an unanswered question, not an answer.
  - **A malformed `state_update` raises rather than being skipped**, and the
    chain resolves that to `on_policy_error` (DENY). A counter that silently
    fails to increment is a limit that silently never fires — precisely the
    declared-but-unconsumed failure the EXA card exists to close.
  - **A policy cannot mutate state by writing to the event it was handed** —
    `PolicyEvent.session_state` is a snapshot, and `apply_updates` is the only
    writer. Updates apply as each policy returns, so a later policy reads what an
    earlier one wrote within the same call.
  - Hard blocks from `.claude/hooks/pre_tool_use.py` still win before any policy,
    and DENY still short-circuits and is never escalated to the approver.
  - Argument **values never persist and never render**: the audit row is written
    by `approval_gate.record_decision()` (reused, not reimplemented) and the
    `--json` CLI emits levels, policy names, effects, reasons and rules only.
  - Persisted session state (`agent_session_policy_state`, migration
    `20260812054330`) holds only what a policy put there, under `classification
    'CUI'` and the platform RLS predicate, and a missing table degrades to
    in-process state with a WARNING naming the migration — never to an absent
    limit reported as a satisfied one.
  - `tests/test_agent_policy_composition.py` pins all of the above, including
    every attempted-loosening case as its own test, that a session DENY means the
    server level is never consulted, that a rebuilt hook counts against the same
    session, and that a CUI-shaped argument value reaches neither the audit
    detail nor the reason.
- **Revisit if:** a policy config becomes **tenant-supplied or LLM-authored**
  (an end user at the keyboard already has shell access to the repo and the
  reversibility gate in front of them; a policy synthesised by a model is a
  different trust question), a level gains the ability to name an import path or
  a code string rather than a registered name, `state_updates` gains an action
  that does anything other than store a value, or a fourth level is added that is
  evaluated after `server`.

### Gap 64 — Fabric `peer` CLI transport (`tools/blockchain/transports/peer_cli.py`)
- **File:** `tools/blockchain/transports/peer_cli.py` (trust-anchor-01, D-GC-1)
- **Risk:** Spawns the vendor `peer` binary via `subprocess` and parses its
  stdout/stderr for a transaction id. Two ingress questions: what reaches the
  child process's argv, and what the parent does with the child's output.
- **Decision:** **trusted-first-party**
- **Rationale:** Same shape ICDEV already uses to wrap `bandit` and `git`, and
  the shape `args/blockchain_config.yaml` has declared under D-GC-1 since
  GovChain shipped ("Fabric CLI via subprocess (same as SAST wrapping bandit)").
  The binary is operator-installed, not fetched; the operands are ICDEV-computed
  Merkle roots and JSON metadata, not user prose.
- **Guardrails:**
  - argv form with `shell=False` and a fixed subcommand vector
    (`peer chaincode invoke|query`). Chaincode arguments are JSON-encoded into a
    single `-c` operand, so no argument can become an additional argv entry —
    pinned by `test_invoke_builds_argv_form_and_parses_txid`.
  - Bounded timeout from `fabric.cli_timeout_seconds` (60s), with the health
    probe capped at 15s separately so `is_enabled()` cannot stall a page render.
  - `health()` short-circuits on `shutil.which()` and spawns **no** subprocess
    when the binary is absent, which is every CI run
    (`test_health_probe_spawns_no_subprocess_when_binary_absent`).
  - Child output is only regex-scanned for a hex tx id and truncated into a
    reason string; it is never `eval`'d, never executed, and never written to
    disk. An unparseable id yields `tx_id_confirmed: False` rather than a
    fabricated id.
  - The env passed to the child is the ambient environment plus explicitly
    configured `CORE_PEER_ADDRESS` / `env` entries from
    `args/blockchain_config.yaml` — a first-party file.
- **Revisit if:** peer endpoints or `env` blocks become tenant-supplied rather
  than operator-supplied, or if `chaincode_query` output is ever fed to a parser
  richer than `json.loads` / an LLM prompt.

### Gap 65 — floci emulator holds the host Docker socket (`docker-compose.yml`, `floci` profile)

- **File:** `docker-compose.yml` — service `floci` (flx-compose-01); switch at
  `tools/cloud/emulator.py` (flx-seam-01).
- **Risk:** The service bind-mounts the host Docker socket
  (`${FLOCI_DOCKER_SOCKET_MOUNT:-//var/run/docker.sock}:/var/run/docker.sock`).
  **A container holding the host Docker socket is root-equivalent on that
  host** — it can start a privileged container, bind-mount `/`, and read or
  write anything the daemon can. This is not a sandbox escape hatch that might
  theoretically be reachable; it is a deliberate grant, and it is the single
  most consequential line in the compose file. The emulator additionally
  ingests whatever an ICDEV caller sends it (Terraform plans, Lambda bundles,
  S3 objects) and executes container-backed services from those inputs.
- **Decision:** **bypass-documented** — an operator-gated grant, off by
  default, never reached by any ICDEV default path.
- **Rationale:** The grant buys the container-backed services and nothing else:
  Lambda, RDS, ElastiCache, OpenSearch, MSK and ECS/EC2/EKS are implemented by
  floci as *sibling containers*, so without the socket those services cannot be
  emulated at all. The in-process services (S3, DynamoDB, SQS, SNS, ECR, IAM,
  SSM, STS, KMS) need no socket and work with the mount removed. Sandboxing the
  emulator itself is not available: nesting it inside `SandboxExecutor` would
  mean handing the socket to the sandbox instead, which relocates the grant
  rather than removing it. The operator approved the grant on 2026-09-05 for a
  **locally hosted** Docker daemon, on a developer workstation, for
  API-contract testing only.
- **Guardrails:**
  - **The service is behind the `floci` compose profile.** A service carrying a
    `profiles:` key does not start on a bare `docker compose up`, is not in
    `/start`, and is not in the 24-service default set. Starting it is two
    deliberate acts: setting `FLOCI_ENABLED=true` (the ICDEV-side switch) and
    `docker compose --profile floci up -d` (the emulator itself). `icdev enable
    floci` becomes the first of those once flx-compose-02 registers the toggle;
    it is not a command today (verified 2026-09-04 against
    `tools/cli/enable.py::TOGGLES`). Pinned by
    `tests/cloud/test_floci_compose_profile.py::test_floci_is_absent_from_the_default_start_set`,
    which asserts on the *absence of a `profiles` key* across every service
    rather than on the profile string, and carries a negative control so it
    cannot pass over an empty default set.
  - **Loopback-only publication.** Every published port is bound to
    `127.0.0.1`, so a socket-holding container is not reachable off-host. Same
    posture as the `litellm-proxy` profile in the same file. Pinned by
    `test_every_published_port_is_loopback_only`.
  - **The image tag is pinned** (`floci/floci:2.0.1`, never `:latest`) so an
    air-gapped bundle is reproducible and the socket is not handed to an image
    that changed under the deployment. Pinned by
    `test_image_is_pinned_and_never_latest`. Pin by digest (`@sha256:...`) and
    record it in the SBOM before any real deployment.
  - **`FLOCI_DOCKER_DOCKER_HOST` is left unset**, so floci reaches only the
    daemon it was handed. A remote daemon and an internal registry mirror are
    named follow-ons (flx-airgap-02), not silently configured here. Pinned by
    `test_remote_docker_host_is_left_unset`.
  - **The mount source is a distinct variable from the seam's socket
    variable**, and this is a correctness guardrail, not a naming preference.
    `FLOCI_DOCKER_SOCKET` is read by `emulator.docker_basis()` to answer how the
    ICDEV *Python process on the host* would reach the daemon; the compose
    mount source is a path inside Docker Desktop's Linux VM namespace that does
    not exist on the Windows filesystem. MEASURED 2026-09-04 on the Windows
    host: giving them one name makes `docker_backed()` return `False` and
    `service_supported("lambda")` return `False` for a Lambda that works — a
    fabricated refusal, the same defect class as a fabricated `[]` pointing the
    other way. Pinned on a POSIX *and* a Windows platform by
    `test_mount_variable_is_not_the_seams_socket_variable`.
  - **Persistent state is gitignored.** `FLOCI_STORAGE_MODE=persistent` writes
    buckets, queues, tables and Lambda bundles under `./data/floci`, and this
    repo is PUBLIC. `data/floci/` is ignored — anchored, never a bare `data/`,
    which once silently dropped a code directory here. Pinned by
    `test_emulator_state_is_gitignored` and
    `test_the_pattern_is_anchored_and_not_a_bare_data_directory` through git's
    own `check-ignore` predicate rather than a substring search, with a
    negative control asserting a tracked file under `data/` stays visible.
  - **Never a source of a performance, cost or capacity claim.** An emulator
    reproduces the AWS *API contract*, not its performance characteristics —
    the standing guard from
    `docs/spikes/twx-spk-01-localstack-go-no-go.md`, which the flx project
    supersedes on the air-gap question **only** (floci carries no auth-token
    image).
- **Revisit if:** the profile is started anywhere automatically (a reflex, a
  CI job, `/start`, or a `depends_on` from a default-profile service); the
  socket mount moves out from behind the profile onto a default service;
  `FLOCI_DOCKER_DOCKER_HOST` is set to a **remote** daemon (a different trust
  question — the grant then crosses a host boundary, and flx-airgap-02 must
  carry its own decision here); the emulator is exposed off-loopback or run on
  a shared/CI host rather than a developer workstation; or floci begins
  accepting tenant-supplied rather than operator-supplied input. Any one of
  those makes **bypass-documented** the wrong decision and requires re-deciding
  between `sandboxed` and refusing the grant.

### Gap 66 — floci-az emulator holds the host Docker socket (`docker-compose.yml`, `floci-az` profile)

- **File:** `docker-compose.yml` — service `floci-az` (flx-az-01); switch at
  `tools/cloud/emulator_az.py`.
- **Decision:** **bypass-documented** — the SAME decision as Gap 65, on the same
  grounds, for the Azure sibling. Recorded as its own entry rather than folded
  into Gap 65 because a socket grant is an operator decision per service, and a
  second service silently inheriting the first's exemption is exactly what
  `test_only_profiled_emulators_are_granted_the_docker_socket` now refuses.
- **Why the grant exists:** Azure Functions spawns runtime containers as
  *siblings* of the emulator, so without the socket that service cannot run at
  all. The operator decision of 2026-09-05 (locally hosted Docker, for now)
  applies unchanged: the LOCAL daemon, no remote `FLOCI_AZ_DOCKER_DOCKER_HOST`,
  no internal registry mirror.
- **Why it is acceptable:**
  - **The service is behind the `floci-az` compose profile**, so it never starts
    with a plain `docker compose up`. Asserted over the WHOLE granted set, not
    one name.
  - **Loopback-only port publishing** (`127.0.0.1:4577:4577`) — an emulator
    holding the host socket must not be reachable off-host. The container-backed
    proxy ranges are declared in `emulator_az.PROXY_PORT_RANGES` and
    deliberately NOT published; publishing them would also collide with the
    `floci` profile's own 6379-6399 range on any host running both.
  - **The image tag is pinned** (`floci/floci-az:0.12.0`, never `:latest`), with
    the digest recorded in `emulator_az.IMAGE_DIGEST`.
  - **Input is operator-supplied, not tenant-supplied.** ICDEV reads this
    emulator through one governed DataBridge grant scoped to
    `twin_observatory_analyst`, READ ONLY, and there is no Azure IaC executor —
    nothing in this tree applies a change through it.
  - **Persistent state is gitignored.** `FLOCI_AZ_STORAGE_MODE=persistent` writes
    under `./data/floci-az` (the image's own `/app/data`, not `/var/lib/floci`),
    and this repo is PUBLIC. `data/floci-az/` needed its OWN `.gitignore` entry:
    the existing `data/floci/` rule ends in a slash and does not cover it.
  - **Never a source of a performance, cost or capacity claim** — the standing
    guard from `docs/spikes/twx-spk-01-localstack-go-no-go.md`.
- **One extra hazard this emulator has and Gap 65 does not:** floci-az serves an
  **IMDS token endpoint** at `/metadata/identity/oauth2/token` and issues real
  signed JWTs (measured 2026-09-05). The connection row's `egress_allowlist`
  (`localhost`, `127.0.0.1`, `::1`) is what stops a mis-set seam dialling the
  real link-local `169.254.169.254` instead, and it is enforced at the point the
  destination is decided rather than per URL.
- **Revisit if:** the profile is started anywhere automatically; the socket mount
  moves onto a default-profile service; `FLOCI_AZ_DOCKER_DOCKER_HOST` is set to a
  **remote** daemon (a different trust question — the grant then crosses a host
  boundary); the emulator is exposed off-loopback or run on a shared/CI host; an
  Azure IaC executor is added (the read-only premise above then no longer holds);
  or floci-az begins accepting tenant-supplied rather than operator-supplied
  input.

### Gap 67 — floci-gcp emulator holds the host Docker socket (`docker-compose.yml`, `floci-gcp` profile)

- **File:** `docker-compose.yml` — service `floci-gcp` (flx-gcp-01); switch at
  `tools/cloud/emulator_gcp.py`.
- **Risk:** The service bind-mounts the host Docker socket
  (`${FLOCI_GCP_DOCKER_SOCKET_MOUNT:-//var/run/docker.sock}:/var/run/docker.sock`).
  **A container holding the host Docker socket is root-equivalent on that
  host** — it can start a privileged container, bind-mount `/`, and read or
  write anything the daemon can. Identical in kind to Gap 65 and Gap 66, and
  the grant is made a third time deliberately rather than inherited.
- **Decision:** **bypass-documented** — an operator-gated grant, off by
  default, never reached by any ICDEV default path.
- **Rationale:** MEASURED 2026-09-05 (see `docs/spikes/flx-gcp-parity.md` §5),
  by observing what each service actually started rather than reading a service
  list: **Cloud SQL** spawns `postgres:15.18-alpine`, **Managed Kafka** spawns
  `redpandadata/redpanda:latest`, **GKE** spawns `rancher/k3s:latest`, and
  **Cloud Run** spawns *the caller's own image*. Without the socket, Cloud SQL
  and Kafka return HTTP 500 and those services cannot be emulated at all. Every
  other lane the ICDEV connector reads — GCS, Pub/Sub, Secret Manager, KMS, IAM,
  BigQuery, Resource Manager — needs no socket and works with the mount removed.
  Sandboxing the emulator itself is not available: nesting it inside
  `SandboxExecutor` would mean handing the socket to the sandbox instead, which
  relocates the grant rather than removing it. The operator approved the grant
  on 2026-09-05 for a **locally hosted** Docker daemon, on a developer
  workstation, for API-contract testing only.
- **Guardrails:**
  - **The service is behind the `floci-gcp` compose profile**, so it does not
    start on a bare `docker compose up` and is not in `/start`. Starting it is
    two deliberate acts: `FLOCI_GCP_ENABLED=true` and
    `docker compose --profile floci-gcp up -d`. Pinned by
    `tests/cloud/test_floci_compose_profile.py::test_only_profiled_emulators_are_granted_the_docker_socket`,
    which asserts the socket-granted set is EXACTLY the three enumerated
    emulators and that every one of them is profiled — so a FOURTH grant fails
    that test rather than inheriting this exemption.
  - **Loopback-only publication.** The single published port (4588) is bound to
    `127.0.0.1`. Pinned by `test_every_published_port_is_loopback_only`.
  - **The image tag is pinned** (`floci/floci-gcp:0.8.0`, never `:latest`), and
    the digest measured on 2026-09-05
    (`sha256:5037d304aded5ab4ccf4697239131521fe66b8952f411f6c1781c9166d2ab01b`)
    is recorded in `emulator_gcp.IMAGE_DIGEST`. Pin by digest and record it in
    the SBOM before any real deployment.
  - **`FLOCI_GCP_DOCKER_DOCKER_HOST` is left unset**, so floci-gcp reaches only
    the daemon it was handed. A remote daemon and an internal registry mirror
    are named follow-ons, not silently configured here.
  - **The mount source is a distinct variable from the seam's socket variable**
    (`FLOCI_GCP_DOCKER_SOCKET_MOUNT` vs `FLOCI_GCP_DOCKER_SOCKET`), for the
    correctness reason established at Gap 65: they answer different questions,
    and conflating them turns an honest `None` into a fabricated `False`.
  - **No IaC execution.** `emulator_gcp.IAC_EXECUTION_SUPPORTED` is `False`,
    `FlociGcpConnector.capabilities.supports_write` is `False`, and `write()`
    returns a refusal naming the absent executor. ICDEV has
    `tools/cloud/aws_config_executor.py` and no GCP analogue.
  - **Persistent state is gitignored.** `FLOCI_GCP_STORAGE_MODE=persistent`
    writes buckets, topics, secrets and key rings under `./data/floci-gcp`, and
    this repo is PUBLIC. It needed its OWN `.gitignore` entry: every rule there
    ends in a slash, so neither `data/floci/` nor `data/floci-az/` covers it.
  - **Never a source of a performance, cost or capacity claim** — the standing
    guard from `docs/spikes/twx-spk-01-localstack-go-no-go.md`.
- **Two ways this emulator's risk profile differs from Gap 66, both measured:**
  - **It serves NO metadata endpoint.** `GET /computeMetadata/v1/...` returns
    404, so unlike floci-az there is no emulator-issued token surface to confuse
    with the real link-local `169.254.169.254`. The connection row's
    `egress_allowlist` is kept regardless — it bounds where the SEAM may point,
    which is a question about ICDEV's configuration.
  - **Cloud Run fails SILENTLY without the socket.** Measured: a socket-less
    deploy returns HTTP **200** with a service body carrying `uid`,
    `createTime`, `traffic` and a `urls` entry — indistinguishable from a real
    deployment. That is why `simulate_delta` raises Cloud Run's severity to
    `high` while the other container-backed services stay `medium`: the others
    fail loudly with a 500.
- **Revisit if:** the profile is started anywhere automatically; the socket mount
  moves onto a default-profile service; `FLOCI_GCP_DOCKER_DOCKER_HOST` is set to
  a **remote** daemon (a different trust question — the grant then crosses a host
  boundary); the emulator is exposed off-loopback or run on a shared/CI host; a
  GCP IaC executor is added (the read-only premise above then no longer holds);
  or floci-gcp begins accepting tenant-supplied rather than operator-supplied
  input.
