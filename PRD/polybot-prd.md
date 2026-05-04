# Polybot — Product Requirements Document

**Version:** 1.0  
**Date:** 2026-05-04  
**Status:** Live (paper trading)  
**Deployment:** Heroku (`polybot-trader-89bba5ed2d0b.herokuapp.com`)

---

## 1. Overview

Polybot is an autonomous prediction market trading bot that uses Claude (Anthropic's LLM) to identify and trade mispricings on [Polymarket](https://polymarket.com) — a decentralised binary-outcome prediction market. Markets resolve at $1.00 (YES) or $0.00 (NO).

The system runs 24/7 on Heroku, scanning live markets every 60 seconds, generating probability estimates via Claude, and executing paper trades when the estimated probability materially differs from the market price (the "edge"). A Flask web dashboard provides real-time visibility into signals, positions, performance, and costs.

---

## 2. Problem Statement

Prediction markets are often mispriced due to:
- Liquidity gaps and thin order books on niche questions
- Retail participants using gut feel rather than base rates
- Information asymmetry — publicly available data not reflected in prices

A systematic, LLM-powered approach can identify these mispricings faster and more consistently than a human trader, particularly across dozens of concurrent markets.

---

## 3. Goals

### Primary
- Identify markets where Claude's probability estimate diverges from the market price by ≥6% ("edge")
- Execute paper trades to validate the approach before committing real capital
- Maintain a win rate >55% on closed positions

### Secondary
- Track running infrastructure costs and provide visibility in the dashboard
- Run daily automated analysis of trading performance and auto-implement improvements to config
- Provide a mobile-optimised view for on-the-go monitoring

### Out of Scope (current version)
- Live money trading (paper trading only)
- Polymarket wallet API integration (leaderboard API currently unavailable)
- Portfolio rebalancing or position sizing updates post-entry

---

## 4. Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                        main.py (scan loop)                    │
│                                                              │
│  Every 60s:                                                  │
│  1. Fetch markets (Polymarket API)                           │
│  2. Enrich (crypto/macro/news/sports odds/Metaculus)         │
│  3. Analyse via Claude (batch prompt, 20 markets)            │
│  4. Confirm high-edge signals (extended thinking)            │
│  5. Check stop-losses                                        │
│  6. Resolve positions (every 5 scans)                        │
│  7. Place trades (paper)                                     │
│  8. Update dashboard                                         │
└──────────────────────────────────────────────────────────────┘
           │                              │
    ┌──────▼──────┐               ┌──────▼──────┐
    │  Postgres   │               │  Flask      │
    │  (Heroku)   │               │  Dashboard  │
    │  trades     │               │  :8080      │
    │  balance_log│               └─────────────┘
    │  predictions│
    │  price_hist │
    └─────────────┘
```

### Module Map

| Module | Purpose |
|---|---|
| `main.py` | Scan loop orchestrator |
| `config.py` | All tunable parameters (single source of truth) |
| `db.py` | SQLite/Postgres abstraction (placeholder, schema adaptation) |
| `data/polymarket.py` | REST client for Gamma API and CLOB API |
| `data/clob_stream.py` | WebSocket real-time price feed (CLOB) |
| `data/enrichment/` | Market context enrichers (crypto, macro, news, sports, Metaculus, search) |
| `data/wallet_tracker.py` | Elite wallet copy-trading (currently disabled) |
| `signals/claude_signal.py` | LLM prompt, signal parsing, batch + confirmation |
| `signals/categorizer.py` | Keyword-based market categorisation (8 categories) |
| `signals/clustering.py` | Correlation-aware cluster grouping for risk |
| `signals/arbitrage.py` | Complementary market pair detection |
| `execution/paper_trader.py` | Trade placement, Kelly sizing, position management |
| `execution/resolver.py` | Position resolution and stop-loss checking |
| `risk/manager.py` | Daily loss circuit-breaker, cluster exposure limits |
| `backtest/` | Optimizer, Dune-backed historical backtesting, forward tracker |
| `analysis/` | Scheduled performance analyser and autonomous improvement executor |
| `web/app.py` | Flask routes and SSE log streaming |
| `web/templates/` | Dashboard (desktop `index.html`, mobile `mobile.html`) |
| `web/costs.py` | SaaS cost estimator |
| `notifications/` | Discord webhook + email alerts |

---

## 5. Feature Modules

### 5.1 Market Data (S1)

**Polymarket REST Client** (`data/polymarket.py`)
- Fetches top markets by volume from `gamma-api.polymarket.com`
- Filters by: `min_volume=$5,000`, `max_days_to_resolve=16`, `min_days_to_resolve=1`
- Every 5 scans, appends newly listed markets (<48h old, min $5k volume) tagged `is_new_market=True`
- Parses YES/NO prices, volume, days-to-resolve, category, CLOB token IDs

**Real-Time Price Feed** (`data/clob_stream.py`)
- WebSocket connection to Polymarket CLOB (`wss://ws-subscriptions-clob.polymarket.com`)
- In-memory price cache updated on each event
- Prices injected into market list at the top of each scan, superseding REST prices
- Fails gracefully — REST prices used if WebSocket unavailable

**Price Momentum Tracking** (`backtest/tracker.py`)
- Records YES prices for all scanned markets to `price_history` table
- Computes 24h velocity: `(current_price - price_24h_ago) / price_24h_ago`
- Velocity injected into market context for Claude

---

### 5.2 Market Enrichment (S2)

Category-specific context is fetched in parallel threads and injected into the Claude prompt alongside each market question.

| Category | Enrichers |
|---|---|
| CRYPTO | CoinGecko prices (BTC, ETH, SOL), market cap, 24h change |
| MACRO | Fed funds rate, CPI, unemployment, SPY/VIX prices |
| TECH | SPY/VIX backdrop |
| SPORTS | Odds API (spread, moneyline, totals) — requires `ODDS_API_KEY` |
| POLITICS / GEO / MACRO / TECH | Metaculus expert consensus — free API, no key required |
| ALL | NewsAPI headlines filtered by question keywords |
| ALL | Brave Search web results — requires `BRAVE_SEARCH_API_KEY` |

All enrichers fail gracefully — a timeout or API error returns an empty string and does not block the scan.

**Arbitrage Pair Detection** (`signals/arbitrage.py`)
- Compares YES prices of market pairs sharing ≥3 keywords
- Flags pairs where implied sum is >1.05 ("overpriced") or <0.95 ("underpriced")
- Injects a `ARBIT:` note into the enrichment context for Claude

---

### 5.3 Signal Generation (S1–S4)

**Claude Analysis** (`signals/claude_signal.py`)

- Model: `claude-haiku-4-5-20251001` (configurable)
- Prompt structure:
  - Static system prompt (cached across calls via Anthropic prompt caching)
  - Category-specific guidance blocks (8 categories)
  - Per-market: question, current YES price, days-to-resolve, volume, price velocity, category, wallet alignment note, enrichment context
- Batch: 20 markets per Claude call, JSON array response
- Output per market: `yes_probability`, `confidence` (high/medium/low), `reasoning` (one sentence)

**Signal Filtering**
- `edge = claude_yes_probability - market_yes_price`
- Trade if `abs(edge) >= MIN_EDGE_TO_TRADE` (6%)
- Extreme price markets (YES < 3% or > 97%): require `MIN_EDGE_TO_TRADE_EXTREME` (20%)
- Skip if `claude_yes_probability < MIN_ENTRY_PROBABILITY` (3%)
- Skip if market category in `DISABLED_CATEGORIES` (currently: `['CRYPTO']`)
- Skip if market on stop-loss cooldown (10 scans after stop-loss)

**High-Edge Confirmation** (`signals/claude_signal.py` — S2)
- Signals with `abs(edge) > 0.25` are re-analysed using `claude-sonnet-4-6` with extended thinking
- Confirmation may increase or decrease the edge — result replaces the original estimate

**Wallet Veto** (S2)
- When `ENABLE_WALLET_VETO=True`, signals are suppressed if elite wallet positions disagree
- Currently passive (wallet tracking API unavailable)

**Market Categorisation** (`signals/categorizer.py`)
- Keyword regex matching against 8 categories: CRYPTO, SPORTS, POLITICS, MACRO, TECH, ENTERTAINMENT, GEO, OTHER
- Used for enrichment routing, category-specific Claude guidance, and `DISABLED_CATEGORIES` filtering

**Correlation Clustering** (`signals/clustering.py`)
- Groups markets by shared keywords into clusters
- Risk manager caps exposure to 15% of portfolio per cluster

---

### 5.4 Trade Execution (S1, S4)

**Paper Trader** (`execution/paper_trader.py`)
- Simulates trades with `STARTING_BALANCE=$1000`
- Position sizing: half-Kelly criterion with confidence multiplier (high=1.0×, medium=0.75×, low=0.5×), capped at `MAX_POSITION_PCT=5%`
- Prevents duplicate entries (one open position per market)
- Max `MAX_OPEN_POSITIONS=10` concurrent positions
- Persists all trades and balance snapshots to database
- `portfolio_value = cash_balance + sum(open_position_entry_costs)`

**Stop-Loss** (`execution/resolver.py` — S4)
- Runs every scan before new trades
- Closes a position if adverse price movement ≥ `2 × abs(entry_edge)`
  - YES trade: `entry_price - current_yes ≥ 2 × abs_edge`
  - NO trade: `entry_NO_price - current_NO_price ≥ 2 × abs_edge`
- After stop-loss: 10-scan cooldown prevents re-entry on same market
- PnL: `shares × exit_price - size_usd`

**Position Resolution** (`execution/resolver.py`)
- Runs every 5 scans
- Queries Polymarket API for resolution status of each open position
- Closes won/lost positions at $1.00 or $0.00 respectively

**Risk Manager** (`risk/manager.py`)
- Daily loss circuit-breaker: halts trading if daily loss ≥ `DAILY_LOSS_LIMIT=10%`
- Resets at midnight
- Cluster exposure cap: blocks trades if cluster already at ≥15% of portfolio

---

### 5.5 Backtesting (S2, S4)

**Dune Analytics Backtest** (`backtest/dune_fetcher.py`, `backtest/optimizer.py`)
- Fetches resolved markets from Dune Analytics (`polymarket_polygon` schema)
- Entry price = average YES-token price 3–14 days before resolution (realistic bot entry window)
- Lookback window: configurable (default 180 days), up to 1,000 markets per run
- Cost: ~5–15 Dune credits per run (free tier: 2,500/month)

**Parameter Optimizer** (`backtest/optimizer.py`)
- Calls Claude once to estimate probabilities for all fetched markets (cached to disk)
- Pure-Python sweep over parameter combinations: `min_edge`, `max_days_to_resolve`, `disabled_categories`
- Optimises for Sharpe ratio (or win rate) across the historical dataset
- Current optimised values: `MIN_EDGE_TO_TRADE=0.06`, `MAX_DAYS_TO_RESOLVE=16`

**Forward Tracker** (`backtest/tracker.py`)
- Logs every live Claude signal to `predictions` table
- When markets resolve, scores each prediction (correct direction? simulated PnL?)
- Accessible via `/api/backtest/tracker` endpoint

---

### 5.6 Analysis & Autonomous Improvement (S3)

**Scheduled Analyser** (`analysis/scheduled_agent.py`)
- Runs daily via Heroku Scheduler (`python3 analysis/scheduled_agent.py`)
- Reads production trade history from Postgres
- Generates a structured JSON report: overall metrics, per-category breakdown, critical issues, improvement plan
- Saves report to both `analysis_reports` Postgres table (30-report rolling window) and `.claude/analysis_report.json`
- Sends summary embed to Discord if `DISCORD_WEBHOOK_URL` is set

**Performance Analyser** (`analysis/performance.py`)
- Computes win rate, average PnL, calibration metrics by category
- Identifies underperforming categories and calibration drift

**Improvement Executor** (`analysis/executor.py`)
- Autonomously applies safe improvements to `config.py` when data supports them:
  - Extreme price filter parameters
  - Category disabling for persistent underperformers
  - Elite wallet veto activation
  - Calibration feedback loop toggle
- Each executor has an "already applied" guard to prevent duplicate config mutations

---

### 5.7 Web Dashboard

**Desktop** (`web/templates/index.html`)
- Auto-redirects to `/mobile` if screen width < 768px (unless manually overridden)
- Password-protected login (session cookie)

**Panels:**

| Panel | Data Source | Refresh |
|---|---|---|
| Stats bar (balance, P&L, win rate, scan count) | `/api/stats` | Every 5s |
| AI Signals table | `/api/signals` | Every 5s |
| Trade Feed | `/api/trades` | Every 10s |
| Open Positions | `/api/positions` | Every 10s |
| Realized P&L chart | `/api/pnl-history` | Every 60s |
| Build Log / Improvements | `/.claude/analysis_report.json` | On load |
| Activity Log (SSE stream) | `/api/logs/stream` | Real-time |
| Costs | `/api/costs` | On load |
| Backtest Results | `/api/backtest/latest`, `/api/backtest/tracker` | On load |

**Realized P&L Chart**
- X axis: time of trade close
- Y axis: cumulative `STARTING_BALANCE + sum(pnl)` across all closed trades
- Intentionally excludes open position costs to avoid oscillation artefact

**Mobile** (`web/templates/mobile.html`)
- Auto-redirects to `/` if screen width ≥ 768px (unless manually overridden)
- Condensed single-column layout: account status, recent trades, open positions
- Account status states: NEW (<3 trades), ACTIVE, CRIT (win rate <40%)

---

### 5.8 Notifications

**Discord** (`notifications/discord.py`)
- High-edge signals (`abs(edge) > 20%`): trade opportunity alert
- Stop-loss triggered: position closed notification
- Daily P&L summary (every 24 scans)
- Daily analysis report summary embed
- System events: halt, crash, restart, deployment

**Email** (`notifications/email.py`)
- API credit exhaustion, system crashes, halt events, deployment notifications
- Requires SMTP configuration

---

### 5.9 Cost Tracking (`web/costs.py`)

Estimates running costs displayed on the dashboard:

| Service | Basis | Pricing |
|---|---|---|
| Anthropic Claude Haiku | Token usage estimated from trade count | $0.80/MTok input |
| Brave Search API | Per query (if enabled) | $1.00/1,000 queries |
| Odds API | Per request (if enabled) | $4.99/1,000 requests |
| Heroku Dyno | Monthly flat | $7.00/month |

---

## 6. Configuration Reference (`config.py`)

All parameters live in a single file. No code changes needed to tune the bot.

| Parameter | Default | Description |
|---|---|---|
| `CLAUDE_MODEL` | `claude-haiku-4-5-20251001` | Model for market analysis |
| `PAPER_TRADING` | `True` | Safety flag — must be `False` for live money |
| `STARTING_BALANCE` | `$1,000` | Initial simulated balance |
| `MIN_EDGE_TO_TRADE` | `0.06` | Minimum edge to place a trade (6%) |
| `MIN_EDGE_TO_TRADE_EXTREME` | `0.20` | Edge required for <3% or >97% markets |
| `EXTREME_PRICE_THRESHOLD` | `0.03` | Price boundary for extreme filter |
| `MIN_ENTRY_PROBABILITY` | `0.03` | Never trade below this probability |
| `MAX_POSITION_PCT` | `0.05` | Max 5% of bankroll per trade |
| `DAILY_LOSS_LIMIT` | `0.10` | Halt if down 10% in one day |
| `MAX_OPEN_POSITIONS` | `10` | Max concurrent open positions |
| `MAX_DAYS_TO_RESOLVE` | `16` | Skip markets resolving >16 days away |
| `MIN_DAYS_TO_RESOLVE` | `1` | Skip markets resolving <1 day away |
| `DISABLED_CATEGORIES` | `['CRYPTO']` | Categories excluded from trading |
| `SCAN_INTERVAL_SECONDS` | `60` | Time between full scans |
| `ENABLE_WALLET_TRACKING` | `False` | Elite wallet copy-trading |
| `ENABLE_WALLET_VETO` | `True` | Suppress trades on wallet disagreement |
| `TOP_WALLETS_TO_TRACK` | `20` | Number of elite wallets to monitor |
| `MIN_WIN_RATE` | `0.55` | Minimum wallet win rate for "elite" status |
| `TRACK_CALIBRATION` | `True` | Log probability estimates for calibration |

### Optional API Keys (env vars)

| Variable | Feature enabled |
|---|---|
| `ANTHROPIC_API_KEY` | Core LLM analysis (required) |
| `BRAVE_SEARCH_API_KEY` | Web search enrichment per market |
| `ODDS_API_KEY` | Sports betting line data |
| `DISCORD_WEBHOOK_URL` | Discord alerts and daily reports |
| `DATABASE_URL` | Heroku Postgres (auto-set by Heroku add-on) |
| `DUNE_API_KEY` | Backtesting with real historical prices |
| `DASHBOARD_USERNAME` | Web dashboard login |
| `DASHBOARD_PASSWORD` | Web dashboard password |
| `FLASK_SECRET_KEY` | Session signing key |

---

## 7. Data Model

### `trades`
| Column | Type | Description |
|---|---|---|
| `id` | int PK | Auto-increment |
| `market_id` | text | Polymarket condition ID |
| `question` | text | Market question |
| `direction` | text | YES or NO |
| `entry_price` | real | Price at trade entry |
| `size_usd` | real | Dollar size of position |
| `shares` | real | Tokens purchased |
| `timestamp` | text | Entry timestamp (ISO 8601) |
| `status` | text | open / won / lost |
| `exit_price` | real | Price at close (0 or 1 if resolved) |
| `pnl` | real | Realised profit/loss in USD |
| `reasoning` | text | Claude's one-sentence rationale |
| `closed_at` | text | Close timestamp |
| `edge` | real | Entry edge (claude_prob - market_price) |

### `balance_log`
Appended on every trade open and close. Used for balance restoration on restart.

### `predictions`
Every Claude signal logged per scan for forward-test calibration tracking.

### `price_history`
YES price snapshots per market per scan. Used for 24h velocity and forward resolution.

### `analysis_reports`
Rolling window of 30 daily analyser JSON reports. Web route reads latest on load.

---

## 8. Deployment

### Heroku

- **Web dyno**: runs `main.py` (scan loop + Flask dashboard)
- **Scheduler dyno**: daily `python3 analysis/scheduled_agent.py`
- **Add-ons**: Heroku Postgres (shared), Heroku Scheduler
- Dyno isolation: web and scheduler dynos share only Postgres (no shared filesystem)

### Local

```bash
cp .env.example .env      # add ANTHROPIC_API_KEY minimum
python main.py            # dashboard at http://localhost:8080
```

### Procfile

```
web: python main.py
```

---

## 9. Known Limitations & Open Issues

| Issue | Status | Notes |
|---|---|---|
| Wallet tracking disabled | Blocked | Polymarket leaderboard API returns 404 |
| CLOB prices-history dead for resolved markets | Confirmed | Returns `{"history":[]}` for all closed markets — Dune used instead |
| Heroku dyno restarts reset in-memory state | Accepted | Stop-loss cooldown dict and scan counter reset on restart |
| Kelly sizing uses balance at trade time, not portfolio value | Known gap | Oversizes during drawdowns if many positions open |
| Anthropic cost estimate is approximate | Accepted | Based on trade count proxy, not actual token usage |
| Email notifications require SMTP config | Manual step | Not yet configured in production |

---

## 10. Roadmap (Candidate Features)

| Feature | Description | Priority |
|---|---|---|
| Live trading mode | Switch `PAPER_TRADING=False`, connect Polymarket wallet | High |
| Real token cost tracking | Use Anthropic usage API for accurate cost data | Medium |
| Wallet tracking re-enable | Monitor Polymarket API status, re-enable when available | Medium |
| Calibration dashboard tab | Visual view of prediction accuracy by category and edge bucket | Medium |
| Position P&L update | Show mark-to-market value of open positions at current prices | Medium |
| Stop-loss cooldown persistence | Persist cooldown set to Postgres so dyno restarts don't reset it | Low |
| Multi-model A/B testing | Compare Haiku vs Sonnet on identical markets | Low |
| Telegram notifications | Alternative to Discord for mobile-first alerts | Low |
