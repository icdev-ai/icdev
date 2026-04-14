# Security (Additional)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Security (Additional)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Confabulation Detector | tools/security/confabulation_detector.py | Deterministic confabulation detection (D310) | --check-output, --summary, --json | Detection results |
| Endpoint Security Scanner | tools/security/endpoint_security_scanner.py | API endpoint security assessment | --scan, --json | Scan results |
| Sandbox Executor | tools/security/sandbox_executor.py | Container-isolated code execution with resource limits, network isolation, and audit logging (D-SEC-10) | --execute --code, --execute-file --path, --health, --gate, --language, --timeout, --memory, --json | SandboxResult JSON |

