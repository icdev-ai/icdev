# CUI // SP-CTI
# ICDEV Agent Model Test — Verify Claude Code models work
# Adapted from ADW test_agents.py

"""
Test that Claude Code CLI models (opus, sonnet, haiku) respond correctly.

Runs model tests in parallel to verify availability.

Usage:
    python tools/testing/test_agent_models.py
"""

import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from tools.testing.data_types import AgentPromptRequest
from tools.ci.modules.agent import prompt_claude_code
from tools.testing.utils import make_run_id

# Models to test
MODELS = ["opus", "sonnet", "haiku"]

TEST_PROMPT = """You are a helpful assistant. Please respond to this test with:
1. Confirm you received this message
2. State which model you are
3. Say "Test successful!"

Keep your response brief."""


def test_model(model: str, run_id: str) -> tuple:
    """Test a specific model and return (success, message)."""
    print(f"\n{'='*50}")
    print(f"Testing model: {model}")
    print(f"{'='*50}")

    output_file = str(PROJECT_ROOT / "agents" / run_id / f"agent_test_{model}.jsonl")

    request = AgentPromptRequest(
        prompt=TEST_PROMPT,
        agent_name=f"test_{model}",
        model=model,
        output_file=output_file,
    )

    try:
        response = prompt_claude_code(request)

        if response.success:
            print(f"PASS {model} — Success!")
            print(f"Session ID: {response.session_id}")
            print(f"Preview: {response.output[:200]}...")
            return True, f"{model}: Success"
        else:
            print(f"FAIL {model} — Failed!")
            print(f"Error: {response.output}")
            return False, f"{model}: {response.output}"

    except Exception as e:
        error_msg = f"Exception: {str(e)}"
        print(f"FAIL {model} — Exception!")
        print(error_msg)
        return False, f"{model}: {error_msg}"


def main():
    """Run tests for all models in parallel."""
    run_id = make_run_id()

    print("CUI // SP-CTI")
    print("Testing Claude Code agent with different models (parallel)")
    print(f"Run ID: {run_id}")
    print(f"Models: {', '.join(MODELS)}")

    results = {}
    all_success = True

    with ThreadPoolExecutor(max_workers=len(MODELS)) as executor:
        future_to_model = {
            executor.submit(test_model, model, run_id): model
            for model in MODELS
        }

        for future in as_completed(future_to_model):
            model = future_to_model[future]
            try:
                success, message = future.result()
                results[model] = (success, message)
                if not success:
                    all_success = False
            except Exception as e:
                results[model] = (False, f"Exception: {str(e)}")
                all_success = False

    print(f"\n{'='*50}")
    print("Test Summary")
    print(f"{'='*50}")

    for model in MODELS:
        if model in results:
            success, message = results[model]
            status = "PASS" if success else "FAIL"
            print(f"{status} — {message}")

    overall = "All tests passed!" if all_success else "Some tests failed!"
    print(f"\nOverall: {overall}")

    sys.exit(0 if all_success else 1)


if __name__ == "__main__":
    main()
