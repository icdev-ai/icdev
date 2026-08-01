#!/usr/bin/env python3
# CUI // SP-CTI
"""prop-sec-01 — Proposal ABAC + column-masking policies.

Verifies:
1. args/security_config.yaml defines proposal ABAC policies (section + draft)
   and column-masking policies for cost/price, win-strategy, and competitor
   intel tables (reviewer/co masked; capture_mgr/admin untouched).
2. The ABAC engine enforces need-to-know on proposal_sections.writer_email:
   a section_writer may edit only sections assigned to them. Dangling
   ${subject.*} references must never collapse to match-all (the PIP
   resolution bug fixed in this task).
3. @abac_protect is wired on the section edit + draft endpoints.
"""

from pathlib import Path

import yaml

from tools.security.abac_engine import evaluate, reload_policies
from tools.security.column_security import (
    get_column_policies_for_role,
    mask_columns,
)

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = BASE_DIR / "args" / "security_config.yaml"


def _config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


# ---------------------------------------------------------------------------
# Config shape
# ---------------------------------------------------------------------------

class TestProposalPolicyConfig:
    def test_abac_section_policies_present(self):
        names = {p["name"] for p in _config()["abac_policies"]}
        assert "proposal_section_privileged_write" in names
        assert "proposal_section_writer_own" in names
        assert "proposal_section_writer_deny_unassigned" in names

    def test_abac_draft_policies_present(self):
        names = {p["name"] for p in _config()["abac_policies"]}
        assert "proposal_draft_privileged_write" in names
        assert "proposal_draft_writer_own" in names
        assert "proposal_draft_writer_deny_unassigned" in names

    def test_column_policies_cover_proposal_tables(self):
        policies = _config()["column_policies"]
        covered = {(p["table"], p["role"]) for p in policies}
        for table in (
            "proposal_opportunities",
            "proposal_competitors",
            "cpmp_clins",
            "pg_cost_volumes",
            "pg_win_themes",
            "pg_competitor_awards",
        ):
            for role in ("reviewer", "co"):
                assert (table, role) in covered, f"missing column policy: {table}/{role}"

    def test_no_column_policy_for_capture_mgr_or_admin(self):
        """capture_mgr/admin see full rows — no masking policies for them."""
        policies = _config()["column_policies"]
        proposal_tables = {
            "proposal_opportunities", "proposal_competitors", "cpmp_clins",
            "pg_cost_volumes", "pg_win_themes", "pg_competitor_awards",
        }
        for p in policies:
            if p["table"] in proposal_tables:
                assert p["role"] not in ("capture_mgr", "admin", "proposal_mgr")


# ---------------------------------------------------------------------------
# Column masking behavior
# ---------------------------------------------------------------------------

class TestProposalColumnMasking:
    def test_cost_volume_masked_for_reviewer(self):
        policies = get_column_policies_for_role("pg_cost_volumes", "reviewer")
        assert policies, "pg_cost_volumes reviewer policy not loaded"
        row = {
            "id": "cv-1",
            "contract_type": "ffp",
            "total_evaluated_price": 12_500_000.0,
            "ptw_estimate_low": 11_000_000.0,
            "ptw_estimate_high": 13_000_000.0,
            "fee_rate": 0.08,
        }
        masked = mask_columns(row, policies)
        assert masked["total_evaluated_price"] is None
        assert masked["ptw_estimate_low"] is None
        assert masked["ptw_estimate_high"] is None
        assert masked["fee_rate"] is None
        # Non-sensitive columns untouched
        assert masked["contract_type"] == "ffp"
        assert masked["id"] == "cv-1"

    def test_win_themes_masked_for_co(self):
        policies = get_column_policies_for_role("pg_win_themes", "co")
        assert policies
        row = {
            "id": "wt-1",
            "theme_type": "discriminator",
            "theme_statement": "Only vendor with deployed IL5 ATO",
            "supporting_evidence": "Contract W15P7T-XX",
            "ghost_competitor": "Acme Corp",
        }
        masked = mask_columns(row, policies)
        assert masked["theme_statement"] == "[REDACTED]"
        assert masked["supporting_evidence"] is None
        assert masked["ghost_competitor"] is None
        assert masked["theme_type"] == "discriminator"

    def test_capture_mgr_sees_full_row(self):
        assert get_column_policies_for_role("pg_cost_volumes", "capture_mgr") == {}
        assert get_column_policies_for_role("pg_win_themes", "capture_mgr") == {}


# ---------------------------------------------------------------------------
# ABAC need-to-know on owned rows
# ---------------------------------------------------------------------------

class TestProposalAbacNeedToKnow:
    @classmethod
    def setup_class(cls):
        reload_policies()

    def test_writer_edits_own_section(self):
        d = evaluate(
            {"user_id": "alice@example.mil", "role": "section_writer"},
            {"type": "proposal_section", "writer_email": "alice@example.mil"},
            "PUT",
        )
        assert d.permit
        assert d.policy_name == "proposal_section_writer_own"

    def test_writer_denied_on_unassigned_section(self):
        d = evaluate(
            {"user_id": "alice@example.mil", "role": "section_writer"},
            {"type": "proposal_section", "writer_email": "bob@example.mil"},
            "PUT",
        )
        assert not d.permit

    def test_writer_denied_on_section_without_writer(self):
        """Unassigned section (empty writer_email) must not match writer_own."""
        d = evaluate(
            {"user_id": "alice@example.mil", "role": "section_writer"},
            {"type": "proposal_section", "writer_email": ""},
            "PUT",
        )
        assert not d.permit

    def test_capture_mgr_edits_any_section(self):
        d = evaluate(
            {"user_id": "cm@example.mil", "role": "capture_mgr"},
            {"type": "proposal_section", "writer_email": "bob@example.mil"},
            "PUT",
        )
        assert d.permit

    def test_draft_writer_own_and_deny(self):
        own = evaluate(
            {"user_id": "alice@example.mil", "role": "section_writer"},
            {"type": "proposal_draft", "writer_email": "alice@example.mil"},
            "PUT",
        )
        other = evaluate(
            {"user_id": "alice@example.mil", "role": "section_writer"},
            {"type": "proposal_draft", "writer_email": "bob@example.mil"},
            "PUT",
        )
        assert own.permit
        assert not other.permit

    def test_reviewer_may_approve_draft(self):
        d = evaluate(
            {"user_id": "rev@example.mil", "role": "reviewer"},
            {"type": "proposal_draft", "writer_email": "bob@example.mil"},
            "PUT",
        )
        assert d.permit
        assert d.policy_name == "proposal_draft_privileged_write"

    def test_anonymous_denied(self):
        d = evaluate({}, {"type": "proposal_draft", "writer_email": "x@y.mil"}, "PUT")
        assert not d.permit


# ---------------------------------------------------------------------------
# Endpoint wiring
# ---------------------------------------------------------------------------

class TestEndpointWiring:
    def test_proposals_section_endpoints_protected(self):
        src = (BASE_DIR / "tools" / "dashboard" / "api" / "proposals.py").read_text(
            encoding="utf-8"
        )
        assert "@abac_protect(_section_resource_attrs" in src

    def test_govcon_draft_endpoints_protected(self):
        src = (BASE_DIR / "tools" / "dashboard" / "api" / "govcon.py").read_text(
            encoding="utf-8"
        )
        assert src.count("@abac_protect(_draft_resource_attrs") >= 3
        assert '"type": "proposal_draft"' in src
