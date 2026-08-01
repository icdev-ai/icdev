# TSR CANV — canvas test-file inventory (tsr-canv-01-d1)

Diagnostic only. Produced 2026-08-01 on branch `kanban/tsr-canv-01-d1`, a worktree off `origin/main`
at `248c2d178`. No source file was modified.

Answers: which `tests/` files exercise the CANV epic — the canvas and canvas-adjacent `tools.*`
packages — selected by what each file **imports**, not by filename.

## Scope

19 packages, all verified to exist under `tools/` in this checkout:

| # | package | files matching | of which a real `import` |
|---|---------|----------------|--------------------------|
| 1 | `tools/network` | 72 | 67 |
| 2 | `tools/security_canvas` | 36 | 28 |
| 3 | `tools/ai_augmentation` | 35 | 32 |
| 4 | `tools/data_canvas` | 33 | 24 |
| 5 | `tools/observability_canvas` | 25 | 17 |
| 6 | `tools/infra_canvas` | 20 | 13 |
| 7 | `tools/boundary_canvas` | 19 | 14 |
| 8 | `tools/canvas` | 17 | 7 |
| 9 | `tools/migration_canvas` | 17 | 16 |
| 10 | `tools/agentic_ai_canvas` | 17 | 15 |
| 11 | `tools/aiml_canvas` | 8 | 7 |
| 12 | `tools/noc_canvas` | 6 | 3 |
| 13 | `tools/dsoc_canvas` | 4 | 2 |
| 14 | `tools/canvas_compliance` | 3 | 0 |
| 15 | `tools/qdc_canvas` | 3 | 2 |
| 16 | `tools/ccc_canvas` | 3 | 1 |
| 17 | `tools/aimc` | 2 | 2 |
| 18 | `tools/aadc` | 1 | 1 |
| 19 | `tools/canvas_health` | 0 | 0 |

`tools/canvas_compliance` (3 files) and `tools/canvas_health` (0) are referenced only through
`mock.patch(...)` target strings — no test imports either package directly.

### Deliberately excluded as canvas-adjacent

`mission_canvas`, `pmc_canvas`, `workflow_canvas` are separate epics. Three files match one of
them and nothing in scope, and are **not** part of this slice:

- `tests/test_cnr_mission_canvas.py` — mission_canvas
- `tests/test_mission_canvas.py` — mission_canvas
- `tests/test_wfc_canvas_hardening.py` — workflow_canvas

## Exact patterns used

Discovery, run from the repo root. Note the task card's literal `-Path tests\*.py` only reaches
the 258 top-level files and silently misses the 16 under `tests/genesis_auto`, `tests/e2e_selenium`,
`tests/security` and `tests/viz` — the recursive form below is what was actually run:

```powershell
Get-ChildItem -Path tests -Recurse -Filter *.py -File | Select-String -Pattern '(?<![\w.])(?:icdev\.)?tools\.(?:observability_canvas|canvas_compliance|agentic_ai_canvas|migration_canvas|security_canvas|boundary_canvas|ai_augmentation|canvas_health|infra_canvas|data_canvas|aiml_canvas|dsoc_canvas|qdc_canvas|noc_canvas|ccc_canvas|network|canvas|aadc|aimc)(?![\w])' -List
```

Regex, verbatim. Matches the canonical `icdev.tools.*` namespace and the legacy `tools.*` shim, and
matches inside `mock.patch("...")` target strings as well as in import statements:

```regex
(?<![\w.])(?:icdev\.)?tools\.(?:observability_canvas|canvas_compliance|agentic_ai_canvas|migration_canvas|security_canvas|boundary_canvas|ai_augmentation|canvas_health|infra_canvas|data_canvas|aiml_canvas|dsoc_canvas|qdc_canvas|noc_canvas|ccc_canvas|network|canvas|aadc|aimc)(?![\w])
```

The lookbehind `(?<![\w.])` keeps `icdev.tools.network` from counting twice and rejects
`foo_tools.network`; the lookahead `(?![\w])` keeps `tools.canvas` from matching
`tools.canvas_compliance` and `tools.network` from matching `tools.network_ingester`.

Second pass, used to tell a real import from a patch-target string:

```regex
^\s*(?:from|import)\s+(?:icdev\.)?tools\.(aadc|agentic_ai_canvas|ai_augmentation|aimc|aiml_canvas|boundary_canvas|canvas|canvas_compliance|canvas_health|ccc_canvas|data_canvas|dsoc_canvas|infra_canvas|migration_canvas|network|noc_canvas|observability_canvas|qdc_canvas|security_canvas)(?![\w])
```

## Result

`274` of the `2010` files under `tests/` match the discovery regex, carrying
`1881` individual matches. The tiers separate a canvas test from a test that merely
patches a canvas symbol on its way to something else.

| tier | definition | files | run for CANV? |
|------|------------|-------|---------------|
| A | imports a CANV package and **no other** `tools.*` subsystem | 149 | **yes — this is the slice** |
| B | imports a CANV package **and** another `tools.*` subsystem | 87 | only when the failure is in canvas code |
| C | never imports one; matches only via `mock.patch` / string refs | 38 | no |

`tools/network` dominates at 72 files because the Network Design Canvas, NOC, CCC and DSOC
all sit under it.

## Full listing

Tier A first, then B, then C. `packages` is every in-scope package the file imports; `first match`
is the first matching line verbatim.

### Tier A — canvas-only imports (149)

| file | packages | matches | first match |
|------|----------|---------|-------------|
| `tests/_aadc_canvas.py` | `agentic_ai_canvas` | 2 | `from tools.agentic_ai_canvas.db.init_db import get_connection` |
| `tests/e2e_full_lifecycle_complex.py` | `canvas`, `network` | 5 | `from tools.canvas.kg_builder import rebuild_canvas_kg` |
| `tests/e2e_network_nl_query.py` | `network` | 1 | `from tools.network.nl_query import (` |
| `tests/e2e_selenium/test_app_migration_module.py` | `migration_canvas` | 2 | `from tools.migration_canvas.post_migration_validator import http_smoke_test` |
| `tests/genesis_auto/test_ato_generator.py` | `network` | 18 | `"""Auto-generated tests for tools.network.ato_generator.` |
| `tests/genesis_auto/test_boundary_engine.py` | `boundary_canvas` | 11 | `"""Auto-generated tests for tools.boundary_canvas.boundary_engine.` |
| `tests/genesis_auto/test_cam_refactor_engine.py` | `migration_canvas` | 13 | `"""Auto-generated tests for tools.migration_canvas.cam_refactor_engine.` |
| `tests/genesis_auto/test_cloud_architecture.py` | `network` | 30 | `"""Auto-generated tests for tools.network.cloud_architecture.` |
| `tests/genesis_auto/test_compliance.py` | `network` | 12 | `"""Auto-generated tests for tools.network.compliance.` |
| `tests/genesis_auto/test_iac_generator.py` | `infra_canvas` | 13 | `"""Auto-generated tests for tools.infra_canvas.iac_generator.` |
| `tests/genesis_auto/test_network_migration.py` | `migration_canvas` | 23 | `"""Auto-generated tests for tools.migration_canvas.network_migration.` |
| `tests/genesis_auto/test_pattern_classifier.py` | `ai_augmentation` | 10 | `"""Auto-generated tests for tools.ai_augmentation.pattern_classifier.` |
| `tests/genesis_auto/test_security_engine.py` | `security_canvas` | 31 | `"""Auto-generated tests for tools.security_canvas.security_engine.` |
| `tests/genesis_auto/test_seed_sops.py` | `network` | 3 | `"""Auto-generated tests for tools.network.seed_sops.` |
| `tests/genesis_auto/test_server_migration.py` | `migration_canvas` | 24 | `"""Auto-generated tests for tools.migration_canvas.server_migration.` |
| `tests/genesis_auto/test_solution_packs.py` | `agentic_ai_canvas` | 6 | `"""Auto-generated tests for tools.agentic_ai_canvas.solution_packs.` |
| `tests/test_aac_pattern_classifier_anomaly.py` | `ai_augmentation` | 10 | `from tools.ai_augmentation.pattern_classifier import AnomalyThresholdDetector` |
| `tests/test_aac_threshold_anomaly_detection.py` | `ai_augmentation` | 3 | `from tools.ai_augmentation.pattern_classifier import (` |
| `tests/test_aadc_agent_layer.py` | `agentic_ai_canvas` | 1 | `from tools.agentic_ai_canvas.agent_layer import (` |
| `tests/test_aadc_confidence_gate.py` | `agentic_ai_canvas` | 4 | `from tools.agentic_ai_canvas.confidence_gate import (` |
| `tests/test_aadc_memory_layer.py` | `agentic_ai_canvas` | 2 | `from tools.agentic_ai_canvas.memory_layer import check_memory_layer, get_memory_layer_map` |
| `tests/test_agent_readiness_anomaly_detector.py` | `ai_augmentation` | 9 | `from tools.ai_augmentation.agent_readiness.pillars._base import (` |
| `tests/test_agent_readiness_base.py` | `ai_augmentation` | 17 | `import tools.ai_augmentation.agent_readiness.pillars._base as m` |
| `tests/test_agent_readiness_base_pillar_threshold.py` | `ai_augmentation` | 7 | `from tools.ai_augmentation.agent_readiness.pillars._base import (` |
| `tests/test_agent_readiness_checker.py` | `ai_augmentation` | 6 | `from tools.ai_augmentation.agent_readiness.checker import (` |
| `tests/test_agent_readiness_checker_weights.py` | `ai_augmentation` | 7 | `import tools.ai_augmentation.agent_readiness.checker as mod` |
| `tests/test_agent_readiness_configuration.py` | `ai_augmentation` | 19 | `from tools.ai_augmentation.agent_readiness.pillars.configuration import _load_thresholds` |
| `tests/test_agent_readiness_configuration_pillar.py` | `ai_augmentation` | 11 | `from tools.ai_augmentation.agent_readiness.pillars.configuration import (` |
| `tests/test_agent_readiness_dependencies_pillar.py` | `ai_augmentation` | 8 | `from tools.ai_augmentation.agent_readiness.pillars.dependencies import (` |
| `tests/test_agent_readiness_documentation_pillar.py` | `ai_augmentation` | 9 | `from tools.ai_augmentation.agent_readiness.pillars.documentation import (` |
| `tests/test_agent_readiness_il_classification_pillar.py` | `ai_augmentation` | 6 | `from tools.ai_augmentation.agent_readiness.pillars.il_classification import (` |
| `tests/test_agent_readiness_score_anomaly.py` | `ai_augmentation` | 7 | `import tools.ai_augmentation.agent_readiness.pillars._base as mod` |
| `tests/test_agent_readiness_structure_pillar.py` | `ai_augmentation` | 8 | `from tools.ai_augmentation.agent_readiness.pillars.structure import (` |
| `tests/test_ansible_emitter.py` | `infra_canvas` | 1 | `from tools.infra_canvas.emitters.ansible import (` |
| `tests/test_attack_graph_builder.py` | `security_canvas` | 1 | `from tools.security_canvas.attack_graph_builder import (` |
| `tests/test_attack_path_twin_predicates.py` | `security_canvas` | 2 | `from tools.security_canvas.attack_path_twin import _safe_eval_predicate` |
| `tests/test_attack_surface_mapper.py` | `network` | 22 | `from tools.network.attack_surface_mapper import _criticality_from_cvss` |
| `tests/test_aws_rgt_importer.py` | `infra_canvas` | 1 | `from tools.infra_canvas.importers.aws_rgt import parse` |
| `tests/test_bdc_export_pptx.py` | `boundary_canvas` | 5 | `from tools.boundary_canvas import export_pptx` |
| `tests/test_bgp_predictor.py` | `network` | 27 | `from tools.network.bgp_predictor import _compute_bgp_score` |
| `tests/test_caldera_adapter.py` | `security_canvas` | 1 | `from tools.security_canvas.caldera_adapter import CalderaAdapter` |
| `tests/test_capacity_predictor.py` | `network` | 24 | `from tools.network.capacity_predictor import _compute_capacity_score` |
| `tests/test_change_failure_predictor.py` | `network` | 14 | `from tools.network.change_failure_predictor import _count_blast_radius` |
| `tests/test_compliance_drift_predictor.py` | `network` | 20 | `from tools.network.compliance_drift_predictor import _run_stig_checks` |
| `tests/test_config_threshold_detector.py` | `ai_augmentation` | 18 | `"""Tests for tools.ai_augmentation.config_threshold_detector.` |
| `tests/test_cvx_sql05_named_params.py` | `migration_canvas` | 4 | `for mod in ("tools.migration_canvas.db.init_db", "tools.migration_canvas.server_migration"):` |
| `tests/test_data_lineage_route.py` | `data_canvas` | 10 | `patch("tools.data_canvas.db.init_db.init_db"),` |
| `tests/test_dcpr_audit_immutability.py` | `data_canvas` | 1 | `from tools.data_canvas.db import init_db as ddc_init` |
| `tests/test_dcpr_blueprint_resilience.py` | `data_canvas` | 9 | `with patch("tools.data_canvas.db.init_db.init_db"):` |
| `tests/test_dcpr_contract_engine.py` | `data_canvas` | 3 | `from tools.data_canvas.data_mesh import contract_engine as ce` |
| `tests/test_dcpr_data_engine.py` | `data_canvas` | 1 | `from tools.data_canvas import data_engine as de` |
| `tests/test_dcpr_domain_manager.py` | `data_canvas` | 3 | `from tools.data_canvas.data_mesh import domain_manager as dm` |
| `tests/test_dcpr_governance_engine.py` | `data_canvas` | 4 | `from tools.data_canvas import governance_engine as ge` |
| `tests/test_dcpr_lineage_emitter.py` | `data_canvas` | 3 | `from tools.data_canvas.data_mesh import lineage_emitter as le` |
| `tests/test_dcpr_product_registry.py` | `data_canvas` | 2 | `from tools.data_canvas.data_mesh import product_registry as pr` |
| `tests/test_dcpr_quality_engine.py` | `data_canvas` | 3 | `from tools.data_canvas import quality_engine as qe` |
| `tests/test_dcpr_query_sandbox.py` | `data_canvas` | 1 | `from tools.data_canvas.query_sandbox import validate_query` |
| `tests/test_dcpr_route_auth.py` | `data_canvas` | 1 | `from tools.data_canvas.blueprint import create_data_canvas_blueprint` |
| `tests/test_ddc_sync_adapters.py` | `data_canvas` | 16 | `from tools.data_canvas.sync.datahub_sync import (` |
| `tests/test_diagram_analysis.py` | `network` | 3 | `from tools.network.diagram_analysis import (` |
| `tests/test_dsyn_emit_network.py` | `network` | 6 | `with patch("tools.network.event_emitter.get_connection", _gc): yield shim` |
| `tests/test_elastic_export.py` | `observability_canvas` | 1 | `from tools.observability_canvas.exporters.elastic import sigma_to_eql` |
| `tests/test_eol_predictor.py` | `network` | 29 | `from tools.network.eol_predictor import _lookup_eos` |
| `tests/test_freshness_guardian_parity.py` | `data_canvas` | 4 | `Not a live outage: both reflex copies import `tools.data_canvas.*`, so the` |
| `tests/test_helm_emitter.py` | `infra_canvas` | 1 | `from tools.infra_canvas.emitters.helm import (` |
| `tests/test_history.py` | `network` | 21 | `"tools.network.db.init_db",` |
| `tests/test_iac_cli.py` | `infra_canvas` | 2 | `"""Tests for python -m tools.infra_canvas.emit (dt-idc-iac-07)."""` |
| `tests/test_idc_twin_phase1.py` | `infra_canvas` | 47 | `from tools.infra_canvas.terraform_show_importer import import_terraform_show` |
| `tests/test_intent_validator.py` | `network` | 2 | `"""Unit tests for tools.network.intent_validator — Intent-Based Validation Engine."""` |
| `tests/test_ip_address_space.py` | `network` | 1 | `from tools.network.ip_address_space import (` |
| `tests/test_juniper_filter_parsing.py` | `network` | 10 | `from tools.network.config_parser import parse_juniper` |
| `tests/test_mce_compliance_gate.py` | `migration_canvas` | 2 | `"""Tests for tools.migration_canvas.compliance_gate."""` |
| `tests/test_mce_dossier_advisor.py` | `migration_canvas` | 2 | `"""Tests for tools.migration_canvas.dossier_advisor."""` |
| `tests/test_mce_inventory_scanner.py` | `migration_canvas` | 2 | `"""Tests for tools.migration_canvas.inventory_scanner."""` |
| `tests/test_mce_wave_backout_validation.py` | `migration_canvas` | 7 | `from tools.migration_canvas import wave_planner as wp` |
| `tests/test_mce_wave_planner.py` | `migration_canvas` | 2 | `"""Tests for tools.migration_canvas.wave_planner."""` |
| `tests/test_migration_canvas_coa.py` | `migration_canvas` | 2 | `from tools.migration_canvas.db import init_db as init_db_mod` |
| `tests/test_migration_canvas_config_map.py` | `migration_canvas` | 9 | `from tools.migration_canvas.db import init_db as init_db_mod` |
| `tests/test_migration_canvas_topology.py` | `migration_canvas` | 2 | `from tools.migration_canvas.db import init_db as init_db_mod` |
| `tests/test_migration_dossier_advisor.py` | `migration_canvas` | 2 | `from tools.migration_canvas.db import init_db as init_db_mod` |
| `tests/test_migration_grounding.py` | `migration_canvas` | 2 | `from tools.migration_canvas.grounding import GROUNDING_WARNING, assess_response` |
| `tests/test_mitre_loader.py` | `observability_canvas` | 1 | `from tools.observability_canvas.mitre_loader import MitreTechnique, load_techniques` |
| `tests/test_nav_sec_05_strategos_rbac.py` | `security_canvas` | 1 | `from tools.security_canvas.blueprint import create_security_blueprint` |
| `tests/test_ndc_blueprint_route_parity.py` | `network` | 1 | `from tools.network.blueprint import create_network_blueprint` |
| `tests/test_ndc_bus_subscriber.py` | `canvas`, `network` | 11 | `from tools.canvas import event_bus` |
| `tests/test_ndc_failclosed_gates.py` | `network` | 6 | `from tools.network.routes import analysis as analysis_routes` |
| `tests/test_ndc_graph_cache.py` | `network` | 6 | `from tools.network import blueprint_helpers as bh` |
| `tests/test_ndc_narrative_cache.py` | `network` | 4 | `(tools.network.narrative_generator.LLMRouter), mirroring` |
| `tests/test_ndc_narrative_egress.py` | `network` | 2 | `(tools.network.narrative_generator.LLMRouter); the local/cloud decision uses the` |
| `tests/test_ndc_pdf_export.py` | `network` | 2 | `from tools.network.pdf_export import (` |
| `tests/test_ndc_pptx_export.py` | `network` | 12 | `from tools.network import blueprint_helpers as bh` |
| `tests/test_ndc_traffic_flow_schema_reconcile.py` | `network` | 2 | `from tools.network.db import init_db as m` |
| `tests/test_ndc_update_routes.py` | `network` | 2 | `from tools.network.db import init_db as ndc_init` |
| `tests/test_ndc_visio_export.py` | `network` | 2 | `from tools.network.visio_export import export_ops_csvs, export_vsdx` |
| `tests/test_network_check_constants.py` | `network` | 4 | `from tools.network.db import constants as C` |
| `tests/test_network_compliance_rname.py` | `network` | 1 | `from tools.network.compliance import validate_hostname_pattern` |
| `tests/test_network_ingester_e2e.py` | `network` | 16 | `patch("tools.network.network_ingester._extract_via_vision", return_value=vision_error),` |
| `tests/test_network_layout_and_config.py` | `network` | 4 | `"""Tests for tools.network.layout and tools.network.config_import."""` |
| `tests/test_nlp_extractor.py` | `ai_augmentation` | 9 | `"""Tests for tools.ai_augmentation.nlp_extractor."""` |
| `tests/test_noc_pg_lifecycle.py` | `noc_canvas` | 3 | `from tools.noc_canvas.alarm_correlator import create_alarm` |
| `tests/test_ocr_fallback.py` | `network` | 4 | `from tools.network.ocr_fallback import (` |
| `tests/test_odc_kill_chain_rls.py` | `observability_canvas` | 1 | `from tools.observability_canvas.blueprint import create_observability_blueprint` |
| `tests/test_odc_observability_engine.py` | `observability_canvas` | 4 | ```tools.observability_canvas.observability_engine``:` |
| `tests/test_odc_sigma_catalog.py` | `observability_canvas` | 2 | `from tools.observability_canvas import sigma_generator as sg  # noqa: E402` |
| `tests/test_openmetadata_client.py` | `data_canvas` | 1 | `from tools.data_canvas.clients.openmetadata import OpenMetadataClient` |
| `tests/test_opportunity_scorer.py` | `ai_augmentation` | 2 | `from tools.ai_augmentation.opportunity_scorer import (` |
| `tests/test_patch_planner.py` | `network` | 34 | `from tools.network.patch_planner import _site_from_device` |
| `tests/test_path_analyzer.py` | `network` | 4 | `"""Tests for tools.network.path_analyzer — NDC path reachability engine."""` |
| `tests/test_path_enumerator.py` | `security_canvas` | 2 | `"""Tests for tools.security_canvas.path_enumerator — 6 cases."""` |
| `tests/test_pattern_classifier_threshold_ad.py` | `ai_augmentation` | 1 | `import tools.ai_augmentation.pattern_classifier as pc` |
| `tests/test_pdf_import.py` | `network` | 2 | `"""Tests for tools.network.pdf_import — vector extraction + rasterizer.` |
| `tests/test_pdf_stitch_multi.py` | `network` | 2 | `from tools.network.pdf_import import import_pdf  # noqa: E402` |
| `tests/test_penta_aadc_cost.py` | `agentic_ai_canvas`, `aiml_canvas` | 5 | `from tools.agentic_ai_canvas import cost_estimator as ce  # noqa: E402` |
| `tests/test_penta_aadc_engine.py` | `agentic_ai_canvas` | 4 | `from tools.agentic_ai_canvas import agentic_engine as eng  # noqa: E402` |
| `tests/test_penta_aadc_p2.py` | `agentic_ai_canvas` | 15 | `bp = importlib.import_module("tools.agentic_ai_canvas.blueprint")` |
| `tests/test_penta_aimc_auth.py` | `aiml_canvas` | 2 | `import tools.aiml_canvas.db.init_db as aimc_init` |
| `tests/test_penta_aimc_engine.py` | `aiml_canvas` | 2 | `import tools.aiml_canvas.db.init_db as aimc_init` |
| `tests/test_penta_aimc_routes.py` | `aiml_canvas` | 14 | `import tools.aiml_canvas.db.init_db as aimc_init` |
| `tests/test_preapply_gate.py` | `infra_canvas` | 1 | `from tools.infra_canvas.preapply_gate import _compute_delta, run_gate` |
| `tests/test_pulumi_emitter.py` | `infra_canvas` | 1 | `from tools.infra_canvas.emitters.pulumi import UnsupportedResourceError, emit_resource` |
| `tests/test_pulumi_state_importer.py` | `infra_canvas` | 1 | `from tools.infra_canvas.importers.pulumi_state import parse` |
| `tests/test_qdc_canvas.py` | `qdc_canvas` | 10 | `from tools.qdc_canvas.qdc_engine import assess_quality_design` |
| `tests/test_remediation_simulator.py` | `network` | 36 | `"""Unit tests for tools.network.remediation_simulator.simulate_remediation."""` |
| `tests/test_replay_verify.py` | `observability_canvas` | 10 | `"""Tests for tools.observability_canvas.replay_verify (ODC closed-loop hook).` |
| `tests/test_roadmap_adaptive_thresholds.py` | `ai_augmentation` | 2 | `from tools.ai_augmentation.roadmap_generator import (` |
| `tests/test_sc_blueprint_errors.py` | `security_canvas` | 4 | `from tools.security_canvas.blueprint import create_security_blueprint` |
| `tests/test_sc_orchestrator_smoke.py` | `security_canvas` | 3 | `from tools.security_canvas import remediation` |
| `tests/test_sdc_attackpath_route.py` | `security_canvas` | 5 | `from tools.security_canvas.db.init_db import SCHEMA` |
| `tests/test_sdc_auth_apikey.py` | `security_canvas` | 2 | `from tools.security_canvas.blueprint import create_security_blueprint` |
| `tests/test_sdc_auth_meta.py` | `security_canvas` | 1 | `from tools.security_canvas.blueprint import create_security_blueprint` |
| `tests/test_sdc_lazy_tables.py` | `security_canvas` | 2 | `from tools.security_canvas.db import lazy_table_inventory as lti` |
| `tests/test_sdc_schema_parity.py` | `security_canvas` | 1 | `from tools.security_canvas.db import init_db` |
| `tests/test_sentinel_export.py` | `observability_canvas` | 1 | `from tools.observability_canvas.exporters.sentinel import sigma_to_kql` |
| `tests/test_sigma_generator.py` | `observability_canvas` | 2 | `"""Unit tests for tools.observability_canvas.sigma_generator — 5 cases."""` |
| `tests/test_splunk_export.py` | `observability_canvas` | 1 | `from tools.observability_canvas.exporters.splunk import sigma_to_spl` |
| `tests/test_stig_nlp_extractor.py` | `ai_augmentation` | 23 | `from tools.ai_augmentation.agent_readiness.pillars import stig_nlp_extractor  # noqa: F401` |
| `tests/test_supply_chain_risk_scorer.py` | `network` | 27 | `from tools.network.supply_chain_risk_scorer import _normalize_vendor` |
| `tests/test_terraform_emitter.py` | `infra_canvas` | 1 | `from tools.infra_canvas.emitters.terraform import UnsupportedResourceError, emit_resource` |
| `tests/test_threshold_advisor.py` | `ai_augmentation` | 10 | `"""Tests for tools.ai_augmentation.threshold_advisor.` |
| `tests/test_threshold_anomaly_detector.py` | `ai_augmentation` | 1 | `from tools.ai_augmentation.pattern_classifier import (` |
| `tests/test_traffic_flow_walkthrough.py` | `network` | 16 | `from tools.network.narrative_generator import generate_all` |
| `tests/test_vsdx_import.py` | `network` | 1 | `from tools.network.export_import import (` |
| `tests/test_vuln_predictor.py` | `network` | 15 | `from tools.network.vuln_predictor import _compute_scores` |
| `tests/test_workload_scanner_performance.py` | `migration_canvas` | 1 | `from tools.migration_canvas.inventory_scanner import (` |
| `tests/test_zig_external_targets.py` | `security_canvas` | 8 | `"tools.security_canvas.zig_activity_tracker",` |
| `tests/test_zig_ingest_adapters.py` | `security_canvas` | 71 | `# The adapter imports from tools.security_canvas.zig_activity_tracker` |
| `tests/test_zig_ingest_route.py` | `security_canvas` | 3 | `from tools.security_canvas.constants import ZIG_INGEST_MAX_BYTES` |
| `tests/test_zig_routes.py` | `security_canvas` | 4 | `import tools.security_canvas.db.init_db as idb` |
| `tests/viz/test_epic_d_reuse.py` | `agentic_ai_canvas` | 3 | `from tools.agentic_ai_canvas.export_pdf import generate_pdf` |

### Tier B — canvas plus other subsystems (87)

| file | packages | matches | first match |
|------|----------|---------|-------------|
| `tests/conftest.py` | `ccc_canvas`, `dsoc_canvas`, `noc_canvas` | 3 | `from tools.ccc_canvas.db.init_db import init_db, get_connection` |
| `tests/e2e_canvas_orchestration.py` | `boundary_canvas`, `canvas`, `security_canvas` | 3 | `from tools.boundary_canvas.isa_expiry import check_isa_expiry` |
| `tests/e2e_dual_track_lifecycle.py` | `canvas`, `network` | 5 | `from tools.canvas.kg_builder import rebuild_canvas_kg` |
| `tests/e2e_infra_twin.py` | `infra_canvas` | 2 | `from tools.infra_canvas.db.init_db import get_connection` |
| `tests/e2e_ndc_full_lifecycle.py` | `canvas`, `network` | 5 | `from tools.canvas.kg_builder import rebuild_canvas_kg` |
| `tests/genesis_auto/test_blueprint.py` | `data_canvas` | 5 | `"""Auto-generated tests for tools.data_canvas.blueprint.` |
| `tests/test_aac_anomaly_thresholds.py` | `ai_augmentation` | 1 | `from tools.ai_augmentation.engine import (` |
| `tests/test_aac_llm_http_auth.py` | `ai_augmentation` | 2 | `from tools.ai_augmentation.implementations.llm_http_auth import (` |
| `tests/test_aac_nlp_ref_extractor.py` | `ai_augmentation` | 2 | `"""Tests for the NLP ref extractor in tools.ai_augmentation.engine.` |
| `tests/test_aadc_governance_layer.py` | `agentic_ai_canvas` | 3 | `from tools.agentic_ai_canvas.governance_layer import (` |
| `tests/test_aadc_model_layer.py` | `agentic_ai_canvas` | 6 | `from tools.agentic_ai_canvas.model_layer import (` |
| `tests/test_advisory.py` | `network` | 48 | `return patch("tools.network.advisory._get_conn", return_value=fake)` |
| `tests/test_agent_readiness_anomaly.py` | `ai_augmentation` | 3 | `from tools.ai_augmentation.agent_readiness.pillars._base import (` |
| `tests/test_agent_readiness_nist_controls_pillar.py` | `ai_augmentation` | 14 | `from tools.ai_augmentation.agent_readiness.pillars.nist_controls import (` |
| `tests/test_agent_readiness_stig_compliance_pillar.py` | `ai_augmentation` | 33 | `from tools.ai_augmentation.agent_readiness.pillars.stig_compliance import (` |
| `tests/test_agent_readiness_stig_pillar.py` | `ai_augmentation` | 13 | `from tools.ai_augmentation.agent_readiness.pillars.stig_compliance import (` |
| `tests/test_aiml_twin.py` | `aiml_canvas` | 1 | `from tools.aiml_canvas import twin as aiml_twin` |
| `tests/test_append_only_audit_nlp.py` | `ai_augmentation` | 1 | `import tools.ai_augmentation.agent_readiness.pillars.append_only_audit as m` |
| `tests/test_attack_graph_db.py` | `security_canvas` | 4 | `"""Tests for tools.security_canvas.attack_graph_db — 6-function CRUD layer."""` |
| `tests/test_bdc_cato_readiness.py` | `boundary_canvas` | 5 | `from tools.boundary_canvas import cato_readiness as cr` |
| `tests/test_bdc_hook_logging.py` | `boundary_canvas` | 8 | `import tools.boundary_canvas.db.init_db  # noqa: F401` |
| `tests/test_bdc_impact_panel.py` | `boundary_canvas` | 2 | `from tools.boundary_canvas.blueprint import create_boundary_blueprint` |
| `tests/test_bdc_init_db_sqlite_fallback.py` | `boundary_canvas` | 1 | `import tools.boundary_canvas.db.init_db as bdc_init` |
| `tests/test_bdc_isa_expiry.py` | `boundary_canvas` | 15 | `with patch("tools.boundary_canvas.db.init_db.get_connection", side_effect=lambda: _NoClose(bd...` |
| `tests/test_bdc_oscal_exporter.py` | `boundary_canvas` | 7 | `from tools.boundary_canvas import oscal_cato_exporter as ox` |
| `tests/test_bdc_poam_generator_fk.py` | `boundary_canvas` | 2 | `from tools.boundary_canvas.cato_twin import poam_auto_generator as _pag  # noqa: E402` |
| `tests/test_bdc_twin_honesty.py` | `boundary_canvas` | 18 | `with patch("tools.boundary_canvas.twin.get_connection", return_value=_RaisingConn()):` |
| `tests/test_bdr_supply_chain_crosslink.py` | `boundary_canvas` | 4 | `import tools.boundary_canvas.db.init_db as bdc_init` |
| `tests/test_bdr_vv_suite.py` | `boundary_canvas` | 12 | `from tools.boundary_canvas.blueprint import create_boundary_blueprint` |
| `tests/test_cato_twin.py` | `boundary_canvas` | 10 | `from tools.boundary_canvas.cato_twin.snapshot_writer import write_snapshot` |
| `tests/test_cnr_ops_mutation_auth.py` | `noc_canvas` | 1 | `from tools.noc_canvas.blueprint import create_noc_canvas_blueprint` |
| `tests/test_crx_soar_lite.py` | `security_canvas` | 1 | `from tools.security_canvas import soar_lite` |
| `tests/test_cvx_sql06_datacanvas_placeholders.py` | `data_canvas` | 4 | `# test in the run resolves tools.data_canvas.db.init_db to that replacement` |
| `tests/test_cvx_sql07_datacanvas_pct_s.py` | `data_canvas` | 5 | `# test in the run resolves tools.data_canvas.db.init_db to that replacement` |
| `tests/test_cvx_sql_03_canvas_init_rls.py` | `aimc`, `infra_canvas`, `network` | 7 | `from tools.aimc.db import init_db as aimc_init` |
| `tests/test_dcpr_ai_mapper_llm.py` | `data_canvas` | 1 | `from tools.data_canvas import ai_mapper` |
| `tests/test_dcpr_anomaly_detector.py` | `data_canvas` | 1 | `from tools.data_canvas import anomaly_detector as ad` |
| `tests/test_dcpr_blueprint_hygiene.py` | `data_canvas` | 2 | `with patch("tools.data_canvas.db.init_db.init_db"):` |
| `tests/test_dcpr_profiler_pii.py` | `data_canvas` | 1 | `from tools.data_canvas import data_profiler, pii_scanner` |
| `tests/test_dcpr_twin.py` | `data_canvas` | 1 | `from tools.data_canvas import twin` |
| `tests/test_dsoc_canvas.py` | `dsoc_canvas` | 3 | `import tools.dsoc_canvas.db.init_db as init_mod` |
| `tests/test_dsyn_emit_ndc.py` | `network` | 3 | `import tools.network.network_intelligence as ni` |
| `tests/test_dsyn_vv_smoke.py` | `network` | 1 | `from tools.network import event_emitter` |
| `tests/test_engine_nlp_input_classifier.py` | `ai_augmentation` | 1 | `import tools.ai_augmentation.engine as engine` |
| `tests/test_event_bus_canvas_rls.py` | `canvas` | 2 | `from tools.canvas import event_bus` |
| `tests/test_event_bus_security.py` | `canvas` | 2 | `from tools.canvas.event_bus import (` |
| `tests/test_federal_peering_request.py` | `network` | 1 | `from tools.network.federal_peering_request import (` |
| `tests/test_guardrails.py` | `network` | 18 | `from tools.network.blueprint import create_network_blueprint` |
| `tests/test_iqe_ext_lineage.py` | `data_canvas` | 9 | `canonical DDC DDL (``tools.data_canvas.db.init_db.SCHEMA``), so column/DDL drift` |
| `tests/test_iqe_nl_to_iqe.py` | `network` | 1 | `from tools.network.nl_query import (` |
| `tests/test_mitre_catalog.py` | `observability_canvas` | 13 | `from tools.observability_canvas import mitre_catalog` |
| `tests/test_mitre_coverage_db.py` | `observability_canvas` | 1 | `import tools.observability_canvas.mitre_coverage_db as mod` |
| `tests/test_mitre_coverage_twin.py` | `observability_canvas` | 3 | `from tools.observability_canvas import mitre_coverage_twin as mct  # noqa: E402` |
| `tests/test_ndc_backend_helpers.py` | `network` | 5 | `from tools.network import blueprint_helpers as bh` |
| `tests/test_ndc_demo_runner.py` | `network` | 1 | `from tools.network.blueprint import create_network_blueprint` |
| `tests/test_ndc_enclave_scanner.py` | `network` | 2 | `from tools.network import enclave_scanner as es` |
| `tests/test_ndc_migration_phases.py` | `network` | 1 | `from tools.network import migration_phases as mp` |
| `tests/test_ndc_sdc_idc_templates.py` | `infra_canvas`, `network`, `security_canvas` | 3 | `from tools.infra_canvas.blueprint import infra_bp` |
| `tests/test_ndc_stencil_importer.py` | `network` | 2 | `from tools.network import stencil_importer as si` |
| `tests/test_ndc_topology_validator.py` | `network` | 3 | `from tools.network import topology_validator as tv` |
| `tests/test_network_config_review.py` | `network` | 12 | `- tools.network.config_review pure functions` |
| `tests/test_network_doc_lifecycle.py` | `network` | 20 | `from tools.network.synthetic_config_gen import generate_topology` |
| `tests/test_network_intelligence.py` | `network` | 27 | `from tools.network.network_ingester import _classify_device_type` |
| `tests/test_nqe_client.py` | `network` | 12 | `from tools.network.nqe_client import NQEClient` |
| `tests/test_odc_api_hygiene.py` | `observability_canvas` | 4 | `from tools.observability_canvas.blueprint import create_observability_blueprint` |
| `tests/test_odc_bus_subscriber.py` | `observability_canvas` | 10 | `init_db_mod = importlib.import_module("tools.observability_canvas.db.init_db")` |
| `tests/test_odc_ndc_check.py` | `observability_canvas` | 4 | ```tools.observability_canvas.observability_engine``:` |
| `tests/test_odc_sops_runbooks.py` | `observability_canvas` | 17 | `getter (tools.observability_canvas.db.init_db.get_connection) monkeypatched —` |
| `tests/test_odc_twin.py` | `observability_canvas` | 14 | `(tools.observability_canvas.db.init_db.get_connection) is monkeypatched — shim` |
| `tests/test_oracle_lens_network.py` | `network` | 3 | `These tests seed a temp SQLite canvas DB via ``tools.network.db.init_db`` with` |
| `tests/test_penta_aadc_auth.py` | `agentic_ai_canvas` | 1 | `import tools.agentic_ai_canvas.blueprint as bp  # noqa: E402` |
| `tests/test_penta_aadc_compliance.py` | `aadc`, `agentic_ai_canvas` | 8 | `import tools.aadc.compliance_checker as cc  # noqa: E402` |
| `tests/test_penta_aadc_routes.py` | `agentic_ai_canvas` | 1 | `import tools.agentic_ai_canvas.blueprint as bp  # noqa: E402` |
| `tests/test_penta_aimc_nodes.py` | `aiml_canvas` | 7 | `import tools.aiml_canvas.db.init_db as aimc_init` |
| `tests/test_penta_aimc_p2.py` | `aiml_canvas` | 6 | `import tools.aiml_canvas.db.init_db as aimc_init` |
| `tests/test_penta_aimc_scanner.py` | `aimc` | 13 | `from tools.aimc.db.init_db import init_db` |
| `tests/test_sc_artifacts.py` | `security_canvas` | 3 | `from tools.security_canvas.artifacts import (` |
| `tests/test_sc_posture_summary.py` | `security_canvas` | 2 | `from tools.security_canvas.db import init_db as init_db_mod` |
| `tests/test_sdc_demo_runner.py` | `security_canvas` | 1 | `from tools.security_canvas.db import init_db as _sc_init` |
| `tests/test_tfw_narrator.py` | `network` | 18 | `from tools.network.narrative_generator import detect_csp` |
| `tests/test_tfw_personas.py` | `network` | 13 | `from tools.network.narrative_generator import load_personas` |
| `tests/test_twx_cov_twins.py` | `agentic_ai_canvas`, `qdc_canvas` | 2 | `from tools.agentic_ai_canvas import twin as aadc_twin` |
| `tests/test_vuln_triage_engine.py` | `network` | 28 | `from tools.network.vuln_triage_engine import _compute_priority` |
| `tests/test_zig_scoring.py` | `security_canvas` | 40 | `from tools.security_canvas.constants import ZIG_PILLARS  # noqa: E402` |
| `tests/test_zig_sql_placeholders.py` | `security_canvas` | 18 | `from tools.security_canvas.zig_activity_tracker import get_phase_completions` |
| `tests/test_zt_fail_closed_sweep.py` | `security_canvas` | 3 | `import tools.security_canvas.device_compliance_scanner as _dcs` |
| `tests/test_zt_stub_gate.py` | `security_canvas` | 1 | `import tools.security_canvas.device_compliance_scanner as _dcs  # noqa: E402` |

### Tier C — patch-target / string reference only (38)

| file | packages | matches | first match |
|------|----------|---------|-------------|
| `tests/security/test_dast_gate_fail_closed.py` | `security_canvas` | 3 | `drg = importlib.import_module("tools.security_canvas.dast_runtime_gates")` |
| `tests/test_aac_score_anomalies.py` | `ai_augmentation` | 1 | `"""Tests for detect_score_anomalies() in tools.ai_augmentation.engine.` |
| `tests/test_aca_vv_integrity_refusal.py` | `ai_augmentation` | 2 | `# tools.ai_augmentation.agent_readiness.checker). The grader is fine; the exercise` |
| `tests/test_bdc_reflex_smoke.py` | `boundary_canvas` | 2 | `isa_mod = importlib.import_module("tools.boundary_canvas.isa_expiry")` |
| `tests/test_canvas_cards_pg.py` | `boundary_canvas`, `canvas_compliance`, `data_canvas`, `infra_canvas`, `security_canvas` | 5 | `_BDC_INIT = "tools.boundary_canvas.db.init_db"` |
| `tests/test_cnr_ops_cache_lookingglass.py` | `noc_canvas` | 1 | `mod = importlib.import_module("tools.noc_canvas.blueprint")` |
| `tests/test_component_registry.py` | `agentic_ai_canvas`, `aiml_canvas`, `boundary_canvas`, `ccc_canvas`, `data_canvas`, `dsoc_canvas`, `infra_canvas`, `migration_canvas`, `network`, `noc_canvas`, `observability_canvas`, `qdc_canvas`, `security_canvas` | 13 | `("aadc", "ICDEV_AADC_ENABLED", "tools.agentic_ai_canvas.blueprint", "aadc_bp"),` |
| `tests/test_dcpr_conn_sweep.py` | `data_canvas` | 5 | `mod = importlib.import_module("tools.data_canvas.anomaly_detector")` |
| `tests/test_dcpr_ext_access_failclosed.py` | `data_canvas` | 1 | `_GOV = importlib.import_module("tools.data_canvas.data_mesh.governance_engine")` |
| `tests/test_dcpr_freshness_guardian.py` | `data_canvas` | 2 | ```tools.data_canvas.freshness_guardian.check_profile_freshness``:` |
| `tests/test_dcpr_governance_access.py` | `data_canvas` | 2 | ```_eval_rule`` in ``tools.data_canvas.governance_engine``:` |
| `tests/test_dwo_bus_subscriber.py` | `canvas` | 1 | `event_bus = importlib.import_module("tools.canvas.event_bus")` |
| `tests/test_idc_emit_page.py` | `infra_canvas` | 1 | `with patch("tools.infra_canvas.blueprint._get_conn", side_effect=_fake_get_conn), \` |
| `tests/test_infra_twin_route.py` | `infra_canvas` | 1 | `with patch("tools.infra_canvas.blueprint._get_conn", side_effect=_fake_get_conn), \` |
| `tests/test_iqe_bi_dashboard_adapter.py` | `data_canvas` | 1 | `a REAL ``tools.data_canvas.query_sandbox.execute_query`` against a seeded SQLite` |
| `tests/test_iqe_ext_governance.py` | `data_canvas` | 2 | `_INIT_DB = importlib.import_module("tools.data_canvas.db.init_db")` |
| `tests/test_nav_llm_01_router_invoke.py` | `ai_augmentation`, `noc_canvas` | 4 | `"tools.ai_augmentation.implementations.llm_http_auth",` |
| `tests/test_observability_mitre_route.py` | `observability_canvas` | 2 | `patch("tools.observability_canvas.db.init_db.get_connection", side_effect=_fake_get_conn),` |
| `tests/test_odc_compliance_card.py` | `canvas_compliance`, `observability_canvas` | 3 | `_COMPLIANCE = "tools.canvas_compliance.compliance"` |
| `tests/test_odc_coverage_refresh.py` | `observability_canvas` | 1 | `init_db = importlib.import_module("tools.observability_canvas.db.init_db")` |
| `tests/test_odc_coverage_routes.py` | `canvas_compliance`, `observability_canvas` | 6 | `(``tools.canvas_compliance.compliance.get_odc_card``) stayed empty in production.` |
| `tests/test_odc_graceful_pages.py` | `observability_canvas` | 2 | `init_db_mod = importlib.import_module("tools.observability_canvas.db.init_db")` |
| `tests/test_odc_twin_snapshots.py` | `observability_canvas` | 5 | `init_db_mod = importlib.import_module("tools.observability_canvas.db.init_db")` |
| `tests/test_pdc_routes_misc.py` | `canvas` | 4 | `with patch("tools.canvas.collaboration.get_connection",` |
| `tests/test_penta_aadc_initdb.py` | `agentic_ai_canvas` | 1 | `mod = importlib.import_module("tools.agentic_ai_canvas.db.init_db")` |
| `tests/test_pipeline_audit_coverage.py` | `canvas` | 1 | `with patch("tools.canvas.collaboration.CanvasCollabManager.join", return_value={"ok": True}), \` |
| `tests/test_pipeline_rbac_sod.py` | `canvas` | 2 | `with patch("tools.canvas.collaboration.CanvasCollabManager.push", return_value=7) as push_mock:` |
| `tests/test_pmc_ccc_dsoc_templates.py` | `ccc_canvas`, `dsoc_canvas` | 4 | `mod = importlib.import_module("tools.ccc_canvas.blueprint")` |
| `tests/test_sc_agent_llm.py` | `security_canvas` | 5 | `Security Canvas LLM adapter (``tools.security_canvas.llm_adapter.generate``).` |
| `tests/test_sc_llm_adapter.py` | `security_canvas` | 1 | `adapter = importlib.import_module("tools.security_canvas.llm_adapter")` |
| `tests/test_sc_nl_query_llm.py` | `security_canvas` | 5 | `routes its open-ended fallback through ``tools.security_canvas.llm_adapter.generate``` |
| `tests/test_system_graph_ndc.py` | `network` | 1 | `monkeypatch.setattr("tools.network.db.init_db.get_connection", _boom)` |
| `tests/test_twin_airgap_rules.py` | `infra_canvas`, `network` | 2 | `mod = importlib.import_module("tools.infra_canvas.preapply_gate")` |
| `tests/test_twin_core.py` | `network` | 1 | `ndc_mod = importlib.import_module("tools.network.twin")` |
| `tests/test_twin_core_event_bridge.py` | `boundary_canvas`, `canvas`, `network` | 4 | `bdc = importlib.import_module("tools.boundary_canvas.twin")` |
| `tests/test_twin_core_observer.py` | `boundary_canvas`, `data_canvas`, `infra_canvas`, `observability_canvas`, `security_canvas` | 6 | `mod = importlib.import_module("tools.boundary_canvas.twin")` |
| `tests/test_twin_freshness_sweep.py` | `canvas` | 1 | `bus = importlib.import_module("tools.canvas.event_bus")` |
| `tests/test_twin_target_presets.py` | `infra_canvas` | 1 | `mod = importlib.import_module("tools.infra_canvas.preapply_gate")` |

## Machine-readable companions

- `docs/testing/tsr-canv-01-slice.txt` — the 274 paths, one per line, for `pytest @file`-style use.
- `docs/testing/tsr-canv-01-inventory.json` — per-file packages, match counts and line numbers.
