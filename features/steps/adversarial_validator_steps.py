# CUI // SP-CTI
"""Step definitions for adversarial data validation BDD scenarios.

Validates that the adversarial validator pipeline correctly detects bias,
deepfakes, and adversarial manipulation in OSINT signals before they enter
the predictive analysis engine.
"""

import importlib.util
import sys
from pathlib import Path

from behave import given, then, when

BASE_DIR = Path(__file__).resolve().parents[2]
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


# ---------------------------------------------------------------------------
# Given
# ---------------------------------------------------------------------------
@given("the adversarial validator module is available")
def step_validator_module_available(context):
    module_path = BASE_DIR / "icdev" / "tools" / "strategos" / "adversarial_validator.py"
    alt_path = BASE_DIR / "tools" / "strategos" / "adversarial_validator.py"
    assert module_path.exists() or alt_path.exists(), (
        f"adversarial_validator.py not found at {module_path} or {alt_path}"
    )
    context.validator = AdversarialValidator()


@given("a social media signal with biased content")
def step_biased_social_media_signal(context):
    context.signal = {
        "id": "bdd-bias-01",
        "source_type": "social_media",
        "content": "All members of Group X are criminals and should be deported.",
    }


@given("a satellite imagery signal with inconsistent metadata")
def step_deepfake_satellite_signal(context):
    context.signal = {
        "id": "bdd-df-01",
        "source_type": "satellite_imagery",
        "metadata": {
            "filename": "sat_scene_01.jpg",
            "exif_make": "Canon",
            "exif_software": "Adobe Photoshop 2024",
            "file_size_bytes": 1024,
        },
    }


@given("a text signal containing homoglyphs and invisible characters")
def step_manipulated_text_signal(context):
    context.signal = {
        "id": "bdd-manip-01",
        "source_type": "social_media",
        "content": "Visit goоgle.com​now",  # Cyrillic o + ZWSP
    }


@given("a clean OSINT signal")
def step_clean_signal(context):
    context.signal = {
        "id": "bdd-clean-01",
        "source_type": "social_media",
        "content": "The market closed higher today on strong earnings.",
    }


@given("a batch of 3 signals with mixed quality")
def step_mixed_batch(context):
    context.signals = [
        {"id": "b1", "source_type": "social_media", "content": "Biased text here."},
        {"id": "b2", "source_type": "social_media", "content": "Clean report."},
        {"id": "b3", "source_type": "satellite_imagery", "metadata": {"filename": "x.tif", "file_size_bytes": 5000}},
    ]


@given("a validated signal result")
def step_validated_result(context):
    validator = AdversarialValidator()
    context.signal = {"id": "bdd-audit-01", "source_type": "social_media", "content": "Test content."}
    context.result = validator.validate(context.signal)


@given("a social media signal with severely biased content")
def step_severely_biased_signal(context):
    context.signal = {
        "id": "bdd-gate-01",
        "source_type": "social_media",
        "content": "All women are inherently less capable leaders than men and should not hold office.",
    }


# ---------------------------------------------------------------------------
# When
# ---------------------------------------------------------------------------
@when("the signal is validated by the pipeline")
def step_validate_signal(context):
    validator = AdversarialValidator()
    context.result = validator.validate(context.signal)


@when("the batch is validated")
def step_validate_batch(context):
    validator = AdversarialValidator()
    context.batch_result = validator.validate_batch(context.signals)


@when("the audit event is generated")
def step_generate_audit(context):
    context.audit_event = context.result.to_audit_event()


@when("the signal is validated with gate mode enabled")
def step_validate_with_gate(context):
    validator = AdversarialValidator()
    context.result = validator.validate(context.signal)
    context.gate_failed = context.result.status == "blocked"


# ---------------------------------------------------------------------------
# Then
# ---------------------------------------------------------------------------
@then("the result should be flagged")
def step_result_flagged(context):
    assert context.result.flagged is True, f"Expected flagged=True, got {context.result.flagged}"


@then("the result should not be flagged")
def step_result_not_flagged(context):
    assert context.result.flagged is False, f"Expected flagged=False, got {context.result.flagged}"


@then('the issue category should include "{category}"')
def step_issue_category_includes(context, category):
    assert category in context.result.issues, (
        f"Expected '{category}' in issues, got {context.result.issues}"
    )


@then('the status should be "{status}"')
def step_status_is(context, status):
    assert context.result.status == status, (
        f"Expected status '{status}', got '{context.result.status}'"
    )


@then("the total count should be {total:d}")
def step_total_count(context, total):
    assert context.batch_result["total"] == total, (
        f"Expected total={total}, got {context.batch_result['total']}"
    )


@then("the flagged count should be {flagged:d}")
def step_flagged_count(context, flagged):
    assert context.batch_result["flagged"] == flagged, (
        f"Expected flagged={flagged}, got {context.batch_result['flagged']}"
    )


@then("the clean count should be {clean:d}")
def step_clean_count(context, clean):
    assert context.batch_result["clean"] == clean, (
        f"Expected clean={clean}, got {context.batch_result['clean']}"
    )


@then("the audit event should contain a timestamp")
def step_audit_has_timestamp(context):
    assert "timestamp" in context.audit_event, "Missing timestamp in audit event"
    assert context.audit_event["timestamp"] is not None


@then("the audit event should contain the signal id")
def step_audit_has_signal_id(context):
    assert "signal_id" in context.audit_event, "Missing signal_id in audit event"
    assert context.audit_event["signal_id"] == context.signal["id"]


@then("the audit event should contain detected issues")
def step_audit_has_issues(context):
    assert "issues" in context.audit_event, "Missing issues in audit event"


@then("the validation gate should fail")
def step_gate_fails(context):
    assert context.gate_failed is True, "Expected gate to fail for critical finding"
