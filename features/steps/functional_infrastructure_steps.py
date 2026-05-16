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


@given('the system is operational and the user is authenticated')
def step_system_operational(context):
    context.project_root = os.getcwd()
    assert os.path.isdir(context.project_root), (
        f"Project root not found: {context.project_root}"
    )


# ---------------------------------------------------------------------------
# When steps
# ---------------------------------------------------------------------------

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

# CUI // SP-CTI
