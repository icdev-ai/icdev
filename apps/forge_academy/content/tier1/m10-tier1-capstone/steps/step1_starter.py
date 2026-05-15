
"""
Tier 1 Capstone: Wire RAG + Agent + MCP into one system.
Goal: Build a compliance_qa MCP tool backed by a RAG retriever and agent loop.
"""



# ── Compliance Document Corpus ────────────────────────────────────────────────

COMPLIANCE_DOCS = [
    {"id": "fedramp-mfa", "source": "FedRAMP High Baseline",
     "text": "Multi-factor authentication is required for all privileged accounts. Controls IA-2 and IA-5 are mandatory. Systems must enforce MFA for remote access."},
    {"id": "stig-ssh", "source": "RHEL 8 STIG",
     "text": "STIG V-220706 (CAT I): SSH PermitRootLogin must be disabled. Set PermitRootLogin no in /etc/ssh/sshd_config. Immediate remediation required."},
    {"id": "poam-timing", "source": "DoD POA&M Policy",
     "text": "CAT I findings must be remediated within 30 days. CAT II within 90 days. CAT III within 180 days."},
    {"id": "ato-duration", "source": "DoD ATO Policy",
     "text": "Authority to Operate (ATO) is granted for 3 years maximum. Continuous monitoring reports required annually."},
    {"id": "nist-ac2", "source": "NIST SP 800-53",
     "text": "AC-2 Account Management: Establish, activate, modify, disable, and remove accounts. Review accounts at 90-day intervals."},
]

# ── Agent Tools ───────────────────────────────────────────────────────────────

def tool_stig_lookup(control_id: str) -> dict:
    db = {
        "V-220706": {"title": "SSH PermitRootLogin disabled", "severity": "CAT1"},
        "IA-2": {"title": "Multi-Factor Authentication", "severity": "CAT1"},
        "AC-2": {"title": "Account Management", "severity": "CAT2"},
    }
    return db.get(control_id, {"error": f"Control {control_id} not found"})


def tool_calculate_risk(severity: str, open_count: int) -> dict:
    weights = {"CAT1": 10, "cat1": 10, "CAT2": 5, "cat2": 5, "CAT3": 1, "cat3": 1}
    score = weights.get(severity, 3) * open_count
    level = "CRITICAL" if score >= 20 else "HIGH" if score >= 10 else "MEDIUM" if score >= 5 else "LOW"
    return {"risk_score": score, "risk_level": level}


AGENT_TOOLS = {
    "stig_lookup": tool_stig_lookup,
    "calculate_risk": tool_calculate_risk,
}


# ── Step 1: RAG Retrieval ─────────────────────────────────────────────────────

def retrieve(question: str, docs: list, top_k: int = 2) -> list:
    """TODO: Implement keyword-scored retrieval (from M03).

    Score each doc by the number of question words it contains.
    Return the top_k docs sorted by descending score.
    """
    # YOUR CODE HERE
    pass


# ── Step 2: Agent Loop ────────────────────────────────────────────────────────

def run_agent(question: str, context: str, max_steps: int = 3) -> str:
    """TODO: Implement a simple agent loop (from M04).

    The agent should:
    1. Decide which tool to call based on question + context
       - If "mfa" or "ia-2" in question → stig_lookup("IA-2")
       - If "ssh" or "v-220706" in question → stig_lookup("V-220706")
       - If "risk" or "score" in question → calculate_risk("CAT1", 2)
       - Otherwise → skip tool, go straight to answer
    2. Call the tool from AGENT_TOOLS
    3. Return a grounded answer string that includes the context source and tool result

    The answer MUST mention the source document (from context).
    """
    # YOUR CODE HERE
    pass


# ── Step 3: MCP Server ────────────────────────────────────────────────────────

class ComplianceMCPServer:
    """MCP server that exposes compliance_qa as a tool."""

    def handle_request(self, request: dict) -> dict:
        method = request.get("method", "")

        if method == "tools/list":
            return {"tools": [
                {
                    "name": "compliance_qa",
                    "description": "Answer a compliance question using RAG retrieval and agent reasoning.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "question": {"type": "string", "description": "The compliance question to answer"},
                        },
                        "required": ["question"],
                    },
                }
            ]}

        elif method == "tools/call":
            params = request.get("params", {})
            if params.get("name") == "compliance_qa":
                question = params.get("arguments", {}).get("question", "")
                answer = self._compliance_qa(question)
                return {"content": [{"type": "text", "text": answer}]}
            return {"error": "Unknown tool"}

        return {"error": f"Unknown method: {method}"}

    def _compliance_qa(self, question: str) -> str:
        """TODO: Wire retrieve() and run_agent() together.

        1. Call retrieve(question, COMPLIANCE_DOCS, top_k=2) → top docs
        2. Build context string from the top docs (include source labels)
        3. Call run_agent(question, context) → final answer
        4. Return the answer
        """
        # YOUR CODE HERE
        pass


# Test
if __name__ == "__main__":
    server = ComplianceMCPServer()

    # Simulate an MCP client
    print("=== Tool Discovery ===")
    tools_resp = server.handle_request({"method": "tools/list"})
    tools = tools_resp.get("tools", [])
    print(f"Tools: {[t['name'] for t in tools]}")

    print("\n=== Compliance Q&A ===")
    questions = [
        "What are the MFA requirements for FedRAMP High?",
        "How long do I have to fix a CAT I STIG finding?",
        "What is the SSH configuration requirement?",
    ]
    for q in questions:
        resp = server.handle_request({
            "method": "tools/call",
            "params": {"name": "compliance_qa", "arguments": {"question": q}},
        })
        answer = resp.get("content", [{}])[0].get("text", "No answer")
        print(f"Q: {q}")
        print(f"A: {answer[:100]}")
        print()
