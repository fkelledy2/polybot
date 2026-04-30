# signals/arbitrage.py
# ─────────────────────────────────────────────────────────────
# Related market pair detection (S3-3).
# Finds complementary markets whose YES prices don't sum to ~1.0,
# indicating a model-free mispricing opportunity.
# ─────────────────────────────────────────────────────────────

import re
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_STOP = frozenset({
    "will", "does", "is", "are", "has", "the", "a", "an", "be",
    "in", "on", "at", "to", "of", "for", "from", "by", "and", "or",
    "not", "this", "that", "which", "who", "if", "it",
})


def _keywords(question: str) -> set[str]:
    words = re.sub(r"[^\w\s]", " ", question.lower()).split()
    return {w for w in words if w not in _STOP and len(w) > 3}


@dataclass
class ArbitragePair:
    market_a: dict
    market_b: dict
    implied_sum: float
    gap: float
    direction: str   # "OVERPRICED" or "UNDERPRICED"


def find_arbitrage_pairs(markets: list[dict]) -> list[ArbitragePair]:
    """
    Scan markets for complementary pairs where YES prices sum to >1.05 or <0.95.
    Requires ≥3 shared keywords to flag as related.
    """
    pairs = []
    n = len(markets)

    for i in range(n):
        kw_i = _keywords(markets[i].get("question", ""))
        for j in range(i + 1, n):
            kw_j = _keywords(markets[j].get("question", ""))
            if len(kw_i & kw_j) < 3:
                continue

            yes_sum = markets[i].get("yes", 0.5) + markets[j].get("yes", 0.5)
            gap = abs(yes_sum - 1.0)
            if gap < 0.05:
                continue

            direction = "OVERPRICED" if yes_sum > 1.0 else "UNDERPRICED"
            pairs.append(ArbitragePair(
                market_a=markets[i],
                market_b=markets[j],
                implied_sum=round(yes_sum, 3),
                gap=round(gap, 3),
                direction=direction,
            ))

    if pairs:
        logger.info(f"Arbitrage detector: {len(pairs)} pair(s) found")
    return pairs
