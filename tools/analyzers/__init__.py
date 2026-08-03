# CUI // SP-CTI
"""ICDEV™ analyzer/responder contract (anz-con-01).

The contract is declared as data in ``args/analyzer_contract.yaml``; no module
here enumerates analyzers, observable types or modules.

    contract    loads and validates the declaration (anz-con-01)
    dispatch    fans one observable out to every analyzer accepting its type,
                and reports every outcome by name (anz-disp-01)
    rate_limit  enforces each declaration's ``rate_limit`` — queueing or
                reporting, never dropping (anz-rate-01)
    sandbox     enforces each declaration's ``sandbox`` posture through the
                platform ``SandboxExecutor``, failing closed (anz-rate-01)
"""
