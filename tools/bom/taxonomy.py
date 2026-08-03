# CUI // SP-CTI
"""The category scheme, derived rather than assumed.

`categorize.py` gets a taxonomy out of the documents for free, and when the
documents have sections that is the right answer — it is the scheme the person who
owns the scope already wrote. But a single-sheet BOM has no sections, and the
keyword backstop is a GovCon CLIN classifier: it knows hardware from labour, and it
files a 100GbE switch and a rack PDU under the same word. Six of fourteen lines
landing in "Other" is not a taxonomy, it is a shrug.

Naming the buckets is genuinely a judgement — "is a hypervisor licence Software or
Virtualisation?" has no deterministic answer, only a conventional one — and that is
what a model is actually good at. So the model gets that job, and only that job.

**THE CAGE.** It is enforced here, in code, not in the prompt:

  1. The model proposes LABELS and DEFINITIONS. There is no numeric field anywhere
     in the schema, so it never sees a price while deciding, and cannot anchor on
     one.
  2. Any string it returns that contains a currency figure VOIDS the response
     entirely. A model that starts talking about money during a naming task has
     stopped doing the naming task.
  3. Assignment is SELECT-ONLY. The model may pick a label from the approved list;
     a label it invents at assignment time is discarded and that line falls to the
     deterministic backstop. It cannot grow the taxonomy by classifying into it.
  4. It never assigns a price, a quantity, or a total. It maps a line_id to a
     label. That is the whole contract.
  5. **It cannot approve itself.** A proposed taxonomy is `status="proposed"` and
     binds nothing. A human approves it, and the approved version is what later
     uploads classify against — so the categories, and therefore every number
     rolled up under them, hold still across leadership reviews.

Public API::

    propose(lines)                  -> Taxonomy      # status="proposed"
    approve(tax)                    -> Taxonomy      # status="approved", version+1
    classify_lines(lines, tax)      -> {line_id: label}
    save(tax, path) / load(path)
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

from tools.bom.categorize import _from_keywords
from tools.bom.lines import ExtractedLine

# Anything that looks like money in a naming task is a red flag, not a value.
_MONEY = re.compile(r"[$£€]\s*\d|(?:\d[\d,]*\.?\d*)\s*(?:usd|dollars|k\b|m\b)", re.I)

# A category label is a NAME. Digits in it usually mean the model has started
# copying part numbers or prices into the taxonomy.
_LABEL_OK = re.compile(r"^[A-Za-z][A-Za-z &/'\-,\.]{2,48}$")

MAX_CATEGORIES = 14
BATCH = 40
FALLBACK = "Other"

# No numeric property. Not one. The model is naming things, and a schema that lets
# it emit a number is a schema that invites it to.
PROPOSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "categories": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string"},
                    "definition": {"type": "string"},
                },
                "required": ["label", "definition"],
            },
        },
    },
    "required": ["categories"],
}

ASSIGN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "assignments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "line_id": {"type": "string"},
                    "label": {"type": "string"},
                },
                "required": ["line_id", "label"],
            },
        },
    },
    "required": ["assignments"],
}


@dataclass
class Category:
    label: str
    definition: str = ""


@dataclass
class Taxonomy:
    categories: list[Category] = field(default_factory=list)
    version: int = 1
    status: str = "proposed"          # proposed | approved
    derived_by: str = "llm"           # llm | human | deterministic
    note: str = ""

    @property
    def labels(self) -> list[str]:
        return [c.label for c in self.categories]

    @property
    def binds(self) -> bool:
        """Only a human's approval binds. Same contract as everything else here."""
        return self.status == "approved"

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "status": self.status,
            "derived_by": self.derived_by,
            "note": self.note,
            "categories": [{"label": c.label, "definition": c.definition}
                           for c in self.categories],
        }


def _voids(*texts: str) -> bool:
    return any(_MONEY.search(t or "") for t in texts)


def _sample(lines: Sequence[ExtractedLine], n: int = 120) -> str:
    """What the model sees: descriptions ONLY.

    No prices, no quantities, no totals. It cannot anchor on a number it was never
    shown, and this is cheaper to guarantee than to detect.
    """
    seen, out = set(), []
    for ln in lines:
        d = (ln.description or "").strip()
        key = d.lower()
        if d and key not in seen:
            seen.add(key)
            out.append(f"- {d[:90]}")
        if len(out) >= n:
            break
    return "\n".join(out)


def propose(
    lines: Iterable[ExtractedLine],
    *,
    intent: str = "",
    min_categories: int = 6,
    ctx: Any = None,
) -> Taxonomy:
    """Ask for a category scheme. It binds nothing until a human approves it.

    ``intent`` is the user's own description of what this BOM is FOR, and it is the
    single most useful thing you can give the model. Item descriptions alone say
    what each line IS; they do not say how the person paying for it wants to see it
    grouped — and "how do you want to see it grouped" is the entire question. The
    same 129 lines are a compute/network/software split to an engineer and a
    capital/subscription/services split to a budget owner, and neither is wrong.

    Without an intent the model free-associates a scheme from the items, which is
    why two runs over the same corpus can return twelve categories and then four.
    That variance is also why the result is PROPOSED: a human approves one, it is
    versioned, and every later upload classifies against it — so the categories,
    and every total rolled up under them, hold still between reviews.
    """
    from tools.cortex import api as cortex

    lines = list(lines)
    if not lines:
        return Taxonomy(categories=[], derived_by="deterministic",
                        note="no lines to categorise")

    steer = (
        f"\nWHAT THIS BILL OF MATERIALS IS FOR (use this to decide how to group):\n"
        f"{intent.strip()}\n"
        if intent.strip() else ""
    )

    prompt = (
        "Below are item descriptions from a bill of materials. Propose a category "
        f"scheme of between {min_categories} and {MAX_CATEGORIES} categories that "
        f"partitions them.\n"
        f"{steer}\n"
        "Rules:\n"
        "- Categories must be the kind of grouping a budget owner reasons about "
        "(e.g. compute hardware, network hardware, software licences, security, "
        "facilities, facility outfitting, professional services).\n"
        "- Every item must plausibly belong to exactly one category.\n"
        "- Prefer the vocabulary of the domain over the wording of this list.\n"
        "- Do NOT mention prices, quantities or totals. You have not been shown "
        "any.\n\n"
        f"ITEMS:\n{_sample(lines)}"
    )

    try:
        res = cortex.extract(prompt, PROPOSE_SCHEMA, ctx)
    except Exception as exc:                       # offline, air-gap, exhausted
        return Taxonomy(categories=[], derived_by="deterministic",
                        note=f"llm unavailable: {exc}")

    if not res.metadata.get("schema_valid", True):
        return Taxonomy(categories=[], derived_by="deterministic",
                        note="model did not return the schema")

    try:
        payload = json.loads(res.text)
    except (ValueError, TypeError):
        return Taxonomy(categories=[], derived_by="deterministic",
                        note="model returned unparseable output")

    raw = payload.get("categories") or []

    # THE CAGE. A single money figure anywhere voids the whole response — a model
    # that starts talking about cost during a naming task has stopped doing the
    # naming task, and there is no way to know what else it did instead.
    if _voids(*[str(c.get("label", "")) for c in raw],
              *[str(c.get("definition", "")) for c in raw]):
        return Taxonomy(categories=[], derived_by="deterministic",
                        note="response mentioned money and was discarded")

    cats: list[Category] = []
    seen: set[str] = set()
    for c in raw[:MAX_CATEGORIES]:
        label = str(c.get("label", "")).strip()
        if not _LABEL_OK.match(label) or label.lower() in seen:
            continue
        seen.add(label.lower())
        cats.append(Category(label=label,
                             definition=str(c.get("definition", "")).strip()[:200]))

    if not cats:
        return Taxonomy(categories=[], derived_by="deterministic",
                        note="no usable labels")

    return Taxonomy(categories=cats, status="proposed", derived_by="llm")


def approve(tax: Taxonomy, *, note: str = "") -> Taxonomy:
    """A human says yes. Now it binds, and the version moves.

    Later uploads classify against the APPROVED version, which is what stops the
    categories — and therefore every total rolled up under them — from moving
    between one leadership review and the next.
    """
    return Taxonomy(
        categories=list(tax.categories),
        version=tax.version + 1,
        status="approved",
        derived_by="human",
        note=note or tax.note,
    )


def classify_lines(
    lines: Iterable[ExtractedLine],
    tax: Taxonomy,
    *,
    ctx: Any = None,
) -> dict[str, str]:
    """{line_id: label}. SELECT-only, backstopped, and never blank.

    A label the model invents here is DISCARDED and the line falls to the keyword
    classifier. It may choose from the taxonomy; it may not grow it by classifying
    into it, because a category that appears at assignment time was never approved
    by anybody.

    The batches are independent — each one names its own lines and nothing else —
    so they run concurrently on the shared bounded pool (tools/cortex/pool.py)
    rather than paying one full 7-gate round trip per BATCH lines in sequence.
    Assignments are still applied in batch order, and a batch that fails still
    costs only its own lines: they fall to FALLBACK exactly as before.
    """
    from tools.cortex import api as cortex
    from tools.cortex.pool import get_pool, map_ordered

    lines = list(lines)
    if not lines:
        return {}

    allowed = {c.label.lower(): c.label for c in tax.categories}
    if not allowed:
        # No approved scheme to select from, so there is nothing to be closed
        # about. The deterministic classifier IS the taxonomy here.
        return {ln.line_id: _from_keywords(ln.description, ln.uom or "")
                for ln in lines}

    menu = "\n".join(
        f"- {c.label}: {c.definition}" if c.definition else f"- {c.label}"
        for c in tax.categories
    )

    # Opaque, per-batch keys — NOT the line_id.
    #
    # Two reasons, and the second is the important one. A line_id is
    # "<filename>:<sheet>:<row>", so sending it ships the customer's document
    # names to the model for no benefit at all; a token does the same job and
    # discloses nothing. And a long id invites the model to mangle it — the
    # first version of this prompt wrote "- id=<line_id> ::" and the model
    # dutifully copied "id=..." INTO the field, so not one assignment ever
    # matched and every line silently fell to the backstop. The output looked
    # like a working classifier producing poor results.
    batches = []
    for i in range(0, len(lines), BATCH):
        chunk = lines[i: i + BATCH]
        keys = {f"t{n}": ln for n, ln in enumerate(chunk)}
        items = "\n".join(
            f"{k}: {(ln.description or '')[:90]}" for k, ln in keys.items()
        )
        batches.append((keys, (
            "File each item under exactly one of the categories below.\n\n"
            "Rules:\n"
            "- Use ONLY the labels listed, copied exactly.\n"
            "- Do not invent a category. If nothing fits well, choose the closest.\n"
            "- Return the item's key verbatim in line_id (e.g. \"t0\"), nothing else.\n"
            "- Do not mention prices or quantities.\n\n"
            f"CATEGORIES:\n{menu}\n\nITEMS:\n{items}"
        )))

    def _assign(batch) -> list:
        res = cortex.extract(batch[1], ASSIGN_SCHEMA, ctx)
        payload = json.loads(res.text)
        return payload.get("assignments") or []

    results = map_ordered(
        get_pool("bom-taxonomy", env_var="CORTEX_BOM_TAXONOMY_WORKERS"),
        _assign,
        batches,
    )

    out: dict[str, str] = {}
    for (keys, _prompt), (rows, exc) in zip(batches, results):
        # A failed batch is not a failed run — its lines simply fall to FALLBACK
        # below, which is what the serial version did too.
        if exc is not None:
            rows = []

        for r in rows:
            key = str(r.get("line_id", "")).strip()
            label = str(r.get("label", "")).strip()
            if _voids(label):
                continue
            ln = keys.get(key)
            # SELECT-only. An invented label is not a new category; it is a miss.
            canon = allowed.get(label.lower())
            if ln is not None and canon:
                out[ln.line_id] = canon

    # Silence is not an answer — but neither is a NEW category.
    #
    # An approved taxonomy is a CLOSED set: it is what every rollup, every pivot
    # and every leadership slide is grouped by, and a line that quietly arrives
    # wearing a label nobody approved has grown the taxonomy through the back door.
    # So an unassigned line lands in the fallback bucket, where it is visible and
    # somebody can file it properly.
    for ln in lines:
        out.setdefault(ln.line_id, FALLBACK)

    return out


def save(tax: Taxonomy, path: str | Path) -> None:
    Path(path).write_text(
        json.dumps(tax.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8",
    )


def load(path: str | Path) -> Taxonomy:
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    return Taxonomy(
        categories=[Category(label=c["label"], definition=c.get("definition", ""))
                    for c in d.get("categories", [])],
        version=int(d.get("version", 1)),
        status=str(d.get("status", "proposed")),
        derived_by=str(d.get("derived_by", "llm")),
        note=str(d.get("note", "")),
    )


__all__ = [
    "ASSIGN_SCHEMA",
    "PROPOSE_SCHEMA",
    "Category",
    "Taxonomy",
    "approve",
    "classify_lines",
    "load",
    "propose",
    "save",
]
