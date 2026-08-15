# CUI // SP-CTI
"""A TRUST validation record must LAND and must ANCHOR (trust-anchor-02).

Every test here writes to a real SQLite database and asserts a row count moved.
That is not a stylistic preference. ``register_citation`` validates the
citation type in Python and then swallows database errors, returning ``""`` — so
a mocked ``register_citation`` proves the caller was reached and proves nothing
about whether anything was persisted. Two subsystems shipped on exactly that
gap: ``citation_type='cortex'`` recorded 0 of 285 rows for its entire lifetime,
and ``citation_type='asset_token'`` never anchored once. Both had coverage. Both
asserted the gate RAN.

The fixture's CHECK constraint is rendered from the SAME Python constant the
migration renders, so it can never be more permissive than production, and
``test_the_constraint_can_actually_reject`` proves it is not vacuous.

The fixture also pins EVERY loaded module alias. ``tools.provenance.registry``
and ``icdev.tools.provenance.registry`` are distinct module objects with
distinct ``get_connection`` globals — patching one leaves the other pointed at
the live board, which is how a test silently writes to production and still
passes. ``test_every_alias_is_pinned`` asserts the redirect actually took.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import sys

import pytest

# The REAL runtime factory, bound once at import so a later monkeypatch of
# tools.db.storage.get_connection cannot make the fixture recurse into itself.
# Deliberately NOT sqlite3.connect: get_connection returns a StorageConnection,
# which is what translates the %s placeholders every runtime query uses. Handing
# runtime code a bare sqlite3 connection makes those queries raise
# `near "%": syntax error`, and the surrounding except-branch swallows it into a
# silently green test — the exact shape coherence_checker's test_db_isolation
# gate exists to catch.
from tools.db.storage import get_connection as _real_get_connection
from tools.provenance.citation_types import sqlite_check_clause

ARTIFACT = "The control is implemented. [source: 1]"
APPROVER = "reviewer@agency.gov"


# ── The throwaway database ───────────────────────────────────────────────────

_REGISTRY_DDL = f"""
CREATE TABLE source_citation_registry (
    id TEXT PRIMARY KEY,
    citation_type TEXT NOT NULL {sqlite_check_clause()},
    source_table TEXT NOT NULL,
    source_record_id TEXT NOT NULL,
    source_doc TEXT,
    source_hash TEXT NOT NULL,
    anchor_hash TEXT,
    merkle_root TEXT,
    blockchain_tx_id TEXT,
    classification TEXT DEFAULT 'CUI',
    project_id TEXT,
    trust_score REAL DEFAULT 0.0,
    created_at TEXT
);
CREATE TABLE trust_deltas (
    delta_id            TEXT PRIMARY KEY,
    artifact_id         TEXT NOT NULL,
    stage               TEXT NOT NULL,
    before_hash         TEXT NOT NULL,
    after_hash          TEXT NOT NULL,
    before_text         TEXT,
    after_text          TEXT,
    findings_before     TEXT,
    findings_after      TEXT,
    spans               TEXT,
    actor               TEXT NOT NULL,
    rationale           TEXT NOT NULL,
    approval_item_id    TEXT,
    supersedes_delta_id TEXT,
    session_id          TEXT,
    classification      TEXT DEFAULT 'CUI',
    created_at          TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE govchain_pending_operations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    operation_type TEXT,
    payload_hash TEXT,
    status TEXT,
    submitted_at TEXT,
    error_message TEXT
);
CREATE TABLE audit_trail (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT, event_type TEXT, actor TEXT, action TEXT,
    details TEXT, classification TEXT, created_at TEXT,
    hash TEXT, signature TEXT
);
"""

#: Every module whose ``get_connection`` must be redirected. Both namespaces,
#: deliberately — see the module docstring.
_ALIASES = (
    "tools.db.storage",
    "icdev.tools.db.storage",
    "tools.provenance.registry",
    "icdev.tools.provenance.registry",
    "tools.provenance.trust_validation",
    "icdev.tools.provenance.trust_validation",
    "tools.blockchain.chain_anchor",
    "icdev.tools.blockchain.chain_anchor",
)


def _import_all_aliases() -> list:
    """Import every alias so there is nothing left unpatched to fall through to."""
    import importlib

    loaded = []
    for name in _ALIASES:
        try:
            loaded.append(importlib.import_module(name))
        except Exception:  # pragma: no cover - an absent mirror is not a failure
            pass
    return loaded


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "trust_validation.db"
    raw = sqlite3.connect(path)
    raw.executescript(_REGISTRY_DDL)
    raw.commit()
    raw.close()

    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(path))

    modules = _import_all_aliases()

    def pinned(db_path=None):
        # Ignore whatever the caller asked for: this test may not touch the
        # live board under any code path, including one added later.
        return _real_get_connection(db_path=str(path))

    for module in modules:
        if getattr(module, "get_connection", None) is not None:
            monkeypatch.setattr(module, "get_connection", pinned, raising=False)

    return path


def _rows(path, sql="SELECT * FROM source_citation_registry", params=()):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def _count(path, citation_type=None):
    if citation_type is None:
        return len(_rows(path, "SELECT id FROM source_citation_registry"))
    return len(
        _rows(
            path,
            "SELECT id FROM source_citation_registry WHERE citation_type = ?",
            (citation_type,),
        )
    )


# ── The fixture's own premise ────────────────────────────────────────────────


def test_every_alias_is_pinned(db):
    """The redirect took on BOTH namespaces, so no write can reach the board.

    Without this the whole file is unfalsifiable: a test that silently wrote to
    the live database would pass every assertion below.
    """
    import importlib

    for name in _ALIASES:
        module = sys.modules.get(name)
        if module is None or getattr(module, "get_connection", None) is None:
            continue
        conn = module.get_connection()
        try:
            # Points at the throwaway file, not at data/icdev.db.
            assert conn.execute(
                "SELECT count(*) AS c FROM source_citation_registry"
            ).fetchone()["c"] == 0, f"{name}.get_connection is not pinned to the fixture"
        finally:
            conn.close()

    # And the two namespaces really are separate objects, which is why both
    # had to be named.
    a = importlib.import_module("tools.provenance.registry")
    b = importlib.import_module("icdev.tools.provenance.registry")
    assert a is not b, (
        "tools.provenance.registry and its icdev mirror resolved to one object — "
        "if that ever becomes true this test is over-specified, but the reverse "
        "assumption is what lets a fake go uninstalled"
    )


def test_the_constraint_can_actually_reject(db):
    """A CHECK that accepts anything would make every write test vacuous."""
    conn = sqlite3.connect(db)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO source_citation_registry "
            "(id, citation_type, source_table, source_record_id, source_hash) "
            "VALUES ('x', 'definitely_not_a_type', 't', 'r', 'h')"
        )
    conn.close()
    assert _count(db) == 0


# ── The vocabulary ───────────────────────────────────────────────────────────


def test_trust_validation_is_in_the_vocabulary():
    from tools.provenance import citation_types as ct

    assert ct.is_valid("trust_validation")


def test_register_citation_does_not_raise_for_it():
    """The precise cortex/asset_token failure: ValueError before the INSERT."""
    from tools.provenance.registry import register_citation

    try:
        register_citation(
            citation_type="trust_validation",
            source_table="trust_deltas",
            source_record_id="r",
            source_hash="h",
        )
    except ValueError as exc:  # pragma: no cover - this is the regression
        pytest.fail(f"register_citation rejected 'trust_validation': {exc}")
    except Exception:
        pass  # a DB error is a different failure mode; the check precedes it


# ── The leaf recipe ──────────────────────────────────────────────────────────


def test_leaf_is_sha256_of_the_four_fields_pipe_joined():
    """Recomputed independently here, so the module cannot define its own truth."""
    from tools.provenance.trust_validation import validation_leaf

    a, f, d = ("a" * 64, "b" * 64, "c" * 64)
    expected = hashlib.sha256(f"{a}|{f}|{d}|{APPROVER}".encode("utf-8")).hexdigest()
    assert validation_leaf(a, f, d, APPROVER) == expected


def test_leaf_changes_when_any_single_component_changes():
    from tools.provenance.trust_validation import validation_leaf

    base = validation_leaf("a" * 64, "b" * 64, "c" * 64, APPROVER)
    assert validation_leaf("d" * 64, "b" * 64, "c" * 64, APPROVER) != base
    assert validation_leaf("a" * 64, "d" * 64, "c" * 64, APPROVER) != base
    assert validation_leaf("a" * 64, "b" * 64, "d" * 64, APPROVER) != base
    assert validation_leaf("a" * 64, "b" * 64, "c" * 64, "someone.else") != base


def test_leaf_refuses_a_separator_in_the_approver():
    """Otherwise two different validations render one leaf — a chosen collision."""
    from tools.provenance.trust_validation import validation_leaf

    with pytest.raises(ValueError, match="separator"):
        validation_leaf("a" * 64, "b" * 64, "c" * 64, "alice|" + "d" * 64)


def test_leaf_refuses_a_non_hash_component():
    from tools.provenance.trust_validation import validation_leaf

    with pytest.raises(ValueError, match="64 lowercase hex"):
        validation_leaf("not-a-hash", "b" * 64, "c" * 64, APPROVER)


def test_leaf_refuses_an_empty_approver():
    from tools.provenance.trust_validation import validation_leaf

    with pytest.raises(ValueError, match="approver is required"):
        validation_leaf("a" * 64, "b" * 64, "c" * 64, "   ")


# ── The delta chain component ────────────────────────────────────────────────


def _insert_delta(path, delta_id, artifact_id, before, after, created_at):
    conn = sqlite3.connect(path)
    conn.execute(
        "INSERT INTO trust_deltas (delta_id, artifact_id, stage, before_hash, "
        "after_hash, actor, rationale, created_at) "
        "VALUES (?,?,'promote',?,?,'a','r',?)",
        (delta_id, artifact_id, before, after, created_at),
    )
    conn.commit()
    conn.close()


def test_no_deltas_folds_to_the_empty_hash(db):
    from tools.provenance.trust_validation import EMPTY_HASH, delta_chain_hash

    assert delta_chain_hash("session-with-no-history") == EMPTY_HASH


def test_a_delta_changes_the_chain_hash(db):
    from tools.provenance.trust_validation import EMPTY_HASH, delta_chain_hash

    _insert_delta(db, "d1", "sess-1", "a" * 64, "b" * 64, "2026-08-15T01:00:00Z")
    first = delta_chain_hash("sess-1")
    assert first != EMPTY_HASH

    _insert_delta(db, "d2", "sess-1", "b" * 64, "c" * 64, "2026-08-15T02:00:00Z")
    assert delta_chain_hash("sess-1") != first, "a second delta must move the fold"


def test_rewriting_a_delta_changes_the_chain_hash(db):
    """The point of the component: post-hoc edits to the evidence are detectable."""
    from tools.provenance.trust_validation import delta_chain_hash

    _insert_delta(db, "d1", "sess-2", "a" * 64, "b" * 64, "2026-08-15T01:00:00Z")
    before = delta_chain_hash("sess-2")

    conn = sqlite3.connect(db)
    conn.execute("UPDATE trust_deltas SET after_hash = ? WHERE delta_id = 'd1'", ("e" * 64,))
    conn.commit()
    conn.close()

    assert delta_chain_hash("sess-2") != before


def test_the_chain_hash_scopes_to_one_artifact(db):
    from tools.provenance.trust_validation import EMPTY_HASH, delta_chain_hash

    _insert_delta(db, "d1", "sess-3", "a" * 64, "b" * 64, "2026-08-15T01:00:00Z")
    assert delta_chain_hash("sess-3") != EMPTY_HASH
    assert delta_chain_hash("sess-4") == EMPTY_HASH


def test_an_unreadable_chain_is_not_an_empty_chain(db):
    """`unmeasured` must never hash the same as `there were none`."""
    from tools.provenance.trust_validation import DeltaChainUnavailable, delta_chain_hash

    conn = sqlite3.connect(db)
    conn.execute("DROP TABLE trust_deltas")
    conn.commit()
    conn.close()

    with pytest.raises(DeltaChainUnavailable):
        delta_chain_hash("sess-1")


# ── The write actually lands ─────────────────────────────────────────────────


def test_record_validation_changes_the_row_count(db):
    """The headline assertion: a row that was not there is there now."""
    from tools.provenance.trust_validation import record_validation

    assert _count(db, "trust_validation") == 0

    out = record_validation(
        artifact_id="sess-100",
        approver=APPROVER,
        artifact_text=ARTIFACT,
        findings=[{"item_number": 1, "issue": "missing_citations", "detail": "x"}],
    )

    assert _count(db, "trust_validation") == 1, (
        "register_citation reported an id but no row exists — the INSERT was "
        "rejected and swallowed"
    )
    assert out["registry_id"].startswith("scr-")


def test_the_persisted_row_carries_the_leaf_and_its_components(db):
    from tools.provenance.trust_validation import record_validation, sha256_text

    out = record_validation(
        artifact_id="sess-101", approver=APPROVER, artifact_text=ARTIFACT, findings=[]
    )
    row = _rows(db, "SELECT * FROM source_citation_registry WHERE id = ?", (out["registry_id"],))[0]

    assert row["citation_type"] == "trust_validation"
    assert row["source_record_id"] == "sess-101"
    assert row["source_hash"] == out["leaf"]

    components = json.loads(row["source_doc"])
    assert components["artifact_hash"] == sha256_text(ARTIFACT)
    assert components["approver"] == APPROVER
    # Recomputed here from the persisted components alone — this is exactly
    # what ChainAnchor does at anchor time.
    assert hashlib.sha256(
        "{artifact_hash}|{findings_hash}|{delta_chain_hash}|{approver}".format(**components).encode()
    ).hexdigest() == row["source_hash"]


def test_the_row_carries_no_artifact_text(db):
    """A registry row is read by subsystems not cleared for the artifact."""
    from tools.provenance.trust_validation import record_validation

    out = record_validation(
        artifact_id="sess-102",
        approver=APPROVER,
        artifact_text=ARTIFACT,
        findings=[{"issue": "missing_citations", "detail": "sentence three"}],
    )
    row = _rows(db, "SELECT * FROM source_citation_registry WHERE id = ?", (out["registry_id"],))[0]
    blob = json.dumps(row)
    assert "The control is implemented" not in blob
    assert "sentence three" not in blob


def test_the_delta_chain_is_folded_into_the_persisted_leaf(db):
    from tools.provenance.trust_validation import EMPTY_HASH, record_validation

    _insert_delta(db, "d9", "sess-103", "a" * 64, "b" * 64, "2026-08-15T01:00:00Z")
    out = record_validation(
        artifact_id="sess-103", approver=APPROVER, artifact_text=ARTIFACT, findings=[]
    )
    assert out["delta_chain_hash"] != EMPTY_HASH


def test_findings_hash_is_order_independent(db):
    """Guard order is a reporting decision, not a change to what was seen."""
    from tools.provenance.trust_validation import findings_hash

    a = {"issue": "missing_citations", "detail": "x"}
    b = {"issue": "placeholder", "detail": "y"}
    assert findings_hash([a, b]) == findings_hash([b, a])


def test_findings_hash_distinguishes_clean_from_three_defects(db):
    from tools.provenance.trust_validation import EMPTY_HASH, findings_hash

    assert findings_hash([]) == EMPTY_HASH
    assert findings_hash([{"issue": "a"}, {"issue": "b"}, {"issue": "c"}]) != EMPTY_HASH


def test_record_validation_raises_when_the_write_does_not_land(db):
    """An empty id must not read as a recorded validation.

    This is the entire cortex/asset_token bug, and the reason record_validation
    exists rather than a bare register_citation call at each site.
    """
    from tools.provenance.trust_validation import TrustValidationError, record_validation

    conn = sqlite3.connect(db)
    conn.execute("DROP TABLE source_citation_registry")
    conn.commit()
    conn.close()

    with pytest.raises(TrustValidationError, match="did not\n?\\s*land"):
        record_validation(
            artifact_id="sess-104", approver=APPROVER, artifact_text=ARTIFACT, findings=[]
        )


# ── Anchoring ────────────────────────────────────────────────────────────────


def _anchor(db):
    from tools.blockchain.chain_anchor import ChainAnchor

    return ChainAnchor(db_path=db)


def test_anchor_provenance_anchors_a_trust_validation_row(db):
    from tools.provenance.trust_validation import record_validation

    out = record_validation(
        artifact_id="sess-200", approver=APPROVER, artifact_text=ARTIFACT, findings=[]
    )
    result = _anchor(db).anchor_provenance([out["registry_id"]])

    assert result["status"] in ("anchored", "queued"), result
    assert not result["rejected"], result["rejected"]
    assert result["batch_size"] == 1

    row = _rows(db, "SELECT * FROM source_citation_registry WHERE id = ?", (out["registry_id"],))[0]
    assert row["merkle_root"], "the anchor did not back-fill merkle_root"


def test_anchor_provenance_refuses_a_tampered_leaf(db):
    """Anchoring a leaf nobody re-derived would wrap proof around a lie."""
    from tools.provenance.trust_validation import record_validation

    out = record_validation(
        artifact_id="sess-201", approver=APPROVER, artifact_text=ARTIFACT, findings=[]
    )
    conn = sqlite3.connect(db)
    conn.execute(
        "UPDATE source_citation_registry SET source_hash = ? WHERE id = ?",
        ("f" * 64, out["registry_id"]),
    )
    conn.commit()
    conn.close()

    result = _anchor(db).anchor_provenance([out["registry_id"]])

    assert result["status"] == "empty", "a tampered row must not be anchored"
    assert [r["registry_id"] for r in result["rejected"]] == [out["registry_id"]]
    row = _rows(db, "SELECT * FROM source_citation_registry WHERE id = ?", (out["registry_id"],))[0]
    assert not row["merkle_root"]


def test_a_tampered_component_is_refused_too(db):
    """Editing source_doc to match a different artifact must not verify."""
    from tools.provenance.trust_validation import record_validation

    out = record_validation(
        artifact_id="sess-202", approver=APPROVER, artifact_text=ARTIFACT, findings=[]
    )
    components = {
        "artifact_hash": "0" * 64,
        "findings_hash": "1" * 64,
        "delta_chain_hash": "2" * 64,
        "approver": APPROVER,
    }
    conn = sqlite3.connect(db)
    conn.execute(
        "UPDATE source_citation_registry SET source_doc = ? WHERE id = ?",
        (json.dumps(components), out["registry_id"]),
    )
    conn.commit()
    conn.close()

    result = _anchor(db).anchor_provenance([out["registry_id"]])
    assert result["status"] == "empty"
    assert result["rejected"][0]["reason"] == "stored leaf does not match its own components"


def test_a_row_with_no_components_is_refused(db):
    """Unverifiable is refused, never waved through as 'probably fine'."""
    from tools.provenance.trust_validation import record_validation

    out = record_validation(
        artifact_id="sess-203", approver=APPROVER, artifact_text=ARTIFACT, findings=[]
    )
    conn = sqlite3.connect(db)
    conn.execute(
        "UPDATE source_citation_registry SET source_doc = NULL WHERE id = ?",
        (out["registry_id"],),
    )
    conn.commit()
    conn.close()

    result = _anchor(db).anchor_provenance([out["registry_id"]])
    assert result["status"] == "empty"
    assert "components missing" in result["rejected"][0]["reason"]


def test_other_citation_types_still_anchor_opaquely(db):
    """The verify branch must not change behaviour for the other 13 types."""
    from tools.provenance.registry import register_citation

    reg_id = register_citation(
        citation_type="manual",
        source_table="t",
        source_record_id="r",
        source_hash="a" * 64,
    )
    assert reg_id, "precondition: the manual citation must have landed"

    result = _anchor(db).anchor_provenance([reg_id])
    assert result["status"] in ("anchored", "queued")
    assert not result["rejected"]


def test_a_tampered_row_does_not_sink_its_batch(db):
    """One refused leaf must not cost the valid rows beside it their anchor."""
    from tools.provenance.trust_validation import record_validation

    good = record_validation(
        artifact_id="sess-204", approver=APPROVER, artifact_text=ARTIFACT, findings=[]
    )
    bad = record_validation(
        artifact_id="sess-205", approver=APPROVER, artifact_text=ARTIFACT, findings=[]
    )
    conn = sqlite3.connect(db)
    conn.execute(
        "UPDATE source_citation_registry SET source_hash = ? WHERE id = ?",
        ("f" * 64, bad["registry_id"]),
    )
    conn.commit()
    conn.close()

    result = _anchor(db).anchor_provenance([good["registry_id"], bad["registry_id"]])

    assert result["status"] in ("anchored", "queued")
    assert result["batch_size"] == 1
    assert len(result["rejected"]) == 1
    rows = {r["id"]: r for r in _rows(db)}
    assert rows[good["registry_id"]]["merkle_root"]
    assert not rows[bad["registry_id"]]["merkle_root"]


def test_anchor_provenance_accepts_records_directly(db):
    """The literal ask: hand it TRUST validation records, not just registry ids."""
    record = {
        "artifact_hash": "a" * 64,
        "findings_hash": "b" * 64,
        "delta_chain_hash": "c" * 64,
        "approver": APPROVER,
    }
    result = _anchor(db).anchor_provenance([], trust_validations=[record])

    assert result["status"] in ("anchored", "queued")
    assert result["batch_size"] == 1


def test_a_supplied_record_missing_a_component_is_refused(db):
    result = _anchor(db).anchor_provenance(
        [], trust_validations=[{"artifact_hash": "a" * 64, "approver": APPROVER}]
    )
    assert result["status"] == "empty"
    assert result["rejected"]


def test_registry_rows_and_supplied_records_share_one_batch(db):
    """One root, one chain write — which is what lets this ride the 30-min reflex."""
    from tools.provenance.trust_validation import record_validation

    out = record_validation(
        artifact_id="sess-206", approver=APPROVER, artifact_text=ARTIFACT, findings=[]
    )
    record = {
        "artifact_hash": "a" * 64,
        "findings_hash": "b" * 64,
        "delta_chain_hash": "c" * 64,
        "approver": APPROVER,
    }
    result = _anchor(db).anchor_provenance([out["registry_id"]], trust_validations=[record])

    assert result["batch_size"] == 2
    assert result["status"] in ("anchored", "queued")


# ── It rides the existing reflex ─────────────────────────────────────────────


def test_periodic_anchor_sweeps_an_unanchored_validation(db):
    """No new schedule: the registry sweep that already runs finds these."""
    from tools.provenance.trust_validation import record_validation

    out = record_validation(
        artifact_id="sess-300", approver=APPROVER, artifact_text=ARTIFACT, findings=[]
    )
    summary = _anchor(db).periodic_anchor()

    assert summary["provenance_batches"] == 1, summary
    assert summary["trust_validations_rejected"] == 0
    row = _rows(db, "SELECT * FROM source_citation_registry WHERE id = ?", (out["registry_id"],))[0]
    assert row["merkle_root"], "the 30-minute reflex path did not anchor it"


def test_periodic_anchor_surfaces_a_refusal(db):
    """A refused validation must not vanish behind 'provenance_batches: 1'."""
    from tools.provenance.trust_validation import record_validation

    out = record_validation(
        artifact_id="sess-301", approver=APPROVER, artifact_text=ARTIFACT, findings=[]
    )
    conn = sqlite3.connect(db)
    conn.execute(
        "UPDATE source_citation_registry SET source_hash = ? WHERE id = ?",
        ("f" * 64, out["registry_id"]),
    )
    conn.commit()
    conn.close()

    assert _anchor(db).periodic_anchor()["trust_validations_rejected"] == 1


def test_no_second_reflex_was_added():
    """The card's constraint, asserted rather than trusted.

    A capability that ships its own dormant reflex is this platform's signature
    defect; this one lands on a wheel that is already turning.
    """
    import pathlib

    repo = pathlib.Path(__file__).resolve().parents[2]
    reflexes = {p.name for p in (repo / "tools" / "genesis" / "reflexes").glob("*.py")}
    for forbidden in ("trust_anchor.py", "trust_validation_anchor.py", "validation_anchor.py"):
        assert forbidden not in reflexes, (
            f"{forbidden} adds a second anchor reflex — trust_validation rows are "
            "swept by ChainAnchor.periodic_anchor via the existing govchain_anchor"
        )

    anchor_reflex = (repo / "tools" / "genesis" / "reflexes" / "govchain_anchor.py").read_text(
        encoding="utf-8"
    )
    assert "trust_validations_rejected" in anchor_reflex, (
        "the existing reflex must surface refused validations"
    )
