# CUI // SP-CTI
r"""Word-level diff spans, computed server-side (dwr-ws-01).

THE ONLY DIFF IN THE SYSTEM WAS LINE-LEVEL. ``blueprint.api_version_diff`` runs
``difflib.SequenceMatcher`` over ``content.splitlines()``, and the redline
"diff" on ``doc_detail.html`` was two ``slice(0, 200)`` blobs side by side.
Neither can show what a change does to a SENTENCE: replacing ``FIPS 140-2``
with ``FIPS 140-3`` inside a 90-character line renders, line-level, as the
whole line deleted and the whole line re-inserted, and a reader has to spot the
one changed token by eye.

WHY difflib AND NOTHING ELSE. This is the established pattern (the version diff
above; ``delta_review`` receives pre-computed spans and renders them), and it
vendors NOTHING new -- which is the load-bearing constraint here, because the
air-gap posture forbids a CDN diff library and none is vendored. Everything in
this module is stdlib.

## The tokenizer, and why the round trip is an invariant and not a nicety

``tokenize`` partitions a string into word runs (``\w+``), whitespace runs
(``\s+``) and single other characters (``[^\w\s]``). Every character of any
string falls into exactly one of those three classes, so::

    "".join(tokenize(text)) == text

holds for EVERY input -- punctuation, unicode, tabs, blank lines, the empty
string. Whitespace is a token rather than a separator precisely so that
rejoining loses nothing; a tokenizer that split on whitespace and threw it away
would render ``a  b`` and ``a b`` as identical and would silently normalise a
document's spacing on the way through the diff.

That property lifts to the spans:

    equal + delete, in order, reassemble ``before`` EXACTLY
    equal + insert, in order, reassemble ``after``  EXACTLY

:func:`round_trip` re-derives both sides and is asserted by the test suite. It
is also checked at RUN time by :func:`word_opcodes`' caller contract
(:func:`diff_words` returns ``round_trip`` on every result), because a diff
renderer that silently drops a character is worse than no diff at all: the
reader believes they have seen the change.

## Span shape

``[{"tag": "equal"|"insert"|"delete", "text": "..."}]`` -- the same three tags
``api_version_diff`` emits per line, so a renderer written for one reads the
other. One span per opcode (its tokens joined), never one span per token: a
50-token unchanged run is one ``equal`` span, which is what a reader wants and
what keeps the payload small. A ``replace`` opcode emits ``delete`` THEN
``insert``, matching the line differ's ordering.

Pure: reads nothing, writes nothing, imports nothing outside the stdlib.
"""
from __future__ import annotations

import difflib
import re

#: Word runs, whitespace runs, and every other character singly. The three
#: classes are exhaustive and disjoint over any str, which is what makes the
#: round trip an invariant rather than a hope.
_TOKEN_RE = re.compile(r"\w+|\s+|[^\w\s]", re.UNICODE)

EQUAL = "equal"
INSERT = "insert"
DELETE = "delete"

#: The tags a span may carry. ``replace`` is decomposed, never emitted.
SPAN_TAGS = (EQUAL, INSERT, DELETE)


def tokenize(text: str | None) -> list[str]:
    """Split ``text`` on word boundaries, KEEPING whitespace and punctuation.

    ``"".join(tokenize(t)) == t`` for every ``t``. ``None`` is treated as the
    empty string and yields ``[]``.
    """
    if not text:
        return []
    return _TOKEN_RE.findall(text)


def word_opcodes(before: str | None, after: str | None) -> list[dict]:
    """Word-level opcodes over ``before`` -> ``after``.

    Returns ``[{"tag": "equal"|"insert"|"delete", "text": "..."}]``. Empty list
    only when both sides are empty -- there is genuinely nothing to render.

    ``autojunk`` is OFF. With it on, ``SequenceMatcher`` treats any element
    appearing in more than 1% of a sequence longer than 200 elements as junk,
    and in word tokens that is every space and every ``the``; the diff would
    quietly degrade on exactly the long sections where a reader needs it most.
    """
    a = tokenize(before)
    b = tokenize(after)
    spans: list[dict] = []
    matcher = difflib.SequenceMatcher(None, a, b, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            spans.append({"tag": EQUAL, "text": "".join(a[i1:i2])})
            continue
        # delete BEFORE insert, so that reassembling equal+delete walks
        # `before` in order and equal+insert walks `after` in order.
        if tag in ("replace", "delete"):
            spans.append({"tag": DELETE, "text": "".join(a[i1:i2])})
        if tag in ("replace", "insert"):
            spans.append({"tag": INSERT, "text": "".join(b[j1:j2])})
    return [s for s in spans if s["text"]]


def reassemble(spans: list[dict] | None, side: str) -> str:
    """Rebuild one side of the diff from its spans.

    ``side`` is ``"before"`` (equal + delete) or ``"after"`` (equal + insert).
    Raises ``ValueError`` for any other side -- a typo must not silently return
    a string that looks plausible.
    """
    if side == "before":
        wanted = (EQUAL, DELETE)
    elif side == "after":
        wanted = (EQUAL, INSERT)
    else:
        raise ValueError(f"side must be 'before' or 'after', got {side!r}")
    return "".join(str(s.get("text") or "") for s in (spans or [])
                   if s.get("tag") in wanted)


def round_trip(before: str | None, after: str | None,
               spans: list[dict] | None) -> bool:
    """Do ``spans`` reassemble BOTH sides byte for byte?

    The whole point of the module. A False here means the renderer would drop
    or duplicate characters, and the caller must show no diff rather than a
    lossy one.
    """
    return (reassemble(spans, "before") == (before or "")
            and reassemble(spans, "after") == (after or ""))


def count_words(spans: list[dict] | None, tag: str) -> int:
    r"""Number of WORD tokens (``\w+``) carrying ``tag``.

    Whitespace and punctuation tokens are not counted -- "3 words added" must
    not become "11 added" because the inserted phrase carried spaces and a full
    stop. A punctuation-only change therefore counts 0 words and is still
    VISIBLE in the spans; the count is a summary, never the evidence.
    """
    total = 0
    for span in spans or []:
        if span.get("tag") != tag:
            continue
        total += sum(1 for t in tokenize(str(span.get("text") or ""))
                     if not t.isspace() and _is_word(t))
    return total


def _is_word(token: str) -> bool:
    return bool(token) and (token[0].isalnum() or token[0] == "_")


def diff_words(before: str | None, after: str | None) -> dict:
    """The whole answer for one before/after pair.

    ``{"spans", "round_trip", "added_words", "removed_words", "changed"}``.

    When the round trip FAILS, ``spans`` is ``None`` -- never a truncated or
    best-effort list -- so a caller cannot render a lossy diff by accident.
    ``round_trip`` stays on the result and says why the spans are missing.
    """
    spans = word_opcodes(before, after)
    ok = round_trip(before, after, spans)
    return {
        "spans": spans if ok else None,
        "round_trip": ok,
        "added_words": count_words(spans, INSERT) if ok else None,
        "removed_words": count_words(spans, DELETE) if ok else None,
        "changed": (before or "") != (after or ""),
    }
