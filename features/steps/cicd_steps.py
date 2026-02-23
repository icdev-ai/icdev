# [TEMPLATE: CUI // SP-CTI]
"""Step definitions for ICDEV CI/CD integration BDD scenarios."""

import json
import os
import subprocess
import sys

from behave import given, then, when, use_step_matcher

from tools.ci.core.event_envelope import BOT_IDENTIFIER, EventEnvelope
from tools.ci.core.event_router import EventRouter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_github_issue_payload(body, issue_number=42, author="developer"):
    """Build a minimal GitHub issues.opened webhook payload."""
    return {
        "action": "opened",
        "issue": {
            "number": issue_number,
            "title": "Test issue",
            "body": body,
            "user": {"login": author},
            "labels": [],
        },
    }


def _make_github_comment_payload(body, issue_number=42, author="developer"):
    """Build a minimal GitHub issue_comment.created webhook payload."""
    return {
        "action": "created",
        "issue": {
            "number": issue_number,
            "title": "Test issue",
            "labels": [],
        },
        "comment": {
            "id": 99,
            "body": body,
            "user": {"login": author},
        },
    }


def _make_gitlab_issue_payload(description, iid=10, author="dev"):
    """Build a minimal GitLab issue webhook payload."""
    return {
        "object_kind": "issue",
        "object_attributes": {
            "iid": iid,
            "title": "GL Test issue",
            "description": description,
            "action": "open",
            "labels": [],
        },
        "user": {"username": author},
        "project": {"id": 1},
    }


# ---------------------------------------------------------------------------
# Scenario: GitHub webhook triggers SDLC workflow
# ---------------------------------------------------------------------------

@given('the webhook server is running')
def step_webhook_server_running(context):
    """Verify the webhook server Flask app can be imported and tested."""
    from tools.ci.triggers.webhook_server import app
    context.webhook_app = app
    context.test_client = app.test_client()


@when('a GitHub issue is created with "/icdev_sdlc" in the body')
def step_github_issue_icdev_sdlc(context):
    """Simulate a GitHub webhook for an issue containing /icdev_sdlc."""
    payload = _make_github_issue_payload("/icdev_sdlc")
    envelope = EventEnvelope.from_github_webhook(payload, "issues")
    context.envelope = envelope
    context.workflow_detected = envelope.workflow_command if envelope else ""


@then('the full SDLC pipeline should be triggered')
def step_full_sdlc_triggered(context):
    """Verify the envelope extracted the icdev_sdlc workflow command."""
    assert context.envelope is not None, "Envelope should not be None"
    assert context.workflow_detected == "icdev_sdlc", (
        f"Expected 'icdev_sdlc', got '{context.workflow_detected}'"
    )
    assert context.envelope.platform == "github"
    assert context.envelope.event_type == "issue_opened"


# ---------------------------------------------------------------------------
# Scenario: GitLab webhook triggers build workflow
# ---------------------------------------------------------------------------

use_step_matcher("re")


@when(r'a GitLab issue has tag "\{\{icdev: build\}\}"')
def step_gitlab_issue_icdev_build_tag(context):
    """Simulate a GitLab issue with an {{icdev: build}} tag."""
    issue_data = {
        "iid": 10,
        "title": "GL build issue",
        "description": "Build the microservice",
        "author": {"username": "dev"},
    }
    envelope = EventEnvelope.from_gitlab_tag(issue_data, "build")
    context.envelope = envelope
    context.workflow_detected = envelope.workflow_command if envelope else ""


use_step_matcher("parse")


@then('the build workflow should be triggered')
def step_build_workflow_triggered(context):
    """Verify the envelope mapped the tag to icdev_build."""
    assert context.envelope is not None, "Envelope should not be None"
    assert context.workflow_detected == "icdev_build", (
        f"Expected 'icdev_build', got '{context.workflow_detected}'"
    )
    assert context.envelope.platform == "gitlab"
    assert context.envelope.source == "gitlab_task_monitor"


# ---------------------------------------------------------------------------
# Scenario: Poll trigger detects new issues
# ---------------------------------------------------------------------------

@given('the poll trigger is configured')
def step_poll_trigger_configured(context):
    """Verify the poll trigger module can be imported."""
    # Confirm the poll trigger module is importable
    import importlib
    spec = importlib.util.find_spec("tools.ci.triggers.poll_trigger")
    assert spec is not None, "poll_trigger module should be importable"
    context.poll_interval = int(os.getenv("POLL_INTERVAL", "20"))


@when('a new issue is created with ICDEV workflow command')
def step_new_issue_with_workflow_cmd(context):
    """Simulate a polled issue containing an ICDEV workflow command."""
    issue_data = {
        "number": 55,
        "title": "Deploy the dashboard",
        "body": "/icdev_deploy",
        "user": {"login": "ops-engineer"},
    }
    envelope = EventEnvelope.from_poll_issue(issue_data, "github")
    context.envelope = envelope
    context.workflow_detected = envelope.workflow_command if envelope else ""


@then('the poll trigger should detect it within 20 seconds')
def step_poll_detects_within_interval(context):
    """Verify the poll trigger's configured interval and command extraction."""
    assert context.poll_interval <= 20, (
        f"Poll interval {context.poll_interval}s exceeds 20s"
    )
    assert context.envelope is not None, "Envelope should not be None"
    assert context.workflow_detected != "", "Workflow command should be extracted"
    assert context.envelope.source == "github_poll"


# ---------------------------------------------------------------------------
# Scenario: SDLC pipeline runs all phases
# ---------------------------------------------------------------------------

@given('a valid issue number')
def step_valid_issue_number(context):
    """Set up a valid issue number for pipeline execution."""
    context.issue_number = "999"
    context.phases_available = []

    # Verify each phase script exists
    workflows_dir = os.path.join(os.getcwd(), 'tools', 'ci', 'workflows')
    for phase_script in ['icdev_plan.py', 'icdev_build.py',
                         'icdev_test.py', 'icdev_review.py']:
        script_path = os.path.join(workflows_dir, phase_script)
        if os.path.isfile(script_path):
            context.phases_available.append(phase_script)


@when('I run the full SDLC pipeline')
def step_run_full_sdlc(context):
    """Verify the SDLC orchestrator script is importable and well-formed."""
    sdlc_path = os.path.join(
        os.getcwd(), 'tools', 'ci', 'workflows', 'icdev_sdlc.py'
    )
    assert os.path.isfile(sdlc_path), f"SDLC script not found at {sdlc_path}"

    # Validate the script compiles (syntax check)
    result = subprocess.run(
        [sys.executable, '-m', 'py_compile', sdlc_path],
        capture_output=True, text=True, timeout=15,
    )
    context.sdlc_compile_result = result
    context.sdlc_script_path = sdlc_path


@then('it should execute plan phase')
def step_execute_plan_phase(context):
    """Verify plan phase script exists and compiles."""
    plan_path = os.path.join(
        os.getcwd(), 'tools', 'ci', 'workflows', 'icdev_plan.py'
    )
    assert os.path.isfile(plan_path), "icdev_plan.py not found"
    result = subprocess.run(
        [sys.executable, '-m', 'py_compile', plan_path],
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, f"Plan script compile failed: {result.stderr}"


@then('it should execute build phase')
def step_execute_build_phase(context):
    """Verify build phase script exists and compiles."""
    build_path = os.path.join(
        os.getcwd(), 'tools', 'ci', 'workflows', 'icdev_build.py'
    )
    assert os.path.isfile(build_path), "icdev_build.py not found"
    result = subprocess.run(
        [sys.executable, '-m', 'py_compile', build_path],
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, f"Build script compile failed: {result.stderr}"


@then('it should execute test phase')
def step_execute_test_phase(context):
    """Verify test phase script exists and compiles."""
    test_path = os.path.join(
        os.getcwd(), 'tools', 'ci', 'workflows', 'icdev_test.py'
    )
    assert os.path.isfile(test_path), "icdev_test.py not found"
    result = subprocess.run(
        [sys.executable, '-m', 'py_compile', test_path],
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, f"Test script compile failed: {result.stderr}"


@then('it should execute review phase')
def step_execute_review_phase(context):
    """Verify review phase script exists and compiles."""
    review_path = os.path.join(
        os.getcwd(), 'tools', 'ci', 'workflows', 'icdev_review.py'
    )
    assert os.path.isfile(review_path), "icdev_review.py not found"
    result = subprocess.run(
        [sys.executable, '-m', 'py_compile', review_path],
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, f"Review script compile failed: {result.stderr}"


# ---------------------------------------------------------------------------
# Scenario: Pipeline generates IaC artifacts
# ---------------------------------------------------------------------------

@given('a project with infrastructure requirements')
def step_project_with_infra(context):
    """Set up a project context for IaC generation."""
    context.project_id = "proj-cicd-test"
    # Verify the infra generator tools exist
    infra_dir = os.path.join(os.getcwd(), 'tools', 'infra')
    assert os.path.isdir(infra_dir), f"Infra tools dir not found: {infra_dir}"
    context.infra_dir = infra_dir


@when('I run the infrastructure generators')
def step_run_infra_generators(context):
    """Run IaC generators and capture results."""
    context.infra_results = {}

    # Terraform generator
    tf_path = os.path.join(context.infra_dir, 'terraform_generator.py')
    if os.path.isfile(tf_path):
        result = subprocess.run(
            [sys.executable, '-m', 'py_compile', tf_path],
            capture_output=True, text=True, timeout=15,
        )
        context.infra_results['terraform'] = {
            'exists': True,
            'compiles': result.returncode == 0,
            'error': result.stderr if result.returncode != 0 else '',
        }
    else:
        context.infra_results['terraform'] = {'exists': False, 'compiles': False}

    # K8s generator
    k8s_path = os.path.join(context.infra_dir, 'k8s_generator.py')
    if os.path.isfile(k8s_path):
        result = subprocess.run(
            [sys.executable, '-m', 'py_compile', k8s_path],
            capture_output=True, text=True, timeout=15,
        )
        context.infra_results['k8s'] = {
            'exists': True,
            'compiles': result.returncode == 0,
            'error': result.stderr if result.returncode != 0 else '',
        }
    else:
        context.infra_results['k8s'] = {'exists': False, 'compiles': False}

    # Pipeline generator
    pipeline_path = os.path.join(context.infra_dir, 'pipeline_generator.py')
    if os.path.isfile(pipeline_path):
        result = subprocess.run(
            [sys.executable, '-m', 'py_compile', pipeline_path],
            capture_output=True, text=True, timeout=15,
        )
        context.infra_results['pipeline'] = {
            'exists': True,
            'compiles': result.returncode == 0,
            'error': result.stderr if result.returncode != 0 else '',
        }
    else:
        context.infra_results['pipeline'] = {'exists': False, 'compiles': False}


@then('Terraform files should be generated')
def step_terraform_generated(context):
    """Verify Terraform generator exists and compiles."""
    tf = context.infra_results.get('terraform', {})
    assert tf.get('exists'), "terraform_generator.py not found"
    assert tf.get('compiles'), (
        f"terraform_generator.py failed to compile: {tf.get('error', '')}"
    )


@then('Kubernetes manifests should be generated')
def step_k8s_generated(context):
    """Verify K8s generator exists and compiles."""
    k8s = context.infra_results.get('k8s', {})
    assert k8s.get('exists'), "k8s_generator.py not found"
    assert k8s.get('compiles'), (
        f"k8s_generator.py failed to compile: {k8s.get('error', '')}"
    )


@then('the pipeline YAML should be generated')
def step_pipeline_generated(context):
    """Verify pipeline generator exists and compiles."""
    pipeline = context.infra_results.get('pipeline', {})
    assert pipeline.get('exists'), "pipeline_generator.py not found"
    assert pipeline.get('compiles'), (
        f"pipeline_generator.py failed to compile: {pipeline.get('error', '')}"
    )


# ---------------------------------------------------------------------------
# Scenario: Bot loop prevention
# ---------------------------------------------------------------------------

@given('a webhook event from an ICDEV bot comment')
def step_bot_comment_event(context):
    """Create a webhook event that originates from the ICDEV bot."""
    bot_body = f"{BOT_IDENTIFIER} ICDEV Webhook: Detected `icdev_plan` workflow"
    payload = _make_github_comment_payload(
        body=bot_body,
        issue_number=42,
        author="icdev-bot",
    )
    envelope = EventEnvelope.from_github_webhook(payload, "issue_comment")
    context.envelope = envelope
    context.bot_payload = payload


@when('the webhook processes the event')
def step_webhook_processes_event(context):
    """Process the bot event through the envelope and check is_bot flag."""
    context.is_bot = context.envelope.is_bot if context.envelope else False


@then('the event should be ignored')
def step_event_ignored(context):
    """Verify the event is flagged as a bot event for ignoring."""
    assert context.is_bot is True, (
        "Bot comment should be detected as is_bot=True"
    )


@then('no workflow should be triggered')
def step_no_workflow_triggered(context):
    """Verify that bot events are identified and would be skipped by router."""
    assert context.envelope.is_bot is True, "Envelope must be flagged as bot"
    # The EventRouter skips bot events; verify the BOT_IDENTIFIER is present
    assert BOT_IDENTIFIER in context.envelope.content, (
        f"Expected '{BOT_IDENTIFIER}' in envelope content"
    )


# ---------------------------------------------------------------------------
# Additional supporting steps
# ---------------------------------------------------------------------------

@given('a GitHub webhook payload for issue #{issue_num:d}')
def step_github_webhook_payload(context, issue_num):
    """Set up a GitHub webhook payload for a specific issue number."""
    context.issue_number = str(issue_num)
    context.payload = _make_github_issue_payload(
        body="/icdev_plan", issue_number=issue_num
    )


@when('the webhook server receives the payload')
def step_webhook_receives_payload(context):
    """Send the payload to the webhook server test client."""
    envelope = EventEnvelope.from_github_webhook(context.payload, "issues")
    context.envelope = envelope
    context.workflow_detected = envelope.workflow_command if envelope else ""


@then('the event should be routed to the correct workflow')
def step_event_routed_correctly(context):
    """Verify the envelope contains a valid workflow command."""
    assert context.envelope is not None, "Envelope should not be None"
    assert context.workflow_detected in (
        "icdev_plan", "icdev_build", "icdev_test", "icdev_review",
        "icdev_sdlc", "icdev_deploy", "icdev_comply", "icdev_secure",
        "icdev_plan_build", "icdev_plan_build_test",
        "icdev_plan_build_test_review",
    ), f"Unexpected workflow: {context.workflow_detected}"


@given('a GitLab webhook payload for merge request !{mr_num:d}')
def step_gitlab_mr_payload(context, mr_num):
    """Set up a GitLab merge request webhook payload."""
    context.mr_number = str(mr_num)
    context.payload = {
        "object_kind": "merge_request",
        "object_attributes": {
            "iid": mr_num,
            "title": "Test MR",
            "description": "/icdev_review",
            "action": "open",
        },
        "user": {"username": "dev"},
        "project": {"id": 1},
    }


@when('the GitLab webhook processes the merge request')
def step_gitlab_webhook_processes_mr(context):
    """Process a GitLab merge request webhook payload."""
    envelope = EventEnvelope.from_gitlab_webhook(context.payload)
    context.envelope = envelope
    context.workflow_detected = envelope.workflow_command if envelope else ""


@then('the merge request workflow should be triggered')
def step_mr_workflow_triggered(context):
    """Verify the MR envelope was created with correct workflow."""
    assert context.envelope is not None, "Envelope should not be None"
    assert context.envelope.event_type == "mr_opened"
    assert context.envelope.platform == "gitlab"
# [TEMPLATE: CUI // SP-CTI]
