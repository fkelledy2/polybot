# data/wallet_tracker.py
# ─────────────────────────────────────────────────────────────
# This module finds and tracks "elite" wallets on Polymarket —
# addresses that have a high win rate over many trades.
# When an elite wallet opens a position, we note it and factor
# it into our signals.
# ─────────────────────────────────────────────────────────────

import requests
import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class WalletProfile:
    """
    Represents a tracked wallet and its performance stats.
    
    @dataclass automatically generates __init__ and __repr__ for us.
    """
    address: str
    total_trades: int = 0
    winning_trades: int = 0
    total_pnl_usd: float = 0.0
    current_positions: list = field(default_factory=list)

    @property
    def win_rate(self) -> float:
        """Calculate win rate as a decimal (0.0 to 1.0)."""
        if self.total_trades == 0:
            return 0.0
        return self.winning_trades / self.total_trades

    @property
    def is_elite(self) -> bool:
        """
        An elite wallet has:
        - At least 50 trades (so win rate is statistically meaningful)
        - At least 55% win rate
        """
        from config import MIN_TRADES_FOR_TRUST, MIN_WIN_RATE
        return (
            self.total_trades >= MIN_TRADES_FOR_TRUST
            and self.win_rate >= MIN_WIN_RATE
        )

    def __repr__(self):
        return (
            f"Wallet({self.address[:8]}... | "
            f"trades={self.total_trades} | "
            f"win_rate={self.win_rate:.1%} | "
            f"pnl=${self.total_pnl_usd:+,.2f})"
        )


class WalletTracker:
    """
    Discovers and tracks high-performing wallets on Polymarket.
    
    Strategy:
    1. Query Polymarket's leaderboard / top traders API
    2. For each wallet, fetch their trade history
    3. Score wallets by win rate (only trust those with 50+ trades)
    4. Watch what markets elite wallets are currently in
    """

    def __init__(self):
        self.session = requests.Session()
        self.tracked_wallets: dict[str, WalletProfile] = {}
        self.elite_wallets: list[WalletProfile] = []

    def fetch_top_wallets(self, limit: int = 100) -> list[str]:
        """
        Fetch addresses of top traders from Polymarket's leaderboard.

        Returns a list of wallet address strings.
        """
        try:
            # Public leaderboard endpoint — no auth required
            response = self.session.get(
                "https://leaderboard-api.polymarket.com/users",
                params={
                    "limit": limit,
                    "offset": 0,
                    "window": "all",    # "daily", "weekly", "monthly", or "all"
                }
            )
            response.raise_for_status()
            data = response.json()

            # Response shape: {"data": [...], "count": N}
            users = data if isinstance(data, list) else data.get("data", [])
            addresses = [
                u.get("proxyWallet") or u.get("address")
                for u in users
                if u.get("proxyWallet") or u.get("address")
            ]
            logger.info(f"Found {len(addresses)} top wallet addresses")
            return addresses

        except requests.RequestException as e:
            logger.warning(f"Leaderboard API unavailable — wallet signals disabled: {e}")
            return []

    def fetch_wallet_stats(self, address: str) -> Optional[WalletProfile]:
        """
        Fetch performance stats for a single wallet address.
        """
        try:
            response = self.session.get(
                f"https://gamma-api.polymarket.com/profiles/{address}",
            )
            response.raise_for_status()
            data = response.json()

            total_trades = int(data.get("tradesCount", 0) or 0)
            # positiveRoi is a float 0.0–1.0 representing win rate fraction.
            # Some API responses use different field names — log available keys
            # on the first call to help diagnose field changes.
            positive_roi = data.get("positiveRoi") or data.get("pnl") or data.get("winRate")
            if positive_roi is None:
                logger.debug(f"positiveRoi missing for {address[:8]}..., available keys: {list(data.keys())}")
                positive_roi = 0.0
            winning_trades = int(float(positive_roi) * total_trades)
            profile = WalletProfile(
                address=address,
                total_trades=total_trades,
                winning_trades=winning_trades,
                total_pnl_usd=float(data.get("profit", 0) or 0),
            )
            return profile

        except (requests.RequestException, ValueError) as e:
            logger.warning(f"Could not fetch stats for {address[:8]}...: {e}")
            return None

    def fetch_wallet_positions(self, address: str) -> list[dict]:
        """
        Fetch the current open positions for a wallet.
        
        Returns a list of position dicts:
        [
            {
                "market_id": "...",
                "question": "Will X happen?",
                "outcome": "YES",
                "size": 150.0,   ← how many shares
                "avg_price": 0.42
            },
            ...
        ]
        """
        try:
            response = self.session.get(
                "https://gamma-api.polymarket.com/positions",
                params={"user": address, "sizeThreshold": "1"}
            )
            response.raise_for_status()
            positions = response.json()
            logger.debug(f"Wallet {address[:8]}... has {len(positions)} open positions")
            return positions

        except requests.RequestException as e:
            logger.warning(f"Could not fetch positions for {address[:8]}...: {e}")
            return []

    def build_elite_list(self, top_n: int = 20) -> list[WalletProfile]:
        """
        Main method: discover and rank elite wallets.
        
        1. Get top 100 wallets by profit
        2. Filter to those with 50+ trades and 55%+ win rate
        3. Return the top N
        """
        logger.info("Building elite wallet list...")

        addresses = self.fetch_top_wallets(limit=100)
        if not addresses:
            logger.info("No wallets returned — running without wallet signals")
            return []

        profiles = []
        for addr in addresses:
            profile = self.fetch_wallet_stats(addr)
            if profile:
                self.tracked_wallets[addr] = profile
                if profile.is_elite:
                    profiles.append(profile)
                    logger.info(f"  ✓ Elite: {profile}")

        # Sort by win rate descending, take top N
        profiles.sort(key=lambda p: p.win_rate, reverse=True)
        self.elite_wallets = profiles[:top_n]

        logger.info(f"Elite wallet list: {len(self.elite_wallets)} wallets")
        return self.elite_wallets

    def get_elite_signals(self) -> list[dict]:
        """
        For each elite wallet, return what markets they're currently
        betting on. These become "copy signals" — we look at whether
        Claude agrees with them.
        
        Returns a list like:
        [
            {
                "wallet": "0xabc...",
                "win_rate": 0.63,
                "market_id": "...",
                "question": "Will X happen?",
                "outcome": "YES",
                "size_usd": 250.0
            }
        ]
        """
        signals = []

        for wallet in self.elite_wallets:
            positions = self.fetch_wallet_positions(wallet.address)
            for pos in positions:
                signals.append({
                    "wallet": wallet.address,
                    "win_rate": wallet.win_rate,
                    "total_trades": wallet.total_trades,
                    "market_id": pos.get("conditionId") or pos.get("market_id"),
                    "question": pos.get("title", "Unknown"),
                    "outcome": pos.get("outcome", "YES"),
                    "size_usd": float(pos.get("currentValue", 0)),
                })

        logger.info(f"Got {len(signals)} elite wallet signals")
        return signals
