# tests/conftest.py
# ─────────────────────────────────────────────────────────────
# Shared fixtures used across all test modules.
# ─────────────────────────────────────────────────────────────

import os
import sqlite3

import pytest

# ── Ensure a dummy API key is set before any module imports config ──
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-dummy")


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    """
    Create a fresh temporary SQLite DB for each test.
    Patches db._TRADES_DB — the single read point used by get_connection().
    """
    db_file = str(tmp_path / "test_trades.db")
    monkeypatch.setenv("TRADES_DB", db_file)

    import db as db_mod
    monkeypatch.setattr(db_mod, "_TRADES_DB", db_file)
    monkeypatch.setattr(db_mod, "IS_POSTGRES", False)
    monkeypatch.setattr(db_mod, "placeholder", "?")

    return db_file


@pytest.fixture()
def paper_trader(tmp_db):
    """Return a fresh PaperTrader connected to the temp DB."""
    from execution.paper_trader import PaperTrader
    return PaperTrader()


@pytest.fixture()
def minimal_signal():
    """Return a minimal TradeSignal-like object for testing."""
    from signals.claude_signal import TradeSignal
    return TradeSignal(
        market_id="abc123",
        question="Will X happen?",
        market_yes_price=0.40,
        claude_yes_probability=0.60,
        edge=0.20,
        direction="YES",
        confidence="medium",
        reasoning="Strong evidence",
        wallet_alignment=False,
        should_trade=True,
    )


@pytest.fixture()
def no_signal():
    """A NO-direction trade signal."""
    from signals.claude_signal import TradeSignal
    return TradeSignal(
        market_id="def456",
        question="Will Y happen?",
        market_yes_price=0.70,
        claude_yes_probability=0.45,
        edge=-0.25,
        direction="NO",
        confidence="high",
        reasoning="Overpriced",
        wallet_alignment=True,
        should_trade=True,
    )
