
# Mission M01 - LLM Fundamentals
# Step 6 (Lab): price a request, then fit a prompt to its context budget.
#
# Offline: no live model, no network. toy_tokenize() is a deterministic stand-in
# for a real tokenizer. Use count_tokens() for every count - the grader does.

import re

# Toy price table, US dollars per MILLION tokens. Model names are illustrative;
# real, dated prices are in the Step 2 lesson.
PRICE_TABLE = {
    "frontier-large": {"input": 4.00, "cached_input": 0.20, "output": 20.00},
    "frontier-mid": {"input": 2.00, "cached_input": 0.20, "output": 10.00},
    "small-fast": {"input": 1.00, "cached_input": 0.10, "output": 5.00},
    "local-ollama": {"input": 0.00, "cached_input": 0.00, "output": 0.00},
}

_PIECE_RE = re.compile(r"\w+|[^\w\s]")
_MAX_PIECE = 6  # long words split into 6-character pieces, like BPE fragments


def toy_tokenize(text: str) -> list:
    """Split text into toy tokens: words, punctuation, and long words in pieces."""
    tokens = []
    for piece in _PIECE_RE.findall(text or ""):
        for start in range(0, len(piece), _MAX_PIECE):
            tokens.append(piece[start:start + _MAX_PIECE])
    return tokens


def count_tokens(text: str) -> int:
    return len(toy_tokenize(text))


def request_cost(usage: dict, prices: dict) -> float:
    """TODO: return the dollar cost of one request.

    usage  - input_tokens (uncached), cached_input_tokens, output_tokens,
             reasoning_tokens. A missing key counts as 0.
    prices - "input", "cached_input", "output" in dollars per MILLION tokens.
    Reasoning tokens are billed at the OUTPUT rate.
    """
    # YOUR CODE HERE
    return None


def fit_to_budget(system_prompt: str, history: list, question: str,
                  context_window: int, reserved_output: int) -> list:
    """TODO: return the most recent history turns that fit the budget.

    The prompt may use at most context_window - reserved_output tokens.
    The system prompt and question are always sent; drop turns from the OLDEST
    end until everything fits. Keep the kept turns in their original order and
    do not modify `history`. Raise ValueError if the system prompt plus the
    question cannot fit even with no history.
    """
    # YOUR CODE HERE
    return None


if __name__ == "__main__":
    demo_usage = {"input_tokens": 5000, "cached_input_tokens": 15000,
                  "output_tokens": 500, "reasoning_tokens": 1500}
    print("toy tokens:", toy_tokenize("CUI//SP-CTI categorically"))
    print("cost:", request_cost(demo_usage, PRICE_TABLE["frontier-mid"]))
    print("kept:", fit_to_budget("You are terse.", ["turn one", "turn two"],
                                 "What now?", context_window=12, reserved_output=4))
