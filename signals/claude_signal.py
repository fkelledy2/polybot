# signals/claude_signal.py
# ─────────────────────────────────────────────────────────────
# Brain of the bot: sends markets to Claude, returns trade signals.
#
# Sprint 1 upgrades:
#   S1-1  Prompt caching   — static system prompt cached; ~80% input token reduction
#   S1-2  Tool use         — structured output via tool definition; no JSON parsing
#   S1-3  Resolution criteria — injected per-market from Polymarket API
# ─────────────────────────────────────────────────────────────

import logging
import anthropic
from dataclasses import dataclass
from typing import Optional
from config import ANTHROPIC_API_KEY, MIN_EDGE_TO_TRADE, CLAUDE_MODEL
from signals.categorizer import get_category_context, CATEGORY_CONTEXT

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


# ── Static system prompt (S1-1: cached across calls) ─────────────────────────
# All category contexts are embedded here so this block exceeds the cache
# minimum token threshold. The user message carries only the dynamic market list.

_CATEGORY_GUIDANCE = "\n\n".join(
    f"[{cat}]\n{ctx}" for cat, ctx in sorted(CATEGORY_CONTEXT.items())
)

_SYSTEM_PROMPT = f"""You are an expert prediction market analyst specialising in Polymarket — a decentralised binary outcome market where prices represent the probability of YES resolving (0.0 = 0%, 1.0 = 100%). Each market settles at $1.00 if YES or $0.00 if NO.

YOUR ROLE:
Estimate the true probability that the YES outcome resolves for each market. You are looking for mispricings where the market's implied probability materially differs from the true probability based on evidence and base rates.

CORE PRINCIPLES:
1. Markets are generally efficient. Do not deviate from the market price without a clear, specific reason backed by concrete evidence, known base rates, or a documented information asymmetry. Most markets should receive a probability close to the current price.
2. Calibration over edge: If you are uncertain, stay close to the market price. 0.52 is a legitimate estimate. Your edge must be earned from analysis, not assumed.
3. Resolution criteria are binding: Read them carefully when provided. "X by date Y" is not the same as "X ever". Predict whether THIS specific resolution criteria will be met — not whether the event is likely in general.
4. Confidence mapping:
   - "high": Clear, specific reason to deviate significantly from the market price. Strong evidence, relevant base rates, or a direct information source confirms the deviation.
   - "medium": Some evidence or a relevant base rate, but uncertainty remains. Moderate deviation acceptable.
   - "low": Limited information or high uncertainty. Stay near the market price. A "low" confidence answer should have yes_probability within ~5 percentage points of the market price.
5. Elite wallet signals are weak supporting evidence. Even top traders are right ~60% of the time. Weight them as a minor tiebreaker, not a primary signal.
6. Avoid anchoring to round numbers — your probability should reflect your actual belief, not be rounded to 0.50, 0.60, 0.70, etc.

CATEGORY-SPECIFIC GUIDANCE:
{_CATEGORY_GUIDANCE}

REASONING QUALITY:
Write one sentence identifying the single most important factor driving your probability estimate. Reference concrete evidence where possible.
- Weak: "There is significant uncertainty around this outcome."
- Strong: "FedWatch futures currently price a 71% cut probability, closely matching the market; no clear divergence."
- Strong: "Bitcoin is 19% below the $100k target with 5 days remaining; historically this gap is rarely closed in the final week."
"""

# ── Tool definition (S1-2: structured output, no fragile JSON parsing) ────────
_TOOL = {
    "name": "submit_market_analysis",
    "description": "Submit your probability estimates and reasoning for every market in this batch. Include exactly one entry per market.",
    "input_schema": {
        "type": "object",
        "properties": {
            "analyses": {
                "type": "array",
                "description": "One analysis object per market, in any order",
                "items": {
                    "type": "object",
                    "properties": {
                        "market_id": {
                            "type": "string",
                            "description": "The market_id exactly as given in the input"
                        },
                        "yes_probability": {
                            "type": "number",
                            "description": "Your estimated probability of YES resolving (0.01 to 0.99)",
                            "minimum": 0.01,
                            "maximum": 0.99
                        },
                        "confidence": {
                            "type": "string",
                            "enum": ["low", "medium", "high"],
                            "description": "Your confidence in this estimate"
                        },
                        "reasoning": {
                            "type": "string",
                            "description": "One clear sentence explaining the key factor driving your estimate"
                        }
                    },
                    "required": ["market_id", "yes_probability", "confidence", "reasoning"]
                }
            }
        },
        "required": ["analyses"]
    }
}


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

    Uses a cached system prompt (S1-1) and structured tool output (S1-2).
    Resolution criteria from Polymarket are injected per market (S1-3).

    Returns:
        Tuple of (all_signals, tradeable_signals)
    """
    markets_to_check = [
        m for m in markets[:max_markets]
        if m.get("market_id") and m.get("question") and m.get("yes") is not None
    ]

    if not markets_to_check:
        logger.info("No valid markets to analyse")
        return [], []

    logger.info(f"Analysing {len(markets_to_check)} markets with Claude...")

    # Build per-market lines — dynamic content, NOT cached
    market_lines = []
    for i, m in enumerate(markets_to_check):
        cat, _ = get_category_context(m["question"])

        wallet_note = ""
        if wallet_signals:
            relevant = [s for s in wallet_signals if s.get("market_id") == m["market_id"]]
            if relevant:
                parts = [
                    f"wallet {s['wallet'][:8]}... ({s['win_rate']:.0%} win rate) bets {s['outcome']} ${s['size_usd']:,.0f}"
                    for s in relevant
                ]
                wallet_note = f" | Elite signals: {'; '.join(parts)}"

        # S1-3: resolution criteria from Polymarket API
        resolution_note = ""
        if m.get("resolution_criteria"):
            resolution_note = f"\n   Resolution: {m['resolution_criteria'][:250]}"

        live_note = ""
        if (enrichment or {}).get(m["market_id"]):
            live_note = f"\n   LIVE: {enrichment[m['market_id']]}"

        # S1-5: price momentum (only show moves ≥2pp in 24h)
        velocity_note = ""
        v = m.get("price_velocity_24h")
        if v is not None and abs(v) >= 0.02:
            velocity_note = f" | 24h: {v:+.0%}"

        market_lines.append(
            f"{i+1}. [{m['market_id']}] [{cat}] {m['question']}\n"
            f"   YES={m['yes']:.1%}  NO={1-m['yes']:.1%}{wallet_note}{velocity_note}{resolution_note}{live_note}"
        )

    markets_block = "\n".join(market_lines)
    user_message = (
        f"Analyse these {len(markets_to_check)} prediction markets and call "
        f"submit_market_analysis with your estimates:\n\n{markets_block}"
    )

    try:
        response = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=3000,
            system=[{
                "type": "text",
                "text": _SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},  # S1-1: cache static system prompt
            }],
            tools=[_TOOL],
            tool_choice={"type": "tool", "name": "submit_market_analysis"},  # S1-2: force tool use
            messages=[{"role": "user", "content": user_message}]
        )

        # S1-2: parse structured tool output — no JSON string parsing
        tool_block = next(
            (b for b in response.content if b.type == "tool_use"),
            None
        )
        if not tool_block:
            logger.error("Claude did not return a tool_use block")
            return [], []

        results = tool_block.input.get("analyses", [])
        if not isinstance(results, list):
            logger.error("Tool call 'analyses' field is not a list")
            return [], []

        # Log cache efficiency so we can verify S1-1 is working
        usage = response.usage
        cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
        cache_create = getattr(usage, "cache_creation_input_tokens", 0) or 0
        if cache_read or cache_create:
            logger.info(
                f"Tokens — input: {usage.input_tokens}, "
                f"cache_read: {cache_read}, cache_create: {cache_create}, "
                f"output: {usage.output_tokens}"
            )

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
