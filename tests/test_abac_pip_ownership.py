# CUI // SP-CTI
"""Tests: ABAC ownership refs must never collapse to match-all (prop-sec-01).

``${subject.user_id}`` is a dotted reference that PIP resolves by walking a
**nested** context (``ctx["subject"]["user_id"]``). It used to be resolved
against a *flattened* dict, so the dotted path never resolved and returned
``None`` — and ``_match_condition(None, actual)`` returns ``True``. That turned
every ownership-scoped Permit into a match-all.

Because ``PDP.evaluate`` is first-match-wins and ``proposal_section_writer_own``
(Permit) is ordered before ``proposal_section_writer_deny_unassigned`` (Deny),
the bug let ANY ``section_writer`` PUT/PATCH ANY proposal section — horizontal
privilege escalation on the live routes in ``tools/dashboard/api/proposals.py``.

The deny cases below are the point of this module: an allow-only suite would
still pass against the fail-open engine.
"""

import importlib

import pytest

abac = importlib.import_module("tools.security.abac_engine")


def _subject(user_id: str, role: str) -> dict:
    """Mirror _ctx_to_subject()'s shape."""
    return {
        "user_id": user_id,
        "role": role,
        "clearance_level": 0,
        "compartments": [],
        "tenant_id": None,
        "classification": "CUI",
    }


ALICE = _subject("alice@example.mil", "section_writer")
BOB = _subject("bob@example.mil", "section_writer")
CAPTURE_MGR = _subject("carol@example.mil", "capture_mgr")


def _section(writer_email: str) -> dict:
    return {"type": "proposal_section", "writer_email": writer_email}


@pytest.fixture(autouse=True)
def _fresh_policies():
    """PDP memoizes decisions; reload + clear so tests don't bleed."""
    abac.reload_policies()
    yield
    abac.reload_policies()


class TestPIPResolution:
    def test_dotted_ref_resolves_against_nested_context(self):
        ctx = {"subject": {"user_id": "alice@example.mil"}, "resource": {}, "environment": {}}
        assert abac.PIP.resolve("${subject.user_id}", ctx) == "alice@example.mil"

    def test_dotted_ref_against_flat_context_yields_none(self):
        """The original bug: a flattened context cannot satisfy a dotted path."""
        flat = {"user_id": "alice@example.mil"}
        assert abac.PIP.resolve("${subject.user_id}", flat) is None

    def test_none_condition_still_matches_all(self):
        """_match_condition's None==match-all semantics are unchanged...

        ...which is exactly why an unresolved ref must never reach it as None.
        """
        pdp = abac.PDP()
        assert pdp._match_condition(None, "anything") is True

    def test_unresolved_ref_becomes_non_matching_sentinel(self):
        """A ref that cannot resolve must fail to match, not match everything."""
        pdp = abac.PDP()
        sentinel = "__UNRESOLVED_ABAC_REF__"
        assert pdp._match_condition(sentinel, "alice@example.mil") is False
        assert pdp._match_condition(sentinel, None) is False


class TestSectionOwnershipNeedToKnow:
    def test_writer_edits_own_section_is_permitted(self):
        d = abac.evaluate(ALICE, _section("alice@example.mil"), "PUT")
        assert d.permit is True
        assert d.policy_name == "proposal_section_writer_own"

    def test_writer_denied_on_another_writers_section(self):
        """THE deny case — this is what the fail-open bug allowed."""
        d = abac.evaluate(ALICE, _section("bob@example.mil"), "PUT")
        assert d.permit is False, (
            "section_writer must not edit another writer's section; "
            "an ownership ref collapsed to match-all"
        )

    def test_writer_denied_on_unassigned_section(self):
        d = abac.evaluate(ALICE, _section(""), "PUT")
        assert d.permit is False

    def test_writer_denied_when_writer_email_absent(self):
        d = abac.evaluate(ALICE, {"type": "proposal_section"}, "PUT")
        assert d.permit is False

    def test_privileged_role_edits_any_section(self):
        """The fix must not over-deny: privileged roles are not ownership-scoped."""
        d = abac.evaluate(CAPTURE_MGR, _section("bob@example.mil"), "PUT")
        assert d.permit is True

    def test_both_writers_are_scoped_to_their_own_rows(self):
        assert abac.evaluate(BOB, _section("bob@example.mil"), "PUT").permit is True
        assert abac.evaluate(BOB, _section("alice@example.mil"), "PUT").permit is False

    def test_anonymous_subject_denied(self):
        d = abac.evaluate({}, _section("alice@example.mil"), "PUT")
        assert d.permit is False
