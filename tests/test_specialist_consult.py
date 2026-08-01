# CUI // SP-CTI
"""tools/govcon/specialist_consult.py -- the never-raise contract of
request_council_consult(), the submit-then-poll job cycle, and that the
redaction step never lets verbatim input text reach the outgoing payload
when a real GovConSanitizer is available.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.govcon import specialist_consult


def _fake_requests(post_response=None, get_responses=None):
    """Build a MagicMock standing in for the `requests` module, with a
    scripted sequence of GET responses (one per poll)."""
    mock_requests = MagicMock()
    mock_requests.post.return_value = post_response
    mock_requests.get.side_effect = get_responses or []
    return mock_requests


def _resp(status_code=200, json_body=None):
    r = MagicMock()
    r.status_code = status_code
    r.json.return_value = json_body or {}
    return r


def test_request_council_consult_returns_none_for_empty_question(monkeypatch):
    monkeypatch.setenv("SPECIALIST_API_KEY", "secret")
    assert specialist_consult.request_council_consult("govcon", "") is None
    assert specialist_consult.request_council_consult("govcon", "   ") is None


def test_request_council_consult_returns_none_when_api_key_not_configured(monkeypatch):
    monkeypatch.delenv("SPECIALIST_API_KEY", raising=False)
    assert specialist_consult.request_council_consult("govcon", "Should we bid?") is None


def test_request_council_consult_returns_none_when_requests_unimportable(monkeypatch):
    monkeypatch.setenv("SPECIALIST_API_KEY", "secret")
    with patch.dict(sys.modules, {"requests": None}):
        assert specialist_consult.request_council_consult("govcon", "Should we bid?") is None


def test_request_council_consult_returns_none_on_submit_http_error(monkeypatch):
    monkeypatch.setenv("SPECIALIST_API_KEY", "secret")
    fake_requests = _fake_requests(post_response=_resp(status_code=500))
    with patch.dict(sys.modules, {"requests": fake_requests}):
        assert specialist_consult.request_council_consult("govcon", "Should we bid?") is None


def test_request_council_consult_returns_none_on_submit_missing_job_id(monkeypatch):
    monkeypatch.setenv("SPECIALIST_API_KEY", "secret")
    fake_requests = _fake_requests(post_response=_resp(json_body={"status": "pending"}))
    with patch.dict(sys.modules, {"requests": fake_requests}):
        assert specialist_consult.request_council_consult("govcon", "Should we bid?") is None


def test_request_council_consult_polls_until_completed(monkeypatch):
    monkeypatch.setenv("SPECIALIST_API_KEY", "secret")
    fake_requests = _fake_requests(
        post_response=_resp(json_body={"job_id": "job-1", "status": "pending"}),
        get_responses=[
            _resp(json_body={"status": "running"}),
            _resp(json_body={"status": "completed", "verdict": "Proceed with caution.", "stop_reason": "completed", "source": "icdev_council"}),
        ],
    )
    with patch.dict(sys.modules, {"requests": fake_requests}), patch("time.sleep"):
        result = specialist_consult.request_council_consult("govcon", "Should we bid?", poll_interval_seconds=0.01)

    assert result == {"verdict": "Proceed with caution.", "stop_reason": "completed", "source": "icdev_council"}
    assert fake_requests.get.call_count == 2


def test_request_council_consult_returns_none_when_job_fails(monkeypatch):
    monkeypatch.setenv("SPECIALIST_API_KEY", "secret")
    fake_requests = _fake_requests(
        post_response=_resp(json_body={"job_id": "job-2"}),
        get_responses=[_resp(json_body={"status": "failed", "error": "Council consult unavailable."})],
    )
    with patch.dict(sys.modules, {"requests": fake_requests}):
        assert specialist_consult.request_council_consult("govcon", "Should we bid?") is None


def test_request_council_consult_returns_none_on_timeout(monkeypatch):
    monkeypatch.setenv("SPECIALIST_API_KEY", "secret")
    fake_requests = _fake_requests(
        post_response=_resp(json_body={"job_id": "job-3"}),
        get_responses=[_resp(json_body={"status": "running"})] * 5,
    )
    with patch.dict(sys.modules, {"requests": fake_requests}), patch("time.sleep"), \
         patch("time.monotonic", side_effect=[0, 0, 1, 2, 300, 300]):
        result = specialist_consult.request_council_consult(
            "govcon", "Should we bid?", max_wait_seconds=10, poll_interval_seconds=0.01,
        )
    assert result is None


def test_request_council_consult_returns_none_on_poll_http_error(monkeypatch):
    monkeypatch.setenv("SPECIALIST_API_KEY", "secret")
    fake_requests = _fake_requests(
        post_response=_resp(json_body={"job_id": "job-4"}),
        get_responses=[_resp(status_code=500)],
    )
    with patch.dict(sys.modules, {"requests": fake_requests}):
        assert specialist_consult.request_council_consult("govcon", "Should we bid?") is None


def test_request_council_consult_sends_sanitized_payload_not_raw_text(monkeypatch):
    """Redaction must actually run: the outgoing POST body must not contain
    the literal PII-bearing string, given a sanitizer that visibly changes it."""
    monkeypatch.setenv("SPECIALIST_API_KEY", "secret")
    fake_requests = _fake_requests(
        post_response=_resp(json_body={"job_id": "job-5"}),
        get_responses=[_resp(json_body={"status": "completed", "verdict": "V", "stop_reason": "completed", "source": "icdev_council"})],
    )

    fake_sanitizer_instance = MagicMock()
    fake_sanitizer_instance.sanitize_for_llm.side_effect = lambda text, **kw: (f"[REDACTED]{text[-4:]}", {})

    with patch.dict(sys.modules, {"requests": fake_requests}), \
         patch("tools.redaction.govcon_sanitizer.GovConSanitizer", return_value=fake_sanitizer_instance):
        specialist_consult.request_council_consult(
            "govcon", "contact John Doe at 555-123-4567 re: bid", context_summary="Jane Roe, jane@example.com",
        )

    sent_json = fake_requests.post.call_args.kwargs["json"]
    assert "John Doe" not in sent_json["question"]
    assert "555-123-4567" not in sent_json["question"]
    assert "jane@example.com" not in sent_json["context_summary"]
    assert sent_json["question"].startswith("[REDACTED]")


def test_request_council_consult_fails_closed_when_sanitizer_unimportable(monkeypatch):
    """Redaction failing to import must abandon the consult, NOT send raw text.

    This asserted the opposite -- that an unimportable sanitizer falls back to
    sending the original text -- and had been failing since the fail-open bypass
    was removed. That bypass contradicted `redaction.fail_closed: true` in
    args/redaction_config.yaml in the one module that crosses the network
    boundary with CUI. Degrading to "no consult" costs an advisory third
    opinion; degrading to "send it raw" costs a spill. Never restore the old
    behavior to make this pass.
    """
    monkeypatch.setenv("SPECIALIST_API_KEY", "secret")
    fake_requests = _fake_requests(
        post_response=_resp(json_body={"job_id": "job-6"}),
        get_responses=[_resp(json_body={"status": "completed", "verdict": "V", "stop_reason": "completed", "source": "icdev_council"})],
    )
    with patch.dict(sys.modules, {"requests": fake_requests, "tools.redaction.govcon_sanitizer": None}):
        result = specialist_consult.request_council_consult("govcon", "Should we bid?")

    # Consult abandoned -- callers treat None as "consult unavailable".
    assert result is None
    # ...and critically, nothing was ever put on the wire.
    assert not fake_requests.post.called
