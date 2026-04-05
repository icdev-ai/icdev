#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for Blueprint Digest Verifier — NemoClaw-adapted supply chain integrity (D-NC-3)."""

import pytest

from tools.security.blueprint_verifier import BlueprintVerifier, ENTITY_TYPES


@pytest.fixture
def verifier_db(tmp_path):
    return tmp_path / "test_blueprint.db"


@pytest.fixture
def verifier(verifier_db):
    return BlueprintVerifier(db_path=verifier_db)


@pytest.fixture
def sample_dir(tmp_path):
    """Create a sample directory tree for digest testing."""
    d = tmp_path / "sample_project"
    d.mkdir()
    (d / "main.py").write_text("print('hello')\n", encoding="utf-8")
    (d / "utils.py").write_text("def add(a, b): return a + b\n", encoding="utf-8")
    sub = d / "lib"
    sub.mkdir()
    (sub / "helper.py").write_text("# helper module\n", encoding="utf-8")
    return d


@pytest.fixture
def empty_dir(tmp_path):
    d = tmp_path / "empty_project"
    d.mkdir()
    return d


class TestComputeDigest:
    def test_compute_returns_digest(self, verifier, sample_dir):
        result = verifier.compute_digest(sample_dir)
        assert "digest" in result
        assert len(result["digest"]) == 64  # SHA-256 hex
        assert result["file_count"] == 3
        assert result["total_bytes"] > 0

    def test_compute_deterministic(self, verifier, sample_dir):
        r1 = verifier.compute_digest(sample_dir)
        r2 = verifier.compute_digest(sample_dir)
        assert r1["digest"] == r2["digest"]

    def test_compute_empty_dir(self, verifier, empty_dir):
        result = verifier.compute_digest(empty_dir)
        assert result["file_count"] == 0
        assert result["total_bytes"] == 0

    def test_compute_nonexistent_dir(self, verifier, tmp_path):
        result = verifier.compute_digest(tmp_path / "nonexistent")
        assert "error" in result

    def test_file_hashes_order_is_deterministic(self, verifier, sample_dir):
        r1 = verifier.compute_digest(sample_dir)
        r2 = verifier.compute_digest(sample_dir)
        paths1 = [fh["path"] for fh in r1["file_hashes"]]
        paths2 = [fh["path"] for fh in r2["file_hashes"]]
        assert paths1 == paths2

    def test_file_hashes_use_forward_slashes(self, verifier, sample_dir):
        result = verifier.compute_digest(sample_dir)
        for fh in result["file_hashes"]:
            assert "\\" not in fh["path"]

    def test_skips_pycache(self, verifier, sample_dir):
        cache = sample_dir / "__pycache__"
        cache.mkdir()
        (cache / "main.cpython-312.pyc").write_bytes(b"\x00" * 100)
        result = verifier.compute_digest(sample_dir)
        paths = [fh["path"] for fh in result["file_hashes"]]
        assert all("__pycache__" not in p for p in paths)
        assert result["file_count"] == 3  # Only the 3 original files


class TestVerifyDigest:
    def test_verify_passes_on_match(self, verifier, sample_dir):
        computed = verifier.compute_digest(sample_dir)
        result = verifier.verify_digest(sample_dir, computed["digest"])
        assert result["verified"] is True

    def test_verify_fails_on_mismatch(self, verifier, sample_dir):
        result = verifier.verify_digest(sample_dir, "0" * 64)
        assert result["verified"] is False
        assert result["expected"] == "0" * 64

    def test_verify_detects_file_modification(self, verifier, sample_dir):
        computed = verifier.compute_digest(sample_dir)
        original_digest = computed["digest"]
        # Modify a file
        (sample_dir / "main.py").write_text("print('modified')\n", encoding="utf-8")
        result = verifier.verify_digest(sample_dir, original_digest)
        assert result["verified"] is False

    def test_verify_detects_file_addition(self, verifier, sample_dir):
        computed = verifier.compute_digest(sample_dir)
        original_digest = computed["digest"]
        (sample_dir / "new_file.py").write_text("# new\n", encoding="utf-8")
        result = verifier.verify_digest(sample_dir, original_digest)
        assert result["verified"] is False

    def test_verify_detects_file_deletion(self, verifier, sample_dir):
        computed = verifier.compute_digest(sample_dir)
        original_digest = computed["digest"]
        (sample_dir / "utils.py").unlink()
        result = verifier.verify_digest(sample_dir, original_digest)
        assert result["verified"] is False


class TestStoreAndLookup:
    def test_store_and_lookup(self, verifier, sample_dir):
        store_result = verifier.store_digest("genome", "v1.0.0", sample_dir)
        assert store_result["stored"] is True
        assert store_result["entity_type"] == "genome"

        lookup = verifier.lookup_digest("genome", "v1.0.0")
        assert lookup["found"] is True
        assert lookup["digest"] == store_result["digest"]

    def test_lookup_not_found(self, verifier):
        result = verifier.lookup_digest("genome", "nonexistent")
        assert result["found"] is False

    def test_store_invalid_entity_type(self, verifier, sample_dir):
        result = verifier.store_digest("invalid_type", "v1.0.0", sample_dir)
        assert "error" in result

    def test_all_entity_types_valid(self, verifier, sample_dir):
        for etype in ENTITY_TYPES:
            result = verifier.store_digest(etype, f"test-{etype}", sample_dir)
            assert result["stored"] is True


class TestVerifyAndRecord:
    def test_verify_entity_passes(self, verifier, sample_dir):
        verifier.store_digest("genome", "v1.0.0", sample_dir)
        result = verifier.verify_and_record("genome", "v1.0.0", sample_dir)
        assert result["verified"] is True

    def test_verify_entity_fails_after_tamper(self, verifier, sample_dir):
        verifier.store_digest("genome", "v1.0.0", sample_dir)
        (sample_dir / "main.py").write_text("print('tampered')\n", encoding="utf-8")
        result = verifier.verify_and_record("genome", "v1.0.0", sample_dir)
        assert result["verified"] is False

    def test_verify_entity_no_stored_digest(self, verifier, sample_dir):
        result = verifier.verify_and_record("genome", "nonexistent", sample_dir)
        assert "error" in result
        assert result["error"] == "no_stored_digest"


class TestHistory:
    def test_history_returns_records(self, verifier, sample_dir):
        verifier.store_digest("genome", "v1.0.0", sample_dir)
        verifier.store_digest("genome", "v2.0.0", sample_dir)
        history = verifier.get_history(entity_type="genome")
        assert len(history) == 2

    def test_history_limit(self, verifier, sample_dir):
        for i in range(10):
            verifier.store_digest("capability", f"cap-{i}", sample_dir)
        history = verifier.get_history(entity_type="capability", limit=5)
        assert len(history) == 5

    def test_history_all_types(self, verifier, sample_dir):
        verifier.store_digest("genome", "g1", sample_dir)
        verifier.store_digest("child_app", "c1", sample_dir)
        history = verifier.get_history()
        assert len(history) == 2
