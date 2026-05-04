# main.py
# ─────────────────────────────────────────────────────────────
# Entry point. Runs the full scan loop with all Sprint 1-4 features.
# ─────────────────────────────────────────────────────────────

import logging
import sys
import threading
import time
from datetime import datetime

from config import (CLAUDE_MODEL, MAX_DAYS_TO_RESOLVE, MIN_DAYS_TO_RESOLVE,
                    PAPER_TRADING, SCAN_INTERVAL_SECONDS, TOP_WALLETS_TO_TRACK,
                    ENABLE_WALLET_TRACKING)
from data.polymarket import PolymarketClient
from data.wallet_tracker import WalletTracker
from execution.paper_trader import PaperTrader
from execution.resolver import resolve_open_positions, check_stop_losses
from risk.manager import RiskManager
from signals.claude_signal import (batch_analyse_markets, confirm_high_edge_signals,
                                    batch_reanalyse_open_positions, poll_batch_results)
from signals.clustering import cluster_markets
from signals.arbitrage import find_arbitrage_pairs
from web.app import install_log_handler, run_server, shared_state, update_signals
from backtest.tracker import (init_tracker, log_signals, check_and_resolve_markets,
                               record_prices, get_price_velocities, prune_price_history)
from data.enrichment import enrich_markets
from data.clob_stream import start as start_clob, update_subscriptions, get_cached_price
from notifications import send as notify

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("polybot.log"),
    ]
)
logger = logging.getLogger("main")
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
    init_tracker()

    web_thread = threading.Thread(target=run_server, daemon=True, name="web-dashboard")
    web_thread.start()
    logger.info("Web dashboard started → http://localhost:8080")

    polymarket     = PolymarketClient()
    wallet_tracker = WalletTracker() if ENABLE_WALLET_TRACKING else None
    paper_trader   = PaperTrader()
    risk_manager   = RiskManager(starting_balance=paper_trader.portfolio_value)

    shared_state.update({
        "model":           CLAUDE_MODEL,
        "balance":         paper_trader.balance,
        "portfolio_value": paper_trader.portfolio_value,
        "is_halted":       False,
    })

    elite_wallets = []
    if ENABLE_WALLET_TRACKING:
        logger.info(f"Building elite wallet list (top {TOP_WALLETS_TO_TRACK})...")
        elite_wallets = wallet_tracker.build_elite_list(top_n=TOP_WALLETS_TO_TRACK)
        logger.info(f"Tracking {len(elite_wallets)} elite wallets")
    else:
        logger.info("Wallet tracking disabled (Polymarket leaderboard API unavailable)")

    scan_count  = 0
    batch_id_pending: str | None = None
    # market_id → scan number when cooldown expires (prevent re-entry after stop-loss)
    _stop_loss_cooldown: dict[str, int] = {}
    STOP_LOSS_COOLDOWN_SCANS = 10  # ~10 min at 60s/scan

    while True:
        scan_count += 1
        logger.info(f"\n{'─' * 50}")
        logger.info(f"Scan #{scan_count} starting...")
        logger.info(risk_manager.status_report(paper_trader.portfolio_value))

        shared_state.update({
            "scan_count":      scan_count,
            "last_scan":       datetime.now().isoformat(),
            "is_halted":       risk_manager.is_halted,
            "balance":         paper_trader.balance,
            "portfolio_value": paper_trader.portfolio_value,
        })

        if risk_manager.is_halted:
            logger.warning("Bot is halted. Waiting for next day...")
            time.sleep(SCAN_INTERVAL_SECONDS)
            continue

        # ── 1. Fetch markets ──────────────────────────────────
        markets_raw = polymarket.get_high_volume_markets(
            min_volume=5_000,
            limit=100,
            max_days=MAX_DAYS_TO_RESOLVE,
            min_days=MIN_DAYS_TO_RESOLVE,
        )
        if not markets_raw:
            logger.warning("No markets returned — retrying")
            time.sleep(SCAN_INTERVAL_SECONDS)
            continue

        markets_parsed = [polymarket.parse_market_price(m) for m in markets_raw]
        markets_parsed = [m for m in markets_parsed if m]

        # S3-2: append new markets every 5 scans
        if scan_count % 5 == 0:
            new_raw = polymarket.get_new_markets(min_volume=5000, max_age_hours=48)
            new_ids = {m["market_id"] for m in markets_parsed}
            for nm in new_raw:
                parsed = polymarket.parse_market_price(nm)
                if parsed and parsed["market_id"] not in new_ids:
                    parsed["is_new_market"] = True
                    markets_parsed.append(parsed)
                    new_ids.add(parsed["market_id"])

        # S1-5: price momentum
        record_prices(markets_parsed)
        velocities = get_price_velocities([m["market_id"] for m in markets_parsed])
        for m in markets_parsed:
            m["price_velocity_24h"] = velocities.get(m["market_id"])

        # S4-1: freshen prices from CLOB WebSocket cache
        update_subscriptions(markets_parsed)
        for m in markets_parsed:
            cached = get_cached_price(m["market_id"])
            if cached is not None:
                m["yes"] = cached
                m["no"]  = 1.0 - cached

        # Exclude markets on stop-loss cooldown from analysis
        expired = [mid for mid, until in _stop_loss_cooldown.items() if scan_count >= until]
        for mid in expired:
            del _stop_loss_cooldown[mid]
        if _stop_loss_cooldown:
            markets_parsed = [m for m in markets_parsed if m["market_id"] not in _stop_loss_cooldown]
            logger.info(f"Cooldown active for {len(_stop_loss_cooldown)} market(s): {list(_stop_loss_cooldown)}")

        # S4-2: cluster markets for correlation-aware risk
        clusters = cluster_markets(markets_parsed)
        risk_manager.update_clusters(clusters)

        # S3-3: arbitrage pair detection — add to enrichment context
        arb_pairs = find_arbitrage_pairs(markets_parsed)
        arb_notes: dict[str, str] = {}
        for pair in arb_pairs:
            for mkt, other in [(pair.market_a, pair.market_b),
                               (pair.market_b, pair.market_a)]:
                note = (f"ARBIT: pair with [{other['question'][:30]}], "
                        f"sum={pair.implied_sum:.2f} ({pair.direction})")
                arb_notes[mkt["market_id"]] = note

        # ── 2. Wallet signals ─────────────────────────────────
        wallet_signals = wallet_tracker.get_elite_signals() if ENABLE_WALLET_TRACKING else []

        # ── 3. Enrich markets ─────────────────────────────────
        enrichment = enrich_markets(markets_parsed)
        # Merge arbitrage notes into enrichment
        for mid, note in arb_notes.items():
            if enrichment.get(mid):
                enrichment[mid] = enrichment[mid] + " | " + note
            else:
                enrichment[mid] = note

        # ── 4. Claude analysis ────────────────────────────────
        all_signals, signals = batch_analyse_markets(
            markets=markets_parsed,
            wallet_signals=wallet_signals,
            enrichment=enrichment,
            max_markets=20,
            scan_count=scan_count,
        )

        # S2-2: confirm high-edge signals with extended thinking
        all_signals = confirm_high_edge_signals(
            all_signals, markets_parsed, enrichment=enrichment
        )
        # Rebuild tradeable signals list after confirmation may have updated edges
        signals = [s for s in all_signals if s.should_trade]

        # ── 5. Stop-losses and position resolution ────────────
        # S4-3: dynamic stop-loss (before new trades)
        stopped_ids = check_stop_losses(paper_trader, markets_parsed)
        if stopped_ids:
            notify(f"🛑 Stop-loss triggered: closed {len(stopped_ids)} position(s)")
            for mid in stopped_ids:
                _stop_loss_cooldown[mid] = scan_count + STOP_LOSS_COOLDOWN_SCANS
            shared_state.update({
                "balance":         paper_trader.balance,
                "portfolio_value": paper_trader.portfolio_value,
            })

        if scan_count % 5 == 0:
            closed = resolve_open_positions(paper_trader)
            if closed:
                shared_state.update({
                    "balance":         paper_trader.balance,
                    "portfolio_value": paper_trader.portfolio_value,
                })

        # ── 6. Place trades ───────────────────────────────────
        new_trades = 0
        for signal in signals:
            # S3-4: notify high-edge signal
            if abs(signal.edge) > 0.20:
                notify(
                    f"🎯 High-edge signal: {signal.direction} "
                    f"{signal.question[:50]} | edge={signal.edge:+.0%} | "
                    f"confidence={signal.confidence}"
                )

            ok, reason = risk_manager.can_trade(
                paper_trader.portfolio_value,
                signal,
                open_positions=paper_trader.open_positions,
                portfolio_value=paper_trader.portfolio_value,
            )
            if ok:
                trade = paper_trader.place_trade(signal)
                if trade:
                    new_trades += 1
                    notify(
                        f"📈 Trade: {signal.direction} {signal.question[:50]} | "
                        f"size=${trade.size_usd:.0f} | edge={signal.edge:+.0%}"
                    )
            else:
                logger.info(f"Trade blocked: {reason}")

        # ── 7. Dashboard + logging ────────────────────────────
        update_signals(all_signals, markets_parsed, len(elite_wallets))
        log_signals(all_signals)

        logger.info(f"Scan #{scan_count} complete. New trades: {new_trades}")

        # ── 8. Periodic tasks ─────────────────────────────────
        if scan_count % 10 == 0:
            resolved = check_and_resolve_markets()
            if resolved:
                logger.info(f"Forward tracker: scored {resolved} predictions")

            paper_trader.print_summary()

            # S4-4: create batch re-analysis of open positions
            if paper_trader.open_positions and not batch_id_pending:
                batch_id_pending = batch_reanalyse_open_positions(
                    paper_trader.open_positions, markets_parsed
                )

        # S4-4: poll for batch results every scan
        if batch_id_pending:
            results = poll_batch_results(batch_id_pending)
            if results is not None:
                logger.info(f"Batch reanalysis results received: {len(results)} markets")
                batch_id_pending = None

        # S3-4: daily P&L summary every 24 scans
        if scan_count % 24 == 0:
            pv = paper_trader.portfolio_value
            start_pv = risk_manager.day_start_balance
            pnl_pct = (pv - start_pv) / start_pv if start_pv > 0 else 0
            notify(
                f"📊 Daily summary: portfolio=${pv:.0f} | "
                f"day P&L={pnl_pct:+.1%} | open={len(paper_trader.open_positions)}"
            )

        # S3-4: notify halt (only once per halt event, not every scan)
        if risk_manager.is_halted and not getattr(risk_manager, "_halt_notified", False):
            notify("🛑 Bot halted: daily loss limit hit")
            risk_manager._halt_notified = True
        elif not risk_manager.is_halted:
            risk_manager._halt_notified = False

        # Weekly: prune old price history
        if scan_count % 1440 == 0:
            prune_price_history(days=7)

        logger.info(f"Sleeping {SCAN_INTERVAL_SECONDS}s until next scan...")
        time.sleep(SCAN_INTERVAL_SECONDS)


if __name__ == "__main__":
    # S4-1: start CLOB WebSocket feed (no markets yet — will subscribe on first scan)
    start_clob([])

    try:
        main()
    except KeyboardInterrupt:
        print("\n\n🛑 Bot stopped by user (Ctrl+C)")
        print("Final summary:")
        PaperTrader().print_summary()
        sys.exit(0)
