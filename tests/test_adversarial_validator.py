#!/usr/bin/env python3
# CUI // SP-CTI
"""Unit tests for the adversarial data validation pipeline (RED phase).

Maps to NIST 800-53 controls:
- SA-11: Developer Security Testing and Evaluation
- SI-10: Information Input Validation
- AU-9: Audit Log Management (append-only audit)
"""

import json
import os
import sys
import tempfile
from pathlib import Path


import importlib.util

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

# Load adversarial_validator directly to avoid icdev/tools/__init__.py circular imports
_validator_path = BASE_DIR / "icdev" / "tools" / "strategos" / "adversarial_validator.py"
if not _validator_path.exists():
    _validator_path = BASE_DIR / "tools" / "strategos" / "adversarial_validator.py"
_spec = importlib.util.spec_from_file_location("adversarial_validator", _validator_path)
_av = importlib.util.module_from_spec(_spec)
sys.modules["adversarial_validator"] = _av
_spec.loader.exec_module(_av)

AdversarialValidator = _av.AdversarialValidator
BiasDetector = _av.BiasDetector
DeepfakeDetector = _av.DeepfakeDetector
ManipulationDetector = _av.ManipulationDetector
ValidationResult = _av.ValidationResult
validate_signals = _av.validate_signals
main = _av.main


# ---------------------------------------------------------------------------
# BiasDetector tests
# ---------------------------------------------------------------------------
class TestBiasDetector:
    def test_detects_sentiment_skew(self):
        detector = BiasDetector()
        text = "All members of Group X are criminals and should be deported."
        result = detector.detect(text)
        assert result["bias_detected"] is True
        assert "sentiment_skew" in result["categories"]

    def test_detects_demographic_bias(self):
        detector = BiasDetector()
        text = "Women are inherently less capable leaders than men."
        result = detector.detect(text)
        assert result["bias_detected"] is True
        assert "demographic_bias" in result["categories"]

    def test_no_bias_in_neutral_text(self):
        detector = BiasDetector()
        text = "The weather today is sunny with a high of 75 degrees."
        result = detector.detect(text)
        assert result["bias_detected"] is False
        assert result["categories"] == []

    def test_bias_score_between_zero_and_one(self):
        detector = BiasDetector()
        result = detector.detect("Some controversial statement here.")
        assert 0.0 <= result["bias_score"] <= 1.0


# ---------------------------------------------------------------------------
# DeepfakeDetector tests
# ---------------------------------------------------------------------------
class TestDeepfakeDetector:
    def test_detects_metadata_inconsistency(self):
        detector = DeepfakeDetector()
        image_meta = {
            "filename": "sat_scene_01.jpg",
            "exif_make": "Canon",
            "exif_software": "Adobe Photoshop 2024",
            "file_size_bytes": 1024,
        }
        result = detector.detect(image_meta)
        assert result["deepfake_detected"] is True
        assert "metadata_inconsistency" in result["indicators"]

    def test_detects_compression_artifacts(self):
        detector = DeepfakeDetector()
        image_meta = {
            "filename": "drone_feed.mp4",
            "codec": "H.264",
            "bitrate_kbps": 50,
            "resolution": "4K",
        }
        result = detector.detect(image_meta)
        assert result["deepfake_detected"] is True
        assert "compression_anomaly" in result["indicators"]

    def test_passes_clean_metadata(self):
        detector = DeepfakeDetector()
        image_meta = {
            "filename": "sentinel2_l2a.tif",
            "exif_make": "Copernicus",
            "exif_software": "SNAP",
            "file_size_bytes": 150_000_000,
            "resolution_meters": 10,
        }
        result = detector.detect(image_meta)
        assert result["deepfake_detected"] is False
        assert result["indicators"] == []


# ---------------------------------------------------------------------------
# ManipulationDetector tests
# ---------------------------------------------------------------------------
class TestManipulationDetector:
    def test_detects_homoglyph_attack(self):
        detector = ManipulationDetector()
        text = "Visit our official site: goоgle.com"  # Cyrillic 'о'
        result = detector.detect(text)
        assert result["manipulation_detected"] is True
        assert "homoglyphs" in result["indicators"]

    def test_detects_invisible_characters(self):
        detector = ManipulationDetector()
        text = "Hello​World"  # zero-width space
        result = detector.detect(text)
        assert result["manipulation_detected"] is True
        assert "invisible_characters" in result["indicators"]

    def test_detects_zero_width_joiner(self):
        detector = ManipulationDetector()
        text = "test‍text"  # zero-width joiner
        result = detector.detect(text)
        assert result["manipulation_detected"] is True

    def test_no_manipulation_in_clean_text(self):
        detector = ManipulationDetector()
        text = "Standard English text without tricks."
        result = detector.detect(text)
        assert result["manipulation_detected"] is False


# ---------------------------------------------------------------------------
# AdversarialValidator integration tests
# ---------------------------------------------------------------------------
class TestAdversarialValidator:
    def test_validate_text_signal_flags_bias(self):
        validator = AdversarialValidator()
        signal = {
            "id": "sig-1",
            "source_type": "social_media",
            "content": "All politicians from Party Y are corrupt thieves.",
        }
        result = validator.validate(signal)
        assert result.flagged is True
        assert "bias" in result.issues

    def test_validate_image_signal_flags_deepfake(self):
        validator = AdversarialValidator()
        signal = {
            "id": "sig-2",
            "source_type": "satellite_imagery",
            "metadata": {
                "filename": "fake.tif",
                "exif_make": "Unknown",
                "exif_software": "GIMP",
                "file_size_bytes": 5000,
            },
        }
        result = validator.validate(signal)
        assert result.flagged is True
        assert "deepfake" in result.issues

    def test_validate_manipulated_text_signal(self):
        validator = AdversarialValidator()
        signal = {
            "id": "sig-3",
            "source_type": "social_media",
            "content": "Urgent: updаte your password at bankоfamеrіca.com",
        }
        result = validator.validate(signal)
        assert result.flagged is True
        assert "manipulation" in result.issues

    def test_validate_clean_signal_passes(self):
        validator = AdversarialValidator()
        signal = {
            "id": "sig-4",
            "source_type": "social_media",
            "content": "The market closed higher today on strong earnings.",
        }
        result = validator.validate(signal)
        assert result.flagged is False
        assert result.issues == []
        assert result.status == "clean"

    def test_batch_validation_counts_correctly(self):
        validator = AdversarialValidator()
        signals = [
            {"id": "a", "source_type": "social_media", "content": "Biased text."},
            {"id": "b", "source_type": "social_media", "content": "Clean text."},
            {"id": "c", "source_type": "satellite_imagery", "metadata": {"filename": "x.tif", "file_size_bytes": 5000}},
        ]
        result = validator.validate_batch(signals)
        assert result["total"] == 3
        assert result["flagged"] == 2
        assert result["clean"] == 1
        assert len(result["results"]) == 3

    def test_audit_event_structure(self):
        validator = AdversarialValidator()
        signal = {"id": "sig-5", "source_type": "social_media", "content": "Test."}
        result = validator.validate(signal)
        audit = result.to_audit_event()
        assert audit["signal_id"] == "sig-5"
        assert "timestamp" in audit
        assert "issues" in audit
        assert "status" in audit

    def test_validator_gate_blocks_flagged_signals(self):
        validator = AdversarialValidator()
        signal = {
            "id": "sig-bad",
            "source_type": "social_media",
            "content": "All Group Z people are terrorists.",
        }
        result = validator.validate(signal)
        assert result.status == "blocked"


# ---------------------------------------------------------------------------
# CLI / functional tests
# ---------------------------------------------------------------------------
class TestValidateSignalsCLI:
    def test_cli_with_json_output(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(
                [
                    {"id": "s1", "source_type": "social_media", "content": "Normal news."}
                ],
                f,
            )
            path = f.name
        try:
            from icdev.tools.strategos.adversarial_validator import main

            result = main(["--signals-file", path, "--json"])
            assert result == 0
        finally:
            os.unlink(path)

    def test_cli_gate_fails_on_flagged(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(
                [
                    {
                        "id": "s2",
                        "source_type": "social_media",
                        "content": "All members of Group X are criminals.",
                    }
                ],
                f,
            )
            path = f.name
        try:
            from icdev.tools.strategos.adversarial_validator import main

            result = main(["--signals-file", path, "--json", "--gate"])
            assert result == 1
        finally:
            os.unlink(path)

    def test_cli_health_check(self):
        from icdev.tools.strategos.adversarial_validator import main

        result = main(["--health", "--json"])
        assert result == 0


# ---------------------------------------------------------------------------
# Audit persistence tests
# ---------------------------------------------------------------------------
class TestAuditPersistence:
    def test_log_audit_writes_to_db(self):
        from icdev.tools.db.storage import get_connection

        validator = AdversarialValidator()
        signal = {"id": "audit-1", "source_type": "social_media", "content": "Clean."}
        result = validator.validate(signal)
        event = result.to_audit_event()

        conn = get_connection()
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sg_adversarial_validation_audit (
                id TEXT PRIMARY KEY,
                timestamp TEXT NOT NULL,
                signal_id TEXT NOT NULL,
                source_type TEXT,
                status TEXT NOT NULL,
                issues TEXT,
                bias_score REAL,
                deepfake_score REAL,
                manipulation_score REAL,
                details TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO sg_adversarial_validation_audit
            (id, timestamp, signal_id, source_type, status, issues, bias_score, deepfake_score, manipulation_score, details)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event["id"],
                event["timestamp"],
                event["signal_id"],
                event.get("source_type"),
                event["status"],
                json.dumps(event["issues"]),
                event.get("bias_score"),
                event.get("deepfake_score"),
                event.get("manipulation_score"),
                json.dumps(event.get("details", {})),
            ),
        )
        conn.commit()
        row = conn.execute(
            "SELECT signal_id, status FROM sg_adversarial_validation_audit WHERE id = ?",
            (event["id"],),
        ).fetchone()
        conn.close()
        assert row is not None
        assert row["signal_id"] == "audit-1"
