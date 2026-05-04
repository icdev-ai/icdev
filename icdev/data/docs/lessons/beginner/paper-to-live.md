# Paper trading vs real money

FathomDesk runs in **paper-trading mode** by default. That means every BUY
or SELL you place is tracked against real market prices but doesn't involve
actual money — it's a simulated account.

This is intentional. Here's why + how to think about graduating.

## Why paper mode is the default

1. **You'll make expensive mistakes early.** Every new investor does. Making
   those mistakes with simulated money is $0 cheaper than making them with
   real money.

2. **Learning the dashboard ≠ learning to invest.** You need to watch a few
   market cycles — up weeks, down weeks, the occasional 10% correction — to
   develop the emotional calluses that make real-money investing tolerable.

3. **It's a fair test of your process.** If your paper account is down 20%
   after 3 months, that's not the system's fault — that's your
   position-sizing, your entry-timing, your patience. Better to learn this
   on paper.

4. **Regulatory simplicity.** FathomDesk doesn't handle real money in paper
   mode, so there are no brokerage licensing issues, no custodial
   concerns, no tax reporting complexity.

## What "paper mode" actually simulates

- **Real prices** — when you BUY 10 shares of AAPL at $200, the system
  locks in $2,000 at the current real market price
- **Real P&L** — as AAPL moves, your paper position moves with it, and
  your unrealized gain/loss is real-time accurate
- **Real fills** — paper orders simulate slippage, bid-ask spread, and
  market-order vs limit-order semantics
- **NOT real money** — obviously. No brokerage account touched, no money
  moved, no taxes owed

## When should you graduate to live trading?

Short answer: **when you've had at least 3-6 months of paper experience
AND your paper results show process discipline, not luck.** Process
discipline looks like:

- You don't size > 10% into any single name
- You honor stop-losses when they trigger
- You didn't panic-sell during a 15%+ drawdown
- You know what regime you're in + why
- You can explain WHY you hold each position (not "it's going up")

Once you check those boxes, a reasonable graduation path:

1. Open a brokerage account that supports paper trading (Alpaca, IBKR, etc.)
2. Use FathomDesk's **BYOK** feature (`Settings → Your Broker Connection`)
   to point at YOUR Alpaca paper account — that mirrors FathomDesk's logic
   against your own separate sandbox
3. Run 1-3 months with YOUR paper account (no real money yet)
4. Flip Alpaca to live mode — start with 10% of your investable savings
5. Scale up only after another 3-6 months of real experience

## Phase 6 — the graduation gate (future)

Future FathomDesk will include a formal **graduation system** — CashFlow-101
style progression where Beginner users unlock live trading only after hitting
measurable paper-portfolio criteria (30+ paper days, 25+ analyses, ≥ 5
achievements, Sharpe ≥ 0.5 over 90d, acknowledged risk disclosure). This
isn't shipped yet but is on the roadmap.

Until then, the system relies on your own discipline to delay live trading
until you're really ready.

## The rule every beginner should tattoo on their wrist

> **You cannot get in a hurry.** Compound interest works IF you don't
> blow up the principal early. Going "slow" with $10K for 5 years
> beats going "fast" with $10K for 3 months. Every. Single. Time.

## What you've learned

Through this beginner track you now know:
1. What a stock actually is (claim on future profits + voting right)
2. How to read an FathomDesk signal (direction + composite + confidence)
3. Why concentration kills and how to size positions
4. What macro regime is + why it matters
5. Why paper mode first, live mode later (after discipline is proven)

That's a real foundation. Most retail investors never get this far.

## Next

The **Intermediate** track covers how FathomDesk specifically works — its
Reading engines, personas system, and how to use alerts. When you're ready,
it's on `/lessons`.
