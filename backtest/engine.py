# backtest/engine.py
# ─────────────────────────────────────────────────────────────
# Runs Claude analysis on historical markets and simulates trades.
#
# Strategy tested:
#   1. Claude estimates true YES probability
#   2. Edge = |claude_prob - market_price|
#   3. Trade if edge >= threshold AND confidence != "low"
#   4. Win if direction matches actual outcome
#   5. PnL per share = (1 - entry_price) if won, -(entry_price) if lost
# ─────────────────────────────────────────────────────────────

import json
import logging
from dataclasses import dataclass, field
from typing import Optional

import anthropic

from backtest.fetcher import ResolvedMarket
from config import ANTHROPIC_API_KEY, CLAUDE_MODEL

logger = logging.getLogger(__name__)
client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

# Synthetic entry prices to test across (since we often can't get real historical prices)
SYNTHETIC_PRICES = [0.10, 0.20, 0.30, 0.40, 0.50, 0.60, 0.70, 0.80, 0.90]


@dataclass
class BacktestResult:
    market_id:           str
    question:            str
    resolved_yes:        bool
    claude_probability:  float
    confidence:          str
    reasoning:           str
    # Results at different edge thresholds and entry prices
    simulations:         list[dict] = field(default_factory=list)


@dataclass
class SimulationResult:
    entry_price:   float
    edge:          float
    direction:     str     # "YES" or "NO"
    would_trade:   bool
    correct:       bool    # Was Claude's direction right?
    pnl_per_unit:  float   # If traded: (1 - entry) if win, -entry if loss


def _build_batch_prompt(markets: list[ResolvedMarket]) -> str:
    """Build prompt for batch analysis of historical markets."""
    lines = []
    for i, m in enumerate(markets):
        lines.append(f"{i+1}. [{m.market_id[:8]}] {m.question}")

    return f"""You are an expert prediction market analyst performing a calibration exercise.

Analyse each market question below and estimate what the TRUE probability of YES was/is.
Do NOT try to guess the actual outcome — reason from first principles about what a well-calibrated
probability should be for each question.

MARKETS:
{chr(10).join(lines)}

INSTRUCTIONS:
- Be well-calibrated. If you're genuinely uncertain, reflect that in your probability.
- Use base rates, expert consensus, and domain knowledge.
- Do NOT anchor to 50% — give your honest best estimate.
- Confidence = "high" only if you have strong evidence for your estimate.

Respond ONLY with a JSON array, one object per market, same order:
[
  {{
    "market_id": "<first 8 chars>",
    "yes_probability": 0.XX,
    "confidence": "low" | "medium" | "high",
    "reasoning": "One sentence explaining your estimate"
  }},
  ...
]"""


def run_claude_on_batch(
    markets: list[ResolvedMarket],
) -> list[Optional[dict]]:
    """
    Send a batch of markets to Claude and get probability estimates.
    Returns a list aligned with the input markets (None if analysis failed).
    """
    if not markets:
        return []

    prompt = _build_batch_prompt(markets)

    try:
        response = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=4000,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = response.content[0].text.strip()
        clean = raw.replace("```json", "").replace("```", "").strip()
        results = json.loads(clean)

        if not isinstance(results, list):
            logger.error("Claude returned non-list for batch")
            return [None] * len(markets)

        # Align by position (Claude returns in order)
        aligned = []
        for i, market in enumerate(markets):
            if i < len(results):
                aligned.append(results[i])
            else:
                aligned.append(None)
        return aligned

    except json.JSONDecodeError as e:
        logger.error(f"JSON parse error in backtest batch: {e}")
        return [None] * len(markets)
    except anthropic.APIError as e:
        logger.error(f"API error in backtest batch: {e}")
        return [None] * len(markets)


def simulate_at_price(
    claude_prob: float,
    confidence: str,
    resolved_yes: bool,
    entry_price: float,
    edge_threshold: float,
) -> SimulationResult:
    """
    Simulate one trade at a given entry price and edge threshold.
    Returns the outcome of the simulated trade.
    """
    edge = claude_prob - entry_price

    if edge >= 0:
        direction = "YES"
        abs_edge = edge
    else:
        direction = "NO"
        abs_edge = abs(edge)

    would_trade = abs_edge >= edge_threshold and confidence != "low"

    # Did we get the direction right?
    correct = (direction == "YES" and resolved_yes) or (direction == "NO" and not resolved_yes)

    # PnL per unit staked if we traded
    if would_trade:
        if direction == "YES":
            pnl = (1.0 - entry_price) if resolved_yes else -entry_price
        else:
            no_price = 1.0 - entry_price
            pnl = (1.0 - no_price) if not resolved_yes else -no_price
    else:
        pnl = 0.0

    return SimulationResult(
        entry_price=entry_price,
        edge=round(edge, 4),
        direction=direction,
        would_trade=would_trade,
        correct=correct,
        pnl_per_unit=round(pnl, 4),
    )


def backtest_markets(
    markets: list[ResolvedMarket],
    edge_thresholds: list[float] = None,
    batch_size: int = 20,
) -> list[BacktestResult]:
    """
    Main backtesting function.

    For each market:
    1. Ask Claude for its probability estimate (batched)
    2. Simulate trades at each synthetic entry price × edge threshold
    3. Record outcomes

    Returns list of BacktestResult objects.
    """
    if edge_thresholds is None:
        edge_thresholds = [0.06, 0.08, 0.10, 0.12, 0.15, 0.20]

    results = []
    total = len(markets)

    for batch_start in range(0, total, batch_size):
        batch = markets[batch_start: batch_start + batch_size]
        batch_num = batch_start // batch_size + 1
        total_batches = (total + batch_size - 1) // batch_size
        logger.info(f"Backtesting batch {batch_num}/{total_batches} ({len(batch)} markets)…")

        claude_outputs = run_claude_on_batch(batch)

        for market, claude_out in zip(batch, claude_outputs):
            if claude_out is None:
                logger.warning(f"No Claude output for market {market.market_id[:8]}")
                continue

            try:
                claude_prob = float(claude_out["yes_probability"])
                confidence  = claude_out.get("confidence", "medium")
                reasoning   = claude_out.get("reasoning", "")
            except (KeyError, ValueError, TypeError):
                continue

            # Run simulations across all synthetic prices × all thresholds
            simulations = []
            for price in SYNTHETIC_PRICES:
                for threshold in edge_thresholds:
                    sim = simulate_at_price(
                        claude_prob=claude_prob,
                        confidence=confidence,
                        resolved_yes=market.resolved_yes,
                        entry_price=price,
                        edge_threshold=threshold,
                    )
                    simulations.append({
                        "entry_price":   sim.entry_price,
                        "edge_threshold": threshold,
                        "edge":          sim.edge,
                        "direction":     sim.direction,
                        "would_trade":   sim.would_trade,
                        "correct":       sim.correct,
                        "pnl_per_unit":  sim.pnl_per_unit,
                    })

            results.append(BacktestResult(
                market_id=market.market_id,
                question=market.question,
                resolved_yes=market.resolved_yes,
                claude_probability=round(claude_prob, 4),
                confidence=confidence,
                reasoning=reasoning,
                simulations=simulations,
            ))

    logger.info(f"Backtesting complete. Analysed {len(results)}/{total} markets.")
    return results
