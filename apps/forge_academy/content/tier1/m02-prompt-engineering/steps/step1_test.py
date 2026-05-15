
# Auto-grader for M02 Step 1: Prompt Anatomy

import sys
from io import StringIO

captured = StringIO()
sys.stdout = captured

try:
    system = "You are an expert GovCon compliance analyst specializing in FedRAMP."
    context = "CONTEXT: System Security Plan for Agency XYZ, current baseline: FedRAMP Moderate."
    user_msg = "Identify the top compliance gap in this SSP."
    fmt = 'Return JSON: {"gap": "...", "severity": "high|medium|low", "recommendation": "..."}'

    prompt = build_prompt(system, context, user_msg, fmt)
    if prompt:
        response = simulate_llm_call(prompt)
        print(f"Quality Score: {response['prompt_quality_score']:.0%}")
        print(f"Components: {response['components_detected']}")
        print(f"Response: {response['content']}")
finally:
    sys.stdout = sys.__stdout__

output = captured.getvalue()

assert prompt is not None, "build_prompt() returned None — implement the function"
assert isinstance(prompt, str), f"build_prompt() must return a string, got {type(prompt)}"
assert len(prompt) > 100, "Prompt is too short — include all 4 components"

prompt_lower = prompt.lower()
assert system[:20].lower() in prompt_lower, "System prompt content must appear in the built prompt"
assert "context" in prompt_lower or "ssp" in prompt_lower, "Context must appear in the built prompt"
assert user_msg[:20].lower() in prompt_lower, "User message must appear in the built prompt"
assert "json" in prompt_lower, "Output format specification (JSON) must appear in the built prompt"

response = simulate_llm_call(prompt)
assert response["prompt_quality_score"] >= 0.67, \
    f"Prompt quality score {response['prompt_quality_score']:.0%} — include system, context, and format spec"

assert "Quality Score" in output, "Print the quality score in your output"

print("PASS: Prompt anatomy understood. All 4 components detected at ≥67% quality.")
