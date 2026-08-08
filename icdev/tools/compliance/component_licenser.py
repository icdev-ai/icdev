#!/usr/bin/env python3
# CUI // SP-CTI
# CANONICAL: backs the Component License element of the 2026 SBOM Minimum Elements for
# tools/compliance/sbom_generator.py.
# Authored in both tools/compliance/ and icdev/tools/compliance/ — keep the two in sync.
"""Component License (2026 SBOM Minimum Elements, sbx-fld-04).

The standard defines the element as:

    Component License   Identifier(s) for the license(s) the component is available
                        under. SPDX license identifiers should be used where possible so
                        the recipient can look up the full terms; where no identifier is
                        available, point at full details another way, such as a URL. The
                        field must convey the existence of PROPRIETARY license
                        conditions. Where the author is unaware of the license, that must
                        be stated explicitly.

Its rationale is risk management, not legal housekeeping: incorrect or fraudulent license
information affects an organization's ability to rely on the software or to obtain
support. That is why nothing here ever guesses. A declared string becomes an SPDX
identifier only when it is on the vendored SPDX License List
(`spdx_license_data.SPDX_LICENSE_IDS`); anything else is carried through as a license
*name* or a URL, and anything absent becomes an explicit unknown marker. The field is
never omitted and never silently emptied.

RESOLUTION ORDER

  1. A license declared by the manifest for that component — `component["declared_license"]`.
  2. Otherwise the explicit unknown marker, with a machine-readable reason.

`sbom_generator.py` reads declared dependency manifests and does not resolve artifacts, so
only the manifests that carry a per-component license field populate step 1 today
(`package-lock.json`). For the rest, the honest outcome is `license-not-declared`, and it
stays that way until sbx-cov-01's resolution work starts populating `declared_license`
from resolved artifact metadata — at which point nothing in this module has to change.
The target component of the document is resolved the same way, from the project's own
manifest, via `project_license_from_manifests()`.

WHAT "PROPRIETARY" MEANS HERE

Tri-state, and deliberately narrow. The flag answers one question: *does this component
carry license conditions the recipient cannot look up?*

  True     There is positive evidence of bespoke or non-public terms — npm `UNLICENSED`
           or `SEE LICENSE IN <file>`, an SPDX `LicenseRef-`/`DocumentRef-` custom
           reference, the SPDX `NONE` keyword (no license granted, i.e. all rights
           reserved), or proprietary vocabulary in the declared text.
  False    The declaration is a valid SPDX expression built solely from SPDX List
           identifiers, with no proprietary vocabulary. The terms are published and the
           recipient resolves them from the identifier.
  None     Not determinable — a bare URL or a bare license name. Reported explicitly as
           "unknown" rather than defaulted to False, because defaulting would assert the
           absence of proprietary conditions we have not checked for.

False therefore means "no conditions the recipient cannot look up", NOT "open source". A
source-available licence with an SPDX id (BUSL-1.1, SSPL-1.0) flags False on purpose: its
terms are restrictive but they are published and identified, which is exactly what the
standard asks the element to deliver.

RELATIONSHIP TO sbx-prc-01

The unknown marker below is local to this element. sbx-prc-01 owns the general
unknown-versus-withheld convention and the `sbom_components.unknown_fields_json` /
`withheld_fields_json` blobs, which this module deliberately does not write — inventing a
blob shape here would only have to be rewritten. `license_db_value()` is the single place
that bridges to it when it lands: an unknown resolution persists the marker string, and
prc-01 changes one function rather than hunting call sites.
"""

import json
import re
from pathlib import Path

from tools.compliance.spdx_license_data import (
    SPDX_EXCEPTION_IDS_BY_CASEFOLD,
    SPDX_LICENSE_IDS_BY_CASEFOLD,
)

# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------

# The explicit unknown marker. The standard forbids omitting the field, so this is what
# is emitted and persisted when the license is unknown to the author.
UNKNOWN = "unknown"

# How the license was declared. Every resolution carries exactly one of these.
DECLARATION_SPDX = "spdx-expression"
DECLARATION_URL = "url"
DECLARATION_NAME = "name"
DECLARATION_NONE = "none"
DECLARATION_UNKNOWN = UNKNOWN

# Machine-readable unknown reasons.
REASON_NOT_DECLARED = "license-not-declared"
REASON_DECLARATION_EMPTY = "license-declaration-empty"
REASON_DECLARED_NOASSERTION = "license-declared-noassertion"

# Machine-readable proprietary evidence.
EVIDENCE_NPM_UNLICENSED = "npm-unlicensed"
EVIDENCE_NPM_SEE_LICENSE_IN = "npm-see-license-in-file"
EVIDENCE_SPDX_CUSTOM_REF = "spdx-custom-license-reference"
EVIDENCE_NO_LICENSE_GRANTED = "spdx-none-no-license-granted"
EVIDENCE_VOCABULARY = "proprietary-vocabulary"

# CycloneDX property names carrying the element. `icdev:component-license` and
# `icdev:component-license-proprietary` are emitted for every component without
# exception — that is what keeps the element from being silently dropped.
PROPERTY_LICENSE = "icdev:component-license"
PROPERTY_DECLARATION = "icdev:component-license-declaration"
PROPERTY_PROPRIETARY = "icdev:component-license-proprietary"
PROPERTY_PROPRIETARY_EVIDENCE = "icdev:component-license-proprietary-evidence"
PROPERTY_UNKNOWN_REASON = "icdev:component-license-unknown-reason"
PROPERTY_URL = "icdev:component-license-url"

# Phrases that positively evidence license conditions the recipient cannot look up.
# Matched case-insensitively on word boundaries so "Commercial" hits and "noncommercial"
# — which is a use restriction inside an otherwise published license — does not.
_PROPRIETARY_VOCABULARY = (
    "proprietary",
    "commercial",
    "closed source",
    "closed-source",
    "all rights reserved",
    "eula",
    "end user license agreement",
    "trade secret",
    "confidential",
    "internal use only",
)
_PROPRIETARY_RE = re.compile(
    r"(?<![\w-])(" + "|".join(re.escape(p) for p in _PROPRIETARY_VOCABULARY) + r")(?![\w-])",
    re.IGNORECASE,
)

# npm's "the terms are in a file in the package" form: `SEE LICENSE IN <filename>`.
_SEE_LICENSE_IN_RE = re.compile(r"^SEE\s+LICEN[CS]E\s+IN\s+(.+)$", re.IGNORECASE)

_URL_RE = re.compile(r"^(https?|ftp)://\S+$", re.IGNORECASE)

# SPDX expression keywords that are only legal on their own.
_KEYWORD_NOASSERTION = "NOASSERTION"
_KEYWORD_NONE = "NONE"

# Manifest spellings that mean "the author does not know", not a license called Unknown.
_NOASSERTION_SPELLINGS = frozenset({"UNKNOWN", "UNSPECIFIED", "NOASSERTION", "N/A", "NA"})


# ---------------------------------------------------------------------------
# SPDX expression parsing
# ---------------------------------------------------------------------------
# Grammar (ISO/IEC 5962 Annex D), precedence WITH > AND > OR:
#
#   expression  = or-expr
#   or-expr     = and-expr   ( "OR"   and-expr   )*
#   and-expr    = with-expr  ( "AND"  with-expr  )*
#   with-expr   = atom       ( "WITH" exception-id )?
#   atom        = "(" expression ")" / license-id [ "+" ] / license-ref
#
# Operators are accepted in any case because manifests write "or" and "and" freely, but
# they are always re-emitted uppercase. Identifiers are matched case-insensitively and
# re-emitted in their canonical SPDX spelling, which is the whole point of validating:
# a recipient looking the id up needs the spelling the SPDX List uses.

_PREC_OR, _PREC_AND, _PREC_WITH, _PREC_ATOM = 1, 2, 3, 4

_LICENSE_REF_RE = re.compile(
    r"^(?:DocumentRef-[A-Za-z0-9.-]+:)?LicenseRef-[A-Za-z0-9.-]+$"
)


class _ParseError(ValueError):
    """The text is not a well-formed SPDX license expression."""


def normalize_spdx_id(token):
    """Return the canonical SPDX spelling of a license identifier, or None.

    Accepts any casing and an optional trailing `+` ("or later"), which is preserved.
    Returns None when the identifier is not on the vendored SPDX License List — the
    caller must then treat the text as a license name, never as an SPDX id.
    """
    if not isinstance(token, str) or not token:
        return None
    bare, plus = (token[:-1], "+") if token.endswith("+") else (token, "")
    canonical = SPDX_LICENSE_IDS_BY_CASEFOLD.get(bare.lower())
    return None if canonical is None else canonical + plus


def normalize_spdx_exception_id(token):
    """Return the canonical SPDX spelling of an exception identifier, or None."""
    if not isinstance(token, str) or not token:
        return None
    return SPDX_EXCEPTION_IDS_BY_CASEFOLD.get(token.lower())


def is_spdx_license_ref(token):
    """True for a `LicenseRef-x` / `DocumentRef-y:LicenseRef-x` custom reference.

    A custom reference is well-formed SPDX, but by definition it names a license that is
    NOT on the SPDX List — the recipient cannot resolve it, which is what makes it
    evidence of proprietary conditions.
    """
    return isinstance(token, str) and bool(_LICENSE_REF_RE.match(token))


class _ExpressionParser:
    """Recursive-descent parser producing a canonical re-rendering and the ids used."""

    def __init__(self, tokens):
        self._tokens = tokens
        self._pos = 0
        self.license_ids = []
        self.exception_ids = []
        self.custom_refs = []

    # -- token helpers ----------------------------------------------------
    def _peek(self):
        return self._tokens[self._pos] if self._pos < len(self._tokens) else None

    def _next(self):
        token = self._peek()
        if token is None:
            raise _ParseError("unexpected end of expression")
        self._pos += 1
        return token

    def _accept_operator(self, name):
        token = self._peek()
        if token is not None and token.upper() == name and token not in ("(", ")"):
            self._pos += 1
            return True
        return False

    # -- grammar ----------------------------------------------------------
    def parse(self):
        node = self._parse_or()
        if self._peek() is not None:
            raise _ParseError(f"trailing token {self._peek()!r}")
        return node

    def _parse_or(self):
        children = [self._parse_and()]
        while self._accept_operator("OR"):
            children.append(self._parse_and())
        return children[0] if len(children) == 1 else ("or", children)

    def _parse_and(self):
        children = [self._parse_with()]
        while self._accept_operator("AND"):
            children.append(self._parse_with())
        return children[0] if len(children) == 1 else ("and", children)

    def _parse_with(self):
        node = self._parse_atom()
        if self._accept_operator("WITH"):
            raw = self._next()
            canonical = normalize_spdx_exception_id(raw)
            if canonical is None:
                raise _ParseError(f"{raw!r} is not an SPDX license exception")
            self.exception_ids.append(canonical)
            return ("with", node, canonical)
        return node

    def _parse_atom(self):
        token = self._next()
        if token == "(":
            node = self._parse_or()
            if self._next() != ")":
                raise _ParseError("unbalanced parenthesis")
            return ("group", node)
        if token in (")",) or token.upper() in ("AND", "OR", "WITH"):
            raise _ParseError(f"unexpected token {token!r}")
        if is_spdx_license_ref(token):
            self.custom_refs.append(token)
            return ("ref", token)
        canonical = normalize_spdx_id(token)
        if canonical is None:
            raise _ParseError(f"{token!r} is not an SPDX license identifier")
        self.license_ids.append(canonical)
        return ("id", canonical)


def _render(node, parent_precedence=0):
    """Re-render a parsed node, parenthesizing only where precedence requires it."""
    kind = node[0]
    if kind in ("id", "ref"):
        return node[1]
    if kind == "group":
        # The input's own parentheses are dropped; precedence rules re-add any that
        # are load-bearing, so `(MIT)` canonicalizes to `MIT`.
        return _render(node[1], parent_precedence)
    if kind == "with":
        rendered = f"{_render(node[1], _PREC_WITH)} WITH {node[2]}"
        return rendered if parent_precedence <= _PREC_WITH else f"({rendered})"
    joiner, precedence = (" AND ", _PREC_AND) if kind == "and" else (" OR ", _PREC_OR)
    rendered = joiner.join(_render(child, precedence) for child in node[1])
    return rendered if parent_precedence <= precedence else f"({rendered})"


def parse_spdx_expression(text):
    """Parse `text` as an SPDX license expression.

    Returns a dict with `valid`, the canonically re-rendered `expression`, and the
    identifiers it referenced. `valid` is False both for malformed syntax and for a
    well-formed expression naming an identifier that is not on the SPDX License List —
    the caller must not emit either as an SPDX id.
    """
    empty = {
        "valid": False,
        "expression": None,
        "license_ids": [],
        "exception_ids": [],
        "custom_refs": [],
    }
    if not isinstance(text, str) or not text.strip():
        return empty

    tokens = text.replace("(", " ( ").replace(")", " ) ").split()
    if not tokens:
        return empty

    parser = _ExpressionParser(tokens)
    try:
        node = parser.parse()
    except _ParseError:
        return empty

    return {
        "valid": True,
        "expression": _render(node),
        "license_ids": parser.license_ids,
        "exception_ids": parser.exception_ids,
        "custom_refs": parser.custom_refs,
    }


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def _result(
    declaration,
    spdx_expression=None,
    spdx_ids=None,
    url=None,
    name=None,
    proprietary=None,
    proprietary_evidence=None,
    reason=None,
    raw=None,
):
    return {
        "declaration": declaration,
        "spdx_expression": spdx_expression,
        "spdx_ids": list(spdx_ids or []),
        "url": url,
        "name": name,
        "proprietary": proprietary,
        "proprietary_evidence": proprietary_evidence,
        "reason": reason,
        "raw": raw,
    }


def unknown_license(reason, raw=None):
    """Build the explicit unknown marker. Never returns an omitted or empty value."""
    return _result(DECLARATION_UNKNOWN, reason=reason, raw=raw)


def is_unknown(result):
    """True when `result` states the license is unknown to the author."""
    return result.get("declaration") == DECLARATION_UNKNOWN


def _proprietary_vocabulary_hit(*texts):
    for text in texts:
        if isinstance(text, str) and _PROPRIETARY_RE.search(text):
            return True
    return False


def _flatten_declaration(declared):
    """Reduce a manifest's license declaration to (text, url).

    Handles the string form, npm's legacy object form (`{"type": …, "url": …}`) and its
    legacy array form. The array means "the recipient may choose", which is npm's own
    documented semantics, so it is joined with OR — the one place a relationship between
    multiple licenses is inferred, and only because the format defines it.
    """
    if declared is None:
        return None, None
    if isinstance(declared, str):
        return declared.strip(), None
    if isinstance(declared, dict):
        text = declared.get("type") or declared.get("name") or declared.get("id")
        url = declared.get("url")
        return (text.strip() if isinstance(text, str) else None), (
            url.strip() if isinstance(url, str) else None
        )
    if isinstance(declared, (list, tuple)):
        texts, url = [], None
        for entry in declared:
            entry_text, entry_url = _flatten_declaration(entry)
            if entry_text:
                texts.append(entry_text)
            if url is None:
                url = entry_url
        if not texts:
            return None, url
        if len(texts) == 1:
            return texts[0], url
        return " OR ".join(texts), url
    return None, None


def resolve_declared_license(declared):
    """Resolve one manifest license declaration into the Component License element.

    Always returns a result. The declaration is classified as an SPDX expression, a URL
    to full terms, a license name, an explicit "no license granted", or unknown — and the
    proprietary flag is set only from positive evidence.
    """
    text, url = _flatten_declaration(declared)

    if text is None and url is None:
        return unknown_license(REASON_NOT_DECLARED)
    if not text:
        if url:
            return _result(
                DECLARATION_URL,
                url=url,
                proprietary=True if _proprietary_vocabulary_hit(url) else None,
                proprietary_evidence=EVIDENCE_VOCABULARY if _proprietary_vocabulary_hit(url) else None,
                raw=url,
            )
        return unknown_license(REASON_DECLARATION_EMPTY, raw=declared if isinstance(declared, str) else None)

    upper = text.upper()

    # "The author does not know" — stated explicitly, which is what the standard asks.
    if upper in _NOASSERTION_SPELLINGS or upper == _KEYWORD_NOASSERTION:
        return unknown_license(REASON_DECLARED_NOASSERTION, raw=text)

    # SPDX `NONE`: no license is granted, i.e. all rights reserved. That is the strongest
    # possible statement that proprietary conditions exist.
    if upper == _KEYWORD_NONE:
        return _result(
            DECLARATION_NONE,
            url=url,
            name=_KEYWORD_NONE,
            proprietary=True,
            proprietary_evidence=EVIDENCE_NO_LICENSE_GRANTED,
            raw=text,
        )

    # npm's two proprietary forms. Both are declarations, not unknowns: the author knows
    # exactly what the terms are and is telling the recipient they are not public.
    if upper == "UNLICENSED":
        return _result(
            DECLARATION_NAME,
            url=url,
            name=text,
            proprietary=True,
            proprietary_evidence=EVIDENCE_NPM_UNLICENSED,
            raw=text,
        )
    if _SEE_LICENSE_IN_RE.match(text):
        return _result(
            DECLARATION_NAME,
            url=url,
            name=text,
            proprietary=True,
            proprietary_evidence=EVIDENCE_NPM_SEE_LICENSE_IN,
            raw=text,
        )

    vocabulary_hit = _proprietary_vocabulary_hit(text, url)

    parsed = parse_spdx_expression(text)
    if parsed["valid"]:
        if parsed["custom_refs"]:
            proprietary, evidence = True, EVIDENCE_SPDX_CUSTOM_REF
        elif vocabulary_hit:
            proprietary, evidence = True, EVIDENCE_VOCABULARY
        else:
            # Every identifier is on the SPDX List: the terms are published and the
            # recipient resolves them from the id.
            proprietary, evidence = False, None
        return _result(
            DECLARATION_SPDX,
            spdx_expression=parsed["expression"],
            spdx_ids=parsed["license_ids"] + parsed["exception_ids"],
            url=url,
            proprietary=proprietary,
            proprietary_evidence=evidence,
            raw=text,
        )

    # Not an SPDX expression. A bare URL points at the full terms; anything else travels
    # as a license name. Neither is ever laundered into the document as an SPDX id.
    if _URL_RE.match(text):
        return _result(
            DECLARATION_URL,
            url=text,
            proprietary=True if vocabulary_hit else None,
            proprietary_evidence=EVIDENCE_VOCABULARY if vocabulary_hit else None,
            raw=text,
        )

    return _result(
        DECLARATION_NAME,
        url=url,
        name=text,
        proprietary=True if vocabulary_hit else None,
        proprietary_evidence=EVIDENCE_VOCABULARY if vocabulary_hit else None,
        raw=text,
    )


def resolve_license(component):
    """Resolve the Component License element for one parsed component.

    Reads `component["declared_license"]` — whatever the manifest stated, in string,
    object or array form. Always returns a result: a license, or an explicit unknown
    marker carrying a machine-readable reason.
    """
    return resolve_declared_license(component.get("declared_license"))


# ---------------------------------------------------------------------------
# Emission
# ---------------------------------------------------------------------------


def license_entries(result):
    """Render a resolution as a CycloneDX `licenses` array.

    Empty when the license is unknown or no license is granted: CycloneDX has no way to
    say "unknown" inside `licenses`, and an invented entry there is worse than none. The
    ICDEV properties carry the explicit marker in that case, so the element is still
    present on the component.
    """
    declaration = result["declaration"]

    if declaration == DECLARATION_SPDX:
        return [{"expression": result["spdx_expression"]}]

    if declaration in (DECLARATION_URL, DECLARATION_NAME):
        entry = {}
        if result.get("name"):
            entry["name"] = result["name"]
        elif result.get("url"):
            # CycloneDX requires an id or a name; the URL is the only thing declared, so
            # it names the license as well as locating it.
            entry["name"] = result["url"]
        if result.get("url"):
            entry["url"] = result["url"]
        return [{"license": entry}] if entry else []

    return []


def license_properties(result):
    """Render a resolution as CycloneDX `properties` entries.

    `icdev:component-license` and `icdev:component-license-proprietary` are always
    present — a known license reports how it was declared, an unknown one reports why,
    and the proprietary flag is always stated as true, false or unknown rather than
    being left to inference.
    """
    properties = [
        {"name": PROPERTY_LICENSE, "value": license_db_value(result)},
        {"name": PROPERTY_DECLARATION, "value": result["declaration"]},
        {
            "name": PROPERTY_PROPRIETARY,
            "value": UNKNOWN if result["proprietary"] is None else str(result["proprietary"]).lower(),
        },
    ]
    if result.get("proprietary_evidence"):
        properties.append(
            {"name": PROPERTY_PROPRIETARY_EVIDENCE, "value": result["proprietary_evidence"]}
        )
    if is_unknown(result):
        properties.append({"name": PROPERTY_UNKNOWN_REASON, "value": result.get("reason") or UNKNOWN})
    elif result.get("url") and result["declaration"] != DECLARATION_URL:
        # A URL alongside a name or an expression still points at the full terms.
        properties.append({"name": PROPERTY_URL, "value": result["url"]})
    return properties


def license_db_value(result):
    """The value persisted in `sbom_components.license`. Never None, never empty.

    This is the one place the element is reduced to a single string, so it is also the
    one place sbx-prc-01 has to touch when the general unknown/withheld convention
    replaces the local marker.
    """
    declaration = result["declaration"]
    if declaration == DECLARATION_SPDX:
        return result["spdx_expression"]
    if declaration == DECLARATION_NONE:
        return _KEYWORD_NONE
    if declaration == DECLARATION_NAME and result.get("name"):
        return result["name"]
    if result.get("url"):
        return result["url"]
    return UNKNOWN


# ---------------------------------------------------------------------------
# The target component's own license
# ---------------------------------------------------------------------------
# The document's target component is a component too, and the standard's Component Data
# elements apply to it. Unlike its dependencies, its license IS declared in the project's
# own manifest, so it is read here rather than left unknown.


def _read_text(path):
    try:
        return Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _pyproject_license(project_dir):
    """`[project] license` (PEP 621 / PEP 639) or an OSI classifier, from pyproject.toml.

    Regex-based like the dependency parsers in `sbom_generator.py`: `requires-python` is
    `>=3.9`, so `tomllib` is not available on every supported interpreter.
    """
    content = _read_text(Path(project_dir) / "pyproject.toml")
    if content is None:
        return None

    # license = "Apache-2.0"  (PEP 639 expression form)
    match = re.search(r'^\s*license\s*=\s*"([^"]+)"', content, re.MULTILINE)
    if match:
        return match.group(1).strip()

    # license = { text = "MIT" }  or  { file = "LICENSE" }
    match = re.search(r'^\s*license\s*=\s*\{([^}]*)\}', content, re.MULTILINE)
    if match:
        inner = match.group(1)
        text_match = re.search(r'text\s*=\s*"([^"]+)"', inner)
        if text_match:
            return text_match.group(1).strip()
        file_match = re.search(r'file\s*=\s*"([^"]+)"', inner)
        if file_match:
            # A pointer to a file, not an identifier. npm's spelling of the same idea is
            # the one the resolver already understands.
            return f"SEE LICENSE IN {file_match.group(1).strip()}"

    # "License :: OSI Approved :: MIT License" — a category, not an identifier, so it is
    # carried as a license name and never as an SPDX id.
    classifier = re.search(r'"License\s*::\s*(?:OSI Approved\s*::\s*)?([^"]+)"', content)
    if classifier:
        return classifier.group(1).strip()

    return None


def _package_json_license(project_dir):
    """`license` or the legacy `licenses` array from package.json."""
    content = _read_text(Path(project_dir) / "package.json")
    if content is None:
        return None
    try:
        data = json.loads(content)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    return data.get("license") or data.get("licenses") or None


def _cargo_toml_license(project_dir):
    """`[package] license` or `license-file` from Cargo.toml."""
    content = _read_text(Path(project_dir) / "Cargo.toml")
    if content is None:
        return None
    match = re.search(r'^\s*license\s*=\s*"([^"]+)"', content, re.MULTILINE)
    if match:
        return match.group(1).strip()
    match = re.search(r'^\s*license-file\s*=\s*"([^"]+)"', content, re.MULTILINE)
    if match:
        return f"SEE LICENSE IN {match.group(1).strip()}"
    return None


def _pom_xml_license(project_dir):
    """The first `<license>` entry from pom.xml.

    Only the first. Maven permits several and does not state whether they are a choice or
    a conjunction, so combining them would assert a relationship the manifest does not —
    the same reason the npm array IS combined, where the format does define it.
    """
    content = _read_text(Path(project_dir) / "pom.xml")
    if content is None:
        return None
    block = re.search(r"<licenses>(.*?)</licenses>", content, re.DOTALL)
    if not block:
        return None
    entry = re.search(r"<license>(.*?)</license>", block.group(1), re.DOTALL)
    if not entry:
        return None
    name = re.search(r"<name>\s*(.*?)\s*</name>", entry.group(1), re.DOTALL)
    url = re.search(r"<url>\s*(.*?)\s*</url>", entry.group(1), re.DOTALL)
    if not name and not url:
        return None
    return {
        "type": name.group(1).strip() if name else None,
        "url": url.group(1).strip() if url else None,
    }


# Order matters only when a project ships more than one manifest; the first that declares
# a license wins, and a project that declares none resolves to the explicit unknown
# marker like any other component.
_PROJECT_LICENSE_READERS = (
    _pyproject_license,
    _package_json_license,
    _cargo_toml_license,
    _pom_xml_license,
)


def project_license_from_manifests(project_dir):
    """Return the target component's declared license, or None if no manifest states one.

    Never raises: an unreadable or malformed manifest is indistinguishable from an absent
    one for this purpose, and SBOM generation must not fail because a project's own
    metadata is broken.
    """
    if not project_dir:
        return None
    directory = Path(project_dir)
    if not directory.is_dir():
        return None
    for reader in _PROJECT_LICENSE_READERS:
        try:
            declared = reader(directory)
        except Exception:  # noqa: BLE001 — a broken manifest is an absent one here
            continue
        if declared:
            return declared
    return None
