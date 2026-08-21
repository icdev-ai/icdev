# CUI // SP-CTI
"""The sensitivity model a parent declares -- ONE place for labels the kernel gates on.

Today the classification lattice is hard-coded four times in this tree and the
copies disagree (``tools/compliance/classification_manager.py::
get_clearance_order``, ``tools/security/security_context.py``,
``tools/security/middleware.py`` and ``tools/llm/proxy_gateway.py::
_CLEARANCE_ORDER`` -- SECRET is rank 3 in one and 4 in another, and the LLM
router treats ``LLMRequest.classification`` as an IL level at one site and as a
label at another). ``args/classification_profiles.yaml`` is a per-level
feature-flag profile with no dominance order at all.

This module reads the ``sensitivity:`` block of the parent's
``icdev_domain.yaml`` (xit-decl-01) and answers:

* :func:`label_column`       -- the row-security column (``classification``)
* :func:`labels`             -- the declared labels, LOWEST to HIGHEST
* :func:`default_label`      -- what an unlabelled row or request is
* :func:`is_egress_restricted(label)`
* :func:`rank(label)`        -- position in the declared order (unknown -> None)
* :func:`dominates(a, b)`    -- may clearance ``a`` read rows labelled ``b``
* :func:`rls_exempt_tables`  -- the generated ownership manifest's opt-outs

For ICDEV[IT] the declared order reproduces the runtime ladder the row-security
layer actually enforces today (``public < unclassified < cui < eci < secret <
top_secret < top_secret_sci``); ICDEV[FT] declares ``public < internal < pii <
mnpi < account_secret`` and no IL levels. Labels are compared case-insensitively
with ``//`` and spaces folded to ``_`` (``"TOP SECRET//SCI"`` -> ``top_secret_sci``).

CONSUMERS ARE NOT REWIRED HERE. xit-core-* moves the router's egress gate, the
RLS predicate builder and the citation-grounding default onto this module one
PR at a time, each asserted behaviour-identical for IT. This task ships the
seam and the tests that pin what the seam says.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from icdev.core.domain import Domain, load_domain
from icdev.core.paths import repo_root

#: The order the row-security layer enforces for ICDEV[IT] today, used when a
#: declaration lists no ``order:``; mirrors security_context._CLASSIFICATION_LABELS.
_IT_RUNTIME_ORDER = ("public", "unclassified", "cui", "eci", "secret", "top_secret", "top_secret_sci")


def normalise(label: str | None) -> str:
    if label is None:
        return ""
    s = str(label).strip().lower().replace("//", "_").replace("/", "_").replace(" ", "_").replace("-", "_")
    while "__" in s:
        s = s.replace("__", "_")
    return s


def _domain(domain: Domain | None = None) -> Domain:
    return domain or load_domain()


def label_column(domain: Domain | None = None) -> str:
    return _domain(domain).sensitivity.column or "classification"


def labels(domain: Domain | None = None) -> tuple[str, ...]:
    """Declared labels, lowest to highest. ``order:`` wins; else ``levels``/IT runtime order."""
    dom = _domain(domain)
    raw = (dom.raw.get("sensitivity") or {}) if dom.raw else {}
    order = raw.get("order")
    if order:
        return tuple(normalise(x) for x in order)
    if dom.key == "it":
        return _IT_RUNTIME_ORDER
    # FT and any other domain: egress_restricted are the HIGH end, default the low end
    low = [normalise(dom.sensitivity.default)]
    high = [normalise(x) for x in dom.sensitivity.egress_restricted]
    return tuple(dict.fromkeys(low + high))


def default_label(domain: Domain | None = None) -> str:
    return normalise(_domain(domain).sensitivity.default) or "public"


def is_egress_restricted(label: str | None, domain: Domain | None = None) -> bool:
    """May data carrying ``label`` leave the enclave (reach a remote provider)?

    Unknown labels are restricted -- fail closed, the same answer
    proxy_gateway gives (rank 99).
    """
    dom = _domain(domain)
    n = normalise(label) if label else default_label(dom)
    restricted = {normalise(x) for x in dom.sensitivity.egress_restricted}
    if n in restricted:
        return True
    return n not in labels(dom)


def rank(label: str | None, domain: Domain | None = None) -> int | None:
    order = labels(domain)
    n = normalise(label) if label else default_label(domain)
    return order.index(n) if n in order else None


def dominates(clearance: str | None, row_label: str | None, domain: Domain | None = None) -> bool:
    """May ``clearance`` read a row labelled ``row_label``? Unknown on either side -> False."""
    a, b = rank(clearance, domain), rank(row_label, domain)
    if a is None or b is None:
        return False
    return a >= b


@lru_cache(maxsize=1)
def _manifest_exempt(root: str) -> frozenset[str]:
    import yaml  # noqa: PLC0415

    out: set[str] = set()
    for rel in (Path("icdev") / "core" / "schema" / "tables.yaml", Path("tools") / "db" / "schema" / "tables.yaml"):
        p = Path(root) / rel
        if not p.is_file():
            continue
        try:
            data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        except Exception:  # noqa: BLE001 -- an unreadable manifest exempts nothing
            continue
        out.update(str(t) for t in (data.get("rls_exempt") or []))
    return frozenset(out)


def rls_exempt_tables(domain: Domain | None = None) -> frozenset[str]:
    """Tables the row-security predicate is NOT injected into (manifest ``rls_exempt``)."""
    root = str(_domain(domain).root if domain else repo_root())
    if os.environ.get("ICDEV_RLS_EXEMPT_DISABLE", "").strip().lower() in ("1", "true", "yes"):
        return frozenset()
    return _manifest_exempt(root)


def describe(domain: Domain | None = None) -> dict:
    dom = _domain(domain)
    return {
        "domain": dom.key,
        "column": label_column(dom),
        "default": default_label(dom),
        "labels": list(labels(dom)),
        "egress_restricted": [normalise(x) for x in dom.sensitivity.egress_restricted],
        "levels": list(dom.sensitivity.levels),
        "rls_exempt_tables": sorted(rls_exempt_tables(dom)),
    }
