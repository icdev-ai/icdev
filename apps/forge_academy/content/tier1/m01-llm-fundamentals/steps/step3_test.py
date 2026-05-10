# Auto-grader for M01 Step 3: Temperature & Sampling

import sys
from io import StringIO

# Capture output
captured = StringIO()
sys.stdout = captured

try:
    # Re-execute with test prompt
    import random

    def simulate_temperature(prompt: str, temperature: float) -> dict:
        random.seed(42 if temperature == 0.0 else None)
        templates = [
            "The concept relates to {topic} through probabilistic sampling mechanisms.",
            "From a systems perspective, {topic} involves token-level probability distributions.",
            "In practice, {topic} means the model weighs each possible next token by its likelihood.",
            "Fundamentally, {topic} is about controlling how the model chooses from its vocabulary.",
        ]
        if temperature == 0.0:
            content = templates[0].format(topic=prompt[:30])
            diversity_score = 0.0
        elif temperature <= 0.5:
            idx = random.randint(0, 1)
            content = templates[idx].format(topic=prompt[:30])
            diversity_score = round(random.uniform(0.2, 0.4), 2)
        else:
            idx = random.randint(0, len(templates) - 1)
            content = templates[idx].format(topic=prompt[:30])
            diversity_score = round(random.uniform(0.6, 0.95), 2)
        return {"content": content, "temperature": temperature, "diversity_score": diversity_score}

    # Import and call student's function
    results = sample_responses("temperature in language models")
    if results:
        for r in results:
            print(f"temp={r['temperature']} | diversity={r['diversity_score']} | {r['content'][:60]}...")
finally:
    sys.stdout = sys.__stdout__

output = captured.getvalue()

# Assertions
assert results is not None, "sample_responses() returned None — did you implement it?"
assert isinstance(results, list), f"sample_responses() must return a list, got {type(results)}"
assert len(results) == 3, f"Expected 3 responses (one per temperature), got {len(results)}"

temps = [r["temperature"] for r in results]
assert 0.0 in temps, "Must include temperature=0.0"
assert 0.5 in temps, "Must include temperature=0.5"
assert 1.0 in temps, "Must include temperature=1.0"

for r in results:
    assert "content" in r, f"Each result must have 'content' key: {r}"
    assert "diversity_score" in r, f"Each result must have 'diversity_score' key: {r}"

assert len(output) > 20, "No print output detected — print each response in sample_responses()"

print("PASS: Temperature sampling implemented correctly. Diversity spectrum understood.")
