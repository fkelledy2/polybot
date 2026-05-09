# data/enrichment/manifold.py
# ─────────────────────────────────────────────────────────────
# Queries the public Manifold Markets API (no key required) to find
# calibrated crowd forecasts from the expert prediction community.
#
# Strategy: search by keyword, return the best-matching market's
# probability and flag divergence from the Polymarket price.
# ─────────────────────────────────────────────────────────────

import re
import logging
import requests

logger = logging.getLogger(__name__)

MANIFOLD_API = "https://api.manifold.markets/v0/search-markets"

_STOP_WORDS = frozenset({
    "will", "does", "is", "are", "has", "have", "did", "do", "be", "been",
    "the", "a", "an", "by", "in", "on", "at", "to", "of", "for", "from",
    "with", "about", "and", "or", "not", "this", "that", "which", "who",
    "when", "what", "how", "if", "its", "it", "than", "then", "before",
    "after", "between", "during", "2024", "2025", "2026",
})


def _keywords(question: str) -> str:
    words = re.sub(r"[^\w\s]", " ", question).split()
    significant = [w for w in words if w.lower() not in _STOP_WORDS and len(w) > 2]
    return " ".join(significant[:6])


def get_context(question: str, polymarket_yes_price: float, session: requests.Session) -> str:
    """
    Search Manifold for a binary market matching the question.
    Returns e.g. "Manifold consensus: 72% YES ('Title…') [DIVERGENCE +14%]"
    or "" if no useful match is found.
    """
    keywords = _keywords(question)
    if not keywords:
        return ""

    try:
        resp = session.get(
            MANIFOLD_API,
            params={
                "term": keywords,
                "filter": "open",
                "sort": "score",
                "contractType": "BINARY",
                "limit": 3,
            },
            timeout=8,
        )
        resp.raise_for_status()
        results = resp.json()

        if not results:
            return ""

        for match in results:
            probability = match.get("probability")
            if probability is None:
                continue

            title = (match.get("question") or "")[:55]
            manifold_pct = float(probability) * 100
            divergence = manifold_pct - (polymarket_yes_price * 100)

            div_str = f" [DIVERGENCE {divergence:+.0f}%]" if abs(divergence) >= 5 else ""
            return f"Manifold consensus: {manifold_pct:.0f}% YES ('{title}…'){div_str}"

        return ""

    except Exception as e:
        logger.debug(f"Manifold lookup failed for '{question[:40]}': {e}")
        return ""
