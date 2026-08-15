# CUI // SP-CTI
"""cortex.extract holds output to its declared shape — CUI // SP-CTI.

extract shipped three postures in sequence, and only the third is a check:

  1. return whatever JSON the model emitted, unvalidated;
  2. validate, then DEGRADE — hand the raw completion back as ``result.text``
     with ``schema_valid=False`` tucked into ``metadata``;
  3. (trust-struct-03) validate against a declared contract, re-prompt ONCE,
     and refuse.

Posture 2 read as a fix and was not one. The flag lived in metadata while the
non-conforming text stayed in the field callers actually read: one of the two
in-repo callers ran ``json.loads(result.text)`` without ever looking at
``schema_valid``. A check whose result nothing has to consult is a comment.

The other half of this file is the ``unmeasurable`` split. ``_validate_against_
schema`` used to return ``(True, "")`` when ``jsonschema`` was absent or no
schema was supplied — an air-gapped deployment validated nothing and reported
that everything conformed. Unmeasured is now its own state and is never True.
"""
from __future__ import annotations

import importlib

import pytest

from tools.cortex import api as cortex_api
from tools.cortex.schemas import CortexContext
from tools.llm.provider import LLMResponse

_SCHEMA = {
    "type": "object",
    "properties": {"name": {"type": "string"}, "year": {"type": "integer"}},
    "required": ["name", "year"],
}

# `minLength` is outside structured_output's closed subset, so this schema
# exercises the jsonschema fallback rather than the contract path.
_OUT_OF_SUBSET = {
    "type": "object",
    "properties": {"name": {"type": "string", "minLength": 3}},
    "required": ["name"],
}

_CTX = CortexContext(tenant_id="t1")

# CortexSchemaError is reached through the module on purpose, never as a
# from-import binding: tests/cortex/test_airgap_assertion.py reloads
# tools.cortex.api, which mints a NEW exception class while a from-import still
# holds the old one -- so `pytest.raises` stops matching and every refusal test
# here fails only in a full-suite run. Same hazard test_reason_facade.py
# documents for facade identity.


class _FakeRouter:
    """Returns the queued responses in order; repeats the last one forever.

    Repeating rather than raising is deliberate: a model that missed the schema
    usually misses it the same way twice, and that is the case where the bound
    on repair attempts has to hold.
    """

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def invoke(self, function, request, **kw):
        self.calls.append(request)
        idx = min(len(self.calls) - 1, len(self.responses) - 1)
        return self.responses[idx]


@pytest.fixture
def install_router(monkeypatch):
    """Install a fake router and hand the test the object, so it can count calls."""

    def _install(*responses):
        router = _FakeRouter(responses)
        llm = importlib.import_module("tools.llm")
        monkeypatch.setattr(llm, "get_router", lambda config_path=None: router)
        return router

    return _install


def _resp(**ov):
    d = dict(content="", provider="fake", model_id="m", cost_usd=0.0,
             duration_ms=1, input_tokens=1, output_tokens=1)
    d.update(ov)
    return LLMResponse(**d)


# --------------------------------------------------------------------------- #
# Conforming output
# --------------------------------------------------------------------------- #


def test_conforming_payload_is_valid(install_router):
    install_router(_resp(structured_output={"name": "ICDEV", "year": 2026}))
    r = cortex_api.extract("txt", _SCHEMA, ctx=_CTX)
    assert r.metadata["schema_valid"] is True
    assert r.metadata["schema_status"] == "valid"
    assert r.metadata["schema_checker"] == "contract"
    assert "schema_error" not in r.metadata


def test_the_parsed_object_travels_beside_the_text(install_router):
    """``data["payload"]`` exists so a caller never has to re-parse ``text``.

    The re-parse is where the old contract leaked: ``json.loads(result.text)``
    was the obvious call and it was the one that got prose.
    """
    install_router(_resp(structured_output={"name": "ICDEV", "year": 2026}))
    r = cortex_api.extract("txt", _SCHEMA, ctx=_CTX)
    assert r.data["payload"] == {"name": "ICDEV", "year": 2026}


def test_a_plain_text_completion_is_parsed_too(install_router):
    """Providers without native structured output still go through one path."""
    install_router(_resp(content='```json\n{"name": "ICDEV", "year": 2026}\n```'))
    r = cortex_api.extract("txt", _SCHEMA, ctx=_CTX)
    assert r.data["payload"] == {"name": "ICDEV", "year": 2026}


# --------------------------------------------------------------------------- #
# Non-conforming output refuses — it does not degrade
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "bad",
    [
        pytest.param({"structured_output": {"name": "ICDEV"}}, id="missing_required"),
        pytest.param({"structured_output": {"name": "ICDEV", "year": "MMXXVI"}}, id="wrong_type"),
        pytest.param({"content": "not json at all"}, id="unparseable"),
        pytest.param({"content": ""}, id="empty"),
    ],
)
def test_non_conforming_output_raises(install_router, bad):
    install_router(_resp(**bad))
    with pytest.raises(cortex_api.CortexSchemaError):
        cortex_api.extract("txt", _SCHEMA, ctx=_CTX)


def test_the_refusal_names_the_defect(install_router):
    """"extraction failed" is not actionable; "$.year: expected integer" is."""
    install_router(_resp(structured_output={"name": "ICDEV"}))
    with pytest.raises(cortex_api.CortexSchemaError) as exc:
        cortex_api.extract("txt", _SCHEMA, ctx=_CTX)
    codes = {f["code"] for f in exc.value.findings}
    assert "missing_required" in codes
    assert any(f["path"].endswith("year") for f in exc.value.findings)


def test_a_partly_valid_object_is_never_returned(install_router):
    """Three of four fields right is the shape of bug this removes."""
    install_router(_resp(structured_output={"name": "ICDEV"}))
    with pytest.raises(cortex_api.CortexSchemaError):
        cortex_api.extract("txt", _SCHEMA, ctx=_CTX)


# --------------------------------------------------------------------------- #
# Repair is bounded at exactly one
# --------------------------------------------------------------------------- #


def test_one_repair_recovers_a_near_miss(install_router):
    router = install_router(
        _resp(structured_output={"name": "ICDEV"}),          # attempt 1: no year
        _resp(content='{"name": "ICDEV", "year": 2026}'),    # attempt 2: corrected
    )
    r = cortex_api.extract("txt", _SCHEMA, ctx=_CTX)
    assert r.metadata["schema_valid"] is True
    assert r.metadata["structure_repaired"] is True
    assert r.metadata["schema_attempts"] == 2
    assert len(router.calls) == 2


def test_repair_stops_at_one_attempt(install_router):
    """Bounded by construction. A model that cannot satisfy a two-field contract
    on the second try will not on the fifth, and an unbounded repair loop is an
    unbounded spend."""
    router = install_router(_resp(content="prose, still prose"))
    with pytest.raises(cortex_api.CortexSchemaError) as exc:
        cortex_api.extract("txt", _SCHEMA, ctx=_CTX)
    assert len(router.calls) == 2
    assert exc.value.attempts == 2


def test_repair_can_be_switched_off(install_router):
    router = install_router(_resp(content="prose"))
    with pytest.raises(cortex_api.CortexSchemaError) as exc:
        cortex_api.extract("txt", _SCHEMA, ctx=_CTX, repair=False)
    assert len(router.calls) == 1
    assert exc.value.attempts == 1


def test_the_repair_call_carries_the_callers_classification(install_router):
    """The retry must not be the one LLM call in this module that egresses
    ungoverned. It goes through cortex's own ``_invoke``, so tenant,
    classification and budget attribution ride along exactly as on call one."""
    router = install_router(_resp(content="prose"))
    ctx = CortexContext(tenant_id="acme", classification="CUI")
    with pytest.raises(cortex_api.CortexSchemaError):
        cortex_api.extract("txt", _SCHEMA, ctx=ctx)
    retry = router.calls[1]
    assert retry.tenant_id == "acme"
    assert retry.classification == "CUI"
    assert retry.agent_id


# --------------------------------------------------------------------------- #
# Explicit best-effort is still available — as a choice, not a default
# --------------------------------------------------------------------------- #


def test_on_invalid_return_keeps_the_old_behaviour(install_router):
    install_router(_resp(content="not json at all"))
    r = cortex_api.extract("txt", _SCHEMA, ctx=_CTX, on_invalid="return")
    assert r.metadata["schema_valid"] is False
    assert r.metadata["schema_status"] == "invalid"
    assert r.metadata["schema_error"]
    assert r.grounded is False
    assert r.text == "not json at all"


def test_on_invalid_rejects_an_unknown_posture():
    with pytest.raises(ValueError):
        cortex_api.extract("txt", _SCHEMA, ctx=_CTX, on_invalid="maybe")


# --------------------------------------------------------------------------- #
# Unmeasured is not clean
# --------------------------------------------------------------------------- #


def test_no_schema_reports_unmeasurable_rather_than_valid(install_router):
    """This used to return schema_valid=True. Nothing was checked."""
    install_router(_resp(structured_output={"anything": True}))
    r = cortex_api.extract("txt", {}, ctx=_CTX)
    assert r.metadata["schema_valid"] is None
    assert r.metadata["schema_status"] == "unmeasurable"
    assert r.metadata["schema_error"]


def test_unmeasurable_does_not_refuse(install_router):
    """A stated posture, not a block. Unmeasurable says nothing about the
    payload, so refusing on it would fail every arbitrary-schema call in a
    deployment without jsonschema while proving nothing about any of them."""
    install_router(_resp(structured_output={"anything": True}))
    r = cortex_api.extract("txt", {}, ctx=_CTX)
    assert r.data["payload"] == {"anything": True}


def test_a_falsy_schema_valid_is_what_callers_must_branch_on(install_router):
    """``metadata.get("schema_valid", True)`` was the old caller idiom and it
    read a missing key as a pass. None is falsy, so the corrected idiom —
    ``if not metadata.get("schema_valid")`` — routes unmeasurable to the
    caller's floor rather than to a claim."""
    install_router(_resp(structured_output={"anything": True}))
    r = cortex_api.extract("txt", {}, ctx=_CTX)
    assert not r.metadata.get("schema_valid")


# --------------------------------------------------------------------------- #
# Schemas outside the contract subset fall back rather than pass
# --------------------------------------------------------------------------- #


def test_out_of_subset_schema_uses_jsonschema(install_router):
    install_router(_resp(structured_output={"name": "ICDEV"}))
    r = cortex_api.extract("txt", _OUT_OF_SUBSET, ctx=_CTX)
    assert r.metadata["schema_checker"] == "jsonschema"
    assert r.metadata["schema_valid"] is True


def test_out_of_subset_violation_still_refuses(install_router):
    """`minLength` is not enforceable by the contract validator, so this proves
    the fallback is a real check and not a shrug."""
    install_router(_resp(structured_output={"name": "ab"}))
    with pytest.raises(cortex_api.CortexSchemaError):
        cortex_api.extract("txt", _OUT_OF_SUBSET, ctx=_CTX)


def test_out_of_subset_schema_is_not_repaired(install_router):
    """No contract means no schema fragment to re-prompt with, so there is
    nothing to repair from — one call, then the verdict."""
    router = install_router(_resp(structured_output={"name": "ab"}))
    with pytest.raises(cortex_api.CortexSchemaError):
        cortex_api.extract("txt", _OUT_OF_SUBSET, ctx=_CTX)
    assert len(router.calls) == 1
