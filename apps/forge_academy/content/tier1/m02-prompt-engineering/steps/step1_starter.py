
"""
Step 1: Prompt Anatomy
Goal: Build a structured prompt from its components and call simulate_llm_call().
"""


def simulate_llm_call(prompt: str) -> dict:
    """Mock LLM call — returns a structured response."""
    word_count = len(prompt.split())
    has_system = "system:" in prompt.lower() or "you are" in prompt.lower()
    has_context = "context:" in prompt.lower() or "document" in prompt.lower()
    has_format = "json" in prompt.lower() or "format:" in prompt.lower()
    quality = sum([has_system, has_context, has_format])
    return {
        "content": '{"analysis": "Compliance gap detected in access control policies", "severity": "high"}',
        "prompt_quality_score": quality / 3.0,
        "token_count": word_count * 1.3,
        "components_detected": {
            "system_prompt": has_system,
            "context": has_context,
            "format_spec": has_format,
        }
    }


def build_prompt(system: str, context: str, user_message: str, output_format: str) -> str:
    """TODO: Assemble a structured prompt from the 4 components.

    Each component should be clearly labeled (e.g., with a header).
    Return the complete prompt as a single string.
    """
    # YOUR CODE HERE
    pass


# Test it
if __name__ == "__main__":
    system = "You are an expert GovCon compliance analyst specializing in FedRAMP."
    context = "CONTEXT: System Security Plan for Agency XYZ, current baseline: FedRAMP Moderate."
    user_msg = "Identify the top compliance gap in this SSP."
    fmt = "Return JSON: {\"gap\": \"...\", \"severity\": \"high|medium|low\", \"recommendation\": \"...\"}"

    prompt = build_prompt(system, context, user_msg, fmt)
    print("=== Built Prompt ===")
    print(prompt)
    print("\n=== LLM Response ===")
    response = simulate_llm_call(prompt)
    print(f"Quality Score: {response['prompt_quality_score']:.0%}")
    print(f"Components detected: {response['components_detected']}")
    print(f"Response: {response['content']}")
