# CUI // SP-CTI
"""Task-prefix scoping for project progress cards.

`args/projects.yaml` allows nested task prefixes — `aadc-` alongside
`aadc-enh-` and `aadc-sp-`. That is a legitimate parent/child namespace, but a
naive `id LIKE 'aadc-%'` query makes the parent absorb every child's rows.

The historical handling was to detect the overlap and DROP the colliding entry,
which silently hid whichever card appeared later in the YAML file (the 38-epic
`aadc` card, in practice). The correct treatment is to keep both cards and
subtract the children from the parent's queries.

Pure functions only — no DB, no Flask, no config reads — so the rule is unit
testable independently of the dashboard.
"""
from __future__ import annotations

from typing import Iterable


def child_prefixes(prefix: str, all_prefixes: Iterable[str]) -> list[str]:
    """Return the prefixes in `all_prefixes` strictly nested under `prefix`.

    A prefix is "nested under" another when it starts with it and is not equal
    to it: `aadc-enh-` is nested under `aadc-`. The result is what a parent
    project must EXCLUDE from its task queries.

    Returns a sorted list so generated SQL and its bound parameters are stable
    across runs (deterministic queries are cacheable and diffable).

    >>> child_prefixes("aadc-", ["aadc-", "aadc-enh-", "aadc-sp-", "agx-"])
    ['aadc-enh-', 'aadc-sp-']
    >>> child_prefixes("aadc-enh-", ["aadc-", "aadc-enh-"])
    []
    >>> child_prefixes("", ["aadc-"])
    []
    """
    if not prefix:
        return []
    return sorted(
        other
        for other in all_prefixes
        if other and other != prefix and other.startswith(prefix)
    )


def duplicate_prefixes(all_prefixes: Iterable[str]) -> list[str]:
    """Return prefixes appearing more than once — genuinely unresolvable.

    Nesting is resolvable by subtraction; an EXACT duplicate is not, because
    there is no predicate that assigns a row to one project over the other.
    Callers should skip the later entry and warn.

    >>> duplicate_prefixes(["a-", "b-", "a-"])
    ['a-']
    >>> duplicate_prefixes(["a-", "a-b-"])
    []
    """
    seen: set = set()
    dupes: set = set()
    for p in all_prefixes:
        if not p:
            continue
        if p in seen:
            dupes.add(p)
        seen.add(p)
    return sorted(dupes)
