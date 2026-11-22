# CUI // SP-CTI
"""Constants of the signed factory builds (icx-ent-03)."""
from __future__ import annotations

CONFIG_RELPATH = "args/factory_builds.yaml"
CONFIG_SCHEMA = "icdev-factory-builds/v1"

#: ICDEV[OS]'s customer artefact contract (osx-ent-06, contracts/schemas/artefact.schema.json), vendored verbatim.
#: Pinned from ICDEV[OS] main (commit 9040bf4c, contract 1.208.0, osx-ent-06 merged). A changed file is refused,
#: never silently used.
SCHEMA = "icos-artefact/v1"
SCHEMA_RELPATH = "context/icos/artefact.schema.json"
SCHEMA_SHA256 = "cdc80df7a7565c23aa005342f6b615eda249788bf6de946f41487ef1458a04aa"
SCHEMA_CONTRACT = "1.208.0"
#: The draft publication contract ICDEV[IT] proposes for ICDEV[OS] to adopt (osx-ent-06 checks a publication with
#: icdev_os/publication.py check_publication and no model yet: publication_template null until this card ships).
PUBLICATION_SCHEMA = "icdev-build-publication/v1"
PUBLICATION_RELPATH = "context/icos/build-publication.schema.json"
PUBLICATION_SHA256 = "18e0056f111d0ac03d9de8cb7100fed8c46844d6c33d6e4b78f7a8873c834985"
PUBLICATION_FIELDS = ("schema", "content_digest", "signed_by", "role", "prepared_by", "step_id", "template",
                      "decided_at", "event_id", "row_hash", "producer")
TRANSFER = "icos-release-transfer/v1"
MANIFEST = "artefact.json"
SBOM, PROVENANCE, VEX = "sbom.cdx.json", "provenance.slsa.json", "vex.openvex.json"
MODEL_SIG = "bundles/model.sigstore.json"

CYCLONEDX_DIR = "context/standards/cyclonedx-1.7"
OPENVEX_DIR = "context/standards/openvex/0.2.0"

# Sigstore / in-toto / DSSE / predicates (each as ICDEV[OS] osx-ent-05/06 and cosign v3 use it)
BUNDLE_MEDIA_TYPE = "application/vnd.dev.sigstore.bundle.v0.3+json"
PAYLOAD_TYPE = "application/vnd.in-toto+json"
STATEMENT = "https://in-toto.io/Statement/v1"
SIGN_PREDICATE = "https://sigstore.dev/cosign/sign/v1"
SBOM_PREDICATE = "https://cyclonedx.org/bom"
SLSA_PREDICATE = "https://slsa.dev/provenance/v1"           # SLSA v1.2 still uses this predicate type
VEX_PREDICATE = "https://openvex.dev/ns"
MODEL_PREDICATE = "https://model_signing/signature/v1.0"
SBOM_MEDIA_TYPE = "application/vnd.cyclonedx+json; version=1.7"
VEX_MEDIA_TYPE = "application/vnd.openvex+json"
ML_MODEL = "machine-learning-model"
BUNDLES = {"signature": ("bundles/signature.sigstore.json", SIGN_PREDICATE),
           "sbom-attestation": ("bundles/sbom.sigstore.json", SBOM_PREDICATE),
           "provenance-attestation": ("bundles/provenance.sigstore.json", SLSA_PREDICATE),
           "vex-attestation": ("bundles/vex.sigstore.json", VEX_PREDICATE)}
KINDS = ("image", "model", "agent", "iac")
SUBJECT_MEDIA = {"model": "application/vnd.icos.model-weights.v1", "agent": "application/vnd.icos.agent-package.v1+tar",
                 "iac": "application/vnd.icos.iac-bundle.v1+tar"}
PROMOTED = ("promoted", "auto_promoted", "override_promoted")
DEMOTED = ("demoted", "override_demoted")

PRODUCER = "icdev-it/factory_builds"
TABLE = "fb_events"
GENESIS = "0" * 64
EVENTS = ("prepared", "published", "refused")
ACTOR_KINDS = ("person",)

TEMPLATE = "decl-wft-fb-build-publish"
SURFACE = "factory_builds.fb_api_publish"

#: Markings a target may carry, in ICDEV's clearance order. An unknown marking or clearance fails closed.
MARKINGS = ("PUBLIC", "CUI", "SECRET", "TOP SECRET")
#: What an agent reading through MCP is cleared for: the MCP results' own marking.
MCP_CLEARANCE = "CUI"

IQE_CANVAS = "factory_builds"
IQE_COLLECTIONS = ("factory_builds.builds",)
IQE_EXAMPLES = (
    {"label": "Published builds", "query": "Show all factory_builds builds"},
    {"label": "Model builds", "query": "Show factory_builds builds where kind is model"},
)
