
# Auto-grader for M09 Step 1: LangChain LCEL Chains

import sys
from io import StringIO

captured = StringIO()
sys.stdout = captured

try:
    # Build a chain
    prompt = PromptTemplate("Analyze this security finding: {finding}")
    llm = MockLLM()
    parser = StrOutputParser()

    chain = prompt | llm | parser

    result1 = chain.invoke({"finding": "2 CAT I STIG findings open on ICDEV-Prod"})
    result2 = chain.invoke({"finding": "RAG retrieval latency exceeds 5 seconds"})
    result3 = chain.invoke({"finding": "System passes all compliance checks"})

    print(f"Result 1: {result1[:60]}")
    print(f"Result 2: {result2[:60]}")
    print(f"Chain steps: {len(chain.steps)}")
    print(f"Is Runnable: {isinstance(chain, Runnable)}")
finally:
    sys.stdout = sys.__stdout__

output = captured.getvalue()

# Runnable.__or__ produces Chain
assert isinstance(chain, Chain), f"prompt | llm | parser must produce a Chain, got {type(chain)}"
assert isinstance(chain, Runnable), "Chain must inherit from Runnable"

# Chain step count
assert len(chain.steps) == 3, f"Chain must have 3 steps (prompt | llm | parser), got {len(chain.steps)}"

# Chain.invoke
assert result1 is not None, "chain.invoke() returned None — implement Chain.invoke()"
assert isinstance(result1, str), f"chain.invoke() must return a string, got {type(result1)}"
assert len(result1) > 10, "Result too short — check StrOutputParser.invoke()"

# Content validation
result1_lower = result1.lower()
assert any(kw in result1_lower for kw in ["cat i", "critical", "analysis", "remediation", "finding"]), \
    f"Result should reference CAT I content from MockLLM, got: {result1[:100]}"

# PromptTemplate
pt_result = PromptTemplate("Check {item} for {system}").invoke({"item": "STIG V-1", "system": "ICDEV"})
assert "STIG V-1" in pt_result, "PromptTemplate must substitute {item} variable"
assert "ICDEV" in pt_result, "PromptTemplate must substitute {system} variable"

# StrOutputParser
stripped = StrOutputParser().invoke("  hello world  ")
assert stripped == "hello world", f"StrOutputParser must strip whitespace, got: '{stripped}'"

# Composability — chain can be extended
extended = chain | StrOutputParser()
assert isinstance(extended, Chain), "Chains must be further composable with |"
assert len(extended.steps) == 4, f"Extended chain should have 4 steps, got {len(extended.steps)}"

assert len(output) > 20, "Print results in your main block"

print(f"PASS: LCEL chain working. {len(chain.steps)} components composed with | operator.")
