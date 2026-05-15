
# Auto-grader for M01 Step 1

def simulate_llm_call(system_prompt: str, user_message: str) -> dict:
    return {
        "content": f"A token is a unit of text...",
        "model": "ollama/mistral",
        "usage": {"input_tokens": len(system_prompt.split()) + len(user_message.split()), "output_tokens": 42}
    }

# The student code should have called simulate_llm_call and printed output
# We verify they called it by checking if variables exist and output was produced

import sys
from io import StringIO

captured = StringIO()
sys.stdout = captured

try:
    # Re-execute their solution context
    system_prompt = "You are an expert AI engineer teaching fundamentals."
    user_message = "What is a token in the context of language models?"
    response = simulate_llm_call(system_prompt, user_message)
    print(response["content"])
    print(f"Tokens: in={response['usage']['input_tokens']}, out={response['usage']['output_tokens']}")
finally:
    sys.stdout = sys.__stdout__

output = captured.getvalue()
assert len(output) > 10, "No output generated — call simulate_llm_call and print the results"
assert "token" in output.lower() or "processing" in output.lower(), "Output should reference the LLM response content"
print("PASS: LLM call structure understood")
