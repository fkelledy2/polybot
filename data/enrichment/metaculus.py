# data/enrichment/metaculus.py
# ─────────────────────────────────────────────────────────────
# Queries the public Metaculus API (no key required) to find
# expert forecaster consensus for a market question.
#
# Strategy: search Metaculus by keyword, return the community
# median probability for the best-matching open question.
# Divergence from Polymarket price = additional signal.
# ─────────────────────────────────────────────────────────────

import re
import logging
import requests

logger = logging.getLogger(__name__)

METACULUS_API = "https://www.metaculus.com/api2/questions/"

# Words too generic to be useful search terms
_STOP_WORDS = frozenset({
    "will", "does", "is", "are", "has", "have", "did", "do", "be", "been",
    "the", "a", "an", "by", "in", "on", "at", "to", "of", "for", "from",
    "with", "about", "and", "or", "not", "this", "that", "which", "who",
    "when", "what", "how", "if", "its", "it", "than", "then", "before",
    "after", "between", "during", "2024", "2025", "2026",
})


def _keywords(question: str) -> str:
    """Extract the most meaningful terms from a market question."""
    words = re.sub(r"[^\w\s]", " ", question).split()
    significant = [w for w in words if w.lower() not in _STOP_WORDS and len(w) > 2]
    return " ".join(significant[:6])


def get_context(question: str, session: requests.Session) -> str:
    """
    Search Metaculus for a question matching the Polymarket question.
    Returns a compact string like "Metaculus: 68% YES ('Best matching title…')"
    or "" if no useful match is found.
    """
    keywords = _keywords(question)
    if not keywords:
        return ""

    try:
        resp = session.get(
            METACULUS_API,
            params={
                "search": keywords,
                "status": "open",
                "type": "forecast",
                "per_page": 3,
                "order_by": "-activity",
            },
            timeout=8,
        )
        resp.raise_for_status()
        results = resp.json().get("results", [])

        if not results:
            return ""

        # Take the top result with a valid community prediction
        for match in results:
            cp = (match.get("community_prediction") or {}).get("full") or {}
            median = cp.get("q2")
            if median is None:
                continue

            title = (match.get("title") or "")[:55]
            return f"Metaculus consensus: {float(median):.0%} YES ('{title}…')"

        return ""

    except Exception as e:
        logger.debug(f"Metaculus lookup failed for '{question[:40]}': {e}")
        return ""
