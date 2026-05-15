
"""
Tier 2 SecOps-Eng Mission 2: STIG Parser
Goal: Extract control metadata from XCCDF benchmark content using regex parsing.
"""


# ── Sample XCCDF Content ──────────────────────────────────────────────────────

SAMPLE_XCCDF = """
<Benchmark id="RHEL_9_STIG" title="Red Hat Enterprise Linux 9 STIG">

<Rule id="V-257777" severity="high">
  <title>RHEL 9 must be configured to use the shadow file to store passwords.</title>
  <fix id="F-257777r1">
    Configure PAM to use shadow password storage. Edit /etc/pam.d/system-auth.
  </fix>
  <check>
    Verify /etc/shadow exists and permissions are 000.
    If /etc/shadow does not exist: FINDING
  </check>
</Rule>

<Rule id="V-257778" severity="medium">
  <title>RHEL 9 must enforce a minimum 15-character password length.</title>
  <fix id="F-257778r1">
    Set PASS_MIN_LEN=15 in /etc/login.defs and configure PAM minlen=15.
  </fix>
  <check>
    Check /etc/login.defs for PASS_MIN_LEN value.
    If value is less than 15: FINDING
  </check>
</Rule>

<Rule id="V-257779" severity="low">
  <title>RHEL 9 should display a login banner before authentication.</title>
  <fix id="F-257779r1">
    Create /etc/issue.net with the DoD warning banner text.
  </fix>
  <check>
    Verify /etc/issue.net contains the standard DoD banner.
    If banner text is absent, this is a FINDING.
  </check>
</Rule>

<Rule id="V-257780" severity="medium">
  <title>RHEL 9 must disable the Ctrl-Alt-Del key sequence.</title>
  <fix id="F-257780r1">
    Run: systemctl mask ctrl-alt-del.target
  </fix>
  <check>
    Run: systemctl status ctrl-alt-del.target
    If not masked, this is not a FINDING.
  </check>
</Rule>

</Benchmark>
"""

SEVERITY_MAP = {
    "high": "CAT I",
    "medium": "CAT II",
    "low": "CAT III",
}


# ── Step 1: Rule Extractor ────────────────────────────────────────────────────

def extract_rules(xccdf_content: str) -> list[str]:
    """TODO: Extract all <Rule>...</Rule> block strings from XCCDF content.

    Use regex to find all text between <Rule ...> and </Rule>.
    Pattern: r'<Rule[^>]*>.*?</Rule>'  with re.DOTALL

    Return list of raw rule block strings.
    Return empty list if no rules found.
    """
    # YOUR CODE HERE
    pass


# ── Step 2: Severity Mapper ───────────────────────────────────────────────────

def map_severity(severity: str) -> str:
    """TODO: Map STIG severity to CAT designation.

    Use SEVERITY_MAP: "high"→"CAT I", "medium"→"CAT II", "low"→"CAT III"
    Return "UNKNOWN" for any other value (no raise).
    """
    # YOUR CODE HERE
    pass


# ── Step 3: Rule Parser ───────────────────────────────────────────────────────

def parse_rule(rule_block: str) -> dict:
    """TODO: Parse a single <Rule> block into a structured dict.

    Extract:
    1. rule_id: from <Rule id="V-NNNNNN" ...> attribute
       Pattern: r'<Rule[^>]*id="([^"]+)"'
    2. severity: from severity="..." attribute
       Pattern: r'severity="([^"]+)"'
    3. cat: map_severity(severity)
    4. title: text content of <title>...</title>
       Pattern: r'<title>(.*?)</title>' with re.DOTALL, strip whitespace
    5. fix_text: text content of <fix>...</fix>
       Pattern: r'<fix[^>]*>(.*?)</fix>' with re.DOTALL, strip whitespace
    6. finding_keyword: True if "FINDING" (uppercase) appears in <check>...</check> text
       Pattern: r'<check>(.*?)</check>' with re.DOTALL

    Return:
    {
        "rule_id": str,
        "severity": str,
        "cat": str,
        "title": str,
        "fix_text": str,
        "finding_keyword": bool,
    }
    Return None if rule_id cannot be extracted.
    """
    # YOUR CODE HERE
    pass


# ── Step 4: STIGParser ────────────────────────────────────────────────────────

class STIGParser:
    """Parses XCCDF benchmark content into structured control objects."""

    def parse(self, xccdf_content: str) -> dict:
        """TODO: Parse an XCCDF document and return all controls.

        1. Extract benchmark title from <Benchmark ... title="..."> attribute
           Pattern: r'<Benchmark[^>]*title="([^"]+)"'
           Use "Unknown Benchmark" if not found.
        2. Call extract_rules(xccdf_content) → rule_blocks
        3. For each block, call parse_rule(block) → skip None results
        4. Return:
        {
            "benchmark_title": str,
            "controls": [list of parsed rule dicts],
            "count": len(controls),
            "cat1_count": count of CAT I controls,
            "cat2_count": count of CAT II controls,
            "cat3_count": count of CAT III controls,
        }
        """
        # YOUR CODE HERE
        pass


# Test
if __name__ == "__main__":
    parser = STIGParser()
    result = parser.parse(SAMPLE_XCCDF)
    print(f"Benchmark: {result['benchmark_title']}")
    print(f"Controls: {result['count']}")
    print(f"CAT I: {result['cat1_count']}, CAT II: {result['cat2_count']}, CAT III: {result['cat3_count']}")
    for ctrl in result["controls"]:
        print(f"  [{ctrl['cat']}] {ctrl['rule_id']}: {ctrl['title'][:60]}...")
        print(f"    Finding keyword: {ctrl['finding_keyword']}")
