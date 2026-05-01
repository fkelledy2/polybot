# Deployment Monitoring — Post-Optimization (2026-05-01)

## What Changed
- **Added**: MIN_ENTRY_PROBABILITY = 0.03 (prevents trades at <3% probability)
- **Impact**: Should prevent unprofitable "impossible odds" trades
- **Deployed to**: Heroku v32 (2026-05-01 10:30 UTC)

## Historical Performance (Pre-Optimization)
- **Total trades**: 5
- **Won**: 0 (0.0%)
- **Lost**: 4 (-111.30 total PnL, -$30 avg loss per trade)
- **Open**: 1

### Why Losses Occurred
All 4 losing trades had entry probabilities **below 3%**, making them mathematically unprofitable:

| Trade | Direction | Entry Price | Outcome | Loss |
|-------|-----------|-------------|---------|------|
| 1 | NO (Yankees) | 0.50% | Lost | -$30.00 |
| 2 | YES (BTC $78k) | 1.30% | Lost | -$28.50 |
| 3 | YES (BTC $62k dip) | 1.30% | Lost | -$27.08 |
| 4 | NO (BTC $66k) | 0.15% | Lost | -$25.72 |

**New filter would have blocked all 4.** ✓

## What to Watch (Next 1-2 Weeks)

### Daily Checklist
- [ ] Dashboard still loading? (polybot-trader.herokuapp.com)
- [ ] No new error logs in activity panel?
- [ ] Are trades being proposed? (Signals panel)
- [ ] Are trades being executed? (Trade Feed updating?)

### Key Metrics to Track
1. **Trade Count**: Should see 2-5 new trades per week (if any signals meet criteria)
2. **Proposed vs Executed**: Compare signals panel (all) vs trades executed (filtered)
3. **Entry Prices**: All should be ≥3% (verify the filter is working)
4. **P&L Trend**: Should be climbing, not negative

### Red Flags 🚨
- No trades proposed for 3+ days (might indicate market shift or API issue)
- Trades with entry <3% (filter not working)
- Large negative PnL on new trades (strategy issue)
- Dashboard errors or missing data

## Success Criteria
After 1-2 weeks, we're successful if:
- ✓ At least 1 trade executed
- ✓ All executed trades have entry ≥3%
- ✓ Win rate on new trades > 50% (or trending positive)
- ✓ Total portfolio value stable or increasing

## If Things Go Wrong

### No trades proposed
1. Check dashboard for errors
2. Check logs: `heroku logs --tail -a polybot-trader`
3. Verify Polymarket API is accessible
4. Check Claude API credits (may be needed for live trading signals)

### Trades with entry <3%
1. Verify claude_signal.py line 169-172 is checking MIN_ENTRY_PROBABILITY
2. Check config.py has MIN_ENTRY_PROBABILITY = 0.03
3. Restart dyno: `heroku restart -a polybot-trader`

### Consistent losses
1. Edge threshold might be too low (current: 0.10)
2. Market conditions changed (crypto volatility, etc.)
3. Run new grid search when API credits refreshed (contact Anthropic)

## Next Optimization Window
After 1-2 weeks of live data:
- Review new trade performance
- Identify any new patterns in losses
- If API credits restored: run deep dive grid search on newest resolved markets
- Adjust MIN_EDGE_TO_TRADE if win rate <50%

## One-Time Setup (Already Done)
- [x] MIN_ENTRY_PROBABILITY added to config.py
- [x] Signal generation updated to enforce threshold
- [x] Days-to-resolve filtering added (for future optimization)
- [x] Grid search framework created for future local optimization
- [x] Deployed to production

---

**Monitoring Period**: 2026-05-01 through 2026-05-15 (2 weeks)

**Owner**: Trader monitoring script or manual daily check

**Escalation**: If red flags appear, pause trading and investigate before deploying fixes.
