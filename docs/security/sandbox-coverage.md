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

### Gap 13 — AI Augmentation Canvas scan engine (`tools/ai_augmentation/`)

**Module:** `tools/ai_augmentation/engine.py`, `tools/ai_augmentation/pattern_classifier.py`

**Ingress path:** `POST /ai-augmentation/api/scan` accepts `input_ref` (a local file-system path) and `il_level`. The engine reads source files from that path, runs AST-based pattern detection (and optionally Semgrep), scores opportunities, and stores results in `aac_scans` / `aac_opportunities` / `aac_scores` tables.

- **Decision:** **trusted-first-party**
- **Rationale:** `input_ref` is a developer/operator-supplied directory path — not end-user HTTP input from the public internet. The scanner performs read-only static analysis (AST walk + Semgrep subprocess with fixed argument list); it does not execute any code from the scanned directory. No `eval()`/`exec()` appears in the analysis hot path. Template files under `tools/dashboard/templates/ai_augmentation/` and `icdev/tools/dashboard/templates/ai_augmentation/` are first-party and code-reviewed.
- **Guardrails:**
  - `input_ref` is resolved with `pathlib.Path.resolve()` before use; empty paths return a 400 before engine invocation.
  - Semgrep subprocess uses a fixed argument list; no `shell=True`; no user-controlled command interpolation.
  - Canvas templates are first-party; the IQE query widget renders query results as text only (no raw HTML).
  - Scan results are written to append-only audit log (`aac_audit_log`).
- **Revisit if:** `input_ref` is ever accepted from an unauthenticated public endpoint, or if the engine adds a step that executes code from the scanned directory.

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
| `tools/ai_augmentation/` scoring (AAC) | **trusted-first-party** | Deterministic weighted scoring; LLM optional (covered by Gap 13). |

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
