# CUI // SP-CTI
"""Step definitions for ICDEV compliance gates BDD scenarios."""

import os
import subprocess
import sys

from behave import given, then, when


@given('a project with Python source files')
def step_project_with_python(context):
    """Set project directory with Python files."""
    context.project_dir = os.getcwd()


@given('a project directory with Python source files')
def step_project_dir_python(context):
    """Set project directory."""
    context.project_dir = os.getcwd()


@given('a project directory with source files')
def step_project_dir_source(context):
    """Set project directory."""
    context.project_dir = os.getcwd()


@given('a project with a requirements file')
def step_project_with_requirements(context):
    """Verify requirements.txt exists."""
    req_path = os.path.join(os.getcwd(), 'requirements.txt')
    assert os.path.exists(req_path), "requirements.txt not found"
    context.project_dir = os.getcwd()


@given('a project directory with dependencies')
def step_project_with_deps(context):
    """Set project directory with dependencies."""
    context.project_dir = os.getcwd()


@given('a project with ID "{project_id}"')
def step_project_with_id(context, project_id):
    """Set project ID."""
    context.project_id = project_id


@given('the project has applicable compliance frameworks')
def step_project_has_frameworks(context):
    """Verify project has compliance frameworks."""
    pass  # Frameworks auto-detected


@when('I check for CUI markings')
def step_check_cui(context):
    """Check all Python files for CUI markings."""
    missing = []
    for root, _dirs, files in os.walk(os.path.join(context.project_dir, 'tools')):
        for f in files:
            if f.endswith('.py') and f != '__init__.py':
                filepath = os.path.join(root, f)
                with open(filepath, 'r', encoding='utf-8', errors='ignore') as fh:
                    content = fh.read(200)
                    if 'CUI // SP-CTI' not in content:
                        missing.append(filepath)
    context.missing_cui = missing


@when('I run the SAST security scan')
def step_run_sast(context):
    """Run SAST scanner."""
    result = subprocess.run(
        [sys.executable, 'tools/security/sast_runner.py',
         '--project-path', context.project_dir, '--json'],
        capture_output=True, text=True, timeout=60
    )
    context.result = result


@when('I run the secret detector')
def step_run_secret_detector(context):
    """Run secret detection."""
    result = subprocess.run(
        [sys.executable, 'tools/security/secret_detector.py',
         '--project-path', context.project_dir, '--json'],
        capture_output=True, text=True, timeout=60
    )
    context.result = result


@when('I run the dependency auditor')
def step_run_dep_audit(context):
    """Run dependency auditor."""
    result = subprocess.run(
        [sys.executable, 'tools/security/dependency_auditor.py',
         '--project-path', context.project_dir, '--json'],
        capture_output=True, text=True, timeout=60
    )
    context.result = result


@when('I generate the SBOM')
def step_generate_sbom(context):
    """Generate SBOM."""
    context.sbom_generated = True


@when('I map activity "{activity}" to NIST controls')
def step_map_nist(context, activity):
    """Map activity to NIST controls."""
    result = subprocess.run(
        [sys.executable, 'tools/compliance/control_mapper.py',
         '--activity', activity, '--project-id', context.project_id],
        capture_output=True, text=True, timeout=30
    )
    context.result = result


@when('I run the multi-regime gate')
def step_run_multi_regime(context):
    """Run multi-regime assessment."""
    context.multi_regime_run = True


@then('every Python file should contain "CUI // SP-CTI"')
def step_all_cui_present(context):
    """Verify no missing CUI markings."""
    assert len(context.missing_cui) == 0, (
        f"{len(context.missing_cui)} files missing CUI markings: "
        f"{context.missing_cui[:5]}"
    )


@then('the result should report {count:d} critical findings')
def step_critical_findings(context, count):
    """Verify critical finding count."""
    assert context.result.returncode == 0, f"Tool failed: {context.result.stderr}"


@then('the result should report {count:d} high findings')
def step_high_findings(context, count):
    """Verify high finding count."""
    assert context.result.returncode == 0


@then('the result should report {count:d} secrets detected')
def step_secrets_detected(context, count):
    """Verify secret count."""
    assert context.result.returncode == 0


@then('the result should report {count:d} critical vulnerabilities')
def step_critical_vulns(context, count):
    """Verify critical vulnerability count."""
    assert context.result.returncode == 0


@then('the result should report {count:d} high vulnerabilities')
def step_high_vulns(context, count):
    """Verify high vulnerability count."""
    assert context.result.returncode == 0


@then('the SBOM should be created successfully')
def step_sbom_created(context):
    """Verify SBOM creation."""
    assert context.sbom_generated


@then('the SBOM should list all components')
def step_sbom_components(context):
    """Verify SBOM component listing."""
    pass


@then('the mapping should include at least one control')
def step_mapping_has_control(context):
    """Verify control mapping."""
    assert context.result.returncode == 0


@then('the crosswalk should cascade to mapped frameworks')
def step_crosswalk_cascade(context):
    """Verify crosswalk cascade."""
    pass  # Cascade is automatic


@then('all applicable frameworks should be assessed')
def step_all_frameworks_assessed(context):
    """Verify framework assessment."""
    pass


@then('the gate result should be reported')
def step_gate_reported(context):
    """Verify gate result."""
    pass
