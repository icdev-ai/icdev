# Phase 2 — AI-ify Determination: aiify-rm-06d89-phase-6100

**Opportunity:** 6100 (scan_id 43, roadmap `rm-06d89040cf`)
**Pattern → Paradigm:** `regex_user_input` → `nlp_extractor`
**External target (unmodifiable):** `aiify_git_zwu66zfu/src/paperless/parsers/mail.py`
**Disposition:** Closed as **duplicate** of the IQE `nl_to_iqe` extractor work.

## Rationale

The opportunity's `module_path` points at a temporary shallow clone of an
external open-source repo (paperless-ngx). The aiify engine clones, scans, then
deletes these clones — by the time this kanban card runs the file is gone and is
unmodifiable in any case (verified: clone directory absent).

Per the established mapping, `regex_user_input → nlp_extractor` opportunities
map to the **internal IQE analog** `tools/iqe/nl_to_iqe.py`, which translates
regex-style natural-language user input into real IQE query predicates instead
of collapsing to `select *` / an LLM round-trip. The same mapping was applied to
the sibling paperless opp 6058 (`src/documents/serialisers.py`, same
pattern/paradigm) — pattern+paradigm decide the analog, not the file path.

## Verification (at HEAD on `irad/feature`)

The three deterministic extractors are present in **both** the canonical and the
mirrored module:

- `tools/iqe/nl_to_iqe.py`: `_extract_comparison` (L154), `_extract_temporal`
  (L187), `_extract_ip` (L239)
- `icdev/tools/iqe/nl_to_iqe.py`: identical (L154 / L187 / L239)

Originating commits are all ancestors of HEAD:

- `c3b8abcb7` — `_extract_comparison` (numeric comparison predicates)
- `186727df8` — `_extract_temporal` (ISO-date / between predicates)
- `acfab827e` — `_extract_ip` (IPv4 / CIDR predicates)

Tests: `tests/test_iqe_nl_to_iqe_extract.py`,
`tests/test_iqe_nl_to_iqe_temporal.py`, `tests/test_iqe_nl_to_iqe_ip.py` —
**119 passed**.

## Outcome

No new code required. The AI-ification this opportunity describes already ships
internally and is covered by tests. Card moved to **done** with
`bypass_verification: true` and a `bypass_reason` naming this determination.
