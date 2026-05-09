# config.py
# ─────────────────────────────────────────────────────────────
# All the settings for your bot live here.
# You only need to edit THIS file to configure the whole project.
# ─────────────────────────────────────────────────────────────

import os
from dotenv import load_dotenv

load_dotenv()  # Loads variables from .env into os.environ

# ── Anthropic (Claude) ────────────────────────────────────────
# Get your key from: https://console.anthropic.com/
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "your-key-here")

# Model to use for market analysis.
# Haiku is ~20x cheaper than Opus and perfectly capable for probability estimation.
#   claude-haiku-4-5-20251001   — fastest, cheapest  (~$0.80/$4 per MTok in/out)
#   claude-sonnet-4-6           — balanced            (~$3/$15 per MTok in/out)
#   claude-opus-4-6             — most capable        (~$15/$75 per MTok in/out)
CLAUDE_MODEL = "claude-haiku-4-5-20251001"

# ── Paper Trading ─────────────────────────────────────────────
# We start with fake money so you can test without risk.
PAPER_TRADING = True          # Set to False only when you're ready for real money
STARTING_BALANCE = 1000.0     # USD — fresh account reset

# ── Signal Settings ───────────────────────────────────────────
# Claude will only suggest a trade if the "edge" is big enough.
# Edge = (Claude's confidence) minus (market's implied probability)
# Example: Claude thinks 70% chance, market prices it at 52% → edge = 18%
MIN_EDGE_TO_TRADE = 0.06      # 6% minimum edge before placing a trade (Dune-optimized)
# ── Extreme Price Markets ───────────────────────
# Markets at extreme prices (<3% or >97%) require higher edge.
MIN_EDGE_TO_TRADE_EXTREME = 0.20  # 20% edge for extreme prices (backtest-optimized)
EXTREME_PRICE_THRESHOLD = 0.03       # Markets below 3% or above 97%

MIN_ENTRY_PROBABILITY = 0.15   # Never enter a trade at <15% implied probability

# ── Risk Management ───────────────────────────────────────────
MAX_POSITION_PCT  = 0.05      # Never risk more than 5% of bankroll on one trade
DAILY_LOSS_LIMIT  = 0.10      # Stop trading for the day if down 10%
MAX_OPEN_POSITIONS = 10       # Don't hold more than 10 markets at once

# ── Wallet Copy-Trading ───────────────────────────────────────
# Discovery via Playwright scrape of polymarket.com/leaderboard __NEXT_DATA__.
ENABLE_WALLET_TRACKING = True

# ── Wallet Veto Signal ─────────────────────────
# Disable trades when elite wallets disagree with Claude.
ENABLE_WALLET_VETO = True
WALLET_VETO_ON_EXTREME = True  # Especially for extreme prices
# How many top wallets to track
TOP_WALLETS_TO_TRACK = 20
# Alpha decay threshold — exclude positions where this fraction of upside is gone
ALPHA_DECAY_THRESHOLD = 0.25
# Minimum win rate for a wallet to be considered "elite"
MIN_WIN_RATE = 0.55           # 55%
# Minimum number of trades before we trust a wallet's win rate
MIN_TRADES_FOR_TRUST = 50

# ── Market Resolution Window ──────────────────────────────────
# Only trade markets that resolve within this many days.
# Short-resolution markets give faster feedback and don't lock
# up capital for months. Set to None to disable the filter.
MAX_DAYS_TO_RESOLVE = 16      # Skip markets resolving > 16 days away (Dune-optimized)

# ── Category Performance Tuning ────────────────
# Disable trading in weak-performing categories.
DISABLED_CATEGORIES = ['CRYPTO', 'SPORTS']
MIN_DAYS_TO_RESOLVE = 1       # Skip markets resolving < 1 day away (too late)

# ── Scheduling ────────────────────────────────────────────────
# How often the bot scans for opportunities (in seconds)
SCAN_INTERVAL_SECONDS = 600   # Every 10 minutes — markets move slowly, saves ~90% API cost

# ── Polymarket API ────────────────────────────────────────────
POLYMARKET_API_BASE = "https://gamma-api.polymarket.com"
POLYMARKET_CLOB_API = "https://clob.polymarket.com"

# ── Logging ───────────────────────────────────────────────────
LOG_FILE = "polybot.log"
TRADES_DB = "trades.db"       # SQLite database for your trade history

# ── External APIs (optional — features degrade gracefully if unset) ───────────
BRAVE_SEARCH_API_KEY = os.getenv("BRAVE_SEARCH_API_KEY", "")   # S2-1 web search
ODDS_API_KEY         = os.getenv("ODDS_API_KEY",         "")   # S2-4 sports lines
DISCORD_WEBHOOK_URL  = os.getenv("DISCORD_WEBHOOK_URL",  "")   # S3-4 notifications
DATABASE_URL         = os.getenv("DATABASE_URL",         "")   # S2-3 Heroku Postgres
DUNE_API_KEY         = os.getenv("DUNE_API_KEY",         "")   # Backtest: real prices

# ── Calibration Feedback ───────────────────────
# Track probability estimates vs actual resolutions.
TRACK_CALIBRATION = True
