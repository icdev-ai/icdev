# Modular Installation (Phase 33)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Modular Installation (Phase 33)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Installer | tools/installer/installer.py | Interactive wizard + profile-based modular deployment with compliance posture configuration | --interactive, --profile, --add-module, --add-compliance, --upgrade, --status, --json | Installation manifest |
| Module Registry | tools/installer/module_registry.py | Module definition registry: dependencies, DB table groups, validation | --validate, --list, --json | Module graph |
| Compliance Configurator | tools/installer/compliance_configurator.py | Compliance posture selection and framework activation | --list-postures, --apply, --json | Compliance config |
| Platform Setup | tools/installer/platform_setup.py | Platform artifact generation (Docker Compose, K8s RBAC, .env, Helm values) | --generate docker\|k8s-rbac\|env\|helm-values, --modules | Platform artifacts |

