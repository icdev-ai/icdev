from __future__ import annotations
# CUI // SP-CTI
"""Tests for tools/observability_canvas/exporters/splunk.py

4 cases:
  1. Field name mapping  — EventID → EventCode
  2. contains|any        — list → OR clause with wildcards
  3. Numeric gt modifier — bytes_out|gt → SPL > operator
  4. contains + mapping  — CommandLine|contains → process.command_line with wildcard
"""

import textwrap

from tools.observability_canvas.exporters.splunk import sigma_to_spl

# ---------------------------------------------------------------------------
# Fixture rules
# ---------------------------------------------------------------------------

RULE_BRUTE_FORCE = textwrap.dedent("""\
    title: Detect T1110 via Auth Log
    id: aaaaaaaa-0001-0001-0001-000000000001
    status: experimental
    description: Detects brute-force login attempts via failed Windows auth events.
    tags:
      - attack.t1110
      - attack.credential-access
    logsource:
      category: authentication
    detection:
      selection:
        EventID: 4625
      condition: selection
    falsepositives:
      - Legitimate users forgetting passwords
    level: medium
""")

RULE_CREDENTIAL_DUMP = textwrap.dedent("""\
    title: Detect T1003 via Endpoint
    id: aaaaaaaa-0002-0002-0002-000000000002
    status: experimental
    description: Detects known credential-dumping tool invocations.
    tags:
      - attack.t1003
      - attack.credential-access
    logsource:
      category: process_creation
    detection:
      selection:
        CommandLine|contains|any:
          - mimikatz
          - lsass
          - procdump
      condition: selection
    falsepositives:
      - Authorized security tooling
    level: high
""")

RULE_EXFIL = textwrap.dedent("""\
    title: Detect T1041 via Network
    id: aaaaaaaa-0003-0003-0003-000000000003
    status: experimental
    description: Detects large outbound transfers indicative of data exfiltration.
    tags:
      - attack.t1041
      - attack.exfiltration
    logsource:
      category: network_connection
    detection:
      selection:
        bytes_out|gt: 50000
      condition: selection
    falsepositives:
      - Backup jobs
    level: medium
""")

RULE_CMD_EXEC = textwrap.dedent("""\
    title: Detect T1059 via OS Log
    id: aaaaaaaa-0004-0004-0004-000000000004
    status: experimental
    description: Detects command-interpreter execution on Windows.
    tags:
      - attack.t1059
      - attack.execution
    logsource:
      category: process_creation
      product: windows
    detection:
      selection:
        CommandLine|contains:
          - cmd.exe
          - powershell
      condition: selection
    falsepositives:
      - Legitimate administrative activity
    level: medium
""")

# ---------------------------------------------------------------------------
# Expected SPL fragments
# ---------------------------------------------------------------------------

EXPECTED_BRUTE_FORCE_SPL = "EventCode=4625"
EXPECTED_CREDENTIAL_DUMP_TERMS = [
    'process.command_line="*mimikatz*"',
    'process.command_line="*lsass*"',
    'process.command_line="*procdump*"',
]
EXPECTED_EXFIL_SPL = "bytes_out > 50000"
EXPECTED_CMD_EXEC_FIELD = "process.command_line"
EXPECTED_CMD_EXEC_WILDCARD = "*cmd.exe*"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_field_name_mapping_event_id():
    """Sigma EventID maps to Splunk EventCode with correct value."""
    spl = sigma_to_spl(RULE_BRUTE_FORCE)
    assert "| search" in spl
    assert EXPECTED_BRUTE_FORCE_SPL in spl


def test_contains_any_produces_or_with_wildcards():
    """CommandLine|contains|any list → OR clause with *value* wildcards, field mapped."""
    spl = sigma_to_spl(RULE_CREDENTIAL_DUMP)
    assert "| search" in spl
    for term in EXPECTED_CREDENTIAL_DUMP_TERMS:
        assert term in spl


def test_numeric_gt_modifier():
    """bytes_out|gt: 50000 translates to SPL 'bytes_out > 50000'."""
    spl = sigma_to_spl(RULE_EXFIL)
    assert "| search" in spl
    assert EXPECTED_EXFIL_SPL in spl


def test_contains_modifier_with_field_mapping():
    """CommandLine|contains maps field to process.command_line and wraps values in wildcards."""
    spl = sigma_to_spl(RULE_CMD_EXEC)
    assert "| search" in spl
    assert EXPECTED_CMD_EXEC_FIELD in spl
    assert EXPECTED_CMD_EXEC_WILDCARD in spl
