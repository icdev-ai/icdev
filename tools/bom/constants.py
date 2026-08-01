# CUI // SP-CTI
"""BOM Evidence Engine — closed vocabularies.

This module is the SINGLE source of truth for every enum in the BOM subsystem.
SQL CHECK constraints are DERIVED from these tuples (see db/init_db.py), never
hand-listed: the two drifting apart is how a value Python happily accepts gets
silently rejected by the database at 2am.

Nothing in here is IRAD-specific. Categories are *not* defined here — they are
derived per project from the evidence and the user's stated intent, then
persisted as an approved, versioned taxonomy. A hardcoded category list is
exactly the thing this engine exists to avoid.
"""

# ── Source roles ─────────────────────────────────────────────────────────────
# What a document *is*, which determines what we are allowed to conclude from it.
# The distinction that matters most: a BOM *claims* things; an inventory
# *identifies individual physical units*. Only the latter can falsify a claim.
SOURCE_ROLES: tuple[str, ...] = (
    "bom_claim",             # asserts what should be bought, and for how much
    "inventory_truth",       # enumerates units that verifiably exist (serials)
    "quote",                 # a vendor's priced offer
    "baseline_architecture", # the agreed design; the BOM is checked AGAINST it
    "narrative",             # prose/briefing; context, not line items
    "diagram",               # a drawing that is not the agreed baseline
    "derived",               # a re-representation of another source (a PDF of an XLSX)
)

# ── Source credibility ───────────────────────────────────────────────────────
# Ordered most-trusted first. AI proposes a tier from deterministic signals;
# only a human's designation is BINDING. See credibility.py.
CREDIBILITY_TIERS: tuple[str, ...] = (
    "authoritative",
    "corroborated",
    "working",
    "draft",
    "derived",
    "unknown",
)
DEFAULT_CREDIBILITY = "unknown"

# Rank for ordering; lower wins. Index into CREDIBILITY_TIERS.
CREDIBILITY_RANK: dict[str, int] = {t: i for i, t in enumerate(CREDIBILITY_TIERS)}

CREDIBILITY_SET_BY: tuple[str, ...] = ("human", "ai_proposed", "default")

# ── Price basis ──────────────────────────────────────────────────────────────
# Without this, you average an MSRP figure against a ROM figure and produce a
# number that is not wrong so much as meaningless. Ordered by preference when
# picking a cluster winner.
PRICE_BASES: tuple[str, ...] = (
    "quoted",      # a vendor put it in writing
    "street",      # observed/discounted transaction price
    "msrp",        # list price
    "budgetary",   # vendor's ballpark
    "rom",         # rough order of magnitude; someone's estimate
    "unknown",     # we could not establish one — this is a FINDING, not a default
)
PRICE_BASIS_RANK: dict[str, int] = {b: i for i, b in enumerate(PRICE_BASES)}
DEFAULT_PRICE_BASIS = "unknown"

# ── Cost type / recurrence ───────────────────────────────────────────────────
COST_TYPES: tuple[str, ...] = ("capex", "opex", "unknown")
RECURRENCE_PERIODS: tuple[str, ...] = ("one_time", "month", "quarter", "year")
DEFAULT_RECURRENCE = "one_time"

# ── Line kinds ───────────────────────────────────────────────────────────────
# Not every line in a defensible BOM was read out of a document. Some are
# *computed* — a refresh reserve for kit we already own, a contingency. Those are
# legitimate and often the most important asks in the whole package, but they
# must never be mistaken for something a source asserted. A synthesized line
# always carries its derivation, so a reviewer can see the arithmetic.
LINE_KINDS: tuple[str, ...] = (
    "extracted",     # a source said this
    "reserve",       # we computed this: earmarked funding against a forecast need
    "contingency",   # a percentage buffer
    # Scope we KNOW is coming and have not priced. A placeholder holds the slot
    # in the plan with an honest NULL instead of a fabricated figure. Leadership
    # can earmark against a placeholder; they cannot earmark against silence, and
    # "why is this the first we are hearing of it" is the worst meeting on any
    # programme.
    "placeholder",
)
DEFAULT_LINE_KIND = "extracted"

# ── Declared scope ───────────────────────────────────────────────────────────
# The third coverage question, and the one an evidence-only engine cannot ask.
#
#   Does the BOM fund the agreed design?          -> unfunded_component
#   Does the design justify the BOM spend?        -> unjustified_line
#   Does EITHER cover what we said we would do?   -> scope_declared_*
#
# You cannot detect the absence of something nobody wrote down. So intent is
# promoted to a checkable source: capabilities the customer states are in scope
# are recorded, and then the design and the BOM are held against them. A whole
# workstream that lives only in someone's head is invisible to every document in
# the corpus, and it is exactly the thing that shows up late and unfunded.
SCOPE_STATUSES: tuple[str, ...] = (
    "declared",   # we have said we are doing this. Nothing else exists yet.
    "designed",   # it appears in an architecture, but nothing prices it
    "priced",     # it has BOM lines
    "funded",     # it is inside the committed total
    "deferred",   # consciously out of this ask
)
DEFAULT_SCOPE_STATUS = "declared"

# ── Time-phasing ─────────────────────────────────────────────────────────────
# A BOM is not a lump sum; it is a sequence. Waves are NOT enumerated here —
# they are named per project from the evidence and the user's intent, because
# "Wave 0 / Buildout / Refresh" means nothing to a shipyard or a factory line.
# What IS fixed is that every line can say when it is needed and what it frees up.
#
# The reason this matters more than it looks: an all-or-nothing funding request
# gets deferred; a phased one gets approved. And the cheapest line in the BOM is
# frequently the most valuable, because it is the one that lets people start.
DEFAULT_WAVE_ORDER = 100  # unphased lines sort last, and raise sequencing_absent

# What a line unblocks, in the customer's own words. Free text, deliberately:
# "the team can bring the platform in-house and start coding" is a business
# outcome, and no enum I could invent would hold it.

# ── Asset disposition ────────────────────────────────────────────────────────
# What we intend to DO with hardware we already own. Repurposing is the whole
# point of a salvage fleet — it is avoided CapEx and it should be celebrated on
# the slide, not buried. But kit outside its warranty will fail, and the honest
# ask is to earmark the replacement now rather than discover it later.
ASSET_DISPOSITIONS: tuple[str, ...] = (
    "repurpose",        # use it until it dies. $0 CapEx today.
    "refresh_reserve",  # repurposed AND out of support: fund its eventual replacement now
    "replace_now",      # too old / too small to carry the design
    "retire",           # dispose, do not replace
    "unknown",
)
DEFAULT_ASSET_DISPOSITION = "unknown"

# ── Line lifecycle ───────────────────────────────────────────────────────────
LINE_STATUSES: tuple[str, ...] = (
    "parsed",
    "clustered",
    "pending_review",
    "accepted",
    "overridden",
    "rejected",
    "suppressed",   # kept for audit, excluded from rollups (e.g. a double-count)
)
DEFAULT_LINE_STATUS = "parsed"

# ── Clustering ───────────────────────────────────────────────────────────────
CLUSTER_ROLES: tuple[str, ...] = ("winner", "duplicate", "variant", "excluded")

# How a pair came to be considered the same thing. Auditable: a human should be
# able to ask "why did you merge these?" and get an answer that is not "the model
# said so".
MATCH_METHODS: tuple[str, ...] = (
    "exact_part",   # part numbers matched exactly
    "trigram",      # char-3-gram similarity, manufacturer-gated
    "function",     # same job, different product
    "llm",          # adjudicated in the ambiguous band
    "human",        # someone decided
)

# The verdict vocabulary the LLM adjudicator is confined to. It may return
# NOTHING outside this set, and no number other than a confidence.
MATCH_VERDICTS: tuple[str, ...] = (
    "same_item",
    "same_function_different_item",  # a DECISION, not a merge — see reconcile.py
    "alternative_of",
    "component_of",
    "different",
    "insufficient_evidence",
)

DECISION_ACTORS: tuple[str, ...] = ("human", "llm", "deterministic")

# ── Option groups ────────────────────────────────────────────────────────────
# Mutually exclusive choices. An undecided group contributes ZERO to the
# committed total — never the max, never the mean, never the first one listed.
OPTION_SCOPES: tuple[str, ...] = (
    "line",          # this item or that item
    "table",         # this sub-BOM or that sub-BOM (MVP / Optimal / Top-End)
    "architecture",  # this whole design or that whole design (RKE2 vs VMware)
)

OPTION_DETECTORS: tuple[str, ...] = (
    "heading",             # sibling headings say "Option A" / "Tier 2"
    "versus",              # prose says "X vs Y"
    "structural",          # same functions, disjoint part numbers, wild total delta
    "baseline",            # two baseline_architecture sources
    "human",
)

# ── Findings ─────────────────────────────────────────────────────────────────
SEVERITIES: tuple[str, ...] = ("critical", "high", "medium", "low", "info")
SEVERITY_RANK: dict[str, int] = {s: i for i, s in enumerate(SEVERITIES)}

DISPOSITIONS: tuple[str, ...] = (
    "open",
    "accepted",       # yes, that's a real problem, and we've acted on it
    "waived",         # known and consciously tolerated
    "fixed",
    "not_an_issue",   # false positive
)
DEFAULT_DISPOSITION = "open"

# Whether a finding was reached by arithmetic or by asking a model. This is
# printed next to the finding: a reader is entitled to know which claims depend
# on an LLM and which are simply true.
DETECTORS: tuple[str, ...] = ("deterministic", "llm_assisted")

# Not everything worth surfacing is something wrong. A register that only ever
# reports defects trains people to dread opening it, and — worse — it buries the
# best news in the package. "You already own the machines that unblock the team
# on day one" is the single most persuasive fact in this BOM, and it is not a bug.
FINDING_KINDS: tuple[str, ...] = (
    "defect",       # this is wrong and someone must fix it
    "risk",         # this is not wrong yet
    "decision",     # this cannot be resolved by arithmetic; a human must choose
    "opportunity",  # this is good news that a cost-sorted table would hide
)
DEFAULT_FINDING_KIND = "defect"

# The catalogue of things this engine knows how to find. Every one of these was
# observed in real customer documents; none is hypothetical.
FINDING_TYPES: tuple[str, ...] = (
    # -- arithmetic and structure (deterministic; no model involved) ----------
    "intra_doc_double_count",     # same money consumed by two rollups that both feed the total
    "hardcoded_rollup",           # a "subtotal" that is a typed-in number, not a formula
    "stale_rollup",               # the stored subtotal disagrees with its own inputs
    "unpriced_line_zeroed",       # qty>0, price blank, formula quietly yields 0
    "arithmetic_mismatch",        # extended != qty * unit
    "price_basis_undeclared",
    "price_basis_mismatch",       # a cluster mixes MSRP with ROM
    "capex_opex_conflation",      # a recurring cost sitting in a one-time table
    "duplicate_representation",   # the PDF is a print of the XLSX; do not count both
    # -- owned kit: getting the sustainment ask right --------------------------
    # None of these is an accusation. Repurposing hardware you already own is
    # good engineering and real avoided CapEx. But a reserve is only as correct
    # as the unit count it was sized from, so the count has to be settled.
    #
    # A serial number proves a machine EXISTS. The absence of a serial number
    # proves nothing at all — inventories go stale, and a rack full of real
    # servers can be missing from a spreadsheet. So this engine never concludes
    # that hardware is fictional. It reports that two sources disagree, says
    # which is which, and asks. Deciding for the user here is precisely the kind
    # of confident wrongness the whole product exists to prevent.
    "asset_count_disputed",       # the design leans on N units; the inventory has serials for M.
                                  # EITHER the inventory is incomplete OR the design is over-drawn.
                                  # The tool does not know which, and says so.
    "inventory_incomplete",       # units are in use with no serial record — the inventory needs
                                  # a walk-around, not the BOM a correction
    "unbooked_asset",             # serials exist that no BOM accounts for (free capacity)
    "unverified_existing_asset",  # "$0, we already own it" — with no inventory to size a reserve from
    "unfunded_refresh_reserve",   # repurposed kit past end-of-support with no replacement earmarked
    "no_replacement_price_basis", # we can see it will need replacing; nothing in the corpus prices it

    # -- time, not money ------------------------------------------------------
    # The most valuable line in a BOM is often not the most expensive one. Kit
    # you already own, that lets the team start building on Monday instead of
    # waiting a year for a facility, is worth vastly more than its price — and a
    # BOM sorted only by cost will bury it.
    "schedule_blocker",           # a long-lead item that gates everything behind it
    "unblocks_now",               # zero-cost / already-owned capability that removes a dependency
                                  # on the critical path. Surfaced as an OPPORTUNITY, not a defect.
    "sequencing_absent",          # a large ask with no phasing: all-or-nothing funding requests
                                  # get deferred, phased ones get approved
    # -- the agreed design ---------------------------------------------------
    "unfunded_component",         # it's in the approved architecture; nobody priced it
    "unjustified_line",           # it's in the BOM; the approved architecture never asked for it

    # -- scope we declared but never wrote down ------------------------------
    # An evidence-only engine is blind here by construction: you cannot detect
    # the absence of something nobody put in a document. So intent is checked as
    # a source in its own right.
    "scope_declared_unpriced",    # we said we are doing it; no line prices it
    "scope_declared_undesigned",  # we said we are doing it; no architecture shows it
    "scope_priced_only_by_weak_source",
                                  # the ONLY thing pricing this workstream is a source we do not
                                  # trust much — which reads as "covered" on a spreadsheet and is
                                  # not. Credibility, conformance and scope all meet here.
    "baseline_asset_gap",         # the approved design draws more owned units than the inventory
                                  # records. Same dispute as asset_count_disputed, reached from the
                                  # drawing instead of the spreadsheet — and resolved the same way:
                                  # by asking, not by assuming the drawing is wrong.
    # -- unresolved decisions -------------------------------------------------
    "open_decision",              # a mutually exclusive choice nobody has made
    "price_spread",               # the same thing, priced wildly differently
    "authoritative_conflict",     # two sources you vouched for disagree
    "reopened_decision",          # new evidence would flip a decision you already made
    # -- things hidden in the files ------------------------------------------
    "hidden_content",             # speaker notes, an image of meeting notes, a sheet that renders empty
    "deleted_content",            # a sheet was removed; the numbering still shows the hole
    "unresolved_placeholder",     # "TBD", "still to price" — left in a costed document
    "budget_variance",            # the total does not respect the stated budget envelope
    "taxonomy_divergence",        # the categories in use are not the categories agreed
)

# ── Taxonomy ─────────────────────────────────────────────────────────────────
TAXONOMY_STATUSES: tuple[str, ...] = ("draft", "approved", "superseded")

# Confidence floor for accepting an LLM classification. Below this we return
# nothing rather than a guess — an unclassified line is honest; a
# confidently-miscategorised one is a lie with a progress bar.
TAXONOMY_CONFIDENCE_FLOOR = 0.7

# ── Reconciliation thresholds ────────────────────────────────────────────────
# Above this (with an exact part match) we cluster without asking a model.
AUTO_CLUSTER_SCORE = 0.92
# Below this we discard without asking a model.
DISCARD_SCORE = 0.45
# Between them lies the ambiguous band — the ONLY place the LLM adjudicates.

# A price disagreement this large is never resolved automatically, no matter how
# confident the match. Two firewalls that differ 19x in price are not a rounding
# error; they are a decision someone has to make out loud.
FORCED_REVIEW_PRICE_RATIO = 1.5

# Two sources both marked authoritative that disagree is a real dispute between
# two things the customer vouched for. The tool does not get to pick a side.
NEVER_AUTO_RESOLVE_TIERS: frozenset[str] = frozenset({"authoritative"})

# Structural-equivalence threshold for spotting that N sibling tables are
# alternatives rather than additions.
OPTION_FUNCTION_OVERLAP = 0.60
# Content overlap above which two files are the same document in two formats.
DERIVATIVE_OVERLAP = 0.80

# Preferred representation when the same document arrives more than once.
# Higher wins. A workbook that still has its formulas is worth more than a print
# of that workbook, because the formulas are where the errors hide.
REPRESENTATION_FIDELITY: dict[str, int] = {
    "xlsx_formulas": 100,
    "xlsx": 90,
    "drawio": 85,
    "csv": 70,
    "pptx_tables": 60,
    "docx": 50,
    "pptx": 40,
    "pdf": 30,
    "image": 10,
}
