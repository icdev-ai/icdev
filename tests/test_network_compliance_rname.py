# [CUI // SP-CTI]
"""Unit tests for R-NAME rule: validate_hostname_pattern."""

from tools.network.compliance import validate_hostname_pattern


def test_hostname_matches_template_pattern():
    # {role}-{site}-{nn} template with a well-formed 2-digit sequence
    is_valid, suggested = validate_hostname_pattern("rtr-nyc-01", "{role}-{site}-{nn}")
    assert is_valid is True
    assert suggested is None


def test_hostname_violates_template_with_derivable_fix():
    # Single-digit nn — engine zero-pads to suggest a corrected name
    is_valid, suggested = validate_hostname_pattern("rtr-nyc-1", "{role}-{site}-{nn}")
    assert is_valid is False
    assert suggested == "rtr-nyc-01"


def test_hostname_violates_regex_pattern():
    # Regex requiring rtr/sw/fw prefix — hostname fails, no auto-fix possible
    is_valid, suggested = validate_hostname_pattern(
        "badhost!", r"^(rtr|sw|fw)-[a-z]{3}-\d{2}$"
    )
    assert is_valid is False
    assert suggested is None
