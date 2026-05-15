
"""
Step 2: Few-Shot Prompting
Goal: Build a few-shot prompt that classifies STIG findings by severity.
"""


def simulate_classifier(prompt: str, finding: str) -> dict:
    """Mock few-shot classifier — returns severity based on keyword patterns."""
    combined = (prompt + finding).lower()
    if any(w in combined for w in ["root login", "default password", "no auth", "critical"]):
        severity = "CAT1"
        risk = "Immediate compromise vector, highest priority remediation."
    elif any(w in combined for w in ["audit", "log", "timeout", "90 days", "patch"]):
        severity = "CAT2"
        risk = "Non-compliance with NIST controls, medium remediation priority."
    else:
        severity = "CAT3"
        risk = "Minor policy deviation, low risk, routine maintenance."

    example_count = prompt.count("EXAMPLE") + prompt.count("example")
    return {
        "severity": severity,
        "risk": risk,
        "confidence": min(0.95, 0.5 + (example_count * 0.15)),
        "examples_detected": example_count,
    }


def classify_finding(finding: str) -> dict:
    """TODO: Build a few-shot prompt with ≥2 examples and classify the finding.

    Return the dict from simulate_classifier().
    Also print: the severity classification and confidence score.
    """
    # YOUR CODE HERE — build few_shot_prompt with examples, then call simulate_classifier
    pass


# Test cases
if __name__ == "__main__":
    test_findings = [
        "SSH root login is permitted on the bastion host.",
        "System patch cycle is 92 days, policy requires 90.",
        "No multi-factor authentication on privileged accounts.",
    ]
    for finding in test_findings:
        result = classify_finding(finding)
        if result:
            print(f"Finding: {finding[:50]}...")
            print(f"  → {result['severity']} | Confidence: {result['confidence']:.0%}")
            print()
