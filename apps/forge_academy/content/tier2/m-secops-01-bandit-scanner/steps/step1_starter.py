
"""
SecOps Mission 1: Build a Static Analysis Security Agent
Goal: Wrap Bandit SAST output in a triage agent with CWE mapping and fix recommendations.
"""


# ── CWE Mapping ───────────────────────────────────────────────────────────────

CWE_MAP = {
    "B102": ("CWE-78", "OS Command Injection", "Use subprocess with a list, never shell=True with user input"),
    "B103": ("CWE-732", "Incorrect Permission Assignment", "Set file permissions to 0o640 or less"),
    "B105": ("CWE-259", "Hard-coded Password", "Use environment variables or a secrets manager"),
    "B106": ("CWE-259", "Hard-coded Password in Function Arg", "Use environment variables or a secrets manager"),
    "B107": ("CWE-259", "Hard-coded Password in Function Default", "Use environment variables or a secrets manager"),
    "B301": ("CWE-502", "Deserialization of Untrusted Data", "Use JSON or a safe serialization format instead of pickle"),
    "B303": ("CWE-327", "Use of Broken Cryptographic Algorithm (MD5/SHA1)", "Use SHA-256 or stronger"),
    "B501": ("CWE-295", "Improper Certificate Validation", "Never set verify=False in production"),
    "B502": ("CWE-295", "TLS Version Too Low", "Enforce TLSv1.2 minimum"),
    "B608": ("CWE-89", "SQL Injection", "Use parameterized queries with ? or %s placeholders"),
}

# ── Simulated Bandit Finding Schema ──────────────────────────────────────────

VULNERABLE_CODE_SAMPLE = """
import subprocess
import hashlib
import pickle

PASSWORD = "admin123"  # hardcoded

def run_cmd(user_input):
    subprocess.run(user_input, shell=True)  # B602/shell injection

def hash_data(data):
    return hashlib.md5(data.encode()).hexdigest()  # B303/weak hash

def load_cache(cache_file):
    with open(cache_file, 'rb') as f:
        return pickle.load(f)  # B301/unsafe deserialization

def build_query(user_id):
    return f"SELECT * FROM users WHERE id = {user_id}"  # B608/SQL injection
"""

# ── Simulated Bandit Output ───────────────────────────────────────────────────

SIMULATED_FINDINGS = [
    {"test_id": "B105", "severity": "HIGH", "confidence": "HIGH",
     "line": 6, "code": 'PASSWORD = "admin123"',
     "issue_text": "Possible hardcoded password: 'admin123'"},
    {"test_id": "B602", "severity": "HIGH", "confidence": "HIGH",
     "line": 9, "code": "subprocess.run(user_input, shell=True)",
     "issue_text": "subprocess call with shell=True identified"},
    {"test_id": "B303", "severity": "MEDIUM", "confidence": "HIGH",
     "line": 12, "code": "hashlib.md5(data.encode()).hexdigest()",
     "issue_text": "Use of MD5 considered insecure"},
    {"test_id": "B301", "severity": "MEDIUM", "confidence": "HIGH",
     "line": 16, "code": "pickle.load(f)",
     "issue_text": "Pickle is unsafe when used with untrusted input"},
    {"test_id": "B608", "severity": "HIGH", "confidence": "MEDIUM",
     "line": 19, "code": 'f"SELECT * FROM users WHERE id = {user_id}"',
     "issue_text": "Possible SQL injection via string-based query construction"},
]


# ── Step 1: Bandit Scanner ────────────────────────────────────────────────────

class BanditScanner:
    """Simulates running Bandit against Python source code."""

    def scan(self, code: str) -> list[dict]:
        """TODO: Return findings from SIMULATED_FINDINGS that match patterns in code.

        For each finding in SIMULATED_FINDINGS, check if any word from finding["code"]
        (split on whitespace) appears in the code string.
        Return all matching findings as a list.

        Hint: a finding matches if any of its code tokens appears in the source.
        """
        # YOUR CODE HERE
        pass


# ── Step 2: Triage Engine ─────────────────────────────────────────────────────

def triage_findings(findings: list[dict]) -> dict:
    """TODO: Prioritize findings into CRITICAL/HIGH/MEDIUM/LOW buckets.

    Use this priority matrix:
    - severity=HIGH AND confidence=HIGH → CRITICAL
    - severity=HIGH (any confidence) OR (severity=MEDIUM AND confidence=HIGH) → HIGH
    - severity=MEDIUM (other confidence) → MEDIUM
    - everything else → LOW

    Return: {"critical": [...], "high": [...], "medium": [...], "low": [...]}
    """
    # YOUR CODE HERE
    pass


# ── Step 3: Report Generator ──────────────────────────────────────────────────

def generate_report(triaged: dict) -> str:
    """TODO: Generate a formatted security report string.

    The report must include:
    1. Total finding counts per priority level
    2. For CRITICAL findings: include the CWE ID and fix recommendation from CWE_MAP
    3. For HIGH findings: include the CWE ID
    4. A one-line summary at the top

    The report must contain at least one "CWE-" reference if there are any findings.
    """
    # YOUR CODE HERE
    pass


# Test
if __name__ == "__main__":
    scanner = BanditScanner()
    findings = scanner.scan(VULNERABLE_CODE_SAMPLE)
    print("=== Bandit Scan Results ===")
    print(f"Findings: {len(findings)}")

    triaged = triage_findings(findings)
    print(f"\nTriage: CRITICAL={len(triaged['critical'])} HIGH={len(triaged['high'])} "
          f"MEDIUM={len(triaged['medium'])} LOW={len(triaged['low'])}")

    report = generate_report(triaged)
    print("\n=== Security Report ===")
    print(report)
