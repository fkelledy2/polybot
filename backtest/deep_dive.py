#!/usr/bin/env python
# backtest/deep_dive.py
# ─────────────────────────────────────────────────────────────
# Deep dive: extended grid search with finer resolution.
# Good for narrowing down optimal parameter range once you have rough bounds.
#
#   python backtest/deep_dive.py --markets 300 --focus-edge 0.08-0.14 --focus-entry 0.02-0.05
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
logger = logging.getLogger("deep_dive")


def parse_range(range_str: str) -> list[float]:
    """Parse 'min-max-step' or return default range."""
    if not range_str or "-" not in range_str:
        return None

    parts = range_str.split("-")
    if len(parts) == 2:
        min_val, max_val = float(parts[0]), float(parts[1])
        step = (max_val - min_val) / 4  # 5 points across range
        return [round(min_val + step * i, 3) for i in range(5)]
    return None


def main():
    parser = argparse.ArgumentParser(description="Deep Dive Parameter Search")
    parser.add_argument(
        "--markets",
        type=int,
        default=300,
        help="Number of resolved markets (default: 300, longer search)",
    )
    parser.add_argument(
        "--focus-edge",
        type=str,
        default=None,
        help="Focus edge range (e.g., '0.08-0.14' for 5 points across)",
    )
    parser.add_argument(
        "--focus-entry",
        type=str,
        default=None,
        help="Focus entry prob range (e.g., '0.02-0.05' for 5 points across)",
    )
    parser.add_argument(
        "--focus-days",
        type=str,
        default=None,
        help="Focus days range (e.g., '7-21' for 5 points across)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="deep_dive_results.json",
        help="Output file for results",
    )
    args = parser.parse_args()

    from backtest.fetcher import fetch_resolved_markets
    from backtest.variants import test_variants, print_variant_report

    logger.info("Starting DEEP DIVE parameter search...")
    logger.info(f"Fetching {args.markets} resolved markets (extended search)...")

    markets = fetch_resolved_markets(limit=max(args.markets * 4, 1000))
    if not markets:
        logger.error("No resolved markets returned.")
        return

    markets = markets[: args.markets]
    logger.info(f"Using {len(markets)} markets (deeper search = more API calls)")

    # Parse focused ranges or use defaults
    edge_thresholds = parse_range(args.focus_edge) or [
        0.05, 0.075, 0.10, 0.125, 0.15, 0.175, 0.20
    ]
    entry_prob_thresholds = parse_range(args.focus_entry) or [
        0.02, 0.03, 0.04, 0.05, 0.075, 0.10
    ]
    max_days_variants = parse_range(args.focus_days) or [7, 10, 14, 21, 30]

    logger.info(f"Testing {len(edge_thresholds)} edge thresholds: {edge_thresholds}")
    logger.info(
        f"Testing {len(entry_prob_thresholds)} entry prob levels: {entry_prob_thresholds}"
    )
    logger.info(f"Testing {len(max_days_variants)} max-days variants: {max_days_variants}")

    total = (
        len(edge_thresholds) * len(entry_prob_thresholds) * len(max_days_variants)
    )
    logger.info(f"Total variants to test: {total}")

    # Run grid search
    results = test_variants(
        markets,
        edge_thresholds=edge_thresholds,
        entry_prob_thresholds=entry_prob_thresholds,
        max_days_variants=max_days_variants,
    )

    # Print summary
    print_variant_report(results)

    # Save detailed results
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
        "depth": "DEEP_DIVE",
        "markets_tested": len(markets),
        "total_variants": len(results),
        "edge_thresholds": edge_thresholds,
        "entry_prob_thresholds": entry_prob_thresholds,
        "max_days_variants": max_days_variants,
        "results": results_dict,
    }

    with open(args.output, "w") as f:
        json.dump(output, f, indent=2)

    logger.info(f"Deep dive results saved to {args.output}")


if __name__ == "__main__":
    main()
