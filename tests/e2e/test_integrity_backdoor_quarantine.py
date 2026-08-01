# CUI // SP-CTI
"""SIPA Backdoor-Assessment E2E flow — QUARANTINE (sipa-vv-04-d3).

The executable, deterministic counterpart of ``integrity_backdoor_quarantine.spec.ts``.
The Playwright spec drives the SAME flow through a live browser/HTTP client when a
dashboard with the integrity canvas is up; this module drives it through the REAL
SIPA pipeline (``engine.assess``) + the REAL Flask blueprint (test client) so the
flow is provable in CI without a running server.

The flow under test (Backdoor Assessment):

  1. Submit the committed fixture ``fixtures/integrity_backdoor_pkg/`` — a package
     that *claims* "formatting only" (README) while hiding base64-decoded socket /
     exec payloads (formatter.py).
  2. Assert the engine verdict is ``QUARANTINE`` (the disclosed-vs-exercised gap
     saturates the risk score; the artifact is staged + scanned, never executed).
  3. Open the assessment detail view (``GET /integrity/<id>`` page +
     ``GET /api/integrity/assessment/<id>``) and assert it surfaces the required
     fields:
       * capabilities  : network_egress, dynamic_code, obfuscation
       * "undiscovered" flag : at least one ``undisclosed_capability`` finding
       * "known-bad"-grade flag : at least one ``critical`` finding
         (a literal ``known_bad_signature`` also fires when Semgrep is unavailable,
         so it is asserted softly — see ``test_known_bad_signature_when_no_semgrep``).

DB seam: both the engine and the blueprint reach the DB via
``tools.db.storage.get_connection``; the suite patches that to hand back one shared
in-memory SQLite connection (``close()`` is a no-op so it survives across calls),
mirroring ``tests/test_integrity_blueprint.py``.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

# Required capabilities the detail view must surface for a backdoor verdict.
REQUIRED_CAPABILITIES = {"network_egress", "dynamic_code", "obfuscation"}

# The committed fixture: claims "formatting only", hides socket/exec payloads.
FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "integrity_backdoor_pkg"


@pytest.fixture
def shared_conn(monkeypatch):
    """One shared in-memory DB, wired in as the result of ``get_connection`` for
    both the engine pipeline writes and the blueprint reads."""
    import importlib

    from _sql_compat import translating

    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_INTEGRITY_ENABLED", "true")

    from tools.integrity.db import init_db as init_db_mod

    raw = sqlite3.connect(":memory:")
    raw.row_factory = sqlite3.Row

    # The integrity engine and blueprint author ``%s`` for PostgreSQL; a bare
    # sqlite3 connection bypasses ``storage.translate_sql`` and every statement
    # raises ``near "%": syntax error``. ``unclosable`` keeps the in-memory DB
    # alive across the ``close()`` both layers call in their finally blocks.
    conn = translating(raw, unclosable=True)
    init_db_mod.init_db(conn)

    # The root ``tools.*`` namespace is a compat shim redirecting to
    # ``icdev.tools.*``; patch the attribute on the exact module callers import
    # (resolved via importlib, not a bare ``import ... as``).
    storage = importlib.import_module("tools.db.storage")
    monkeypatch.setattr(storage, "get_connection", lambda *a, **k: conn)
    yield conn
    raw.close()


@pytest.fixture
def assessment(shared_conn):
    """Run the REAL SIPA pipeline over the fixture once; return its disposition.

    ``provenance_blind`` forces Mode B (claimed-vs-exercised reconciliation), which
    is the external-untrusted-code path this E2E exercises.
    """
    from tools.integrity import engine

    assert FIXTURE_DIR.is_dir(), f"fixture missing: {FIXTURE_DIR}"
    return engine.assess(str(FIXTURE_DIR), mode="provenance_blind", conn=shared_conn)


@pytest.fixture
def client(shared_conn):
    """Flask test client with the integrity blueprint mounted (flag on)."""
    from flask import Flask

    from tools.integrity.blueprint import create_integrity_blueprint

    bp = create_integrity_blueprint()
    assert bp is not None, "blueprint should mount with ICDEV_INTEGRITY_ENABLED=true"
    app = Flask(__name__)
    app.register_blueprint(bp)
    app.config.update(TESTING=True)
    return app.test_client()


# --------------------------------------------------------------------------- #
# Step 2 — verdict is QUARANTINE
# --------------------------------------------------------------------------- #
def test_backdoor_fixture_verdict_is_quarantine(assessment):
    """The claims-vs-payload gap drives the verdict to QUARANTINE."""
    assert assessment["verdict"] == "quarantine", assessment
    assert assessment["status"] == "assessed"
    # Risk saturates well past the quarantine threshold (undisclosed caps dominate).
    assert assessment["risk_score"] >= 70.0


# --------------------------------------------------------------------------- #
# Step 3 — detail view surfaces the required fields
# --------------------------------------------------------------------------- #
def test_detail_api_surfaces_required_capabilities(assessment, client):
    """The detail API exposes the network_egress / dynamic_code / obfuscation caps."""
    aid = assessment["assessment_id"]
    resp = client.get(f"/api/integrity/assessment/{aid}")
    assert resp.status_code == 200
    detail = resp.get_json()

    cap_types = {c["capability_type"] for c in detail["capabilities"]}
    missing = REQUIRED_CAPABILITIES - cap_types
    assert not missing, f"detail view missing capabilities {missing}; saw {cap_types}"

    assert (detail["verdict"] or {}).get("verdict") == "quarantine"


def test_detail_api_surfaces_undisclosed_and_known_bad_flags(assessment, client):
    """The detail view carries the 'undiscovered' (undisclosed_capability) gap flag
    and a 'known-bad'-grade critical finding."""
    aid = assessment["assessment_id"]
    detail = client.get(f"/api/integrity/assessment/{aid}").get_json()

    finding_types = {f["finding_type"] for f in detail["findings"]}
    severities = {f["severity"] for f in detail["findings"]}

    assert "undisclosed_capability" in finding_types, (
        f"expected an undisclosed_capability (undiscovered) flag; saw {finding_types}"
    )
    assert "critical" in severities, (
        f"expected a critical (known-bad-grade) finding; saw {severities}"
    )


def test_detail_page_renders_quarantine_verdict(assessment, client):
    """The detail PAGE (``/integrity/<id>``) responds for the quarantined item.

    Bare ``Flask(__name__)`` has no dashboard template loader, so the blueprint's
    guarded ``render_template`` falls back to its JSON body — either way the page
    route returns 200 and carries the QUARANTINE verdict + the capability rows.
    """
    aid = assessment["assessment_id"]
    resp = client.get(f"/integrity/{aid}")
    assert resp.status_code == 200

    body = resp.get_data(as_text=True).lower()
    assert "quarantine" in body
    # Capability tokens appear raw in the JSON fallback (and underscore-spaced in
    # the rendered template); accept either spelling.
    for cap in REQUIRED_CAPABILITIES:
        assert cap in body or cap.replace("_", " ") in body, f"detail page missing {cap}"


# --------------------------------------------------------------------------- #
# Soft assertion — literal known_bad_signature only when Semgrep is unavailable
# --------------------------------------------------------------------------- #
def test_known_bad_signature_when_no_semgrep(assessment, client):
    """``exec(base64.b64decode(...))`` trips the ``decode_then_exec`` malicious
    signature, BUT only through the regex fallback (when Semgrep is absent). With
    Semgrep installed-but-quiet the literal ``known_bad_signature`` finding does
    not fire, so this is asserted softly and the deterministic 'known-bad'-grade
    coverage lives in :func:`test_detail_api_surfaces_undisclosed_and_known_bad_flags`.
    """
    aid = assessment["assessment_id"]
    detail = client.get(f"/api/integrity/assessment/{aid}").get_json()
    finding_types = {f["finding_type"] for f in detail["findings"]}
    if "known_bad_signature" not in finding_types:
        pytest.skip("Semgrep present-but-quiet: regex signature fallback did not run")
    assert any(
        f["finding_type"] == "known_bad_signature" and f["severity"] == "critical"
        for f in detail["findings"]
    )
# CUI // SP-CTI
