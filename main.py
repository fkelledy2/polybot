# main.py
# ─────────────────────────────────────────────────────────────
# This is the entry point. Run this file to start the bot.
#
#   python main.py
#
# The bot will:
#   1. Build a list of elite wallets (on startup)
#   2. Every 60 seconds, scan active markets
#   3. Ask Claude to analyse the most promising ones
#   4. Place paper trades where there's a clear edge
#   5. Log everything, and serve a live dashboard at :8080
#
# Press Ctrl+C to stop cleanly.
# ─────────────────────────────────────────────────────────────

import logging
import sys
import threading
import time
from datetime import datetime

# Local imports — our own modules
from config import (CLAUDE_MODEL, MAX_DAYS_TO_RESOLVE, MIN_DAYS_TO_RESOLVE,
                    PAPER_TRADING, SCAN_INTERVAL_SECONDS, TOP_WALLETS_TO_TRACK)
from data.polymarket import PolymarketClient
from data.wallet_tracker import WalletTracker
from execution.paper_trader import PaperTrader
from risk.manager import RiskManager
from signals.claude_signal import batch_analyse_markets
from web.app import install_log_handler, run_server, shared_state, update_signals
from backtest.tracker import (init_tracker, log_signals, check_and_resolve_markets,
                               record_prices, get_price_velocities, prune_price_history)
from execution.resolver import resolve_open_positions
from data.enrichment import enrich_markets

# ── Logging Setup ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("polybot.log"),
    ]
)
logger = logging.getLogger("main")

# Attach web log handler so browser dashboard gets live logs
install_log_handler()


def startup_banner():
    mode = "📄 PAPER TRADING" if PAPER_TRADING else "💰 LIVE TRADING"
    print("\n" + "═" * 60)
    print("  🤖 POLYBOT — Claude-powered Prediction Market Trader")
    print(f"  Mode:      {mode}")
    print(f"  Model:     {CLAUDE_MODEL}")
    print(f"  Started:   {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Dashboard: http://localhost:8080")
    print("  Press Ctrl+C to stop")
    print("═" * 60 + "\n")


def main():
    startup_banner()

    # ── Initialise prediction tracker DB ─────────────────────
    init_tracker()

    # ── Start web dashboard in background thread ──────────────
    web_thread = threading.Thread(target=run_server, daemon=True, name="web-dashboard")
    web_thread.start()
    logger.info("Web dashboard started → http://localhost:8080")

    # ── Initialise trading components ─────────────────────────
    logger.info("Initialising components...")

    polymarket     = PolymarketClient()
    wallet_tracker = WalletTracker()
    paper_trader   = PaperTrader()
    risk_manager   = RiskManager(starting_balance=paper_trader.balance)

    # Seed shared state so dashboard shows something immediately
    shared_state.update({
        "model":           CLAUDE_MODEL,
        "balance":         paper_trader.balance,
        "portfolio_value": paper_trader.portfolio_value,
        "is_halted":       False,
    })

    # ── One-time startup: build elite wallet list ─────────────
    logger.info(f"Building elite wallet list (top {TOP_WALLETS_TO_TRACK})...")
    elite_wallets = wallet_tracker.build_elite_list(top_n=TOP_WALLETS_TO_TRACK)
    logger.info(f"Tracking {len(elite_wallets)} elite wallets")

    # ── Main loop ─────────────────────────────────────────────
    scan_count = 0

    while True:
        scan_count += 1
        logger.info(f"\n{'─' * 50}")
        logger.info(f"Scan #{scan_count} starting...")
        logger.info(risk_manager.status_report(paper_trader.portfolio_value))

        # Update dashboard state
        shared_state.update({
            "scan_count":      scan_count,
            "last_scan":       datetime.now().isoformat(),
            "is_halted":       risk_manager.is_halted,
            "balance":         paper_trader.balance,
            "portfolio_value": paper_trader.portfolio_value,
        })

        # 1. Check risk — should we even be trading right now?
        if risk_manager.is_halted:
            logger.warning("Bot is halted. Waiting for next day...")
            time.sleep(SCAN_INTERVAL_SECONDS)
            continue

        # 2. Fetch active markets (high volume only)
        markets_raw = polymarket.get_high_volume_markets(
            min_volume=10_000,
            limit=50,
            max_days=MAX_DAYS_TO_RESOLVE,
            min_days=MIN_DAYS_TO_RESOLVE,
        )
        if not markets_raw:
            logger.warning("No markets returned from API — will retry")
            time.sleep(SCAN_INTERVAL_SECONDS)
            continue

        # Parse markets into clean dicts
        markets_parsed = [polymarket.parse_market_price(m) for m in markets_raw]
        markets_parsed = [m for m in markets_parsed if m]  # Remove empty

        # Record prices and attach 24h velocity for momentum signal (S1-5)
        record_prices(markets_parsed)
        velocities = get_price_velocities([m["market_id"] for m in markets_parsed])
        for m in markets_parsed:
            m["price_velocity_24h"] = velocities.get(m["market_id"])

        # 3. Get fresh elite wallet signals
        wallet_signals = wallet_tracker.get_elite_signals()

        # 4. Enrich markets with live data (prices, news, odds)
        enrichment = enrich_markets(markets_parsed)

        # 5. Ask Claude to analyse markets and return tradeable signals
        all_signals, signals = batch_analyse_markets(
            markets=markets_parsed,
            wallet_signals=wallet_signals,
            enrichment=enrichment,
            max_markets=20,
        )

        # Push all signals to the web dashboard
        update_signals(all_signals, markets_parsed, len(elite_wallets))

        # Log all predictions for forward tracking
        log_signals(all_signals)

        # Every 5 scans, auto-close any resolved open positions
        if scan_count % 5 == 0:
            closed = resolve_open_positions(paper_trader)
            if closed:
                shared_state.update({
                    "balance":         paper_trader.balance,
                    "portfolio_value": paper_trader.portfolio_value,
                })

        # Every 10 scans, check if any predicted markets have resolved
        if scan_count % 10 == 0:
            resolved = check_and_resolve_markets()
            if resolved:
                logger.info(f"Forward tracker: scored {resolved} predictions")

        # Weekly: prune price history older than 7 days
        if scan_count % 1440 == 0:
            prune_price_history(days=7)

        # 5. For each signal, check risk and place trade
        new_trades = 0
        for signal in signals:
            ok, reason = risk_manager.can_trade(paper_trader.balance, signal)

            if ok:
                trade = paper_trader.place_trade(signal)
                if trade:
                    new_trades += 1
            else:
                logger.info(f"Trade blocked: {reason}")

        logger.info(f"Scan #{scan_count} complete. New trades this scan: {new_trades}")

        # 6. Print summary every 10 scans
        if scan_count % 10 == 0:
            paper_trader.print_summary()

        # 7. Wait before next scan
        logger.info(f"Sleeping {SCAN_INTERVAL_SECONDS}s until next scan...")
        time.sleep(SCAN_INTERVAL_SECONDS)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n🛑 Bot stopped by user (Ctrl+C)")
        print("Final summary:")
        PaperTrader().print_summary()
        sys.exit(0)
