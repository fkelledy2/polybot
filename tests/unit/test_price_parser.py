# tests/unit/test_price_parser.py
# Tests for parse_market_price — the critical price ingestion function.
# Bugs here cause wrong probabilities → wrong trades → money lost.

import pytest
from unittest.mock import MagicMock, patch
from data.polymarket import PolymarketClient


@pytest.fixture()
def client():
    with patch("data.polymarket.requests.Session"):
        return PolymarketClient()


class TestParseMarketPrice:
    def test_json_string_prices(self, client):
        """outcomePrices arrives as a JSON-encoded string from the API."""
        market = {
            "id": "m1",
            "question": "Test?",
            "outcomePrices": '["0.60","0.40"]',
            "volume": "50000",
            "endDate": "2026-12-31T00:00:00Z",
        }
        result = client.parse_market_price(market)
        assert result["yes"] == pytest.approx(0.60)
        assert result["no"]  == pytest.approx(0.40)

    def test_list_prices(self, client):
        """outcomePrices can also arrive as a Python list."""
        market = {
            "id": "m2",
            "question": "Test?",
            "outcomePrices": ["0.35", "0.65"],
            "volume": "10000",
            "endDate": "2026-06-01T00:00:00Z",
        }
        result = client.parse_market_price(market)
        assert result["yes"] == pytest.approx(0.35)
        assert result["no"]  == pytest.approx(0.65)

    def test_yes_no_sum_to_one(self, client):
        market = {
            "id": "m3",
            "question": "Test?",
            "outcomePrices": '["0.73","0.27"]',
            "volume": "1000",
            "endDate": "2026-09-01T00:00:00Z",
        }
        result = client.parse_market_price(market)
        assert result["yes"] + result["no"] == pytest.approx(1.0)

    def test_missing_outcome_prices_uses_default(self, client):
        """Missing outcomePrices should not crash — defaults to 50/50."""
        market = {"id": "m4", "question": "Test?", "volume": "1000", "endDate": "2026-09-01T00:00:00Z"}
        result = client.parse_market_price(market)
        assert result["yes"] == pytest.approx(0.5)
        assert result["no"]  == pytest.approx(0.5)

    def test_malformed_prices_returns_empty(self, client):
        """Completely broken prices should return {} not crash."""
        market = {
            "id": "m5",
            "question": "Test?",
            "outcomePrices": "not-valid-json",
            "volume": "1000",
        }
        result = client.parse_market_price(market)
        assert result == {}

    def test_days_to_resolve_calculated(self, client):
        from freezegun import freeze_time
        with freeze_time("2026-04-01T00:00:00Z"):
            market = {
                "id": "m6",
                "question": "Test?",
                "outcomePrices": '["0.5","0.5"]',
                "volume": "1000",
                "endDate": "2026-04-08T00:00:00Z",
            }
            result = client.parse_market_price(market)
            assert result["days_to_resolve"] == pytest.approx(7.0, abs=0.1)

    def test_missing_end_date_gives_none(self, client):
        market = {
            "id": "m7",
            "question": "Test?",
            "outcomePrices": '["0.5","0.5"]',
            "volume": "1000",
        }
        result = client.parse_market_price(market)
        assert result["days_to_resolve"] is None

    def test_volume_parsed_as_float(self, client):
        market = {
            "id": "m8",
            "question": "Test?",
            "outcomePrices": '["0.5","0.5"]',
            "volume": "123456.78",
            "endDate": "2026-12-01T00:00:00Z",
        }
        result = client.parse_market_price(market)
        assert result["volume_usd"] == pytest.approx(123456.78)

    def test_market_id_preserved(self, client):
        market = {
            "id": "unique-id-xyz",
            "question": "Test?",
            "outcomePrices": '["0.5","0.5"]',
            "volume": "1000",
            "endDate": "2026-12-01T00:00:00Z",
        }
        result = client.parse_market_price(market)
        assert result["market_id"] == "unique-id-xyz"

    def test_extreme_prices_near_zero(self, client):
        """Prices near 0 and 1 (resolved-ish markets) should parse fine."""
        market = {
            "id": "m9",
            "question": "Test?",
            "outcomePrices": '["0.99","0.01"]',
            "volume": "1000",
            "endDate": "2026-12-01T00:00:00Z",
        }
        result = client.parse_market_price(market)
        assert result["yes"] == pytest.approx(0.99)


class TestVolumeFiltering:
    def test_filters_low_volume(self, client):
        markets = [
            {"id": str(i), "volume": str(v), "outcomePrices": '["0.5","0.5"]',
             "endDate": "2026-12-01T00:00:00Z", "question": f"Q{i}"}
            for i, v in enumerate([5000, 50000, 200000, 1000, 15000])
        ]
        with patch.object(client, "get_active_markets", return_value=markets):
            result = client.get_high_volume_markets(min_volume=10_000, limit=10)
        assert len(result) == 3
        for m in result:
            assert float(m["volume"]) >= 10_000

    def test_max_days_filter(self, client):
        from freezegun import freeze_time
        with freeze_time("2026-04-01T00:00:00Z"):
            markets = [
                {"id": "short", "volume": "50000", "outcomePrices": '["0.5","0.5"]',
                 "endDate": "2026-04-05T00:00:00Z", "question": "Short"},
                {"id": "long",  "volume": "50000", "outcomePrices": '["0.5","0.5"]',
                 "endDate": "2026-06-01T00:00:00Z", "question": "Long"},
            ]
            with patch.object(client, "get_active_markets", return_value=markets):
                result = client.get_high_volume_markets(min_volume=1000, limit=10, max_days=14)
        assert len(result) == 1
        assert result[0]["id"] == "short"

    def test_min_days_filter(self, client):
        from freezegun import freeze_time
        with freeze_time("2026-04-01T00:00:00Z"):
            markets = [
                {"id": "today",  "volume": "50000", "outcomePrices": '["0.5","0.5"]',
                 "endDate": "2026-04-01T12:00:00Z", "question": "Today"},
                {"id": "future", "volume": "50000", "outcomePrices": '["0.5","0.5"]',
                 "endDate": "2026-04-10T00:00:00Z", "question": "Future"},
            ]
            with patch.object(client, "get_active_markets", return_value=markets):
                result = client.get_high_volume_markets(min_volume=1000, limit=10, min_days=1)
        assert len(result) == 1
        assert result[0]["id"] == "future"
