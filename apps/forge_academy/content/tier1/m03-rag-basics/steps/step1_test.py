# Auto-grader for M03 Step 1: What is RAG?

import sys
from io import StringIO

captured = StringIO()
sys.stdout = captured

try:
    q1 = "What are the MFA requirements for FedRAMP High systems?"
    q2 = "How long do I have to fix a CAT I STIG finding?"
    q3 = "What is the ATO duration?"

    retrieved = retrieve(q1, DOCS, top_k=2)
    answer1 = simple_rag(q1)
    answer2 = simple_rag(q2)
    answer3 = simple_rag(q3)

    print(f"Retrieved {len(retrieved)} docs for MFA query")
    print(f"Answer 1: {answer1[:80]}")
    print(f"Answer 2: {answer2[:80]}")
    print(f"Answer 3: {answer3[:80]}")
finally:
    sys.stdout = sys.__stdout__

output = captured.getvalue()

# Validate retrieve()
assert retrieved is not None, "retrieve() returned None"
assert isinstance(retrieved, list), f"retrieve() must return a list, got {type(retrieved)}"
assert len(retrieved) == 2, f"retrieve() should return top_k=2 docs, got {len(retrieved)}"

# MFA query should retrieve the FedRAMP doc
sources = [d.get("source", "") for d in retrieved]
assert any("FedRAMP" in s for s in sources), \
    f"MFA query should retrieve FedRAMP document. Got: {sources}"

# Validate generate_answer()
assert answer1 is not None, "simple_rag() returned None for answer1"
assert isinstance(answer1, str), f"simple_rag() must return a string, got {type(answer1)}"
assert len(answer1) > 30, "Answer too short — reference the retrieved context"

# Answer should reference a source
answer1_lower = answer1.lower()
assert any(kw in answer1_lower for kw in ["fedramp", "mfa", "ac-2", "ia-2", "multi-factor", "ssp"]), \
    "Answer should reference retrieved document content"

# CAT I answer should reference something from retrieved docs (STIG, POA&M, or timing)
assert any(kw in answer2.lower() for kw in ["30", "cat i", "cat", "stig", "immediate", "poam", "remediat"]), \
    "CAT I answer should reference retrieved document content (timing, STIG, or remediation)"

assert len(output) > 20, "Print the answers in your main block"

print("PASS: RAG pipeline working. Retrieval grounds the answer in source documents.")
