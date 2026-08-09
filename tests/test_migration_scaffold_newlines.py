# CUI // SP-CTI
"""The migration scaffolder must emit LF, on every platform.

`MigrationRunner.create_migration` wrote its three scaffold files with
`Path.write_text(..., encoding="utf-8")` and no `newline`, so on Windows Python
translated every "\\n" to "\\r\\n". The repo is LF, so every scaffolded migration
arrived with CRLF endings and git reported the whole file as changed the first
time anything touched it.

Caught on 2026-08-08 while scaffolding `20260808195754_resync_hook_events_id_
sequence`: `git add` warned "CRLF will be replaced by LF" on the generated
meta.json. Same failure mode as the one fixed in tools/genesis/rubric_build_tools
.py the same day — a text write with no `newline` argument.

Cannot reproduce on Linux, which is where CI runs, so only an explicit byte-level
assertion catches it.
"""
import json

import pytest

from tools.db.migration_runner import MigrationRunner


@pytest.fixture
def scaffolded(tmp_path):
    runner = MigrationRunner.__new__(MigrationRunner)
    runner.migrations_dir = tmp_path / "migrations"
    created = runner.create_migration("crlf probe migration")
    from pathlib import Path

    return Path(created)


def test_scaffold_creates_the_three_expected_files(scaffolded):
    names = sorted(p.name for p in scaffolded.iterdir())
    assert names == ["down.sql", "meta.json", "up.sql"]


@pytest.mark.parametrize("filename", ["up.sql", "down.sql", "meta.json"])
def test_scaffold_files_contain_no_crlf(scaffolded, filename):
    """Asserted on RAW BYTES — read_text() would translate the evidence away."""
    data = (scaffolded / filename).read_bytes()
    assert b"\r\n" not in data, (
        f"{filename} was scaffolded with CRLF; the repo is LF, so the first edit "
        "reports the whole file as changed"
    )
    assert b"\r" not in data, f"{filename} contains a bare CR"


def test_meta_json_is_still_valid_json(scaffolded):
    meta = json.loads((scaffolded / "meta.json").read_text(encoding="utf-8"))
    assert meta["description"] == "crlf probe migration"
    assert meta["reversible"] is True


def test_sql_scaffolds_keep_their_cui_marking(scaffolded):
    for name in ("up.sql", "down.sql"):
        text = (scaffolded / name).read_text(encoding="utf-8")
        assert "CUI // SP-CTI" in text
        assert text.endswith("\n"), f"{name} should end with a newline"
