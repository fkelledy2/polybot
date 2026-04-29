# backtest/metrics.py
# ─────────────────────────────────────────────────────────────
# Calculates performance statistics from backtest results.
# Answers the key questions:
#   - What is the optimal edge threshold?
#   - Is Claude's directional accuracy > 50% (i.e., better than random)?
#   - How does win rate change with confidence level?
#   - What is the expected value per trade at each threshold?
# ─────────────────────────────────────────────────────────────

from dataclasses import dataclass
from backtest.engine import BacktestResult


@dataclass
class ThresholdStats:
    threshold:       float
    trades:          int     # Simulated trades triggered
    wins:            int
    losses:          int
    win_rate:        float
    avg_pnl:         float   # Average PnL per triggered trade
    total_pnl:       float
    expected_value:  float   # Expected value per market (trades + skips)


def analyse_by_threshold(results: list[BacktestResult]) -> list[ThresholdStats]:
    """
    For each edge threshold, aggregate stats across all simulations
    that used that threshold.

    We use the 50¢ synthetic price as the canonical entry to get
    a threshold-only view (removes price sensitivity from the analysis).
    """
    threshold_map: dict[float, dict] = {}

    for r in results:
        for sim in r.simulations:
            if abs(sim["entry_price"] - 0.50) > 0.001:
                continue  # Only use 50¢ entries for this analysis
            t = sim["edge_threshold"]
            if t not in threshold_map:
                threshold_map[t] = {"trades": 0, "wins": 0, "losses": 0, "pnl": 0.0}

            if sim["would_trade"]:
                threshold_map[t]["trades"] += 1
                threshold_map[t]["pnl"] += sim["pnl_per_unit"]
                if sim["correct"]:
                    threshold_map[t]["wins"] += 1
                else:
                    threshold_map[t]["losses"] += 1

    stats = []
    total_markets = len(results)
    for threshold, d in sorted(threshold_map.items()):
        trades = d["trades"]
        wins   = d["wins"]
        pnl    = d["pnl"]
        stats.append(ThresholdStats(
            threshold=threshold,
            trades=trades,
            wins=wins,
            losses=d["losses"],
            win_rate=round(wins / trades, 4) if trades > 0 else 0.0,
            avg_pnl=round(pnl / trades, 4)   if trades > 0 else 0.0,
            total_pnl=round(pnl, 4),
            expected_value=round(pnl / total_markets, 4) if total_markets > 0 else 0.0,
        ))

    return stats


def analyse_by_confidence(results: list[BacktestResult]) -> dict:
    """Directional accuracy broken down by Claude confidence level."""
    groups: dict[str, dict] = {}

    for r in results:
        c = r.confidence
        if c not in groups:
            groups[c] = {"total": 0, "correct": 0}
        groups[c]["total"] += 1
        # Correct = Claude's probability direction matches outcome
        claude_says_yes = r.claude_probability >= 0.5
        if (claude_says_yes and r.resolved_yes) or (not claude_says_yes and not r.resolved_yes):
            groups[c]["correct"] += 1

    return {
        level: {
            "total":    d["total"],
            "correct":  d["correct"],
            "accuracy": round(d["correct"] / d["total"], 4) if d["total"] > 0 else 0.0,
        }
        for level, d in groups.items()
    }


def analyse_calibration(results: list[BacktestResult]) -> list[dict]:
    """
    Calibration curve: bucket Claude's probability estimates and measure
    actual hit rate within each bucket. A well-calibrated model should
    show hit rate ≈ predicted probability.
    """
    buckets = {
        "0-10%":   {"pred_sum": 0, "actual": 0, "n": 0},
        "10-20%":  {"pred_sum": 0, "actual": 0, "n": 0},
        "20-30%":  {"pred_sum": 0, "actual": 0, "n": 0},
        "30-40%":  {"pred_sum": 0, "actual": 0, "n": 0},
        "40-50%":  {"pred_sum": 0, "actual": 0, "n": 0},
        "50-60%":  {"pred_sum": 0, "actual": 0, "n": 0},
        "60-70%":  {"pred_sum": 0, "actual": 0, "n": 0},
        "70-80%":  {"pred_sum": 0, "actual": 0, "n": 0},
        "80-90%":  {"pred_sum": 0, "actual": 0, "n": 0},
        "90-100%": {"pred_sum": 0, "actual": 0, "n": 0},
    }

    bucket_ranges = [
        ("0-10%",   0.0, 0.10),
        ("10-20%",  0.10, 0.20),
        ("20-30%",  0.20, 0.30),
        ("30-40%",  0.30, 0.40),
        ("40-50%",  0.40, 0.50),
        ("50-60%",  0.50, 0.60),
        ("60-70%",  0.60, 0.70),
        ("70-80%",  0.70, 0.80),
        ("80-90%",  0.80, 0.90),
        ("90-100%", 0.90, 1.01),
    ]

    for r in results:
        p = r.claude_probability
        for label, lo, hi in bucket_ranges:
            if lo <= p < hi:
                buckets[label]["pred_sum"] += p
                buckets[label]["n"]        += 1
                if r.resolved_yes:
                    buckets[label]["actual"] += 1
                break

    calibration = []
    for label, lo, hi in bucket_ranges:
        d = buckets[label]
        n = d["n"]
        calibration.append({
            "bucket":         label,
            "n":              n,
            "avg_predicted":  round(d["pred_sum"] / n, 3) if n > 0 else (lo + hi) / 2,
            "actual_rate":    round(d["actual"] / n, 3)   if n > 0 else None,
        })

    return calibration


def optimal_threshold(threshold_stats: list[ThresholdStats]) -> float:
    """
    Return the edge threshold that maximises expected value per market.
    Requires at least 5 trades to be considered.
    """
    candidates = [s for s in threshold_stats if s.trades >= 5 and s.expected_value > 0]
    if not candidates:
        return 0.12  # Default
    return max(candidates, key=lambda s: s.expected_value).threshold


def print_report(
    results: list[BacktestResult],
    threshold_stats: list[ThresholdStats],
    confidence_stats: dict,
    calibration: list[dict],
) -> None:
    """Print a formatted backtest report to stdout."""
    n = len(results)
    total_yes = sum(1 for r in results if r.resolved_yes)
    overall_dir = sum(
        1 for r in results
        if (r.claude_probability >= 0.5) == r.resolved_yes
    )

    print("\n" + "═" * 65)
    print("  POLYBOT BACKTEST REPORT")
    print("═" * 65)
    print(f"  Markets analysed : {n}")
    print(f"  Resolved YES     : {total_yes} ({total_yes/n:.1%})")
    print(f"  Claude direction : {overall_dir}/{n} correct ({overall_dir/n:.1%})")
    print()

    print("  ── THRESHOLD OPTIMISATION (entry @ 50¢) ──────────────")
    print(f"  {'Threshold':>10}  {'Trades':>7}  {'Win%':>6}  {'Avg PnL':>8}  {'EV/mkt':>8}")
    print("  " + "─" * 55)
    for s in threshold_stats:
        print(
            f"  {s.threshold:>9.0%}  {s.trades:>7}  {s.win_rate:>6.1%}  "
            f"{s.avg_pnl:>+8.3f}  {s.expected_value:>+8.3f}"
        )
    best = optimal_threshold(threshold_stats)
    print(f"\n  ★  Recommended threshold: {best:.0%}")
    print()

    print("  ── ACCURACY BY CONFIDENCE ────────────────────────────")
    for level, d in confidence_stats.items():
        print(f"  {level:>8}:  {d['correct']}/{d['total']} correct  ({d['accuracy']:.1%})")
    print()

    print("  ── CALIBRATION CURVE ─────────────────────────────────")
    print(f"  {'Bucket':>10}  {'N':>4}  {'Predicted':>10}  {'Actual':>8}  {'Diff':>7}")
    print("  " + "─" * 50)
    for row in calibration:
        if row["n"] == 0 or row["actual_rate"] is None:
            continue
        diff = row["actual_rate"] - row["avg_predicted"]
        print(
            f"  {row['bucket']:>10}  {row['n']:>4}  "
            f"{row['avg_predicted']:>10.1%}  {row['actual_rate']:>8.1%}  {diff:>+7.1%}"
        )

    print("\n" + "═" * 65 + "\n")
