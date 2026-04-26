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

## References

- D-SEC-10 — SandboxExecutor (container isolation, Phase 71)
- D-SEC-11 — 6-path sandbox integration (Phase 72)
- Phase 72 feature doc: [phase-72-sandbox-integration.md](../features/phase-72-sandbox-integration.md)
- `tools/security/sandbox_executor.py` — runtime implementation
