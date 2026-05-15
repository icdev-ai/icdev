
# Auto-grader for M10 Step 1: Tier 1 Capstone — Wire the Stack

import sys
from io import StringIO

captured = StringIO()
sys.stdout = captured

try:
    server = ComplianceMCPServer()

    # Tool discovery
    tools_resp = server.handle_request({"method": "tools/list"})
    tools = tools_resp.get("tools", [])

    # Q&A calls
    def ask(q):
        resp = server.handle_request({
            "method": "tools/call",
            "params": {"name": "compliance_qa", "arguments": {"question": q}},
        })
        return resp.get("content", [{}])[0].get("text", "")

    ans_mfa = ask("What are the MFA requirements for FedRAMP High?")
    ans_cat1 = ask("How long do I have to fix a CAT I STIG finding?")
    ans_ssh = ask("What is the SSH PermitRootLogin requirement?")

    print(f"Tools: {[t['name'] for t in tools]}")
    print(f"MFA: {ans_mfa[:80]}")
    print(f"CAT1: {ans_cat1[:80]}")
    print(f"SSH: {ans_ssh[:80]}")
finally:
    sys.stdout = sys.__stdout__

output = captured.getvalue()

# MCP tools/list
assert tools, "tools/list returned no tools"
tool_names = {t["name"] for t in tools}
assert "compliance_qa" in tool_names, "compliance_qa tool must be registered"

tool = tools[0]
assert "inputSchema" in tool, "compliance_qa must have an inputSchema"
assert "question" in tool["inputSchema"].get("properties", {}), \
    "inputSchema must include 'question' property"

# retrieve() validation
retrieved = retrieve("MFA requirements FedRAMP", COMPLIANCE_DOCS, top_k=2)
assert retrieved is not None, "retrieve() returned None"
assert isinstance(retrieved, list), f"retrieve() must return a list, got {type(retrieved)}"
assert len(retrieved) == 2, f"retrieve() should return top_k=2, got {len(retrieved)}"
sources = [d.get("source", "") for d in retrieved]
assert any("FedRAMP" in s for s in sources), \
    f"MFA query should retrieve FedRAMP doc. Got sources: {sources}"

# Answer validation
assert ans_mfa, "MFA question returned empty answer"
assert ans_cat1, "CAT I question returned empty answer"
assert ans_ssh, "SSH question returned empty answer"

# Answers must be grounded in source docs
ans_mfa_lower = ans_mfa.lower()
assert any(kw in ans_mfa_lower for kw in ["fedramp", "mfa", "ia-2", "ia-5", "multi-factor", "authenticat"]), \
    f"MFA answer not grounded in FedRAMP doc: {ans_mfa[:100]}"

ans_cat1_lower = ans_cat1.lower()
assert any(kw in ans_cat1_lower for kw in ["30", "cat i", "cat", "remediat", "poam", "stig", "ssh", "finding", "day"]), \
    f"CAT I answer not grounded in source docs: {ans_cat1[:150]}"

ans_ssh_lower = ans_ssh.lower()
assert any(kw in ans_ssh_lower for kw in ["ssh", "permitrootlogin", "stig", "v-220706", "cat"]), \
    f"SSH answer not grounded: {ans_ssh[:100]}"

assert len(output) > 30, "Print Q&A results in main block"

print("PASS: Tier 1 Capstone complete. RAG + Agent + MCP stack wired and grounded. You are ready for Tier 2.")
