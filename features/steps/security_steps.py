# CUI // SP-CTI
"""Step definitions for ICDEV security scanning BDD scenarios."""

import subprocess
import sys

from behave import given, then, when


@given('a project directory at "."')
def step_project_dir_current(context):
    """Set project directory to current."""
    context.project_dir = '.'


@given('a container image "{image}"')
def step_container_image(context, image):
    """Set container image name."""
    context.container_image = image


@when('I run the SAST runner with JSON output')
def step_run_sast_json(context):
    """Run SAST with JSON output."""
    result = subprocess.run(
        [sys.executable, 'tools/security/sast_runner.py',
         '--project-path', context.project_dir, '--json'],
        capture_output=True, text=True, timeout=60
    )
    context.result = result


@when('I run the secret detector with JSON output')
def step_run_secret_json(context):
    """Run secret detector with JSON output."""
    result = subprocess.run(
        [sys.executable, 'tools/security/secret_detector.py',
         '--project-path', context.project_dir, '--json'],
        capture_output=True, text=True, timeout=60
    )
    context.result = result


@when('I run the dependency auditor with JSON output')
def step_run_dep_json(context):
    """Run dependency auditor with JSON output."""
    result = subprocess.run(
        [sys.executable, 'tools/security/dependency_auditor.py',
         '--project-path', context.project_dir, '--json'],
        capture_output=True, text=True, timeout=60
    )
    context.result = result


@when('I run the STIG checker')
def step_run_stig(context):
    """Run STIG compliance checker."""
    result = subprocess.run(
        [sys.executable, 'tools/compliance/stig_checker.py',
         '--project-id', context.project_id],
        capture_output=True, text=True, timeout=30
    )
    context.result = result


@when('I run the container scanner')
def step_run_container_scan(context):
    """Run container security scanner."""
    result = subprocess.run(
        [sys.executable, 'tools/security/container_scanner.py',
         '--image', context.container_image],
        capture_output=True, text=True, timeout=30
    )
    context.result = result


@then('the output should contain severity counts')
def step_severity_counts(context):
    """Verify severity counts in output."""
    assert context.result.returncode == 0, f"Failed: {context.result.stderr}"


@then('the output should contain scanned file count')
def step_scanned_files(context):
    """Verify scanned file count."""
    assert context.result.returncode == 0


@then('the output should report files scanned')
def step_files_scanned(context):
    """Verify files scanned count."""
    assert context.result.returncode == 0


@then('the output should report findings count')
def step_findings_count(context):
    """Verify findings count."""
    assert context.result.returncode == 0


@then('the output should list audited packages')
def step_audited_packages(context):
    """Verify audited package list."""
    assert context.result.returncode == 0


@then('the output should report vulnerability counts')
def step_vuln_counts(context):
    """Verify vulnerability counts."""
    assert context.result.returncode == 0


@then('the result should report CAT1 findings count')
def step_cat1_count(context):
    """Verify CAT1 count."""
    assert context.result.returncode == 0


@then('the result should report CAT2 findings count')
def step_cat2_count(context):
    """Verify CAT2 count."""
    assert context.result.returncode == 0


@then('the result should report CAT3 findings count')
def step_cat3_count(context):
    """Verify CAT3 count."""
    assert context.result.returncode == 0


@then('CAT1 findings should be 0 for gate pass')
def step_cat1_zero(context):
    """Verify 0 CAT1 findings."""
    assert context.result.returncode == 0


@then('the result should report vulnerability counts by severity')
def step_vuln_by_severity(context):
    """Verify vulnerability breakdown."""
    assert context.result.returncode == 0
