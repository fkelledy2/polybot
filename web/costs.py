# web/costs.py
# ─────────────────────────────────────────────────────────────
# Real API usage from the api_usage DB table.
# ─────────────────────────────────────────────────────────────

import logging
import os

logger = logging.getLogger(__name__)


def get_service_status() -> list[dict]:
    """Enabled/disabled status of all external services."""
    return [
        {
            "id": "anthropic",
            "name": "Anthropic API (Claude)",
            "enabled": bool(os.environ.get("ANTHROPIC_API_KEY")),
            "description": "Prediction market analysis with Claude Haiku",
        },
        {
            "id": "brave_search",
            "name": "Brave Search API",
            "enabled": bool(os.environ.get("BRAVE_SEARCH_API_KEY")),
            "description": "Web search for market research",
        },
        {
            "id": "odds_api",
            "name": "Odds API",
            "enabled": bool(os.environ.get("ODDS_API_KEY")),
            "description": "Sports odds data",
        },
        {
            "id": "heroku",
            "name": "Heroku Dyno",
            "enabled": bool(os.environ.get("DYNO")),
            "description": "Web dashboard hosting",
        },
    ]


def get_all_costs_summary() -> dict:
    """Return real cost data from the api_usage table for 7 and 30-day windows."""
    try:
        from web.usage import get_costs_since
        weekly  = get_costs_since(7)
        monthly = get_costs_since(30)
    except Exception as e:
        logger.warning(f"Could not load usage data: {e}")
        weekly  = {"services": {}, "total": 0.0, "days": 7}
        monthly = {"services": {}, "total": 0.0, "days": 30}

    return {
        "services": get_service_status(),
        "weekly":   weekly,
        "monthly":  monthly,
        "pricing_notes": {
            "anthropic": "Haiku $0.80/MTok in · $4.00/MTok out · $0.08/MTok cache-read",
            "calculation": "Actual token counts recorded per API call",
            "heroku": "Eco Dyno $7/month prorated",
        },
    }
