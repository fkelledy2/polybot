#!/usr/bin/env python
# backtest/grid_search.py
# ─────────────────────────────────────────────────────────────
# Grid search over parameter space to find optimal configuration.
#
#   python backtest/grid_search.py --markets 200 --output results.json
# ─────────────────────────────────────────────────────────────

import argparse
import json
import logging
import sys
import os
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("grid_search")


def main():
    parser = argparse.ArgumentParser(description="Parameter Grid Search")
    parser.add_argument(
        "--markets",
        type=int,
        default=200,
        help="Number of resolved markets to test (default: 200)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="grid_search_results.json",
        help="Output file for results (default: grid_search_results.json)",
    )
    parser.add_argument(
        "--edge",
        type=str,
        default="0.05,0.10,0.15,0.20",
        help="Edge thresholds to test (comma-separated)",
    )
    parser.add_argument(
        "--entry-prob",
        type=str,
        default="0.02,0.03,0.05,0.10",
        help="Entry probability minimums to test (comma-separated)",
    )
    parser.add_argument(
        "--max-days",
        type=str,
        default="7,14,30",
        help="Max days-to-resolve to test (comma-separated)",
    )
    args = parser.parse_args()

    from backtest.fetcher import fetch_resolved_markets
    from backtest.variants import test_variants, print_variant_report

    logger.info("Starting parameter grid search...")
    logger.info(f"Fetching {args.markets} resolved markets...")

    markets = fetch_resolved_markets(limit=max(args.markets * 4, 800))
    if not markets:
        logger.error("No resolved markets returned. Check API connectivity.")
        return

    markets = markets[: args.markets]
    logger.info(f"Using {len(markets)} markets for grid search")

    # Parse variant parameters
    edge_thresholds = [float(x.strip()) for x in args.edge.split(",")]
    entry_prob_thresholds = [float(x.strip()) for x in args.entry_prob.split(",")]
    max_days_variants = [int(x.strip()) for x in args.max_days.split(",")]

    logger.info(f"Testing {len(edge_thresholds)} edge thresholds: {edge_thresholds}")
    logger.info(f"Testing {len(entry_prob_thresholds)} entry prob levels: {entry_prob_thresholds}")
    logger.info(f"Testing {len(max_days_variants)} max-days variants: {max_days_variants}")

    # Run grid search
    results = test_variants(
        markets,
        edge_thresholds=edge_thresholds,
        entry_prob_thresholds=entry_prob_thresholds,
        max_days_variants=max_days_variants,
    )

    # Print summary
    print_variant_report(results)

    # Save to JSON
    results_dict = [
        {
            "edge_threshold": r.edge_threshold,
            "entry_prob_min": r.entry_prob_min,
            "max_days": r.max_days,
            "total_trades": r.total_trades,
            "win_rate": r.win_rate,
            "avg_pnl": r.avg_pnl,
            "total_pnl": r.total_pnl,
            "directional_accuracy": r.directional_accuracy,
        }
        for r in results
    ]

    output = {
        "timestamp": datetime.now().isoformat(),
        "markets_tested": len(markets),
        "edge_thresholds": edge_thresholds,
        "entry_prob_thresholds": entry_prob_thresholds,
        "max_days_variants": max_days_variants,
        "results": results_dict,
    }

    with open(args.output, "w") as f:
        json.dump(output, f, indent=2)

    logger.info(f"Results saved to {args.output}")


if __name__ == "__main__":
    main()
