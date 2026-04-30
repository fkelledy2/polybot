# data/polymarket.py
# ─────────────────────────────────────────────────────────────
# This module handles all communication with the Polymarket API.
# Think of it as the "eyes" of the bot — it fetches what markets
# exist, what prices are, and what the orderbook looks like.
# ─────────────────────────────────────────────────────────────

import json
import requests
import logging
from datetime import datetime, timezone
from typing import Optional

# Set up logging so we can see what the bot is doing
logger = logging.getLogger(__name__)


class PolymarketClient:
    """
    A wrapper around the Polymarket REST API.
    
    Usage:
        client = PolymarketClient()
        markets = client.get_active_markets(limit=50)
    """

    def __init__(self):
        self.base_url = "https://gamma-api.polymarket.com"
        # Requests session = reuses the same connection (faster)
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})

    def get_active_markets(self, limit: int = 100) -> list[dict]:
        """
        Fetch currently active prediction markets.
        
        Returns a list of market dicts. Each looks like:
        {
            "id": "some-id",
            "question": "Will the Fed cut rates in May 2026?",
            "outcomes": ["Yes", "No"],
            "outcomePrices": ["0.34", "0.66"],   ← price = probability (0 to 1)
            "volume": "45230.50",
            "endDate": "2026-05-15T00:00:00Z",
            ...
        }
        """
        try:
            response = self.session.get(
                f"{self.base_url}/markets",
                params={
                    "active": True,
                    "closed": False,
                    "limit": limit,
                    "order": "volume",      # Sort by most traded first
                    "ascending": False,
                }
            )
            response.raise_for_status()     # Raises an error if status != 200
            markets = response.json()
            logger.info(f"Fetched {len(markets)} active markets")
            return markets

        except requests.RequestException as e:
            logger.error(f"Failed to fetch markets: {e}")
            return []

    def get_market_by_id(self, market_id: str) -> Optional[dict]:
        """Fetch a single market by its ID."""
        try:
            response = self.session.get(f"{self.base_url}/markets/{market_id}")
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            logger.error(f"Failed to fetch market {market_id}: {e}")
            return None

    def get_orderbook(self, token_id: str) -> Optional[dict]:
        """
        Fetch the live orderbook for a specific market token.
        The orderbook shows all the buy and sell orders currently sitting
        in the market — useful for estimating how easy it is to get filled.
        """
        try:
            clob_url = "https://clob.polymarket.com"
            response = self.session.get(
                f"{clob_url}/book",
                params={"token_id": token_id}
            )
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            logger.error(f"Failed to fetch orderbook for {token_id}: {e}")
            return None

    def parse_market_price(self, market: dict) -> dict:
        """
        Extract the Yes/No prices from a market dict.
        
        Polymarket prices are probabilities: 0.34 means 34% chance.
        The Yes price + No price should always add up to ~1.00.
        
        Returns: {"yes": 0.34, "no": 0.66, "question": "..."}
        """
        try:
            prices_raw = market.get("outcomePrices", ["0.5", "0.5"])
            # API returns outcomePrices as a JSON-encoded string, e.g. '["0.34","0.66"]'
            if isinstance(prices_raw, str):
                prices = json.loads(prices_raw)
            else:
                prices = prices_raw

            end_date_str = market.get("endDate")
            days_to_resolve = None
            if end_date_str:
                try:
                    end_dt = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
                    now    = datetime.now(tz=timezone.utc)
                    days_to_resolve = (end_dt - now).total_seconds() / 86400
                except Exception:
                    pass

            description = (market.get("description") or "").strip()
            resolution_source = (market.get("resolutionSource") or "").strip()
            resolution_criteria = description[:400] if description else ""
            if resolution_source:
                suffix = f" (Source: {resolution_source})"
                resolution_criteria = (resolution_criteria + suffix).strip()

            # Created-at for new-market detection (S3-2)
            created_at_str = market.get("createdAt") or market.get("created_at")
            created_at = None
            if created_at_str:
                try:
                    created_at = datetime.fromisoformat(
                        created_at_str.replace("Z", "+00:00")
                    )
                except Exception:
                    pass
            hours_old = None
            if created_at:
                hours_old = (datetime.now(tz=timezone.utc) - created_at).total_seconds() / 3600

            # CLOB token ID for YES outcome (used by WebSocket feed)
            clob_ids_raw = market.get("clobTokenIds", "[]")
            clob_ids = []
            try:
                import json as _json
                clob_ids = _json.loads(clob_ids_raw) if isinstance(clob_ids_raw, str) else clob_ids_raw
            except Exception:
                pass

            return {
                "question":             market.get("question", "Unknown"),
                "market_id":            market.get("id"),
                "yes":                  float(prices[0]),
                "no":                   float(prices[1]),
                "volume_usd":           float(market.get("volume", 0)),
                "end_date":             end_date_str,
                "days_to_resolve":      round(days_to_resolve, 1) if days_to_resolve is not None else None,
                "resolution_criteria":  resolution_criteria,
                "hours_old":            round(hours_old, 1) if hours_old is not None else None,
                "clob_token_id_yes":    clob_ids[0] if clob_ids else None,
            }
        except (ValueError, IndexError, json.JSONDecodeError) as e:
            logger.warning(f"Could not parse prices for market: {e}")
            return {}

    def get_new_markets(self, min_volume: float = 5000,
                        max_age_hours: float = 48) -> list[dict]:
        """Fetch recently listed markets (structural alpha at formation)."""
        try:
            response = self.session.get(
                f"{self.base_url}/markets",
                params={
                    "active": True,
                    "closed": False,
                    "limit": 50,
                    "order": "created_at",
                    "ascending": False,
                }
            )
            response.raise_for_status()
            markets = response.json()
        except requests.RequestException as e:
            logger.error(f"Failed to fetch new markets: {e}")
            return []

        now = datetime.now(tz=timezone.utc)
        result = []
        for m in markets:
            if float(m.get("volume", 0)) < min_volume:
                continue
            created_raw = m.get("createdAt") or m.get("created_at")
            if not created_raw:
                continue
            try:
                created = datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
                age_hours = (now - created).total_seconds() / 3600
                if age_hours <= max_age_hours:
                    result.append(m)
            except Exception:
                pass

        logger.info(f"Found {len(result)} new markets (≤{max_age_hours}h old, >${min_volume:,.0f} vol)")
        return result

    def get_high_volume_markets(
        self,
        min_volume: float = 10000,
        limit: int = 50,
        max_days: float = None,
        min_days: float = None,
    ) -> list[dict]:
        """
        Get markets filtered by volume and optional resolution window.

        max_days: skip markets resolving more than this many days away
        min_days: skip markets resolving sooner than this (too late to trade)
        """
        all_markets = self.get_active_markets(limit=limit * 4)

        filtered = []
        skipped_vol = skipped_days = 0
        now = datetime.now(tz=timezone.utc)

        for m in all_markets:
            if float(m.get("volume", 0)) < min_volume:
                skipped_vol += 1
                continue

            if max_days is not None or min_days is not None:
                end_date_str = m.get("endDate")
                if end_date_str:
                    try:
                        end_dt = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
                        days   = (end_dt - now).total_seconds() / 86400
                        if max_days is not None and days > max_days:
                            skipped_days += 1
                            continue
                        if min_days is not None and days < min_days:
                            skipped_days += 1
                            continue
                    except Exception:
                        pass  # Keep market if we can't parse the date

            filtered.append(m)
            if len(filtered) >= limit:
                break

        logger.info(
            f"Found {len(filtered)} markets "
            f"(skipped {skipped_vol} low-volume, {skipped_days} outside resolution window)"
        )
        return filtered
