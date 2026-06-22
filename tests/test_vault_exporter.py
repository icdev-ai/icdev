"""Tests for the Tolaria-inspired vault exporter (adapt-tol-03)."""
import json
import zipfile
import tempfile
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def test_module_importable():
    from tools.knowledge.vault_exporter import export_vault, import_vault
    assert callable(export_vault)
    assert callable(import_vault)


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def test_export_creates_zip():
    from tools.knowledge.vault_exporter import export_vault
    with tempfile.TemporaryDirectory() as tmp:
        zip_path = export_vault(Path(tmp))
        assert zip_path.exists()
        assert zip_path.suffix == ".zip"


def test_export_zip_contains_manifest():
    from tools.knowledge.vault_exporter import export_vault
    with tempfile.TemporaryDirectory() as tmp:
        zip_path = export_vault(Path(tmp))
        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
        manifest_entries = [n for n in names if n.endswith("manifest.json")]
        assert len(manifest_entries) == 1, "manifest.json must be present in vault"


def test_export_manifest_is_valid_json():
    from tools.knowledge.vault_exporter import export_vault
    with tempfile.TemporaryDirectory() as tmp:
        zip_path = export_vault(Path(tmp))
        with zipfile.ZipFile(zip_path) as zf:
            manifest_name = [n for n in zf.namelist() if n.endswith("manifest.json")][0]
            manifest = json.loads(zf.read(manifest_name))
        assert "exported_at" in manifest
        assert "vault_name" in manifest
        assert "files" in manifest
        assert isinstance(manifest["files"], list)


def test_export_manifest_has_classification():
    from tools.knowledge.vault_exporter import export_vault
    with tempfile.TemporaryDirectory() as tmp:
        zip_path = export_vault(Path(tmp))
        with zipfile.ZipFile(zip_path) as zf:
            manifest_name = [n for n in zf.namelist() if n.endswith("manifest.json")][0]
            manifest = json.loads(zf.read(manifest_name))
        assert manifest.get("classification") == "CUI"


def test_export_contains_context_files():
    from tools.knowledge.vault_exporter import export_vault
    with tempfile.TemporaryDirectory() as tmp:
        zip_path = export_vault(Path(tmp))
        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
        context_entries = [n for n in names if "/context/" in n]
        assert len(context_entries) > 0, "vault must include context/ files"


def test_export_contains_hardprompts_files():
    from tools.knowledge.vault_exporter import export_vault
    with tempfile.TemporaryDirectory() as tmp:
        zip_path = export_vault(Path(tmp))
        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
        hp_entries = [n for n in names if "/hardprompts/" in n]
        assert len(hp_entries) > 0, "vault must include hardprompts/ files"


def test_export_contains_memory_jsonl():
    from tools.knowledge.vault_exporter import export_vault
    with tempfile.TemporaryDirectory() as tmp:
        zip_path = export_vault(Path(tmp))
        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
        assert any("memory_entries.jsonl" in n for n in names)


def test_export_label_in_filename():
    from tools.knowledge.vault_exporter import export_vault
    with tempfile.TemporaryDirectory() as tmp:
        zip_path = export_vault(Path(tmp), label="test")
        assert "test" in zip_path.name


def test_export_idempotent():
    from tools.knowledge.vault_exporter import export_vault
    with tempfile.TemporaryDirectory() as tmp:
        zip_path_1 = export_vault(Path(tmp), label="run1")
        zip_path_2 = export_vault(Path(tmp), label="run2")
        assert zip_path_1.exists()
        assert zip_path_2.exists()
        assert zip_path_1 != zip_path_2


# ---------------------------------------------------------------------------
# Import (dry-run, no DB required)
# ---------------------------------------------------------------------------

def test_import_dry_run_reports_files():
    from tools.knowledge.vault_exporter import export_vault, import_vault
    with tempfile.TemporaryDirectory() as tmp:
        zip_path = export_vault(Path(tmp))
        result = import_vault(zip_path, dry_run=True)
    assert "files_restored" in result
    assert result["files_restored"] > 0


def test_import_missing_file_returns_error():
    from tools.knowledge.vault_exporter import import_vault
    result = import_vault(Path("/nonexistent/vault.zip"), dry_run=True)
    assert "error" in result


def test_import_invalid_zip_returns_error():
    from tools.knowledge.vault_exporter import import_vault
    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as f:
        f.write(b"not a zip")
        tmp_path = f.name
    try:
        result = import_vault(Path(tmp_path), dry_run=True)
        assert "error" in result or result.get("errors")
    finally:
        os.unlink(tmp_path)


# ---------------------------------------------------------------------------
# Round-trip: file count preserved
# ---------------------------------------------------------------------------

def test_roundtrip_file_count_matches():
    """Files in manifest == files_restored in dry_run import."""
    from tools.knowledge.vault_exporter import export_vault, import_vault
    with tempfile.TemporaryDirectory() as tmp:
        zip_path = export_vault(Path(tmp))

        with zipfile.ZipFile(zip_path) as zf:
            manifest_name = [n for n in zf.namelist() if n.endswith("manifest.json")][0]
            manifest = json.loads(zf.read(manifest_name))
        exported_count = len(manifest["files"])

        result = import_vault(zip_path, dry_run=True)
        assert result["files_restored"] == exported_count
