# CUI // SP-CTI
"""Fabric-scoped posture roll-up (rmf-fab-02).

A *fabric* is an enclave instance that HAS a classification; it is not itself
a classification level. The registry that names them is ``tools.fabric.registry``
(rmf-fab-01); this package's :mod:`~tools.fabric.posture` rolls posture up
across whatever fabrics that registry declares.
"""
