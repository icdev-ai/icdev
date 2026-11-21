# CUI // SP-CTI
"""Constants of the evidence collectors (icx-ent-02)."""
from __future__ import annotations

CONFIG_RELPATH = "args/evidence_collectors.yaml"
CONFIG_SCHEMA = "icdev-evidence-collectors/v1"

#: ICDEV[OS]'s objective-evidence contract (osx-ent-02, contracts/schemas/objective-evidence.schema.json at contract
#: SCHEMA_CONTRACT), vendored verbatim. A changed file is refused, never silently used. Re-pinned to ICDEV[OS]
#: main after osx-ent-02 merged (merge cfe0b91e).
SCHEMA = "icos-objective-evidence/v1"
SCHEMA_RELPATH = "context/icos/objective-evidence.schema.json"
SCHEMA_SHA256 = "adea74c3cc1e384f7fae9250098e6460c7966467e1ebc48b75bc651273c563d7"
SCHEMA_CONTRACT = "1.199.0"
#: The fields ICDEV[OS]'s Publication model admits (extra fields are refused there): the publication is EMBEDDED in
#: the report (its ``publication`` field), the placement icdev_os/publication.py ``split(raw, "embedded")`` reads.
PUBLICATION_FIELDS = ("prepared_by", "signed_by", "step_id", "decided_at", "content_digest")

PRODUCER = "icdev-it/evidence_collectors"
TABLE = "ec_events"
GENESIS = "0" * 64
EVENTS = ("prepared", "published", "refused", "step_lost")
ACTOR_KINDS = ("person", "agent", "bootstrap")

TEMPLATE = "decl-wft-ec-evidence-publish"
SURFACE = "evidence_collectors.ec_api_publish"

#: The four kinds of collector the card asks for, each a real, named integration (collectors/*.py).
KINDS = ("cloud_config", "identity_provider", "endpoint_mdm", "siem")

#: Markings a system may carry; the order is ICDEV's clearance order (tools/compliance/classification_manager.py).
#: An unknown marking or clearance fails closed.
MARKINGS = ("PUBLIC", "CUI", "SECRET", "TOP SECRET")

#: What an agent reading through MCP is cleared for: the MCP results' own marking (CUI // SP-CTI).
MCP_CLEARANCE = "CUI"

#: Credentials are read only BY REFERENCE from the customer's vault or the OS keyring.
SECRET_SCHEMES = ("keyring:", "vault:")

FACT_TRUE, FACT_FALSE = True, False
RESULTS = ("satisfied", "other_than_satisfied")

# IQE (the canvas completeness gate, point 8): tools/iqe/adapters/evidence_collectors.py registers the collection.
IQE_CANVAS = "evidence_collectors"
IQE_COLLECTIONS = ("evidence_collectors.objectives",)
IQE_EXAMPLES = (
    {"label": "Other than satisfied", "query": "Show objectives observed other than satisfied"},
    {"label": "All published objectives", "query": "Show all evidence_collectors objectives"},
)
