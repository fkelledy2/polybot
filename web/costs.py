# web/costs.py
# ─────────────────────────────────────────────────────────────
# Track and calculate running costs of all SaaS services
# ─────────────────────────────────────────────────────────────

import logging
from datetime import datetime, timedelta
import json
import os

logger = logging.getLogger(__name__)

# Pricing data (as of 2024)
PRICING = {
    "anthropic": {
        "name": "Anthropic API (Claude)",
        "unit": "per 1M input tokens",
        "cost_per_unit": 0.80,
        "description": "Prediction market analysis with Claude Haiku",
    },
    "brave_search": {
        "name": "Brave Search API",
        "unit": "per 1000 queries",
        "cost_per_unit": 1.00,
        "description": "Web search for market research (optional)",
    },
    "odds_api": {
        "name": "Odds API",
        "unit": "per 1000 requests",
        "cost_per_unit": 4.99,
        "description": "Sports odds data (optional)",
    },
    "heroku": {
        "name": "Heroku Dyno",
        "unit": "monthly",
        "cost_per_unit": 7.00,
        "description": "Web dashboard hosting",
    },
}


def get_service_status() -> list[dict]:
    """Get enabled/disabled status of all services."""
    services = []

    # Anthropic
    services.append({
        "id": "anthropic",
        "name": PRICING["anthropic"]["name"],
        "enabled": bool(os.environ.get("ANTHROPIC_API_KEY")),
        "description": PRICING["anthropic"]["description"],
    })

    # Brave Search
    services.append({
        "id": "brave_search",
        "name": PRICING["brave_search"]["name"],
        "enabled": bool(os.environ.get("BRAVE_SEARCH_API_KEY")),
        "description": PRICING["brave_search"]["description"],
    })

    # Odds API
    services.append({
        "id": "odds_api",
        "name": PRICING["odds_api"]["name"],
        "enabled": bool(os.environ.get("ODDS_API_KEY")),
        "description": PRICING["odds_api"]["description"],
    })

    # Heroku (always enabled if running on Heroku)
    services.append({
        "id": "heroku",
        "name": PRICING["heroku"]["name"],
        "enabled": bool(os.environ.get("DYNO")),
        "description": PRICING["heroku"]["description"],
    })

    return services


def calculate_anthropic_costs(start_date: datetime = None) -> dict:
    """Estimate Anthropic API costs from trade count."""
    import db

    if start_date is None:
        start_date = datetime.now() - timedelta(days=30)

    try:
        conn = db.get_connection()
        c = db.get_cursor(conn)

        # Count trades created since start_date to estimate API usage
        # Each trade scan analyzes ~20 markets, ~1000 tokens per market
        c.execute(
            """
            SELECT COUNT(*) as trade_count FROM trades
            WHERE timestamp >= ?
            """,
            (start_date.isoformat(),),
        )
        result = c.fetchone()
        trade_count = result["trade_count"] if result else 0
        conn.close()

        # Rough estimate: ~5 scans per trade execution, ~20 markets per scan, ~1000 tokens per market
        estimated_tokens = max(trade_count * 100000, 1000000)  # At least 1M tokens baseline

        cost = (estimated_tokens / 1_000_000) * 0.80  # Haiku pricing: $0.80/MTok
        return {
            "service": "anthropic",
            "estimated_tokens": estimated_tokens,
            "estimated_trades": trade_count,
            "cost": round(cost, 4),
        }
    except Exception as e:
        logger.debug(f"Could not calculate Anthropic costs: {e}")
        return {
            "service": "anthropic",
            "estimated_tokens": 0,
            "estimated_trades": 0,
            "cost": 0,
        }


def estimate_weekly_costs() -> dict:
    """Estimate costs for the past 7 days."""
    now = datetime.now()
    week_ago = now - timedelta(days=7)

    costs = {
        "period": "weekly",
        "start_date": week_ago.isoformat(),
        "end_date": now.isoformat(),
        "services": {},
        "total": 0.0,
    }

    # Anthropic - enabled service
    if os.environ.get("ANTHROPIC_API_KEY"):
        anthropic_costs = calculate_anthropic_costs(week_ago)
        # Ensure minimum baseline if service is enabled
        if anthropic_costs["cost"] < 0.20:
            anthropic_costs["cost"] = round(0.20, 4)  # Minimum $0.20/week
        costs["services"]["anthropic"] = anthropic_costs
        costs["total"] += anthropic_costs["cost"]

    # Brave Search - optional service
    if os.environ.get("BRAVE_SEARCH_API_KEY"):
        costs["services"]["brave_search"] = {
            "service": "brave_search",
            "cost": round(0.10, 4),  # Baseline estimate if enabled
        }
        costs["total"] += costs["services"]["brave_search"]["cost"]

    # Heroku (prorated: $7/month ≈ $1.62/week)
    costs["services"]["heroku"] = {
        "service": "heroku",
        "cost": round(7.0 / 4.29, 4),  # rough weekly estimate
    }
    costs["total"] += costs["services"]["heroku"]["cost"]

    return costs


def estimate_monthly_costs() -> dict:
    """Estimate costs for the past 30 days."""
    now = datetime.now()
    month_ago = now - timedelta(days=30)

    costs = {
        "period": "monthly",
        "start_date": month_ago.isoformat(),
        "end_date": now.isoformat(),
        "services": {},
        "total": 0.0,
    }

    # Anthropic - enabled service
    if os.environ.get("ANTHROPIC_API_KEY"):
        anthropic_costs = calculate_anthropic_costs(month_ago)
        # Ensure minimum baseline if service is enabled
        if anthropic_costs["cost"] < 1.0:
            anthropic_costs["cost"] = round(1.0, 4)  # Minimum $1.00/month
        costs["services"]["anthropic"] = anthropic_costs
        costs["total"] += anthropic_costs["cost"]

    # Brave Search - optional service
    if os.environ.get("BRAVE_SEARCH_API_KEY"):
        costs["services"]["brave_search"] = {
            "service": "brave_search",
            "cost": round(1.00, 4),  # Baseline estimate if enabled
        }
        costs["total"] += costs["services"]["brave_search"]["cost"]

    # Heroku
    costs["services"]["heroku"] = {
        "service": "heroku",
        "cost": 7.0,  # $7/month
    }
    costs["total"] += costs["services"]["heroku"]["cost"]

    return costs


def get_all_costs_summary() -> dict:
    """Get comprehensive cost summary: services, weekly, monthly, to-date."""
    return {
        "services": get_service_status(),
        "weekly": estimate_weekly_costs(),
        "monthly": estimate_monthly_costs(),
        "pricing_notes": {
            "anthropic": "Using Claude Haiku (cheapest model)",
            "calculation": "Token usage estimated from signal analysis count",
            "heroku": "Eco Dyno pricing",
        },
    }
