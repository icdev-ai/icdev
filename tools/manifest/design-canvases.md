# Design Canvases (7-Canvas Suite)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Design Canvases (7-Canvas Suite)
All canvases share: separate SQLite DB, Flask Blueprint, YAML config in `args/`, feature flag env var, NIST 800-53 compliance assessment, and cross-canvas integration via `tools/canvas/orchestrator.py`.

| Canvas | Blueprint | Engine | Config | DB | Route | Feature Flag | Description |
|--------|-----------|--------|--------|----|-------|--------------|-------------|
| **IDC** Infrastructure | tools\infra_canvas\blueprint.py | tools\infra_canvas\infra_engine.py | args\infra_canvas_config.yaml | infra_canvas.db | /infra | ICDEV_INFRA_ENABLED | Multi-CSP infrastructure design (compute, storage, containers, serverless) with NIST/FedRAMP/CMMC assessment and CSP equivalence mapping |
| **NDC** Network | tools\network\blueprint.py | tools\network\routes\ | — | network_canvas.db | /network | ICDEV_NETWORK_ENABLED | Network topology design with intent validation, ACAS/Nessus overlay, NL query, change request markup, and NetBox sync |
| **SDC** Security | tools\security_canvas\blueprint.py | tools\security_canvas\security_engine.py | args\security_canvas_config.yaml | security_canvas.db | /security | ICDEV_SECURITY_ENABLED | STRIDE threat modeling, NIST/FedRAMP/CMMC control mapping, MITRE ATT&CK coverage, compliance KG, remediation, LLM agent, NL query |
| **BDC** Boundary | tools\boundary_canvas\blueprint.py | tools\boundary_canvas\boundary_engine.py | args\boundary_canvas_config.yaml | boundary_canvas.db | /boundary | ICDEV_BOUNDARY_ENABLED | ATO/FedRAMP/SCIF authorization boundary design, ISA lifecycle (expiry warning 60d / critical 30d), PPS matrix generation, boundary gap detection |
| **PDC** Pipeline | tools\pipeline\blueprint.py | — | — | pipeline_canvas.db | /devops | ICDEV_PIPELINE_ENABLED | DevSecOps pipeline design, CI/CD stage modeling, security gate placement, GitLab/GitHub Actions export |
| **ODC** Observability | tools\observability_canvas\blueprint.py | tools\observability_canvas\observability_engine.py | args\observability_canvas_config.yaml | observability_canvas.db | /observability | ICDEV_OBSERVABILITY_ENABLED | SIEM/SOAR/log stack design, MITRE ATT&CK detection coverage, source type weighting, NIST AU/SI control assessment, log retention policy |
| **DDC** Data | tools\data_canvas\blueprint.py | tools\data_canvas\data_engine.py | args\data_canvas_config.yaml | data_canvas.db | /data | ICDEV_DATA_CANVAS_ENABLED | Data model design with PII/PHI/CUI/SECRET classification, retention policy enforcement, Privacy Act/HIPAA/GDPR assessment, ER diagram export |

## SDC Sub-Tools
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| SDC Attack Path Twin | tools/security_canvas/security_engine.py (`find_attack_paths`), tools/iqe/adapters/security.py, tools/security_canvas/caldera_adapter.py | BAS-style attack path digital twin for the Security Design Canvas. Snapshots the STRIDE/attack graph as `sdc_attack_snapshots` rows, enumerates all simple BFS paths from entry points to high-value targets with risk scoring, and registers three IQE collections (`attack.nodes`, `attack.edges`, `attack.paths`) plus 5 seed queries (data exfil, lateral-to-IL5, priv escalation, cross-boundary, MTTR critical). Caldera adapter maps ability-IDs→ATT&CK technique-IDs for replay enrichment. Routes: `POST /api/designs/<design_id>/attack-paths`. Tables: `sdc_attack_snapshots`. | `find_attack_paths(graph_data)` → attack path list; `CalderaAdapter(url, api_key)` → `.fetch_scenarios()`, `.ability_technique_map`; IQE: `attack.nodes`, `attack.edges`, `attack.paths(src, goal)` | Dict `{attack_paths, total_paths, critical_paths, …}` / IQE row list |

## PDC Sub-Tools
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| PDC Pipeline Twin | tools/pipeline/twin.py | Phase-1 pre-merge what-if simulation. Snapshot any pipeline DAG as `pdc_snapshots`, then run a delta graph through antipattern detector + SLSA assessor + compliance engine to produce a PASS/WARN/FAIL verdict. Tables: `pdc_snapshots`, `pdc_simulations`. Routes: `GET /devops/twin/<pipe_id>`, `POST /api/pipelines/<id>/twin/snapshot`, `GET /api/pipelines/<id>/twin/snapshots`, `POST /api/pipelines/<id>/twin/simulate`, `GET /api/twin/simulations/<sim_id>`. | `take_snapshot(pipeline_id)`, `simulate_delta(pipeline_id, delta_graph)`, `list_snapshots(pipeline_id)`, `get_simulation(sim_id)` | Snapshot dict / Simulation result dict (verdict, antipatterns, slsa, compliance, diff) |

## IDC Sub-Tools
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| IDC SOPs | tools/infra_canvas/sops.py | Infrastructure Design Canvas — Standard Operating Procedures CRUD + draft→pending_review→approved approval workflow. Seeds change management, capacity planning, and cloud account onboarding SOPs into `idc_sops` table. No LLM dependency. | (library) `get_all_sops`, `get_sop_by_id`, `create_sop`, `update_sop`, `transition_status`, `delete_sop`, `seed_sops` | SOP dict / list |
| IDC Runbooks | tools/infra_canvas/runbooks.py | Infrastructure Design Canvas — operational runbooks for common infrastructure incidents (provisioning failure, capacity breach, cloud drift, patch rollback). CRUD + seed for `idc_runbooks` table; no LLM dependency. | (library) | Runbook dict / list |
| IDC Helm Emitter | tools/infra_canvas/emitters/helm.py | Helm chart emitter for IDC graph nodes (K8s scope). Emits STIG-hardened Deployment, Service, ConfigMap, and HPA manifests; auto-injects CUI classification labels/annotations; assembles full chart dict with Chart.yaml. | (library) `emit_manifest(node)` → YAML str; `emit_chart(nodes, chart_name, chart_version, classification)` → `{filename: yaml_str}` | YAML string / Chart dict |
| IDC IaC Emit Page | tools/infra_canvas/blueprint.py (routes: GET /infra/emit, POST /infra/emit/run) | Dashboard page for interactive IaC generation from a saved design or inline graph JSON. Form selects project, IaC target (terraform/pulumi/ansible/helm), and CSP; POST /infra/emit/run emits and returns generated file contents as a JSON dict {filename: content} for tabbed rendering. Validation: 400 on missing fields, 422 on unsupported target/CSP combo. Template: tools/dashboard/templates/infra_canvas/emit.html. | Form: project (graph JSON or design ID), target, csp | JSON: {target, csp, node_count, emitted_count, skipped_count, files: {filename: content}} |
| IDC Pre-Apply Gate | tools/infra_canvas/preapply_gate.py | Parses `terraform plan -json` output, computes resource delta (add/modify/delete), and runs all infra/* IQE checks against the planned final state. CLI: `python tools/infra_canvas/preapply_gate.py [--gate] <plan.json|->`; exits 1 on fail when --gate set. | `run_gate(plan_json: dict)` → `{gate: pass\|fail, violations: [...], delta: {add, modify, delete}}` | JSON verdict dict |

