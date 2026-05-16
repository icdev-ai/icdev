# CUI // SP-CTI
"""Step definitions for ICDEV™ functional infrastructure BDD scenarios.

Validates that the platform infrastructure required for functional capabilities
is present and operational: environment setup, CI/CD pipeline configuration,
security hardening, and compliance scaffolding. Steps check file existence
without subprocess or network calls for fast, dependency-free execution.
"""

import os

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


# ---------------------------------------------------------------------------
# Then steps
# ---------------------------------------------------------------------------

@then('the system behaves as specified and the requirement is satisfied')
def step_requirement_satisfied(context):
    assert not context.missing, (
        f"Functional infrastructure artifacts missing: {context.missing}. "
        "Platform enablement requirement cannot be satisfied."
    )


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

# CUI // SP-CTI
