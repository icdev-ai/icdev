# How do I read an FathomDesk signal?

When you open `/analysis` and run an analysis on a ticker, FathomDesk produces
a **signal** with three key numbers:

- **Direction** — one of `BUY`, `SELL`, `SHORT`, or `HOLD`
- **Composite score** — a number from 0 to 100
- **Confidence** — a percentage from 0% to 100%

Here's how to read each.

## Direction — what the system thinks

`BUY` means the system's aggregate view is constructive. Multiple analysis
pillars (fundamentals, technicals, news, macro, sentiment) lean positive.

`SELL` / `SHORT` means the system expects the stock to decline. `SELL`
specifically means "close an existing position." `SHORT` means "bet on
decline" — a more advanced move that isn't enabled in paper mode.

`HOLD` means the signals are mixed. No action is a valid action.

## Composite score — how strong the conviction is

The composite is a weighted blend of 5-6 analyst pillars. Each pillar
scores 0-100; the composite aggregates them.

| Score range | Interpretation |
|---|---|
| 75+ | Strong-conviction `BUY` (or strong-conviction `SELL` if below 25) |
| 60-75 | Moderate `BUY` — worth considering, not scream-buy |
| 40-60 | Mixed / uncertain — probably `HOLD` |
| 25-40 | Moderate `SELL` / weak |
| <25 | Strong-conviction `SELL` |

A 50 is the fulcrum. If you're a beginner, **only act on ≥ 70 or ≤ 30**.
The mushy middle 40-60 band is where overconfident traders get hurt.

## Confidence — how sure the system is

Confidence is *different* from the composite score. Score = "which direction";
confidence = "how sure of that direction."

A `BUY` with composite 75 + confidence 40% means: "We think it's up, but
there's big disagreement inside the pillars. Don't size aggressively."

A `BUY` with composite 75 + confidence 85% means: "We think it's up, and
the pillars agree. This is a higher-conviction setup."

**Rule of thumb**: only act on signals where **both** composite AND confidence
are in the good zone. High-composite-but-low-confidence = wait for more data.

## Where to see it

- `/analysis` — run a signal on any ticker
- `/signals` — queue of recent signals across the universe
- `/portfolio` — reads for your book
- Signal details + the reasoning pillars

## What the system is NOT telling you

A signal is probabilistic, not predictive. BUY at 75/85 historically means
"this has worked more often than not." It doesn't mean "this will definitely
go up." **Position-sizing matters more than signal-picking** — a 5% position
in a wrong bet hurts less than a 30% position in a right-by-accident bet.

## Next

Speaking of position-sizing: **diversification** is lesson 3. It's boring.
It's also the single best protection against the signals being wrong.
