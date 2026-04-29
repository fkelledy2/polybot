# data/enrichment/sports.py
# ─────────────────────────────────────────────────────────────
# Sports enrichment via The Odds API.
# Free tier: 500 requests/month — enough for dozens of scans/day.
#
# Register for a free key at: https://the-odds-api.com
# Add to .env: ODDS_API_KEY=your-key-here
#
# Phase 2 — stub in place, activate by setting ODDS_API_KEY.
# ─────────────────────────────────────────────────────────────

import logging
import os
import re

import requests

from .cache import _cache

logger = logging.getLogger(__name__)

ODDS_API_KEY  = os.getenv("ODDS_API_KEY", "")
ODDS_BASE_URL = "https://api.the-odds-api.com/v4/sports"

# Polymarket sport name → Odds API sport key
SPORT_MAP = {
    "nfl":   "americanfootball_nfl",
    "nba":   "basketball_nba",
    "mlb":   "baseball_mlb",
    "nhl":   "icehockey_nhl",
    "epl":   "soccer_epl",
    "ucl":   "soccer_uefa_champs_league",
    "ufc":   "mma_mixed_martial_arts",
}


def _detect_sport(question: str) -> str | None:
    q = question.lower()
    for keyword, sport_key in SPORT_MAP.items():
        if re.search(rf"\b{keyword}\b", q):
            return sport_key
    return None


def _fetch_odds(sport_key: str, session: requests.Session) -> list[dict]:
    if not ODDS_API_KEY:
        return []

    cache_key = f"odds_{sport_key}"
    cached = _cache.get(cache_key)
    if cached is not None:
        return cached

    try:
        r = session.get(
            f"{ODDS_BASE_URL}/{sport_key}/odds/",
            params={
                "apiKey":  ODDS_API_KEY,
                "regions": "us,uk",
                "markets": "h2h",
                "oddsFormat": "decimal",
            },
            timeout=10,
        )
        if r.status_code == 200:
            data = r.json()
            _cache.set(cache_key, data, ttl=900)  # 15 min
            return data
        logger.debug(f"Odds API returned {r.status_code} for {sport_key}")
    except Exception as e:
        logger.debug(f"Odds API fetch failed: {e}")
    return []


def _match_event(events: list[dict], question: str) -> dict | None:
    """Find the event that best matches the market question by team name."""
    q = question.lower()
    for event in events:
        home = event.get("home_team", "").lower()
        away = event.get("away_team", "").lower()
        if home in q or away in q or (len(home) > 4 and home[:4] in q):
            return event
    return None


def _implied_prob(decimal_odds: float) -> float:
    """Convert decimal odds to implied probability."""
    return round(1 / decimal_odds, 4) if decimal_odds > 0 else 0.0


def get_context(question: str, session: requests.Session) -> str:
    """
    Return betting line context for a sports market question.
    Example: "Bookmaker consensus: Lakers 58% | Celtics 42% (h2h avg across 8 books)"
    Returns "" if no key set, sport not detected, or event not found.
    """
    if not ODDS_API_KEY:
        return ""

    sport_key = _detect_sport(question)
    if not sport_key:
        return ""

    events = _fetch_odds(sport_key, session)
    event  = _match_event(events, question)
    if not event:
        return ""

    bookmakers = event.get("bookmakers", [])
    if not bookmakers:
        return ""

    # Average implied probabilities across bookmakers
    home_probs, away_probs = [], []
    for bm in bookmakers:
        for market in bm.get("markets", []):
            if market.get("key") != "h2h":
                continue
            outcomes = {o["name"]: o["price"] for o in market.get("outcomes", [])}
            home = event["home_team"]
            away = event["away_team"]
            if home in outcomes and away in outcomes:
                home_probs.append(_implied_prob(outcomes[home]))
                away_probs.append(_implied_prob(outcomes[away]))

    if not home_probs:
        return ""

    home_avg = sum(home_probs) / len(home_probs)
    away_avg = sum(away_probs) / len(away_probs)
    n        = len(home_probs)

    return (
        f"Bookmaker consensus ({n} books): "
        f"{event['home_team']} {home_avg:.0%} | "
        f"{event['away_team']} {away_avg:.0%}"
    )
