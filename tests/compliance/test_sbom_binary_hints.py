#!/usr/bin/env python3
# CUI // SP-CTI
"""xrv-bin-03 — a string in a binary becomes a HINTED component, and says so.

``sbom_generator`` has only ever discovered components from DECLARED manifests.
This is the consumer side of ``binary_triage`` (xrv-bin-01): the artifact is
opened, what it appears to contain becomes components, and every one of them
carries ``evidence: binary_strings`` and ``confidence: unasserted``.

Three things are asserted here that a happy-path test would miss, and each one
is a way this feature could ship looking correct and be wrong:

* **The version is the observed text, not the seam's token.**
  ``binary_triage._VERSION_TOKEN`` ends at a word boundary, so its hint for
  ``OpenSSL 1.1.1k`` is **1.1** — a WRONG version rather than a vague one. The
  planted fixture string is exactly that shape, and the assertion is on
  ``1.1.1k``.
* **A manifest-declared component is byte-identical with and without the
  feature.** The property hook runs in the shared per-component loop, so the
  control arm is what proves it is additive.
* **An unreadable artifact is ``unmeasurable``, never ``no_hints``.** Those are
  the two answers this module exists to keep apart.
"""

import json
import sqlite3
import struct
import sys
from pathlib import Path

import pytest

from tests._schema_compat import ensure_tables

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.compliance import binary_components as bc  # noqa: E402
from tools.compliance.sbom_generator import generate_sbom  # noqa: E402

# The string the card names, planted verbatim.
PLANTED_OPENSSL = b"OpenSSL 1.1.1k  25 Mar 2021"


def _elf64(payload: bytes) -> bytes:
    """A minimal well-formed ELF64 header followed by *payload*.

    Generated rather than committed: a checked-in binary fixture is a blob no
    reviewer can read, and this repo would have to make a sandbox-coverage
    decision about a compiled artifact in its own tree.
    """
    header = bytearray(64)
    header[0:4] = b"\x7fELF"
    header[4] = 2  # 64-bit
    header[5] = 1  # little endian
    header[6] = 1  # version
    struct.pack_into("<HH", header, 16, 2, 0x3E)  # ET_EXEC, x86-64
    return bytes(header) + b"\x00" * 64 + payload


@pytest.fixture()
def fixture_binary(tmp_path):
    payload = PLANTED_OPENSSL + b"\x00zlib 1.2.11\x00libcurl/7.68.0\x00"
    path = tmp_path / "fixture.bin"
    path.write_bytes(_elf64(payload))
    return path


# ---------------------------------------------------------------------------
# The library
# ---------------------------------------------------------------------------


def test_planted_openssl_string_yields_a_hinted_component(fixture_binary):
    result = bc.hinted_components(str(fixture_binary))

    assert result["status"] == bc.STATUS_OK
    by_name = {c["declared_name"]: c for c in result["components"]}
    assert "OpenSSL" in by_name, by_name

    component = by_name["OpenSSL"]
    hint = component["binary_hint"]
    assert hint["evidence"] == bc.EVIDENCE_BINARY_STRINGS
    assert hint["confidence"] == bc.CONFIDENCE_UNASSERTED
    # The evidence is carried, not thrown away: the reader's next question is
    # always "what did it actually say".
    assert PLANTED_OPENSSL.decode() in hint["evidence_string"]


def test_the_version_is_the_observed_text_and_not_the_seams_truncated_token(fixture_binary):
    """`1.1` would be a wrong version, not a vague one — a CVE lookup on it
    answers about a release this artifact does not contain."""
    component = {
        c["declared_name"]: c for c in bc.hinted_components(str(fixture_binary))["components"]
    }["OpenSSL"]

    assert component["version"] == "1.1.1k"
    # And BOTH values are carried, so the extension is auditable rather than
    # something a reader has to take on trust.
    assert component["binary_hint"]["version_token"] == "1.1"
    assert component["binary_hint"]["version_basis"] == bc.VERSION_BASIS_EXTENDED


def test_a_hint_with_no_name_beside_it_is_skipped_by_name(tmp_path):
    """Binaries are full of version-shaped numbers. A component named after
    nothing is noise that buries the real entries — but a SILENT drop is the
    same defect one layer down, so the skip is reported with its reason."""
    path = tmp_path / "nameless.bin"
    path.write_bytes(_elf64(b"---------- 9.8.7 ----------\x00"))

    result = bc.hinted_components(str(path))

    assert result["components"] == []
    assert result["status"] == bc.STATUS_NO_HINTS
    reasons = {s["reason"] for s in result["skipped"]}
    assert bc.SKIP_NO_NAME in reasons
    assert result["skipped_count"] == len(result["skipped"]) >= 1


def test_a_version_label_is_not_the_component_name(tmp_path):
    """`OpenSSL version 1.1.1k` must not produce a component called `version`."""
    path = tmp_path / "label.bin"
    path.write_bytes(_elf64(b"OpenSSL version 1.1.1k ready\x00"))

    names = {c["declared_name"] for c in bc.hinted_components(str(path))["components"]}
    assert names == {"OpenSSL"}


def test_a_v_prefixed_version_is_invisible_to_the_seam_and_is_not_invented_here(tmp_path):
    r"""MEASURED, and it belongs to `binary_triage`, not to this module.

    Its `_VERSION_TOKEN` opens with `\b`, and there is no word boundary between
    the `v` and the `1` of `v1.1.1k` — so the seam returns NO hint for that very
    common spelling. This module reports the measured zero rather than running
    its own regex over the strings: a second version scanner here would make
    the SBOM and `binary_triage --json` disagree about what the same artifact
    says. Widening the token is that module's card, and this test is what will
    fail on the day it happens.
    """
    path = tmp_path / "vprefix.bin"
    path.write_bytes(_elf64(b"OpenSSL v1.1.1k build\x00"))

    result = bc.hinted_components(str(path))

    assert result["hints_seen"] == 0
    assert result["status"] == bc.STATUS_NO_HINTS
    assert result["components"] == []


def test_an_unreadable_artifact_is_unmeasurable_and_never_no_hints(tmp_path):
    result = bc.hinted_components(str(tmp_path / "does-not-exist.bin"))

    assert result["status"] == bc.STATUS_UNMEASURABLE
    assert result["status"] != bc.STATUS_NO_HINTS
    assert result["reason"]
    # `hints_seen` is None — never 0 — because nothing was looked at.
    assert result["hints_seen"] is None


def test_a_file_that_was_read_and_carries_no_version_is_a_measured_zero(tmp_path):
    path = tmp_path / "quiet.bin"
    path.write_bytes(_elf64(b"no versions in here at all\x00"))

    result = bc.hinted_components(str(path))

    assert result["status"] == bc.STATUS_NO_HINTS
    assert result["hints_seen"] == 0
    assert result["components"] == []


def test_the_component_budget_is_reported_rather_than_silently_applied(fixture_binary):
    result = bc.hinted_components(str(fixture_binary), max_components=1)

    assert result["component_count"] == 1
    over = [s for s in result["skipped"] if s["reason"] == bc.SKIP_OVER_BUDGET]
    assert over, result["skipped"]
    # Deferred entries are named, not merely counted.
    assert all(s.get("name") for s in over)


def test_binary_hint_properties_is_empty_for_a_manifest_declared_component():
    """The hook runs in the generator's shared per-component loop. If this were
    non-empty for an ordinary component, every SBOM in the tree would change."""
    declared = {"type": "library", "name": "flask", "version": "3.0.0", "purl": "pkg:pypi/flask"}

    assert bc.binary_hint_properties(declared) == []
    assert bc.is_hinted_component(declared) is False


def test_a_hinted_component_carries_no_invented_purl(fixture_binary):
    """A purl asserts an ecosystem and a namespace; a string in a binary names
    neither. `pkg:generic/openssl@1.1.1k` would be looked up by a downstream
    scanner as though somebody had declared it."""
    for component in bc.hinted_components(str(fixture_binary))["components"]:
        assert not component.get("purl")


def test_binary_triage_is_the_one_reader():
    """No private `open(..., "rb")` here: `binary_triage` carries the
    sandbox-coverage posture (Gap 71) that a second reader would step around."""
    import ast

    source = Path(bc.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    opens = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "open"
    ]
    assert opens == []
    assert "read_bytes" not in source


# ---------------------------------------------------------------------------
# End to end, through the real generator and the real storage layer
# ---------------------------------------------------------------------------

# Declared as separate statements and applied through `ensure_table`, not as one
# `executescript`: `CREATE TABLE IF NOT EXISTS` NO-OPS against a table another
# module in the same pytest process created first with a narrower shape, and the
# INSERT below then raises on a column this file just declared. It passes alone
# and fails on one bin-packed shard.
_MINIMAL_SCHEMA = [
    """CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    type TEXT NOT NULL,
    classification TEXT NOT NULL DEFAULT 'CUI',
    status TEXT NOT NULL DEFAULT 'active',
    directory_path TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)""",
    """CREATE TABLE IF NOT EXISTS sbom_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    version TEXT NOT NULL,
    format TEXT NOT NULL DEFAULT 'cyclonedx',
    file_path TEXT NOT NULL,
    component_count INTEGER,
    vulnerability_count INTEGER,
    generated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    classification TEXT NOT NULL DEFAULT 'CUI',
    tenant_id TEXT,
    sbom_author TEXT,
    author_signature TEXT,
    signature_algorithm TEXT,
    data_format_name TEXT,
    data_format_version TEXT,
    generation_context TEXT,
    tool_name TEXT,
    tool_version TEXT,
    sbom_version TEXT,
    serial_number TEXT,
    supersedes_sbom_id INTEGER REFERENCES sbom_records(id),
    content_digest TEXT,
    source_revision TEXT,
    revision_reason TEXT
)""",
    """CREATE TABLE IF NOT EXISTS sbom_components (
    id              TEXT    PRIMARY KEY,
    component_name  TEXT    NOT NULL,
    version         TEXT,
    vendor          TEXT,
    component_type  TEXT,
    purl            TEXT,
    license         TEXT,
    classification  TEXT    NOT NULL DEFAULT 'CUI',
    created_at      TEXT    DEFAULT (datetime('now')),
    updated_at      TEXT    DEFAULT (datetime('now')),
    producer             TEXT,
    hash_value           TEXT,
    hash_algorithm       TEXT,
    identifiers_json     TEXT NOT NULL DEFAULT '{}',
    unknown_fields_json  TEXT NOT NULL DEFAULT '{}',
    withheld_fields_json TEXT NOT NULL DEFAULT '{}',
    tenant_id            TEXT
)""",
    """CREATE TABLE IF NOT EXISTS audit_trail (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT,
    event_type TEXT NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    details TEXT,
    affected_files TEXT,
    classification TEXT DEFAULT 'CUI',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)""",
]


def _seed(tmp_path, project_id):
    project_dir = tmp_path / "project"
    project_dir.mkdir(exist_ok=True)
    (project_dir / "requirements.txt").write_text("flask==3.0.0\n", encoding="utf-8")

    db_path = tmp_path / "icdev.db"
    conn = sqlite3.connect(db_path)
    ensure_tables(conn, _MINIMAL_SCHEMA)
    conn.execute(
        "INSERT OR REPLACE INTO projects (id, name, type, directory_path) VALUES (?, ?, ?, ?)",
        (project_id, "target", "cli", str(project_dir)),
    )
    conn.commit()
    conn.close()
    return db_path


def _generate(tmp_path, monkeypatch, name, **kwargs):
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    db_path = _seed(tmp_path / name, f"proj-{name}")
    out = tmp_path / name / "sbom.cdx.json"
    generate_sbom(f"proj-{name}", output_path=str(out), db_path=db_path, **kwargs)
    return json.loads(out.read_text(encoding="utf-8"))


@pytest.fixture()
def two_projects(tmp_path):
    (tmp_path / "with").mkdir()
    (tmp_path / "without").mkdir()
    return tmp_path


def test_generate_sbom_with_binary_emits_the_hinted_component(
    two_projects, monkeypatch, fixture_binary
):
    document = _generate(
        two_projects, monkeypatch, "with", binary_paths=[str(fixture_binary)]
    )

    hinted = [
        component
        for component in document["components"]
        if any(
            prop["name"] == bc.PROPERTY_EVIDENCE and prop["value"] == bc.EVIDENCE_BINARY_STRINGS
            for prop in component.get("properties", [])
        )
    ]
    names = {component["name"]: component for component in hinted}
    assert "openssl" in names, sorted(names)
    assert names["openssl"]["version"] == "1.1.1k"

    properties = {prop["name"]: prop["value"] for prop in names["openssl"]["properties"]}
    assert properties[bc.PROPERTY_CONFIDENCE] == bc.CONFIDENCE_UNASSERTED
    assert PLANTED_OPENSSL.decode() in properties[bc.PROPERTY_STRING]
    assert properties[bc.PROPERTY_SOURCE_SHA256_SCOPE] == "whole_file"


def test_a_manifest_declared_component_is_unchanged_by_the_hook(
    two_projects, monkeypatch, fixture_binary
):
    """The control arm. The property hook runs in the shared per-component loop,
    so `flask` is what proves the feature is additive rather than a rewrite."""
    with_binary = _generate(
        two_projects, monkeypatch, "with", binary_paths=[str(fixture_binary)]
    )
    without_binary = _generate(two_projects, monkeypatch, "without")

    def flask(document):
        entries = [c for c in document["components"] if c["name"] == "flask"]
        assert len(entries) == 1, entries
        return entries[0]

    left, right = flask(with_binary), flask(without_binary)
    # bom-ref is minted from the component set, so it is allowed to differ;
    # everything the recipient reads about flask itself must not.
    left.pop("bom-ref", None)
    right.pop("bom-ref", None)
    assert left == right

    # And no hint property reached it.
    assert not [p for p in left.get("properties", []) if p["name"].startswith(bc.PROPERTY_PREFIX)]


def test_generating_without_binary_paths_is_unchanged(two_projects, monkeypatch):
    """A deployment that never passes --binary must get the document it always
    got: the hook is `[]` for every component and the loop is untouched."""
    document = _generate(two_projects, monkeypatch, "without")

    for component in document["components"]:
        assert not [
            prop
            for prop in component.get("properties", [])
            if prop["name"].startswith(bc.PROPERTY_PREFIX)
        ]
