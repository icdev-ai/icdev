
"""
Step 1: What is RAG?
Goal: Implement simple_rag() that retrieves relevant docs and generates an answer.
"""

DOCS = [
    {
        "id": "ssp-001",
        "text": "FedRAMP High requires multi-factor authentication for all privileged accounts. Controls AC-2, IA-2, and IA-5 are mandatory. Systems must enforce MFA for both local and remote access.",
        "source": "FedRAMP High Baseline v5",
    },
    {
        "id": "stig-002",
        "text": "STIG V-220706: SSH daemon must not permit root logins. Set PermitRootLogin no in sshd_config. CAT I finding — immediate remediation required.",
        "source": "RHEL 8 STIG",
    },
    {
        "id": "nist-003",
        "text": "NIST SP 800-53 AC-2 (Account Management) requires organizations to manage information system accounts, including establishing, activating, modifying, disabling, and removing accounts.",
        "source": "NIST SP 800-53 Rev 5",
    },
    {
        "id": "poam-004",
        "text": "Open POA&M items must be tracked to closure within 30 days for CAT I, 90 days for CAT II, and 180 days for CAT III findings per DoD policy.",
        "source": "DoD POA&M Policy",
    },
    {
        "id": "ato-005",
        "text": "Authority to Operate (ATO) is granted for a maximum of 3 years for systems with a FedRAMP Moderate or High authorization. Annual continuous monitoring reports are required.",
        "source": "DoD ATO Policy",
    },
]


def retrieve(query: str, docs: list, top_k: int = 2) -> list:
    """TODO: Retrieve the top_k most relevant docs for the query.

    Use keyword matching: score each doc by how many query words appear in its text.
    Return the top_k docs sorted by descending score.
    """
    # YOUR CODE HERE
    pass


def generate_answer(query: str, context_docs: list) -> str:
    """TODO: Build a prompt that includes the retrieved context, then 'generate' an answer.

    Format the context docs clearly (id + source + text).
    Simulate the LLM response by returning a string that references the source documents.
    The answer must mention at least one document source.
    """
    # YOUR CODE HERE
    pass


def simple_rag(query: str) -> str:
    """TODO: Tie it together. Retrieve + generate. Return the final answer string."""
    # YOUR CODE HERE
    pass


# Test
if __name__ == "__main__":
    queries = [
        "What are the MFA requirements for FedRAMP High systems?",
        "How long do I have to fix a CAT I STIG finding?",
    ]
    for q in queries:
        print(f"Q: {q}")
        answer = simple_rag(q)
        print(f"A: {answer}")
        print()
