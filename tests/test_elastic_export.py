from __future__ import annotations
# CUI // SP-CTI
"""Tests for tools/observability_canvas/exporters/elastic.py

4 cases:
  1. Field name mapping  — EventID → winlog.event_id term query
  2. contains|any        — list → bool/should with wildcard clauses
  3. Numeric gt modifier — bytes_out|gt → range > operator
  4. contains + mapping  — CommandLine|contains → process.command_line wildcard
"""

import json
import textwrap

from tools.observability_canvas.exporters.elastic import sigma_to_eql

# ---------------------------------------------------------------------------
# Fixture rules (same scenarios as Splunk tests for cross-exporter parity)
# ---------------------------------------------------------------------------

RULE_BRUTE_FORCE = textwrap.dedent("""\
    title: Detect T1110 via Auth Log
    id: bbbbbbbb-0001-0001-0001-000000000001
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
    id: bbbbbbbb-0002-0002-0002-000000000002
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
    id: bbbbbbbb-0003-0003-0003-000000000003
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
    id: bbbbbbbb-0004-0004-0004-000000000004
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
    """Sigma EventID maps to winlog.event_id with correct numeric term value."""
    result = json.loads(sigma_to_eql(RULE_BRUTE_FORCE))
    query = result["query"]
    # Must be a term query on winlog.event_id
    assert query.get("term", {}).get("winlog.event_id", {}).get("value") == 4625


def test_contains_any_produces_bool_should_wildcards():
    """CommandLine|contains|any list → bool/should with *value* wildcard clauses."""
    result = json.loads(sigma_to_eql(RULE_CREDENTIAL_DUMP))
    query = result["query"]
    should = query.get("bool", {}).get("should", [])
    assert len(should) == 3
    wildcard_values = [c["wildcard"]["process.command_line"]["value"] for c in should]
    assert "*mimikatz*" in wildcard_values
    assert "*lsass*" in wildcard_values
    assert "*procdump*" in wildcard_values


def test_numeric_gt_modifier():
    """bytes_out|gt: 50000 translates to ES range query with gt: 50000."""
    result = json.loads(sigma_to_eql(RULE_EXFIL))
    query = result["query"]
    assert query.get("range", {}).get("destination.bytes", {}).get("gt") == 50000


def test_contains_modifier_with_field_mapping():
    """CommandLine|contains list maps field to process.command_line with wildcard clauses."""
    result = json.loads(sigma_to_eql(RULE_CMD_EXEC))
    query = result["query"]
    should = query.get("bool", {}).get("should", [])
    assert len(should) == 2
    for clause in should:
        assert "process.command_line" in clause.get("wildcard", {})
    values = [c["wildcard"]["process.command_line"]["value"] for c in should]
    assert "*cmd.exe*" in values
    assert "*powershell*" in values
