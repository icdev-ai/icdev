# CUI // SP-CTI
"""Constants for AI-assisted vulnerability discovery (icx-ent-01).

The ledger's vocabulary, the declared templates and the surfaces they decide. The
SQL CHECK constraints in the migration (20261118231119_vr_events) are derived from
these tuples, never typed twice.
"""
from __future__ import annotations

TABLE = "vr_events"
GENESIS = "0" * 64
CLASSIFICATION = "CUI // SP-CTI"
CONFIG_RELPATH = "args/vuln_research.yaml"

#: One hash chain per stream: ``target:<id>``, ``run:<id>``, ``patch:<finding id>``,
#: ``bundle:<target id>``.
STREAM_KINDS = ("target", "run", "patch", "bundle")

EVENTS = (
    # a target's life
    "registered", "authorised", "refused", "revoked",
    # one discovery run (the whole result, at its digest)
    "run_recorded",
    # a measurement on the labelled benchmark
    "measured",
    # a patch proposal and a person's review
    "patch_proposed", "patch_accepted", "patch_rejected",
    # the findings bundle: prepared, then a person publishes or refuses
    "prepared", "published", "bundle_refused",
)
ACTOR_KINDS = ("person", "agent", "bootstrap")

# Declared decision templates (args/workflow_hitl_templates/vr-*.yaml) and the surfaces
# (Flask endpoint ids) each decides.
TEMPLATE_AUTHORISE = "decl-wft-vr-target-authorise"
TEMPLATE_PATCH = "decl-wft-vr-patch-review"
TEMPLATE_PUBLISH = "decl-wft-vr-findings-publish"
SURFACE_AUTHORISE = "vulnresearch.vr_api_target_authorise"
SURFACE_PATCH = "vulnresearch.vr_api_patch_review"
SURFACE_PUBLISH = "vulnresearch.vr_api_bundle_publish"

# Candidate outcomes. Only ``finding`` is a finding; every other is a dropped lead,
# counted by its reason.
FINDING = "finding"
DROP_REASONS = (
    "not_reproduced",      # the PoV did not fire
    "tautology",           # the oracle fired on the benign control too
    "indecisive",          # the sandbox could not decide (error, timeout)
    "sandbox_unavailable",  # no container engine: nothing could be replayed
    "no_recipe",           # no PoV recipe for its weakness class
    "no_harness",          # the function cannot be called with one input
    "out_of_scope",        # names a file or function outside the target
)
SOURCES = ("sast", "sca", "agent", "fuzz")

BUNDLE_SCHEMA = "icos-findings/v1"
BUNDLE_SCHEMA_RELPATH = "context/icos/findings.schema.json"
PUBLICATION_SCHEMA = "icos-findings-publication/v1"
PUBLICATION_RELPATH = "context/icos/findings-publication.schema.json"
PRODUCER_SYSTEM = "icdev-it"
#: ICDEV[OS]'s registered system id (its findings contract's ``target.system_id``): a name it would refuse never
#: reaches a bundle.
SYSTEM_ID = r"^[a-z][a-z0-9-]{1,62}$"
PRODUCER_TOOL = "icdev-it/vulnresearch"

# IQE (the canvas completeness gate, point 8): tools/iqe/adapters/vulnresearch.py registers these.
IQE_CANVAS = "vulnresearch"
IQE_COLLECTIONS = ("vulnresearch.findings", "vulnresearch.targets")
IQE_API_ROUTE = "/vulnresearch/api/iqe-query"
IQE_EXAMPLES = (
    {"label": "Critical findings", "query": "Show critical findings"},
    {"label": "Unpatched findings", "query": "Which findings have no accepted patch?"},
    {"label": "Targets awaiting authorisation", "query": "Which targets are awaiting authorisation?"},
)
