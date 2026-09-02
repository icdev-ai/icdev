# CUI // SP-CTI
"""ICDEV™ Asset Visibility.

Two halves of the same question, kept apart on purpose:

``identity.py`` (rmf-ident-01) — one row per physical asset, carrying a
resolver onto each of the three stacks that key an asset differently: DoD
7-pillar ZTA (``project_id``), NSA ZIG (``sha256(hostname)``) and NDC/PVM
(``ni_devices.id``).

``discovery_adapters/`` (rmf-disc-01) — csv, netbox, snmp, ssh and gns3 behind
ONE contract, so ``ni_devices`` is populated by whichever sources a deployment
actually has, with health reported per fabric.
"""
