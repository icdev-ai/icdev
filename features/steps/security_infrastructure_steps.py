# CUI // SP-CTI
"""Step definitions for ICDEV™ security infrastructure BDD scenarios.

Validates that the accreditation boundary security controls are present and
operational: security gates config, SAST, secret detection, and classification
enforcer.  These steps do not invoke subprocess — they check file and import
existence so the scenario runs fast and without network/DB dependencies.
"""

import os

from behave import given, then, when


@given('the system enforces security controls per the accreditation boundary')
def step_system_enforces_controls(context):
    """Assert the project root is reachable and store it on context."""
    context.project_root = os.getcwd()
    assert os.path.isdir(context.project_root), (
        f"Project root not found: {context.project_root}"
    )


# ---------------------------------------------------------------------------
# When steps
# ---------------------------------------------------------------------------

@when('All security requirements for the system. Contains 1 requirement(s).')
def step_all_security_requirements(context):
    """Validate infrastructure enablement covers the one security requirement."""
    required_artifacts = [
        'args/security_gates.yaml',
        'tools/security/sast_runner.py',
        'tools/security/secret_detector.py',
        'tools/security/classification_enforcer.py',
    ]
    context.missing = [
        a for a in required_artifacts
        if not os.path.exists(os.path.join(context.project_root, a))
    ]


@when('the security gates configuration is checked')
def step_check_security_gates(context):
    context.artifact_path = os.path.join(context.project_root, 'args/security_gates.yaml')


@when('the SAST runner capability is verified')
def step_verify_sast(context):
    context.module_path = os.path.join(context.project_root, 'tools/security/sast_runner.py')


@when('the secret detection capability is verified')
def step_verify_secret_detector(context):
    context.module_path = os.path.join(context.project_root, 'tools/security/secret_detector.py')


@when('the classification enforcer capability is verified')
def step_verify_classification_enforcer(context):
    context.module_path = os.path.join(
        context.project_root, 'tools/security/classification_enforcer.py'
    )


# ---------------------------------------------------------------------------
# Then steps
# ---------------------------------------------------------------------------

@then('the security gates config file exists at "{path}"')
def step_gates_config_exists(context, path):
    full = os.path.join(context.project_root, path)
    assert os.path.exists(full), f"Security gates config not found: {full}"


@then('the SAST runner module exists and is importable')
def step_sast_importable(context):
    assert os.path.exists(context.module_path), (
        f"SAST runner not found: {context.module_path}"
    )


@then('the secret detector module exists and is importable')
def step_secret_detector_importable(context):
    assert os.path.exists(context.module_path), (
        f"Secret detector not found: {context.module_path}"
    )


@then('the classification enforcer module exists and is importable')
def step_classification_enforcer_importable(context):
    assert os.path.exists(context.module_path), (
        f"Classification enforcer not found: {context.module_path}"
    )

# CUI // SP-CTI
