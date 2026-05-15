
"""
DevOps Mission 1: Build a CI/CD Pipeline Agent
Goal: Analyze pipeline failures, diagnose root causes, generate fixes.
"""

import re

# ── Failure Patterns ─────────────────────────────────────────────────────────

FAILURE_PATTERNS = [
    (r"ImportError|ModuleNotFoundError|No module named", "missing_dependency"),
    (r"Permission denied|EACCES|EPERM", "permission_error"),
    (r"Connection refused|Connection timed out|ETIMEDOUT|ECONNREFUSED", "network_error"),
    (r"No such file or directory|FileNotFoundError|ENOENT", "missing_file"),
    (r"FAILED|AssertionError|pytest.*error|test.*failed", "test_failure"),
    (r"Out of memory|OOM|MemoryError|Killed", "resource_error"),
]

# ── Fix Database ──────────────────────────────────────────────────────────────

FIX_DATABASE = {
    "missing_dependency": {
        "fix_command": "pip install -r requirements.txt",
        "explanation": "A Python package is missing from the environment.",
        "estimated_minutes": 3,
    },
    "permission_error": {
        "fix_command": "chmod 755 <path> or check service account permissions",
        "explanation": "Runner lacks permission to access a file or directory.",
        "estimated_minutes": 5,
    },
    "network_error": {
        "fix_command": "Check service health: curl -f <service_url>",
        "explanation": "A downstream service or registry is unreachable.",
        "estimated_minutes": 10,
    },
    "missing_file": {
        "fix_command": "Verify artifact paths and retention policies in .gitlab-ci.yml",
        "explanation": "A file expected by this stage was not produced by a prior stage.",
        "estimated_minutes": 5,
    },
    "test_failure": {
        "fix_command": "Run failing tests locally: pytest -x -v",
        "explanation": "One or more unit/integration tests failed.",
        "estimated_minutes": 20,
    },
    "resource_error": {
        "fix_command": "Increase runner memory limit in .gitlab-ci.yml: variables: RUNNER_MEMORY: 4096m",
        "explanation": "The runner ran out of memory during execution.",
        "estimated_minutes": 2,
    },
    "unknown": {
        "fix_command": "Review full pipeline logs for additional context.",
        "explanation": "Root cause could not be automatically determined.",
        "estimated_minutes": 30,
    },
}

# ── Pipeline Event Schema ─────────────────────────────────────────────────────

SAMPLE_PIPELINE_EVENT = {
    "pipeline_id": "p-2047",
    "project": "icdev/platform",
    "stages": [
        {"name": "install", "status": "success", "logs": "Installing dependencies... done"},
        {"name": "test", "status": "failed",
         "logs": "FAILED test_compliance.py::test_stig_scanner - AssertionError: expected 3 CAT I findings, got 0"},
        {"name": "build", "status": "skipped", "logs": ""},
        {"name": "deploy", "status": "skipped", "logs": ""},
    ],
}


# ── Step 1: Failure Analyzer ──────────────────────────────────────────────────

def analyze_failure(stage_name: str, logs: str) -> dict:
    """TODO: Analyze stage logs to identify root cause.

    Iterate through FAILURE_PATTERNS. For each (pattern, category) pair,
    use re.search() to check if the pattern appears in logs.
    Return the first match as: {"root_cause": <matched text>, "category": <category>}

    If no pattern matches, return: {"root_cause": "unknown", "category": "unknown"}
    """
    # YOUR CODE HERE
    pass


# ── Step 2: Fix Generator ─────────────────────────────────────────────────────

def generate_fix(root_cause: str, category: str) -> dict:
    """TODO: Look up the fix for a given failure category.

    Look up category in FIX_DATABASE.
    Return the fix dict (fix_command, explanation, estimated_minutes).
    If category not found, return FIX_DATABASE["unknown"].
    """
    # YOUR CODE HERE
    pass


# ── Step 3: Pipeline Agent ────────────────────────────────────────────────────

class PipelineAgent:
    """Agent that processes a pipeline event and generates a full remediation report."""

    def run(self, pipeline_event: dict) -> dict:
        """TODO: Process all stages, find failed ones, analyze and generate fixes.

        1. Find all stages where status == "failed"
        2. For each failed stage: call analyze_failure(stage_name, logs)
        3. For each failure analysis: call generate_fix(root_cause, category)
        4. Return a report dict:
           {
             "pipeline_id": <id>,
             "project": <project>,
             "failed_stages": [
               {
                 "stage": <name>,
                 "analysis": <analyze_failure result>,
                 "fix": <generate_fix result>,
               }
             ],
             "total_estimated_minutes": <sum of estimated_minutes>,
             "recommendation": "Automated fixes available for N of M failures."
           }
        """
        # YOUR CODE HERE
        pass


# Test
if __name__ == "__main__":
    agent = PipelineAgent()
    report = agent.run(SAMPLE_PIPELINE_EVENT)

    print("=== Pipeline Failure Report ===")
    print(f"Pipeline: {report['pipeline_id']} | Project: {report['project']}")
    print(f"Failed stages: {len(report['failed_stages'])}")

    for item in report["failed_stages"]:
        print(f"\n  Stage: {item['stage']}")
        print(f"  Category: {item['analysis']['category']}")
        print(f"  Fix: {item['fix']['fix_command']}")
        print(f"  ETA: {item['fix']['estimated_minutes']} min")

    print(f"\nTotal remediation time: {report['total_estimated_minutes']} minutes")
    print(f"Recommendation: {report['recommendation']}")
