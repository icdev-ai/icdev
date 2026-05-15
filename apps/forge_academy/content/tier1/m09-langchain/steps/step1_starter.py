
from __future__ import annotations
"""
Step 1: LangChain LCEL — Build a Composable Chain
Goal: Implement Runnable base class with | operator and build a 3-step chain.
"""


class Runnable:
    """TODO: Base class for all chain components.

    Implement:
    1. invoke(input) → should raise NotImplementedError (subclasses override)
    2. __or__(other) → return a Chain([self, other]) so that a | b works
    3. __ror__(other) → handle the case where other is a dict (for parallel inputs)
    """

    def invoke(self, input):
        raise NotImplementedError("Subclasses must implement invoke()")

    def __or__(self, other):
        # YOUR CODE HERE: return a Chain that sequences self → other
        pass

    def __ror__(self, other):
        # Allow dict | runnable for parallel input patterns
        if isinstance(other, dict):
            return Chain([RunnablePassthrough(other), self])
        return NotImplemented


class Chain(Runnable):
    """A sequence of Runnables — output of each feeds into the next."""

    def __init__(self, steps: list):
        self.steps = steps

    def invoke(self, input):
        """TODO: Execute each step in order, passing output to the next.

        Return the final step's output.
        """
        # YOUR CODE HERE
        pass

    def __or__(self, other):
        return Chain(self.steps + [other])


class RunnablePassthrough(Runnable):
    """Passes input unchanged (optionally merging a dict)."""

    def __init__(self, merge: dict | None = None):
        self.merge = merge or {}

    def invoke(self, input):
        if self.merge and isinstance(input, dict):
            return {**input, **self.merge}
        return input


# ── Chain components ──────────────────────────────────────────────────────────

class PromptTemplate(Runnable):
    """Formats a prompt string from a template and input variables."""

    def __init__(self, template: str):
        self.template = template

    def invoke(self, input: dict) -> str:
        """TODO: Format self.template with the input dict using str.format_map().

        Return the formatted string.
        """
        # YOUR CODE HERE
        pass


class MockLLM(Runnable):
    """Simulates an LLM call — returns a canned response based on input keywords."""

    def invoke(self, prompt: str) -> str:
        prompt_lower = prompt.lower()
        if "cat i" in prompt_lower or "critical" in prompt_lower:
            return "ANALYSIS: 2 CAT I findings detected. Immediate remediation required within 30 days per DoD policy."
        elif "rag" in prompt_lower or "retrieval" in prompt_lower:
            return "ANALYSIS: RAG system is operational. 3 documents retrieved from compliance corpus."
        else:
            return "ANALYSIS: System appears compliant based on available evidence."


class StrOutputParser(Runnable):
    """Extracts and cleans the string from LLM output."""

    def invoke(self, llm_output: str) -> str:
        """TODO: Strip whitespace and return the clean string."""
        # YOUR CODE HERE
        pass


class JsonOutputParser(Runnable):
    """Parses JSON from LLM output string."""

    def invoke(self, llm_output: str) -> dict:
        import json
        import re
        match = re.search(r'\{[^{}]+\}', llm_output, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except Exception:
                pass
        return {"raw": llm_output, "parsed": False}


# Test
if __name__ == "__main__":
    # Build a 3-component chain: prompt | llm | parser
    prompt = PromptTemplate("Analyze this security finding for a DoD system: {finding}")
    llm = MockLLM()
    parser = StrOutputParser()

    chain = prompt | llm | parser

    print("=== Chain Invocation ===")
    result1 = chain.invoke({"finding": "2 CAT I STIG findings open on ICDEV-Prod"})
    print(f"Result 1: {result1}")

    result2 = chain.invoke({"finding": "RAG retrieval latency exceeds 5 seconds"})
    print(f"Result 2: {result2}")

    print(f"\nChain steps: {len(chain.steps)}")
    print(f"Is Runnable: {isinstance(chain, Runnable)}")
