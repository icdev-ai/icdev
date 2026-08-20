# CUI // SP-CTI
"""rem-tst-05: setting `abstained` must DROP the prose, not merely label it.

THE CONTRACT, stated by the consumer that depends on it. The shared citation
gate (`tools/quality/citation_grounding.py`) skips any section flagged
abstained, and `regen_quality_gate._section_dicts` says why in its own comment:
"Abstained sections make no claims and are dropped (they carry the
'(Abstained — ...)' sentinel, not real prose)."

So a producer that sets the flag and leaves live prose behind creates the worst
combination available: the text is exempted from the citation check AND
persisted. It ships unexamined and unverified.

THREE SITES HAD IT. `doc_generator` sets `abstained` in six places. Two were
correct from the start, one was the very-low-confidence band, and TWO were the
currency guard's `abstain` action — the setting CLAUDE.md describes as
"`on_deprecated: abstain` drops the prose instead". It did not: a draft naming a
deprecated entity was flagged and persisted, which is precisely the outcome that
guard exists to prevent.

This file pins the contract STRUCTURALLY rather than case by case, because the
defect arrived three times in one module and a fourth site would arrive the same
way — by someone setting a flag and not knowing a distant consumer reads it.
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

import tools.document_intelligence.doc_generator as dg

#: How far after `abstained = True` the prose replacement may appear. Generous
#: on purpose: the point is to catch a site with NO replacement at all, not to
#: police formatting.
_WINDOW = 8

#: A site that constructs the section fresh with no `content=` is safe — there
#: is no prose to drop.
_CONSTRUCTS_EMPTY = "GeneratedSection(heading=heading, abstained=True"


def _abstain_sites(src: str):
    """(line_number, following_window) for every place the flag is set."""
    lines = src.splitlines()
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("abstained = True") or "abstained=True" in stripped:
            yield i + 1, "\n".join(lines[i:i + _WINDOW])


def test_every_abstain_site_drops_the_prose():
    """The whole contract, as one assertion over the module.

    A site that sets the flag without replacing the text is exempt from the
    citation gate and still persisted — which is how an uncited section, and
    separately a section naming a deprecated entity, both reached a document
    while the gate reported no findings.
    """
    src = inspect.getsource(dg)
    offenders = []
    for lineno, window in _abstain_sites(src):
        if _CONSTRUCTS_EMPTY in window:
            continue                      # no content to drop
        if "(Abstained" in window:
            continue                      # prose replaced
        offenders.append(lineno)
    assert not offenders, (
        "doc_generator sets `abstained` without dropping the prose at line(s) "
        f"{offenders}. A consumer that trusts the flag — the shared citation "
        "gate does — will skip that section while it is still persisted."
    )


def test_the_module_really_has_abstain_sites_to_check():
    """A structural test that silently matched nothing would pass forever. If
    the flag is ever renamed, this fails and sends a reader here."""
    assert len(list(_abstain_sites(inspect.getsource(dg)))) >= 4


def test_the_consumer_this_protects_still_skips_abstained_sections():
    """The contract only matters because something acts on it. If the shared
    gate ever stopped skipping, this file would be guarding nothing — and the
    reader should be told, rather than left with a green test."""
    from tools.quality import citation_grounding as cg

    src = inspect.getsource(cg)
    assert 'abstained' in src, (
        "the shared citation gate no longer mentions abstained — re-check "
        "whether this contract is still load-bearing")


def test_the_regen_gate_still_documents_why_it_drops_them():
    """`_section_dicts` is where the assumption is written down. Losing that
    comment loses the only place a reader learns the flag is load-bearing."""
    from tools.doc_modernization import regen_quality_gate as qg

    src = inspect.getsource(qg._section_dicts)
    assert "Abstained" in src and "sentinel" in src


def test_the_currency_abstain_names_what_it_dropped():
    """A sentinel that says only "abstained" sends a reviewer back to the logs.
    `on_deprecated: abstain` fires on a specific entity, so the replacement says
    which — that is the whole diagnostic."""
    src = inspect.getsource(dg)
    assert "deprecated or superseded entities" in src


def test_no_site_is_hidden_behind_a_different_spelling():
    """AST rather than text: `setattr(x, 'abstained', True)` or a dict literal
    would pass the line scan above. Catching the shape here means the structural
    test cannot be sidestepped by a rename."""
    tree = ast.parse(Path(inspect.getfile(dg)).read_text(encoding="utf-8"))
    suspicious = [
        n.lineno for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name) and n.func.id == "setattr"
        and len(n.args) >= 2 and isinstance(n.args[1], ast.Constant)
        and n.args[1].value == "abstained"
    ]
    assert not suspicious, (
        f"`abstained` set via setattr at line(s) {suspicious} — the structural "
        "check above scans assignments and would not see it")
