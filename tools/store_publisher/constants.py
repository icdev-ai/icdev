# CUI // SP-CTI
"""Constants of the store publisher (icx-ent-04)."""
from __future__ import annotations

CONFIG_RELPATH = "args/store_publisher.yaml"
CONFIG_SCHEMA = "icdev-store-publisher/v1"

#: ICDEV[OS]'s store contracts (osx-ent-11; contracts/schemas/store-package.schema.json and
#: store-publication.schema.json), vendored verbatim from ICDEV[OS] main (ba4e0a56, contract 1.207.0). A changed file is
#: refused, never silently used.
SCHEMA = "icos-store-package/v1"
SCHEMA_RELPATH = "context/icos/store-package.schema.json"
SCHEMA_SHA256 = "07503b4e5468427b36c9582ccf6af6a3f555e74e3c966bbc8900231007293efa"
PUBLICATION_SCHEMA = "icos-store-publication/v1"
PUBLICATION_RELPATH = "context/icos/store-publication.schema.json"
PUBLICATION_SHA256 = "2ecf86c73ec41cff5a57707f71a2a67b3d52f97115ffa86bcfb9055096ae4795"
SCHEMA_CONTRACT = "1.207.0"

#: The Agent Card metadata key that binds the card to the package's content (ICDEV[OS] icdev_os/store/package.BINDING).
BINDING = "icos.store/package_digest"

#: ICDEV[OS] pins the step a publication is taken in (``icdev_os/store/package.TEMPLATE``): ICDEV[IT]'s declared
#: template carries exactly that id, so the publication names the template the step was really taken in.
TEMPLATE = "icos-store-publish"
SURFACE = "store_publisher.sp_api_publish"

KINDS = ("agent", "crew", "skill", "mcp_server", "plugin")
#: The ICDEV[OS] ladder a package is marked on. A package may claim no ceiling that needs an accreditation (cui and
#: above): raising a ceiling is the owner's, never a package's, so the packager refuses one rather than export it.
ICOS_MARKINGS = ("public", "internal")
#: Who in ICDEV[IT] may see a package of each ICDEV[OS] marking (ICDEV's clearance order): ``internal`` is not public,
#: so it needs the next level up.
ICDEV_CLEARANCE_FOR = {"public": "PUBLIC", "internal": "CUI"}
CLEARANCES = ("PUBLIC", "CUI", "SECRET", "TOP SECRET")
MCP_CLEARANCE = "CUI"

#: ICDEV's seven marketplace scanner gates (tools/marketplace/asset_scanner.py), as ICDEV[OS]'s review lists them
#: (icdev_os/store/review.SCANNER).
SCANNER = ("sast_scan", "secret_detection", "dependency_audit", "cui_marking_validation", "sbom_generation",
           "supply_chain_provenance", "digital_signature")
#: What a gate may report. ``unverified``: it could not run, or ran on nothing it needs -- never a pass.
#: ``not_applicable``: there is nothing of its kind in the package (an agent, crew or skill carries no code).
GATE_STATUSES = ("pass", "fail", "warning", "unverified", "not_applicable")

#: The OWASP ids each kind's red-team report must answer (ICDEV[OS] args/store.yaml redteam.required).
OWASP_ALL = tuple(f"ASI{n:02d}" for n in range(1, 11)) + tuple(f"LLM{n:02d}" for n in range(1, 11))
REDTEAM_REQUIRED = {"agent": OWASP_ALL, "crew": OWASP_ALL,
                    "skill": ("ASI01", "ASI02", "ASI06", "LLM01", "LLM02", "LLM07"),
                    "mcp_server": ("ASI01", "ASI02", "ASI03", "ASI04", "ASI05", "LLM01", "LLM02", "LLM05", "LLM06"),
                    "plugin": ()}
REDTEAM_MAX_AGE_D = 180
DEPENDENCY_MAX_AGE_D = 30
BLOCKING_SEVERITIES = ("critical", "high")

PRODUCER = "icdev-it/store_publisher"
TABLE = "sp_events"
GENESIS = "0" * 64
EVENTS = ("prepared", "published", "refused")
ACTOR_KINDS = ("person",)

IQE_CANVAS = "store_publisher"
