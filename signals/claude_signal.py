# signals/claude_signal.py
# ─────────────────────────────────────────────────────────────
# This is the "brain" of the bot.
# We send all markets to Claude in a single batched call, along with:
#   - The question being asked
#   - The current market price (implied probability)
#   - Any elite wallet signals for each market
#
# Claude returns a structured JSON array with probability estimates.
# If Claude's estimate differs significantly from the market price → edge.
# ─────────────────────────────────────────────────────────────

import json
import logging
import anthropic
from dataclasses import dataclass
from typing import Optional
from config import ANTHROPIC_API_KEY, MIN_EDGE_TO_TRADE, CLAUDE_MODEL
from signals.categorizer import get_category_context

logger = logging.getLogger(__name__)

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


@dataclass
class TradeSignal:
    """
    The output of Claude's analysis for a single market.

    If edge > MIN_EDGE_TO_TRADE, we consider placing a trade.
    """
    market_id: str
    question: str
    market_yes_price: float         # What the market says (0 to 1)
    claude_yes_probability: float   # What Claude thinks (0 to 1)
    edge: float                     # claude_probability - market_price
    direction: str                  # "YES" or "NO"
    confidence: str                 # "low", "medium", "high"
    reasoning: str                  # Claude's explanation
    wallet_alignment: bool          # Do elite wallets agree?
    should_trade: bool              # Final recommendation

    def __repr__(self):
        arrow = "↑" if self.direction == "YES" else "↓"
        return (
            f"Signal({arrow}{self.direction} | "
            f"market={self.market_yes_price:.0%} | "
            f"claude={self.claude_yes_probability:.0%} | "
            f"edge={self.edge:+.0%} | "
            f"trade={self.should_trade})\n"
            f"  Q: {self.question[:70]}...\n"
            f"  Reason: {self.reasoning[:100]}..."
        )


def _build_signal(market: dict, result: dict, wallet_signals: list[dict]) -> Optional[TradeSignal]:
    """Convert a single Claude result dict into a TradeSignal."""
    try:
        market_id = market["market_id"]
        yes_price = market["yes"]

        claude_prob = float(result["yes_probability"])
        confidence = result.get("confidence", "medium")
        reasoning = result.get("reasoning", "No reasoning provided")

        edge = claude_prob - yes_price

        if edge >= 0:
            direction = "YES"
            abs_edge = edge
        else:
            direction = "NO"
            abs_edge = abs(edge)

        wallet_alignment = False
        if wallet_signals:
            relevant = [s for s in wallet_signals if s.get("market_id") == market_id]
            wallet_alignment = any(s["outcome"] == direction for s in relevant)

        should_trade = abs_edge >= MIN_EDGE_TO_TRADE and confidence != "low"
        if wallet_alignment and abs_edge >= MIN_EDGE_TO_TRADE * 0.8:
            should_trade = True

        return TradeSignal(
            market_id=market_id,
            question=market["question"],
            market_yes_price=yes_price,
            claude_yes_probability=claude_prob,
            edge=edge,
            direction=direction,
            confidence=confidence,
            reasoning=reasoning,
            wallet_alignment=wallet_alignment,
            should_trade=should_trade,
        )
    except (KeyError, ValueError, TypeError) as e:
        logger.warning(f"Could not build signal for market {market.get('market_id')}: {e}")
        return None


def batch_analyse_markets(
    markets: list[dict],
    wallet_signals: list[dict] = None,
    enrichment: dict[str, str] = None,
    max_markets: int = 20
) -> list[TradeSignal]:
    """
    Analyse multiple markets in a single Claude API call.

    Sends all markets in one batched prompt and parses the JSON array
    response — far cheaper than one call per market.

    Returns:
        List of TradeSignal objects where should_trade = True
    """
    markets_to_check = [
        m for m in markets[:max_markets]
        if m.get("market_id") and m.get("question") and m.get("yes") is not None
    ]

    if not markets_to_check:
        logger.info("No valid markets to analyse")
        return [], []

    logger.info(f"Analysing {len(markets_to_check)} markets with Claude...")

    # Build the market list for the prompt — group by category for context injection
    market_lines = []
    category_contexts = {}   # category → context string (for deduplication)

    for i, m in enumerate(markets_to_check):
        cat, ctx = get_category_context(m["question"])
        category_contexts[cat] = ctx   # same category seen multiple times → overwrite is fine

        wallet_note = ""
        if wallet_signals:
            relevant = [s for s in wallet_signals if s.get("market_id") == m["market_id"]]
            if relevant:
                parts = [
                    f"wallet {s['wallet'][:8]}... ({s['win_rate']:.0%} win rate) bets {s['outcome']} ${s['size_usd']:,.0f}"
                    for s in relevant
                ]
                wallet_note = f" | Elite signals: {'; '.join(parts)}"

        live_ctx = (enrichment or {}).get(m["market_id"], "")
        live_note = f"\n   LIVE: {live_ctx}" if live_ctx else ""

        market_lines.append(
            f"{i+1}. [{m['market_id']}] [{cat}] {m['question']}\n"
            f"   YES={m['yes']:.1%}  NO={1-m['yes']:.1%}{wallet_note}{live_note}"
        )

    markets_block = "\n".join(market_lines)

    # Build category guidance block (only categories that appear in this batch)
    category_block = "\n\n".join(
        f"[{cat}]\n{ctx}" for cat, ctx in sorted(category_contexts.items())
    )

    prompt = f"""You are an expert prediction market analyst. Analyse each market below and estimate the true probability of YES.

CATEGORY GUIDANCE (apply when relevant):
{category_block}

MARKETS:
{markets_block}

INSTRUCTIONS:
- Each market is tagged with its category. Apply the relevant category guidance above.
- Reason about the true probability using base rates, recent news, expert consensus, and resolution criteria.
- Be calibrated — don't deviate from the market price without strong reason.
- Elite wallet signals (if shown) are weak supporting evidence only.

Respond ONLY with a JSON array — one object per market, in the same order. No markdown, no extra text:
[
  {{
    "market_id": "<id>",
    "yes_probability": 0.XX,
    "confidence": "low" | "medium" | "high",
    "reasoning": "One clear sentence"
  }},
  ...
]"""

    try:
        response = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}]
        )

        raw_text = response.content[0].text.strip()
        clean_json = raw_text.replace("```json", "").replace("```", "").strip()
        results = json.loads(clean_json)

        if not isinstance(results, list):
            logger.error("Claude returned non-list JSON")
            return []

        # Index markets by ID for fast lookup
        market_by_id = {m["market_id"]: m for m in markets_to_check}

        all_signals = []
        tradeable_signals = []
        for result in results:
            mid = result.get("market_id")
            market = market_by_id.get(mid)
            if not market:
                logger.warning(f"Claude returned unknown market_id: {mid}")
                continue

            signal = _build_signal(market, result, wallet_signals)
            if signal:
                logger.info(f"Signal: {signal}")
                all_signals.append(signal)
                if signal.should_trade:
                    tradeable_signals.append(signal)

        logger.info(f"Found {len(tradeable_signals)} tradeable signals")
        return all_signals, tradeable_signals

    except json.JSONDecodeError as e:
        logger.error(f"Claude returned invalid JSON: {e}\nRaw: {raw_text}")
        return [], []
    except anthropic.APIStatusError as e:
        logger.error(
            f"Anthropic API HTTP {e.status_code} — type={e.type!r} message={e.message!r}"
        )
        if "credit balance" in str(e).lower():
            logger.critical(
                "Anthropic API credit balance exhausted. "
                "Top up at console.anthropic.com/settings/billing and restart."
            )
        return [], []
    except anthropic.APIError as e:
        logger.error(f"Anthropic API error: {e}")
        return [], []
    except Exception as e:
        logger.error(f"Unexpected error in batch_analyse_markets: {e}")
        return [], []
