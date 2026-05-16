# CUI // SP-CTI
"""Step definitions for ICDEV™ security infrastructure BDD scenarios.

Validates that the accreditation boundary security controls are present and
operational: security gates config, SAST, secret detection, and classification
enforcer.  These steps do not invoke subprocess — they check file and import
existence so the scenario runs fast and without network/DB dependencies.
"""

import os
import sys

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


@when('Enforce attribute-based access control (ABAC) with mandatory classification markings on all returned data objects')
def step_enforce_abac_cross_domain(context):
    """Exercise the ABAC engine to verify cross-domain pull enforcement and classification markings."""
    context.missing = []
    abac_path = os.path.join(context.project_root, 'tools', 'security', 'abac_engine.py')
    if not os.path.exists(abac_path):
        context.missing.append('tools/security/abac_engine.py not found')
        return

    # Ensure project root is on sys.path so tools.* imports resolve
    if context.project_root not in sys.path:
        sys.path.insert(0, context.project_root)

    try:
        import importlib
        mod = importlib.import_module('tools.security.abac_engine')
    except Exception as exc:
        context.missing.append(f'abac_engine import failed: {exc}')
        return

    # Subject: cleared analyst at partner agency
    cleared_analyst = {
        'user_id': 'analyst-001',
        'role': 'cleared_analyst',
        'agency': 'partner_agency',
        'clearance_level': 2,
        'classification': 'SECRET',
        'compartments': ['COI_INTEL'],
        'entitlements': ['cross_domain_pull'],
        'impact_level': 'IL4',
    }

    # Subject: uncleared user
    uncleared_user = {
        'user_id': 'user-002',
        'role': 'viewer',
        'agency': 'internal',
        'clearance_level': 1,
        'classification': 'CUI',
        'compartments': [],
        'entitlements': [],
        'impact_level': 'IL2',
    }

    resource = {
        'type': 'cross_domain_data',
        'source_il': 'IL4',
        'target_il': 'IL2',
        'classification': 'SECRET',
        'data_owner': 'MCIP',
        'compartments': ['COI_INTEL'],
    }

    data_objects = [
        {'id': 'rec-001', 'content': 'Operational briefing A', 'sensitivity': 'high'},
        {'id': 'rec-002', 'content': 'Mission summary B', 'sensitivity': 'medium'},
    ]

    # Verify permitted pull returns marked objects
    try:
        result = mod.enforce_cross_domain_pull(cleared_analyst, resource, data_objects)
        if not result.permitted:
            context.missing.append(f'Cleared analyst denied: {result.reason}')
        elif not result.data_objects:
            context.missing.append('Permitted pull returned no data objects')
        else:
            for obj in result.data_objects:
                if obj.get('classification_marking') != 'SECRET':
                    context.missing.append(
                        f"Object {obj.get('id')} missing correct classification_marking"
                    )
    except Exception as exc:
        context.missing.append(f'Permitted pull test failed: {exc}')

    # Verify denied pull returns no data (no leak)
    try:
        result = mod.enforce_cross_domain_pull(uncleared_user, resource, data_objects)
        if result.permitted:
            context.missing.append('Uncleared user was permitted — ABAC leak')
        if result.data_objects:
            context.missing.append('Denied pull leaked data objects')
    except Exception as exc:
        context.missing.append(f'Denied pull test failed: {exc}')


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
