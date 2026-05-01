#!/usr/bin/env python
# backtest/variants.py
# ─────────────────────────────────────────────────────────────
# Test multiple parameter configurations to find optimal settings.
#
# Variant space:
#   - MIN_EDGE_TO_TRADE: 0.05, 0.10, 0.15, 0.20
#   - MIN_ENTRY_PROBABILITY: 0.02, 0.03, 0.05, 0.10
#   - MAX_DAYS_TO_RESOLVE: 7, 14, 30
# ─────────────────────────────────────────────────────────────

import json
import logging
from dataclasses import dataclass, asdict
from typing import Optional
from backtest.fetcher import ResolvedMarket
from backtest.engine import run_claude_on_batch, SYNTHETIC_PRICES

logger = logging.getLogger(__name__)


@dataclass
class VariantResult:
    """Result of testing one configuration variant."""
    edge_threshold: float
    entry_prob_min: float
    max_days: int

    total_trades: int
    win_rate: float
    avg_pnl: float
    total_pnl: float
    directional_accuracy: float

    def expected_value(self) -> float:
        """Rough EV: assume 50/50 coin flip on average."""
        if self.total_trades == 0:
            return 0.0
        return self.avg_pnl


def simulate_with_config(
    markets: list[ResolvedMarket],
    edge_threshold: float,
    entry_prob_min: float,
    max_days: int,
    batch_size: int = 20,
) -> VariantResult:
    """
    Simulate trades using a specific configuration.
    Returns aggregated performance metrics.
    """
    results = []
    total_markets = len(markets)

    for batch_start in range(0, total_markets, batch_size):
        batch = markets[batch_start: batch_start + batch_size]
        batch_num = batch_start // batch_size + 1
        total_batches = (total_markets + batch_size - 1) // batch_size
        logger.debug(f"  Batch {batch_num}/{total_batches} ({len(batch)} markets)…")

        claude_outputs = run_claude_on_batch(batch)

        for market, claude_out in zip(batch, claude_outputs):
            if claude_out is None:
                continue

            # Skip markets that don't match time horizon filter
            if hasattr(market, "days_to_resolve") and market.days_to_resolve is not None:
                if market.days_to_resolve > max_days:
                    continue

            try:
                claude_prob = float(claude_out["yes_probability"])
                confidence = claude_out.get("confidence", "medium")
            except (KeyError, ValueError, TypeError):
                continue

            # Test at one representative entry price (50% - neutral)
            entry_price = 0.50
            edge = claude_prob - entry_price

            if edge >= 0:
                direction = "YES"
                abs_edge = edge
                entry_probability = entry_price
            else:
                direction = "NO"
                abs_edge = abs(edge)
                entry_probability = 1.0 - entry_price

            # Would we trade with this configuration?
            would_trade = (
                abs_edge >= edge_threshold
                and confidence != "low"
                and entry_probability >= entry_prob_min
            )

            if would_trade:
                correct = (direction == "YES" and market.resolved_yes) or (
                    direction == "NO" and not market.resolved_yes
                )

                if direction == "YES":
                    pnl = (1.0 - entry_price) if market.resolved_yes else -entry_price
                else:
                    no_price = 1.0 - entry_price
                    pnl = (1.0 - no_price) if not market.resolved_yes else -no_price

                results.append({
                    "market_id": market.market_id,
                    "correct": correct,
                    "pnl": pnl,
                })

    # Aggregate results
    total_trades = len(results)
    directional_accuracy = (
        sum(1 for r in results if r["correct"]) / total_trades
        if total_trades > 0
        else 0.0
    )
    total_pnl = sum(r["pnl"] for r in results)
    avg_pnl = total_pnl / total_trades if total_trades > 0 else 0.0

    return VariantResult(
        edge_threshold=edge_threshold,
        entry_prob_min=entry_prob_min,
        max_days=max_days,
        total_trades=total_trades,
        win_rate=directional_accuracy,
        avg_pnl=round(avg_pnl, 4),
        total_pnl=round(total_pnl, 4),
        directional_accuracy=round(directional_accuracy, 4),
    )


def test_variants(
    markets: list[ResolvedMarket],
    edge_thresholds: list[float] = None,
    entry_prob_thresholds: list[float] = None,
    max_days_variants: list[int] = None,
) -> list[VariantResult]:
    """
    Test multiple parameter combinations and return results.
    """
    if edge_thresholds is None:
        edge_thresholds = [0.05, 0.10, 0.15, 0.20]
    if entry_prob_thresholds is None:
        entry_prob_thresholds = [0.02, 0.03, 0.05, 0.10]
    if max_days_variants is None:
        max_days_variants = [7, 14, 30]

    all_results = []
    total_variants = (
        len(edge_thresholds) * len(entry_prob_thresholds) * len(max_days_variants)
    )

    variant_num = 0
    for edge_thresh in edge_thresholds:
        for entry_prob in entry_prob_thresholds:
            for max_days in max_days_variants:
                variant_num += 1
                logger.info(
                    f"Testing variant {variant_num}/{total_variants}: "
                    f"edge={edge_thresh:.0%}, entry_prob={entry_prob:.0%}, "
                    f"max_days={max_days}"
                )

                result = simulate_with_config(
                    markets,
                    edge_threshold=edge_thresh,
                    entry_prob_min=entry_prob,
                    max_days=max_days,
                )
                all_results.append(result)

    return all_results


def print_variant_report(results: list[VariantResult]) -> None:
    """Print a summary comparison of all variants."""
    if not results:
        logger.error("No results to report")
        return

    # Sort by average PnL descending
    sorted_results = sorted(results, key=lambda r: r.avg_pnl, reverse=True)

    print("\n" + "═" * 100)
    print("  VARIANT TESTING RESULTS (sorted by avg PnL)")
    print("═" * 100)
    print(
        f"  {'Edge':>6}  {'Min Entry%':>10}  {'Max Days':>9}  "
        f"{'Trades':>7}  {'Win%':>7}  {'Avg PnL':>9}  {'Total PnL':>10}"
    )
    print("  " + "─" * 96)

    for r in sorted_results[:20]:  # Show top 20
        print(
            f"  {r.edge_threshold:>6.0%}  {r.entry_prob_min:>10.0%}  "
            f"{r.max_days:>9}  {r.total_trades:>7}  {r.win_rate:>7.1%}  "
            f"{r.avg_pnl:>+9.4f}  {r.total_pnl:>+10.4f}"
        )

    print("═" * 100)

    # Identify best configuration
    best = sorted_results[0]
    print(
        f"\n  🏆 BEST VARIANT:\n"
        f"     MIN_EDGE_TO_TRADE = {best.edge_threshold:.2f}\n"
        f"     MIN_ENTRY_PROBABILITY = {best.entry_prob_min:.2f}\n"
        f"     MAX_DAYS_TO_RESOLVE = {best.max_days}\n"
        f"\n"
        f"     Performance: {best.total_trades} trades, "
        f"{best.win_rate:.1%} win rate, {best.avg_pnl:+.4f} avg PnL"
    )
    print()
