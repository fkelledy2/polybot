#!/usr/bin/env python
# backtest/run.py
# ─────────────────────────────────────────────────────────────
# CLI entry point for running a historical backtest.
#
#   python backtest/run.py                  # run with defaults
#   python backtest/run.py --markets 100    # analyse 100 markets
#   python backtest/run.py --report-only    # show tracker stats only
# ─────────────────────────────────────────────────────────────

import argparse
import json
import logging
import sqlite3
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("backtest")


def save_results_to_db(results, threshold_stats, confidence_stats, calibration):
    """Persist backtest results to trades.db for the web UI."""
    from config import TRADES_DB

    conn = sqlite3.connect(TRADES_DB)
    c = conn.cursor()

    # Create backtest tables if needed
    c.executescript("""
        CREATE TABLE IF NOT EXISTS backtest_runs (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            run_at      TEXT,
            markets_n   INTEGER,
            directional_accuracy REAL,
            best_threshold REAL,
            best_ev    REAL,
            summary_json TEXT
        );
        CREATE TABLE IF NOT EXISTS backtest_markets (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id       INTEGER,
            market_id    TEXT,
            question     TEXT,
            resolved_yes INTEGER,
            claude_prob  REAL,
            confidence   TEXT,
            reasoning    TEXT
        );
    """)

    from datetime import datetime
    from backtest.metrics import optimal_threshold

    best_t = optimal_threshold(threshold_stats)
    best_ev = next((s.expected_value for s in threshold_stats if s.threshold == best_t), 0)

    total = len(results)
    correct_dir = sum(
        1 for r in results
        if (r.claude_probability >= 0.5) == r.resolved_yes
    )
    dir_accuracy = correct_dir / total if total > 0 else 0

    summary = {
        "threshold_stats": [
            {
                "threshold": s.threshold,
                "trades": s.trades,
                "win_rate": s.win_rate,
                "avg_pnl": s.avg_pnl,
                "expected_value": s.expected_value,
            }
            for s in threshold_stats
        ],
        "confidence_stats": confidence_stats,
        "calibration": calibration,
    }

    c.execute("""
        INSERT INTO backtest_runs
        (run_at, markets_n, directional_accuracy, best_threshold, best_ev, summary_json)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        datetime.now().isoformat(),
        total,
        round(dir_accuracy, 4),
        best_t,
        round(best_ev, 4),
        json.dumps(summary),
    ))
    run_id = c.lastrowid

    for r in results:
        c.execute("""
            INSERT INTO backtest_markets
            (run_id, market_id, question, resolved_yes, claude_prob, confidence, reasoning)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (run_id, r.market_id, r.question, int(r.resolved_yes),
              r.claude_probability, r.confidence, r.reasoning))

    conn.commit()
    conn.close()
    logger.info(f"Results saved to DB (run_id={run_id})")
    return run_id


def run_historical_backtest(n_markets: int = 100) -> None:
    from backtest.fetcher import fetch_resolved_markets
    from backtest.engine import backtest_markets
    from backtest.metrics import (
        analyse_by_threshold, analyse_by_confidence,
        analyse_calibration, print_report, optimal_threshold,
    )
    from config import MIN_EDGE_TO_TRADE

    logger.info(f"Starting historical backtest on {n_markets} resolved markets")

    markets = fetch_resolved_markets(limit=n_markets * 2)
    if not markets:
        logger.error("No resolved markets returned. Check API connectivity.")
        return

    markets = markets[:n_markets]
    logger.info(f"Using {len(markets)} markets for backtest")

    results = backtest_markets(markets)
    if not results:
        logger.error("No backtest results — Claude API may have failed.")
        return

    threshold_stats   = analyse_by_threshold(results)
    confidence_stats  = analyse_by_confidence(results)
    calibration       = analyse_calibration(results)

    print_report(results, threshold_stats, confidence_stats, calibration)

    best_threshold = optimal_threshold(threshold_stats)
    current = MIN_EDGE_TO_TRADE

    if abs(best_threshold - current) >= 0.02:
        print(f"  💡 SUGGESTION: Current MIN_EDGE_TO_TRADE = {current:.0%}")
        print(f"     Backtest suggests optimal threshold = {best_threshold:.0%}")
        print(f"     Update config.py to MIN_EDGE_TO_TRADE = {best_threshold:.2f}")
    else:
        print(f"  ✓ Current threshold ({current:.0%}) is near-optimal per backtest.")
    print()

    save_results_to_db(results, threshold_stats, confidence_stats, calibration)


def show_tracker_report() -> None:
    from backtest.tracker import get_tracker_stats, get_recent_predictions

    stats = get_tracker_stats()
    preds = get_recent_predictions(20)

    print("\n" + "═" * 55)
    print("  FORWARD TRACKER REPORT")
    print("═" * 55)
    print(f"  Total predictions logged : {stats['total_predictions']}")
    print(f"  Markets resolved so far  : {stats['resolved']}")

    if stats["directional_accuracy"] is not None:
        print(f"  Directional accuracy     : {stats['directional_accuracy']:.1%}")
    else:
        print("  Directional accuracy     : — (no resolved markets yet)")

    if stats["traded_accuracy"] is not None:
        print(f"  Traded signal accuracy   : {stats['traded_accuracy']:.1%}")
        print(f"  Simulated PnL (per unit) : {stats['simulated_pnl']:+.4f}")
        print(f"  Traded + resolved        : {stats['traded_resolved']}")

    if preds:
        print(f"\n  Recent predictions ({len(preds)}):")
        print(f"  {'Question':50s}  {'Claude':>7}  {'Mkt':>5}  {'Edge':>6}  {'Result':>8}")
        print("  " + "─" * 85)
        for p in preds[:15]:
            q   = (p["question"] or "")[:48]
            cp  = f"{p['claude_yes_prob']:.0%}" if p["claude_yes_prob"] else "—"
            mp  = f"{p['market_yes_price']:.0%}" if p["market_yes_price"] else "—"
            edg = f"{p['edge']:+.0%}" if p["edge"] else "—"
            if p["resolved_yes"] is None:
                res = "PENDING"
            elif p["outcome_correct"]:
                res = "✓ CORRECT"
            else:
                res = "✗ WRONG"
            print(f"  {q:50s}  {cp:>7}  {mp:>5}  {edg:>6}  {res:>8}")

    print("═" * 55 + "\n")


def main():
    parser = argparse.ArgumentParser(description="Polybot Backtesting Tool")
    parser.add_argument("--markets",     type=int, default=80,
                        help="Number of resolved markets to analyse (default: 80)")
    parser.add_argument("--report-only", action="store_true",
                        help="Show forward-tracker report only, skip historical backtest")
    args = parser.parse_args()

    from backtest.tracker import init_tracker
    init_tracker()

    if args.report_only:
        show_tracker_report()
    else:
        run_historical_backtest(n_markets=args.markets)
        show_tracker_report()


if __name__ == "__main__":
    main()
