"""Tests for the LLM correspondence-extraction enrichment in the DIC ingest
orchestrator.

aiify-opp-6100: regex_user_input -> nlp_extractor. The external scan flagged
paperless' MailDocumentParser — regex/header parsing of user-controlled email
text to recover the envelope fields (From, To/Cc, Subject, sent date). Per the
established aiify-opp pattern the augmentation lands in the analogous ICDEV
subsystem (DIC) as ``_ai_extract_correspondence``. These pin its load-bearing
guarantees:

* it grounds the model on the document text alone and only sends the leading
  ``_CORRESPONDENCE_INPUT_CHARS`` slice (cheap, bounded regardless of size);
* email addresses are kept only when they are e-mail-shaped AND their core
  literally appears in the source text (anti-hallucination);
* party display names and the subject are length-capped and kept only when their
  alphanumeric core appears in the text — the model cannot invent a participant
  or subject not present in the message;
* recipients are de-duplicated and count-capped; the sent date is kept only when
  it is a real ISO calendar date;
* a single confidence score gates the whole suggestion — below
  ``_CORRESPONDENCE_MIN_CONFIDENCE`` the result is dropped for the HITL path;
* with no grounded envelope signal at all it returns ``None`` (not an email);
* it degrades silently to ``None`` on empty input, blank/garbled output, or any
  LLM failure — ingestion must never break on enrichment;
* the proposal is surfaced under ``IngestOutcome.metadata["correspondence"]`` and
  never persisted.
"""
from __future__ import annotations

import importlib
import json

import pytest

ingest = importlib.import_module("tools.document_intelligence.ingest_orchestrator")
router_mod = importlib.import_module("tools.llm.router")

# A document body that all grounded tokens used below appear in, verbatim.
SOURCE = (
    "From: Jane Analyst <jane.analyst@example.mil>\n"
    "To: Bob Reviewer <bob.reviewer@example.mil>, Carol Lead <carol.lead@example.mil>\n"
    "Subject: Quarterly Security Posture Review\n"
    "Date: 2026-01-15\n\n"
    "Please find the quarterly security posture review attached for your records."
)


class _Resp:
    def __init__(self, content):
        self.content = content


class _Router:
    """Stand-in LLMRouter that records the request and returns a canned reply."""

    last_request = None
    _content = "{}"

    def __init__(self, *a, **k):
        pass

    def invoke(self, function, request):
        _Router.last_request = request
        return _Resp(self._content)


@pytest.fixture(autouse=True)
def _reset_router():
    _Router.last_request = None
    _Router._content = "{}"
    yield


def _patch_router(monkeypatch, content=None):
    import sys as _sys
    if content is not None:
        _Router._content = content
    monkeypatch.setattr(router_mod, "LLMRouter", _Router)
    # Patch ALL known router aliases — the _ToolsRedirect shim causes
    # `from tools.llm.router import LLMRouter` to resolve to different
    # module objects depending on full-suite import ordering.
    import icdev.tools.llm.router as _icdev_router_mod
    monkeypatch.setattr(_icdev_router_mod, "LLMRouter", _Router)
    for _key, _mod in list(_sys.modules.items()):
        if "llm.router" in _key and hasattr(_mod, "LLMRouter"):
            monkeypatch.setattr(_mod, "LLMRouter", _Router)


def _json(**kw):
    return json.dumps(kw)


def test_returns_normalized_correspondence(monkeypatch):
    _patch_router(
        monkeypatch,
        content=_json(
            from_name="Jane Analyst",
            from_email="jane.analyst@example.mil",
            to=[
                {"name": "Bob Reviewer", "email": "bob.reviewer@example.mil"},
                {"name": "Carol Lead", "email": "carol.lead@example.mil"},
            ],
            subject="Quarterly Security Posture Review",
            sent_date="2026-01-15",
            confidence=0.95,
        ),
    )
    out = ingest._ai_extract_correspondence(SOURCE)
    assert out == {
        "from_name": "Jane Analyst",
        "from_email": "jane.analyst@example.mil",
        "to": [
            {"name": "Bob Reviewer", "email": "bob.reviewer@example.mil"},
            {"name": "Carol Lead", "email": "carol.lead@example.mil"},
        ],
        "subject": "Quarterly Security Posture Review",
        "sent_date": "2026-01-15",
        "confidence": 0.95,
    }


def test_empty_text_returns_none_without_calling_llm(monkeypatch):
    _patch_router(monkeypatch)
    assert ingest._ai_extract_correspondence("   ") is None
    assert _Router.last_request is None


def test_low_confidence_dropped(monkeypatch):
    _patch_router(
        monkeypatch,
        content=_json(
            from_name="Jane Analyst",
            from_email="jane.analyst@example.mil",
            to=[],
            subject="",
            sent_date=None,
            confidence=0.4,
        ),
    )
    assert ingest._ai_extract_correspondence(SOURCE) is None


def test_confidence_at_threshold_kept(monkeypatch):
    _patch_router(
        monkeypatch,
        content=_json(
            from_name="Jane Analyst",
            from_email="",
            to=[],
            subject="",
            sent_date=None,
            confidence=ingest._CORRESPONDENCE_MIN_CONFIDENCE,
        ),
    )
    out = ingest._ai_extract_correspondence(SOURCE)
    assert out is not None
    assert out["confidence"] == ingest._CORRESPONDENCE_MIN_CONFIDENCE


def test_ungrounded_email_dropped(monkeypatch):
    # Address is e-mail-shaped but never appears in SOURCE -> dropped.
    _patch_router(
        monkeypatch,
        content=_json(
            from_name="Jane Analyst",
            from_email="attacker@evil.example",
            to=[],
            subject="",
            sent_date=None,
            confidence=0.9,
        ),
    )
    out = ingest._ai_extract_correspondence(SOURCE)
    assert out is not None
    assert out["from_email"] == ""
    # The grounded name keeps the suggestion alive.
    assert out["from_name"] == "Jane Analyst"


def test_malformed_email_dropped(monkeypatch):
    _patch_router(
        monkeypatch,
        content=_json(
            from_name="",
            from_email="not-an-email",
            to=[],
            subject="Quarterly Security Posture Review",
            sent_date=None,
            confidence=0.9,
        ),
    )
    out = ingest._ai_extract_correspondence(SOURCE)
    assert out["from_email"] == ""


def test_ungrounded_subject_dropped(monkeypatch):
    _patch_router(
        monkeypatch,
        content=_json(
            from_name="Jane Analyst",
            from_email="",
            to=[],
            subject="A Totally Fabricated Subject Line",
            sent_date=None,
            confidence=0.9,
        ),
    )
    out = ingest._ai_extract_correspondence(SOURCE)
    assert out["subject"] == ""


def test_ungrounded_recipient_dropped(monkeypatch):
    _patch_router(
        monkeypatch,
        content=_json(
            from_name="Jane Analyst",
            from_email="",
            to=[
                {"name": "Bob Reviewer", "email": "bob.reviewer@example.mil"},
                {"name": "Ghost Person", "email": "ghost@nowhere.example"},
            ],
            subject="",
            sent_date=None,
            confidence=0.9,
        ),
    )
    out = ingest._ai_extract_correspondence(SOURCE)
    # Only the grounded recipient survives.
    assert out["to"] == [{"name": "Bob Reviewer", "email": "bob.reviewer@example.mil"}]


def test_recipients_deduped(monkeypatch):
    _patch_router(
        monkeypatch,
        content=_json(
            from_name="",
            from_email="",
            to=[
                {"name": "Bob Reviewer", "email": "bob.reviewer@example.mil"},
                {"name": "Bob Reviewer", "email": "bob.reviewer@example.mil"},
            ],
            subject="",
            sent_date=None,
            confidence=0.9,
        ),
    )
    out = ingest._ai_extract_correspondence(SOURCE)
    assert len(out["to"]) == 1


def test_recipients_count_capped(monkeypatch):
    # Build many grounded recipients by embedding them in the source text.
    names = [f"User{i}" for i in range(ingest._CORRESPONDENCE_MAX_RECIPIENTS + 10)]
    body = SOURCE + "\nCc: " + ", ".join(names)
    _patch_router(
        monkeypatch,
        content=_json(
            from_name="",
            from_email="",
            to=[{"name": n, "email": ""} for n in names],
            subject="",
            sent_date=None,
            confidence=0.9,
        ),
    )
    out = ingest._ai_extract_correspondence(body)
    assert len(out["to"]) == ingest._CORRESPONDENCE_MAX_RECIPIENTS


def test_invalid_date_dropped(monkeypatch):
    _patch_router(
        monkeypatch,
        content=_json(
            from_name="Jane Analyst",
            from_email="",
            to=[],
            subject="",
            sent_date="last Tuesday",
            confidence=0.9,
        ),
    )
    out = ingest._ai_extract_correspondence(SOURCE)
    assert out["sent_date"] is None


def test_impossible_calendar_date_dropped(monkeypatch):
    _patch_router(
        monkeypatch,
        content=_json(
            from_name="Jane Analyst",
            from_email="",
            to=[],
            subject="",
            sent_date="2026-13-40",
            confidence=0.9,
        ),
    )
    out = ingest._ai_extract_correspondence(SOURCE)
    assert out["sent_date"] is None


def test_no_envelope_signal_returns_none(monkeypatch):
    # Everything ungrounded / empty -> not correspondence -> None.
    _patch_router(
        monkeypatch,
        content=_json(
            from_name="",
            from_email="attacker@evil.example",
            to=[{"name": "Ghost", "email": "ghost@nowhere.example"}],
            subject="Fabricated",
            sent_date=None,
            confidence=0.9,
        ),
    )
    assert ingest._ai_extract_correspondence(SOURCE) is None


def test_subject_length_capped(monkeypatch):
    long_subject = "A" * (ingest._CORRESPONDENCE_SUBJECT_MAX_LEN + 50)
    _patch_router(
        monkeypatch,
        content=_json(
            from_name="",
            from_email="",
            to=[],
            subject=long_subject,
            sent_date=None,
            confidence=0.9,
        ),
    )
    out = ingest._ai_extract_correspondence(long_subject)
    assert out is not None
    assert len(out["subject"]) == ingest._CORRESPONDENCE_SUBJECT_MAX_LEN


def test_missing_confidence_returns_none(monkeypatch):
    _patch_router(
        monkeypatch,
        content=_json(
            from_name="Jane Analyst", from_email="", to=[], subject="", sent_date=None
        ),
    )
    assert ingest._ai_extract_correspondence(SOURCE) is None


def test_strips_fenced_block(monkeypatch):
    body = _json(
        from_name="Jane Analyst",
        from_email="",
        to=[],
        subject="",
        sent_date=None,
        confidence=0.8,
    )
    _patch_router(monkeypatch, content=f"```json\n{body}\n```")
    out = ingest._ai_extract_correspondence(SOURCE)
    assert out["from_name"] == "Jane Analyst"


def test_garbled_output_returns_none(monkeypatch):
    _patch_router(monkeypatch, content="not json at all")
    assert ingest._ai_extract_correspondence(SOURCE) is None


def test_input_truncated_to_budget(monkeypatch):
    _patch_router(
        monkeypatch,
        content=_json(
            from_name="Jane Analyst",
            from_email="",
            to=[],
            subject="",
            sent_date=None,
            confidence=0.9,
        ),
    )
    src = "q" * (ingest._CORRESPONDENCE_INPUT_CHARS + 5000)
    ingest._ai_extract_correspondence(src)
    sent = _Router.last_request.messages[0]["content"]
    assert sent.count("q") == ingest._CORRESPONDENCE_INPUT_CHARS


def test_llm_failure_returns_none(monkeypatch):
    class _Boom:
        def __init__(self, *a, **k):
            pass

        def invoke(self, *a, **k):
            raise RuntimeError("provider down")

    monkeypatch.setattr(router_mod, "LLMRouter", _Boom)
    import sys as _sys
    import icdev.tools.llm.router as _icdev_router_mod
    monkeypatch.setattr(_icdev_router_mod, "LLMRouter", _Boom)
    for _key, _mod in list(_sys.modules.items()):
        if "llm.router" in _key and hasattr(_mod, "LLMRouter"):
            monkeypatch.setattr(_mod, "LLMRouter", _Boom)
    assert ingest._ai_extract_correspondence(SOURCE) is None
