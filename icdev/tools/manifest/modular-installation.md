# Modular Installation (Phase 33)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Modular Installation (Phase 33)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Installer | tools/installer/installer.py | Interactive wizard + profile-based modular deployment with compliance posture configuration | --interactive, --profile, --add-module, --add-compliance, --upgrade, --status, --json | Installation manifest |
| Module Registry | tools/installer/module_registry.py | Module definition registry: dependencies, DB table groups, validation | --validate, --list, --json | Module graph |
| Compliance Configurator | tools/installer/compliance_configurator.py | Compliance posture selection and framework activation | --list-postures, --apply, --json | Compliance config |
| Release Orchestrator | tools/installer/release.py | End-to-end PyPI release: preflight (semver, not-on-main, not-going-backwards) → release-notes gate (README `## What's New in X` + CHANGELOG `## [X]`) → version bump across icdev/_version.py, pyproject.toml, args/brand.yaml → delegates the whole build to `build_release.py` (sync_package_tree, validate_package_config, build, wheel inspection, throwaway-venv smoke, air-gap install) → artifact verification (filenames carry the target version; packaged brand.yaml agrees; twine check) → opt-in upload with credentials read from .env. Publishing is OFF by default; `--publish` is refused alongside `--skip-smoke` or `--allow-missing-notes`. The notes gate runs BEFORE the bump so a missing-notes run writes nothing, and re-running the current version is treated as resuming a half-finished release rather than an error. | --version X.Y.Z \| --bump major\|minor\|patch, --publish, --bump-only, --scaffold-notes, --skip-smoke, --allow-missing-notes, --json | Release report {target_version, published, steps{preflight,notes,bump,build,verify,publish}} |
| Platform Setup | tools/installer/platform_setup.py | Platform artifact generation (Docker Compose, K8s RBAC, .env, Helm values) | --generate docker\|k8s-rbac\|env\|helm-values, --modules | Platform artifacts |

