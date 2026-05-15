
# Auto-grader for M02 Step 2: Few-Shot Prompting

import sys
from io import StringIO

captured = StringIO()
sys.stdout = captured

try:
    result1 = classify_finding("SSH root login is permitted on the bastion host.")
    result2 = classify_finding("System patch cycle is 92 days, policy requires 90.")
    if result1 and result2:
        print(f"Result1: {result1['severity']} | {result1['confidence']:.0%}")
        print(f"Result2: {result2['severity']} | {result2['confidence']:.0%}")
finally:
    sys.stdout = sys.__stdout__

output = captured.getvalue()

assert result1 is not None, "classify_finding() returned None — implement the function"
assert result2 is not None, "classify_finding() returned None for second test"

assert "severity" in result1, "Result must include 'severity' key"
assert "confidence" in result1, "Result must include 'confidence' key"
assert "examples_detected" in result1, "Result must include 'examples_detected' key"

assert result1["examples_detected"] >= 2, \
    f"Must use ≥2 examples in the prompt — only {result1['examples_detected']} detected"

assert result1["confidence"] >= 0.7, \
    f"Low confidence {result1['confidence']:.0%} — add more examples to improve accuracy"

assert result1["severity"] == "CAT1", \
    f"SSH root login should classify as CAT1, got {result1['severity']}"

assert len(output) > 10, "Print the severity and confidence in classify_finding()"

print(f"PASS: Few-shot classifier working. CAT1 correctly identified. Confidence: {result1['confidence']:.0%}")
