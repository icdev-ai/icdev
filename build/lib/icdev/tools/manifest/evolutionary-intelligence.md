# Evolutionary Intelligence (Phase 36 — D209-D214)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Evolutionary Intelligence (Phase 36 — D209-D214)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Child Registry | tools/registry/child_registry.py | Enhanced child app registry with capabilities CRUD, status tracking | --register, --list, --get, --add-capability, --json | Child record |
| Telemetry Collector | tools/registry/telemetry_collector.py | Pull-based health telemetry from child heartbeat endpoints (D210) | --collect, --child-id, --summary, --json | Health data |
| Genome Manager | tools/registry/genome_manager.py | Versioned capability genome with semver + SHA-256 content hash (D209) | --get, --create, --diff, --rollback, --history, --verify, --json | Genome version |
| Capability Evaluator | tools/registry/capability_evaluator.py | 7-dimension scoring: universality, compliance_safety, risk, evidence, novelty, cost, security_assessment (REQ-36-020 + Phase 37) | --evaluate, --capability-data, --json | Score + outcome |
| Staging Manager | tools/registry/staging_manager.py | Git worktree isolation for testing capabilities (D211, 72-hour expiry) | --create, --test, --check-compliance, --destroy, --list, --json | Staging env |
| Propagation Manager | tools/registry/propagation_manager.py | Deploy capabilities to children with HITL approval (REQ-36-040, D214) | --prepare, --approve, --execute, --rollback, --status, --list, --json | Propagation log |
| Absorption Engine | tools/registry/absorption_engine.py | 72-hour stability window before genome absorption (D212) | --check, --absorb, --candidates, --json | Absorption result |
| Learning Collector | tools/registry/learning_collector.py | Process child-reported learned behaviors (D213) | --ingest, --evaluate, --unevaluated, --json | Behavior records |
| Cross-Pollinator | tools/registry/cross_pollinator.py | Broker capabilities between children via parent (HITL required) | --find, --propose, --execute, --json | Pollination result |
| Evolution Daemon | tools/registry/evolution_daemon.py | Autonomous 7-step capability lifecycle: discover, evaluate, stage, test, approve, verify, absorb (D-EVO-1) | --once, --status, --reflex NAME, --enable, --disable, --reset, --json | Daemon status |
| Egress Monitor | tools/registry/egress_monitor.py | NemoClaw-adapted child network egress tracking against parent policies (D-NC-6) | --collect, --evaluate, --summary, --json | Violation report |
| Propagation Verifier | tools/registry/propagation_verifier.py | Post-propagation integrity verification: digest, DB, health, CUI checks (D-NC-5) | --verify, --history, --json | Verification checklist |
| Sandbox Scorer | tools/registry/sandbox_scorer.py | 8th capability evaluation dimension: isolation posture scoring (D-NC-4) | --score, --capability-id, --source-metadata, --json | Score + breakdown |

