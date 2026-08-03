# CUI // SP-CTI
"""ICDEV™ analyzer/responder contract (anz-con-01).

The contract is declared as data in ``args/analyzer_contract.yaml``. This
package holds its loader and validator (:mod:`tools.analyzers.contract`), the
layer that hands a declared analyzer an observable without touching the
analyzer (:mod:`tools.analyzers.binding`, anz-mig-01), and the harness that
proves a port changed no behaviour (:mod:`tools.analyzers.parity`).
"""
