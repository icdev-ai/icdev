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
  endpoint (`POST /security/api/zig/targets/<id>/ingest`).
- **Decision:** **bypass-documented**
- **Rationale:** All 5 parsers use safe stdlib decoders only — `json.loads()`,
  `xml.etree.ElementTree.fromstring()`, `yaml.safe_load()`. No `eval()`, `exec()`,
  `subprocess`, or filesystem writes occur. Parsed data flows only to `set_activity_status()`
  via parameterized SQL inserts. XML parsing uses stdlib `ElementTree` (no DTD expansion,
  no external entity resolution). YAML uses `safe_load` (no custom constructors). This
  guarantee is enforced by `tests/test_zig_ingest_adapters.py` (31 unit tests, all
  using a DB-stub that verifies no real SQL calls reach the DB).
- **Revisit if:** any ingest path adds `eval()`, subprocess execution of scan tools,
  or resolves external references from within the uploaded content.

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
