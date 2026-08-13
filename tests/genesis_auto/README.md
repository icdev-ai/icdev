# `tests/genesis_auto/` — generated tests

CUI // SP-CTI

Every file here is **generated**, not hand-written, by the Genesis Test Reflex
(`tools/genesis/reflexes/test.py`, YELLOW tier) from the API surface that
`tools/testing/api_surface_extractor.py` reads out of a module's AST. Read this
file before regenerating, before adding an assertion by hand, and before
"fixing" a red file by widening its guard.

## The policy

> **A generated test asserts behaviour, never the existence of a private name.**

`assert hasattr(mod, "_THRESHOLD")` is not a behavioural claim. It cannot fail in
a way that indicates a defect, and it *does* fail on any legal rename or
refactor. Because the emitted guard catches `ImportError` but not
`AssertionError`, the failure surfaces as a **hard error rather than a skip**.
That is the mechanism that turned this directory into a standing source of red:
96 such assertions across 26 files, of which 3 were failing and 93 were latent.
In `test_extractors.py`, 8 of the 10 asserted names existed nowhere in `tools/`
at all — there was nothing to regenerate against.

Measured and removed in tsg-gen-01 / PR #1591: 96 private-constant assertions
across 27 files, 0 assertions on public names, 0 production files touched. Every
file kept the bulk of its tests (worst case 1 of 10 removed;
`test_classification_manager.py` went 41 → 40).

### What this rule does *not* say

Assertions on **public** constants stay — those are API surface and earn their
keep. #1591 kept all 77 of them.

A `hasattr` on a private name is also fine when it is the **precondition of a
behavioural assertion** rather than the claim itself.
`tests/genesis_auto/test_pattern_classifier.py::test_pattern_classifier_ad_java_config`
is the example: the `hasattr` line exists only to guard the
`isinstance(mod._AD_JAVA_STATIC_INT_MIN, int)` beneath it, which is a real claim
about config loading. That test is hand-added, not generated, and #1591 left it
alone deliberately.

### The kept-and-repaired example

The two assertions in this directory that had caught real behaviour were repaired
rather than deleted. The one worth citing is **exa-refine-04's evidence gate**
(`test_reflexion_agent.py`): a refinement with no `lesson_learned` rows behind it
is written `rejected_no_evidence`, never `pending`, so it can never reach GEPA or
a review queue. The generated test still expected `pending`, and a second
asserted `get_latest_improvement()` returned text for it — it selects
`WHERE status='pending'`, so `''` was correct. **The code was right; the test was
wrong.** Both tests now supply supporting evidence for the accepted path, and a
new test asserts the rejection end-to-end: status, lesson count, the persisted
row, and that the refinement stays invisible to its consumer.

That is the shape of an assertion worth keeping — it names a behaviour a reader
can be wrong about. `hasattr(mod, "_X")` never does.

## Why the generator has to filter

`extract_api_surface(file_path, include_private=False)` sounds like it already
handles this. It does not. That flag filters `functions` and `classes` only
(`api_surface_extractor.py:531`); `constants` and `dict_constants` are returned
verbatim, and `"_MAX_CONSTANTS_ASSERTED".isupper()` is `True`, so every private
UPPER_CASE module constant arrives in the surface regardless of the flag. The
extractor is left alone deliberately — a human reading a module's surface has a
legitimate reason to see its private constants, and other callers depend on that.
**Filtering is the generator's job**, at the point of emission.

## How the rule is enforced

| Where | What |
|-------|------|
| `tools/genesis/reflexes/test.py` | `_is_public_name` filters constants **before** the `_MAX_CONSTANTS_ASSERTED` cap, so private names cannot consume the budget public ones deserve. Mirrored in `icdev/tools/genesis/reflexes/test.py`. |
| `tests/test_genesis_test_reflex_policy.py` | Generates from a synthetic surface *and* from a real module that has private constants, then asserts no `hasattr(mod, "_` appears in the output. Without this the next generation run silently re-adds all 96. |

## If the policy turns out to be wrong

Revert PR #1591. It is cheap precisely because it touched no production code.
Do **not** restore individual assertions piecemeal, and do not widen the emitted
guard to `except (ImportError, AttributeError)` — that change is inert.
`assert hasattr(...)` raises `AssertionError`, and `hasattr` itself never raises,
so catching `AttributeError` changes nothing.
