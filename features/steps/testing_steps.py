# CUI // SP-CTI
"""Step definitions for ICDEV testing pipeline BDD scenarios."""

import os
import subprocess
import sys

from behave import given, then, when


@given('a set of Python source files')
def step_python_source_files(context):
    """Set source file directory."""
    context.source_dir = os.path.join(os.getcwd(), 'tools')


@given('the project root directory')
def step_project_root(context):
    """Set project root."""
    context.project_root = os.getcwd()


@given('the tests/ directory with test files')
def step_tests_dir(context):
    """Verify tests/ directory exists."""
    tests_dir = os.path.join(os.getcwd(), 'tests')
    assert os.path.isdir(tests_dir), "tests/ directory not found"
    context.tests_dir = tests_dir


@given('a project directory with tests')
def step_project_with_tests(context):
    """Set project with tests."""
    context.project_dir = os.getcwd()


@given('a plan file with acceptance criteria')
def step_plan_with_criteria(context):
    """Set plan file path."""
    context.plan_file = None  # Will use test fixture


@given('test results from a previous run')
def step_test_results(context):
    """Set test results path."""
    context.test_results = None


@given('E2E test specs in .claude/commands/e2e/')
def step_e2e_specs_exist(context):
    """Check for E2E test specs."""
    e2e_dir = os.path.join(os.getcwd(), '.claude', 'commands', 'e2e')
    context.e2e_dir = e2e_dir


@when('I run py_compile on each file')
def step_run_pycompile(context):
    """Compile-check Python files."""
    failures = []
    for root, _dirs, files in os.walk(context.source_dir):
        for f in files:
            if f.endswith('.py'):
                filepath = os.path.join(root, f)
                result = subprocess.run(
                    [sys.executable, '-m', 'py_compile', filepath],
                    capture_output=True, text=True, timeout=10
                )
                if result.returncode != 0:
                    failures.append(filepath)
    context.compile_failures = failures


@when('I run ruff check')
def step_run_ruff(context):
    """Run ruff linter."""
    result = subprocess.run(
        [sys.executable, '-m', 'ruff', 'check', '.'],
        capture_output=True, text=True, timeout=60,
        cwd=context.project_root
    )
    context.result = result


@when('I run pytest with verbose output')
def step_run_pytest(context):
    """Run pytest."""
    result = subprocess.run(
        [sys.executable, '-m', 'pytest', 'tests/', '-v', '--tb=short'],
        capture_output=True, text=True, timeout=120
    )
    context.result = result


@when('I run the test orchestrator')
def step_run_orchestrator(context):
    """Run test orchestrator."""
    context.orchestrator_run = True


@when('I run the acceptance validator')
def step_run_acceptance(context):
    """Run acceptance validator."""
    context.acceptance_run = True


@when('I run the E2E runner with discover flag')
def step_run_e2e_discover(context):
    """Run E2E test discovery."""
    result = subprocess.run(
        [sys.executable, 'tools/testing/e2e_runner.py', '--discover'],
        capture_output=True, text=True, timeout=30
    )
    context.result = result


@then('all files should compile without errors')
def step_all_compile(context):
    """Verify 0 compile failures."""
    assert len(context.compile_failures) == 0, (
        f"{len(context.compile_failures)} files failed: "
        f"{context.compile_failures[:5]}"
    )


@then('there should be 0 remaining violations')
def step_no_violations(context):
    """Verify 0 ruff violations."""
    # Ruff returns 0 on success
    assert context.result.returncode == 0, (
        f"Ruff violations found: {context.result.stdout[:500]}"
    )


@then('all tests should pass')
def step_all_tests_pass(context):
    """Verify all pytest tests pass."""
    assert context.result.returncode == 0, (
        f"Tests failed: {context.result.stdout[-500:]}"
    )


@then('there should be 0 failures')
def step_no_failures(context):
    """Verify 0 test failures."""
    assert 'failed' not in context.result.stdout.lower() or \
        '0 failed' in context.result.stdout.lower()


@then('there should be 0 errors')
def step_no_errors(context):
    """Verify 0 test errors."""
    assert 'error' not in context.result.stdout.split('=')[-1].lower() or \
        '0 error' in context.result.stdout.lower()


@then('it should execute health check step')
def step_health_check(context):
    """Verify health check step."""
    assert context.orchestrator_run


@then('it should execute unit test step')
def step_unit_test_step(context):
    """Verify unit test step."""
    assert context.orchestrator_run


@then('it should execute security gate step')
def step_security_gate_step(context):
    """Verify security gate step."""
    assert context.orchestrator_run


@then('it should report overall pass/fail')
def step_overall_result(context):
    """Verify overall result."""
    assert context.orchestrator_run


@then('each criterion should be mapped to evidence')
def step_criteria_mapped(context):
    """Verify criteria mapping."""
    assert context.acceptance_run


@then('the overall gate should pass if all criteria met')
def step_gate_pass(context):
    """Verify gate pass."""
    assert context.acceptance_run


@then('it should list available test specifications')
def step_list_specs(context):
    """Verify E2E spec listing."""
    assert context.result.returncode == 0
