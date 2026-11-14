# Feature Conformance (icx-cnf-01)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Five-rule audit

| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Feature Conformance Audit | tools/conformance/feature_audit.py | Judges each feature in `args/feature_conformance.yaml` against the five rules (GUI, workflow, twin, architecture, AI-native) from repo evidence only: the live url_map read in the scrubbed `nav_paths` probe environment, view source, nav hrefs, templates, `args/workflow_hitl_templates/`, `args/agent_approval_policy.yaml`, `tools/twin_core/adapters/`, `args/platform_model/`, and the MCP registry's read-only and authorization declarations. Deterministic (no timestamps or line numbers). Stated reasons excuse one rule and go stale when not needed. Open gaps are enumerated by name in `args/feature_conformance_census.txt` | --json, --markdown, --census, --check | JSON report; exit 1 on a stale doc/snapshot/reason/census; 2 when the url_map probe fails |
