# CUI // SP-CTI
"""The four read-only collectors (icx-ent-02), by name. Each module declares NAME, KIND, INTEGRATION, FACTS,
CREDENTIALS (references it needs) and ``collect(system, spec, *, now, max_age_h, resolve) -> [observation]``."""
from __future__ import annotations

from tools.evidence_collectors.collectors import aws_config, intune, oidc_scim, splunk

REGISTRY = {m.NAME: m for m in (aws_config, oidc_scim, intune, splunk)}

#: Every fact any collector can observe -> the collector that observes it.
FACT_OWNER = {f: m.NAME for m in REGISTRY.values() for f in m.FACTS}
