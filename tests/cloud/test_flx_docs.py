# CUI // SP-CTI
"""The `flx` record: a DATED addendum, an ADR, a feature doc, and rows (flx-docs-01).

WHY A TEST OVER PROSE AT ALL
----------------------------
Three of the assertions here are the ones a documentation card can actually get
WRONG in a way nothing else catches.

* **The addendum must be APPENDED, never a rewrite.** `twx-spk-01` ruled
  LocalStack NO-GO for air-gap on evidence that was correct at the time -- the
  2026 image validates an auth token at container start. floci changed the
  FACT, not the reasoning. A rewritten spike destroys the reasoning that made
  the original call defensible, and the next reader cannot tell a decision that
  was REVERSED from one that was NEVER MADE. So the original verdict text is
  pinned here VERBATIM and the addendum is required to sit strictly AFTER all
  of it.
* **The two standing guards must survive the supersession.** Neither depends on
  which emulator is running: never source a performance, cost or capacity claim
  from emulator timings, and the IAM-sandbox NO-GO. A card that "supersedes a
  spike" and quietly drops the parts that were never about licensing is exactly
  how a guard evaporates.
* **A documented command whose file does not exist is worse than no
  documentation** -- an agent reading it burns a cycle deciding whether the
  tree is broken or the doc is. Every `python ...` line in this card's own
  commands section is RE-DERIVED against the filesystem here, module spellings
  included, rather than trusted.

One assertion is not about documents at all and is here because this card
MEASURED it while writing the feature doc: ``tools/cloud/emulator.py`` declares
the image pin TWICE (``DEFAULT_IMAGE`` for the generators, ``IMAGE`` for the sim
topologies), each documented as the one place the tag lives. They agree today,
which is precisely why a version bump can move one and not the other with no
symptom but an unattributable behaviour difference. Naming it in prose changes
nothing; asserting it turns "they agree today" into a checked fact.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_SPIKE = _ROOT / "docs" / "spikes" / "twx-spk-01-localstack-go-no-go.md"
_ADRS = _ROOT / "docs" / "reference" / "adrs.md"
_FEATURE = _ROOT / "docs" / "features" / "phase-flx-floci-emulator.md"
_COMMANDS = _ROOT / "docs" / "reference" / "commands.md"

#: The heading this card appends under. Everything above it is the spike as it
#: was written; nothing above it may be edited.
_ADDENDUM_MARK = "## Addendum — 2026-09-05"

#: Sentences from the ORIGINAL spike that must survive verbatim. Each is a load-
#: bearing part of the superseded verdict -- if the addendum had been written as
#: a rewrite, these are what would have disappeared.
_ORIGINAL_VERDICT_FRAGMENTS = (
    # The licensing NO-GO itself, which is the half being superseded.
    "**Air-gapped / classified customers: NO-GO.**",
    # Wrapped in the source; the fragment stops at the line break on purpose.
    "the image requires an **auth token** validated at",
    # The IAM-sandbox NO-GO, which is NOT superseded.
    "| 3 | IAM policy sandbox | **NO-GO** |",
    # The performance-claim guard, which is NOT superseded.
    "LocalStack emulates the AWS **API contract**, not AWS **performance",
    # The footprint finding, which is NOT superseded either.
    "### 2. Footprint (against the pure-Python/offline preference)",
)


def _read(path: Path) -> str:
    assert path.exists(), f"{path.relative_to(_ROOT).as_posix()} does not exist"
    return path.read_text(encoding="utf-8")


# ── The spike is APPENDED to, not rewritten ────────────────────────────────

def test_the_spike_carries_a_dated_addendum():
    text = _read(_SPIKE)
    assert _ADDENDUM_MARK in text, (
        "the spike carries no dated addendum -- a supersession with no date "
        "cannot be told from a spike that always said this"
    )


@pytest.mark.parametrize("fragment", _ORIGINAL_VERDICT_FRAGMENTS)
def test_the_original_verdict_text_survives_verbatim(fragment):
    """A rewrite is what this asserts against, one sentence at a time."""
    assert fragment in _read(_SPIKE), (
        f"the original spike text {fragment!r} is gone -- the addendum was "
        "written as a rewrite, which destroys the reasoning that made the "
        "original NO-GO defensible"
    )


@pytest.mark.parametrize("fragment", _ORIGINAL_VERDICT_FRAGMENTS)
def test_every_original_fragment_sits_ABOVE_the_addendum(fragment):
    """Present is not enough: the addendum must be strictly APPENDED.

    A fragment that survived only because it was re-stated INSIDE the new
    section would satisfy the test above while the original body had been
    rewritten around it.
    """
    text = _read(_SPIKE)
    assert text.index(fragment) < text.index(_ADDENDUM_MARK), (
        f"{fragment!r} appears only at or below the addendum heading, so the "
        "original body was edited rather than appended to"
    )


def test_the_supersession_is_scoped_to_LICENSING_and_says_so():
    """The scope is the whole point. floci changed a FACT, not the reasoning."""
    addendum = _read(_SPIKE).split(_ADDENDUM_MARK, 1)[1]
    lowered = addendum.lower()
    assert "licensing question only" in lowered or "licensing question, only" in lowered
    assert "floci" in lowered
    assert "mit" in lowered, "the licence that removed the constraint is not named"


@pytest.mark.parametrize(
    "guard",
    [
        # Guard 1 -- performance/cost/capacity claims. Both halves: the
        # prohibition, and the REASON, which is what makes it re-derivable.
        "performance, cost or capacity claim",
        "An emulator reproduces the AWS **API contract**, not AWS's **performance",
        # Guard 2 -- the IAM sandbox stays NO-GO. A bare "IAM" would match the
        # word anywhere in the section and prove nothing.
        "IAM policy sandbox stays NO-GO",
    ],
)
def test_both_standing_guards_are_carried_forward_in_the_addendum(guard):
    addendum = _read(_SPIKE).split(_ADDENDUM_MARK, 1)[1]
    assert guard in addendum, (
        f"the addendum does not carry {guard!r} forward -- a supersession that "
        "drops the findings that were never about licensing is how a guard "
        "silently evaporates"
    )


def test_the_addendum_does_not_claim_the_footprint_finding_was_superseded():
    """floci is still a multi-hundred-MB Docker container. Only licensing moved."""
    addendum = _read(_SPIKE).split(_ADDENDUM_MARK, 1)[1]
    assert "STANDS UNCHANGED" in addendum or "stands unchanged" in addendum


# ── The ADRs ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("adr", ["D398", "D399", "D400", "D401"])
def test_the_flx_adrs_are_recorded(adr):
    assert f"**{adr}:**" in _read(_ADRS), f"{adr} is not recorded in adrs.md"


def test_the_flx_adrs_live_under_a_phase_heading():
    text = _read(_ADRS)
    assert "### Phase 79 — FLX" in text
    assert text.index("### Phase 79 — FLX") < text.index("**D398:**")


def test_D398_names_D382_as_superseded_on_the_licensing_question_only():
    """The earlier ADR recorded the LocalStack spike outcome. A superseding ADR
    that does not name it leaves two live ADRs saying opposite things."""
    text = _read(_ADRS)
    body = text.split("**D398:**", 1)[1].split("**D399:**", 1)[0]
    assert "D382" in body, "D398 does not name the ADR it supersedes"
    assert "licensing question" in body.lower()
    assert "Batfish" in body, (
        "D382 also carried the Batfish spike outcome; D398 must say that half "
        "is untouched, or a reader takes the whole ADR as retired"
    )


# ── The feature doc ────────────────────────────────────────────────────────

def test_the_feature_doc_exists_at_the_declared_path():
    _read(_FEATURE)


@pytest.mark.parametrize(
    "card",
    ["flx-seam-01", "flx-compose-01", "flx-bridge-02", "flx-studio-01",
     "flx-sim-01", "flx-gen-01", "flx-airgap-01", "flx-airgap-02",
     "flx-airgap-03", "flx-twin-01", "flx-ci-01", "flx-ci-02"],
)
def test_the_feature_doc_accounts_for_every_landed_card(card):
    assert card in _read(_FEATURE), f"{card} landed and the feature doc omits it"


@pytest.mark.parametrize("sibling", ["flx-az-01", "flx-gcp-01", "flx-oci-01"])
def test_the_unbuilt_siblings_are_named_as_unbuilt(sibling):
    """An emulator project that quietly implies four CSPs ship is the exact
    'declared but never consumed' shape this repo keeps finding."""
    text = _read(_FEATURE)
    assert sibling in text, f"{sibling} is not mentioned at all"
    tail = text.split("## Not built, on purpose", 1)
    assert len(tail) == 2, "the feature doc has no 'Not built, on purpose' section"
    assert sibling in tail[1], f"{sibling} is not listed as unbuilt"


def test_the_feature_doc_carries_both_standing_guards():
    text = _read(_FEATURE)
    assert "performance, cost or capacity claim" in text
    assert "IAM policy sandbox stays NO-GO" in text


# ── The commands section, re-derived against the filesystem ────────────────

_COMMANDS_SECTION_MARK = "## Floci Cloud Emulator — the rest of the surface (flx-docs-01)"


def _commands_section() -> str:
    text = _read(_COMMANDS)
    assert _COMMANDS_SECTION_MARK in text, "this card's commands section is missing"
    tail = text.split(_COMMANDS_SECTION_MARK, 1)[1]
    # Stop at the next top-level heading, if any.
    nxt = re.search(r"^## ", tail, flags=re.MULTILINE)
    return tail[: nxt.start()] if nxt else tail


def _documented_targets() -> list[tuple[str, Path]]:
    """Every `python tools/x.py` and `python -m tools.x` in this card's section,
    resolved to the file it names."""
    section = _commands_section()
    out: list[tuple[str, Path]] = []
    for m in re.finditer(r"python\s+(tools/[\w/]+\.py)", section):
        out.append((m.group(0), _ROOT / m.group(1)))
    for m in re.finditer(r"python\s+-m\s+(tools(?:\.[\w]+)+)", section):
        rel = Path(*m.group(1).split("."))
        out.append((m.group(0), _ROOT / rel.with_suffix(".py")))
    return out


def test_the_section_actually_documents_some_commands():
    """Guard the derivation. An empty walk passes every check below vacuously."""
    assert len(_documented_targets()) >= 4, (
        "no `python ...` invocations were extracted -- the check below would "
        "pass over nothing"
    )


def test_every_documented_command_names_a_file_that_exists():
    """CLAUDE.md's rule, applied to this card's own section.

    A `-m` spelling is the trap: `tools.studio.sim.gns3_sim` reads perfectly
    and the module lives at `tools/studio/executors/gns3_sim.py`.
    """
    missing = [cmd for cmd, path in _documented_targets() if not path.exists()]
    assert not missing, f"documented commands whose file does not exist: {missing}"


def test_the_commands_section_carries_both_standing_guards():
    section = _commands_section()
    assert "performance, cost or capacity claim" in section
    assert "IAM policy sandbox stays NO-GO" in section


# ── Manifest rows, in BOTH trees ───────────────────────────────────────────

_MANIFEST_ROWS = [
    ("databridge.md", "tools/databridge/connectors/floci_connector.py"),
    ("design-canvases.md", "tools/infra_canvas/adapters/floci_adapter.py"),
]


@pytest.mark.parametrize("shard,module", _MANIFEST_ROWS)
@pytest.mark.parametrize("tree", ["tools", "icdev/tools"])
def test_the_floci_modules_are_registered_in_their_topic_shard(shard, module, tree):
    """Both trees: a row added to `tools/` only leaves the wheel shipping a
    manifest that does not mention the module it ships."""
    path = _ROOT / tree / "manifest" / shard
    assert module in _read(path), f"{module} is not registered in {tree}/manifest/{shard}"
    assert (_ROOT / module).exists(), f"{module} was registered and does not exist"


# ── The finding this card measured while writing the feature doc ───────────

def test_the_emulator_declares_ONE_image_pin_or_the_two_agree():
    """`DEFAULT_IMAGE` (flx-gen-01) and `IMAGE` (flx-sim-01) are two independent
    constants in one module, each documented as the one place the tag lives.

    They agree today, so nothing is broken -- which is why the next version bump
    can move one and not the other with no symptom but an unattributable
    behaviour difference between a generated customer compose and a canvas sim.
    Collapsing them to one constant is a code change with its own red-first
    proof; this asserts the property that change would preserve.
    """
    from tools.cloud import emulator

    assert emulator.IMAGE == emulator.DEFAULT_IMAGE, (
        "the two image pins in tools/cloud/emulator.py have drifted: "
        f"IMAGE={emulator.IMAGE!r} DEFAULT_IMAGE={emulator.DEFAULT_IMAGE!r}. "
        "The generated customer compose and the canvas sim topologies would "
        "now run different emulator versions."
    )
    assert ":latest" not in emulator.IMAGE
