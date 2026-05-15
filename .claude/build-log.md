# Polybot Build & Improvement Log

---

## Cycle 1 — 2026-05-09

**Heroku release:** v80 (commit `a5204e40`)
**Analysis window:** 2026-05-04 to 2026-05-09 (5 days live)
**Next recommended run:** 2026-05-23 (after ~20 more closed trades with new parameters)

### Baseline Metrics (Cycle 1 — established this run)

| Metric | Value |
|---|---|
| Closed trades (real) | 9 |
| Win rate | 3/9 = 33.3% |
| Gross wins | +$142.65 |
| Gross losses | -$194.68 |
| Net realized PnL | -$52.03 |
| Avg PnL/trade | -$5.78 |
| Balance | $625.16 (started $1,000) |
| Open positions | 10 (capital at risk: $322.81) |

Note: IDs 1, 2, 3 (IONQ duplicates, $0 pnl each) excluded from real trade count.
The duplicate bug was caused by SCAN_INTERVAL_SECONDS=60 re-entering the same market
before position dedup was confirmed. Fixed by raising scan interval to 600s.

### Production Win/Loss Analysis

**Wins (3 trades, +$142.65):**
| ID | Trade | Entry | PnL |
|---|---|---|---|
| 40 | Elon <40 tweets May 4-6 (YES) | 24.5% | +$86.16 |
| 70 | Cesena FC NO | 36.0% | +$41.49 |
| 42 | Elon 180-199 tweets May 1-8 (NO) | 70.0% | +$14.99 |

Winning pattern: mid-range entry prices (24%-70%), social media / sports categories (pre-SPORTS disable), market-level logic rather than Claude's stale company data.

**Losses (6 real trades, -$194.68):**
| ID | Trade | Entry | PnL | Root cause |
|---|---|---|---|---|
| 4 | IONQ NO | 11.8% | -$50.00 | Extreme longshot + Claude lacks current earnings data |
| 34 | Uber YES | 10.0% | -$47.50 | Extreme longshot + Claude lacks current earnings data |
| 101 | Angels YES (baseball) | 40.5% | -$31.01 | SPORTS; bad elite wallet signal |
| 41 | Prizmic NO (tennis) | 10.5% | -$22.70 | SPORTS + sub-15% entry |
| 67 | Boulter YES (tennis) | 29.5% | -$22.25 | SPORTS; thin elite signal (1 trader) |
| 43 | Elon 220-239 tweets (YES) | 6.0% | -$21.22 | Sub-15% extreme longshot |

### Parameter Changes (this cycle)

Parameters applied earlier in this session (not re-applied here):

| Parameter | Before | After | Rationale |
|---|---|---|---|
| MIN_ENTRY_PROBABILITY | 0.03 | 0.15 | Blocks sub-15% longshots; would have prevented 4/6 losses (-$141.42) |
| DISABLED_CATEGORIES | ['CRYPTO'] | ['CRYPTO', 'SPORTS'] | SPORTS had 25% WR across 4 trades |
| KELLY_FRACTION | 0.50 | 0.35 | Reduce position volatility on uncertain entries |
| SCAN_INTERVAL_SECONDS | 60 | 600 | ~90% API cost reduction; prevents market re-entry duplicates |
| Sonnet confirmation edge threshold | 0.20 | 0.30 | Max 1 Sonnet call/scan |
| Signal cache | none | >2% price move | Avoids re-analysing unchanged markets |

Parameter applied this run:

| Parameter | Before | After | Rationale |
|---|---|---|---|
| DISABLED_CATEGORIES | ['CRYPTO', 'SPORTS'] | ['CRYPTO', 'SPORTS', 'EARNINGS'] | EARNINGS had 0W/2L, -$97.50 (50% of all losses); Claude has no fresh earnings data |

### Backtest

- **Run:** YES (cached data, $0.00 cost)
- **Baseline (prior cycle, 2026-05-04):** win_rate=71.4%, avg_pnl=+0.3975
- **Cycle 1 result:** current params confirmed optimal vs all neighborhood perturbations
- **Optimizer suggestion:** re-enable SPORTS (delta +0.0004) — REJECTED
  - Rationale: synthetic backtest prices don't model the real SPORTS loss pattern
  - 5-day production sample too thin to override empirical SPORTS disable
  - Re-evaluate SPORTS after 20+ post-filter closed trades

### Tests Added

File: `tests/unit/test_categorizer.py` (+17 tests, +47 lines)

- 8 EARNINGS detection tests (covering beat/EPS/revenue/Q1/surprise patterns)
- 5 Cycle 1 regression guards: EARNINGS/SPORTS/CRYPTO disabled, MIN_ENTRY_PROBABILITY>=15%, KELLY_FRACTION<=35%
- Updated `test_all_categories_have_context` to cover EARNINGS

Full suite: 304 pass, 1 pre-existing failure (unrelated — `test_signal_filters.py::TestExtremePriceFilter::test_extreme_price_with_sufficient_edge_trades`, pre-dates this cycle).

### Files Modified

| File | Change |
|---|---|
| `config.py` | DISABLED_CATEGORIES += 'EARNINGS' |
| `signals/categorizer.py` | EARNINGS patterns + context block; fixed `estimates?` regex |
| `tests/unit/test_categorizer.py` | 8 EARNINGS tests + 5 regression guards |

### Deployment

- Commit: `a5204e40`
- Heroku release: v80
- Deployed: 2026-05-09 11:25 BST
- Status: confirmed live

### Next Cycle Triggers

Re-run when ANY of the following:
1. 20+ additional closed trades (gives sufficient sample for SPORTS re-evaluation)
2. Cumulative realized PnL delta exceeds ±$100 from current -$52.03
3. Any new category consistently wins/loses 3+ trades
4. New parameter changes proposed (requires backtest re-run with --force-refresh)

---
