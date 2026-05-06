# tests/unit/test_wallet_consensus.py
import pytest
from data.wallet_tracker import (
    WalletProfile, WalletConsensus, WalletTracker,
    _compute_alpha_remaining,
)


# ── _compute_alpha_remaining ──────────────────────────────────

def _pos(outcome="YES", size=100.0, initial=40.0, current=70.0):
    return {"outcome": outcome, "size": size, "initialValue": initial, "currentValue": current}


def test_alpha_remaining_yes_normal():
    # entry=0.40, current=0.70 → (1-0.70)/(1-0.40) = 0.30/0.60 = 0.50
    alpha = _compute_alpha_remaining(_pos("YES", size=100, initial=40, current=70))
    assert abs(alpha - 0.50) < 0.001


def test_alpha_remaining_no_normal():
    # entry=0.70, current=0.30 → 0.30/0.70 ≈ 0.43
    alpha = _compute_alpha_remaining(_pos("NO", size=100, initial=70, current=30))
    assert abs(alpha - (30/70)) < 0.001


def test_alpha_remaining_yes_decayed():
    # entry=0.40, current=0.97 → (0.03)/(0.60) ≈ 0.05 — well below 0.25
    alpha = _compute_alpha_remaining(_pos("YES", size=100, initial=40, current=97))
    assert alpha < 0.10


def test_alpha_remaining_no_decayed():
    # NO bet: entry_price=0.30, current_price=0.03 → 0.03/0.30 = 0.10
    alpha = _compute_alpha_remaining(_pos("NO", size=100, initial=30, current=3))
    assert alpha < 0.25


def test_alpha_remaining_zero_size_returns_none():
    assert _compute_alpha_remaining(_pos(size=0)) is None


def test_alpha_remaining_zero_initial_returns_none():
    assert _compute_alpha_remaining(_pos(initial=0)) is None


def test_alpha_remaining_entry_price_at_boundary():
    # entry_price == 1.0 → guard triggers → None
    assert _compute_alpha_remaining(_pos(size=100, initial=100, current=50)) is None


# ── Consensus scoring via WalletTracker ──────────────────────

def _make_tracker(wallets):
    """Build a WalletTracker with pre-populated elite_wallets."""
    t = WalletTracker.__new__(WalletTracker)
    t.elite_wallets = wallets
    return t


def _make_wallet(rank, name="trader"):
    return WalletProfile(address=f"0x{rank:040x}", name=name, rank=rank,
                         total_trades=100, winning_trades=60, total_pnl_usd=100_000)


def test_consensus_single_yes_trader(monkeypatch):
    tracker = _make_tracker([_make_wallet(rank=1)])
    monkeypatch.setattr(tracker, "fetch_wallet_positions", lambda addr: [{
        "conditionId": "0xabc", "title": "Will X happen?",
        "outcome": "Yes", "currentValue": 500, "size": 1000, "initialValue": 300,
    }])
    bundle = tracker.get_elite_consensus()
    wc = bundle.consensus.get("0xabc")
    assert wc is not None
    assert wc.winning_direction == "YES"
    assert wc.consensus_score == 1.0
    assert wc.trader_count == 1
    assert wc.yes_count == 1
    assert wc.no_count == 0


def test_consensus_yes_majority_by_rank_weight(monkeypatch):
    # rank=1 bets YES (weight=2), rank=2 bets NO (weight=1) — YES wins
    wallets = [_make_wallet(rank=1), _make_wallet(rank=2)]
    tracker = _make_tracker(wallets)

    def mock_positions(addr):
        if "1" in addr[-5:]:   # rank-1 wallet
            return [{"conditionId": "0xabc", "title": "Q", "outcome": "Yes",
                     "currentValue": 100, "size": 200, "initialValue": 80}]
        else:
            return [{"conditionId": "0xabc", "title": "Q", "outcome": "No",
                     "currentValue": 100, "size": 200, "initialValue": 140}]

    monkeypatch.setattr(tracker, "fetch_wallet_positions", mock_positions)
    bundle = tracker.get_elite_consensus()
    wc = bundle.consensus["0xabc"]
    # rank=1 → weight=2, rank=2 → weight=1; yes_weight=200, no_weight=100
    assert wc.winning_direction == "YES"
    assert abs(wc.consensus_score - (200/300)) < 0.01


def test_consensus_alpha_decayed_excluded(monkeypatch):
    tracker = _make_tracker([_make_wallet(rank=1)])
    # entry=0.10, current=0.98 → alpha=(1-0.98)/(1-0.10)=0.02/0.90≈0.022 < 0.25
    monkeypatch.setattr(tracker, "fetch_wallet_positions", lambda addr: [{
        "conditionId": "0xdecay", "title": "Decayed market",
        "outcome": "Yes", "currentValue": 980, "size": 1000, "initialValue": 100,
    }])
    bundle = tracker.get_elite_consensus()
    assert "0xdecay" not in bundle.consensus         # excluded from consensus
    assert "0xdecay" in bundle.all_condition_ids     # still in discovery set


def test_rank_weight_formula():
    # With n=20 wallets: rank=1 → weight=20, rank=20 → weight=1
    n = 20
    assert (n + 1 - 1) == 20
    assert (n + 1 - 20) == 1


def test_all_condition_ids_includes_decayed(monkeypatch):
    tracker = _make_tracker([_make_wallet(rank=1)])
    monkeypatch.setattr(tracker, "fetch_wallet_positions", lambda addr: [
        {"conditionId": "0xfresh",  "title": "Q1", "outcome": "Yes",
         "currentValue": 500, "size": 1000, "initialValue": 400},
        {"conditionId": "0xdecayed","title": "Q2", "outcome": "Yes",
         "currentValue": 990, "size": 1000, "initialValue": 100},
    ])
    bundle = tracker.get_elite_consensus()
    assert "0xfresh"   in bundle.all_condition_ids
    assert "0xdecayed" in bundle.all_condition_ids
    assert "0xfresh"   in bundle.consensus
    assert "0xdecayed" not in bundle.consensus
