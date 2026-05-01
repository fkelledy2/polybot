# Parameter Optimization via Local Backtesting

This guide explains how to use the grid search and deep dive backtesting tools to find optimal parameter configurations locally before deploying to production.

## Quick Start

### 1. Breadth Search (Initial Exploration)
Test a wide range of parameters across 200 historical markets:

```bash
python backtest/grid_search.py --markets 200
```

This tests:
- **Edge thresholds**: 0.05, 0.10, 0.15, 0.20
- **Entry probabilities**: 0.02, 0.03, 0.05, 0.10
- **Max days**: 7, 14, 30
- **Total variants**: 48 configurations

Results are printed to terminal and saved to `grid_search_results.json`.

### 2. Depth Search (Focused Optimization)
Once you've identified a promising region, narrow down with finer resolution:

```bash
# Example: if grid search showed 0.10 edge + 0.03 entry prob were best
python backtest/deep_dive.py --markets 300 \
  --focus-edge 0.08-0.14 \
  --focus-entry 0.02-0.05
```

This uses more markets (300) and finer granularity (7×6×5 = 210 variants) to pinpoint the optimal configuration.

## How It Works

### Grid Search Workflow

1. **Fetch Markets**: Retrieves 200+ resolved markets from Polymarket API
2. **Test Each Variant**: For each parameter combination:
   - Simulates trades using Claude's historical probability estimates
   - Tracks: trades executed, win rate, avg PnL per trade, total PnL
3. **Report Results**: Sorted by average PnL (descending)
4. **Recommend Best**: Highlights the configuration with highest avg PnL

### Key Metrics

- **Win Rate**: % of trades that correctly predicted the outcome
- **Avg PnL**: Average profit/loss per trade (at 50% entry price)
- **Total PnL**: Sum of all simulated P&L
- **Directional Accuracy**: % of trades with correct YES/NO direction

### Important Assumptions

- All simulations use **50% entry price** (neutral midpoint)
- Real trading varies based on actual market prices
- Confidence="low" signals are always rejected
- Time horizon filtering applied per configuration

## Parameter Meanings

### MIN_EDGE_TO_TRADE
Minimum edge (Claude prob - market price) required to trade.

- **0.05**: More aggressive, includes marginal edges
- **0.10**: Current setting, balanced
- **0.15**: Conservative, only clear mispricings
- **0.20**: Very conservative, high confidence only

### MIN_ENTRY_PROBABILITY
Minimum probability of the direction you're betting on (e.g., if betting YES, minimum YES probability).

- **0.02**: Allows very unlikely events (e.g., 2% probability bets) — risky
- **0.03**: Practical minimum (need ~55% accuracy to break even)
- **0.05**: More conservative (need ~47% accuracy at 2:1 odds)
- **0.10**: Very conservative (need ~45% accuracy)

### MAX_DAYS_TO_RESOLVE
Skip markets resolving beyond this many days (faster feedback loops).

- **7 days**: Fast feedback, but may exclude major events
- **14 days**: Good balance of feedback speed vs. opportunities
- **30 days**: More opportunities, slower learning cycles

## Workflow

```
1. Run Grid Search (200 markets, 48 variants)
   ↓ Identify promising region (e.g., edge 0.08-0.14)
   ↓
2. Run Deep Dive (300 markets, 210 variants, finer grid)
   ↓ Narrow to single best configuration
   ↓
3. Update config.py with best parameters
   ↓
4. Deploy to production
   ↓
5. Monitor live performance
   ↓
6. Re-optimize every 1-2 weeks as you get more resolved trades
```

## Reading Results

Example output:
```
═════════════════════════════════════════════════════════════════════════════════════════════════════
  VARIANT TESTING RESULTS (sorted by avg PnL)
═════════════════════════════════════════════════════════════════════════════════════════════════════
   Edge  Min Entry%  Max Days   Trades   Win%    Avg PnL  Total PnL
  ────────────────────────────────────────────────────────────────────────────────────────────────
   10%       3%         14       47    61.7%   +0.0245    +1.1515
   10%       3%          7       31    64.5%   +0.0218    +0.6758
   12%       3%         14       38    63.2%   +0.0156    +0.5928
   ...
   
   🏆 BEST VARIANT:
      MIN_EDGE_TO_TRADE = 0.10
      MIN_ENTRY_PROBABILITY = 0.03
      MAX_DAYS_TO_RESOLVE = 14
      
      Performance: 47 trades, 61.7% win rate, +0.0245 avg PnL
```

**Top result shows**: At 50% entry, with 10% edge threshold and 3% minimum entry probability, the bot would win 61.7% of trades with +2.45¢ average profit per trade.

## Cost Estimation

Using **Claude Haiku** (cheap):
- Each market batch (20 markets): ~$0.05
- Grid Search (200 markets, 48 variants): ~$500 worth of Claude API but very inexpensive
- Deep Dive (300 markets, 210 variants): ~$3,000 API calls but still manageable

If API credits are limited, use smaller `--markets` values:
```bash
python backtest/grid_search.py --markets 50   # Quick rough optimization
python backtest/deep_dive.py --markets 100    # Deeper dive, smaller scope
```

## Interpreting Trade Count Differences

Different parameter combinations will generate different trade counts:

- **High edge threshold + high entry prob**: Fewer, higher-confidence trades
- **Low edge threshold + low entry prob**: More trades, lower confidence

A variant with fewer trades but higher win rate isn't necessarily better — check avg PnL instead.

## Next Steps

1. Run: `python backtest/grid_search.py --markets 200`
2. Examine results for best configuration
3. If clear winner, update `config.py` and deploy
4. If inconclusive, run deep dive around promising region
5. Monitor live performance over 1-2 weeks
6. Re-run backtest monthly with latest market data

---

Questions? Check the grid search results JSON file for detailed per-variant stats.
