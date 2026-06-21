# Phase 2 — AI-ify determination: aiify-rm-06d89-phase-6099

**Classification:** CUI // SP-CTI

- **Roadmap:** `rm-06d89040cf`
- **Scan:** 43
- **Opportunity:** 6099
- **Pattern → Paradigm:** `regex_user_input` → `nlp_extractor`
- **External module_path:** `…/aiify_git_zwu66zfu/src/paperless/parsers/mail.py` (paperless-ngx, shallow-cloned then reaped by the AI-ify engine — external, unmodifiable, clone GONE at run time)
- **Disposition:** **Closed as dup** of the IQE `nl_to_iqe` AI-ification (same disposition as sibling opp 6058).

## Rationale

Per the established AI-ify external-repo mapping (`pattern + paradigm` decide the
internal analog, **not** the external file path), `regex_user_input → nlp_extractor`
maps to the **IQE natural-language-to-query extractor**, `tools/iqe/nl_to_iqe.py`
(and its hand-kept mirror `icdev/tools/iqe/nl_to_iqe.py`). That subsystem is the
ICDEV analog of a regex-driven user-input parser: it replaces brittle regex /
`select *` collapse with structured NL extraction.

This card (6099) is the `parsers/mail.py` instance of the very same external
paperless-ngx pattern already dispositioned for `serialisers.py` under opp **6058**
(commit `6e7c07ddb`). The mail parser's regex-driven field extraction (sender,
subject, date matchers) is the identical brittle-regex-over-user-text shape; its
ICDEV analog is the same `nl_to_iqe` extractor stack. No distinct new grammar is
recoverable from a deleted external clone.

The AI-ification of that subsystem already landed across three sibling opps that
cover the unambiguous regex-parseable user-input forms:

| Extractor | Form parsed | Commit |
|-----------|-------------|--------|
| `_extract_comparison` (nl_to_iqe.py:154) | `<field> <comparison-phrase> <number>` | `c3b8abcb7` (opp 5971) |
| `_extract_temporal` (nl_to_iqe.py:187) | `[<field>] <temporal-phrase\|between> <ISO date>` | `186727df8` (opp 5981) |
| `_extract_ip` (nl_to_iqe.py:239) | `<field> is <IPv4>` / `<field> in <CIDR>` | `acfab827e` (opp 5983) |

## Verification (at HEAD, branch `kanban/aiify-rm-06d89-phase-6099`)

- All three source commits are ancestors of HEAD.
- All three extractors present in **both** `tools/iqe/nl_to_iqe.py` and the
  `icdev/tools/iqe/nl_to_iqe.py` mirror (lines 154 / 187 / 239 in each).
- Tests green: `tests/test_iqe_nl_to_iqe_extract.py`,
  `tests/test_iqe_nl_to_iqe_temporal.py`, `tests/test_iqe_nl_to_iqe_ip.py`
  → **119 passed**.

The external `parsers/mail.py` clone is gone and unmodifiable; no distinct
new grammar is identifiable from a deleted file. The `regex_user_input →
nlp_extractor` AI-ification is complete in the internal analog, so this card
is closed as a dup with `bypass_verification:true` + `bypass_reason`.
