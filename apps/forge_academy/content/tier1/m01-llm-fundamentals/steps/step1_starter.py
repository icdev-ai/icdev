
# Mission M01 — LLM Fundamentals
# Step 1: Make your first LLM call
# Goal: Understand the input/output structure of a language model

# Since we're in a sandbox (no live LLM access), we'll simulate the call
# In production, you'd use tools.llm.router.LLMRouter

# Simulate an LLM response structure
def simulate_llm_call(system_prompt: str, user_message: str) -> dict:
    """Simulates what an LLM provider returns."""
    # Real API returns: content, model, usage (tokens in/out)
    return {
        "content": f"Processing: '{user_message}' with context: '{system_prompt[:30]}...'",
        "model": "ollama/mistral",
        "usage": {
            "input_tokens": len(system_prompt.split()) + len(user_message.split()),
            "output_tokens": 42
        }
    }

# Your task: Call the simulator and print the results
# 1. Create a system prompt that defines the AI's role
# 2. Create a user message asking what a token is
# 3. Print the response content
# 4. Print the token usage

system_prompt = "You are an expert AI engineer teaching fundamentals."
user_message = "What is a token in the context of language models?"

# TODO: Call simulate_llm_call with system_prompt and user_message
# TODO: Print the response "content"
# TODO: Print "input_tokens" and "output_tokens" from usage
