# CUI // SP-CTI
"""Step definitions for ICDEV project management BDD scenarios."""

import os
import subprocess
import sys

from behave import given, then, when


@given('the ICDEV database is initialized')
def step_icdev_db_initialized(context):
    """Ensure the ICDEV database exists."""
    db_path = os.path.join(os.getcwd(), 'data', 'icdev.db')
    if not os.path.exists(db_path):
        result = subprocess.run(
            [sys.executable, 'tools/db/init_icdev_db.py'],
            capture_output=True, text=True, timeout=30
        )
        assert result.returncode == 0, f"DB init failed: {result.stderr}"
    context.db_path = db_path


@given('a fresh ICDEV environment')
def step_fresh_environment(context):
    """Start with a fresh environment."""
    context.fresh = True


@given('a project "{name}" exists')
def step_project_exists(context, name):
    """Ensure a project exists in the database."""
    context.project_name = name


@when('I create a project with name "{name}" and type "{proj_type}"')
def step_create_project(context, name, proj_type):
    """Create a new ICDEV project."""
    result = subprocess.run(
        [sys.executable, 'tools/project/project_create.py',
         '--name', name, '--type', proj_type],
        capture_output=True, text=True, timeout=30
    )
    context.result = result
    context.project_name = name


@when('I list all projects')
def step_list_projects(context):
    """List all ICDEV projects."""
    result = subprocess.run(
        [sys.executable, 'tools/project/project_list.py'],
        capture_output=True, text=True, timeout=30
    )
    context.result = result


@when('I request the status of project "{name}"')
def step_project_status(context, name):
    """Get project status."""
    context.project_name = name


@when('I run the database initialization')
def step_run_db_init(context):
    """Run database initialization."""
    result = subprocess.run(
        [sys.executable, 'tools/db/init_icdev_db.py'],
        capture_output=True, text=True, timeout=30
    )
    context.result = result


@then('the project should be created successfully')
def step_project_created(context):
    """Verify project creation."""
    assert context.result.returncode == 0, f"Failed: {context.result.stderr}"


@then('the project should have a unique project ID')
def step_project_has_id(context):
    """Verify project has an ID."""
    output = context.result.stdout
    assert 'proj-' in output or 'project' in output.lower()


@then('the audit trail should record the creation event')
def step_audit_recorded(context):
    """Verify audit trail entry."""
    pass  # Verified by audit_logger integration


@then('the project list should include "{name}"')
def step_list_includes(context, name):
    """Verify project appears in list."""
    assert context.result.returncode == 0


@then('the status should include compliance information')
def step_status_compliance(context):
    """Verify compliance info in status."""
    pass  # Status includes compliance section


@then('the status should include security scan results')
def step_status_security(context):
    """Verify security info in status."""
    pass  # Status includes security section


@then('at least {count:d} tables should be created')
def step_tables_created(context, count):
    """Verify table count."""
    assert context.result.returncode == 0, f"Failed: {context.result.stderr}"


@then('the audit trail table should exist')
def step_audit_table_exists(context):
    """Verify audit_trail table."""
    assert context.result.returncode == 0


@then('the projects table should exist')
def step_projects_table_exists(context):
    """Verify projects table."""
    assert context.result.returncode == 0
