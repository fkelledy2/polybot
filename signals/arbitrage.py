# signals/arbitrage.py
# ─────────────────────────────────────────────────────────────
# Related market pair detection (S3-3).
# Finds complementary markets whose YES prices don't sum to ~1.0,
# indicating a model-free mispricing opportunity.
#
# IMPORTANT GUARD: The binary-sum heuristic (YES_a + YES_b ≠ 1.0)
# only signals mispricing in MUTUALLY EXCLUSIVE binary markets.
# It is INVALID for multi-entrant tournament markets (Eurovision
# semi-finals, award shows, league fixtures) where both outcomes
# can simultaneously resolve YES. Such pairs are excluded by
# is_tournament_market() to prevent spurious arbitrage signals.
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

# Tournament / multi-entrant contest patterns — the sum-to-1.0
# assumption does NOT hold because multiple entrants can advance.
_TOURNAMENT_PATTERNS = [
    r"\beurovision\b",
    r"\bsemi.?final\b",
    r"\badvance\b.{0,40}\b(semi|quarter|round|through)\b",
    r"\b(qualify|qualif\w+)\b",
    r"\bgroup\s+stage\b",
    r"\bworld\s+cup\b.{0,30}\b(advance|qualify|group)\b",
    r"\bchampions\s+league\b.{0,30}\b(advance|qualify|group|knockout)\b",
    r"\baward\b.{0,30}\bwinner\b",   # award show categories: multiple nominees can win categories
    r"\bnominated\b",
    r"\bbracket\b",
    r"\bplayoff\b.{0,30}\b(advance|qualify)\b",
    r"\bqualif\w+\s+(for|to)\b",
]

_TOURNAMENT_RE = re.compile("|".join(_TOURNAMENT_PATTERNS), re.IGNORECASE)


def is_tournament_market(question: str) -> bool:
    """
    Returns True if the question describes a multi-entrant tournament or contest
    where the binary YES/NO sum heuristic is invalid.

    In these markets, two related questions (e.g. "Will A advance?" and
    "Will B advance?") can BOTH resolve YES, so their YES prices need not
    sum to 1.0 — the apparent mispricing is structural, not exploitable.
    """
    return bool(_TOURNAMENT_RE.search(question))


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

    Tournament / multi-entrant markets are excluded: the binary-sum heuristic
    is only valid for mutually exclusive binary outcomes. See is_tournament_market().
    """
    pairs = []
    n = len(markets)

    for i in range(n):
        q_i = markets[i].get("question", "")
        if is_tournament_market(q_i):
            continue
        kw_i = _keywords(q_i)

        for j in range(i + 1, n):
            q_j = markets[j].get("question", "")
            if is_tournament_market(q_j):
                continue
            kw_j = _keywords(q_j)

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
