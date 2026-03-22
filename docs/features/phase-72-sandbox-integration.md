# CUI // SP-CTI
# Phase 72 — LLM Sandbox Integration (D-SEC-11)

## Metadata
- **Phase:** 72
- **ADR:** D-SEC-11
- **Date:** 2026-03-21
- **Classification:** CUI // SP-CTI

## Problem Statement

The LLM Sandbox (D-SEC-10, Phase 71) provided container-isolated code execution
via `sandbox_executor.py`, but was only wired into Connector Forge testing and
capability evaluation scoring. Untrusted code entering ICDEV™ through the CodeLens
testing pipeline, CI/CD contributor PRs, marketplace skill installations, OpenClaw
bridge imports, and Genesis Evolve mutations was executing without container isolation.

## Goals

1. Integrate LLM Sandbox into the CodeLens/testing pipeline as step 2.5
2. Add sandbox verification to CI/CD pipelines (GitLab CI + GitHub Actions)
3. Upgrade OpenClaw bridge Gate 9 from subprocess fallback to container sandbox
4. Add pre-install sandbox verification to marketplace install_manager
5. Add sandbox verification to Genesis Evolve reflex code mutations
6. Maintain graceful degradation when Docker/llm-sandbox is unavailable

## Architecture

### Integration Points

| Area | File | Integration | Graceful Degradation |
|------|------|-------------|---------------------|
| CodeLens | `tools/testing/test_orchestrator.py` | Step 2.5: `run_sandbox_isolation()` | Skip + pass when unavailable |
| GitLab CI | `.gitlab-ci.yml` | `sandbox` stage with Docker-in-Docker | N/A (Docker always available in CI) |
| GitHub Actions | `.github/workflows/icdev-ci.yml` | `sandbox` job on PRs | Skip files when unavailable |
| OpenClaw | `tools/marketplace/openclaw_bridge.py` | Gate 9 + Gate 9b full execution | Subprocess fallback |
| Marketplace | `tools/marketplace/install_manager.py` | `_sandbox_verify_asset()` pre-install | Skip (returns None) |
| Genesis | `tools/genesis/reflexes/evolve.py` | Step 3.5 + confidence penalty | Pass when unavailable |

### Sandbox Execution Flow

```
Untrusted Code → SandboxExecutor.execute()
                    ↓
              Docker Container
              (--network none, memory-limited, timeout-enforced)
                    ↓
              compile() check — no import side effects
                    ↓
              Result: pass/fail/unavailable
                    ↓
              Audit → sandbox_execution_log (append-only, NIST AU-2)
```

### executor_type Values (Audit Trail)

| Caller | executor_type |
|--------|--------------|
| Test Orchestrator | `codelens` |
| CI/CD Pipeline | `ci-pipeline` |
| OpenClaw Bridge | `openclaw_import` / `openclaw_full_sandbox` |
| Marketplace Install | `marketplace_install` |
| Genesis Evolve | `genesis_evolve` |

## Configuration

`args/sandbox_config.yaml` updated with integration-specific overrides:
- `codelens.max_files`: 15 (cap per run)
- `ci_cd.fail_on_error`: true (block merge)
- `marketplace.block_on_failure`: true (reject install)
- `genesis.confidence_penalty`: 0.10 (reduce confidence on failure)

## CLI Changes

- `test_orchestrator.py` gains `--skip-sandbox` flag to bypass sandbox isolation

## Security Controls

- **NIST SA-11**: Developer Testing — sandbox verifies code in clean environment
- **NIST SI-7**: Software Integrity — container isolation prevents side effects
- **NIST AU-2**: Audit Events — all executions logged to append-only table
- **NIST SC-7**: Boundary Protection — network disabled by default

## Testing

- All modified files pass py_compile, ruff, bandit
- 55 sandbox_executor tests pass
- 2938 tests pass (1 pre-existing failure unrelated to this change)
- Companion sync updated all 10 LLM platforms

## Files Changed

- `tools/testing/test_orchestrator.py` — Added `run_sandbox_isolation()` + step 2.5
- `tools/marketplace/openclaw_bridge.py` — Upgraded `_sandboxed_run()` to use SandboxExecutor
- `tools/marketplace/install_manager.py` — Added `_sandbox_verify_asset()` pre-install check
- `tools/genesis/reflexes/evolve.py` — Added `_sandbox_verify_file()` + confidence penalty
- `.gitlab-ci.yml` — Added `sandbox` stage with Docker-in-Docker
- `.github/workflows/icdev-ci.yml` — Added `sandbox` job for PRs
- `args/sandbox_config.yaml` — Added integration-specific overrides

# CUI // SP-CTI
