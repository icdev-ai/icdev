#!/usr/bin/env python3
# CUI // SP-CTI
"""Unit tests for tools.saas.auth.mfa (TOTP MFA, G-07).

Skipped when pyotp is not installed (optional dependency).
"""
import hashlib
import json
import os
import sqlite3
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

os.environ.setdefault("ICDEV_STORAGE_BACKEND", "sqlite")

pyotp = pytest.importorskip("pyotp", reason="pyotp not installed")

import tools.saas.auth.mfa as _mfa
from tests._sql_compat import translating


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def mfa_db(tmp_path, monkeypatch):
    """SQLite DB wired into the mfa module, with PG placeholders translated.

    mfa.py authors its SQL for PostgreSQL (``%s``) — the primary backend — and
    ``StorageConnection`` rewrites the placeholders when the backend is SQLite.
    The fixture stands in for ``get_connection()``, so it has to keep that layer:
    a bare ``sqlite3`` connection makes every statement in the module under test
    raise ``sqlite3.OperationalError: near "%": syntax error``, which is what
    took this whole file red. ``tests/_sql_compat`` delegates to the same
    ``translate_sql`` the runtime uses, so the fixture cannot drift from it.

    ``unclosable=True`` because the wrapper's ``__exit__`` closes, while every
    ``with _mfa._conn()`` block here shares this one connection with the test.
    """
    db_path = tmp_path / "mfa_test.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript(
        _mfa.MFA_SCHEMA + "\n" + _mfa.MFA_ATTEMPT_SCHEMA
    )
    conn.commit()

    monkeypatch.setattr(_mfa, "_conn", lambda: translating(conn, unclosable=True))
    return conn


# ---------------------------------------------------------------------------
# Enrollment
# ---------------------------------------------------------------------------

class TestEnroll:
    def test_returns_enroll_result(self, mfa_db):
        result = _mfa.enroll("user-001", "alice@example.com")
        assert result.user_id == "user-001"
        assert result.totp_secret
        assert result.provisioning_uri.startswith("otpauth://totp/")
        assert len(result.backup_codes) == 10
        assert all(len(c) == 8 for c in result.backup_codes)

    def test_secret_persisted(self, mfa_db):
        result = _mfa.enroll("user-002", "bob@example.com")
        row = mfa_db.execute(
            "SELECT totp_secret, enabled FROM user_mfa WHERE user_id = ?",
            ("user-002",),
        ).fetchone()
        assert row is not None
        assert row["totp_secret"] == result.totp_secret
        assert row["enabled"] == 1

    def test_backup_codes_hashed_in_db(self, mfa_db):
        result = _mfa.enroll("user-003", "charlie@example.com")
        row = mfa_db.execute(
            "SELECT backup_codes FROM user_mfa WHERE user_id = ?",
            ("user-003",),
        ).fetchone()
        stored = json.loads(row["backup_codes"])
        assert len(stored) == 10
        for code, h in zip(result.backup_codes, stored):
            assert h == hashlib.sha256(code.encode()).hexdigest()

    def test_reenroll_replaces_secret(self, mfa_db):
        r1 = _mfa.enroll("user-004", "d@example.com")
        r2 = _mfa.enroll("user-004", "d@example.com")
        assert r1.totp_secret != r2.totp_secret
        row = mfa_db.execute(
            "SELECT totp_secret FROM user_mfa WHERE user_id = ?",
            ("user-004",),
        ).fetchone()
        assert row["totp_secret"] == r2.totp_secret


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------

class TestVerify:
    def test_valid_token(self, mfa_db):
        result = _mfa.enroll("user-010", "e@example.com")
        token = pyotp.TOTP(result.totp_secret).now()
        assert _mfa.verify("user-010", token) is True

    def test_invalid_token(self, mfa_db):
        _mfa.enroll("user-011", "f@example.com")
        assert _mfa.verify("user-011", "000000") is False

    def test_missing_user(self, mfa_db):
        assert _mfa.verify("nonexistent-user", "123456") is False

    def test_disabled_mfa(self, mfa_db):
        result = _mfa.enroll("user-012", "g@example.com")
        mfa_db.execute(
            "UPDATE user_mfa SET enabled = 0 WHERE user_id = ?", ("user-012",)
        )
        mfa_db.commit()
        token = pyotp.TOTP(result.totp_secret).now()
        assert _mfa.verify("user-012", token) is False

    def test_verify_logs_attempt(self, mfa_db):
        result = _mfa.enroll("user-013", "h@example.com")
        token = pyotp.TOTP(result.totp_secret).now()
        _mfa.verify("user-013", token, ip_address="10.0.0.1")
        rows = mfa_db.execute(
            "SELECT success FROM mfa_attempts WHERE user_id = ?", ("user-013",)
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["success"] == 1


# ---------------------------------------------------------------------------
# Backup codes
# ---------------------------------------------------------------------------

class TestVerifyBackup:
    def test_valid_backup_code(self, mfa_db):
        result = _mfa.enroll("user-020", "i@example.com")
        code = result.backup_codes[0]
        assert _mfa.verify_backup("user-020", code) is True

    def test_backup_code_consumed(self, mfa_db):
        result = _mfa.enroll("user-021", "j@example.com")
        code = result.backup_codes[0]
        _mfa.verify_backup("user-021", code)
        assert _mfa.verify_backup("user-021", code) is False

    def test_invalid_backup_code(self, mfa_db):
        _mfa.enroll("user-022", "k@example.com")
        assert _mfa.verify_backup("user-022", "XXXXXXXX") is False

    def test_remaining_codes_after_use(self, mfa_db):
        result = _mfa.enroll("user-023", "l@example.com")
        _mfa.verify_backup("user-023", result.backup_codes[0])
        row = mfa_db.execute(
            "SELECT backup_codes FROM user_mfa WHERE user_id = ?", ("user-023",)
        ).fetchone()
        remaining = json.loads(row["backup_codes"])
        assert len(remaining) == 9

    def test_case_insensitive(self, mfa_db):
        result = _mfa.enroll("user-024", "m@example.com")
        code = result.backup_codes[0].lower()
        assert _mfa.verify_backup("user-024", code) is True


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

class TestIsEnrolled:
    def test_enrolled(self, mfa_db):
        _mfa.enroll("user-030", "n@example.com")
        assert _mfa.is_enrolled("user-030") is True

    def test_not_enrolled(self, mfa_db):
        assert _mfa.is_enrolled("user-999") is False

    def test_disabled_not_enrolled(self, mfa_db):
        _mfa.enroll("user-031", "o@example.com")
        mfa_db.execute(
            "UPDATE user_mfa SET enabled = 0 WHERE user_id = ?", ("user-031",)
        )
        mfa_db.commit()
        assert _mfa.is_enrolled("user-031") is False


# ---------------------------------------------------------------------------
# Disable
# ---------------------------------------------------------------------------

class TestDisable:
    def test_disable(self, mfa_db):
        _mfa.enroll("user-040", "p@example.com")
        _mfa.disable("user-040", disabled_by="admin-001")
        assert _mfa.is_enrolled("user-040") is False


# ---------------------------------------------------------------------------
# Dialect
# ---------------------------------------------------------------------------


class TestPgNativePlaceholders:
    """mfa.py must author its runtime SQL for PostgreSQL, the primary backend.

    This is what the fixture above stands in for, so it is worth pinning in both
    directions: reverting the module to SQLite-dialect ``?`` would make the
    fixture pass either way while ``translate_sql`` — an init/seed/migrate
    fallback, never load-bearing — silently carried the runtime on PG.
    """

    def test_no_bare_question_mark_placeholders(self):
        import ast
        import re
        from pathlib import Path

        source = Path(_mfa.__file__).read_text(encoding="utf-8")
        placeholder = re.compile(r"(?<![A-Za-z0-9_])\?")

        offenders = []
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
            if name not in {"execute", "executemany"}:
                continue
            for lit in ast.walk(node.args[0]):
                if isinstance(lit, ast.Constant) and isinstance(lit.value, str):
                    if placeholder.search(lit.value):
                        offenders.append((lit.lineno, lit.value.strip()[:70]))

        assert not offenders, (
            "tools/saas/auth/mfa.py passes SQLite-dialect SQL to execute(); use %s "
            f"so psycopg2 binds it directly: {offenders}"
        )
