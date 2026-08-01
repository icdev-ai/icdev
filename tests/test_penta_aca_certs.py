# CUI // SP-CTI
"""penta-aca-07 — FORGE Academy certificate lifecycle tests.

Covers the certificate subsystem end to end:

  * eligibility gating per CERT_TIERS (apps/forge_academy/constants.py) — a fresh
    user is NOT eligible and every gate is well-formed;
  * issue_certificate is gated on eligibility (None when ineligible);
  * issue -> verify_certificate_token round trip (with eligibility forced so the
    lifecycle is tested in isolation from the gate logic above);
  * a tampered token is rejected;
  * idempotent issuance (same token on re-issue);
  * my-certificates listing (get_user_certificates).
"""

from __future__ import annotations

import pytest

from apps.forge_academy import db
from apps.forge_academy.constants import CERT_BY_KEY, CERT_TIERS


@pytest.fixture(scope="module")
def _migrated():
    db.migrate()


def _new_user(email: str) -> dict:
    return db.get_or_create_user(email, display_name=email.split("@")[0], tenant_id=None)


# ---------------------------------------------------------------------------
# CERT_TIERS shape + eligibility gating (real gate logic)
# ---------------------------------------------------------------------------

def test_cert_tiers_well_formed():
    assert len(CERT_TIERS) >= 3
    keys = [c["key"] for c in CERT_TIERS]
    assert keys == ["foundation", "practitioner", "expert"]
    for c in CERT_TIERS:
        for field in ("key", "label", "requirements", "xp_bonus"):
            assert field in c, f"cert {c.get('key')} missing {field}"
        assert isinstance(c["requirements"], dict) and c["requirements"]
    assert CERT_BY_KEY["foundation"]["label"].startswith("FORGE")


@pytest.mark.parametrize("cert_key", [c["key"] for c in CERT_TIERS])
def test_fresh_user_not_eligible_but_gates_well_formed(_migrated, cert_key):
    user = _new_user(f"fresh_{cert_key}@test.local")
    result = db.check_cert_eligibility(user["id"], cert_key)
    assert set(result) >= {"eligible", "gates"}
    assert result["eligible"] is False, "a brand-new user must not be cert-eligible"
    assert result["gates"], "eligibility must expose the gates it evaluated"
    for gate in result["gates"]:
        assert {"name", "met", "detail"} <= set(gate)
        assert isinstance(gate["met"], bool)


def test_issue_is_blocked_when_ineligible(_migrated):
    user = _new_user("ineligible@test.local")
    assert db.issue_certificate(user["id"], "foundation") is None


# ---------------------------------------------------------------------------
# Issue -> verify round trip (eligibility forced to isolate the lifecycle)
# ---------------------------------------------------------------------------

@pytest.fixture()
def _force_eligible(monkeypatch):
    # Force the eligibility gate open so the issue/verify LIFECYCLE is tested in
    # isolation from the gate logic (which is covered above).
    monkeypatch.setattr(
        db, "check_cert_eligibility",
        lambda user_id, cert_key: {"eligible": True, "gates": []},
    )
    # penta-fix-03: the real update_user_xp is now exercised. issue_certificate
    # awards the XP bonus on the SAME connection/transaction as the cert INSERT
    # (single commit), so it no longer self-deadlocks the SQLite backend and the
    # no-op monkeypatch that used to hide the bug is gone.


def _as_dict(cert):
    return dict(cert) if not isinstance(cert, dict) else cert


def test_issue_verify_round_trip(_migrated, _force_eligible):
    user = _new_user("cert_holder@test.local")
    cert = db.issue_certificate(user["id"], "foundation")
    assert cert is not None, "eligible user should receive a certificate"
    cert = _as_dict(cert)
    token = cert["token"]
    assert token and cert["cert_tier"] == "foundation"

    verified = db.verify_certificate_token(token)
    assert verified is not None, "a real token must verify"
    assert verified["cert_tier"] == "foundation"
    assert verified["user_id"] == user["id"]
    # verify joins fa_users, so identity fields ride along
    assert "display_name" in verified and "role" in verified


def test_issue_awards_xp_without_deadlock(_migrated, monkeypatch):
    """penta-fix-03 regression: issue_certificate awards its XP bonus on the SAME
    connection/transaction as the cert INSERT, so under the SQLite test backend it
    completes (no 'database is locked' self-deadlock) AND the bonus lands. The real
    update_user_xp is intentionally NOT monkeypatched here."""
    import os

    from tools.db.storage import get_connection

    # This regression is only meaningful on the SQLite backend (conftest forces it).
    assert os.environ.get("ICDEV_STORAGE_BACKEND", "sqlite") == "sqlite"

    monkeypatch.setattr(
        db, "check_cert_eligibility",
        lambda user_id, cert_key: {"eligible": True, "gates": []},
    )

    # Unique email per run: the test DB persists across pytest invocations, and
    # issue_certificate is idempotent — a pre-existing cert would short-circuit
    # before the XP award and make the delta assertion flaky.
    import uuid
    user = _new_user(f"xp_award_{uuid.uuid4().hex[:8]}@test.local")
    before = get_connection().execute(
        "SELECT xp FROM fa_users WHERE id=?", (user["id"],)
    ).fetchone()["xp"]

    cert = _as_dict(db.issue_certificate(user["id"], "foundation"))  # would hang/error if deadlocked
    assert cert["token"], "cert issued with a token"

    after = get_connection().execute(
        "SELECT xp FROM fa_users WHERE id=?", (user["id"],)
    ).fetchone()["xp"]
    # foundation cert grants a 1000 XP bonus (constants.CERT_TIERS).
    bonus = CERT_BY_KEY["foundation"].get("xp_bonus", 0)
    assert bonus > 0
    assert after == before + bonus, f"XP bonus not awarded: {before} -> {after} (bonus {bonus})"

    # Contract preserved: the issued token still verifies.
    assert db.verify_certificate_token(cert["token"]) is not None


def test_tampered_token_is_rejected(_migrated, _force_eligible):
    user = _new_user("tamper@test.local")
    cert = _as_dict(db.issue_certificate(user["id"], "foundation"))
    good = cert["token"]
    assert db.verify_certificate_token(good) is not None
    # Flip the token — must not verify.
    assert db.verify_certificate_token(good + "TAMPER") is None
    assert db.verify_certificate_token("not-a-real-token") is None


def test_issuance_is_idempotent(_migrated, _force_eligible):
    user = _new_user("idempotent@test.local")
    first = _as_dict(db.issue_certificate(user["id"], "foundation"))
    second = _as_dict(db.issue_certificate(user["id"], "foundation"))
    assert first["token"] == second["token"], "re-issue must return the same certificate"
    # exactly one row for this (user, tier)
    certs = [c for c in db.get_user_certificates(user["id"]) if c["cert_tier"] == "foundation"]
    assert len(certs) == 1


def test_my_certificates_listing(_migrated, _force_eligible):
    user = _new_user("multi_cert@test.local")
    db.issue_certificate(user["id"], "foundation")
    db.issue_certificate(user["id"], "practitioner")
    certs = db.get_user_certificates(user["id"])
    tiers = {c["cert_tier"] for c in certs}
    assert {"foundation", "practitioner"} <= tiers
    # every listed cert carries a verifiable token
    for c in certs:
        assert db.verify_certificate_token(c["token"]) is not None
