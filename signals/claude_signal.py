# signals/claude_signal.py
# ─────────────────────────────────────────────────────────────
# Brain of the bot: sends markets to Claude, returns trade signals.
#
# Sprint 1: prompt caching, tool use, resolution criteria
# Sprint 2: extended thinking confirmation (S2-2)
# Sprint 3: calibration correction (S3-1), new market handling (S3-2)
# Sprint 4: batch API re-analysis (S4-4)
# ─────────────────────────────────────────────────────────────

import logging
import anthropic
from dataclasses import dataclass, field
from typing import Optional
from config import ANTHROPIC_API_KEY, MIN_EDGE_TO_TRADE, CLAUDE_MODEL
from signals.categorizer import get_category_context, CATEGORY_CONTEXT

logger = logging.getLogger(__name__)

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

_CONFIRMATION_MODEL = "claude-sonnet-4-6"

# ── S4-4: active batch state ──────────────────────────────────
_active_batch_id: str | None = None


@dataclass
class TradeSignal:
    market_id: str
    question: str
    market_yes_price: float
    claude_yes_probability: float
    edge: float
    direction: str
    confidence: str
    reasoning: str
    wallet_alignment: bool
    should_trade: bool
    confirmed_by_thinking: bool = field(default=False)  # S2-2

    def __repr__(self):
        arrow = "↑" if self.direction == "YES" else "↓"
        confirmed = " ✓think" if self.confirmed_by_thinking else ""
        return (
            f"Signal({arrow}{self.direction} | "
            f"market={self.market_yes_price:.0%} | "
            f"claude={self.claude_yes_probability:.0%} | "
            f"edge={self.edge:+.0%} | "
            f"trade={self.should_trade}{confirmed})\n"
            f"  Q: {self.question[:70]}...\n"
            f"  Reason: {self.reasoning[:100]}..."
        )


# ── Static system prompt (S1-1: cached across calls) ─────────────────────────
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
7. [NEW] markets (listed <48h) may have less-efficient pricing at formation — apply slightly more scrutiny to find edge.

CATEGORY-SPECIFIC GUIDANCE:
{_CATEGORY_GUIDANCE}

REASONING QUALITY:
Write one sentence identifying the single most important factor driving your probability estimate. Reference concrete evidence where possible.
- Weak: "There is significant uncertainty around this outcome."
- Strong: "FedWatch futures currently price a 71% cut probability, closely matching the market; no clear divergence."
- Strong: "Bitcoin is 19% below the $100k target with 5 days remaining; historically this gap is rarely closed in the final week."
"""

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
                        "market_id":       {"type": "string"},
                        "yes_probability": {"type": "number", "minimum": 0.01, "maximum": 0.99},
                        "confidence":      {"type": "string", "enum": ["low", "medium", "high"]},
                        "reasoning":       {"type": "string"},
                    },
                    "required": ["market_id", "yes_probability", "confidence", "reasoning"]
                }
            }
        },
        "required": ["analyses"]
    }
}


def _get_calibration_correction(question: str, scan_count: int = 0) -> float:
    """Import lazily to avoid circular imports at module load time."""
    try:
        from backtest.calibration import get_correction
        return get_correction(question, scan_count)
    except Exception:
        return 0.0


def _build_signal(market: dict, result: dict, wallet_signals: list[dict],
                  scan_count: int = 0) -> Optional[TradeSignal]:
    """Convert a single Claude result dict into a TradeSignal."""
    try:
        market_id = market["market_id"]
        yes_price = market["yes"]

        claude_prob = float(result["yes_probability"])

        # S3-1: apply per-category calibration correction
        correction = _get_calibration_correction(market.get("question", ""), scan_count)
        if correction != 0.0:
            corrected_prob = max(0.01, min(0.99, claude_prob - correction))
            logger.debug(
                f"Calibration: {claude_prob:.3f} → {corrected_prob:.3f} "
                f"(correction={correction:+.3f})"
            )
            claude_prob = corrected_prob

        confidence = result.get("confidence", "medium")
        reasoning  = result.get("reasoning", "No reasoning provided")
        edge       = claude_prob - yes_price

        if edge >= 0:
            direction = "YES"
            abs_edge  = edge
        else:
            direction = "NO"
            abs_edge  = abs(edge)

        wallet_alignment = False
        if wallet_signals:
            relevant = [s for s in wallet_signals if s.get("market_id") == market_id]
            wallet_alignment = any(s["outcome"] == direction for s in relevant)

        # S3-2: lower threshold for newly listed markets
        effective_min = MIN_EDGE_TO_TRADE * 0.8 if market.get("is_new_market") else MIN_EDGE_TO_TRADE

        should_trade = abs_edge >= effective_min and confidence != "low"
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
    max_markets: int = 20,
    scan_count: int = 0,
) -> tuple[list[TradeSignal], list[TradeSignal]]:
    """
    Analyse multiple markets in a single Claude API call.
    Returns (all_signals, tradeable_signals).
    """
    markets_to_check = [
        m for m in markets[:max_markets]
        if m.get("market_id") and m.get("question") and m.get("yes") is not None
    ]

    if not markets_to_check:
        logger.info("No valid markets to analyse")
        return [], []

    logger.info(f"Analysing {len(markets_to_check)} markets with Claude...")

    market_lines = []
    for i, m in enumerate(markets_to_check):
        cat, _ = get_category_context(m["question"])

        new_tag = " [NEW]" if m.get("is_new_market") else ""

        wallet_note = ""
        if wallet_signals:
            relevant = [s for s in wallet_signals if s.get("market_id") == m["market_id"]]
            if relevant:
                parts = [
                    f"wallet {s['wallet'][:8]}... ({s['win_rate']:.0%} win rate) "
                    f"bets {s['outcome']} ${s['size_usd']:,.0f}"
                    for s in relevant
                ]
                wallet_note = f" | Elite: {'; '.join(parts)}"

        resolution_note = ""
        if m.get("resolution_criteria"):
            resolution_note = f"\n   Resolution: {m['resolution_criteria'][:250]}"

        live_note = ""
        if (enrichment or {}).get(m["market_id"]):
            live_note = f"\n   LIVE: {enrichment[m['market_id']]}"

        velocity_note = ""
        v = m.get("price_velocity_24h")
        if v is not None and abs(v) >= 0.02:
            velocity_note = f" | 24h: {v:+.0%}"

        market_lines.append(
            f"{i+1}. [{m['market_id']}] [{cat}]{new_tag} {m['question']}\n"
            f"   YES={m['yes']:.1%}  NO={1-m['yes']:.1%}"
            f"{wallet_note}{velocity_note}{resolution_note}{live_note}"
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
                "cache_control": {"type": "ephemeral"},
            }],
            tools=[_TOOL],
            tool_choice={"type": "tool", "name": "submit_market_analysis"},
            messages=[{"role": "user", "content": user_message}]
        )

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

        usage = response.usage
        cache_read   = getattr(usage, "cache_read_input_tokens", 0) or 0
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
            mid    = result.get("market_id")
            market = market_by_id.get(mid)
            if not market:
                logger.warning(f"Claude returned unknown market_id: {mid}")
                continue

            signal = _build_signal(market, result, wallet_signals, scan_count)
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


def confirm_high_edge_signals(
    signals: list[TradeSignal],
    markets: list[dict],
    enrichment: dict[str, str] = None,
    max_confirmations: int = 3,
) -> list[TradeSignal]:
    """
    S2-2: Re-analyse high-edge signals with adaptive thinking on Sonnet.
    Updates confidence/reasoning in place for signals where abs(edge) > 0.20.
    Capped at max_confirmations per scan to control cost.
    """
    candidates = [
        s for s in signals
        if abs(s.edge) > 0.20 and s.confidence != "low"
    ][:max_confirmations]

    if not candidates:
        return signals

    market_by_id = {m["market_id"]: m for m in markets}
    logger.info(f"Confirming {len(candidates)} high-edge signal(s) with extended thinking...")

    for signal in candidates:
        market = market_by_id.get(signal.market_id)
        if not market:
            continue

        live_ctx = (enrichment or {}).get(signal.market_id, "")
        resolution = market.get("resolution_criteria", "")[:200]

        content = (
            f"Re-analyse this single high-edge prediction market with careful reasoning:\n\n"
            f"Market: {signal.question}\n"
            f"Current YES price: {signal.market_yes_price:.1%}\n"
            f"Your initial estimate: {signal.claude_yes_probability:.1%} "
            f"(edge={signal.edge:+.1%}, confidence={signal.confidence})\n"
            f"Initial reasoning: {signal.reasoning}\n"
        )
        if resolution:
            content += f"Resolution criteria: {resolution}\n"
        if live_ctx:
            content += f"Live context: {live_ctx}\n"
        content += "\nCall submit_market_analysis with your updated assessment."

        try:
            resp = client.messages.create(
                model=_CONFIRMATION_MODEL,
                max_tokens=8000,
                thinking={"type": "adaptive"},
                system=[{
                    "type": "text",
                    "text": _SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }],
                tools=[_TOOL],
                tool_choice={"type": "auto"},  # forced tool_choice conflicts with thinking
                messages=[{"role": "user", "content": content}]
            )

            tool_block = next(
                (b for b in resp.content if b.type == "tool_use"),
                None
            )
            if not tool_block:
                continue

            analyses = tool_block.input.get("analyses", [])
            if not analyses:
                continue

            result = analyses[0]
            new_prob  = float(result.get("yes_probability", signal.claude_yes_probability))
            new_conf  = result.get("confidence", signal.confidence)
            new_reason = result.get("reasoning", signal.reasoning)

            if new_conf != signal.confidence or abs(new_prob - signal.claude_yes_probability) > 0.03:
                logger.info(
                    f"Thinking confirmation [{signal.market_id[:8]}]: "
                    f"{signal.claude_yes_probability:.0%}→{new_prob:.0%} "
                    f"conf={signal.confidence}→{new_conf}"
                )
                signal.claude_yes_probability = new_prob
                signal.confidence = new_conf
                signal.reasoning  = new_reason
                new_edge = new_prob - signal.market_yes_price
                signal.edge      = new_edge
                signal.direction = "YES" if new_edge >= 0 else "NO"
                # Re-evaluate should_trade if confidence was downgraded to low
                if new_conf == "low":
                    signal.should_trade = False

            signal.confirmed_by_thinking = True

        except anthropic.APIError as e:
            logger.warning(f"Thinking confirmation failed for {signal.market_id[:8]}: {e}")

    return signals


# ── S4-4: Batch API re-analysis ───────────────────────────────

def batch_reanalyse_open_positions(
    open_trades: dict,
    markets_parsed: list[dict],
) -> str | None:
    """
    Create an Anthropic Messages Batch to re-analyse all open positions.
    Non-blocking — returns batch_id. Call poll_batch_results() later.
    """
    global _active_batch_id

    if not open_trades:
        return None

    market_by_id = {m["market_id"]: m for m in markets_parsed}
    requests_list = []

    for market_id, trade in open_trades.items():
        market = market_by_id.get(market_id)
        current_yes = market.get("yes") if market else None
        price_str = f"Current YES price: {current_yes:.1%}" if current_yes else "Current price unknown"

        content = (
            f"Re-analyse this open position:\n\n"
            f"Question: {trade.question}\n"
            f"Direction: {trade.direction} @ entry {trade.entry_price:.1%}\n"
            f"{price_str}\n"
            f"Call submit_market_analysis with updated assessment."
        )
        requests_list.append({
            "custom_id": market_id,
            "params": {
                "model": CLAUDE_MODEL,
                "max_tokens": 1000,
                "system": [{"type": "text", "text": _SYSTEM_PROMPT,
                             "cache_control": {"type": "ephemeral"}}],
                "tools": [_TOOL],
                "tool_choice": {"type": "tool", "name": "submit_market_analysis"},
                "messages": [{"role": "user", "content": content}],
            }
        })

    if not requests_list:
        return None

    try:
        batch = client.messages.batches.create(requests=requests_list)
        _active_batch_id = batch.id
        logger.info(f"Batch reanalysis created: {batch.id} ({len(requests_list)} positions)")
        return batch.id
    except Exception as e:
        logger.warning(f"Batch API creation failed: {e}")
        return None


def poll_batch_results(batch_id: str) -> dict | None:
    """
    Poll for batch results. Returns {market_id: result_dict} if complete, else None.
    """
    try:
        batch = client.messages.batches.retrieve(batch_id)
        if batch.processing_status != "ended":
            return None

        results = {}
        for item in client.messages.batches.results(batch_id):
            if item.result.type != "succeeded":
                continue
            tool_block = next(
                (b for b in item.result.message.content if b.type == "tool_use"),
                None
            )
            if not tool_block:
                continue
            analyses = tool_block.input.get("analyses", [])
            if analyses:
                results[item.custom_id] = analyses[0]

        logger.info(f"Batch {batch_id[:12]}… complete: {len(results)} results")
        return results

    except Exception as e:
        logger.debug(f"Batch poll failed: {e}")
        return None
