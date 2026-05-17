# CUI // SP-CTI
"""Step definitions for ICDEV™ functional infrastructure BDD scenarios.

Validates that the platform infrastructure required for functional capabilities
is present and operational: environment setup, CI/CD pipeline configuration,
security hardening, and compliance scaffolding. Steps check file existence
without subprocess or network calls for fast, dependency-free execution.
"""

import os
import sqlite3

from behave import given, then, when

_FUNCTIONAL_ARTIFACTS = [
    '.env.example',
    'requirements.txt',
    'tools/testing/health_check.py',
    'tools/ci/pipeline_config_generator.py',
    'args/cicd_config.yaml',
    'tools/devsecops/pipeline_security_generator.py',
    'args/security_gates.yaml',
    'tools/compliance/classification_manager.py',
    'tools/compliance/control_mapper.py',
]

_INTERFACE_ARTIFACTS = [
    'tools/mosa/icd_generator.py',
    'tools/mosa/modular_design_analyzer.py',
    'tools/compliance/mosa_assessor.py',
    'args/mosa_config.yaml',
    'args/security_gates.yaml',
]

_DEPLOYMENT_ARTIFACTS = [
    'tools/infra/iac_generator.py',
    'tools/infra/terraform_generator.py',
    'tools/infra/ansible_generator.py',
    'tools/infra/k8s_generator.py',
    'tools/infra/dockerfile_generator.py',
    'args/deployment_profiles.yaml',
    'args/infra_canvas_config.yaml',
]

_WATCHCON_ARTIFACTS = [
    'tools/monitor/constants.py',
    'tools/monitor/watchcon.py',
    'tests/test_watchcon_tiers.py',
]

_WATCHCON_TIERS = {
    4: 'routine',
    3: 'elevated',
    2: 'high',
}


@given('the system is operational and the user is authenticated')
def step_system_operational(context):
    context.project_root = os.getcwd()
    assert os.path.isdir(context.project_root), (
        f"Project root not found: {context.project_root}"
    )


@given('all external system interfaces are connected and operational')
def step_interfaces_operational(context):
    context.project_root = os.getcwd()
    assert os.path.isdir(context.project_root), (
        f"Project root not found: {context.project_root}"
    )


# ---------------------------------------------------------------------------
# When steps
# ---------------------------------------------------------------------------

_IL5_ARTIFACTS = [
    'tools/il5/ingestion.py',
]

_IL5_SLA_SECONDS = 30


@when('Support three alert tiers: WATCHCON 4 (routine), WATCHCON 3 (elevated), and WATCHCON 2 (high)')
def step_watchcon_tiers(context):
    context.missing = [
        a for a in _WATCHCON_ARTIFACTS
        if not os.path.exists(os.path.join(context.project_root, a))
    ]
    if context.missing:
        return

    # Verify constants module defines all three tiers
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        'watchcon_constants',
        os.path.join(context.project_root, 'tools', 'monitor', 'constants.py'),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for tier, label in _WATCHCON_TIERS.items():
        if not hasattr(mod, f'WATCHCON_{tier}'):
            context.missing.append(f"Missing constant WATCHCON_{tier}")
            continue
        if getattr(mod, f'WATCHCON_{tier}') != tier:
            context.missing.append(f"WATCHCON_{tier} value mismatch")
        if mod.WATCHCON_LABELS.get(tier) != label:
            context.missing.append(f"WATCHCON_{tier} label mismatch: expected {label}")

    # Verify watchcon.py module can classify, insert, and query by tier
    spec2 = importlib.util.spec_from_file_location(
        'watchcon',
        os.path.join(context.project_root, 'tools', 'monitor', 'watchcon.py'),
    )
    wc_mod = importlib.util.module_from_spec(spec2)
    spec2.loader.exec_module(wc_mod)
    db_path = os.path.join(context.project_root, 'data', 'icdev.db')
    if os.path.exists(db_path):
        summary = wc_mod.tier_summary(db_path=db_path)
        tiers = summary.get('tiers', {})
        for tier_key in ('2', '3', '4'):
            if tier_key not in tiers:
                context.missing.append(f"tier_summary missing tier {tier_key}")


@when('Support IL5 data ingestion and display within 30 seconds of source publication')
def step_il5_ingestion_sla(context):
    context.missing = [
        a for a in _IL5_ARTIFACTS
        if not os.path.exists(os.path.join(context.project_root, a))
    ]
    if not context.missing:
        # Verify the SLA constant matches the requirement
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            'il5_ingestion',
            os.path.join(context.project_root, 'tools', 'il5', 'ingestion.py'),
        )
        mod = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(mod)
            if getattr(mod, 'SLA_SECONDS', None) != _IL5_SLA_SECONDS:
                context.missing.append(
                    f'SLA_SECONDS mismatch: expected {_IL5_SLA_SECONDS}, '
                    f'got {getattr(mod, "SLA_SECONDS", None)}'
                )
        except Exception as exc:
            context.missing.append(f'il5/ingestion.py import error: {exc}')


_DAT_ARTIFACTS = [
    'tools/dat/ingestion_engine.py',
    'tools/dat/dti_calculator.py',
    'tools/dat/dti_update_runner.py',
    'tools/dashboard/templates/strategos/dat.html',
    'tools/dat/icdev_dat_scheduler_task.xml',
    'args/dat_config.yaml',
    'tools/strategos/dat.py',
]


@when('the Diplomatic Activity Tracker capability is verified')
def step_verify_dat_capability(context):
    context.missing = [
        a for a in _DAT_ARTIFACTS
        if not os.path.exists(os.path.join(context.project_root, a))
    ]
    if not context.missing:
        # Verify DTI can be computed end-to-end
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location(
                'dti_calculator',
                os.path.join(context.project_root, 'tools', 'dat', 'dti_calculator.py'),
            )
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            sample_manifest = {
                'cables': [
                    {'tension_level': 'high', 'received_at': '2026-05-16T00:00:00+00:00'},
                ],
                'schedules': [
                    {'emergency': True, 'veto_cast': False, 'walkout': False},
                ],
                'metadata': [
                    {'escalation_flag': True, 'communication_breakdown': False, 'frequency_delta': -0.5},
                ],
            }
            score = mod.compute_dti_from_manifest(sample_manifest)
            if not (0.0 <= score <= 1.0):
                context.missing.append(f'DTI score {score} out of expected [0.0, 1.0] range')
        except Exception as exc:
            context.missing.append(f'DTI computation verification failed: {exc}')


@when(
    'Infrastructure and platform enablement for functional capabilities. '
    'Covers environment setup, CI/CD pipeline configuration, security '
    'hardening, and compliance scaffolding required to support 5 functional '
    'requirement(s).'
)
def step_functional_enablement(context):
    context.missing = [
        a for a in _FUNCTIONAL_ARTIFACTS
        if not os.path.exists(os.path.join(context.project_root, a))
    ]


@when(
    'Infrastructure and platform enablement for interface capabilities. '
    'Covers environment setup, CI/CD pipeline configuration, security '
    'hardening, and compliance scaffolding required to support 2 interface '
    'requirement(s).'
)
def step_interface_enablement(context):
    context.missing = [
        a for a in _INTERFACE_ARTIFACTS
        if not os.path.exists(os.path.join(context.project_root, a))
    ]


@when('the ICD generation infrastructure is verified')
def step_verify_icd_gen(context):
    context.check_paths = [
        'tools/mosa/icd_generator.py',
        'args/mosa_config.yaml',
    ]


@when('the compliance scaffolding for interfaces is verified')
def step_verify_interface_compliance(context):
    context.check_paths = [
        'tools/compliance/mosa_assessor.py',
        'tools/mosa/modular_design_analyzer.py',
    ]


@when('the environment setup is verified')
def step_verify_env_setup(context):
    context.check_paths = [
        '.env.example',
        'requirements.txt',
        'tools/testing/health_check.py',
    ]


@when('the CI/CD pipeline configuration is verified')
def step_verify_cicd(context):
    context.check_paths = [
        'tools/ci/pipeline_config_generator.py',
        'args/cicd_config.yaml',
    ]


@when('the security hardening configuration is verified')
def step_verify_security_hardening(context):
    context.check_paths = [
        'tools/devsecops/pipeline_security_generator.py',
        'args/security_gates.yaml',
    ]


@when('the compliance scaffolding is verified')
def step_verify_compliance(context):
    context.check_paths = [
        'tools/compliance/classification_manager.py',
        'tools/compliance/control_mapper.py',
    ]


@when(
    'Infrastructure and platform enablement for deployment capabilities. '
    'Covers IaC generation, container tooling, and deployment configuration '
    'required to support 3 deployment requirement(s).'
)
def step_deployment_enablement(context):
    context.missing = [
        a for a in _DEPLOYMENT_ARTIFACTS
        if not os.path.exists(os.path.join(context.project_root, a))
    ]


@when('the IaC generation tooling is verified')
def step_verify_iac_tooling(context):
    context.check_paths = [
        'tools/infra/iac_generator.py',
        'tools/infra/terraform_generator.py',
        'tools/infra/ansible_generator.py',
    ]


@when('the container and orchestration tooling is verified')
def step_verify_container_tooling(context):
    context.check_paths = [
        'tools/infra/k8s_generator.py',
        'tools/infra/dockerfile_generator.py',
    ]


@when('the deployment configuration is verified')
def step_verify_deployment_config(context):
    context.check_paths = [
        'args/deployment_profiles.yaml',
        'args/infra_canvas_config.yaml',
    ]


# ---------------------------------------------------------------------------
# Then steps
# ---------------------------------------------------------------------------

def _assert_path(context, path):
    full = os.path.join(context.project_root, path)
    assert os.path.exists(full), f"Required artifact not found: {full}"


@then('the environment template exists at "{path}"')
def step_env_template_exists(context, path):
    _assert_path(context, path)


@then('the dependency manifest exists at "{path}"')
def step_dep_manifest_exists(context, path):
    _assert_path(context, path)


@then('the health check tool exists at "{path}"')
def step_health_check_exists(context, path):
    _assert_path(context, path)


@then('the pipeline config generator exists at "{path}"')
def step_pipeline_config_exists(context, path):
    _assert_path(context, path)


@then('the CI/CD config file exists at "{path}"')
def step_cicd_config_exists(context, path):
    _assert_path(context, path)


@then('the pipeline security generator exists at "{path}"')
def step_pipeline_security_exists(context, path):
    _assert_path(context, path)


@then('the security gates config exists at "{path}"')
def step_security_gates_exists(context, path):
    _assert_path(context, path)


@then('the classification manager exists at "{path}"')
def step_classification_manager_exists(context, path):
    _assert_path(context, path)


@then('the control mapper exists at "{path}"')
def step_control_mapper_exists(context, path):
    _assert_path(context, path)


@then('the ICD generator exists at "{path}"')
def step_icd_generator_exists(context, path):
    _assert_path(context, path)


@then('the MOSA configuration exists at "{path}"')
def step_mosa_config_exists(context, path):
    _assert_path(context, path)


@then('the MOSA gate blocks on "{condition}"')
def step_mosa_gate_blocks(context, condition):
    gates_path = os.path.join(context.project_root, 'args', 'security_gates.yaml')
    assert os.path.exists(gates_path), f"Security gates config not found: {gates_path}"
    with open(gates_path, 'r', encoding='utf-8') as fh:
        content = fh.read()
    assert condition in content, (
        f"MOSA gate condition '{condition}' not found in security_gates.yaml"
    )


@then('the MOSA assessor exists at "{path}"')
def step_mosa_assessor_exists(context, path):
    _assert_path(context, path)


@then('the modular design analyzer exists at "{path}"')
def step_modular_design_analyzer_exists(context, path):
    _assert_path(context, path)


@then('the IaC generator exists at "{path}"')
def step_iac_generator_exists(context, path):
    _assert_path(context, path)


@then('the Terraform generator exists at "{path}"')
def step_terraform_generator_exists(context, path):
    _assert_path(context, path)


@then('the Ansible generator exists at "{path}"')
def step_ansible_generator_exists(context, path):
    _assert_path(context, path)


@then('the Kubernetes generator exists at "{path}"')
def step_k8s_generator_exists(context, path):
    _assert_path(context, path)


@then('the Dockerfile generator exists at "{path}"')
def step_dockerfile_generator_exists(context, path):
    _assert_path(context, path)


@then('the deployment profiles config exists at "{path}"')
def step_deployment_profiles_exists(context, path):
    _assert_path(context, path)


@then('the infra canvas config exists at "{path}"')
def step_infra_canvas_config_exists(context, path):
    _assert_path(context, path)


@when('the IC IE data fabric interface is verified')
def step_verify_ic_ie_data_fabric(context):
    context.check_paths = [
        'icdev/tools/ic_ie/data_fabric.py',
        'tools/manifest/ic-ie-data-fabric.md',
        'tests/test_ic_ie_data_fabric.py',
    ]


@then('the data fabric service exists at "{path}"')
def step_data_fabric_service_exists(context, path):
    _assert_path(context, path)


@then('the data fabric manifest exists at "{path}"')
def step_data_fabric_manifest_exists(context, path):
    _assert_path(context, path)


@then('the data fabric tests exist at "{path}"')
def step_data_fabric_tests_exist(context, path):
    _assert_path(context, path)


@then('the DAT ingestion engine exists at "{path}"')
def step_dat_ingestion_engine_exists(context, path):
    _assert_path(context, path)


@then('the DTI calculator exists at "{path}"')
def step_dti_calculator_exists(context, path):
    _assert_path(context, path)


@then('the DAT update runner exists at "{path}"')
def step_dat_update_runner_exists(context, path):
    _assert_path(context, path)


@then('the DAT dashboard template exists at "{path}"')
def step_dat_dashboard_template_exists(context, path):
    _assert_path(context, path)


@then('the DAT scheduler configuration exists at "{path}"')
def step_dat_scheduler_config_exists(context, path):
    _assert_path(context, path)


@then('the DAT configuration exists at "{path}"')
def step_dat_config_exists(context, path):
    _assert_path(context, path)


@then('the DAT blueprint module exists at "{path}"')
def step_dat_blueprint_module_exists(context, path):
    _assert_path(context, path)


# CUI // SP-CTI
