# Code Intelligence & Verification

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Code Intelligence & Verification
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Formal Verifier | tools/analysis/formal_verifier.py | Property-based security checks (LeanStral-adapted, D-VL-6) | --file, --project-dir, --gate, --generate-properties, --json | Formal check results |
| Verify Loop | tools/analysis/verify_loop.py | Compiler-in-the-loop verification (LeanStral-adapted, D-VL-1) | --file, --project-dir, --repair, --gate, --json | Verification results |
| Session Purpose | tools/agent/session_purpose.py | Session purpose declaration for NIST AU-3 (D-ORCH-5) | --declare, --active, --complete, --history, --json | Purpose records |
| CLI Harmonizer | tools/compat/cli_harmonizer.py | CLI argument normalization | (library) | Harmonized CLI args |
| CLI Formatter | tools/cli_formatter.py | ANSI terminal output formatter | (library) | Colored CLI output |

