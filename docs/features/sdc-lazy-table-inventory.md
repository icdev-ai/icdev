<!-- CUI // SP-CTI -->
# SDC Lazy Pillar-Engine Table Inventory

> **GENERATED FILE -- do not edit by hand.** Regenerate with `python tools/security_canvas/db/lazy_table_inventory.py --markdown`.

The Security Design Canvas (SDC) ZIG pillar engines, managers, and orchestrators create their backing tables lazily via per-module `_ensure_tables(conn)` functions (idempotent `CREATE TABLE IF NOT EXISTS`). Decision (shx-db-03): keep the lazy pattern but make it auditable. This inventory is the audit surface -- `tests/test_sdc_lazy_tables.py` fails if it drifts from source, and asserts each `_ensure_tables` is idempotent.

- **Modules with lazy tables:** 25
- **Distinct tables:** 53
- **Table declarations (incl. duplicates across modules):** 53

| Module | Tables |
|--------|--------|
| `app_access_controller.py` | `zig_app_access_decisions`, `zig_app_behavior_baseline` |
| `automation_exchange.py` | `zig_automation_feedback`, `zig_openc2_commands`, `zig_taxii_exchanges` |
| `compliance_kg.py` | `kg_edges`, `kg_graphs`, `kg_nodes` |
| `continuous_authorization.py` | `zig_app_monitoring_events`, `zig_continuous_ato` |
| `dast_runtime_gates.py` | `zig_dast_gate_results`, `zig_dast_scans` |
| `data_access_governor.py` | `zig_data_access_baseline`, `zig_data_access_events` |
| `data_dlp_engine.py` | `zig_dlp_events`, `zig_encrypt_in_use` |
| `data_rights_manager.py` | `zig_drm_access_log`, `zig_drm_documents`, `zig_drm_grants` |
| `device_attestation_engine.py` | `zig_device_attestations` |
| `device_compliance_scanner.py` | `zig_device_compliance_scans`, `zig_device_registry` |
| `device_xdr_engine.py` | `zig_device_remediations`, `zig_patch_compliance`, `zig_xdr_correlations` |
| `edr_deployment_controller.py` | `zig_edr_agents` |
| `identity_governance.py` | `zig_access_certifications`, `zig_entitlement_findings`, `zig_federation_trusts` |
| `lateral_movement_detector.py` | `zig_lateral_movement_events`, `zig_quarantine` |
| `mdm_enrollment_manager.py` | `zig_mdm_enrollments` |
| `mfa_manager.py` | `zig_mfa_challenges`, `zig_mfa_enrollments` |
| `nac_enforcer.py` | `zig_nac_device_allowlist`, `zig_nac_events` |
| `network_segmentation.py` | `zig_segmentation_evaluations`, `zig_segmentation_policies` |
| `pam_manager.py` | `zig_pam_sessions`, `zig_pam_vault` |
| `sdn_policy_engine.py` | `zig_sdn_intents`, `zig_sdn_posture` |
| `soar_engine.py` | `zig_adaptive_access_policy`, `zig_soar_executions` |
| `threat_intel_engine.py` | `zig_threat_hunts`, `zig_ti_indicators`, `zig_ti_matches` |
| `ueba_engine.py` | `zig_ueba_anomalies`, `zig_ueba_baselines`, `zig_ueba_correlations` |
| `user_risk_engine.py` | `zig_user_behavior_profile`, `zig_user_risk_scores` |
| `ztna_gateway.py` | `zig_ztna_sessions` |
