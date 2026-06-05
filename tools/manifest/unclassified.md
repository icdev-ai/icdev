# Unclassified (auto-added)

> Shard of `tools/manifest.md`. Entries added by auto-remediation.
> Move to the correct topic shard and update descriptions.

| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Auto-added codelens.py | tools/codelens.py | (auto-added by remediation; update description) | --json | stdout |
| Auto-added kanban.py | tools/kanban.py | (auto-added by remediation; update description) | --json | stdout |
| Auto-added schedule_alphadesk_news_plan.py | tools/scripts/schedule_alphadesk_news_plan.py | (auto-added by remediation; update description) | --json | stdout |
| Auto-added sr.py | tools/trading/ta/sr.py | (auto-added by remediation; update description) | --json | stdout |
| Auto-added package_registry.py | tools/installer/package_registry.py | (auto-added by remediation; update description) | --json | stdout |
| Auto-added package_registry.py | tools/installer/package_registry.py | (auto-added by remediation; update description) | --json | stdout |
| Auto-added frontline_importer.py | tools/frontline_importer.py | (auto-added by remediation; update description) | --json | stdout |
| Auto-added war_endurance.py | tools/simulation/war_endurance.py | (auto-added by remediation; update description) | --json | stdout |
| Auto-added seed_wex_kanban.py | tools/studio/seed_wex_kanban.py | (auto-added by remediation; update description) | --json | stdout |
| Auto-added lineage_scanner.py | tools/data/lineage_scanner.py | (auto-added by remediation; update description) | --json | stdout |
| Auto-added terraform_executor.py | tools/data/terraform_executor.py | (auto-added by remediation; update description) | --json | stdout |
| Auto-added constants.py | tools/infra_canvas/constants.py | (auto-added by remediation; update description) | --json | stdout |
| SIPA scanner adapters | tools/integrity/scanners.py | Shell out to existing static scanners (sast / secrets / deps / semgrep / formal / container) over a quarantined tree and normalize into integrity_findings; the semgrep adapter runs malicious-signature rules (context/integrity/semgrep_rules/*.yaml) via the reused Semgrep engine in tools/aiify/pattern_classifier with a regex fallback, emitting known_bad_signature findings; honors args/integrity_config.yaml scanner toggles | --assessment-id N [--scanner sast\|secrets\|deps\|semgrep\|formal\|container] [--staged-path P] --json | stdout |
| SIPA capability extractor | tools/integrity/capability_extractor.py | Python ast scan (never executes the target) that turns code into a normalized behavioral capability manifest: Phase-1 detectors network_egress (socket/http.client/urllib/requests/httpx + host/url literal), filesystem (open/Path/shutil/os + path + mode), process_exec (subprocess/os.system/popen/exec/multiprocessing + command). Resolves import aliases so renamed imports can't hide a capability; persists append-only to integrity_capabilities. extract(path)/extract_and_persist(assessment_id, path) | --path P [--assessment-id N] --json | stdout |
