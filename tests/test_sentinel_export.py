from __future__ import annotations
# CUI // SP-CTI
"""Tests for tools/observability_canvas/exporters/sentinel.py

4 cases:
  1. Field name mapping  — EventID → EventID equality, table = SecurityEvent
  2. contains|any        — list → or-joined 'contains' clauses
  3. Numeric gt modifier — bytes_out|gt → DstBytes > 50000
  4. contains + mapping  — CommandLine|contains → CommandLine contains clauses
"""

import textwrap

from tools.observability_canvas.exporters.sentinel import sigma_to_kql

# ---------------------------------------------------------------------------
# Fixture rules
# ---------------------------------------------------------------------------

RULE_BRUTE_FORCE = textwrap.dedent("""\
    title: Detect T1110 via Auth Log
    id: cccccccc-0001-0001-0001-000000000001
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
    id: cccccccc-0002-0002-0002-000000000002
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
    id: cccccccc-0003-0003-0003-000000000003
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
    id: cccccccc-0004-0004-0004-000000000004
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
# Tests
# ---------------------------------------------------------------------------

def test_field_name_mapping_event_id():
    """Sigma EventID maps to EventID, table is SecurityEvent for authentication category."""
    kql = sigma_to_kql(RULE_BRUTE_FORCE)
    assert "SecurityEvent" in kql
    assert "EventID == 4625" in kql


def test_contains_any_produces_or_clauses():
    """CommandLine|contains|any list → or-joined 'contains' expressions."""
    kql = sigma_to_kql(RULE_CREDENTIAL_DUMP)
    assert 'CommandLine contains "mimikatz"' in kql
    assert 'CommandLine contains "lsass"' in kql
    assert 'CommandLine contains "procdump"' in kql
    assert " or " in kql


def test_numeric_gt_modifier():
    """bytes_out|gt: 50000 translates to KQL 'DstBytes > 50000'."""
    kql = sigma_to_kql(RULE_EXFIL)
    assert "DstBytes > 50000" in kql


def test_contains_modifier_with_field_mapping():
    """CommandLine|contains maps to CommandLine contains clauses in DeviceProcessEvents."""
    kql = sigma_to_kql(RULE_CMD_EXEC)
    assert "DeviceProcessEvents" in kql
    assert 'CommandLine contains "cmd.exe"' in kql
    assert 'CommandLine contains "powershell"' in kql
