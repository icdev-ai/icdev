"""
Step 3: Temperature & Sampling
Goal: Call simulate_temperature() with temps 0.0, 0.5, and 1.0 and print results.
"""

import random


def simulate_temperature(prompt: str, temperature: float) -> dict:
    """Simulates how an LLM samples at different temperatures.

    Returns a response dict with 'content' and 'diversity_score'.
    Higher temperature = more diverse, lower = more deterministic.
    """
    random.seed(42 if temperature == 0.0 else None)

    templates = [
        "The concept relates to {topic} through probabilistic sampling mechanisms.",
        "From a systems perspective, {topic} involves token-level probability distributions.",
        "In practice, {topic} means the model weighs each possible next token by its likelihood.",
        "Fundamentally, {topic} is about controlling how the model chooses from its vocabulary.",
    ]

    if temperature == 0.0:
        # Always pick the first (most likely)
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

    return {
        "content": content,
        "temperature": temperature,
        "diversity_score": diversity_score,
    }


def sample_responses(prompt: str) -> list:
    """TODO: Call simulate_temperature with temps 0.0, 0.5, and 1.0.

    Return a list of the 3 response dicts.
    Print each response's temperature and content.
    """
    # YOUR CODE HERE
    pass


# Run it
if __name__ == "__main__":
    results = sample_responses("temperature in language models")
    if results:
        for r in results:
            print(f"temp={r['temperature']} | diversity={r['diversity_score']} | {r['content'][:60]}...")
