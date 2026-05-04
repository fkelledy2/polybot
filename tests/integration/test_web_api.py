# tests/integration/test_web_api.py
# Flask route tests using the test client — no real server started.

import json
import pytest
from unittest.mock import patch


@pytest.fixture()
def flask_client(tmp_db):
    """Return an authenticated Flask test client wired to the temp DB."""
    from web.app import app
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test-secret"
    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["authenticated"] = True
        yield client


@pytest.fixture(autouse=True)
def init_db(tmp_db):
    """Ensure DB tables exist before web tests run."""
    from execution.paper_trader import PaperTrader
    from backtest.tracker import init_tracker
    PaperTrader()  # creates tables
    init_tracker()


class TestStatsRoute:
    def test_returns_200(self, flask_client):
        r = flask_client.get("/api/stats")
        assert r.status_code == 200

    def test_returns_json(self, flask_client):
        r = flask_client.get("/api/stats")
        data = json.loads(r.data)
        assert isinstance(data, dict)

    def test_expected_keys_present(self, flask_client):
        r = flask_client.get("/api/stats")
        data = json.loads(r.data)
        for key in ("balance", "portfolio_value", "win_rate", "won_count",
                    "lost_count", "open_count", "total_pnl", "starting_balance"):
            assert key in data, f"Missing key: {key}"

    def test_starting_balance_correct(self, flask_client):
        from config import STARTING_BALANCE
        r = flask_client.get("/api/stats")
        data = json.loads(r.data)
        assert data["starting_balance"] == STARTING_BALANCE


class TestSignalsRoute:
    def test_returns_200(self, flask_client):
        r = flask_client.get("/api/signals")
        assert r.status_code == 200

    def test_returns_list(self, flask_client):
        r = flask_client.get("/api/signals")
        data = json.loads(r.data)
        assert isinstance(data, list)

    def test_signals_sorted_by_edge_desc(self, flask_client):
        """Signals should arrive sorted by absolute edge descending."""
        from web.app import update_signals
        from signals.claude_signal import TradeSignal

        sigs = [
            TradeSignal("m1", "Q1?", 0.5, 0.65, 0.15, "YES", "medium", "r", False, True),
            TradeSignal("m2", "Q2?", 0.5, 0.80, 0.30, "YES", "high",   "r", False, True),
            TradeSignal("m3", "Q3?", 0.5, 0.55, 0.05, "YES", "low",    "r", False, False),
        ]
        update_signals(sigs, [], 0)

        r = flask_client.get("/api/signals")
        data = json.loads(r.data)
        if len(data) >= 2:
            edges = [abs(d["edge"]) for d in data]
            assert edges == sorted(edges, reverse=True)


class TestTradesRoute:
    def test_returns_200(self, flask_client):
        r = flask_client.get("/api/trades")
        assert r.status_code == 200

    def test_empty_when_no_trades(self, flask_client):
        r = flask_client.get("/api/trades")
        data = json.loads(r.data)
        assert data == []

    def test_trade_appears_after_placement(self, flask_client, tmp_db, minimal_signal):
        from execution.paper_trader import PaperTrader
        pt = PaperTrader()
        pt.place_trade(minimal_signal)

        r = flask_client.get("/api/trades")
        data = json.loads(r.data)
        assert len(data) == 1
        assert data[0]["market_id"] == minimal_signal.market_id
        assert data[0]["status"] == "open"

    def test_closed_trade_shows_pnl(self, flask_client, tmp_db, minimal_signal):
        from execution.paper_trader import PaperTrader
        pt = PaperTrader()
        pt.place_trade(minimal_signal)
        pt.close_trade(minimal_signal.market_id, resolved_yes=True)

        r = flask_client.get("/api/trades")
        data = json.loads(r.data)
        assert data[0]["status"] == "won"
        assert data[0]["pnl"] > 0


class TestTimelineRoute:
    def test_returns_200(self, flask_client):
        r = flask_client.get("/api/trade-timeline")
        assert r.status_code == 200

    def test_returns_list(self, flask_client):
        r = flask_client.get("/api/trade-timeline")
        data = json.loads(r.data)
        assert isinstance(data, list)

    def test_closed_at_populated_after_resolution(self, flask_client, tmp_db, minimal_signal):
        from execution.paper_trader import PaperTrader
        pt = PaperTrader()
        pt.place_trade(minimal_signal)
        pt.close_trade(minimal_signal.market_id, resolved_yes=True)

        r = flask_client.get("/api/trade-timeline")
        data = json.loads(r.data)
        assert len(data) == 1
        assert data[0]["closed_at"] is not None

    def test_open_trade_has_null_closed_at(self, flask_client, tmp_db, minimal_signal):
        from execution.paper_trader import PaperTrader
        pt = PaperTrader()
        pt.place_trade(minimal_signal)

        r = flask_client.get("/api/trade-timeline")
        data = json.loads(r.data)
        assert data[0]["closed_at"] is None


class TestPositionsRoute:
    def test_returns_200(self, flask_client):
        r = flask_client.get("/api/positions")
        assert r.status_code == 200

    def test_open_position_appears(self, flask_client, tmp_db, minimal_signal):
        from execution.paper_trader import PaperTrader
        pt = PaperTrader()
        pt.place_trade(minimal_signal)

        r = flask_client.get("/api/positions")
        data = json.loads(r.data)
        assert len(data) == 1

    def test_closed_position_not_in_positions(self, flask_client, tmp_db, minimal_signal):
        from execution.paper_trader import PaperTrader
        pt = PaperTrader()
        pt.place_trade(minimal_signal)
        pt.close_trade(minimal_signal.market_id, resolved_yes=True)

        r = flask_client.get("/api/positions")
        data = json.loads(r.data)
        assert len(data) == 0


class TestPnlHistoryRoute:
    def test_returns_200(self, flask_client):
        r = flask_client.get("/api/pnl-history")
        assert r.status_code == 200

    def test_empty_when_no_closed_trades(self, flask_client):
        """PnL history is empty with no closed trades — correct for cumulative approach."""
        r = flask_client.get("/api/pnl-history")
        data = json.loads(r.data)
        assert data == []

    def test_contains_entry_after_closed_trade(self, flask_client, tmp_db, minimal_signal):
        from execution.paper_trader import PaperTrader
        pt = PaperTrader()
        pt.place_trade(minimal_signal)
        pt.close_trade(minimal_signal.market_id, resolved_yes=True)

        r = flask_client.get("/api/pnl-history")
        data = json.loads(r.data)
        assert len(data) >= 1
        assert "t" in data[0]
        assert "b" in data[0]

    def test_pnl_history_cumulative_after_win(self, flask_client, tmp_db, minimal_signal):
        from execution.paper_trader import PaperTrader
        from config import STARTING_BALANCE
        pt = PaperTrader()
        trade = pt.place_trade(minimal_signal)
        pt.close_trade(minimal_signal.market_id, resolved_yes=True)

        r = flask_client.get("/api/pnl-history")
        data = json.loads(r.data)
        assert len(data) == 1
        # After a win, cumulative balance should be above starting balance
        assert data[0]["b"] > STARTING_BALANCE
