# A PERFECT SCORE returned when the denominator is empty (rem-hyg-13)

> Moved verbatim from CLAUDE.md on 2026-09-12 (xrv-docs-02). The prose is the card's own record; only the leading `#` comment markers were stripped and the command runs fenced.

```bash
python tools/ci/perfect_score_census.py --check               # the gate; exit 1 on a NEW site
python tools/ci/perfect_score_census.py --json
python tools/ci/perfect_score_census.py --changed tools/foo.py --check
python tools/ci/perfect_score_census.py --staged
python tools/ci/perfect_score_census.py --prune               # drop entries whose site is gone
```

    pct = round(within / total_relevant * 100, 1) if total_relevant > 0 else 100.0
Nothing was scanned, nothing was assessed, and the page draws a full green bar
at 100%. STRICTLY WORSE THAN A MISSING NUMBER: a missing number prompts
somebody to go and measure, and a perfect one closes the question. Three of
the four defects fixed on 2026-08-20 were this same shape — rem-hyg-08 (a
project card over rows no epic claimed), cch-obs-03/ctx-obs-03 (cache and
governance rates nobody had measured), rem-hyg-09 (canvases nobody had
assessed).
THE FINDING IS A CONJUNCTION, never the literal alone: a 100.0 fallback arm
AND a body that computes a RATIO. Requiring the ratio is what keeps this
high-signal — `else 100.0` greps to 15 sites and TWO are not scores at all
(trading/data/fixture_provider.py a synthetic bar price;
trading/data/macro_data.py the US Dollar Index, whose BASE IS 100 by
definition). Both are excluded by the PREDICATE and hold NO exemption entry:
an exemption list is a claim a reviewer must check, a predicate is one the
scanner re-derives every run. Parsing to an AST disposes of the third grep
hit for free — canvas_compliance/posture.py:260 is a COMMENT inside the
rem-hyg-09 fix explaining this very defect, and a census whose first entry
was the previous fix's own explanation of itself would have discredited the
gate on day one.
The constant is the FLOAT 100.0 and NEVER the bare int, MEASURED: widening
adds ZERO true positives and adds one legitimate site needing an excuse —
trading/dashboard/app.py's RSI, which IS 100 with no down moves, by
definition. The broader `if X else 0` shape is deliberately NOT gated: 1,167
occurrences across 566 files, mostly ordinary counters and indices, and
refusing those refuses routine work — the exact defect the PreToolUse survey
found.
THE FIX is the convention already in the tree,
tools/quality/component_scorer.py::NOT_ASSESSED — return None, never a
number, and let the renderer say "not assessed". Compare against null
EXPLICITLY, never for truthiness: a MEASURED 0% is a real finding and must
keep rendering as a red bar.
ZERO GRANDFATHERED. All 12 ratio sites were FIXED here, so
args/perfect_score_census.txt is EMPTY, `perfect_score_max` in
args/perfect_score_gate.yaml is 0, and ANY entry breaches it. That is a
stronger posture than the raw-INSERT (219) and undeclared-import (210)
censuses could take, and only because 12 was small enough to drain — there is
no follow-up card because there is no census to drain.
