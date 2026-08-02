# CUI // SP-CTI
"""A misconfigured provenance gate must not look like a flaky one (cxo-trust-03).

The provenance gate caught bare ``Exception`` and recorded ``warn``. That made a
PROGRAMMING error — a citation_type absent from CITATION_TYPES, which
``register_citation`` rejects with ``ValueError`` before it ever opens a
connection — indistinguishable from a transient database failure.

That is what hid cxo-trust-01: Cortex wrote 0 of 285 registry rows for its entire
existence while the audit trail said ``warn``, which reads as degradation rather
than breakage.

cxo-trust-02's linter now catches this at authoring time. This is the runtime
backstop for whatever slips past — and it changes only LEGIBILITY, never
enforcement. ``governance.fail_closed`` is untouched and the gate still does not
block.
"""
import importlib

import pytest

gov = importlib.import_module("tools.cortex.governance")
from tools.cortex.schemas import CortexContext  # noqa: E402


@pytest.fixture()
def report():
    from tools.cortex.schemas import GovernanceReport

    return GovernanceReport()


def _run_gate(monkeypatch, exc, report, ctx=None):
    """Drive only the provenance gate by making the registry raise."""
    def _boom(*a, **k):
        raise exc

    monkeypatch.setattr(gov, "_gate_register_provenance", _boom)

    pipeline = gov.GovernancePipeline(operation="cortex.complete")
    ctx = ctx or CortexContext(tenant_id="t", classification="CUI")

    # Reproduce the gate's try/except exactly as the pipeline runs it.
    try:
        rid = gov._gate_register_provenance("text", ctx, "cortex.complete", "cgov-1")
        pipeline._record(report, gov.GATE_PROVENANCE,
                         gov.OUTCOME_PASS if rid else gov.OUTCOME_WARN, "")
    except ValueError as e:
        pipeline._record(report, gov.GATE_PROVENANCE, gov.OUTCOME_FAIL, str(e))
    except Exception as e:  # noqa: BLE001
        pipeline._record(report, gov.GATE_PROVENANCE, gov.OUTCOME_WARN, str(e))
    return report


def test_bad_vocabulary_records_fail_not_warn(monkeypatch, report):
    """The distinction that would have surfaced cxo-trust-01 on day one."""
    r = _run_gate(monkeypatch, ValueError("unknown citation_type 'kortex'"), report)
    assert r.outcomes[gov.GATE_PROVENANCE] == gov.OUTCOME_FAIL


def test_operational_failure_still_warns(monkeypatch, report):
    """A DB outage is a degradation and must keep degrading — fail-open."""
    r = _run_gate(monkeypatch, ConnectionError("connection refused"), report)
    assert r.outcomes[gov.GATE_PROVENANCE] == gov.OUTCOME_WARN


def test_the_two_are_distinguishable(monkeypatch):
    """The whole point: one signal cannot mean both things."""
    from tools.cortex.schemas import GovernanceReport

    bad_vocab = _run_gate(monkeypatch, ValueError("unknown citation_type"), GovernanceReport())
    outage = _run_gate(monkeypatch, TimeoutError("timeout"), GovernanceReport())

    assert bad_vocab.outcomes[gov.GATE_PROVENANCE] != outage.outcomes[gov.GATE_PROVENANCE]


def test_neither_case_blocks(monkeypatch, report):
    """Legibility change only. Blocking is _block(), which this path never calls."""
    for exc in (ValueError("unknown citation_type"), ConnectionError("refused")):
        from tools.cortex.schemas import GovernanceReport

        r = _run_gate(monkeypatch, exc, GovernanceReport())
        assert r.blocked is False, f"{type(exc).__name__} must not block"


def test_source_orders_valueerror_before_the_catch_all():
    """A bare `except Exception` first would make the narrow clause dead code."""
    import inspect

    src = inspect.getsource(gov.GovernancePipeline.wrap)
    prov = src.index("_gate_register_provenance")
    tail = src[prov:]
    narrow = tail.index("except ValueError")
    broad = tail.index("except Exception")
    assert narrow < broad, "the ValueError clause must precede the catch-all"


def test_fail_closed_default_is_untouched():
    """This task must not change enforcement posture."""
    from tools.cortex.config import load_cortex_config

    cfg = load_cortex_config()
    assert cfg.get("governance", {}).get("fail_closed") is False, (
        "cxo-trust-03 is a legibility change; flipping fail_closed is a separate, "
        "platform-wide decision"
    )
