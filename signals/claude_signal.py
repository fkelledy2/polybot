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
from config import (
    ANTHROPIC_API_KEY, MIN_EDGE_TO_TRADE, MIN_ENTRY_PROBABILITY,
    MAX_DAYS_TO_RESOLVE, MIN_DAYS_TO_RESOLVE, CLAUDE_MODEL,
    MIN_EDGE_TO_TRADE_EXTREME, EXTREME_PRICE_THRESHOLD,
    ENABLE_WALLET_VETO, WALLET_VETO_ON_EXTREME,
    MOMENTUM_CONFIRM_DISCOUNT, MOMENTUM_OPPOSE_PENALTY, MOMENTUM_MIN_MAGNITUDE,
    LONGSHOT_NO_THRESHOLD, LONGSHOT_NO_MIN_EDGE,
    AMBIGUITY_BLOCK_THRESHOLD, AMBIGUITY_WARN_THRESHOLD,
)
from signals.categorizer import get_category_context, detect_category, CATEGORY_CONTEXT

try:
    from config import DISABLED_CATEGORIES as _DISABLED_CATEGORIES
except ImportError:
    _DISABLED_CATEGORIES = []

logger = logging.getLogger(__name__)

# Import notification alerts (safe to fail if not configured)
try:
    from notifications import alert_api_credit_exhausted
except ImportError:
    def alert_api_credit_exhausted(service):
        logger.warning(f"Notifications module not available: {service}")
        return False

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

_CONFIRMATION_MODEL = "claude-sonnet-4-6"

# ── S4-4: active batch state ──────────────────────────────────
_active_batch_id: str | None = None

# ── Signal cache — skip re-analysis when price hasn't moved ──
# market_id -> (cached_yes_price, TradeSignal, scan_count_when_cached)
_signal_cache: dict[str, tuple] = {}
_PRICE_MOVE_THRESHOLD = 0.02   # re-analyse if YES price moved >2%
_CACHE_TTL_SCANS      = 18     # force refresh after ~3 hrs (18×10 min scans)


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
8. [LONGSHOT] markets (YES price ≤ 12%) are structural overdog markets. Academic research confirms prediction market longshots lose ~60% of the time due to crowd overpricing. When you see [LONGSHOT] and agree the outcome is genuinely unlikely, set yes_probability in the 0.04–0.09 range and confidence "medium". Only assign higher probability if you have a specific concrete reason the market is underpriced.
9. [AMBIGUITY=X.XX] next to Resolution criteria means the criteria text contains vague or discretionary language (0.0=clear, 1.0=highly subjective). For AMBIGUITY >= 0.35: widen your probability toward 0.50 and lower confidence to "medium". For AMBIGUITY >= 0.60: set confidence "low" — these markets carry meaningful misresolution risk regardless of your probability estimate.
10. When LIVE context contains "Manifold consensus" with [DIVERGENCE ±N%], treat this as a meaningful independent calibration signal from a separate expert forecaster community. A [DIVERGENCE +10%] or more means Manifold forecasters think YES is more likely than Polymarket; a negative divergence means they think it is less likely. Weight this similarly to a Metaculus consensus — it is not decisive alone, but at ≥±10% divergence it should pull your estimate noticeably toward Manifold's direction unless you have specific evidence against it.
11. When LIVE context contains "WALLET SURGE", multiple elite Polymarket traders have newly entered the same direction simultaneously — this is the strongest wallet signal available and indicates possible informed money. Treat it as a moderate positive signal in the indicated direction. If the surge direction aligns with your estimate, add ~3–5% to your probability. If it conflicts with your estimate, hold your estimate but lower confidence to "medium" and note the disagreement. A surge does NOT override your fundamental analysis.

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


def _build_signal(market: dict, result: dict, wallet_signals: list[dict] = None,
                  scan_count: int = 0,
                  wallet_consensus: dict = None) -> Optional[TradeSignal]:
    """Convert a single Claude result dict into a TradeSignal."""
    try:
        market_id = market["market_id"]
        yes_price = market["yes"]

        claude_prob = float(result["yes_probability"])
        # Clamp before any arithmetic — Anthropic tool schema says [0.01, 0.99]
        # but downstream code must not trust the model to honour it.
        claude_prob = max(0.01, min(0.99, claude_prob))

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
            entry_probability = yes_price
        else:
            direction = "NO"
            abs_edge  = abs(edge)
            entry_probability = 1.0 - yes_price

        wallet_alignment = False
        has_wallet_data = False

        # Prefer richer consensus data; fall back to legacy flat list
        if wallet_consensus:
            cid = market.get("condition_id")
            wc = wallet_consensus.get(cid) if cid else None
            if wc:
                has_wallet_data = True
                wallet_alignment = (wc.winning_direction == direction)
        elif wallet_signals:
            relevant = [s for s in wallet_signals if s.get("market_id") == market_id]
            has_wallet_data = bool(relevant)
            wallet_alignment = any(s["outcome"] == direction for s in relevant)

        # S3-2: lower threshold for newly listed markets
        effective_min = MIN_EDGE_TO_TRADE * 0.8 if market.get("is_new_market") else MIN_EDGE_TO_TRADE

        # Apply higher edge requirement for extreme-priced markets (<5% or >95%)
        yes_price_is_extreme = (
            yes_price < EXTREME_PRICE_THRESHOLD
            or yes_price > (1 - EXTREME_PRICE_THRESHOLD)
        )
        if yes_price_is_extreme:
            effective_min = max(effective_min, MIN_EDGE_TO_TRADE_EXTREME)

        # ── FEAT-03: Longshot NO bias guard ──────────────────────
        if yes_price <= LONGSHOT_NO_THRESHOLD and direction == "NO":
            effective_min = min(effective_min, LONGSHOT_NO_MIN_EDGE)
            logger.debug(
                f"Longshot NO: YES={yes_price:.1%} edge bar→{LONGSHOT_NO_MIN_EDGE:.0%} "
                f"({market.get('question','')[:50]})"
            )

        # ── FEAT-04: Price momentum modifier ─────────────────────
        velocity = market.get("price_velocity_24h")
        if velocity is not None and abs(velocity) >= MOMENTUM_MIN_MAGNITUDE:
            momentum_confirms = (direction == "YES" and velocity > 0) or \
                                (direction == "NO"  and velocity < 0)
            if momentum_confirms:
                effective_min = max(0.02, effective_min - MOMENTUM_CONFIRM_DISCOUNT)
                logger.debug(f"Momentum confirms {direction}: vel={velocity:+.1%} bar→{effective_min:.0%}")
            else:
                effective_min = effective_min + MOMENTUM_OPPOSE_PENALTY
                logger.debug(f"Momentum opposes {direction}: vel={velocity:+.1%} bar→{effective_min:.0%}")

        should_trade = (
            abs_edge >= effective_min
            and confidence != "low"
            and entry_probability >= MIN_ENTRY_PROBABILITY
        )
        # Wallet alignment can unlock borderline trades with edge >= 80% of minimum
        if wallet_alignment and abs_edge >= MIN_EDGE_TO_TRADE * 0.8 and confidence != "low":
            should_trade = entry_probability >= MIN_ENTRY_PROBABILITY

        # Wallet veto: skip trade when elite wallets disagree
        if has_wallet_data and not wallet_alignment:
            if ENABLE_WALLET_VETO or (WALLET_VETO_ON_EXTREME and yes_price_is_extreme):
                should_trade = False

        # ── FEAT-05: Resolution ambiguity gate ───────────────────
        ambiguity = market.get("ambiguity_score", 0.0)
        if ambiguity >= AMBIGUITY_BLOCK_THRESHOLD:
            logger.info(
                f"Ambiguity block (score={ambiguity:.2f}): {market.get('question','')[:60]}"
            )
            should_trade = False
        elif ambiguity >= AMBIGUITY_WARN_THRESHOLD:
            logger.debug(
                f"Ambiguity warn (score={ambiguity:.2f}): {market.get('question','')[:60]}"
            )

        # Category filter: skip if this category has been disabled by the analyzer
        if _DISABLED_CATEGORIES:
            market_cat = detect_category(market.get("question", ""))
            if market_cat in _DISABLED_CATEGORIES:
                should_trade = False

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
    wallet_consensus: dict = None,
) -> tuple[list[TradeSignal], list[TradeSignal]]:
    """
    Analyse multiple markets in a single Claude API call.
    Returns (all_signals, tradeable_signals).
    """
    markets_to_check = []
    for m in markets[:max_markets]:
        if not (m.get("market_id") and m.get("question") and m.get("yes") is not None):
            continue

        days_to_resolve = m.get("days_to_resolve")
        if days_to_resolve is not None:
            if MAX_DAYS_TO_RESOLVE and days_to_resolve > MAX_DAYS_TO_RESOLVE:
                continue
            if MIN_DAYS_TO_RESOLVE and days_to_resolve < MIN_DAYS_TO_RESOLVE:
                continue

        markets_to_check.append(m)

    if not markets_to_check:
        logger.info("No valid markets to analyse")
        return [], []

    # ── Signal cache: only send markets whose price moved or cache expired ──
    fresh, cached_signals = [], []
    for m in markets_to_check:
        mid = m["market_id"]
        yes = m["yes"]
        entry = _signal_cache.get(mid)
        if entry is None:
            fresh.append(m)
        else:
            cached_price, cached_sig, cached_scan = entry
            price_moved = abs(yes - cached_price) >= _PRICE_MOVE_THRESHOLD
            cache_stale = (scan_count - cached_scan) >= _CACHE_TTL_SCANS
            if price_moved or cache_stale:
                fresh.append(m)
            else:
                cached_signals.append(cached_sig)

    if not fresh:
        logger.info(f"All {len(cached_signals)} markets served from signal cache (no price moves)")
        tradeable = [s for s in cached_signals if s.should_trade]
        return cached_signals, tradeable

    logger.info(
        f"Analysing {len(fresh)} markets with Claude "
        f"({len(cached_signals)} cached, {len(fresh)} fresh)..."
    )

    market_lines = []
    for i, m in enumerate(fresh):
        cat, _ = get_category_context(m["question"])

        if m.get("is_new_market"):
            new_tag = " [NEW]"
        elif m.get("is_discovered_market"):
            new_tag = " [ELITE-DISCOVERED]"
        else:
            new_tag = ""

        longshot_tag = " [LONGSHOT]" if m.get("yes", 1) <= LONGSHOT_NO_THRESHOLD else ""

        wallet_note = ""
        if wallet_consensus:
            cid = m.get("condition_id")
            wc = wallet_consensus.get(cid) if cid else None
            if wc:
                split = f"{wc.yes_count}Y/{wc.no_count}N"
                wallet_note = (
                    f" | Elite consensus: {wc.winning_direction} "
                    f"{wc.consensus_score:.0%} ({wc.trader_count} traders [{split}], "
                    f"${wc.raw_usd:,.0f} combined)"
                )
                if wc.avg_entry_price:
                    wallet_note += f" entry≈{wc.avg_entry_price:.0%}"
        elif wallet_signals:
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
            amb = m.get("ambiguity_score", 0.0)
            amb_tag = f" [AMBIGUITY={amb:.2f}]" if amb >= 0.20 else ""
            resolution_note = f"\n   Resolution{amb_tag}: {m['resolution_criteria'][:250]}"

        live_note = ""
        if (enrichment or {}).get(m["market_id"]):
            live_note = f"\n   LIVE: {enrichment[m['market_id']]}"

        velocity_note = ""
        v = m.get("price_velocity_24h")
        if v is not None and abs(v) >= 0.02:
            velocity_note = f" | 24h: {v:+.0%}"

        market_lines.append(
            f"{i+1}. [{m['market_id']}] [{cat}]{new_tag}{longshot_tag} {m['question']}\n"
            f"   YES={m['yes']:.1%}  NO={1-m['yes']:.1%}"
            f"{wallet_note}{velocity_note}{resolution_note}{live_note}"
        )

    markets_block = "\n".join(market_lines)
    user_message = (
        f"Analyse these {len(fresh)} prediction markets and call "
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
            return cached_signals, [s for s in cached_signals if s.should_trade]

        results = tool_block.input.get("analyses", [])
        if not isinstance(results, list):
            logger.error("Tool call 'analyses' field is not a list")
            return cached_signals, [s for s in cached_signals if s.should_trade]

        usage = response.usage
        cache_read   = getattr(usage, "cache_read_input_tokens", 0) or 0
        cache_create = getattr(usage, "cache_creation_input_tokens", 0) or 0
        if cache_read or cache_create:
            logger.info(
                f"Tokens — input: {usage.input_tokens}, "
                f"cache_read: {cache_read}, cache_create: {cache_create}, "
                f"output: {usage.output_tokens}"
            )
        try:
            from web.usage import record_anthropic
            record_anthropic(CLAUDE_MODEL, usage.input_tokens, usage.output_tokens,
                             cache_read, cache_create)
        except Exception:
            pass

        market_by_id = {m["market_id"]: m for m in fresh}

        new_signals = []
        for result in results:
            mid    = result.get("market_id")
            market = market_by_id.get(mid)
            if not market:
                logger.warning(f"Claude returned unknown market_id: {mid}")
                continue

            signal = _build_signal(market, result, wallet_signals, scan_count,
                                   wallet_consensus=wallet_consensus)
            if signal:
                logger.info(f"Signal: {signal}")
                new_signals.append(signal)
                _signal_cache[mid] = (market["yes"], signal, scan_count)

        all_signals = cached_signals + new_signals
        tradeable_signals = [s for s in all_signals if s.should_trade]
        logger.info(
            f"Found {len(tradeable_signals)} tradeable signals "
            f"({len(new_signals)} fresh, {len(cached_signals)} cached)"
        )
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
            # Send email alert for credit exhaustion
            alert_api_credit_exhausted("Anthropic")
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
    max_confirmations: int = 1,
) -> list[TradeSignal]:
    """
    S2-2: Re-analyse high-edge signals with adaptive thinking on Sonnet.
    Updates confidence/reasoning in place for signals where abs(edge) > 0.20.
    Capped at max_confirmations per scan to control cost.
    """
    candidates = [
        s for s in signals
        if abs(s.edge) > 0.30 and s.confidence != "low"
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

            try:
                from web.usage import record_anthropic
                _u = resp.usage
                record_anthropic(
                    _CONFIRMATION_MODEL, _u.input_tokens, _u.output_tokens,
                    getattr(_u, "cache_read_input_tokens", 0) or 0,
                    getattr(_u, "cache_creation_input_tokens", 0) or 0,
                )
            except Exception:
                pass

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
