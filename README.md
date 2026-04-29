# 🤖 Polybot — Claude-powered Prediction Market Trader

A paper-trading bot that uses Claude AI to find mispriced contracts on Polymarket,
combined with elite wallet copy-trading signals.

---

## What it does

1. **Fetches active markets** from Polymarket (filters to high-volume ones only)
2. **Tracks elite wallets** — finds the top traders by win rate and watches what they're betting on
3. **Asks Claude** to estimate the true probability of each market resolving YES
4. **Finds edges** — where Claude's estimate differs significantly from the market price
5. **Places paper trades** — simulated trades logged to a local database (no real money)
6. **Manages risk** — enforces position sizing, daily loss limits, and max positions

---

## Setup (step by step)

### Step 1: Make sure Python is installed
Open your terminal and run:
```bash
python3 --version
```
You need Python 3.11 or higher. If you don't have it, download from python.org.

### Step 2: Download this project
If you received this as a zip file, unzip it. Then open Terminal and navigate to the folder:
```bash
cd path/to/polybot
```

### Step 3: Create a virtual environment
A virtual environment keeps this project's dependencies separate from everything else:
```bash
python3 -m venv venv
source venv/bin/activate      # On Mac/Linux
# venv\Scripts\activate       # On Windows
```
You'll see `(venv)` appear in your terminal prompt — that means it's active.

### Step 4: Install dependencies
```bash
pip install -r requirements.txt
```

### Step 5: Get an Anthropic API key
1. Go to https://console.anthropic.com/
2. Sign up / log in
3. Go to "API Keys" → "Create Key"
4. Copy the key (starts with `sk-ant-...`)

Set it as an environment variable in your terminal:
```bash
export ANTHROPIC_API_KEY="sk-ant-your-key-here"
```
(Add this line to your `~/.zshrc` or `~/.bashrc` to make it permanent)

### Step 6: Run the bot
```bash
python main.py
```

You should see output like:
```
════════════════════════════════════════
  🤖 POLYBOT — Claude-powered Prediction Market Trader
  Mode: 📄 PAPER TRADING
  Started: 2026-04-08 14:30:00
  Press Ctrl+C to stop
════════════════════════════════════════

[14:30:01] Building elite wallet list...
[14:30:03] Tracking 12 elite wallets
[14:30:03] Scan #1 starting...
[14:30:03] Fetching active markets...
[14:30:05] Analysing 20 markets with Claude...
[14:30:45] Found 3 tradeable signals
[14:30:45] 📄 PAPER TRADE: YES on 'Will Fed cut rates in May 2026?...' | $30.00 @ 34.00%
```

Press `Ctrl+C` at any time to stop. Your trade history is always saved.

---

## Project structure

```
polybot/
├── main.py                  ← Entry point. Run this.
├── config.py                ← All settings. Edit this to tune the bot.
├── requirements.txt         ← Python packages needed
├── trades.db                ← SQLite database (created on first run)
├── polybot.log              ← Log file (created on first run)
│
├── data/
│   ├── polymarket.py        ← Fetches markets and prices from Polymarket
│   └── wallet_tracker.py   ← Finds and tracks elite wallets
│
├── signals/
│   └── claude_signal.py    ← Sends markets to Claude, gets probability estimates
│
├── execution/
│   └── paper_trader.py     ← Simulates trades (no real money)
│
└── risk/
    └── manager.py          ← Safety checks before every trade
```

---

## Key settings (config.py)

| Setting | Default | What it does |
|---|---|---|
| `PAPER_TRADING` | `True` | Keep True until you're confident in the bot |
| `STARTING_BALANCE` | `600.0` | Simulated starting balance in USD |
| `MIN_EDGE_TO_TRADE` | `0.12` | Min difference between Claude and market (12%) |
| `MAX_POSITION_PCT` | `0.05` | Max 5% of balance on any single trade |
| `DAILY_LOSS_LIMIT` | `0.10` | Stop trading if down 10% in one day |
| `SCAN_INTERVAL_SECONDS` | `60` | How often to scan for new opportunities |

---

## Understanding the output

- `YES @ 34.00%` — we paid 34 cents per share, meaning we think YES is worth more than 34%
- `Edge: +18%` — Claude thinks the chance is 52%, market says 34% → 18% edge
- `PnL: +$58.82` — if YES resolves correctly, we get $1/share back (profit = payout - cost)
- `Win Rate: 62%` — 62% of our closed trades resolved in our favour

---

## Viewing your trade history

Your trades are stored in `trades.db` (a SQLite file). You can view them with:
```bash
# Install a viewer (optional)
brew install sqlite  # Mac

# Open the database
sqlite3 trades.db

# Inside sqlite3, type:
.mode column
.headers on
SELECT question, direction, entry_price, size_usd, pnl, status FROM trades ORDER BY id DESC LIMIT 20;
.quit
```

---

## Honest expectations

- Paper trading for **at least 2-4 weeks** before considering real money
- Target: consistent win rate above 55% over 50+ closed trades
- Claude's reasoning is probabilistic — it will be wrong sometimes
- Polymarket edges are real but thin; the bot makes money through volume and discipline
- The €600→€10,000 articles are not realistic benchmarks

---

## Next steps (after paper trading looks good)

1. Add a Streamlit dashboard for visual monitoring
2. Integrate news APIs (NewsAPI, Perplexity) for better Claude context
3. Add market closing detection (to auto-close positions when markets resolve)
4. Consider Kalshi/Manifold arbitrage across platforms
